from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from market_adapters import MetaculusAdapter, PaperOrderRequest, UnsupportedFeatureError
from market_adapters.errors import MarketConfigurationError


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "metaculus"


def load_fixture(name: str):
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


class MetaculusAdapterTests(unittest.TestCase):
    def make_adapter(self) -> MetaculusAdapter:
        adapter = MetaculusAdapter()
        posts = load_fixture("posts")
        post_binary = load_fixture("post_binary")
        post_multiple = load_fixture("post_multiple")
        post_numeric = load_fixture("post_numeric")

        def fake_get_json(url: str, *, params=None, headers=None):
            self.assertEqual(headers["Authorization"], "Token unit-test-token")
            if url.endswith("/posts/"):
                return posts
            if url.endswith("/posts/1001/"):
                return post_binary
            if url.endswith("/posts/1002/"):
                return post_multiple
            if url.endswith("/posts/1003/"):
                return post_numeric
            raise AssertionError(f"unexpected Metaculus URL: {url}")

        adapter.runtime.get_json = fake_get_json  # type: ignore[method-assign]
        return adapter

    def test_metadata_advertises_read_only_forecasting_capabilities(self) -> None:
        adapter = MetaculusAdapter()
        health = adapter.health_check()

        self.assertTrue(health["ok"])
        self.assertEqual(adapter.market_id, "metaculus")
        self.assertTrue(adapter.capabilities.event_listing)
        self.assertTrue(adapter.capabilities.price_reading)
        self.assertTrue(adapter.capabilities.alerts)
        self.assertFalse(adapter.capabilities.paper_trading)
        self.assertFalse(adapter.capabilities.live_trading)
        self.assertFalse(adapter.capabilities.orderbook_reading)
        self.assertIn("metaculus.com/api", health["api_base_url"])

    def test_missing_api_token_is_clear(self) -> None:
        adapter = MetaculusAdapter()

        with self.assertRaises(MarketConfigurationError) as ctx:
            adapter.list_events("demo")

        self.assertIn("METACULUS_API_TOKEN", str(ctx.exception))

    def test_list_events_reads_authenticated_posts_feed(self) -> None:
        adapter = self.make_adapter()

        with patch.dict("os.environ", {"METACULUS_API_TOKEN": "unit-test-token"}):
            events = adapter.list_events("demo", limit=10)

        self.assertEqual(len(events), 2)
        self.assertEqual(events[0].market_id, "metaculus")
        self.assertEqual(events[0].event_id, "1001")
        self.assertEqual(events[0].status, "open")
        self.assertIn("demo launch", events[0].title)

    def test_list_contracts_maps_binary_multiple_choice_and_numeric_questions(self) -> None:
        adapter = self.make_adapter()

        with patch.dict("os.environ", {"METACULUS_API_TOKEN": "unit-test-token"}):
            binary = adapter.list_contracts("1001")
            multiple = adapter.list_contracts("1002")
            numeric = adapter.list_contracts("1003")

        self.assertEqual([contract.contract_id for contract in binary], ["1001:501:YES", "1001:501:NO"])
        self.assertEqual(
            [contract.contract_id for contract in multiple],
            ["1002:601:CHOICE:alpha", "1002:601:CHOICE:beta"],
        )
        self.assertEqual([contract.contract_id for contract in numeric], ["1003:701:VALUE"])

    def test_get_price_supports_binary_yes_no_choice_and_numeric_values(self) -> None:
        adapter = self.make_adapter()

        with patch.dict("os.environ", {"METACULUS_API_TOKEN": "unit-test-token"}):
            yes = adapter.get_price("1001:501:YES")
            no = adapter.get_price("1001:501:NO")
            choice = adapter.get_price("1002:601:CHOICE:beta")
            value = adapter.get_price("1003:701:VALUE")

        self.assertEqual(yes.last, 0.64)
        self.assertAlmostEqual(no.last or 0, 0.36)
        self.assertEqual(choice.last, 0.75)
        self.assertEqual(value.last, 1250)
        self.assertEqual(yes.source, "metaculus_api")

    def test_unavailable_community_prediction_is_clear(self) -> None:
        adapter = MetaculusAdapter()
        post = {
            "id": 2001,
            "title": "Private forecast",
            "question": {
                "id": 801,
                "title": "Private forecast",
                "type": "binary",
            },
        }

        def fake_get_json(url: str, *, params=None, headers=None):
            return post

        adapter.runtime.get_json = fake_get_json  # type: ignore[method-assign]

        with patch.dict("os.environ", {"METACULUS_API_TOKEN": "unit-test-token"}):
            with self.assertRaises(MarketConfigurationError) as ctx:
                adapter.get_price("2001:801:YES")

        self.assertIn("Community Prediction", str(ctx.exception))

    def test_orderbook_and_trading_are_unsupported(self) -> None:
        adapter = self.make_adapter()

        with self.assertRaises(UnsupportedFeatureError) as orderbook_ctx:
            adapter.get_orderbook("1001:501:YES")
        self.assertEqual(orderbook_ctx.exception.feature, "orderbook_reading")

        with self.assertRaises(UnsupportedFeatureError) as paper_ctx:
            adapter.place_paper_order(
                PaperOrderRequest(
                    market_id="metaculus",
                    contract_id="1001:501:YES",
                    side="BUY",
                    size=1,
                )
            )
        self.assertEqual(paper_ctx.exception.feature, "paper_trading")

        with self.assertRaises(UnsupportedFeatureError) as live_ctx:
            adapter.place_live_order(
                PaperOrderRequest(
                    market_id="metaculus",
                    contract_id="1001:501:YES",
                    side="BUY",
                    size=1,
                )
            )
        self.assertEqual(live_ctx.exception.feature, "live_trading")


if __name__ == "__main__":
    unittest.main()
