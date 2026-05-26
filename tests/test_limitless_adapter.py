from __future__ import annotations

import base64
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from market_adapters import LimitlessAdapter, PaperOrderRequest
from market_adapters.errors import MarketConfigurationError, UnsupportedFeatureError


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "limitless_exchange"


def load_fixture(name: str):
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


class FakeResponse:
    status_code = 200
    text = '{"orderId":"order-1"}'

    def json(self):
        return {"orderId": "order-1"}


class LimitlessAdapterTests(unittest.TestCase):
    def make_adapter(self, config=None) -> LimitlessAdapter:
        adapter = LimitlessAdapter(config)
        active = load_fixture("active")
        market = load_fixture("market")
        orderbook = load_fixture("orderbook")

        def fake_get_json(url: str, *, params=None, headers=None):
            if url.endswith("/markets/active"):
                return active
            if url.endswith("/markets/doge-above-021652-sep-1-1200-utc"):
                return market
            if url.endswith("/markets/doge-above-021652-sep-1-1200-utc/orderbook"):
                return orderbook
            raise AssertionError(f"unexpected Limitless URL: {url}")

        adapter.runtime.get_json = fake_get_json  # type: ignore[method-assign]
        return adapter

    def test_registered_metadata_advertises_supported_limitless_features(self) -> None:
        adapter = LimitlessAdapter()
        health = adapter.health_check()

        self.assertTrue(health["ok"])
        self.assertEqual(adapter.market_id, "limitless_exchange")
        self.assertTrue(adapter.capabilities.event_listing)
        self.assertTrue(adapter.capabilities.price_reading)
        self.assertTrue(adapter.capabilities.orderbook_reading)
        self.assertTrue(adapter.capabilities.alerts)
        self.assertTrue(adapter.capabilities.paper_trading)
        self.assertTrue(adapter.capabilities.live_trading)
        self.assertFalse(adapter.capabilities.copy_trading)
        self.assertIn("api.limitless.exchange", health["api_base_url"])
        self.assertIn("ws.limitless.exchange", health["websocket_url"])
        self.assertEqual(health["websocket_namespace"], "/markets")

    def test_list_events_uses_active_market_endpoint_and_filters_query(self) -> None:
        adapter = self.make_adapter()

        events = adapter.list_events("doge", limit=10)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].market_id, "limitless_exchange")
        self.assertEqual(events[0].event_id, "doge-above-021652-sep-1-1200-utc")
        self.assertEqual(events[0].status, "active")
        self.assertIn("DOGE", events[0].title)

    def test_list_contracts_creates_yes_and_no_contracts(self) -> None:
        adapter = self.make_adapter()

        contracts = adapter.list_contracts("doge-above-021652-sep-1-1200-utc")

        self.assertEqual(len(contracts), 2)
        self.assertEqual(contracts[0].contract_id, "doge-above-021652-sep-1-1200-utc:YES")
        self.assertEqual(contracts[1].contract_id, "doge-above-021652-sep-1-1200-utc:NO")
        self.assertEqual(contracts[0].outcome, "Yes")
        self.assertEqual(contracts[1].outcome, "No")

    def test_orderbook_and_price_support_yes_and_no_contracts(self) -> None:
        adapter = self.make_adapter()

        yes_book = adapter.get_orderbook("doge-above-021652-sep-1-1200-utc:YES")
        no_book = adapter.get_orderbook("doge-above-021652-sep-1-1200-utc:NO")
        price = adapter.get_price("doge-above-021652-sep-1-1200-utc:YES")

        self.assertEqual([level.price for level in yes_book.bids], [0.42, 0.4])
        self.assertEqual([level.price for level in yes_book.asks], [0.44, 0.46])
        self.assertEqual([level.price for level in no_book.bids], [0.56, 0.54])
        self.assertEqual([level.price for level in no_book.asks], [0.58, 0.6])
        self.assertEqual(price.bid, 0.42)
        self.assertEqual(price.ask, 0.44)
        self.assertAlmostEqual(price.midpoint or 0, 0.43)

    def test_paper_order_builds_delegated_order_shape_without_live_post(self) -> None:
        adapter = self.make_adapter()

        result = adapter.place_paper_order(
            PaperOrderRequest(
                market_id="limitless_exchange",
                contract_id="doge-above-021652-sep-1-1200-utc:YES",
                side="BUY",
                size=5,
                limit_price=0.43,
                metadata={"order_type": "FAK"},
            )
        )

        self.assertTrue(result.accepted)
        self.assertIn("DRY RUN", result.message)
        self.assertEqual(result.raw["request"]["marketSlug"], "doge-above-021652-sep-1-1200-utc")
        self.assertEqual(result.raw["request"]["orderType"], "FAK")
        self.assertEqual(result.raw["request"]["args"]["tokenId"], "1111111111111111111111111111111111111111111111111111111111111111")

        with self.assertRaises(MarketConfigurationError):
            adapter.place_paper_order(
                PaperOrderRequest(
                    market_id="limitless_exchange",
                    contract_id="doge-above-021652-sep-1-1200-utc:MAYBE",
                    side="BUY",
                    size=5,
                    limit_price=0.43,
                )
            )

    def test_websocket_connection_info_uses_documented_public_market_subscription(self) -> None:
        adapter = self.make_adapter()

        info = adapter.websocket_connection_info(
            market_slugs=["doge-above-021652-sep-1-1200-utc"],
            market_addresses=["0x76d3e2098Be66Aa7E15138F467390f0Eb7349B9b"],
        )

        self.assertEqual(info["url"], "wss://ws.limitless.exchange")
        self.assertEqual(info["namespace"], "/markets")
        self.assertEqual(info["subscribe"]["event"], "subscribe_market_prices")
        self.assertEqual(info["subscribe"]["payload"]["marketSlugs"], ["doge-above-021652-sep-1-1200-utc"])
        self.assertEqual(
            info["subscribe"]["payload"]["marketAddresses"],
            ["0x76d3e2098Be66Aa7E15138F467390f0Eb7349B9b"],
        )

        with self.assertRaises(MarketConfigurationError):
            adapter.websocket_connection_info()

    def test_live_order_is_disabled_by_default(self) -> None:
        adapter = self.make_adapter()

        with self.assertRaises(MarketConfigurationError) as ctx:
            adapter.place_live_order(
                PaperOrderRequest(
                    market_id="limitless_exchange",
                    contract_id="doge-above-021652-sep-1-1200-utc:YES",
                    side="BUY",
                    size=5,
                    limit_price=0.43,
                )
            )

        self.assertIn("disabled", str(ctx.exception))

    def test_live_order_posts_hmac_signed_canonical_json_when_enabled(self) -> None:
        adapter = self.make_adapter({"live_trading_enabled": True, "live_trading_confirmed": True})
        calls = []

        def fake_request(method: str, url: str, *, data=None, headers=None, timeout=None):
            calls.append((method, url, data, headers, timeout))
            return FakeResponse()

        adapter.runtime.session.request = fake_request  # type: ignore[method-assign]
        secret = base64.b64encode(b"unit-test-secret").decode("ascii")

        with patch.dict(
            "os.environ",
            {
                "LIMITLESS_TOKEN_ID": "token-id",
                "LIMITLESS_TOKEN_SECRET": secret,
                "LIMITLESS_ON_BEHALF_OF": "profile-123",
            },
        ):
            result = adapter.place_live_order(
                PaperOrderRequest(
                    market_id="limitless_exchange",
                    contract_id="doge-above-021652-sep-1-1200-utc:NO",
                    side="SELL",
                    size=2,
                    limit_price=0.55,
                    metadata={"order_type": "GTC", "post_only": True},
                )
            )

        self.assertEqual(result["response"]["orderId"], "order-1")
        method, url, body, headers, timeout = calls[0]
        self.assertEqual(method, "POST")
        self.assertTrue(url.endswith("/orders"))
        self.assertIn('"marketSlug":"doge-above-021652-sep-1-1200-utc"', body)
        self.assertIn('"onBehalfOf":"profile-123"', body)
        self.assertEqual(headers["lmts-api-key"], "token-id")
        self.assertTrue(headers["lmts-signature"])
        self.assertIn("T", headers["lmts-timestamp"])
        self.assertGreater(timeout, 0)

    def test_copy_trading_is_clear_unsupported_feature(self) -> None:
        adapter = self.make_adapter()

        with self.assertRaises(UnsupportedFeatureError) as ctx:
            adapter.copy_trade_from_activity({})

        self.assertEqual(ctx.exception.feature, "copy_trading")
        self.assertIn("unsupported", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
