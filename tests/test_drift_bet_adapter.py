from __future__ import annotations

import json
import unittest
from pathlib import Path

from market_adapters import DriftBetAdapter, PaperOrderRequest, UnsupportedFeatureError
from market_adapters.errors import MarketConfigurationError, MarketHTTPError


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "drift_bet"


def load_fixture(name: str):
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


class DriftBetAdapterTests(unittest.TestCase):
    def make_adapter(self, config=None):
        settings = {"drift_bet_market_symbols": [{"symbol": "BTC-ELECTION-BET", "title": "BTC election"}]}
        settings.update(config or {})
        adapter = DriftBetAdapter(settings)
        calls = []

        def fake_get_json(url: str, *, params=None, headers=None):
            calls.append((url, dict(params or {}), dict(headers or {})))
            self.assertEqual(headers, {})
            self.assertTrue(url.endswith("/market/BTC-ELECTION-BET/predictions"))
            return load_fixture("predictions")

        adapter.runtime.get_json = fake_get_json  # type: ignore[method-assign]
        return adapter, calls

    def test_health_and_catalog_surfaces_are_explicit(self) -> None:
        adapter, _ = self.make_adapter()
        health = adapter.health_check()

        self.assertTrue(health["ok"])
        self.assertEqual(adapter.market_id, "drift_bet")
        self.assertEqual(health["configured_market_symbols"], ["BTC-ELECTION-BET"])
        self.assertFalse(health["dynamic_discovery"])
        self.assertTrue(adapter.capabilities.event_listing)
        self.assertTrue(adapter.capabilities.price_reading)
        self.assertTrue(adapter.capabilities.paper_trading)
        self.assertFalse(adapter.capabilities.orderbook_reading)
        self.assertFalse(adapter.capabilities.live_trading)
        self.assertFalse(adapter.capabilities.copy_trading)

    def test_events_contracts_price_and_paper_order_use_official_prediction_shape(self) -> None:
        adapter, calls = self.make_adapter()

        events = adapter.list_events("election")
        contracts = adapter.list_contracts(events[0].event_id)
        yes_price = adapter.get_price("BTC-ELECTION-BET:YES")
        no_price = adapter.get_price("BTC-ELECTION-BET:NO")
        paper = adapter.place_paper_order(
            PaperOrderRequest("drift_bet", "BTC-ELECTION-BET:YES", "BUY", 3, 0.62)
        )

        self.assertEqual(events[0].event_id, "drift:BTC-ELECTION-BET")
        self.assertEqual(events[0].status, "active")
        self.assertEqual([contract.outcome for contract in contracts], ["YES", "NO"])
        self.assertAlmostEqual(yes_price.midpoint or 0.0, 0.62)
        self.assertAlmostEqual(no_price.midpoint or 0.0, 0.38)
        self.assertEqual(yes_price.source, "drift_data_api_predictions")
        self.assertTrue(paper.accepted)
        self.assertEqual(paper.average_price, 0.62)
        self.assertGreaterEqual(len(calls), 4)

    def test_missing_inventory_and_unsupported_features_fail_clearly(self) -> None:
        adapter = DriftBetAdapter()
        with self.assertRaises(MarketConfigurationError):
            adapter.list_events()

        adapter, _ = self.make_adapter()
        order = PaperOrderRequest("drift_bet", "BTC-ELECTION-BET:YES", "BUY", 1, 0.5)
        with self.assertRaises(UnsupportedFeatureError):
            adapter.get_orderbook(order.contract_id)
        with self.assertRaises(UnsupportedFeatureError):
            adapter.place_live_order(order)
        with self.assertRaises(UnsupportedFeatureError):
            adapter.copy_trade_from_activity({})

        adapter.runtime.get_json = lambda *args, **kwargs: {"success": True, "records": []}  # type: ignore[method-assign]
        with self.assertRaises(MarketHTTPError):
            adapter.get_price(order.contract_id)

    def test_symbol_and_order_validation_blocks_path_injection(self) -> None:
        with self.assertRaises(MarketConfigurationError):
            DriftBetAdapter({"drift_bet_market_symbols": ["../private"]}).health_check()

        adapter, _ = self.make_adapter()
        for contract_id in ("../private:YES", "BTC-ELECTION-BET:maybe"):
            with self.subTest(contract_id=contract_id):
                with self.assertRaises(MarketConfigurationError):
                    adapter.get_price(contract_id)
        with self.assertRaises(MarketConfigurationError):
            adapter.place_paper_order(PaperOrderRequest("drift_bet", "BTC-ELECTION-BET:YES", "HOLD", 1, 0.5))


if __name__ == "__main__":
    unittest.main()
