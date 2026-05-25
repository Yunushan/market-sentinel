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


if __name__ == "__main__":
    unittest.main()
