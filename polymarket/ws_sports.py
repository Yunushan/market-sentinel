from __future__ import annotations

import json
import threading
import time
from typing import Any, Callable, Dict, Optional

from websocket import WebSocketApp

from .constants import SPORTS_WSS_BASE


SportsEventHandler = Callable[[Dict[str, Any]], None]


def sports_ws_url(url_base: str = SPORTS_WSS_BASE) -> str:
    return url_base.rstrip("/") + "/ws"


class SportsWSClient:
    """Unauthenticated Polymarket sports WebSocket client for live score events."""

    def __init__(
        self,
        on_event: SportsEventHandler,
        *,
        verbose: bool = False,
        url_base: str = SPORTS_WSS_BASE,
    ) -> None:
        self._on_event = on_event
        self._verbose = verbose
        self._url = sports_ws_url(url_base)
        self._stop = threading.Event()
        self._ws: Optional[WebSocketApp] = None
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

    def _run(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            try:
                self._connect_once()
                backoff = 1.0
            except Exception as exc:
                if self._verbose:
                    print("[ws-sports] error:", repr(exc))
            if self._stop.is_set():
                break
            time.sleep(backoff)
            backoff = min(backoff * 2, 30.0)

    def _connect_once(self) -> None:
        def on_message(ws, message: str):
            if message == "ping":
                ws.send("pong")
                return
            try:
                data = json.loads(message)
                if isinstance(data, dict):
                    self._on_event(data)
            except Exception:
                if self._verbose:
                    print("[ws-sports] non-json:", message[:200])

        self._ws = WebSocketApp(self._url, on_message=on_message)
        self._ws.run_forever()
