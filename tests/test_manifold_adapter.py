from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from market_adapters import ManifoldAdapter, PaperOrderRequest, UnsupportedFeatureError
from market_adapters.errors import MarketConfigurationError


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "manifold"


def load_fixture(name: str):
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


class ManifoldAdapterTests(unittest.TestCase):
    def make_adapter(self) -> ManifoldAdapter:
        adapter = ManifoldAdapter()
        search = load_fixture("search_markets")
        market_binary = load_fixture("market_binary")
        market_multi = load_fixture("market_multi")
        prob_binary = load_fixture("prob_binary")
        prob_multi = load_fixture("prob_multi")

        def fake_get_json(url: str, *, params=None, headers=None):
            if url.endswith("/search-markets"):
                return search["results"]
            if url.endswith("/market/mf-binary-1"):
                return market_binary
            if url.endswith("/market/mf-multi-1"):
                return market_multi
            if url.endswith("/market/mf-binary-1/prob"):
                return prob_binary
            if url.endswith("/market/mf-multi-1/prob"):
                return prob_multi
            raise AssertionError(f"unexpected Manifold URL: {url}")

        adapter.runtime.get_json = fake_get_json  # type: ignore[method-assign]
        return adapter

    def test_metadata_advertises_documented_manifold_capabilities(self) -> None:
        adapter = ManifoldAdapter()
        health = adapter.health_check()

        self.assertTrue(health["ok"])
        self.assertEqual(adapter.market_id, "manifold")
        self.assertTrue(adapter.capabilities.event_listing)
        self.assertTrue(adapter.capabilities.price_reading)
        self.assertFalse(adapter.capabilities.orderbook_reading)
        self.assertTrue(adapter.capabilities.paper_trading)
        self.assertTrue(adapter.capabilities.live_trading)
        self.assertFalse(adapter.capabilities.copy_trading)
        self.assertIn("api.manifold.markets", health["api_base_url"])

    def test_list_events_uses_search_endpoint_and_maps_markets(self) -> None:
        adapter = self.make_adapter()

        events = adapter.list_events("launch", limit=5)

        self.assertEqual(len(events), 2)
        self.assertEqual(events[0].market_id, "manifold")
        self.assertEqual(events[0].event_id, "mf-binary-1")
        self.assertEqual(events[0].status, "open")
        self.assertIn("demo launch", events[0].title)

    def test_list_contracts_maps_binary_and_multiple_choice_markets(self) -> None:
        adapter = self.make_adapter()

        binary_contracts = adapter.list_contracts("mf-binary-1")
        multi_contracts = adapter.list_contracts("mf-multi-1")

        self.assertEqual([contract.contract_id for contract in binary_contracts], ["mf-binary-1:YES", "mf-binary-1:NO"])
        self.assertEqual([contract.contract_id for contract in multi_contracts], ["mf-multi-1:ANSWER:answer-a", "mf-multi-1:ANSWER:answer-b"])
        self.assertEqual(multi_contracts[0].outcome, "Alpha")

    def test_get_price_supports_binary_yes_no_and_answer_probabilities(self) -> None:
        adapter = self.make_adapter()

        yes_price = adapter.get_price("mf-binary-1:YES")
        no_price = adapter.get_price("mf-binary-1:NO")
        answer_price = adapter.get_price("mf-multi-1:ANSWER:answer-b")

        self.assertEqual(yes_price.last, 0.62)
        self.assertAlmostEqual(no_price.last or 0, 0.38)
        self.assertEqual(answer_price.last, 0.65)
        self.assertEqual(answer_price.source, "manifold_probability")

    def test_orderbook_is_clear_unsupported_feature(self) -> None:
        adapter = self.make_adapter()

        with self.assertRaises(UnsupportedFeatureError) as ctx:
            adapter.get_orderbook("mf-binary-1:YES")

        self.assertEqual(ctx.exception.market_id, "manifold")
        self.assertEqual(ctx.exception.feature, "orderbook_reading")

    def test_paper_order_builds_documented_dry_run_payload(self) -> None:
        adapter = self.make_adapter()
        result = adapter.place_paper_order(
            PaperOrderRequest(
                market_id="manifold",
                contract_id="mf-binary-1:YES",
                side="BUY",
                size=10,
                limit_price=0.62,
            )
        )

        self.assertTrue(result.accepted)
        self.assertIn("DRY RUN", result.message)
        self.assertEqual(result.raw["endpoint"], "/bet")
        self.assertTrue(result.raw["request"]["dryRun"])
        self.assertEqual(result.raw["request"]["limitProb"], 0.62)

    def test_order_validation_rejects_bad_inputs(self) -> None:
        adapter = self.make_adapter()

        with self.assertRaises(MarketConfigurationError):
            adapter.place_paper_order(
                PaperOrderRequest(
                    market_id="manifold",
                    contract_id="mf-binary-1:YES",
                    side="BUY",
                    size=10,
                    limit_price=0.625,
                )
            )

        with self.assertRaises(MarketConfigurationError):
            adapter.place_paper_order(
                PaperOrderRequest(
                    market_id="manifold",
                    contract_id="mf-binary-1:MAYBE",
                    side="BUY",
                    size=10,
                    limit_price=0.62,
                )
            )

    def test_live_trading_is_disabled_by_default(self) -> None:
        adapter = self.make_adapter()

        with self.assertRaises(MarketConfigurationError) as ctx:
            adapter.place_live_order(
                PaperOrderRequest(
                    market_id="manifold",
                    contract_id="mf-binary-1:YES",
                    side="BUY",
                    size=10,
                    limit_price=0.62,
                )
            )

        self.assertIn("disabled", str(ctx.exception))

    def test_live_buy_posts_with_api_key_when_enabled(self) -> None:
        adapter = ManifoldAdapter({"live_trading_enabled": True, "live_trading_confirmed": True})
        calls = []

        def fake_request_json(method: str, url: str, *, params=None, json_body=None, headers=None):
            calls.append((method, url, json_body, headers))
            return {"id": "bet-1", "contractId": "mf-binary-1", "outcome": "YES"}

        adapter.runtime.request_json = fake_request_json  # type: ignore[method-assign]

        with patch.dict("os.environ", {"MANIFOLD_API_KEY": "unit-test-key"}):
            result = adapter.place_live_order(
                PaperOrderRequest(
                    market_id="manifold",
                    contract_id="mf-binary-1:YES",
                    side="BUY",
                    size=10,
                    limit_price=0.62,
                )
            )

        self.assertEqual(result["response"]["id"], "bet-1")
        method, url, payload, headers = calls[0]
        self.assertEqual(method, "POST")
        self.assertTrue(url.endswith("/bet"))
        self.assertEqual(payload["contractId"], "mf-binary-1")
        self.assertEqual(payload["outcome"], "YES")
        self.assertFalse(payload["dryRun"])
        self.assertEqual(headers["Authorization"], "Key unit-test-key")

    def test_live_sell_posts_to_documented_sell_endpoint(self) -> None:
        adapter = ManifoldAdapter({"live_trading_enabled": True, "live_trading_confirmed": True})
        calls = []

        def fake_request_json(method: str, url: str, *, params=None, json_body=None, headers=None):
            calls.append((method, url, json_body, headers))
            return {"sold": True}

        adapter.runtime.request_json = fake_request_json  # type: ignore[method-assign]

        with patch.dict("os.environ", {"MANIFOLD_API_KEY": "unit-test-key"}):
            adapter.place_live_order(
                PaperOrderRequest(
                    market_id="manifold",
                    contract_id="mf-binary-1:NO",
                    side="SELL",
                    size=4,
                )
            )

        method, url, payload, headers = calls[0]
        self.assertEqual(method, "POST")
        self.assertTrue(url.endswith("/market/mf-binary-1/sell"))
        self.assertEqual(payload, {"shares": 4.0, "outcome": "NO"})
        self.assertEqual(headers["Authorization"], "Key unit-test-key")

    def test_live_buy_for_single_answer_is_blocked_until_documented_shape_maps_safely(self) -> None:
        adapter = ManifoldAdapter({"live_trading_enabled": True, "live_trading_confirmed": True})

        with patch.dict("os.environ", {"MANIFOLD_API_KEY": "unit-test-key"}):
            with self.assertRaises(MarketConfigurationError) as ctx:
                adapter.place_live_order(
                    PaperOrderRequest(
                        market_id="manifold",
                        contract_id="mf-multi-1:ANSWER:answer-a",
                        side="BUY",
                        size=10,
                    )
                )

        self.assertIn("multi-bet", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
