from __future__ import annotations

import queue
import unittest
from unittest.mock import patch

from app import (
    App,
    WalletPoller,
    activity_key,
    extract_slug,
    market_choice_label,
    market_id_from_choice,
    safe_float,
)
from core.models import AppConfig, CopyTradeSettings, PriceAlert, WalletWatch
from market_adapters import MARKET_IDS, build_default_registry
from market_adapters.types import MarketMetadata, OrderBookLevel, OrderBookSnapshot


WALLET = "0x" + "b" * 40


class FakeVar:
    def __init__(self, value: str = "") -> None:
        self.value = value

    def get(self) -> str:
        return self.value

    def set(self, value: str) -> None:
        self.value = value


class MarketSelectionHarness:
    def __init__(self) -> None:
        self.cfg = AppConfig()
        self.adapter_registry = build_default_registry()
        self.market_var = FakeVar()
        self.status_var = FakeVar()
        self.ui_queue: "queue.Queue[tuple]" = queue.Queue()

    def _market_label_for_id(self, market_id: str) -> str:
        return App._market_label_for_id(self, market_id)

    def _get_selected_market_adapter(self):
        return App._get_selected_market_adapter(self)

    def _selected_market_display_name(self) -> str:
        return App._selected_market_display_name(self)


class AlertHarness:
    def __init__(self, alert: PriceAlert, value: float) -> None:
        self.cfg = AppConfig(alerts=[alert])
        self.price_state = {alert.token_id: {alert.source: value}}
        self.fired = []
        self.refreshed = False

    def _fire_alert(self, alert: PriceAlert, value: float) -> None:
        self.fired.append((alert.id, value))

    def _refresh_alert_table(self) -> None:
        self.refreshed = True


class CopyHarness:
    def __init__(self) -> None:
        self.cfg = AppConfig(
            copytrading=CopyTradeSettings(
                enabled=True,
                live=False,
                follow_wallet=WALLET,
                scale=2.0,
                max_usdc_per_trade=5.0,
                slippage=0.02,
            )
        )
        self.ui_queue: "queue.Queue[tuple]" = queue.Queue()
        self._geoblock_cache = None
        self.polymarket_adapter = FakePolymarketAdapter()

    def _get_polymarket_adapter(self) -> "FakePolymarketAdapter":
        return self.polymarket_adapter


class FakePolymarketAdapter:
    def get_orderbook(self, contract_id: str) -> OrderBookSnapshot:
        return OrderBookSnapshot(
            market_id="polymarket",
            contract_id=contract_id,
            bids=[OrderBookLevel(price=0.48, size=10.0)],
            asks=[OrderBookLevel(price=0.50, size=10.0)],
        )


