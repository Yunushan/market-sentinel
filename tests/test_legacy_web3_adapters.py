from __future__ import annotations

import json
import unittest
from pathlib import Path

from market_adapters import (
    AugurAdapter,
    GnosisPredictionMarketsAdapter,
    OmenAdapter,
    PaperOrderRequest,
    RealityEthMarketsAdapter,
    ZeitgeistAdapter,
    ZeitgeistPredictionPoolsAdapter,
    ZeitgeistSdkMarketsAdapter,
)
from market_adapters.errors import MarketConfigurationError, UnsupportedFeatureError


FIXTURES = Path(__file__).resolve().parent / "fixtures"
AUGUR_MARKET_ID = "0xaugurmarket1"
OMEN_FPMM_ID = "0xomenfpmm1"
ZEITGEIST_MARKET_ID = "90"
REALITY_QUESTION_ENTITY_ID = "0xreality-question-1"


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

    def make_omen(self, extra_config=None) -> OmenAdapter:
        config = {"omen_subgraph_url": "https://example.test/omen"}
        config.update(extra_config or {})
        adapter = OmenAdapter(config)
        markets = load_fixture("omen", "fpmms")
        market = load_fixture("omen", "fpmm")

        def fake_request_json(method: str, url: str, *, params=None, json_body=None, headers=None):
            self.assertEqual(method, "POST")
            if url == "https://rpc.example.test/omen":
                self.assertEqual(json_body["method"], "eth_sendRawTransaction")
                return {"jsonrpc": "2.0", "id": 1, "result": "0x" + "ab" * 32}
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

    def make_reality(self) -> RealityEthMarketsAdapter:
        adapter = RealityEthMarketsAdapter({"reality_eth_subgraph_url": "https://example.test/reality"})
        questions = load_fixture("reality_eth_markets", "questions")
        question = load_fixture("reality_eth_markets", "question")

        def fake_request_json(method: str, url: str, *, params=None, json_body=None, headers=None):
            self.assertEqual(method, "POST")
            self.assertEqual(url, "https://example.test/reality")
            query = json_body["query"]
            if "questions(first" in query:
                return questions
            if "question(id" in query:
                self.assertEqual(json_body["variables"]["id"], REALITY_QUESTION_ENTITY_ID)
                return question
            raise AssertionError(f"unexpected Reality.eth query: {query}")

        adapter.runtime.request_json = fake_request_json  # type: ignore[method-assign]
        return adapter

    def make_gnosis(self) -> GnosisPredictionMarketsAdapter:
        adapter = GnosisPredictionMarketsAdapter({"gnosis_subgraph_url": "https://example.test/gnosis"})
        markets = load_fixture("gnosis_prediction_markets", "fpmms")
        market = load_fixture("gnosis_prediction_markets", "fpmm")

        def fake_request_json(method: str, url: str, *, params=None, json_body=None, headers=None):
            self.assertEqual(method, "POST")
            self.assertEqual(url, "https://example.test/gnosis")
            query = json_body["query"]
            if "fixedProductMarketMakers" in query:
                return markets
            if "fixedProductMarketMaker" in query:
                self.assertEqual(json_body["variables"]["id"], OMEN_FPMM_ID)
                return market
            raise AssertionError(f"unexpected Gnosis query: {query}")

        adapter.runtime.request_json = fake_request_json  # type: ignore[method-assign]
        return adapter

    def make_zeitgeist(self, extra_config=None) -> ZeitgeistAdapter:
        adapter = ZeitgeistAdapter(dict(extra_config or {}))
        markets = load_fixture("zeitgeist", "markets")
        market = load_fixture("zeitgeist", "market")
        assets = load_fixture("zeitgeist", "assets")

        def fake_request_json(method: str, url: str, *, params=None, json_body=None, headers=None):
            self.assertEqual(method, "POST")
            if url == "https://rpc.example.test/zeitgeist":
                if json_body["method"] == "state_getRuntimeVersion":
                    return {"jsonrpc": "2.0", "id": 1, "result": {"specVersion": 57}}
                if json_body["method"] == "author_submitExtrinsic":
                    self.assertEqual(json_body["params"][0], "0x" + "ab" * 128)
                    return {"jsonrpc": "2.0", "id": 1, "result": "0x" + "12" * 32}
                raise AssertionError(f"unexpected Zeitgeist RPC method: {json_body['method']}")
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

    def make_zeitgeist_pools(self) -> ZeitgeistPredictionPoolsAdapter:
        adapter = ZeitgeistPredictionPoolsAdapter(
            {"zeitgeist_pools_indexer_url": "https://example.test/zeitgeist-pools"}
        )
        markets = load_fixture("zeitgeist_prediction_pools", "markets")
        market = load_fixture("zeitgeist_prediction_pools", "market")
        assets = load_fixture("zeitgeist_prediction_pools", "assets")

        def fake_request_json(method: str, url: str, *, params=None, json_body=None, headers=None):
            self.assertEqual(method, "POST")
            self.assertEqual(url, "https://example.test/zeitgeist-pools")
            query = json_body["query"]
            if "ZeitgeistMarkets" in query:
                return markets
            if "ZeitgeistMarket" in query:
                self.assertEqual(json_body["variables"]["marketId"], int(ZEITGEIST_MARKET_ID))
                return market
            if "ZeitgeistAsset" in query:
                self.assertEqual(json_body["variables"]["assetId"], "CategoricalOutcome:90:0")
                return assets
            raise AssertionError(f"unexpected Zeitgeist pool query: {query}")

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

    def test_reality_eth_lists_questions_and_response_options_from_official_subgraph(self) -> None:
        adapter = self.make_reality()
        health = adapter.health_check()
        events = adapter.list_events("eth", limit=10)
        contracts = adapter.list_contracts(REALITY_QUESTION_ENTITY_ID)

        self.assertTrue(adapter.capabilities.market_discovery)
        self.assertTrue(adapter.capabilities.alerts)
        self.assertFalse(adapter.capabilities.price_reading)
        self.assertTrue(health["question_schema_supported"])
        self.assertEqual(health["graphql_url_source"], "config:reality_eth_subgraph_url")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event_id, REALITY_QUESTION_ENTITY_ID)
        self.assertEqual(events[0].status, "open")
        self.assertEqual(len(contracts), 2)
        self.assertEqual(contracts[0].outcome, "Yes")

        with self.assertRaises(UnsupportedFeatureError) as ctx:
            adapter.get_price(f"{REALITY_QUESTION_ENTITY_ID}:0xreality-question-1:yes")
        self.assertEqual(ctx.exception.feature, "price_reading")

    def test_reality_eth_requires_a_configured_subgraph_endpoint(self) -> None:
        adapter = RealityEthMarketsAdapter()

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
        with self.assertRaises(MarketConfigurationError):
            adapter.place_live_order(
                PaperOrderRequest(market_id="omen", contract_id=f"{OMEN_FPMM_ID}:0", side="BUY", size=1)
            )

    def test_omen_guarded_live_order_forwards_reviewed_signed_fpmm_transaction(self) -> None:
        adapter = self.make_omen(
            {
                "omen_rpc_url": "https://rpc.example.test/omen",
                "live_trading_enabled": True,
                "live_trading_confirmed": True,
                "omen_submit_signed_transactions": True,
            }
        )
        result = adapter.place_live_order(
            PaperOrderRequest(
                market_id="omen",
                contract_id=f"{OMEN_FPMM_ID}:0",
                side="BUY",
                size=1,
                metadata={
                    "signed_transaction": "0x" + "cd" * 96,
                    "transaction_to": OMEN_FPMM_ID,
                    "method": "buy",
                    "outcome_index": 0,
                    "data": "0x12345678",
                },
            )
        )
        self.assertTrue(result["live"])
        self.assertEqual(result["submission"], "evm_rpc_eth_sendRawTransaction")
        self.assertEqual(result["tx_hash"], "0x" + "ab" * 32)

    def test_gnosis_prediction_markets_alias_uses_official_omen_schema(self) -> None:
        adapter = self.make_gnosis()
        health = adapter.health_check()
        events = adapter.list_events("gnosis", limit=10)
        contracts = adapter.list_contracts(OMEN_FPMM_ID)
        price = adapter.get_price(f"{OMEN_FPMM_ID}:0")
        paper = adapter.place_paper_order(
            PaperOrderRequest(
                market_id="gnosis_prediction_markets",
                contract_id=f"{OMEN_FPMM_ID}:0",
                side="BUY",
                size=2,
            )
        )

        self.assertEqual(adapter.market_id, "gnosis_prediction_markets")
        self.assertEqual(health["alias_of"], "omen")
        self.assertTrue(health["graphql_url_source"].startswith("config"))
        self.assertEqual(len(events), 1)
        self.assertEqual(len(contracts), 2)
        self.assertAlmostEqual(price.last or 0, 0.62)
        self.assertTrue(paper.accepted)

        with self.assertRaises(UnsupportedFeatureError):
            adapter.get_orderbook(f"{OMEN_FPMM_ID}:0")
        with self.assertRaises(MarketConfigurationError):
            adapter.place_live_order(
                PaperOrderRequest(
                    market_id="gnosis_prediction_markets",
                    contract_id=f"{OMEN_FPMM_ID}:0",
                    side="BUY",
                    size=1,
                )
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

        with self.assertRaises(MarketConfigurationError):
            adapter.place_live_order(
                PaperOrderRequest(market_id="zeitgeist", contract_id=f"{ZEITGEIST_MARKET_ID}:0", side="BUY", size=1)
            )

    def test_zeitgeist_guarded_live_order_forwards_reviewed_hybrid_router_extrinsic(self) -> None:
        adapter = self.make_zeitgeist(
            {
                "zeitgeist_rpc_url": "https://rpc.example.test/zeitgeist",
                "live_trading_enabled": True,
                "live_trading_confirmed": True,
                "zeitgeist_submit_signed_extrinsics": True,
            }
        )
        result = adapter.place_live_order(
            PaperOrderRequest(
                market_id="zeitgeist",
                contract_id=f"{ZEITGEIST_MARKET_ID}:0",
                side="BUY",
                size=1,
                metadata={
                    "pallet": "HybridRouter",
                    "call": "buy",
                    "market_id": 90,
                    "outcome_index": 0,
                    "asset": "CategoricalOutcome:90:0",
                    "asset_count": 2,
                    "amount_in": "1000000",
                    "max_price": "900000",
                    "orders": [1, 3],
                    "strategy": "ImmediateOrCancel",
                    "runtime_spec_version": 57,
                    "signed_extrinsic": "0x" + "ab" * 128,
                },
            )
        )
        self.assertTrue(result["live"])
        self.assertEqual(result["submission"], "substrate_rpc_author_submitExtrinsic")
        self.assertEqual(result["extrinsic_hash"], "0x" + "12" * 32)
        self.assertEqual(result["call"], "buy")

    def test_zeitgeist_sdk_markets_alias_uses_explicit_indexer_configuration(self) -> None:
        adapter = ZeitgeistSdkMarketsAdapter({"zeitgeist_sdk_indexer_url": "https://example.test/zeitgeist-sdk"})
        markets = load_fixture("zeitgeist_sdk_markets", "markets")
        market = load_fixture("zeitgeist_sdk_markets", "market")
        assets = load_fixture("zeitgeist_sdk_markets", "assets")

        def fake_request_json(method: str, url: str, *, params=None, json_body=None, headers=None):
            self.assertEqual(method, "POST")
            self.assertEqual(url, "https://example.test/zeitgeist-sdk")
            query = json_body["query"]
            if "ZeitgeistMarkets" in query:
                return markets
            if "ZeitgeistMarket" in query:
                self.assertEqual(json_body["variables"]["marketId"], 90)
                return market
            if "ZeitgeistAsset" in query:
                self.assertEqual(json_body["variables"]["assetId"], "CategoricalOutcome:90:0")
                return assets
            raise AssertionError(f"unexpected Zeitgeist SDK query: {query}")

        adapter.runtime.request_json = fake_request_json  # type: ignore[method-assign]
        events = adapter.list_events("sdk")
        contracts = adapter.list_contracts("90")
        price = adapter.get_price("90:0")
        paper = adapter.place_paper_order(PaperOrderRequest("zeitgeist_sdk_markets", "90:0", "BUY", 2))

        self.assertEqual(adapter.market_id, "zeitgeist_sdk_markets")
        self.assertEqual(events[0].event_id, "90")
        self.assertEqual(contracts[0].outcome, "Yes")
        self.assertAlmostEqual(price.last or 0, 0.8076745721806113)
        self.assertTrue(paper.accepted)

    def test_zeitgeist_prediction_pools_requires_pool_metadata(self) -> None:
        adapter = self.make_zeitgeist_pools()
        health = adapter.health_check()
        events = adapter.list_events("dex", limit=10)
        contracts = adapter.list_contracts(ZEITGEIST_MARKET_ID)
        price = adapter.get_price(f"{ZEITGEIST_MARKET_ID}:0")
        paper = adapter.place_paper_order(PaperOrderRequest("zeitgeist_prediction_pools", "90:0", "BUY", 2))

        self.assertEqual(adapter.market_id, "zeitgeist_prediction_pools")
        self.assertEqual(health["alias_of"], "zeitgeist")
        self.assertTrue(health["pool_schema_supported"])
        self.assertFalse(health["pool_settlement_supported"])
        self.assertTrue(adapter.capabilities.live_trading)
        self.assertEqual(events[0].raw["pool"]["poolId"], 17)
        self.assertEqual(contracts[0].raw["market"]["pool"]["poolId"], 17)
        self.assertAlmostEqual(price.last or 0, 0.8076745721806113)
        self.assertTrue(paper.accepted)

        with self.assertRaises(UnsupportedFeatureError):
            adapter.get_orderbook("90:0")


if __name__ == "__main__":
    unittest.main()
