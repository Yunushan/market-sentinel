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
        ):
            orderbook = adapter.get_orderbook("token-yes")
            price = adapter.get_price("token-yes")

        self.assertEqual(orderbook.bids[0].price, 0.60)
        self.assertEqual(orderbook.bids[0].size, 12.0)
        self.assertEqual(orderbook.asks[0].price, 0.64)
        self.assertEqual(price.bid, 0.60)
        self.assertEqual(price.ask, 0.64)
        self.assertEqual(price.midpoint, 0.62)

    def test_copy_trade_from_activity_uses_paper_order_path(self) -> None:
        adapter = PolymarketAdapter()
        activity = load_fixture("activity_buy.json")

        result = adapter.copy_trade_from_activity(activity)

        self.assertTrue(result.accepted)
        self.assertEqual(result.contract_id, "token-1234567890")
        self.assertIn("BUY", result.message)
        self.assertIn("0.4500", result.message)

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
