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

    def test_metaculus_fixtures_cover_core_payload_shapes(self) -> None:
        posts = json.loads((FIXTURE_ROOT / "metaculus" / "posts.json").read_text(encoding="utf-8"))
        binary = json.loads((FIXTURE_ROOT / "metaculus" / "post_binary.json").read_text(encoding="utf-8"))
        multiple = json.loads((FIXTURE_ROOT / "metaculus" / "post_multiple.json").read_text(encoding="utf-8"))
        numeric = json.loads((FIXTURE_ROOT / "metaculus" / "post_numeric.json").read_text(encoding="utf-8"))

        self.assertIsInstance(posts.get("results"), list)
        self.assertIn("question", binary)
        self.assertIn("aggregations", binary["question"])
        self.assertIsInstance(multiple.get("questions"), list)
        self.assertEqual(numeric["question"].get("type"), "numeric")

    def test_predictit_fixtures_cover_core_payload_shapes(self) -> None:
        all_markets = json.loads((FIXTURE_ROOT / "predictit" / "all.json").read_text(encoding="utf-8"))
        market = json.loads((FIXTURE_ROOT / "predictit" / "market.json").read_text(encoding="utf-8"))

        self.assertIsInstance(all_markets.get("markets"), list)
        self.assertGreaterEqual(len(all_markets["markets"]), 1)
        self.assertIn("contracts", all_markets["markets"][0])
        self.assertIsInstance(market.get("contracts"), list)
        self.assertIn("bestBuyYesCost", market["contracts"][0])
        self.assertIn("bestSellNoCost", market["contracts"][0])

    def test_limitless_fixtures_cover_core_payload_shapes(self) -> None:
        active = json.loads((FIXTURE_ROOT / "limitless_exchange" / "active.json").read_text(encoding="utf-8"))
        market = json.loads((FIXTURE_ROOT / "limitless_exchange" / "market.json").read_text(encoding="utf-8"))
        orderbook = json.loads((FIXTURE_ROOT / "limitless_exchange" / "orderbook.json").read_text(encoding="utf-8"))

        self.assertIsInstance(active.get("data"), list)
        self.assertGreaterEqual(len(active["data"]), 1)
        self.assertIn("slug", active["data"][0])
        self.assertIn("positionIds", market)
        self.assertIn("tokens", market)
        self.assertIsInstance(orderbook.get("bids"), list)
        self.assertIsInstance(orderbook.get("asks"), list)

    def test_sx_bet_fixtures_cover_core_payload_shapes(self) -> None:
        active = json.loads((FIXTURE_ROOT / "sx_bet" / "active_markets.json").read_text(encoding="utf-8"))
        market_find = json.loads((FIXTURE_ROOT / "sx_bet" / "market_find.json").read_text(encoding="utf-8"))
        orders = json.loads((FIXTURE_ROOT / "sx_bet" / "orders.json").read_text(encoding="utf-8"))
        best_odds = json.loads((FIXTURE_ROOT / "sx_bet" / "best_odds.json").read_text(encoding="utf-8"))

        self.assertIsInstance(active.get("data", {}).get("markets"), list)
        self.assertIn("marketHash", active["data"]["markets"][0])
        self.assertIsInstance(market_find.get("data"), list)
        self.assertIsInstance(orders.get("data"), list)
        self.assertIn("percentageOdds", orders["data"][0])
        self.assertIsInstance(best_odds.get("data", {}).get("bestOdds"), list)

    def test_azuro_fixtures_cover_core_payload_shapes(self) -> None:
        games = json.loads((FIXTURE_ROOT / "azuro" / "games_by_filters.json").read_text(encoding="utf-8"))
        game = json.loads((FIXTURE_ROOT / "azuro" / "games_by_ids.json").read_text(encoding="utf-8"))
        conditions = json.loads((FIXTURE_ROOT / "azuro" / "conditions_by_game_ids.json").read_text(encoding="utf-8"))
        order = json.loads((FIXTURE_ROOT / "azuro" / "order_response.json").read_text(encoding="utf-8"))

        self.assertIsInstance(games.get("games"), list)
        self.assertIn("gameId", games["games"][0])
        self.assertIsInstance(game.get("games"), list)
        self.assertIsInstance(conditions.get("conditions"), list)
        self.assertIn("outcomes", conditions["conditions"][0])
        self.assertIn("currentOdds", conditions["conditions"][0]["outcomes"][0])
        self.assertIn("state", order)

    def test_legacy_web3_fixtures_cover_core_payload_shapes(self) -> None:
        augur_markets = json.loads((FIXTURE_ROOT / "augur" / "markets.json").read_text(encoding="utf-8"))
        omen_markets = json.loads((FIXTURE_ROOT / "omen" / "fpmms.json").read_text(encoding="utf-8"))
        zeitgeist_markets = json.loads((FIXTURE_ROOT / "zeitgeist" / "markets.json").read_text(encoding="utf-8"))
        zeitgeist_assets = json.loads((FIXTURE_ROOT / "zeitgeist" / "assets.json").read_text(encoding="utf-8"))

        self.assertIsInstance(augur_markets.get("data", {}).get("markets"), list)
        self.assertIn("outcomes", augur_markets["data"]["markets"][0])
        self.assertIsInstance(omen_markets.get("data", {}).get("fixedProductMarketMakers"), list)
        self.assertIn("outcomeTokenMarginalPrices", omen_markets["data"]["fixedProductMarketMakers"][0])
        self.assertIsInstance(zeitgeist_markets.get("data", {}).get("markets"), list)
        self.assertIn("outcomeAssets", zeitgeist_markets["data"]["markets"][0])
        self.assertIsInstance(zeitgeist_assets.get("data", {}).get("assets"), list)
        self.assertIn("price", zeitgeist_assets["data"]["assets"][0])

    def test_additional_official_adapter_fixtures_cover_core_payload_shapes(self) -> None:
        gemini_events = json.loads((FIXTURE_ROOT / "gemini" / "events.json").read_text(encoding="utf-8"))
        myriad_questions = json.loads((FIXTURE_ROOT / "myriad_markets" / "questions.json").read_text(encoding="utf-8"))
        opinion_markets = json.loads((FIXTURE_ROOT / "opinion_labs" / "markets.json").read_text(encoding="utf-8"))
        predict_markets = json.loads((FIXTURE_ROOT / "predict_fun" / "markets.json").read_text(encoding="utf-8"))
        xo_markets = json.loads((FIXTURE_ROOT / "xo_market" / "markets.json").read_text(encoding="utf-8"))
        betfair_catalogue = json.loads(
            (FIXTURE_ROOT / "betfair_exchange" / "market_catalogue.json").read_text(encoding="utf-8")
        )
        myriad_orderbook = json.loads((FIXTURE_ROOT / "myriad_markets" / "orderbook.json").read_text(encoding="utf-8"))
        gemini_order = json.loads((FIXTURE_ROOT / "gemini" / "order_response.json").read_text(encoding="utf-8"))
        predict_order = json.loads((FIXTURE_ROOT / "predict_fun" / "order_response.json").read_text(encoding="utf-8"))
        betfair_order = json.loads(
            (FIXTURE_ROOT / "betfair_exchange" / "place_order_response.json").read_text(encoding="utf-8")
        )

        self.assertIsInstance(gemini_events.get("data"), list)
        self.assertIn("contracts", gemini_events["data"][0])
        self.assertIsInstance(myriad_questions.get("data"), list)
        self.assertIn("markets", myriad_questions["data"][0])
        self.assertIsInstance(opinion_markets.get("result", {}).get("list"), list)
        self.assertIn("yesTokenId", opinion_markets["result"]["list"][0])
        self.assertIsInstance(predict_markets.get("data"), list)
        self.assertIn("outcomes", predict_markets["data"][0])
        self.assertIsInstance(xo_markets.get("markets"), list)
        self.assertIn("outcomes", xo_markets["markets"][0])
        self.assertIsInstance(betfair_catalogue.get("result"), list)
        self.assertIn("runners", betfair_catalogue["result"][0])
        self.assertIsInstance(myriad_orderbook.get("bids"), list)
        self.assertIsInstance(myriad_orderbook.get("asks"), list)
        self.assertIn("order_id", gemini_order)
        self.assertEqual(predict_order.get("success"), True)
        self.assertEqual(betfair_order.get("status"), "SUCCESS")


if __name__ == "__main__":
    unittest.main()