class AppLogicTests(unittest.TestCase):
    def test_slug_and_float_helpers(self) -> None:
        self.assertEqual(
            extract_slug("https://polymarket.com/event/some-market?tid=123#details"),
            "some-market",
        )
        self.assertEqual(extract_slug("/some-market/"), "some-market")
        self.assertEqual(safe_float("0.25"), 0.25)
        self.assertIsNone(safe_float("bad"))
        self.assertEqual(safe_float("bad", 1.5), 1.5)

    def test_activity_key_prefers_transaction_hash(self) -> None:
        self.assertEqual(activity_key({"transactionHash": "0xABC"}), "tx:0xabc")
        fallback = activity_key({"timestamp": 1, "asset": "token", "side": "BUY"})
        self.assertTrue(fallback.startswith("activity:1|"))

    def test_market_choice_label_roundtrip(self) -> None:
        metadata = MarketMetadata(market_id="kalshi", display_name="Kalshi")
        label = market_choice_label(metadata)

        self.assertEqual(label, "Kalshi (kalshi)")
        self.assertEqual(market_id_from_choice(label), "kalshi")
        self.assertEqual(market_id_from_choice("polymarket"), "polymarket")

    def test_market_choices_cover_all_catalog_markets(self) -> None:
        harness = MarketSelectionHarness()

        choices = App._market_choices(harness)
        market_ids = {market_id_from_choice(choice) for choice in choices}

        self.assertEqual(market_ids, set(MARKET_IDS))
        self.assertIn("Polymarket (polymarket)", choices)

    def test_market_change_persists_kalshi_selection_without_gui_window(self) -> None:
        harness = MarketSelectionHarness()
        harness.market_var.set("Kalshi (kalshi)")

        with patch("app.save_config") as save_config:
            App._on_market_change(harness)

        self.assertEqual(harness.cfg.selected_market_id, "kalshi")
        self.assertEqual(harness.market_var.get(), "Kalshi (kalshi)")
        self.assertEqual(harness.status_var.get(), "Selected market: Kalshi.")
        self.assertEqual(harness.ui_queue.get_nowait()[0], "log")
        save_config.assert_called_once_with(harness.cfg)

    def test_stub_market_blocks_polymarket_only_actions(self) -> None:
        harness = MarketSelectionHarness()
        harness.cfg.selected_market_id = "kalshi"

        with patch("app.messagebox.showinfo") as showinfo:
            result = App._require_polymarket_selected(harness, "Market fetch")

        self.assertFalse(result)
        self.assertIn("currently implemented only for Polymarket", harness.status_var.get())
        self.assertIn("has not been generalized", harness.status_var.get())
        self.assertEqual(harness.ui_queue.get_nowait()[0], "log")
        showinfo.assert_called_once()

    def test_polymarket_selection_allows_polymarket_only_actions(self) -> None:
        harness = MarketSelectionHarness()
        harness.cfg.selected_market_id = "polymarket"

        with patch("app.messagebox.showinfo") as showinfo:
            result = App._require_polymarket_selected(harness, "Market fetch")

        self.assertTrue(result)
        self.assertTrue(harness.ui_queue.empty())
        showinfo.assert_not_called()

    def test_once_alert_fires_and_disables_on_crossing(self) -> None:
        alert = PriceAlert(
            token_id="token-1",
            label="Yes",
            direction="above",
            threshold=0.5,
            source="last_trade",
            once=True,
        )
        harness = AlertHarness(alert, 0.51)

        with patch("app.save_config") as save_config:
            App._eval_alerts_for_token(harness, "token-1")

        self.assertEqual(harness.fired, [(alert.id, 0.51)])
        self.assertTrue(alert.triggered)
        self.assertFalse(alert.enabled)
        self.assertTrue(harness.refreshed)
        save_config.assert_called_once()

    def test_repeat_alert_resets_after_condition_clears(self) -> None:
        alert = PriceAlert(
            token_id="token-1",
            label="Yes",
            direction="above",
            threshold=0.5,
            source="last_trade",
            once=False,
            triggered=True,
            last_value=0.6,
        )
        harness = AlertHarness(alert, 0.49)

        with patch("app.save_config") as save_config:
            App._eval_alerts_for_token(harness, "token-1")

        self.assertFalse(alert.triggered)
        self.assertEqual(harness.fired, [])
        self.assertTrue(harness.refreshed)
        save_config.assert_called_once()

    def test_copy_trade_simulation_uses_best_ask_slippage_and_max_usdc_cap(self) -> None:
        harness = CopyHarness()
        item = {
            "proxyWallet": WALLET,
            "side": "BUY",
            "asset": "token-1234567890",
            "size": "100",
            "price": "0.45",
        }

        App._copy_trade_from_activity(harness, item)

        kind, message = harness.ui_queue.get_nowait()
        self.assertEqual(kind, "log")
        self.assertIn("[copy SIM] BUY", message)
        self.assertIn("size=9.6154", message)
        self.assertIn("price<= 0.5200", message)

    def test_wallet_poller_deduplicates_same_timestamp_transactions(self) -> None:
        cfg = AppConfig(wallets=[WalletWatch(wallet=WALLET, display_name="tracked")])
        ui_queue: "queue.Queue[tuple]" = queue.Queue()
        poller = WalletPoller(ui_queue, cfg, poll_interval=0.01)
        responses = [
            [
                {"timestamp": 100, "transactionHash": "tx2", "slug": "m"},
                {"timestamp": 100, "transactionHash": "tx1", "slug": "m"},
            ],
            [
                {"timestamp": 100, "transactionHash": "tx2", "slug": "m"},
                {"timestamp": 100, "transactionHash": "tx1", "slug": "m"},
            ],
        ]
        calls = 0

        def fake_get_activity(*_args, **_kwargs):
            nonlocal calls
            response = responses[calls]
            calls += 1
            if calls == len(responses):
                poller.stop()
            return response

        with patch("app.data_api.get_activity", side_effect=fake_get_activity):
            poller._run()

        drained = []
        while not ui_queue.empty():
            drained.append(ui_queue.get_nowait())

        activity = [item for item in drained if item[0] == "wallet_activity"]
        self.assertEqual([item[2]["transactionHash"] for item in activity], ["tx1", "tx2"])
        self.assertEqual(cfg.wallets[0].last_seen_ts, 100)
        self.assertEqual(set(cfg.wallets[0].seen_activity_keys), {"tx:tx1", "tx:tx2"})


if __name__ == "__main__":
    unittest.main()
