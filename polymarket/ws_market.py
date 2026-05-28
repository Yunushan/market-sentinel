from __future__ import annotations

import json
import threading
import time
import queue
from typing import Callable, Dict, Any, Iterable, Optional, Set

from websocket import WebSocketApp  # websocket-client

from .constants import CLOB_WSS_BASE


MarketEventHandler = Callable[[Dict[str, Any]], None]


def build_market_subscription(token_ids: Iterable[str], *, custom_feature_enabled: bool = False) -> Dict[str, Any]:
    msg: Dict[str, Any] = {"assets_ids": [str(x) for x in token_ids if x], "type": "market"}
    if custom_feature_enabled:
        msg["custom_feature_enabled"] = True
    return msg


class MarketWSClient:
    """
    Minimal market-channel WebSocket client for Polymarket CLOB.

    Connects to: wss://ws-subscriptions-clob.polymarket.com/ws/market
    Subscribes with: {"assets_ids":[...], "type":"market"}
    """

    def __init__(
        self,
        token_ids: Iterable[str],
        on_event: MarketEventHandler,
        *,
        custom_feature_enabled: bool = False,
        verbose: bool = False,
        url_base: str = CLOB_WSS_BASE,
    ):
        self._token_ids: Set[str] = set(str(x) for x in token_ids if x)
        self._on_event = on_event
        self._custom_feature_enabled = custom_feature_enabled
        self._verbose = verbose
        self._url = url_base.rstrip("/") + "/ws/market"

        self._stop = threading.Event()
        self._ws: Optional[WebSocketApp] = None

        self._outbox: "queue.Queue[str]" = queue.Queue()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        if not self._thread.is_alive():
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        try:
            if self._ws:
                self._ws.close()
        except Exception:
            pass

    def set_tokens(self, token_ids: Iterable[str]) -> None:
        self._token_ids = set(str(x) for x in token_ids if x)

    def subscribe(self, token_ids: Iterable[str]) -> None:
        ids = [str(x) for x in token_ids if x]
        if not ids:
            return
        for x in ids:
            self._token_ids.add(x)
        msg = {"assets_ids": ids, "operation": "subscribe"}
        if self._custom_feature_enabled:
            msg["custom_feature_enabled"] = True
        self._outbox.put(json.dumps(msg))

    def unsubscribe(self, token_ids: Iterable[str]) -> None:
        ids = [str(x) for x in token_ids if x]
        if not ids:
            return
        for x in ids:
            self._token_ids.discard(x)
        msg = {"assets_ids": ids, "operation": "unsubscribe"}
        if self._custom_feature_enabled:
            msg["custom_feature_enabled"] = True
        self._outbox.put(json.dumps(msg))

    # ---- internal ----

    def _run(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            try:
                self._connect_once()
                backoff = 1.0
            except Exception as e:
                if self._verbose:
                    print("[ws] error:", repr(e))
            if self._stop.is_set():
                break
            time.sleep(backoff)
            backoff = min(backoff * 2, 30.0)

    def _connect_once(self) -> None:
        def on_open(ws):
            if self._verbose:
                print("[ws] open")
            # initial subscribe
            init = build_market_subscription(self._token_ids, custom_feature_enabled=self._custom_feature_enabled)
            ws.send(json.dumps(init))

            # start ping + outbox pumps
            threading.Thread(target=self._ping_loop, args=(ws,), daemon=True).start()
            threading.Thread(target=self._outbox_loop, args=(ws,), daemon=True).start()

        def on_message(ws, message: str):
            if message == "PONG" or message == "PING":
                return
            try:
                data = json.loads(message)
                if isinstance(data, dict):
                    self._on_event(data)
            except Exception:
                # ignore non-JSON or unknown message types
                if self._verbose:
                    print("[ws] non-json:", message[:200])

        def on_error(ws, error):
            if self._verbose:
                print("[ws] error callback:", error)

        def on_close(ws, close_status_code, close_msg):
            if self._verbose:
                print("[ws] close", close_status_code, close_msg)

        self._ws = WebSocketApp(
            self._url,
            on_open=on_open,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
        )
        # Blocks until closed
        self._ws.run_forever()

    def _ping_loop(self, ws) -> None:
        while not self._stop.is_set():
            try:
                ws.send("PING")
            except Exception:
                return
            time.sleep(10)

    def _outbox_loop(self, ws) -> None:
        while not self._stop.is_set():
            try:
                msg = self._outbox.get(timeout=1.0)
            except queue.Empty:
                continue
            try:
                ws.send(msg)
            except Exception:
                return
