from __future__ import annotations

import json
import queue
import threading
import time
from typing import Any, Callable, Dict, Iterable, Mapping, Optional, Set

from websocket import WebSocketApp, create_connection

from .constants import CLOB_WSS_BASE


UserEventHandler = Callable[[Dict[str, Any]], None]


def build_user_subscription(auth: Mapping[str, str], markets: Optional[Iterable[str]] = None) -> Dict[str, Any]:
    required = ("apiKey", "secret", "passphrase")
    missing = [key for key in required if not auth.get(key)]
    if missing:
        raise ValueError(f"Polymarket user WebSocket auth is missing: {', '.join(missing)}")
    msg: Dict[str, Any] = {
        "auth": {key: str(auth[key]) for key in required},
        "type": "user",
    }
    market_ids = [str(market) for market in (markets or []) if str(market)]
    if market_ids:
        msg["markets"] = market_ids
    return msg


def user_ws_url(url_base: str = CLOB_WSS_BASE) -> str:
    return url_base.rstrip("/") + "/ws/user"


def probe_user_websocket(
    auth: Mapping[str, str],
    markets: Optional[Iterable[str]] = None,
    *,
    timeout: float = 8.0,
    url_base: str = CLOB_WSS_BASE,
    connection_factory: Optional[Callable[..., Any]] = None,
) -> Dict[str, Any]:
    subscription = build_user_subscription(auth, markets)
    factory = connection_factory or create_connection
    ws = factory(user_ws_url(url_base), timeout=timeout)
    try:
        ws.send(json.dumps(subscription))
        try:
            ws.send("PING")
            message = ws.recv()
        except Exception:
            message = ""
        return {
            "connected": True,
            "subscription_sent": True,
            "received_message": bool(message),
            "message_sample_type": type(message).__name__ if message else "",
        }
    finally:
        close = getattr(ws, "close", None)
        if callable(close):
            close()


class UserWSClient:
    """Authenticated Polymarket user-channel WebSocket client for order/trade events."""

    def __init__(
        self,
        auth: Mapping[str, str],
        markets: Iterable[str],
        on_event: UserEventHandler,
        *,
        verbose: bool = False,
        url_base: str = CLOB_WSS_BASE,
    ) -> None:
        self._auth = dict(auth)
        self._markets: Set[str] = set(str(market) for market in markets if market)
        self._on_event = on_event
        self._verbose = verbose
        self._url = user_ws_url(url_base)
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

    def subscribe(self, markets: Iterable[str]) -> None:
        ids = [str(market) for market in markets if market]
        if not ids:
            return
        for market in ids:
            self._markets.add(market)
        self._outbox.put(json.dumps({"markets": ids, "operation": "subscribe"}))

    def unsubscribe(self, markets: Iterable[str]) -> None:
        ids = [str(market) for market in markets if market]
        if not ids:
            return
        for market in ids:
            self._markets.discard(market)
        self._outbox.put(json.dumps({"markets": ids, "operation": "unsubscribe"}))

    def _run(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            try:
                self._connect_once()
                backoff = 1.0
            except Exception as exc:
                if self._verbose:
                    print("[ws-user] error:", repr(exc))
            if self._stop.is_set():
                break
            time.sleep(backoff)
            backoff = min(backoff * 2, 30.0)

    def _connect_once(self) -> None:
        def on_open(ws):
            ws.send(json.dumps(build_user_subscription(self._auth, self._markets)))
            threading.Thread(target=self._ping_loop, args=(ws,), daemon=True).start()
            threading.Thread(target=self._outbox_loop, args=(ws,), daemon=True).start()

        def on_message(ws, message: str):
            if message in {"PONG", "PING"}:
                return
            try:
                data = json.loads(message)
                if isinstance(data, dict):
                    self._on_event(data)
            except Exception:
                if self._verbose:
                    print("[ws-user] non-json:", message[:200])

        self._ws = WebSocketApp(self._url, on_open=on_open, on_message=on_message)
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
