from __future__ import annotations

import json
import os
import queue
import re
import sys
import threading
import time
from importlib import metadata as importlib_metadata
from pathlib import Path
from urllib import request as urllib_request
from urllib import error as urllib_error
from dataclasses import asdict
from typing import Any, Dict, List, Optional, Tuple

import tkinter as tk
from tkinter import ttk, messagebox

from dotenv import load_dotenv

from core.models import AppConfig, PriceAlert, WalletWatch, CopyTradeSettings
from core.storage import load_config, save_config
from market_adapters import build_default_registry
from market_adapters.base import MarketAdapter
from market_adapters.errors import UnsupportedFeatureError
from market_adapters.polymarket import PolymarketAdapter
from market_adapters.types import MarketMetadata

from polymarket.util import is_wallet_address, normalize_wallet
from polymarket import data_api
from polymarket.ws_market import MarketWSClient
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
    s = re.sub(r"[?#].*$", "", s)  # drop query/fragment
    # If it's a URL, take last non-empty path segment
    if "://" in s:
        parts = [p for p in s.split("/") if p]
        return parts[-1] if parts else ""
    return s.strip("/")


def safe_float(s: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        return float(s)
    except Exception:
        return default


def activity_key(item: Dict[str, Any]) -> str:
    """Stable local identity for a Data API activity item."""
    tx = str(item.get("transactionHash") or "").strip().lower()
    if tx:
        return f"tx:{tx}"
    fields = ("timestamp", "proxyWallet", "asset", "side", "price", "size", "slug", "outcome")
    return "activity:" + "|".join(str(item.get(k) or "").strip().lower() for k in fields)


def market_choice_label(metadata: MarketMetadata) -> str:
    return f"{metadata.display_name} ({metadata.market_id})"


def market_id_from_choice(choice: str) -> str:
    match = re.search(r"\(([^()]+)\)\s*$", str(choice or ""))
    if match:
        return match.group(1).strip().lower()
    return str(choice or "").strip().lower()


def set_windows_app_id(app_id: str) -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
    except Exception:
        pass


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
                    seen_keys = set(w.seen_activity_keys or [])
                    for it in reversed(items):
                        ts = int(it.get("timestamp") or 0)
                        tx = str(it.get("transactionHash") or "")
                        key = activity_key(it)
                        if key in seen_keys:
                            continue
                        if ts > (w.last_seen_ts or 0):
                            new_items.append((key, it))
                            seen_keys.add(key)
                        elif ts == (w.last_seen_ts or 0) and (not tx or tx != (w.last_seen_tx or "")):
                            # Same timestamp but different activity; still emit once.
                            new_items.append((key, it))
                            seen_keys.add(key)

                    for key, it in new_items:
                        # market slug filter (optional)
                        if w.only_market_slug:
                            if str(it.get("slug") or "") != w.only_market_slug:
                                continue
                        self.ui_queue.put(("wallet_activity", w.id, it))

                        # update last seen to this item
                        w.last_seen_ts = max(w.last_seen_ts or 0, int(it.get("timestamp") or 0))
                        w.last_seen_tx = str(it.get("transactionHash") or w.last_seen_tx or "")
                        seen_keys.add(key)
                        w.seen_activity_keys.append(key)
                        if len(w.seen_activity_keys) > 200:
                            w.seen_activity_keys = w.seen_activity_keys[-200:]
                            seen_keys = set(w.seen_activity_keys)

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
        self.title("prediction-market-alert-and-copy-trade-gui")
        self.geometry("1050x700")
        self._load_icon_image()
        self._apply_window_icon(self)

        load_dotenv()

        self.cfg: AppConfig = load_config()
        self.cfg.theme = self._normalize_theme(self.cfg.theme)
        self.adapter_registry = build_default_registry()
        self.polymarket_adapter = self._create_polymarket_adapter()
        self.ui_queue: "queue.Queue[tuple]" = queue.Queue()
        self.style = ttk.Style(self)
        self._themes = self._build_theme_palettes()
        self._palette = self._themes[self.cfg.theme]
        self._requirements = self._load_requirements()
        self._dep_check_running = False
        self._icon_images: List[tk.PhotoImage] = []

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
        self._apply_theme(self.cfg.theme)
        self._apply_window_icon(self)

        # Kick off queue processing
        self.after(100, self._process_queue)

    # ------------------ UI build ------------------

    def _create_polymarket_adapter(self) -> PolymarketAdapter:
        market_cfg = self.cfg.markets.get("polymarket")
        return PolymarketAdapter(market_cfg.settings if market_cfg else {})

    def _get_polymarket_adapter(self) -> PolymarketAdapter:
        adapter = getattr(self, "polymarket_adapter", None)
        if adapter is None:
            adapter = self._create_polymarket_adapter()
            self.polymarket_adapter = adapter
        return adapter

    def _market_choices(self) -> List[str]:
        return [market_choice_label(meta) for meta in self.adapter_registry.list_metadata()]

    def _market_label_for_id(self, market_id: str) -> str:
        try:
            return market_choice_label(self.adapter_registry.get_metadata(market_id))
        except Exception:
            return market_choice_label(self.adapter_registry.get_metadata("polymarket"))

    def _get_selected_market_adapter(self) -> MarketAdapter:
        market_id = self.cfg.selected_market_id
        market_cfg = self.cfg.markets.get(market_id)
        settings = market_cfg.settings if market_cfg else {}
        return self.adapter_registry.create(market_id, settings)

    def _selected_market_display_name(self) -> str:
        return self.adapter_registry.get_metadata(self.cfg.selected_market_id).display_name

    def _require_polymarket_selected(self, feature: str) -> bool:
        if self.cfg.selected_market_id == "polymarket":
            return True
        message = (
            f"{feature} is currently implemented only for Polymarket. "
            f"{self._selected_market_display_name()} is visible as a market adapter entry, "
            "but its adapter is still a stub."
        )
        self.status_var.set(message)
        self.ui_queue.put(("log", f"[market] {message}"))
        messagebox.showinfo("Unsupported market", message)
        return False

    def _on_market_change(self):
        market_id = market_id_from_choice(self.market_var.get())
        if market_id not in self.adapter_registry.list_market_ids():
            market_id = "polymarket"
        if market_id == self.cfg.selected_market_id:
            return
        self.cfg.selected_market_id = market_id
        save_config(self.cfg)
        self.market_var.set(self._market_label_for_id(market_id))
        adapter = self._get_selected_market_adapter()
        health = adapter.health_check()
        if health.get("ok"):
            msg = f"Selected market: {adapter.display_name}."
        else:
            msg = f"Selected market: {adapter.display_name}. {health.get('message')}"
        self.status_var.set(msg)
        self.ui_queue.put(("log", f"[market] {msg}"))

    def _build_ui(self):
        topbar = ttk.Frame(self)
        topbar.pack(fill="x", padx=10, pady=(10, 0))

        ttk.Label(topbar, text="Market:").pack(side="left")
        self.market_var = tk.StringVar(value=self._market_label_for_id(self.cfg.selected_market_id))
        self.market_combo = ttk.Combobox(
            topbar,
            textvariable=self.market_var,
            values=self._market_choices(),
            state="readonly",
            width=58,
        )
        self.market_combo.pack(side="left", padx=(6, 18))
        self.market_combo.bind("<<ComboboxSelected>>", lambda e: self._on_market_change())

        ttk.Label(topbar, text="Theme:").pack(side="left")
        self.theme_var = tk.StringVar(value=self._theme_label(self.cfg.theme))
        self.theme_combo = ttk.Combobox(
            topbar,
            textvariable=self.theme_var,
            values=["Light", "Dark"],
            state="readonly",
            width=8,
        )
        self.theme_combo.pack(side="left", padx=(6, 0))
        self.theme_combo.bind("<<ComboboxSelected>>", lambda e: self._on_theme_change())

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True)

        self.tab_alerts = ttk.Frame(nb)
        self.tab_wallets = ttk.Frame(nb)
        self.tab_copy = ttk.Frame(nb)
        self.tab_logs = ttk.Frame(nb)
        self.tab_about = ttk.Frame(nb)

        nb.add(self.tab_alerts, text="Markets & Alerts")
        nb.add(self.tab_wallets, text="Wallet Tracker")
        nb.add(self.tab_copy, text="Copy Trading")
        nb.add(self.tab_logs, text="Logs")
        nb.add(self.tab_about, text="About")

        self._build_alerts_tab()
        self._build_wallets_tab()
        self._build_copy_tab()
        self._build_logs_tab()
        self._build_about_tab()

        # status bar
        self.status_var = tk.StringVar(value="Ready.")
        status = ttk.Label(self, textvariable=self.status_var, anchor="w", style="Status.TLabel")
        status.pack(fill="x", side="bottom")

    # ------------------ Theme ------------------

    def _build_theme_palettes(self) -> Dict[str, Dict[str, str]]:
        return {
            "light": {
                "bg": "#f6f4ef",
                "fg": "#1f1f1f",
                "muted": "#6b6b6b",
                "accent": "#2b6e6d",
                "accent_hover": "#245a59",
                "field_bg": "#ffffff",
                "field_fg": "#1f1f1f",
                "border": "#c9c3b8",
                "tab_bg": "#e9e5dd",
                "tab_active_bg": "#f6f4ef",
                "select_bg": "#dfe8e6",
                "select_fg": "#1f1f1f",
                "log_bg": "#fbfaf7",
                "button_bg": "#e6e1d8",
                "button_fg": "#1f1f1f",
            },
            "dark": {
                "bg": "#1e1f24",
                "fg": "#e9e6df",
                "muted": "#a6a9b3",
                "accent": "#63b7af",
                "accent_hover": "#4fa79d",
                "field_bg": "#2a2c33",
                "field_fg": "#e9e6df",
                "border": "#3b3f48",
                "tab_bg": "#2a2c33",
                "tab_active_bg": "#1e1f24",
                "select_bg": "#394048",
                "select_fg": "#e9e6df",
                "log_bg": "#15171b",
                "button_bg": "#2f323a",
                "button_fg": "#e9e6df",
            },
        }

    def _normalize_theme(self, theme: str) -> str:
        return "dark" if str(theme).strip().lower() == "dark" else "light"

    def _theme_label(self, theme: str) -> str:
        return "Dark" if self._normalize_theme(theme) == "dark" else "Light"

    def _theme_from_label(self, label: str) -> str:
        return "dark" if str(label).strip().lower() == "dark" else "light"

    def _on_theme_change(self):
        theme = self._theme_from_label(self.theme_var.get())
        if theme == self.cfg.theme:
            return
        self.cfg.theme = theme
        save_config(self.cfg)
        self._apply_theme(theme)

    def _apply_theme(self, theme: str):
        theme = self._normalize_theme(theme)
        palette = self._themes.get(theme, self._themes["light"])
        self._palette = palette

        try:
            self.style.theme_use("clam")
        except Exception:
            pass

        bg = palette["bg"]
        fg = palette["fg"]
        muted = palette["muted"]
        field_bg = palette["field_bg"]
        field_fg = palette["field_fg"]
        border = palette["border"]
        tab_bg = palette["tab_bg"]
        tab_active_bg = palette["tab_active_bg"]
        select_bg = palette["select_bg"]
        select_fg = palette["select_fg"]
        button_bg = palette["button_bg"]
        button_fg = palette["button_fg"]
        accent = palette["accent"]
        accent_hover = palette["accent_hover"]
        log_bg = palette["log_bg"]

        self.configure(background=bg)

        self.style.configure(".", background=bg, foreground=fg)
        self.style.configure("TFrame", background=bg)
        self.style.configure("TLabel", background=bg, foreground=fg)
        self.style.configure("Status.TLabel", background=bg, foreground=muted)
        self.style.configure("TLabelframe", background=bg, foreground=fg)
        self.style.configure("TLabelframe.Label", background=bg, foreground=fg)

        self.style.configure("TButton", background=button_bg, foreground=button_fg, bordercolor=border)
        self.style.map(
            "TButton",
            background=[("active", accent_hover), ("pressed", accent)],
            foreground=[("active", button_fg), ("pressed", button_fg)],
        )

        self.style.configure("TEntry", fieldbackground=field_bg, foreground=field_fg, background=bg, bordercolor=border)
        self.style.map("TEntry", fieldbackground=[("disabled", tab_bg)], foreground=[("disabled", muted)])

        self.style.configure("TCombobox", fieldbackground=field_bg, background=bg, foreground=field_fg, bordercolor=border)
        self.style.map(
            "TCombobox",
            fieldbackground=[("readonly", field_bg)],
            foreground=[("readonly", field_fg)],
            selectbackground=[("readonly", select_bg)],
            selectforeground=[("readonly", select_fg)],
        )

        self.style.configure("TCheckbutton", background=bg, foreground=fg)
        self.style.map("TCheckbutton", background=[("active", bg)], foreground=[("disabled", muted)])

        self.style.configure("TNotebook", background=bg, bordercolor=border)
        self.style.configure("TNotebook.Tab", background=tab_bg, foreground=fg, padding=(10, 6))
        self.style.map("TNotebook.Tab", background=[("selected", tab_active_bg), ("active", tab_active_bg)])

        self.style.configure(
            "Treeview",
            background=field_bg,
            fieldbackground=field_bg,
            foreground=field_fg,
            bordercolor=border,
        )
        self.style.map("Treeview", background=[("selected", select_bg)], foreground=[("selected", select_fg)])
        self.style.configure("Treeview.Heading", background=tab_bg, foreground=fg)
        self.style.map("Treeview.Heading", background=[("active", tab_active_bg)])

        self.option_add("*TCombobox*Listbox.background", field_bg)
        self.option_add("*TCombobox*Listbox.foreground", field_fg)
        self.option_add("*TCombobox*Listbox.selectBackground", select_bg)
        self.option_add("*TCombobox*Listbox.selectForeground", select_fg)
        self.option_add("*Listbox.background", field_bg)
        self.option_add("*Listbox.foreground", field_fg)
        self.option_add("*Listbox.selectBackground", select_bg)
        self.option_add("*Listbox.selectForeground", select_fg)

        if hasattr(self, "log_text"):
            self.log_text.configure(
                bg=log_bg,
                fg=field_fg,
                insertbackground=field_fg,
                selectbackground=select_bg,
                selectforeground=select_fg,
                highlightbackground=border,
                highlightcolor=border,
            )

        for lb_name in ("outcome_list", "activity_list"):
            lb = getattr(self, lb_name, None)
            if lb is not None:
                lb.configure(
                    bg=field_bg,
                    fg=field_fg,
                    selectbackground=select_bg,
                    selectforeground=select_fg,
                    highlightbackground=border,
                    highlightcolor=border,
                )

    def _icon_path(self) -> Optional[Path]:
        path = Path(__file__).resolve().parent / "assets" / "polymarket.ico"
        return path if path.exists() else None

    def _icon_png_path(self) -> Optional[Path]:
        root = Path(__file__).resolve().parent
        candidates = [
            root / "assets" / "polymarket.png",
            root / "polymarket.png",
        ]
        for path in candidates:
            if path.exists():
                return path
        return None

    def _load_icon_image(self):
        self._icon_images = []
        icon_path = self._icon_png_path()
        if not icon_path:
            return
        try:
            base = tk.PhotoImage(file=str(icon_path))
            self._icon_images.append(base)
            base_w = base.width()
            for size in (256, 128, 64, 32, 16):
                if base_w == size:
                    continue
                if base_w > size and base_w % size == 0:
                    factor = base_w // size
                    self._icon_images.append(base.subsample(factor, factor))
        except Exception:
            self._icon_images = []

    def _apply_window_icon(self, window: tk.Misc):
        icon_path = self._icon_path()
        if not icon_path:
            icon_path = None
        if icon_path:
            try:
                window.iconbitmap(str(icon_path))
            except Exception:
                pass
        if self._icon_images:
            try:
                window.iconphoto(True, *self._icon_images)
            except Exception:
                pass

    # ------------------ Dependency versions ------------------

    def _requirements_path(self) -> Path:
        return Path(__file__).resolve().parent / "requirements.txt"

    def _load_requirements(self) -> List[Dict[str, str]]:
        path = self._requirements_path()
        if not path.exists():
            return []
        reqs: List[Dict[str, str]] = []
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if "#" in line:
                line = line.split("#", 1)[0].strip()
            if ";" in line:
                line = line.split(";", 1)[0].strip()
            if not line:
                continue
            match = re.match(r"([A-Za-z0-9_.-]+)(\[[^\]]+\])?(.*)$", line)
            if not match:
                continue
            name = match.group(1)
            extras = match.group(2) or ""
            spec = (match.group(3) or "").strip()
            reqs.append({"name": name, "display": f"{name}{extras}", "spec": spec})
        return reqs

    def _get_installed_version(self, package: str) -> str:
        try:
            return importlib_metadata.version(package)
        except importlib_metadata.PackageNotFoundError:
            return ""

    def _fetch_latest_version(self, package: str) -> str:
        url = f"https://pypi.org/pypi/{package}/json"
        req = urllib_request.Request(
            url,
            headers={"User-Agent": "prediction-market-alert-and-copy-trade-gui/1.0"},
        )
        try:
            with urllib_request.urlopen(req, timeout=10) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"HTTP {resp.status}")
                data = json.loads(resp.read().decode("utf-8"))
        except (urllib_error.URLError, urllib_error.HTTPError) as exc:
            raise RuntimeError(str(exc)) from exc
        version = str(data.get("info", {}).get("version", "")).strip()
        return version

    def _is_up_to_date(self, installed: str, latest: str) -> bool:
        try:
            from packaging.version import Version
        except Exception:
            return installed == latest
        try:
            return Version(installed) >= Version(latest)
        except Exception:
            return installed == latest

    def _refresh_dependency_table(self, rows: Optional[List[Dict[str, str]]] = None):
        for iid in self.deps_tree.get_children():
            self.deps_tree.delete(iid)

        if rows is None:
            rows = []
            for req in self._requirements:
                installed = self._get_installed_version(req["name"])
                rows.append(
                    {
                        "display": req["display"],
                        "spec": req["spec"],
                        "installed": installed or "not installed",
                        "latest": "",
                        "status": "",
                    }
                )

        if not rows:
            self.deps_tree.insert("", "end", values=("No requirements.txt", "", "", "", ""))
            if hasattr(self, "check_versions_btn"):
                self.check_versions_btn.configure(state="disabled")
            self.dep_status_var.set("No requirements found.")
            return

        for row in rows:
            spec = row.get("spec") or "-"
            self.deps_tree.insert(
                "",
                "end",
                values=(
                    row.get("display", ""),
                    spec,
                    row.get("installed", ""),
                    row.get("latest", ""),
                    row.get("status", ""),
                ),
            )

    def check_dependency_versions(self):
        if self._dep_check_running:
            return
        if not self._requirements:
            self.dep_status_var.set("No requirements found.")
            return
        self._dep_check_running = True
        self.dep_status_var.set("Checking versions...")
        self.check_versions_btn.configure(state="disabled")
        threading.Thread(target=self._check_dependency_versions_bg, daemon=True).start()

    def _check_dependency_versions_bg(self):
        rows: List[Dict[str, str]] = []
        errors: List[str] = []
        for req in self._requirements:
            name = req["name"]
            installed = self._get_installed_version(name) or "not installed"
            latest = ""
            try:
                latest = self._fetch_latest_version(name)
            except Exception as exc:
                errors.append(f"{req['display']}: {exc}")
            if installed == "not installed":
                status = "missing"
            elif latest:
                status = "ok" if self._is_up_to_date(installed, latest) else "outdated"
            else:
                status = "unknown"
            rows.append(
                {
                    "display": req["display"],
                    "spec": req["spec"],
                    "installed": installed,
                    "latest": latest or "-",
                    "status": status,
                }
            )
        self.ui_queue.put(("dep_versions", rows, errors))

    def _build_logs_tab(self):
        frm = self.tab_logs
        self.log_text = tk.Text(frm, height=20, wrap="word")
        self.log_text.pack(fill="both", expand=True, padx=10, pady=10)
        self.log("App started. Price WS connected in background.")

    def _build_about_tab(self):
        frm = self.tab_about
        content = ttk.Frame(frm)
        content.pack(fill="both", expand=True, padx=10, pady=10)

        header = ttk.Label(content, text="Dependency versions")
        header.pack(anchor="w")

        info = ttk.Label(
            content,
            text="Check installed dependencies against the latest versions on PyPI.",
            wraplength=700,
            justify="left",
        )
        info.pack(anchor="w", pady=(4, 10))

        self.dep_status_var = tk.StringVar(value="Versions not checked.")
        ttk.Label(content, textvariable=self.dep_status_var).pack(anchor="w", pady=(0, 8))

        cols = ("package", "required", "installed", "latest", "status")
        self.deps_tree = ttk.Treeview(content, columns=cols, show="headings", height=10)
        for c in cols:
            self.deps_tree.heading(c, text=c)
        self.deps_tree.column("package", width=180, stretch=True)
        self.deps_tree.column("required", width=120, stretch=False, anchor="center")
        self.deps_tree.column("installed", width=120, stretch=False, anchor="center")
        self.deps_tree.column("latest", width=100, stretch=False, anchor="center")
        self.deps_tree.column("status", width=90, stretch=False, anchor="center")
        self.deps_tree.pack(fill="x", pady=(0, 8))

        self.check_versions_btn = ttk.Button(content, text="Check versions", command=self.check_dependency_versions)
        self.check_versions_btn.pack(anchor="w")

        self._refresh_dependency_table()

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
        self._market_outcomes: List[Any] = []
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

    def _set_market_loaded(self, market: Dict[str, Any], fallback_slug: str = ""):
        self._market_loaded = market
        title = market.get("question") or market.get("title") or fallback_slug or "Unknown market"
        slug = market.get("slug") or fallback_slug
        if slug:
            self.market_info_var.set(f"Loaded: {title} (slug: {slug})")
        else:
            self.market_info_var.set(f"Loaded: {title}")
        self._market_outcomes = self._get_polymarket_adapter().parse_market_outcomes(market)

        self.outcome_list.delete(0, "end")
        for o in self._market_outcomes:
            price_str = f"{o.price:.3f}" if isinstance(o.price, float) else "?"
            self.outcome_list.insert("end", f"{o.outcome}  |  token {o.token_id[:10]}...  |  market price {price_str}")

        self.status_var.set("Market loaded. Select an outcome to create an alert.")
        self._selected_token_id = None

    def _select_market_from_event(self, event: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], bool]:
        markets = list(event.get("markets") or [])
        if not markets:
            return None, False

        active = [m for m in markets if m.get("active") or not m.get("closed")]
        if len(active) == 1:
            return active[0], True
        if len(markets) == 1:
            return markets[0], True

        ordered = active + [m for m in markets if m not in active]

        win = tk.Toplevel(self)
        win.title("Select market")
        win.geometry("820x360")
        self._apply_window_icon(win)
        palette = self._palette
        win.configure(background=palette["bg"])

        lb = tk.Listbox(win)
        lb.configure(
            bg=palette["field_bg"],
            fg=palette["field_fg"],
            selectbackground=palette["select_bg"],
            selectforeground=palette["select_fg"],
            highlightbackground=palette["border"],
            highlightcolor=palette["border"],
        )
        lb.pack(fill="both", expand=True, padx=10, pady=10)

        for m in ordered:
            question = m.get("question") or m.get("title") or m.get("slug") or m.get("id") or "Unknown"
            status = "closed" if m.get("closed") else "active" if m.get("active") else "inactive"
            lb.insert("end", f"{question} [{status}]")

        result: Dict[str, Any] = {}

        def load_selected():
            sel = lb.curselection()
            if not sel:
                return
            result["market"] = ordered[sel[0]]
            win.destroy()

        ttk.Button(win, text="Load market", command=load_selected).pack(pady=(0, 10))
        self.status_var.set("Select a market from the event.")
        win.grab_set()
        self.wait_window(win)
        return result.get("market") if result else None, True

    def fetch_market(self):
        if not self._require_polymarket_selected("Market fetch"):
            return
        raw = self.market_entry.get()
        slug = extract_slug(raw)
        if not slug:
            messagebox.showerror("Error", "Enter a market slug or a Polymarket URL.")
            return

        self.status_var.set("Fetching market from Polymarket adapter...")
        self.update_idletasks()
        adapter = self._get_polymarket_adapter()

        try:
            if slug.isdigit():
                m = adapter.get_market_by_id(slug)
                if m:
                    self._set_market_loaded(m)
                    return
                ev = adapter.get_event_by_id(slug)
                if ev:
                    m, has_markets = self._select_market_from_event(ev)
                    if not has_markets:
                        messagebox.showerror("Not found", "Event has no markets.")
                        self.status_var.set("No markets found for event.")
                        return
                    if not m:
                        self.status_var.set("Market selection canceled.")
                        return
                    self._set_market_loaded(m, fallback_slug=str(ev.get("slug") or ""))
                    return
                messagebox.showerror(
                    "Not found",
                    "No market/event found for that numeric id. "
                    "If this is a Polymarket ?tid value, paste the full URL or slug instead.",
                )
                self.status_var.set("Market not found.")
                return

            m = adapter.get_market_by_slug(slug)
            if m:
                self._set_market_loaded(m, fallback_slug=slug)
                return

            ev = adapter.get_event_by_slug(slug)
            if ev:
                m, has_markets = self._select_market_from_event(ev)
                if not has_markets:
                    messagebox.showerror("Not found", "Event has no markets.")
                    self.status_var.set("No markets found for event.")
                    return
                if not m:
                    self.status_var.set("Market selection canceled.")
                    return
                self._set_market_loaded(m, fallback_slug=str(ev.get("slug") or slug))
                return

            messagebox.showerror("Not found", f"Could not fetch market or event for: {slug}")
            self.status_var.set("Market not found.")

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
        if not self._require_polymarket_selected("Wallet tracking"):
            return
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
            profiles = self._get_polymarket_adapter().search_profiles(q, limit=10)
            if not profiles:
                messagebox.showinfo("No results", "No profiles found. Try pasting the 0x wallet address instead.")
                self.status_var.set("No profiles found.")
                return

            # popup select
            win = tk.Toplevel(self)
            win.title("Select profile")
            win.geometry("700x300")
            self._apply_window_icon(win)
            lb = tk.Listbox(win)
            palette = self._palette
            win.configure(background=palette["bg"])
            lb.configure(
                bg=palette["field_bg"],
                fg=palette["field_fg"],
                selectbackground=palette["select_bg"],
                selectforeground=palette["select_fg"],
                highlightbackground=palette["border"],
                highlightcolor=palette["border"],
            )
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
        if not self._require_polymarket_selected("Wallet polling"):
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
        if not self._require_polymarket_selected("Geoblock checks"):
            return
        self.status_var.set("Checking geoblock...")
        self.update_idletasks()
        try:
            geo = self._get_polymarket_adapter().check_geoblock()
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
        if not self._require_polymarket_selected("Copy trading settings"):
            return
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
        if self.cfg.selected_market_id != "polymarket":
            return
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
            book = self._get_polymarket_adapter().get_orderbook(token_id)
            best_bid = book.bids[0].price if book.bids else None
            best_ask = book.asks[0].price if book.asks else None
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
                item = self.ui_queue.get_nowait()
                if isinstance(item, tuple) and len(item) == 2:
                    kind, a = item
                    b = None
                elif isinstance(item, tuple) and len(item) == 3:
                    kind, a, b = item
                else:
                    kind, a, b = "log", f"[debug] unknown queue item: {item}", None
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
                elif kind == "dep_versions":
                    rows = a or []
                    errors = b or []
                    self._refresh_dependency_table(rows)
                    ts = time.strftime("%H:%M:%S")
                    if errors:
                        self.dep_status_var.set(f"Checked at {ts} with errors.")
                        for err in errors:
                            self.log(f"[deps] {err}")
                    else:
                        self.dep_status_var.set(f"Checked at {ts}.")
                    self.check_versions_btn.configure(state="normal")
                    self._dep_check_running = False
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
    set_windows_app_id("prediction-market-alert-and-copy-trade-gui")
    app = App()
    app.mainloop()
