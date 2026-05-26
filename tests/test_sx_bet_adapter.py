from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from market_adapters import PaperOrderRequest, SxBetAdapter
from market_adapters.errors import MarketConfigurationError, UnsupportedFeatureError


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "sx_bet"
MARKET_HASH = "0x1111111111111111111111111111111111111111111111111111111111111111"


def load_fixture(name: str):
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


class SxBetAdapterTests(unittest.TestCase):
    def make_adapter(self, config=None) -> SxBetAdapter:
        adapter = SxBetAdapter(config)
        active = load_fixture("active_markets")
        market_find = load_fixture("market_find")
        orders = load_fixture("orders")
        best_odds = load_fixture("best_odds")

        def fake_get_json(url: str, *, params=None, headers=None):
            if url.endswith("/markets/active"):
                return active
            if url.endswith("/markets/find"):
                self.assertEqual((params or {}).get("marketHashes"), MARKET_HASH)
                return market_find
            if url.endswith("/orders"):
                self.assertEqual((params or {}).get("marketHashes"), MARKET_HASH)
                return orders
            if url.endswith("/orders/odds/best"):
                return best_odds
            raise AssertionError(f"unexpected SX Bet URL: {url}")

        adapter.runtime.get_json = fake_get_json  # type: ignore[method-assign]
        return adapter

    def test_registered_metadata_advertises_supported_sx_features(self) -> None:
        adapter = SxBetAdapter()
        health = adapter.health_check()

        self.assertTrue(health["ok"])
        self.assertEqual(adapter.market_id, "sx_bet")
        self.assertTrue(adapter.capabilities.event_listing)
        self.assertTrue(adapter.capabilities.price_reading)
        self.assertTrue(adapter.capabilities.orderbook_reading)
        self.assertTrue(adapter.capabilities.alerts)
        self.assertTrue(adapter.capabilities.paper_trading)
        self.assertTrue(adapter.capabilities.live_trading)
        self.assertFalse(adapter.capabilities.copy_trading)
        self.assertIn("api.sx.bet", health["api_base_url"])
        self.assertIn("realtime.sx.bet", health["websocket_url"])

    def test_list_events_uses_active_markets_and_filters_query(self) -> None:
        adapter = self.make_adapter()

        events = adapter.list_events("warriors", limit=10)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].market_id, "sx_bet")
        self.assertEqual(events[0].event_id, MARKET_HASH)
        self.assertEqual(events[0].status, "active")
        self.assertIn("Golden State", events[0].title)

    def test_list_contracts_maps_sports_outcomes(self) -> None:
        adapter = self.make_adapter()

        contracts = adapter.list_contracts(MARKET_HASH)

        self.assertEqual(len(contracts), 2)
        self.assertEqual(contracts[0].contract_id, f"{MARKET_HASH}:ONE")
        self.assertEqual(contracts[1].contract_id, f"{MARKET_HASH}:TWO")
        self.assertEqual(contracts[0].outcome, "Golden State Warriors")
        self.assertEqual(contracts[1].outcome, "Minnesota Timberwolves")

    def test_orderbook_reconstructs_bid_and_ask_prices_from_active_orders(self) -> None:
        adapter = self.make_adapter()

        one_book = adapter.get_orderbook(f"{MARKET_HASH}:ONE")
        two_book = adapter.get_orderbook(f"{MARKET_HASH}:TWO")
        price = adapter.get_price(f"{MARKET_HASH}:ONE")

        self.assertEqual([level.price for level in one_book.bids], [0.4])
        self.assertEqual([level.price for level in one_book.asks], [0.45])
        self.assertAlmostEqual(one_book.bids[0].size, 10.0)
        self.assertAlmostEqual(one_book.asks[0].size, 4.090909, places=5)
        self.assertEqual([level.price for level in two_book.bids], [0.55])
        self.assertEqual([level.price for level in two_book.asks], [0.6])
        self.assertEqual(price.bid, 0.4)
        self.assertEqual(price.ask, 0.45)
        self.assertAlmostEqual(price.midpoint or 0, 0.425)

    def test_paper_order_builds_unsigned_order_payload(self) -> None:
        adapter = self.make_adapter()

        result = adapter.place_paper_order(
            PaperOrderRequest(
                market_id="sx_bet",
                contract_id=f"{MARKET_HASH}:TWO",
                side="BUY",
                size=12.5,
                limit_price=0.57,
                metadata={"maker": "0x740d5718a79A8559fEeE8B00922F8Cd773A81D84", "salt": "99", "api_expiry": 1773511660},
            )
        )

        payload = result.raw["request"]
        self.assertTrue(result.accepted)
        self.assertIn("DRY RUN", result.message)
        self.assertEqual(payload["marketHash"], MARKET_HASH)
        self.assertEqual(payload["totalBetSize"], "12500000")
        self.assertEqual(payload["percentageOdds"], "57000000000000000000")
        self.assertFalse(payload["isMakerBettingOutcomeOne"])

        with self.assertRaises(MarketConfigurationError):
            adapter.place_paper_order(
                PaperOrderRequest(
                    market_id="sx_bet",
                    contract_id=f"{MARKET_HASH}:MAYBE",
                    side="BUY",
                    size=1,
                    limit_price=0.5,
                )
            )

    def test_websocket_connection_info_uses_centrifugo_order_book_channels(self) -> None:
        adapter = self.make_adapter()

        info = adapter.websocket_connection_info(market_hashes=[MARKET_HASH], event_ids=["L18272456"])

        self.assertEqual(info["url"], "wss://realtime.sx.bet/connection/websocket")
        self.assertEqual(info["token_endpoint"], "https://api.sx.bet/user/realtime-token/api-key")
        self.assertEqual(info["requires_api_key_header"], "X-Api-Key")
        self.assertEqual(
            info["channels"],
            [f"order_book:market_{MARKET_HASH}", "order_book:event_L18272456"],
        )
        self.assertTrue(info["subscription_options"]["recoverable"])

        with self.assertRaises(MarketConfigurationError):
            adapter.websocket_connection_info()

    def test_live_order_is_disabled_by_default(self) -> None:
        adapter = self.make_adapter()

        with self.assertRaises(MarketConfigurationError) as ctx:
            adapter.place_live_order(
                PaperOrderRequest(
                    market_id="sx_bet",
                    contract_id=f"{MARKET_HASH}:ONE",
                    side="BUY",
                    size=1,
                    limit_price=0.5,
                )
            )

        self.assertIn("disabled", str(ctx.exception))

    def test_live_order_posts_signed_order_when_enabled(self) -> None:
        adapter = self.make_adapter({"live_trading_enabled": True, "live_trading_confirmed": True})
        calls = []

        def fake_request_json(method: str, url: str, *, params=None, json_body=None, headers=None):
            calls.append((method, url, json_body, headers))
            return {"status": "success", "data": {"orders": ["0xorder"], "inserted": 1}}

        adapter.runtime.request_json = fake_request_json  # type: ignore[method-assign]

        private_key = "0x59c6995e998f97a5a004497e5da6f8ad5b9d4fc9b352b7d483b7246d6368d68c"
        maker = "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC"
        with patch.dict("os.environ", {"SX_BET_PRIVATE_KEY": private_key, "SX_BET_MAKER_ADDRESS": maker}):
            result = adapter.place_live_order(
                PaperOrderRequest(
                    market_id="sx_bet",
                    contract_id=f"{MARKET_HASH}:ONE",
                    side="BUY",
                    size=2,
                    limit_price=0.5,
                    metadata={"salt": "100", "api_expiry": 1773511660},
                )
            )

        self.assertEqual(result["response"]["data"]["inserted"], 1)
        method, url, body, headers = calls[0]
        self.assertEqual(method, "POST")
        self.assertTrue(url.endswith("/orders/new"))
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertEqual(body["orders"][0]["maker"], maker)
        self.assertEqual(body["orders"][0]["totalBetSize"], "2000000")
        self.assertTrue(str(body["orders"][0]["signature"]).startswith("0x"))

    def test_copy_trading_is_clear_unsupported_feature(self) -> None:
        adapter = self.make_adapter()

        with self.assertRaises(UnsupportedFeatureError) as ctx:
            adapter.copy_trade_from_activity({})

        self.assertEqual(ctx.exception.feature, "copy_trading")
        self.assertIn("unsupported", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
