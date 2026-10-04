from __future__ import annotations

import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from core.models import AppConfig
from core.storage import load_config, save_config
from web_api import (
    MarketConfigConflictError, ReactGuiHandler, ReactGuiServer,
    apply_market_patch, market_configuration_revision,
)


class MarketRevisionTests(unittest.TestCase):
    def test_revision_changes_with_safety_settings_and_enabled_but_not_other_markets(self) -> None:
        cfg = AppConfig()
        original = market_configuration_revision(cfg, "polymarket")
        cfg.markets["kalshi"].enabled = not cfg.markets["kalshi"].enabled
        self.assertEqual(market_configuration_revision(cfg, "polymarket"), original)
        cfg.markets["polymarket"].settings["live_trading_kill_switch"] = True
        self.assertNotEqual(market_configuration_revision(cfg, "polymarket"), original)

    def test_stale_patch_does_not_mutate_any_fields(self) -> None:
        cfg = AppConfig()
        original = market_configuration_revision(cfg, "polymarket")
        cfg.markets["polymarket"].settings["live_trading_kill_switch"] = True
        before = cfg.to_dict()
        with self.assertRaises(MarketConfigConflictError):
            apply_market_patch(cfg, "polymarket", {"expected_revision": original, "enabled": True,
                                                  "settings": {"live_trading_kill_switch": False}})
        self.assertEqual(cfg.to_dict(), before)

    def test_two_http_clients_cannot_overwrite_newer_kill_switch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "config.json"
            cfg = AppConfig()
            cfg.markets["polymarket"].settings["polymarket_order_management_enabled"] = True
            save_config(cfg, path)
            frontend = root / "frontend"
            frontend.mkdir()
            (frontend / "index.html").write_text("<html></html>", encoding="utf-8")
            with patch("web_api.DEFAULT_FRONTEND_DIR", frontend):
                server = ReactGuiServer(("127.0.0.1", 0), ReactGuiHandler, config_path=path, frontend_dir=frontend)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_address[1]}"

            def request(method, endpoint, payload=None):
                data = None if payload is None else json.dumps(payload).encode()
                call = Request(base + endpoint, data=data, method=method, headers={"Content-Type": "application/json"})
                try:
                    response = urlopen(call, timeout=5)
                except HTTPError as error:
                    response = error
                with response:
                    return response.status, json.load(response)

            try:
                status, current = request("GET", "/api/markets")
                self.assertEqual(status, 200)
                original = next(row for row in current["markets"] if row["market_id"] == "polymarket")["configuration_revision"]
                status, _ = request("PATCH", "/api/markets/polymarket", {"expected_revision": original,
                    "settings": {"live_trading_kill_switch": True}})
                self.assertEqual(status, 200)
                before = path.read_bytes()
                status, conflict = request("PATCH", "/api/markets/polymarket", {"expected_revision": original,
                    "settings": {"live_trading_kill_switch": False, "live_trading_enabled": True}})
                self.assertEqual(status, 409)
                self.assertEqual(conflict["error"]["code"], "market_config_conflict")
                self.assertEqual(path.read_bytes(), before)
                status, _ = request("PATCH", "/api/markets/polymarket", {"enabled": True})
                self.assertEqual(status, 428)
                self.assertEqual(path.read_bytes(), before)
                saved = load_config(path)
                self.assertTrue(saved.markets["polymarket"].settings["live_trading_kill_switch"])
                self.assertTrue(saved.markets["polymarket"].settings["polymarket_order_management_enabled"])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
