from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from market_adapters import (
    BetfairExchangeAdapter,
    GeminiPredictionAdapter,
    MyriadAdapter,
    OpinionAdapter,
    PaperOrderRequest,
    PredictFunAdapter,
    XOMarketAdapter,
)
from market_adapters.errors import MarketConfigurationError, UnsupportedFeatureError


FIXTURES = Path(__file__).resolve().parent / "fixtures"


def load_fixture(market_id: str, name: str):
    return json.loads((FIXTURES / market_id / f"{name}.json").read_text(encoding="utf-8"))


class FakeResponse:
    status_code = 200
    text = "{}"

    def __init__(self, payload):
        self.payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self.payload


class AdditionalOfficialAdapterTests(unittest.TestCase):
    def test_gemini_prediction_adapter_maps_events_contracts_orderbook_and_paper_orders(self) -> None:
        adapter = GeminiPredictionAdapter()
        events = load_fixture("gemini", "events")
        event = load_fixture("gemini", "event")
        orderbook = load_fixture("gemini", "orderbook")

        def fake_get_json(url: str, *, params=None, headers=None):
            if url.endswith("/v1/prediction-markets/events"):
                return events
            if url.endswith("/v1/prediction-markets/events/BTC100K2026"):
                return event
            if url.endswith("/v1/book/GEMI-BTC100K26-YES"):
                return orderbook
            raise AssertionError(f"unexpected Gemini URL: {url}")

        adapter.runtime.get_json = fake_get_json  # type: ignore[method-assign]

        listed = adapter.list_events("bitcoin")
        contracts = adapter.list_contracts("BTC100K2026")
        book = adapter.get_orderbook("BTC100K2026:GEMI-BTC100K26-YES")
        price = adapter.get_price("BTC100K2026:GEMI-BTC100K26-YES")
        paper = adapter.place_paper_order(
            PaperOrderRequest("gemini_titan", "BTC100K2026:GEMI-BTC100K26-YES", "BUY", 3, 0.44)
        )

        self.assertEqual(listed[0].event_id, "BTC100K2026")
        self.assertEqual([contract.outcome for contract in contracts], ["Yes", "No"])
        self.assertEqual([level.price for level in book.bids], [0.42, 0.4])
        self.assertEqual([level.price for level in book.asks], [0.45, 0.47])
        self.assertAlmostEqual(price.midpoint or 0.0, 0.435)
        self.assertTrue(paper.accepted)

    def test_myriad_adapter_maps_questions_outcomes_prices_and_dry_run_quotes(self) -> None:
        adapter = MyriadAdapter()
        questions = load_fixture("myriad_markets", "questions")
        question = load_fixture("myriad_markets", "question")
        market = load_fixture("myriad_markets", "market")

        def fake_get_json(url: str, *, params=None, headers=None):
            if url.endswith("/questions"):
                return questions
            if url.endswith("/questions/10"):
                return question
            if url.endswith("/markets/501"):
                return market
            raise AssertionError(f"unexpected Myriad URL: {url}")

        adapter.runtime.get_json = fake_get_json  # type: ignore[method-assign]

        events = adapter.list_events("BTC")
        contracts = adapter.list_contracts("10")
        price = adapter.get_price("501:1")
        paper = adapter.place_paper_order(PaperOrderRequest("myriad_markets", "501:1", "BUY", 20))

        self.assertEqual(events[0].event_id, "10")
        self.assertEqual([contract.contract_id for contract in contracts], ["501:1", "501:2"])
        self.assertEqual(price.last, 0.61)
        self.assertEqual(paper.raw["request"]["action"], "buy")
        self.assertEqual(paper.raw["request"]["value"], 20.0)
        with self.assertRaises(UnsupportedFeatureError):
            adapter.get_orderbook("501:1")

    def test_opinion_adapter_requires_key_and_maps_market_data(self) -> None:
        adapter = OpinionAdapter()
        markets = load_fixture("opinion_labs", "markets")
        market = load_fixture("opinion_labs", "market")
        price_payload = load_fixture("opinion_labs", "price")
        orderbook = load_fixture("opinion_labs", "orderbook")

        def fake_get_json(url: str, *, params=None, headers=None):
            self.assertEqual(headers["apikey"], "opinion-key")
            if url.endswith("/market"):
                return markets
            if url.endswith("/market/77"):
                return market
            if url.endswith("/token/latest-price"):
                return price_payload
            if url.endswith("/token/orderbook"):
                return orderbook
            raise AssertionError(f"unexpected Opinion URL: {url}")

        adapter.runtime.get_json = fake_get_json  # type: ignore[method-assign]

        with self.assertRaises(MarketConfigurationError):
            adapter.list_events()

        with patch.dict("os.environ", {"OPINION_API_KEY": "opinion-key"}):
            events = adapter.list_events("ETH")
            contracts = adapter.list_contracts("77")
            price = adapter.get_price("77:YES:0xyes")
            book = adapter.get_orderbook("77:YES:0xyes")
            paper = adapter.place_paper_order(PaperOrderRequest("opinion_labs", "77:YES:0xyes", "SELL", 4, 0.64))

        self.assertEqual(events[0].event_id, "77")
        self.assertEqual([contract.contract_id for contract in contracts], ["77:YES:0xyes", "77:NO:0xno"])
        self.assertEqual(price.last, 0.65)
        self.assertEqual([level.price for level in book.bids], [0.64, 0.62])
        self.assertTrue(paper.accepted)

    def test_predict_fun_adapter_maps_markets_orderbooks_and_no_prices(self) -> None:
        adapter = PredictFunAdapter()
        markets = load_fixture("predict_fun", "markets")
        market = load_fixture("predict_fun", "market")
        orderbook = load_fixture("predict_fun", "orderbook")

        def fake_get_json(url: str, *, params=None, headers=None):
            self.assertEqual(headers["x-api-key"], "predict-key")
            if url.endswith("/markets"):
                return markets
            if url.endswith("/markets/9001"):
                return market
            if url.endswith("/markets/9001/orderbook"):
                return orderbook
            raise AssertionError(f"unexpected Predict.fun URL: {url}")

        adapter.runtime.get_json = fake_get_json  # type: ignore[method-assign]

        with patch.dict("os.environ", {"PREDICT_FUN_API_KEY": "predict-key"}):
            events = adapter.list_events("SOL")
            contracts = adapter.list_contracts("9001")
            yes_book = adapter.get_orderbook("9001:YES")
            no_book = adapter.get_orderbook("9001:NO")
            price = adapter.get_price("9001:YES")
            paper = adapter.place_paper_order(PaperOrderRequest("predict_fun", "9001:NO", "BUY", 5, 0.44))

        self.assertEqual(events[0].event_id, "9001")
        self.assertEqual([contract.contract_id for contract in contracts], ["9001:YES", "9001:NO"])
        self.assertEqual([level.price for level in yes_book.bids], [0.56, 0.54])
        self.assertEqual([level.price for level in no_book.bids], [0.42, 0.4])
        self.assertAlmostEqual(price.midpoint or 0.0, 0.57)
        self.assertTrue(paper.accepted)

    def test_xo_adapter_uses_hmac_headers_and_keeps_live_orders_guarded(self) -> None:
        adapter = XOMarketAdapter()
        markets = load_fixture("xo_market", "markets")
        market = load_fixture("xo_market", "market")
        orderbook = load_fixture("xo_market", "orderbook")

        def fake_get_json(url: str, *, params=None, headers=None):
            self.assertEqual(headers["XO-API-KEY"], "xo-key")
            self.assertTrue(headers["XO-SIGNATURE"])
            if url.endswith("/markets"):
                return markets
            if url.endswith("/markets/us-election-2028"):
                return market
            if url.endswith("/markets/us-election-2028/outcomes/vance/orderbook"):
                return orderbook
            raise AssertionError(f"unexpected XO URL: {url}")

        adapter.runtime.get_json = fake_get_json  # type: ignore[method-assign]
        order = PaperOrderRequest("xo_market", "us-election-2028:vance", "BUY", 25, 0.35)

        with patch.dict("os.environ", {"XO_API_KEY": "xo-key", "XO_API_SECRET": "xo-secret"}):
            events = adapter.list_events("election")
            contracts = adapter.list_contracts("us-election-2028")
            price = adapter.get_price("us-election-2028:vance")
            paper = adapter.place_paper_order(order)
            with self.assertRaises(MarketConfigurationError):
                adapter.place_live_order(order)

        self.assertEqual(events[0].event_id, "us-election-2028")
        self.assertEqual([contract.contract_id for contract in contracts], ["us-election-2028:vance", "us-election-2028:newsom"])
        self.assertAlmostEqual(price.midpoint or 0.0, 0.35)
        self.assertEqual(paper.raw["request"]["amount_usd"], 25.0)

        live_adapter = XOMarketAdapter({"live_trading_enabled": True})
        calls = []

        def fake_request(method: str, url: str, *, params=None, data=None, headers=None, timeout=None):
            calls.append((method, url, data, headers, timeout))
            return FakeResponse(load_fixture("xo_market", "order_response"))

        live_adapter.runtime.session.request = fake_request  # type: ignore[method-assign]
        with patch.dict("os.environ", {"XO_API_KEY": "xo-key", "XO_API_SECRET": "xo-secret"}):
            result = live_adapter.place_live_order(order)

        self.assertEqual(result["response"]["id"], "ord_123")
        self.assertEqual(calls[0][0], "POST")
        self.assertTrue(calls[0][1].endswith("/orders"))
        self.assertIn('"market_id":"us-election-2028"', calls[0][2])
        self.assertEqual(calls[0][3]["XO-API-KEY"], "xo-key")

    def test_betfair_adapter_maps_market_catalogue_and_best_offer_books(self) -> None:
        adapter = BetfairExchangeAdapter()
        catalogue = load_fixture("betfair_exchange", "market_catalogue")["result"]
        market_book = load_fixture("betfair_exchange", "market_book")["result"]

        def fake_request(method: str, url: str, *, json=None, headers=None, timeout=None):
            self.assertEqual(headers["X-Application"], "betfair-app")
            self.assertEqual(headers["X-Authentication"], "betfair-session")
            if json["method"].endswith("listMarketCatalogue"):
                return FakeResponse({"jsonrpc": "2.0", "result": catalogue, "id": 1})
            if json["method"].endswith("listMarketBook"):
                return FakeResponse({"jsonrpc": "2.0", "result": market_book, "id": 1})
            raise AssertionError(f"unexpected Betfair method: {json['method']}")

        adapter.runtime.session.request = fake_request  # type: ignore[method-assign]

        with patch.dict(
            "os.environ",
            {"BETFAIR_APP_KEY": "betfair-app", "BETFAIR_SESSION_TOKEN": "betfair-session"},
        ):
            events = adapter.list_events("Team")
            contracts = adapter.list_contracts("1.234")
            book = adapter.get_orderbook("1.234:101")
            price = adapter.get_price("1.234:101")
            paper = adapter.place_paper_order(PaperOrderRequest("betfair_exchange", "1.234:101", "BACK", 10, 0.5))

        self.assertEqual(events[0].event_id, "1.234")
        self.assertEqual([contract.contract_id for contract in contracts], ["1.234:101", "1.234:102"])
        self.assertEqual([round(level.price, 4) for level in book.bids], [0.5, 0.4545])
        self.assertEqual([round(level.price, 4) for level in book.asks], [0.5556, 0.5882])
        self.assertAlmostEqual(price.midpoint or 0.0, (0.5 + (1 / 1.8)) / 2)
        self.assertTrue(paper.accepted)


if __name__ == "__main__":
    unittest.main()
