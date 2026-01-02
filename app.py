from __future__ import annotations

import json
import os
import queue
import re
import threading
import time
from dataclasses import asdict
from typing import Any, Dict, List, Optional, Tuple

import tkinter as tk
from tkinter import ttk, messagebox

from dotenv import load_dotenv

from core.models import AppConfig, PriceAlert, WalletWatch, CopyTradeSettings
from core.storage import load_config, save_config

from polymarket.util import is_wallet_address, normalize_wallet
from polymarket import gamma
from polymarket import data_api
from polymarket.ws_market import MarketWSClient
from polymarket import clob_rest
from polymarket.geoblock import check_geoblock
from polymarket.trader import PolymarketTrader, TraderConfig


# ---------------------------
# Helpers
# ---------------------------

def extract_slug(s: str) -> str:
    """
    Accept a raw slug or a Polymarket URL and try to extract the last path segment.
    This is best-effort because Polymarket URLs can differ by page type.
    """
    s = (s or "").strip()
    if not s:
        return ""
    # If it's a URL, take last non-empty path segment
    if "://" in s:
        s = re.sub(r"[?#].*$", "", s)  # drop query/fragment
        parts = [p for p in s.split("/") if p]
        return parts[-1] if parts else ""
    return s.strip("/")


def safe_float(s: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        return float(s)
    except Exception:
        return default


# ---------------------------
# Background wallet poller
# ---------------------------

class WalletPoller:
    def __init__(self, ui_queue: "queue.Queue[tuple]", cfg: AppConfig, poll_interval: float = 10.0):
        self.ui_queue = ui_queue
        self.cfg = cfg
        self.poll_interval = poll_interval
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        if not self._thread.is_alive():
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()

    def stop(self):
        self._stop.set()

    def _run(self):
        while not self._stop.is_set():
            for w in list(self.cfg.wallets):
                if self._stop.is_set():
                    break
                if not w.enabled:
                    continue
                try:
                    items = data_api.get_activity(
                        w.wallet,
                        limit=25,
                        types=["TRADE"],  # keep it simple; only trades
                    )
                    # Items are sorted DESC per API; process oldest->newest
                    new_items = []
                    for it in reversed(items):
                        ts = int(it.get("timestamp") or 0)
                        tx = str(it.get("transactionHash") or "")
                        if ts > (w.last_seen_ts or 0):
                            new_items.append(it)
                        elif ts == (w.last_seen_ts or 0) and tx and tx != (w.last_seen_tx or ""):
                            # Same timestamp but different tx; still emit
                            new_items.append(it)

                    for it in new_items:
                        # market slug filter (optional)
                        if w.only_market_slug:
                            if str(it.get("slug") or "") != w.only_market_slug:
                                continue
                        self.ui_queue.put(("wallet_activity", w.id, it))

                        # update last seen to this item
                        w.last_seen_ts = max(w.last_seen_ts or 0, int(it.get("timestamp") or 0))
                        w.last_seen_tx = str(it.get("transactionHash") or w.last_seen_tx or "")

                except Exception as e:
                    self.ui_queue.put(("log", f"[wallet poll] {w.wallet}: {e}"))
            # Persist updated last_seen state
            self.ui_queue.put(("config_changed", None, None))

            # sleep
            end = time.time() + self.poll_interval
            while time.time() < end:
                if self._stop.is_set():
                    break
                time.sleep(0.2)


# ---------------------------
# Main GUI App
# ---------------------------

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Polymarket Sentinel GUI (MVP)")
        self.geometry("1050x700")

        load_dotenv()

        self.cfg: AppConfig = load_config()
        self.ui_queue: "queue.Queue[tuple]" = queue.Queue()

        # price state by token_id
        self.price_state: Dict[str, Dict[str, Optional[float]]] = {}

        # WebSocket client (market channel)
        self.market_ws = MarketWSClient(
            token_ids=[a.token_id for a in self.cfg.alerts if a.enabled],
            on_event=self._on_market_event_bg,
            custom_feature_enabled=False,
            verbose=False,
        )
        self.market_ws.start()

        # Wallet poller
        self.wallet_poller = WalletPoller(self.ui_queue, self.cfg, poll_interval=10.0)

        # Cached trader
        self._trader: Optional[PolymarketTrader] = None
        self._geoblock_cache: Optional[Dict[str, Any]] = None

        # UI
        self._build_ui()

        # Kick off queue processing
        self.after(100, self._process_queue)

    # ------------------ UI build ------------------

    def _build_ui(self):
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True)

        self.tab_alerts = ttk.Frame(nb)
        self.tab_wallets = ttk.Frame(nb)
        self.tab_copy = ttk.Frame(nb)
        self.tab_logs = ttk.Frame(nb)

        nb.add(self.tab_alerts, text="Markets & Alerts")
        nb.add(self.tab_wallets, text="Wallet Tracker")
        nb.add(self.tab_copy, text="Copy Trading")
        nb.add(self.tab_logs, text="Logs")

        self._build_alerts_tab()
        self._build_wallets_tab()
        self._build_copy_tab()
        self._build_logs_tab()

        # status bar
        self.status_var = tk.StringVar(value="Ready.")
        status = ttk.Label(self, textvariable=self.status_var, anchor="w")
        status.pack(fill="x", side="bottom")

    def _build_logs_tab(self):
        frm = self.tab_logs
        self.log_text = tk.Text(frm, height=20, wrap="word")
        self.log_text.pack(fill="both", expand=True, padx=10, pady=10)
        self.log("App started. Price WS connected in background.")

    def _build_alerts_tab(self):
        frm = self.tab_alerts

        top = ttk.Frame(frm)
        top.pack(fill="x", padx=10, pady=10)

        ttk.Label(top, text="Market slug or URL:").grid(row=0, column=0, sticky="w")
        self.market_entry = ttk.Entry(top, width=60)
        self.market_entry.grid(row=0, column=1, padx=5)

        ttk.Button(top, text="Fetch Market", command=self.fetch_market).grid(row=0, column=2, padx=5)

        self.market_info_var = tk.StringVar(value="No market loaded.")
        ttk.Label(top, textvariable=self.market_info_var).grid(row=1, column=0, columnspan=3, sticky="w", pady=(6,0))

        mid = ttk.Frame(frm)
        mid.pack(fill="both", expand=True, padx=10, pady=10)

        # Outcomes list
        left = ttk.Frame(mid)
        left.pack(side="left", fill="both", expand=True)

        ttk.Label(left, text="Market outcomes (select one):").pack(anchor="w")
        self.outcome_list = tk.Listbox(left, height=12)
        self.outcome_list.pack(fill="both", expand=True)
        self.outcome_list.bind("<<ListboxSelect>>", lambda e: self._on_outcome_selected())

        # Alert form
        right = ttk.Frame(mid)
        right.pack(side="left", fill="both", expand=True, padx=(10,0))

        ttk.Label(right, text="Create price alert:").grid(row=0, column=0, columnspan=2, sticky="w")

        ttk.Label(right, text="Label:").grid(row=1, column=0, sticky="e")
        self.alert_label_entry = ttk.Entry(right, width=35)
        self.alert_label_entry.grid(row=1, column=1, sticky="w", pady=2)

        ttk.Label(right, text="Threshold (0..1):").grid(row=2, column=0, sticky="e")
        self.alert_threshold_entry = ttk.Entry(right, width=10)
        self.alert_threshold_entry.grid(row=2, column=1, sticky="w", pady=2)

        ttk.Label(right, text="Direction:").grid(row=3, column=0, sticky="e")
        self.alert_dir_var = tk.StringVar(value="above")
        ttk.Combobox(right, textvariable=self.alert_dir_var, values=["above","below"], width=10, state="readonly").grid(row=3, column=1, sticky="w", pady=2)

        ttk.Label(right, text="Source:").grid(row=4, column=0, sticky="e")
        self.alert_src_var = tk.StringVar(value="last_trade")
        ttk.Combobox(right, textvariable=self.alert_src_var, values=["last_trade","midpoint","best_bid","best_ask"], width=12, state="readonly").grid(row=4, column=1, sticky="w", pady=2)

        self.alert_once_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(right, text="Trigger once (disable after firing)", variable=self.alert_once_var).grid(row=5, column=1, sticky="w", pady=2)

        ttk.Button(right, text="Add Alert", command=self.add_alert).grid(row=6, column=1, sticky="w", pady=(8,2))

        # Alerts table
        bottom = ttk.Frame(frm)
        bottom.pack(fill="both", expand=True, padx=10, pady=(0,10))

        cols = ("label","token","dir","threshold","source","enabled","triggered","last")
        self.alert_tree = ttk.Treeview(bottom, columns=cols, show="headings", height=10)
        for c in cols:
            self.alert_tree.heading(c, text=c)
        self.alert_tree.column("label", width=180)
        self.alert_tree.column("token", width=220)
        self.alert_tree.column("dir", width=60)
        self.alert_tree.column("threshold", width=80)
        self.alert_tree.column("source", width=90)
        self.alert_tree.column("enabled", width=70)
        self.alert_tree.column("triggered", width=70)
        self.alert_tree.column("last", width=80)
        self.alert_tree.pack(fill="both", expand=True, side="left")

        btns = ttk.Frame(bottom)
        btns.pack(fill="y", side="left", padx=(8,0))

        ttk.Button(btns, text="Toggle enable", command=self.toggle_selected_alert).pack(fill="x", pady=2)
        ttk.Button(btns, text="Delete", command=self.delete_selected_alert).pack(fill="x", pady=2)
        ttk.Button(btns, text="Resubscribe WS", command=self.resubscribe_ws).pack(fill="x", pady=10)

        self._refresh_alert_table()

        self._market_loaded: Optional[Dict[str, Any]] = None
        self._market_outcomes: List[gamma.MarketOutcome] = []
        self._selected_token_id: Optional[str] = None

    def _build_wallets_tab(self):
        frm = self.tab_wallets

        top = ttk.Frame(frm)
        top.pack(fill="x", padx=10, pady=10)

        ttk.Label(top, text="Username/pseudonym or wallet (0x...):").grid(row=0, column=0, sticky="w")
        self.wallet_search_entry = ttk.Entry(top, width=50)
        self.wallet_search_entry.grid(row=0, column=1, padx=5)
        ttk.Button(top, text="Search/Add", command=self.search_or_add_wallet).grid(row=0, column=2, padx=5)

        ttk.Label(top, text="Poll interval (sec):").grid(row=1, column=0, sticky="w", pady=(8,0))
        self.poll_interval_var = tk.StringVar(value="10")
        ttk.Entry(top, textvariable=self.poll_interval_var, width=6).grid(row=1, column=1, sticky="w", pady=(8,0))

        self.polling_var = tk.BooleanVar(value=False)
        ttk.Button(top, text="Start / Stop Tracking", command=self.toggle_wallet_polling).grid(row=1, column=2, padx=5, pady=(8,0))

        mid = ttk.Frame(frm)
        mid.pack(fill="both", expand=True, padx=10, pady=10)

        # tracked wallets table
        cols = ("name","wallet","enabled","last_seen")
        self.wallet_tree = ttk.Treeview(mid, columns=cols, show="headings", height=10)
        for c in cols:
            self.wallet_tree.heading(c, text=c)
        self.wallet_tree.column("name", width=160)
        self.wallet_tree.column("wallet", width=340)
        self.wallet_tree.column("enabled", width=80)
        self.wallet_tree.column("last_seen", width=120)
        self.wallet_tree.pack(fill="both", expand=True, side="left")

        btns = ttk.Frame(mid)
        btns.pack(fill="y", side="left", padx=(8,0))
        ttk.Button(btns, text="Toggle enable", command=self.toggle_selected_wallet).pack(fill="x", pady=2)
        ttk.Button(btns, text="Delete", command=self.delete_selected_wallet).pack(fill="x", pady=2)

        # activity feed
        bottom = ttk.Frame(frm)
        bottom.pack(fill="both", expand=True, padx=10, pady=(0,10))

        ttk.Label(bottom, text="Recent activity (newest first):").pack(anchor="w")
        self.activity_list = tk.Listbox(bottom, height=10)
        self.activity_list.pack(fill="both", expand=True)

        self._refresh_wallet_table()

    def _build_copy_tab(self):
        frm = self.tab_copy

        top = ttk.Frame(frm)
        top.pack(fill="x", padx=10, pady=10)

        ttk.Label(top, text="Geoblock status:").grid(row=0, column=0, sticky="w")
        self.geo_var = tk.StringVar(value="Unknown (check)")
        ttk.Label(top, textvariable=self.geo_var).grid(row=0, column=1, sticky="w")
        ttk.Button(top, text="Check Geoblock", command=self.do_geoblock_check).grid(row=0, column=2, padx=5)

        # Copy settings form
        form = ttk.Labelframe(frm, text="Copy trading settings (default = SIM)")
        form.pack(fill="x", padx=10, pady=10)

        self.ct_enabled_var = tk.BooleanVar(value=self.cfg.copytrading.enabled)
        ttk.Checkbutton(form, text="Enable copy trading", variable=self.ct_enabled_var).grid(row=0, column=0, sticky="w", padx=5, pady=5)

        self.ct_live_var = tk.BooleanVar(value=self.cfg.copytrading.live)
        ttk.Checkbutton(form, text="LIVE mode (places real orders)", variable=self.ct_live_var).grid(row=0, column=1, sticky="w", padx=5, pady=5)

        ttk.Label(form, text="Follow wallet:").grid(row=1, column=0, sticky="e", padx=5, pady=5)
        self.ct_follow_var = tk.StringVar(value=self.cfg.copytrading.follow_wallet)
        self.ct_follow_combo = ttk.Combobox(form, textvariable=self.ct_follow_var, values=self._wallet_choices(), width=50)
        self.ct_follow_combo.grid(row=1, column=1, sticky="w", padx=5, pady=5)

        ttk.Label(form, text="Scale:").grid(row=2, column=0, sticky="e", padx=5, pady=5)
        self.ct_scale_var = tk.StringVar(value=str(self.cfg.copytrading.scale))
        ttk.Entry(form, textvariable=self.ct_scale_var, width=10).grid(row=2, column=1, sticky="w", padx=5, pady=5)

        ttk.Label(form, text="Max USDC / trade:").grid(row=3, column=0, sticky="e", padx=5, pady=5)
        self.ct_max_var = tk.StringVar(value=str(self.cfg.copytrading.max_usdc_per_trade))
        ttk.Entry(form, textvariable=self.ct_max_var, width=10).grid(row=3, column=1, sticky="w", padx=5, pady=5)

        ttk.Label(form, text="Slippage (0..1):").grid(row=4, column=0, sticky="e", padx=5, pady=5)
        self.ct_slip_var = tk.StringVar(value=str(self.cfg.copytrading.slippage))
        ttk.Entry(form, textvariable=self.ct_slip_var, width=10).grid(row=4, column=1, sticky="w", padx=5, pady=5)

        self.ct_allow_sells_var = tk.BooleanVar(value=self.cfg.copytrading.allow_sells)
        ttk.Checkbutton(form, text="Allow copying SELL trades (riskier)", variable=self.ct_allow_sells_var).grid(row=5, column=1, sticky="w", padx=5, pady=5)

        ttk.Button(form, text="Save settings", command=self.save_copy_settings).grid(row=6, column=1, sticky="w", padx=5, pady=10)

        hint = ttk.Label(frm, text="LIVE mode requires PRIVATE_KEY (+ optional FUNDER_ADDRESS/SIGNATURE_TYPE) set in .env or environment variables.")
        hint.pack(anchor="w", padx=10)

    # ------------------ Logging ------------------

    def log(self, msg: str):
        ts = time.strftime("%H:%M:%S")
        line = f"[{ts}] {msg}\n"
        self.log_text.insert("end", line)
        self.log_text.see("end")

    # ------------------ Market fetch / alerts ------------------

    def fetch_market(self):
        raw = self.market_entry.get()
        slug = extract_slug(raw)
        if not slug:
            messagebox.showerror("Error", "Enter a market slug or a Polymarket URL.")
            return

        self.status_var.set("Fetching market from Gamma API...")
        self.update_idletasks()

        try:
            m = gamma.get_market_by_slug(slug)
            if not m:
                messagebox.showerror("Not found", f"Could not fetch market for slug: {slug}")
                return
            self._market_loaded = m
            title = m.get("question") or m.get("title") or slug
            self.market_info_var.set(f"Loaded: {title} (slug: {slug})")
            self._market_outcomes = gamma.parse_market_outcomes(m)

            self.outcome_list.delete(0, "end")
            for o in self._market_outcomes:
                price_str = f"{o.price:.3f}" if isinstance(o.price, float) else "?"
                self.outcome_list.insert("end", f"{o.outcome}  |  token {o.token_id[:10]}...  |  gamma price {price_str}")

            self.status_var.set("Market loaded. Select an outcome to create an alert.")
            self._selected_token_id = None

        except Exception as e:
            messagebox.showerror("Error", str(e))
            self.status_var.set("Error fetching market.")

    def _on_outcome_selected(self):
        idxs = self.outcome_list.curselection()
        if not idxs:
            return
        i = idxs[0]
        if i >= len(self._market_outcomes):
            return
        tok = self._market_outcomes[i].token_id
        self._selected_token_id = tok
        # Pre-fill label if blank
        if not self.alert_label_entry.get().strip():
            self.alert_label_entry.insert(0, self._market_outcomes[i].outcome)
        self.status_var.set(f"Selected token_id: {tok}")

    def add_alert(self):
        token_id = self._selected_token_id
        if not token_id:
            messagebox.showerror("Error", "Select a market outcome first.")
            return

        label = self.alert_label_entry.get().strip() or f"Alert {token_id[:8]}"
        thr = safe_float(self.alert_threshold_entry.get().strip())
        if thr is None or thr < 0 or thr > 1:
            messagebox.showerror("Error", "Threshold must be a number between 0 and 1.")
            return

        direction = self.alert_dir_var.get()
        source = self.alert_src_var.get()
        once = bool(self.alert_once_var.get())

        a = PriceAlert(
            token_id=token_id,
            label=label,
            direction=direction,  # type: ignore
            threshold=float(thr),
            source=source,  # type: ignore
            once=once,
            enabled=True,
        )
        self.cfg.alerts.append(a)
        save_config(self.cfg)
        self._refresh_alert_table()

        # subscribe WS
        self.market_ws.subscribe([token_id])

        self.status_var.set("Alert added.")
        self.ui_queue.put(("log", f"Added alert: {label} {direction} {thr} ({source}) on token {token_id}"))

    def _refresh_alert_table(self):
        for iid in self.alert_tree.get_children():
            self.alert_tree.delete(iid)
        for a in self.cfg.alerts:
            last_val = "" if a.last_value is None else f"{a.last_value:.3f}"
            self.alert_tree.insert(
                "",
                "end",
                iid=a.id,
                values=(
                    a.label,
                    a.token_id,
                    a.direction,
                    f"{a.threshold:.3f}",
                    a.source,
                    "yes" if a.enabled else "no",
                    "yes" if a.triggered else "no",
                    last_val,
                ),
            )

    def _selected_alert_id(self) -> Optional[str]:
        sel = self.alert_tree.selection()
        return sel[0] if sel else None

    def toggle_selected_alert(self):
        aid = self._selected_alert_id()
        if not aid:
            return
        for a in self.cfg.alerts:
            if a.id == aid:
                a.enabled = not a.enabled
                if a.enabled:
                    self.market_ws.subscribe([a.token_id])
                else:
                    # we don't unsubscribe automatically because another alert might use same token
                    pass
                save_config(self.cfg)
                self._refresh_alert_table()
                self.ui_queue.put(("log", f"Toggled alert {a.label} -> {'enabled' if a.enabled else 'disabled'}"))
                return

    def delete_selected_alert(self):
        aid = self._selected_alert_id()
        if not aid:
            return
        self.cfg.alerts = [a for a in self.cfg.alerts if a.id != aid]
        save_config(self.cfg)
        self._refresh_alert_table()
        self.ui_queue.put(("log", f"Deleted alert {aid}"))
        # WS unsubscribe: only unsubscribe if no other alert uses token
        # (We'll just resubscribe to current set to keep it simple.)
        self.resubscribe_ws()

    def resubscribe_ws(self):
        ids = [a.token_id for a in self.cfg.alerts if a.enabled]
        self.market_ws.set_tokens(ids)
        # Reconnect by stopping and restarting client (simple, brute-force)
        self.market_ws.stop()
        self.market_ws = MarketWSClient(
            token_ids=ids,
            on_event=self._on_market_event_bg,
            custom_feature_enabled=False,
            verbose=False,
        )
        self.market_ws.start()
        self.ui_queue.put(("log", f"Resubscribed WS to {len(ids)} tokens."))

    # ------------------ Wallet tracking ------------------

    def _wallet_choices(self) -> List[str]:
        return [w.wallet for w in self.cfg.wallets]

    def _refresh_wallet_table(self):
        for iid in self.wallet_tree.get_children():
            self.wallet_tree.delete(iid)
        for w in self.cfg.wallets:
            last_seen = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(w.last_seen_ts)) if w.last_seen_ts else ""
            name = w.display_name or w.wallet[:10] + "..."
            self.wallet_tree.insert(
                "",
                "end",
                iid=w.id,
                values=(name, w.wallet, "yes" if w.enabled else "no", last_seen),
            )
        # refresh copy tab dropdown too
        try:
            self.ct_follow_combo["values"] = self._wallet_choices()
        except Exception:
            pass

    def _selected_wallet_id(self) -> Optional[str]:
        sel = self.wallet_tree.selection()
        return sel[0] if sel else None

    def toggle_selected_wallet(self):
        wid = self._selected_wallet_id()
        if not wid:
            return
        for w in self.cfg.wallets:
            if w.id == wid:
                w.enabled = not w.enabled
                save_config(self.cfg)
                self._refresh_wallet_table()
                self.ui_queue.put(("log", f"Toggled wallet {w.wallet} -> {'enabled' if w.enabled else 'disabled'}"))
                return

    def delete_selected_wallet(self):
        wid = self._selected_wallet_id()
        if not wid:
            return
        self.cfg.wallets = [w for w in self.cfg.wallets if w.id != wid]
        save_config(self.cfg)
        self._refresh_wallet_table()
        self.ui_queue.put(("log", f"Deleted wallet watch {wid}"))

    def search_or_add_wallet(self):
        q = (self.wallet_search_entry.get() or "").strip()
        if not q:
            return
        w = normalize_wallet(q)
        if w:
            self._add_wallet_watch(w, display_name=w[:10] + "...")
            return

        # treat as username/pseudonym
        self.status_var.set("Searching profiles...")
        self.update_idletasks()
        try:
            profiles = gamma.search_profiles(q, limit=10)
            if not profiles:
                messagebox.showinfo("No results", "No profiles found. Try pasting the 0x wallet address instead.")
                self.status_var.set("No profiles found.")
                return

            # popup select
            win = tk.Toplevel(self)
            win.title("Select profile")
            win.geometry("700x300")
            lb = tk.Listbox(win)
            lb.pack(fill="both", expand=True, padx=10, pady=10)

            for p in profiles:
                lb.insert("end", f"{p.pseudonym}  |  {p.proxy_wallet}")

            def add_selected():
                sel = lb.curselection()
                if not sel:
                    return
                p = profiles[sel[0]]
                self._add_wallet_watch(p.proxy_wallet, display_name=p.pseudonym or p.proxy_wallet[:10]+"...")
                win.destroy()

            ttk.Button(win, text="Add", command=add_selected).pack(pady=10)
            self.status_var.set("Select a profile to track.")
        except Exception as e:
            messagebox.showerror("Error", str(e))
            self.status_var.set("Profile search error.")

    def _add_wallet_watch(self, wallet: str, display_name: str = ""):
        wallet = wallet.lower().strip()
        if not is_wallet_address(wallet):
            messagebox.showerror("Error", "Not a valid 0x wallet/proxyWallet address.")
            return
        if any(w.wallet == wallet for w in self.cfg.wallets):
            messagebox.showinfo("Already tracked", "This wallet is already being tracked.")
            return

        ww = WalletWatch(wallet=wallet, display_name=display_name, enabled=True)
        self.cfg.wallets.append(ww)
        save_config(self.cfg)
        self._refresh_wallet_table()
        self.ui_queue.put(("log", f"Added wallet watch: {display_name} ({wallet})"))

    def toggle_wallet_polling(self):
        if self.wallet_poller._thread.is_alive():
            self.wallet_poller.stop()
            self.status_var.set("Wallet polling stopped.")
            self.ui_queue.put(("log", "Wallet polling stopped."))
            return

        iv = safe_float(self.poll_interval_var.get(), default=10.0)
        if iv is None or iv < 2:
            iv = 10.0
        self.wallet_poller.poll_interval = float(iv)
        self.wallet_poller.start()
        self.status_var.set(f"Wallet polling started (every {iv:.0f}s).")
        self.ui_queue.put(("log", f"Wallet polling started (interval={iv:.0f}s)."))

    # ------------------ Copy trading ------------------

    def do_geoblock_check(self):
        self.status_var.set("Checking geoblock...")
        self.update_idletasks()
        try:
            geo = check_geoblock()
            self._geoblock_cache = geo
            if geo.get("blocked") is True:
                self.geo_var.set(f"BLOCKED ({geo.get('country')} {geo.get('region')})")
            else:
                self.geo_var.set(f"OK ({geo.get('country')} {geo.get('region')})")
            self.ui_queue.put(("log", f"Geoblock: {geo}"))
            self.status_var.set("Geoblock check done.")
        except Exception as e:
            self.geo_var.set(f"Error: {e}")
            self.ui_queue.put(("log", f"Geoblock error: {e}"))
            self.status_var.set("Geoblock check error.")

    def save_copy_settings(self):
        s = CopyTradeSettings(
            enabled=bool(self.ct_enabled_var.get()),
            live=bool(self.ct_live_var.get()),
            follow_wallet=str(self.ct_follow_var.get() or "").strip().lower(),
            scale=float(safe_float(self.ct_scale_var.get(), 1.0) or 1.0),
            max_usdc_per_trade=float(safe_float(self.ct_max_var.get(), 25.0) or 25.0),
            slippage=float(safe_float(self.ct_slip_var.get(), 0.02) or 0.02),
            allow_sells=bool(self.ct_allow_sells_var.get()),
        )
        self.cfg.copytrading = s
        save_config(self.cfg)
        self.ui_queue.put(("log", f"Saved copy settings: {asdict(s)}"))

        # Safety: warn on LIVE
        if s.live and s.enabled:
            messagebox.showwarning(
                "LIVE Copy Trading Enabled",
                "LIVE mode will place real orders.\n\n"
                "Make sure:\n"
                "- You are not geoblocked\n"
                "- Your PRIVATE_KEY / FUNDER_ADDRESS / SIGNATURE_TYPE are correct\n"
                "- You understand the risk controls\n",
            )

    def _get_trader(self) -> PolymarketTrader:
        if self._trader is not None:
            return self._trader

        pk = (os.getenv("PRIVATE_KEY") or "").strip()
        if not pk:
            raise RuntimeError("Missing PRIVATE_KEY in environment/.env")
        funder = (os.getenv("FUNDER_ADDRESS") or "").strip() or None
        sig_type = int((os.getenv("SIGNATURE_TYPE") or "0").strip())

        cfg = TraderConfig(private_key=pk, funder_address=funder, signature_type=sig_type)
        self._trader = PolymarketTrader(cfg)
        return self._trader

    def _copy_trade_from_activity(self, item: Dict[str, Any]):
        s = self.cfg.copytrading
        if not s.enabled:
            return
        if not s.follow_wallet:
            return

        if str(item.get("proxyWallet") or "").lower() != s.follow_wallet.lower():
            return

        side = str(item.get("side") or "").upper()
        if side not in ("BUY", "SELL"):
            return
        if side == "SELL" and not s.allow_sells:
            self.ui_queue.put(("log", f"[copy] Skipping SELL trade (allow_sells=false)."))
            return

        token_id = str(item.get("asset") or "")
        if not token_id:
            return

        raw_size = safe_float(item.get("size"), 0.0) or 0.0
        raw_price = safe_float(item.get("price"), None)
        size = max(0.0, raw_size * float(s.scale))

        # Pull current best bid/ask for safer limit pricing
        best_bid = best_ask = None
        try:
            book = clob_rest.get_book(token_id)
            best_bid, best_ask = clob_rest.best_bid_ask_from_book(book)
        except Exception:
            pass

        slip = max(0.0, min(float(s.slippage), 1.0))
        if side == "BUY":
            # pick an ask-based price cap
            ref = best_ask if best_ask is not None else raw_price
            if ref is None:
                ref = 0.99
            limit_price = min(1.0, float(ref) + slip)
        else:
            ref = best_bid if best_bid is not None else raw_price
            if ref is None:
                ref = 0.01
            limit_price = max(0.0, float(ref) - slip)

        # Enforce max USDC exposure by shrinking share size
        max_usdc = max(0.01, float(s.max_usdc_per_trade))
        if limit_price > 0:
            max_shares = max_usdc / limit_price
            if size > max_shares:
                size = max_shares

        if size <= 0:
            return

        if not s.live:
            self.ui_queue.put(("log", f"[copy SIM] {side} token={token_id[:10]}... size={size:.4f} price<= {limit_price:.4f}"))
            return

        # LIVE mode safety: geoblock
        if self._geoblock_cache is None:
            self.do_geoblock_check()
        if self._geoblock_cache and self._geoblock_cache.get("blocked") is True:
            self.ui_queue.put(("log", f"[copy] BLOCKED by geoblock. Refusing to place order."))
            return

        trader = self._get_trader()
        self.ui_queue.put(("log", f"[copy LIVE] Placing {side} order token={token_id} size={size:.4f} price={limit_price:.4f} FOK"))
        try:
            resp = trader.place_limit_order(token_id=token_id, side=side, price=limit_price, size=size, tif="FOK")
            self.ui_queue.put(("log", f"[copy LIVE] response: {resp}"))
        except Exception as e:
            self.ui_queue.put(("log", f"[copy LIVE] error: {e}"))

    # ------------------ Market WS event handling ------------------

    def _on_market_event_bg(self, data: Dict[str, Any]):
        # Called from WS thread; push to UI thread
        self.ui_queue.put(("market_event", None, data))

    def _update_price_state(self, ev: Dict[str, Any]):
        et = ev.get("event_type")
        if not et:
            return

        if et == "last_trade_price":
            token_id = str(ev.get("asset_id") or "")
            price = safe_float(ev.get("price"))
            if not token_id or price is None:
                return
            st = self.price_state.setdefault(token_id, {})
            st["last_trade"] = price
            # Evaluate alerts
            self._eval_alerts_for_token(token_id)

        elif et == "best_bid_ask":
            token_id = str(ev.get("asset_id") or "")
            bid = safe_float(ev.get("best_bid"))
            ask = safe_float(ev.get("best_ask"))
            if not token_id:
                return
            st = self.price_state.setdefault(token_id, {})
            if bid is not None:
                st["best_bid"] = bid
            if ask is not None:
                st["best_ask"] = ask
            if st.get("best_bid") is not None and st.get("best_ask") is not None:
                st["midpoint"] = (st["best_bid"] + st["best_ask"]) / 2.0  # type: ignore
            self._eval_alerts_for_token(token_id)

        elif et == "price_change":
            changes = ev.get("price_changes") or []
            if not isinstance(changes, list):
                return
            for ch in changes:
                token_id = str(ch.get("asset_id") or "")
                if not token_id:
                    continue
                bid = safe_float(ch.get("best_bid"))
                ask = safe_float(ch.get("best_ask"))
                st = self.price_state.setdefault(token_id, {})
                if bid is not None:
                    st["best_bid"] = bid
                if ask is not None:
                    st["best_ask"] = ask
                if st.get("best_bid") is not None and st.get("best_ask") is not None:
                    st["midpoint"] = (st["best_bid"] + st["best_ask"]) / 2.0  # type: ignore
                self._eval_alerts_for_token(token_id)

        elif et == "book":
            token_id = str(ev.get("asset_id") or "")
            if not token_id:
                return
            bid = ask = None
            try:
                bids = ev.get("bids") or ev.get("buys") or []
                asks = ev.get("asks") or ev.get("sells") or []
                if bids:
                    bid = safe_float(bids[0].get("price"))
                if asks:
                    ask = safe_float(asks[0].get("price"))
            except Exception:
                pass
            st = self.price_state.setdefault(token_id, {})
            if bid is not None:
                st["best_bid"] = bid
            if ask is not None:
                st["best_ask"] = ask
            if st.get("best_bid") is not None and st.get("best_ask") is not None:
                st["midpoint"] = (st["best_bid"] + st["best_ask"]) / 2.0  # type: ignore
            self._eval_alerts_for_token(token_id)

    def _eval_alerts_for_token(self, token_id: str):
        st = self.price_state.get(token_id) or {}
        # evaluate all alerts for this token
        changed = False
        for a in self.cfg.alerts:
            if not a.enabled:
                continue
            if a.token_id != token_id:
                continue

            val = st.get(a.source)
            if val is None:
                continue

            prev = a.last_value
            a.last_value = float(val)

            cond_now = (val >= a.threshold) if a.direction == "above" else (val <= a.threshold)
            cond_prev = None
            if prev is not None:
                cond_prev = (prev >= a.threshold) if a.direction == "above" else (prev <= a.threshold)

            # Trigger only on crossing into the condition
            crossed = cond_now and (cond_prev is False or cond_prev is None)

            if crossed and not a.triggered:
                a.triggered = True
                changed = True
                self._fire_alert(a, val)

                if a.once:
                    a.enabled = False

            # For repeat alerts: reset triggered flag when condition becomes false again
            if not a.once and a.triggered and not cond_now:
                a.triggered = False
                changed = True

        if changed:
            save_config(self.cfg)
            self._refresh_alert_table()

    def _fire_alert(self, alert: PriceAlert, value: float):
        msg = f"ALERT: {alert.label} | {alert.source}={value:.3f} crossed {alert.direction} {alert.threshold:.3f}"
        self.ui_queue.put(("log", msg))
        try:
            self.bell()
        except Exception:
            pass
        # Non-blocking popup
        self.after(0, lambda: messagebox.showinfo("Price Alert", msg))

    # ------------------ Queue processing ------------------

    def _process_queue(self):
        try:
            while True:
                kind, a, b = self.ui_queue.get_nowait()
                if kind == "log":
                    self.log(str(a))
                elif kind == "market_event":
                    self._update_price_state(b)  # type: ignore
                elif kind == "wallet_activity":
                    watch_id = a
                    item = b
                    self._handle_wallet_activity(watch_id, item)  # type: ignore
                elif kind == "config_changed":
                    # Persist config (for last_seen state)
                    save_config(self.cfg)
                    self._refresh_wallet_table()
                else:
                    self.log(f"[debug] unknown queue item: {kind}")
        except queue.Empty:
            pass
        finally:
            self.after(100, self._process_queue)

    def _handle_wallet_activity(self, watch_id: str, item: Dict[str, Any]):
        # Add to activity UI
        ts = int(item.get("timestamp") or 0)
        tss = time.strftime("%H:%M:%S", time.localtime(ts)) if ts else "?"
        pseudo = item.get("pseudonym") or item.get("name") or ""
        side = item.get("side") or ""
        slug = item.get("slug") or ""
        outcome = item.get("outcome") or ""
        price = safe_float(item.get("price"), None)
        size = safe_float(item.get("size"), None)

        line = f"{tss}  {pseudo}  {side}  {slug}  {outcome}  price={price}  size={size}"
        self.activity_list.insert(0, line)
        # keep list bounded
        if self.activity_list.size() > 200:
            self.activity_list.delete(200, "end")

        self.log(f"[activity] {line}")

        # Copy trade logic
        self._copy_trade_from_activity(item)


if __name__ == "__main__":
    app = App()
    app.mainloop()
