from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.models import AppConfig, CopyTradeSettings, MarketConfig, PaperTradeRecord, PriceAlert, WalletWatch
from core.storage import load_config, save_config
from market_adapters import MARKET_IDS
from polymarket.util import is_wallet_address, normalize_wallet


WALLET = "0x" + "a" * 40


class CoreModelTests(unittest.TestCase):
    def test_wallet_validation_and_normalization(self) -> None:
        self.assertTrue(is_wallet_address(WALLET))
        self.assertEqual(normalize_wallet(WALLET.upper().replace("X", "x", 1)), WALLET)
        self.assertFalse(is_wallet_address("0x123"))
        self.assertIsNone(normalize_wallet("not-a-wallet"))

    def test_config_roundtrip_preserves_alert_wallet_and_copy_settings(self) -> None:
        cfg = AppConfig(
            alerts=[
                PriceAlert(
                    token_id="token-1",
                    label="Yes alert",
                    direction="above",
                    threshold=0.55,
                    source="last_trade",
                )
            ],
            paper_trades=[
                PaperTradeRecord(
                    market_id="kalshi",
                    contract_id="FED-YES:yes",
                    side="BUY",
                    size=3.0,
                    limit_price=0.44,
                    accepted=True,
                    message="DRY RUN",
                    raw={"request": {"ticker": "FED-YES"}},
                )
            ],
            wallets=[
                WalletWatch(
                    wallet=WALLET,
                    display_name="tracked",
                    last_seen_ts=123,
                    last_seen_tx="tx1",
                    seen_activity_keys=["tx:tx1"],
                )
            ],
            copytrading=CopyTradeSettings(
                enabled=True,
                live=False,
                follow_wallet=WALLET,
                scale=0.5,
                max_usdc_per_trade=10.0,
            ),
            selected_market_id="kalshi",
            theme="dark",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config.json"
            save_config(cfg, path)
            loaded = load_config(path)

        self.assertEqual(loaded.theme, "dark")
        self.assertEqual(loaded.selected_market_id, "kalshi")
        self.assertEqual(len(loaded.alerts), 1)
        self.assertEqual(loaded.alerts[0].threshold, 0.55)
        self.assertEqual(loaded.alerts[0].market_id, "polymarket")
        self.assertEqual(len(loaded.paper_trades), 1)
        self.assertEqual(loaded.paper_trades[0].market_id, "kalshi")
        self.assertEqual(loaded.paper_trades[0].limit_price, 0.44)
        self.assertEqual(loaded.paper_trades[0].raw["request"]["ticker"], "FED-YES")
        self.assertEqual(len(loaded.wallets), 1)
        self.assertEqual(loaded.wallets[0].seen_activity_keys, ["tx:tx1"])
        self.assertIn("polymarket", loaded.markets)
        self.assertTrue(loaded.markets["polymarket"].enabled)
        self.assertTrue(loaded.copytrading.enabled)
        self.assertFalse(loaded.copytrading.live)

    def test_default_config_includes_all_catalog_markets(self) -> None:
        cfg = AppConfig()

        self.assertEqual(set(cfg.markets), set(MARKET_IDS))
        self.assertEqual(cfg.selected_market_id, "polymarket")
        self.assertTrue(cfg.markets["polymarket"].enabled)
        disabled = [mid for mid, market_cfg in cfg.markets.items() if not market_cfg.enabled]
        self.assertGreater(len(disabled), 0)

    def test_market_config_roundtrip_preserves_unknown_settings(self) -> None:
        cfg = MarketConfig(
            market_id="kalshi",
            enabled=True,
            settings={"api_key_env": "KALSHI_API_KEY"},
        )

        loaded = MarketConfig.from_dict("kalshi", cfg.to_dict())

        self.assertEqual(loaded.market_id, "kalshi")
        self.assertTrue(loaded.enabled)
        self.assertEqual(loaded.settings, {"api_key_env": "KALSHI_API_KEY"})

    def test_corrupt_config_returns_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config.json"
            path.write_text("{not-json", encoding="utf-8")
            loaded = load_config(path)

        self.assertEqual(loaded.alerts, [])
        self.assertEqual(loaded.wallets, [])
        self.assertEqual(loaded.theme, "light")
        self.assertEqual(loaded.selected_market_id, "polymarket")
        self.assertEqual(set(loaded.markets), set(MARKET_IDS))

    def test_save_config_does_not_clobber_existing_file_when_atomic_replace_fails(self) -> None:
        original = AppConfig(theme="dark", selected_market_id="kalshi")
        replacement = AppConfig(theme="light", selected_market_id="polymarket")

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config.json"
            save_config(original, path)

            with patch("core.storage.os.replace", side_effect=OSError("disk full super-secret-token")):
                with self.assertRaises(OSError):
                    save_config(replacement, path)

            loaded = load_config(path)
            leftovers = list(Path(temp_dir).glob(".config.json.*.tmp"))

        self.assertEqual(loaded.theme, "dark")
        self.assertEqual(loaded.selected_market_id, "kalshi")
        self.assertEqual(leftovers, [])

    def test_unknown_selected_market_falls_back_to_polymarket(self) -> None:
        loaded = AppConfig.from_dict({"selected_market_id": "unknown-market"})

        self.assertEqual(loaded.selected_market_id, "polymarket")

    def test_legacy_alert_without_market_id_defaults_to_polymarket(self) -> None:
        alert = PriceAlert.from_dict(
            {
                "token_id": "token-1",
                "label": "Legacy alert",
                "direction": "above",
                "threshold": 0.5,
            }
        )

        self.assertEqual(alert.market_id, "polymarket")

    def test_paper_trade_record_normalizes_loaded_values(self) -> None:
        record = PaperTradeRecord.from_dict(
            {
                "market_id": "KALSHI",
                "contract_id": "FED-YES:yes",
                "side": "buy",
                "size": "2",
                "limit_price": "",
                "accepted": True,
                "message": "DRY RUN",
            }
        )

        self.assertEqual(record.market_id, "kalshi")
        self.assertEqual(record.side, "BUY")
        self.assertEqual(record.size, 2.0)
        self.assertIsNone(record.limit_price)


if __name__ == "__main__":
    unittest.main()
