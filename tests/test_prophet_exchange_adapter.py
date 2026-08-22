from __future__ import annotations

import json
import unittest
from pathlib import Path

from market_adapters import PaperOrderRequest, ProphetExchangeAdapter, UnsupportedFeatureError
from market_adapters.errors import MarketConfigurationError


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "prophet_exchange"


def load_fixture(name: str):
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


class ProphetExchangeAdapterTests(unittest.TestCase):
    def _market_data_adapter(self, **config):
        settings = {
            "prophet_exchange_api_key": "api-key",
            "prophet_exchange_api_base_url": "https://api.test/partner",
        }
        settings.update(config)
        adapter = ProphetExchangeAdapter(settings)
        calls = []

        def fake_get_json(url: str, *, params=None, headers=None):
            calls.append((url, dict(params or {}), dict(headers or {})))
            self.assertEqual(headers, {"Authorization": "api-key"})
            if url.endswith("/affiliate/get_sport_events"):
                return load_fixture("sport_events")
            if url.endswith("/v3/affiliate/get_markets"):
                self.assertEqual(params, {"event_id": 101})
                return load_fixture("markets")
            raise AssertionError(f"unexpected ProphetX URL: {url}")

        adapter.runtime.get_json = fake_get_json  # type: ignore[method-assign]
        return adapter, calls

    def test_health_and_documented_capabilities_are_explicit(self) -> None:
        adapter, _ = self._market_data_adapter()
        health = adapter.health_check()

        self.assertTrue(health["ok"])
        self.assertEqual(adapter.market_id, "prophet_exchange")
        self.assertEqual(health["api_version"], "v3")
        self.assertTrue(health["market_data_api_key_required"])
        self.assertTrue(health["trading_api_credentials_required"])
        self.assertTrue(adapter.capabilities.market_discovery)
        self.assertTrue(adapter.capabilities.event_listing)
        self.assertTrue(adapter.capabilities.price_reading)
        self.assertTrue(adapter.capabilities.orderbook_reading)
        self.assertTrue(adapter.capabilities.alerts)
        self.assertTrue(adapter.capabilities.paper_trading)
        self.assertTrue(adapter.capabilities.live_trading)
        self.assertFalse(adapter.capabilities.copy_trading)

    def test_market_data_contracts_quotes_and_paper_order(self) -> None:
        adapter, calls = self._market_data_adapter()
        order = PaperOrderRequest(
            "prophet_exchange",
            "101:555:1:line_1",
            "BUY",
            5,
            0.5,
        )

        events = adapter.list_events("patriots")
        contracts = adapter.list_contracts("101")
        book = adapter.get_orderbook(order.contract_id)
        price = adapter.get_price(order.contract_id)
        paper = adapter.place_paper_order(order)

        self.assertEqual(events[0].event_id, "101")
        self.assertEqual(events[0].title, "Patriots vs. Jets")
        self.assertEqual(
            [contract.contract_id for contract in contracts],
            ["101:555:1:line_1", "101:555:2:line_2", "101:556:3:total_over", "101:556:4:total_under"],
        )
        self.assertAlmostEqual(book.asks[0].price, 1 / 1.95)
        self.assertEqual(book.asks[0].size, 2100.0)
        self.assertAlmostEqual(price.ask or 0.0, 1 / 1.95)
        self.assertEqual(price.source, "prophetx_affiliate_market_data")
        self.assertTrue(paper.accepted)
        self.assertEqual(paper.raw["request"]["strike_id"], "strike_1")
        self.assertEqual(paper.raw["request"]["price"], 2.0)
        self.assertGreaterEqual(len(calls), 5)

        with self.assertRaises(UnsupportedFeatureError):
            adapter.copy_trade_from_activity({})

    def test_live_order_uses_guarded_trading_api_shape(self) -> None:
        adapter = ProphetExchangeAdapter(
            {
                "prophet_exchange_access_token": "fixture-access-token",
                "prophet_exchange_api_base_url": "https://api.test/partner",
                "live_trading_enabled": True,
                "live_trading_confirmed": True,
            }
        )
        calls = []

        def fake_get_json(url: str, *, params=None, headers=None):
            self.assertEqual(headers, {"Authorization": "fixture-access-token"})
            self.assertTrue(url.endswith("/v3/affiliate/get_markets"))
            self.assertEqual(params, {"event_id": 101})
            return load_fixture("markets")

        def fake_request_json(method: str, url: str, *, params=None, json_body=None, headers=None):
            calls.append((method, url, dict(json_body or {}), dict(headers or {})))
            self.assertEqual(method, "POST")
            self.assertEqual(headers["Authorization"], "fixture-access-token")
            self.assertTrue(url.endswith("/mm/submit_order"))
            return load_fixture("order_response")

        adapter.runtime.get_json = fake_get_json  # type: ignore[method-assign]
        adapter.runtime.request_json = fake_request_json  # type: ignore[method-assign]

        result = adapter.place_live_order(
            PaperOrderRequest("prophet_exchange", "101:555:1:line_1", "BUY", 5, 0.5)
        )

        self.assertTrue(result["live"])
        self.assertEqual(result["response"]["data"]["order"]["status"], "accepted")
        self.assertEqual(calls[0][2]["strike_id"], "strike_1")
        self.assertEqual(calls[0][2]["price"], 2.0)
        self.assertEqual(calls[0][2]["quantity"], 5.0)

    def test_contract_and_order_validation_rejects_unsafe_inputs(self) -> None:
        adapter, _ = self._market_data_adapter()
        with self.assertRaises(MarketConfigurationError):
            adapter.get_price("../../private:555:1:line_1")
        with self.assertRaises(MarketConfigurationError):
            adapter.place_paper_order(
                PaperOrderRequest("prophet_exchange", "101:555:1:line_1", "SELL", 1, 0.5)
            )
        with self.assertRaises(MarketConfigurationError):
            adapter.place_paper_order(
                PaperOrderRequest("prophet_exchange", "101:555:1:line_1", "BUY", 1, 0.0)
            )


if __name__ == "__main__":
    unittest.main()
