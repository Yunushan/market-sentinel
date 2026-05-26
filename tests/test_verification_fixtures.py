from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent
FIXTURE_ROOT = ROOT / "fixtures"


class VerificationFixtureTests(unittest.TestCase):
    def test_fixture_json_files_parse(self) -> None:
        fixture_paths = sorted(FIXTURE_ROOT.glob("**/*.json"))

        self.assertTrue(fixture_paths, "expected offline JSON fixtures")
        for path in fixture_paths:
            with self.subTest(path=path):
                data = json.loads(path.read_text(encoding="utf-8"))
                self.assertIsInstance(data, dict)

    def test_polymarket_fixtures_cover_core_payload_shapes(self) -> None:
        market = json.loads((FIXTURE_ROOT / "polymarket" / "market.json").read_text(encoding="utf-8"))
        event = json.loads((FIXTURE_ROOT / "polymarket" / "event.json").read_text(encoding="utf-8"))
        orderbook = json.loads((FIXTURE_ROOT / "polymarket" / "orderbook.json").read_text(encoding="utf-8"))
        activity = json.loads((FIXTURE_ROOT / "polymarket" / "activity_buy.json").read_text(encoding="utf-8"))

        self.assertIn("clobTokenIds", market)
        self.assertIn("outcomes", market)
        self.assertIsInstance(event.get("markets"), list)
        self.assertIn("bids", orderbook)
        self.assertIn("asks", orderbook)
        self.assertEqual(activity.get("side"), "BUY")
        self.assertIn("asset", activity)

    def test_kalshi_fixtures_cover_core_payload_shapes(self) -> None:
        markets = json.loads((FIXTURE_ROOT / "kalshi" / "markets.json").read_text(encoding="utf-8"))
        orderbook = json.loads((FIXTURE_ROOT / "kalshi" / "orderbook.json").read_text(encoding="utf-8"))

        self.assertIsInstance(markets.get("markets"), list)
        self.assertGreaterEqual(len(markets["markets"]), 1)
        self.assertIn("ticker", markets["markets"][0])
        self.assertIn("event_ticker", markets["markets"][0])
        self.assertIn("orderbook_fp", orderbook)
        self.assertIn("yes_dollars", orderbook["orderbook_fp"])
        self.assertIn("no_dollars", orderbook["orderbook_fp"])

    def test_manifold_fixtures_cover_core_payload_shapes(self) -> None:
        search = json.loads((FIXTURE_ROOT / "manifold" / "search_markets.json").read_text(encoding="utf-8"))
        market = json.loads((FIXTURE_ROOT / "manifold" / "market_binary.json").read_text(encoding="utf-8"))
        multi = json.loads((FIXTURE_ROOT / "manifold" / "market_multi.json").read_text(encoding="utf-8"))
        prob = json.loads((FIXTURE_ROOT / "manifold" / "prob_binary.json").read_text(encoding="utf-8"))

        self.assertIsInstance(search.get("results"), list)
        self.assertIn("id", search["results"][0])
        self.assertEqual(market.get("outcomeType"), "BINARY")
        self.assertIsInstance(multi.get("answers"), list)
        self.assertIn("prob", prob)


if __name__ == "__main__":
    unittest.main()
