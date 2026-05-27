from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from market_adapters import PolymarketAdapter
from market_adapters.errors import MarketConfigurationError
from market_adapters.types import PaperOrderRequest


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "polymarket"


def load_fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class PolymarketAdapterTests(unittest.TestCase):
    def test_list_contracts_maps_gamma_market_outcomes(self) -> None:
        adapter = PolymarketAdapter()
        market = load_fixture("market.json")
        with patch("market_adapters.polymarket.gamma.get_event_by_slug", return_value=None), patch(
            "market_adapters.polymarket.gamma.get_market_by_slug", return_value=market
        ):
            contracts = adapter.list_contracts("market-slug")

        self.assertEqual([c.contract_id for c in contracts], ["token-yes", "token-no"])
        self.assertEqual([c.outcome for c in contracts], ["Yes", "No"])
        self.assertEqual(contracts[0].title, "Will it happen?")
        self.assertEqual(contracts[0].status, "active")

    def test_list_contracts_maps_gamma_event_markets(self) -> None:
        adapter = PolymarketAdapter()
        event = load_fixture("event.json")
        with patch("market_adapters.polymarket.gamma.get_event_by_slug", return_value=event):
            contracts = adapter.list_contracts("event-slug")

        self.assertEqual(len(contracts), 2)
        self.assertEqual(contracts[0].event_id, "market-1")
        self.assertEqual(contracts[0].contract_id, "token-yes")
        self.assertEqual(contracts[1].outcome, "No")

    def test_get_orderbook_and_price_map_clob_payloads(self) -> None:
        adapter = PolymarketAdapter()
        book = load_fixture("orderbook.json")
        with patch("market_adapters.polymarket.clob_rest.get_book", return_value=book), patch(
            "market_adapters.polymarket.clob_rest.get_midpoint", return_value=0.62
        ), patch(
            "market_adapters.polymarket.clob_rest.get_last_trade_price", return_value=0.61
        ):
            orderbook = adapter.get_orderbook("token-yes")
            price = adapter.get_price("token-yes")

        self.assertEqual(orderbook.bids[0].price, 0.60)
        self.assertEqual(orderbook.bids[0].size, 12.0)
        self.assertEqual(orderbook.asks[0].price, 0.64)
        self.assertEqual(price.bid, 0.60)
        self.assertEqual(price.ask, 0.64)
        self.assertEqual(price.last, 0.61)
        self.assertEqual(price.midpoint, 0.62)

    def test_get_orderbook_filters_invalid_levels_and_sorts_book(self) -> None:
        adapter = PolymarketAdapter()
        book = {
            "bids": [
                {"price": "0.40", "size": "10"},
                {"price": "1.50", "size": "10"},
                {"price": "0.55", "size": "0"},
                {"price": "0.50", "size": "4"},
                "bad",
            ],
            "asks": [
                {"price": "0.70", "size": "8"},
                {"price": "-0.10", "size": "8"},
                {"price": "0.62", "size": "3"},
            ],
        }
        with patch("market_adapters.polymarket.clob_rest.get_book", return_value=book):
            orderbook = adapter.get_orderbook("token-yes")

        self.assertEqual([level.price for level in orderbook.bids], [0.50, 0.40])
        self.assertEqual([level.price for level in orderbook.asks], [0.62, 0.70])

    def test_get_price_falls_back_to_book_midpoint_when_midpoint_payload_is_bad(self) -> None:
        adapter = PolymarketAdapter()
        book = {
            "bids": [{"price": "0.40", "size": "10"}],
            "asks": [{"price": "0.60", "size": "8"}],
        }
        with patch("market_adapters.polymarket.clob_rest.get_book", return_value=book), patch(
            "market_adapters.polymarket.clob_rest.get_midpoint", side_effect=RuntimeError("bad midpoint")
        ), patch(
            "market_adapters.polymarket.clob_rest.get_last_trade_price", side_effect=RuntimeError("bad last")
        ):
            price = adapter.get_price("token-yes")

        self.assertEqual(price.midpoint, 0.50)
        self.assertIsNone(price.last)

    def test_list_events_skips_malformed_search_items_and_clamps_limit(self) -> None:
        adapter = PolymarketAdapter()
        payload = {
            "events": [
                {"id": "event-1", "title": "Event 1", "active": True},
                "bad",
                {"slug": "event-2", "question": "Event 2", "closed": True},
            ],
            "markets": "not-a-list",
        }
        with patch("market_adapters.polymarket.gamma.public_search", return_value=payload) as search:
            events = adapter.list_events(" election ", limit=250)

        search.assert_called_once()
        self.assertEqual(search.call_args.kwargs["limit_per_type"], 100)
        self.assertEqual([event.event_id for event in events], ["event-1", "event-2"])
        self.assertEqual(events[0].status, "active")
        self.assertEqual(events[1].status, "closed")

    def test_list_contracts_skips_malformed_event_markets(self) -> None:
        adapter = PolymarketAdapter()
        market = load_fixture("market.json")
        event = {"id": "event-1", "markets": ["bad", market, None]}
        with patch("market_adapters.polymarket.gamma.get_event_by_slug", return_value=event):
            contracts = adapter.list_contracts("event-slug")

        self.assertEqual([c.contract_id for c in contracts], ["token-yes", "token-no"])

    def test_copy_trade_from_activity_uses_paper_order_path(self) -> None:
        adapter = PolymarketAdapter()
        activity = load_fixture("activity_buy.json")

        result = adapter.copy_trade_from_activity(activity)

        self.assertTrue(result.accepted)
        self.assertEqual(result.contract_id, "token-1234567890")
        self.assertIn("BUY", result.message)
        self.assertIn("0.4500", result.message)

    def test_copy_trade_from_activity_rejects_bad_numeric_activity(self) -> None:
        adapter = PolymarketAdapter()
        activity = load_fixture("activity_buy.json")
        activity["size"] = "not-a-size"

        with self.assertRaises(MarketConfigurationError) as ctx:
            adapter.copy_trade_from_activity(activity)

        self.assertIn("size must be numeric", str(ctx.exception))

    def test_paper_order_is_dry_run_and_does_not_fill(self) -> None:
        adapter = PolymarketAdapter()
        result = adapter.place_paper_order(
            PaperOrderRequest(
                market_id="polymarket",
                contract_id="token-yes",
                side="BUY",
                size=5.0,
                limit_price=0.55,
            )
        )

        self.assertTrue(result.accepted)
        self.assertEqual(result.filled_size, 0.0)
        self.assertIn("DRY RUN", result.message)

    def test_live_order_is_disabled_by_adapter_config_by_default(self) -> None:
        adapter = PolymarketAdapter()

        with self.assertRaises(MarketConfigurationError) as ctx:
            adapter.place_live_order(
                PaperOrderRequest(
                    market_id="polymarket",
                    contract_id="token-yes",
                    side="BUY",
                    size=1.0,
                    limit_price=0.5,
                )
            )

        self.assertIn("disabled", str(ctx.exception).lower())

    def test_live_order_requires_limit_before_geoblock_or_credentials(self) -> None:
        adapter = PolymarketAdapter(
            {"live_trading_enabled": True, "live_trading_confirmed": True, "private_key": "not-used"}
        )

        with patch.object(adapter, "check_geoblock") as check_geoblock:
            with self.assertRaises(MarketConfigurationError) as ctx:
                adapter.place_live_order(
                    PaperOrderRequest(
                        market_id="polymarket",
                        contract_id="token-yes",
                        side="BUY",
                        size=1.0,
                        limit_price=None,
                    )
                )

        self.assertIn("requires a limit price", str(ctx.exception))
        check_geoblock.assert_not_called()

    def test_live_order_rejects_bad_signature_type_with_clear_error(self) -> None:
        adapter = PolymarketAdapter(
            {
                "live_trading_enabled": True,
                "live_trading_confirmed": True,
                "private_key": "not-a-real-key",
                "signature_type": "bad",
            }
        )

        with patch.object(adapter, "check_geoblock", return_value={"blocked": False}):
            with self.assertRaises(MarketConfigurationError) as ctx:
                adapter.place_live_order(
                    PaperOrderRequest(
                        market_id="polymarket",
                        contract_id="token-yes",
                        side="BUY",
                        size=1.0,
                        limit_price=0.5,
                    )
                )

        self.assertIn("SIGNATURE_TYPE must be an integer", str(ctx.exception))

    def test_health_check_exposes_runtime_without_secret_values(self) -> None:
        adapter = PolymarketAdapter(
            {
                "live_trading_enabled": True,
                "live_trading_confirmed": True,
                "private_key": "super-secret",
                "http_timeout_seconds": 4,
            }
        )

        health = adapter.health_check()

        self.assertTrue(health["live_trading_enabled"])
        self.assertEqual(health["runtime"]["timeout_seconds"], 4.0)
        self.assertEqual(health["credential_sources"], [{"name": "PRIVATE_KEY", "source": "config:private_key"}])
        self.assertNotIn("super-secret", str(health))

    def test_order_validation_rejects_bad_price(self) -> None:
        adapter = PolymarketAdapter()

        with self.assertRaises(MarketConfigurationError):
            adapter.place_paper_order(
                PaperOrderRequest(
                    market_id="polymarket",
                    contract_id="token-yes",
                    side="BUY",
                    size=1.0,
                    limit_price=1.5,
                )
            )


if __name__ == "__main__":
    unittest.main()
