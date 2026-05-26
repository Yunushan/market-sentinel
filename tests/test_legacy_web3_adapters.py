from __future__ import annotations

import json
import unittest
from pathlib import Path

from market_adapters import AugurAdapter, OmenAdapter, PaperOrderRequest, ZeitgeistAdapter
from market_adapters.errors import MarketConfigurationError, UnsupportedFeatureError


FIXTURES = Path(__file__).resolve().parent / "fixtures"
AUGUR_MARKET_ID = "0xaugurmarket1"
OMEN_FPMM_ID = "0xomenfpmm1"
ZEITGEIST_MARKET_ID = "90"


def load_fixture(market: str, name: str):
    return json.loads((FIXTURES / market / f"{name}.json").read_text(encoding="utf-8"))


class LegacyWeb3AdapterTests(unittest.TestCase):
    def make_augur(self) -> AugurAdapter:
        adapter = AugurAdapter({"augur_subgraph_url": "https://example.test/augur"})
        markets = load_fixture("augur", "markets")
        market = load_fixture("augur", "market")

        def fake_request_json(method: str, url: str, *, params=None, json_body=None, headers=None):
            self.assertEqual(method, "POST")
            self.assertEqual(url, "https://example.test/augur")
            query = json_body["query"]
            if "markets(first" in query:
                return markets
            if "market(id" in query:
                self.assertEqual(json_body["variables"]["id"], AUGUR_MARKET_ID)
                return market
            raise AssertionError(f"unexpected Augur query: {query}")

        adapter.runtime.request_json = fake_request_json  # type: ignore[method-assign]
        return adapter

    def make_omen(self) -> OmenAdapter:
        adapter = OmenAdapter({"omen_subgraph_url": "https://example.test/omen"})
        markets = load_fixture("omen", "fpmms")
        market = load_fixture("omen", "fpmm")

        def fake_request_json(method: str, url: str, *, params=None, json_body=None, headers=None):
            self.assertEqual(method, "POST")
            self.assertEqual(url, "https://example.test/omen")
            query = json_body["query"]
            if "fixedProductMarketMakers" in query:
                return markets
            if "fixedProductMarketMaker" in query:
                self.assertEqual(json_body["variables"]["id"], OMEN_FPMM_ID)
                return market
            raise AssertionError(f"unexpected Omen query: {query}")

        adapter.runtime.request_json = fake_request_json  # type: ignore[method-assign]
        return adapter

    def make_zeitgeist(self) -> ZeitgeistAdapter:
        adapter = ZeitgeistAdapter()
        markets = load_fixture("zeitgeist", "markets")
        market = load_fixture("zeitgeist", "market")
        assets = load_fixture("zeitgeist", "assets")

        def fake_request_json(method: str, url: str, *, params=None, json_body=None, headers=None):
            self.assertEqual(method, "POST")
            self.assertIn("processor.bsr.zeitgeist.pm/graphql", url)
            query = json_body["query"]
            if "ZeitgeistMarkets" in query:
                return markets
            if "ZeitgeistMarket" in query:
                self.assertEqual(json_body["variables"]["marketId"], int(ZEITGEIST_MARKET_ID))
                return market
            if "ZeitgeistAsset" in query:
                self.assertEqual(json_body["variables"]["assetId"], "CategoricalOutcome:90:0")
                return assets
            raise AssertionError(f"unexpected Zeitgeist query: {query}")

        adapter.runtime.request_json = fake_request_json  # type: ignore[method-assign]
        return adapter

    def test_augur_lists_markets_and_outcomes_from_configured_subgraph(self) -> None:
        adapter = self.make_augur()
        health = adapter.health_check()

        self.assertTrue(adapter.capabilities.event_listing)
        self.assertFalse(adapter.capabilities.price_reading)
        self.assertTrue(health["graphql_url_configured"])

        events = adapter.list_events("eth", limit=10)
        contracts = adapter.list_contracts(AUGUR_MARKET_ID)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event_id, AUGUR_MARKET_ID)
        self.assertEqual(events[0].status, "trading")
        self.assertEqual(len(contracts), 3)
        self.assertEqual(contracts[2].outcome, "Yes")

        with self.assertRaises(UnsupportedFeatureError) as ctx:
            adapter.get_price(f"{AUGUR_MARKET_ID}:0xaugurmarket1-2")
        self.assertEqual(ctx.exception.feature, "price_reading")

    def test_augur_requires_subgraph_endpoint_before_network_calls(self) -> None:
        adapter = AugurAdapter()

        with self.assertRaises(MarketConfigurationError) as ctx:
            adapter.list_events()

        self.assertIn("GraphQL endpoint", str(ctx.exception))

    def test_omen_reads_amm_marginal_prices_and_paper_orders(self) -> None:
        adapter = self.make_omen()

        events = adapter.list_events("gnosis", limit=10)
        contracts = adapter.list_contracts(OMEN_FPMM_ID)
        price = adapter.get_price(f"{OMEN_FPMM_ID}:0")
        paper = adapter.place_paper_order(
            PaperOrderRequest(
                market_id="omen",
                contract_id=f"{OMEN_FPMM_ID}:0",
                side="BUY",
                size=12.5,
            )
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].status, "active")
        self.assertEqual(len(contracts), 2)
        self.assertEqual(contracts[0].outcome, "Yes")
        self.assertAlmostEqual(price.last or 0, 0.62)
        self.assertTrue(paper.accepted)
        self.assertAlmostEqual(paper.average_price or 0, 0.62)
        self.assertIn("DRY RUN", paper.message)

        with self.assertRaises(UnsupportedFeatureError):
            adapter.get_orderbook(f"{OMEN_FPMM_ID}:0")
        with self.assertRaises(UnsupportedFeatureError):
            adapter.place_live_order(
                PaperOrderRequest(market_id="omen", contract_id=f"{OMEN_FPMM_ID}:0", side="BUY", size=1)
            )

    def test_zeitgeist_uses_official_indexer_shape_for_prices_and_paper_orders(self) -> None:
        adapter = self.make_zeitgeist()
        health = adapter.health_check()

        self.assertTrue(health["indexer_url_configured"])
        self.assertEqual(health["indexer_url_source"], "default")

        events = adapter.list_events("dex", limit=5)
        contracts = adapter.list_contracts(ZEITGEIST_MARKET_ID)
        price = adapter.get_price(f"{ZEITGEIST_MARKET_ID}:0")
        paper = adapter.place_paper_order(
            PaperOrderRequest(
                market_id="zeitgeist",
                contract_id=f"{ZEITGEIST_MARKET_ID}:0",
                side="SELL",
                size=3,
                limit_price=0.8,
            )
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event_id, ZEITGEIST_MARKET_ID)
        self.assertEqual(len(contracts), 2)
        self.assertEqual(contracts[0].outcome, "Yes")
        self.assertAlmostEqual(price.last or 0, 0.8076745721806113)
        self.assertTrue(paper.accepted)
        self.assertEqual(paper.average_price, 0.8)

        with self.assertRaises(UnsupportedFeatureError):
            adapter.get_orderbook(f"{ZEITGEIST_MARKET_ID}:0")


if __name__ == "__main__":
    unittest.main()
