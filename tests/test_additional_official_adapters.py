from __future__ import annotations

import base64
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from market_adapters import (
    BetfairExchangeAdapter,
    ContextV2Adapter,
    DFlowAdapter,
    GeminiPredictionAdapter,
    HyperliquidAdapter,
    CMEPredictionMarketsAdapter,
    ForecastExAdapter,
    IBKRForecastTraderAdapter,
    IBKREventContractsAdapter,
    MyriadAdapter,
    MatchbookAdapter,
    MetaDAOAdapter,
    OpinionAdapter,
    PaperOrderRequest,
    ProbableAdapter,
    PredictFunAdapter,
    SmarketsAdapter,
    SeerAdapter,
    ThalesMarketAdapter,
    TrueoAdapter,
    XOMarketAdapter,
    XMarketAdapter,
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
    def test_ibkr_event_contract_adapters_map_forecastex_cme_snapshots_paper_and_guarded_orders(self) -> None:
        forecast_fixtures = {
            name: load_fixture("ibkr_forecasttrader", name)
            for name in ("category_tree", "search", "strikes", "info", "accounts", "snapshot", "order_response")
        }
        adapter = IBKRForecastTraderAdapter({"ibkr_session_cookie": "api=test-session"})

        def fake_get_json(url: str, *, params=None, headers=None):
            self.assertEqual(headers, {"Cookie": "api=test-session"})
            if url.endswith("/trsrv/event/category-tree"):
                return forecast_fixtures["category_tree"]
            if url.endswith("/iserver/secdef/search"):
                self.assertEqual(params["symbol"], "FF")
                return forecast_fixtures["search"]
            if url.endswith("/iserver/secdef/strikes"):
                self.assertEqual(params["exchange"], "FORECASTX")
                return forecast_fixtures["strikes"]
            if url.endswith("/iserver/secdef/info"):
                self.assertEqual(params["exchange"], "FORECASTX")
                return forecast_fixtures["info"]
            if url.endswith("/iserver/accounts"):
                return forecast_fixtures["accounts"]
            if url.endswith("/iserver/marketdata/snapshot"):
                return forecast_fixtures["snapshot"]
            raise AssertionError(f"unexpected IBKR URL: {url}")

        adapter.runtime.get_json = fake_get_json  # type: ignore[method-assign]
        events = adapter.list_events("FF")
        contracts = adapter.list_contracts(events[0].event_id)
        order = PaperOrderRequest("ibkr_forecasttrader", contracts[0].contract_id, "BUY", 5, 0.48)
        book = adapter.get_orderbook(order.contract_id)
        price = adapter.get_price(order.contract_id)
        paper = adapter.place_paper_order(order)

        self.assertEqual(events[0].event_id, "IBKR:FF")
        self.assertEqual({contract.outcome for contract in contracts}, {"YES", "NO"})
        self.assertEqual([level.price for level in book.bids], [0.45])
        self.assertEqual([level.price for level in book.asks], [0.5])
        self.assertAlmostEqual(price.midpoint or 0.0, 0.475)
        self.assertTrue(paper.accepted)

        calls = []
        live = IBKRForecastTraderAdapter(
            {
                "ibkr_session_cookie": "api=test-session",
                "ibkr_account_id": "DU123456",
                "ibkr_submit_live_orders": True,
                "live_trading_enabled": True,
                "live_trading_confirmed": True,
            }
        )

        def fake_live_request(method: str, url: str, *, params=None, json_body=None, headers=None):
            calls.append((method, url, params, json_body, headers))
            return forecast_fixtures["order_response"]

        live.runtime.request_json = fake_live_request  # type: ignore[method-assign]
        live_result = live.place_live_order(order)
        self.assertTrue(live_result["live"])
        self.assertEqual(calls[0][0], "POST")
        self.assertTrue(calls[0][1].endswith("/iserver/account/DU123456/orders"))
        self.assertEqual(calls[0][3]["orders"][0]["conid"], 721095497)

        cme_fixtures = {name: load_fixture("cme_prediction_markets", name) for name in ("search", "info", "accounts", "snapshot")}
        cme = CMEPredictionMarketsAdapter({"ibkr_session_cookie": "api=cme-session", "ibkr_contract_month": "SEP26"})

        def fake_cme_get(url: str, *, params=None, headers=None):
            self.assertEqual(headers, {"Cookie": "api=cme-session"})
            if url.endswith("/iserver/secdef/search"):
                return cme_fixtures["search"]
            if url.endswith("/iserver/secdef/info"):
                return cme_fixtures["info"]
            if url.endswith("/iserver/accounts"):
                return cme_fixtures["accounts"]
            if url.endswith("/iserver/marketdata/snapshot"):
                return cme_fixtures["snapshot"]
            raise AssertionError(f"unexpected CME URL: {url}")

        cme.runtime.get_json = fake_cme_get  # type: ignore[method-assign]
        cme_events = cme.list_events("NQ")
        cme_contracts = cme.list_contracts(cme_events[0].event_id)
        self.assertEqual(cme_events[0].event_id, "IBKR:NQ")
        self.assertEqual({contract.outcome for contract in cme_contracts}, {"YES", "NO"})
        self.assertEqual(len(cme_contracts), 2)
        cme_order = PaperOrderRequest("cme_prediction_markets", cme_contracts[0].contract_id, "SELL", 2, 0.34)
        self.assertTrue(cme.place_paper_order(cme_order).accepted)

        forecastex = ForecastExAdapter({"ibkr_session_cookie": "api=forecastx-session"})
        self.assertIsInstance(forecastex, IBKREventContractsAdapter)
        self.assertEqual(forecastex.metadata.market_id, "forecastex")
        with self.assertRaises(UnsupportedFeatureError):
            adapter.copy_trade_from_activity({"side": "BUY"})

    def test_hyperliquid_public_hip4_fills_support_safe_simulation_copy(self) -> None:
        wallet = "0xabcdefabcdefabcdefabcdefabcdefabcdefabcd"
        adapter = HyperliquidAdapter()
        fills = load_fixture("hyperliquid", "user_fills")

        def fake_request_json(method: str, url: str, *, params=None, json_body=None, headers=None):
            self.assertEqual(method, "POST")
            self.assertIsNone(params)
            self.assertEqual(headers["Content-Type"], "application/json")
            self.assertEqual(json_body, {"type": "userFills", "user": wallet, "aggregateByTime": True})
            return fills

        adapter.runtime.request_json = fake_request_json  # type: ignore[method-assign]
        activities = adapter.list_activity(wallet, limit=10)

        self.assertTrue(adapter.capabilities.copy_trading)
        self.assertEqual(len(activities), 2)
        buy, sell = activities
        self.assertEqual(buy["asset"], "outcome:1:0")
        self.assertEqual(buy["side"], "BUY")
        self.assertAlmostEqual(buy["size"], 5.0)
        self.assertAlmostEqual(buy["price"], 0.63)
        self.assertEqual(buy["timestamp"], 1788264000)
        self.assertEqual(sell["asset"], "outcome:1:1")
        self.assertEqual(sell["side"], "SELL")
        self.assertAlmostEqual(sell["size"], 2.5)
        self.assertAlmostEqual(sell["price"], 0.39)

        copied = adapter.copy_trade_from_activity(sell)
        self.assertTrue(copied.accepted)
        self.assertEqual(copied.contract_id, "outcome:1:1")
        self.assertAlmostEqual(copied.average_price or 0.0, 0.39)

        with self.assertRaises(MarketConfigurationError):
            adapter.list_activity("not-a-wallet")
        with patch.dict("os.environ", {"OPINION_API_KEY": "opinion-key"}):
            with self.assertRaises(MarketConfigurationError):
                adapter.list_candles("77:YES:0xyes", resolution="30m")
            with self.assertRaises(MarketConfigurationError):
                adapter.list_candles(
                    "77:YES:0xyes",
                    from_timestamp=1733356800,
                    to_timestamp=1733184000,
                )

    def test_seer_adapter_maps_official_search_prices_and_paper_orders(self) -> None:
        adapter = SeerAdapter()
        markets = load_fixture("seer", "markets_search")
        market = load_fixture("seer", "market")
        market_id = "0x1111111111111111111111111111111111111111"

        def fake_request_json(method: str, url: str, *, params=None, json_body=None, headers=None):
            self.assertEqual(method, "POST")
            self.assertEqual(headers, {})
            self.assertIsNone(params)
            if url.endswith("/.netlify/functions/markets-search"):
                self.assertEqual(json_body["marketName"], "Bitcoin")
                return markets
            if url.endswith("/.netlify/functions/get-market"):
                self.assertEqual(json_body, {"chainId": 100, "id": market_id})
                return market
            raise AssertionError(f"unexpected Seer URL: {url}")

        adapter.runtime.request_json = fake_request_json  # type: ignore[method-assign]
        event_id = f"100:{market_id}"
        order = PaperOrderRequest("seer", f"100:{market_id}:0", "BUY", 5, 0.6)
        events = adapter.list_events("Bitcoin")
        contracts = adapter.list_contracts(event_id)
        price = adapter.get_price(order.contract_id)
        paper = adapter.place_paper_order(order)

        self.assertEqual(events[0].event_id, event_id)
        self.assertEqual(events[0].status, "active")
        self.assertEqual([contract.outcome for contract in contracts], ["Yes", "No"])
        self.assertAlmostEqual(price.last or 0.0, 0.62)
        self.assertTrue(paper.accepted)
        self.assertEqual(paper.raw["request"]["outcome_index"], 0)

        with self.assertRaises(UnsupportedFeatureError):
            adapter.get_orderbook(order.contract_id)
        with self.assertRaises(MarketConfigurationError):
            adapter.place_live_order(order)
        with self.assertRaises(UnsupportedFeatureError):
            adapter.copy_trade_from_activity({"side": "BUY"})

    def test_seer_guarded_live_order_forwards_reviewed_signed_dex_transaction(self) -> None:
        market_id = "0x1111111111111111111111111111111111111111"
        dex_address = "0x2222222222222222222222222222222222222222"
        tx_hash = "0x" + "ab" * 32
        adapter = SeerAdapter(
            {
                "live_trading_enabled": True,
                "live_trading_confirmed": True,
                "seer_submit_signed_transactions": True,
                "seer_rpc_url": "https://rpc.example.invalid/seer",
                "seer_trading_contract_addresses": [dex_address],
            }
        )

        def fake_request_json(method: str, url: str, *, params=None, json_body=None, headers=None):
            self.assertEqual(method, "POST")
            self.assertIsNone(params)
            self.assertEqual(headers, {"Content-Type": "application/json"})
            self.assertEqual(url, "https://rpc.example.invalid/seer")
            self.assertEqual(json_body["method"], "eth_sendRawTransaction")
            return {"jsonrpc": "2.0", "id": 1, "result": tx_hash}

        adapter.runtime.request_json = fake_request_json  # type: ignore[method-assign]
        order = PaperOrderRequest(
            "seer",
            f"100:{market_id}:0",
            "BUY",
            5,
            0.6,
            metadata={
                "signed_transaction": "0x" + "cd" * 96,
                "transaction_to": dex_address,
                "chain_id": "100",
                "market_address": market_id,
                "outcome_index": 0,
                "method": "buy",
                "data": "0x12345678",
            },
        )
        result = adapter.place_live_order(order)
        self.assertTrue(result["live"])
        self.assertEqual(result["tx_hash"], tx_hash)
        self.assertEqual(result["dex_address"], dex_address)
        self.assertEqual(result["chain_id"], "100")

        with self.assertRaises(MarketConfigurationError):
            adapter.place_live_order(
                PaperOrderRequest(
                    "seer",
                    order.contract_id,
                    "BUY",
                    5,
                    0.6,
                    metadata={**order.metadata, "transaction_to": "0x3333333333333333333333333333333333333333"},
                )
            )

    def test_hyperliquid_adapter_maps_hip4_outcomes_books_paper_and_signed_orders(self) -> None:
        adapter = HyperliquidAdapter()
        outcome_meta = load_fixture("hyperliquid", "outcome_meta")
        l2_book = load_fixture("hyperliquid", "l2_book")
        def fake_request_json(method: str, url: str, *, params=None, json_body=None, headers=None):
            self.assertEqual(method, "POST")
            self.assertIsNone(params)
            self.assertEqual(headers["Content-Type"], "application/json")
            if url.endswith("/info") and json_body == {"type": "outcomeMeta"}:
                return outcome_meta
            if url.endswith("/info") and json_body == {"type": "l2Book", "coin": "#10"}:
                return l2_book
            if url.endswith("/exchange"):
                return load_fixture("hyperliquid", "exchange_response")
            raise AssertionError(f"unexpected Hyperliquid request: {url} {json_body}")

        adapter.runtime.request_json = fake_request_json  # type: ignore[method-assign]
        order = PaperOrderRequest("hyperliquid", "outcome:1:0", "BUY", 5, 0.63)
        events = adapter.list_events("BTC")
        contracts = adapter.list_contracts("outcome:1")
        book = adapter.get_orderbook(order.contract_id)
        price = adapter.get_price(order.contract_id)
        paper = adapter.place_paper_order(order)

        self.assertEqual(events[0].event_id, "outcome:1")
        self.assertIn("BTC", events[0].title)
        self.assertEqual([contract.outcome for contract in contracts], ["Yes", "No"])
        self.assertEqual([level.price for level in book.bids], [0.62, 0.6])
        self.assertEqual([level.price for level in book.asks], [0.64, 0.66])
        self.assertAlmostEqual(price.midpoint or 0.0, 0.63)
        self.assertTrue(paper.accepted)
        self.assertEqual(paper.raw["action"]["orders"][0]["a"], 100000010)

        with self.assertRaises(MarketConfigurationError):
            adapter.place_live_order(order)

        live_adapter = HyperliquidAdapter({"live_trading_enabled": True, "live_trading_confirmed": True})
        live_adapter.runtime.request_json = fake_request_json  # type: ignore[method-assign]
        signed_action = {
            "action": {
                "type": "order",
                "orders": [
                    {"a": 100000010, "b": True, "p": "0.63", "s": "5", "r": False, "t": {"limit": {"tif": "Gtc"}}}
                ],
                "grouping": "na",
            },
            "nonce": 1788264000000,
            "signature": {"r": "0x1", "s": "0x2", "v": 27},
        }
        result = live_adapter.place_live_order(
            PaperOrderRequest("hyperliquid", "outcome:1:0", "BUY", 5, 0.63, {"signed_action": signed_action})
        )
        self.assertTrue(result["live"])
        self.assertEqual(result["response"]["status"], "ok")

        with self.assertRaises(MarketConfigurationError):
            adapter.copy_trade_from_activity({"side": "BUY"})

    def test_hyperliquid_public_hip4_candles_are_normalized_with_documented_bounds(self) -> None:
        adapter = HyperliquidAdapter()
        candles = load_fixture("hyperliquid", "candles")
        requests = []

        def fake_request_json(method: str, url: str, *, params=None, json_body=None, headers=None):
            self.assertEqual(method, "POST")
            self.assertIsNone(params)
            self.assertEqual(headers["Content-Type"], "application/json")
            requests.append(json_body)
            self.assertTrue(url.endswith("/info"))
            return candles

        adapter.runtime.request_json = fake_request_json  # type: ignore[method-assign]
        result = adapter.list_candles(
            "outcome:1:0",
            resolution="1h",
            from_timestamp=1788264000,
            to_timestamp=1788271200,
        )

        self.assertEqual(
            requests,
            [
                {
                    "type": "candleSnapshot",
                    "req": {
                        "coin": "#10",
                        "interval": "1h",
                        "startTime": 1788264000000,
                        "endTime": 1788271200000,
                    },
                }
            ],
        )
        self.assertEqual([candle.contract_id for candle in result], ["outcome:1:0", "outcome:1:0"])
        self.assertEqual([candle.timestamp for candle in result], [1788264000.0, 1788267600.0])
        self.assertAlmostEqual(result[0].open, 0.62)
        self.assertAlmostEqual(result[0].high, 0.66)
        self.assertAlmostEqual(result[0].low, 0.60)
        self.assertAlmostEqual(result[0].close, 0.64)
        self.assertAlmostEqual(result[0].volume or 0.0, 150.5)

        with self.assertRaises(MarketConfigurationError):
            adapter.list_candles("outcome:1:0", resolution="45m")
        with self.assertRaises(MarketConfigurationError):
            adapter.list_candles(
                "outcome:1:0",
                from_timestamp=1788271200,
                to_timestamp=1788264000,
            )
    def test_trueo_adapter_maps_onchain_manager_pools_prices_paper_and_signed_tx(self) -> None:
        from eth_abi import encode

        fixture = load_fixture("trueo", "rpc")
        adapter = TrueoAdapter()
        manager = fixture["manager"]
        market = fixture["market"]
        yes_token = fixture["yesToken"]
        no_token = fixture["noToken"]
        payment_token = fixture["paymentToken"]
        yes_pool = fixture["yesPool"]
        no_pool = fixture["noPool"]

        def encoded(types, values):
            return "0x" + encode(types, values).hex()

        def fake_request_json(method: str, url: str, *, params=None, json_body=None, headers=None):
            self.assertEqual(method, "POST")
            self.assertEqual(url, "https://mainnet.base.org")
            self.assertEqual(headers, {})
            self.assertEqual(json_body["jsonrpc"], "2.0")
            if json_body["method"] == "eth_sendRawTransaction":
                self.assertEqual(json_body["params"], [fixture["signedTransaction"]])
                return {"jsonrpc": "2.0", "id": 1, "result": fixture["transactionHash"]}
            self.assertEqual(json_body["method"], "eth_call")
            call = json_body["params"][0]
            target = call["to"].lower()
            data = call["data"]
            selector = data[2:10]
            if target == manager.lower() and selector == "7d6a0d1a":
                return {"jsonrpc": "2.0", "id": 1, "result": encoded(["uint256"], [1])}
            if target == manager.lower() and selector == "dd5adfa3":
                return {"jsonrpc": "2.0", "id": 1, "result": encoded(["address"], [market])}
            if target == market.lower() and selector == "066f69af":
                return {"jsonrpc": "2.0", "id": 1, "result": encoded(["string"], [fixture["question"]])}
            if target == market.lower() and selector == "17447836":
                return {"jsonrpc": "2.0", "id": 1, "result": encoded(["string"], [fixture["source"]])}
            if target == market.lower() and selector == "4063c865":
                return {"jsonrpc": "2.0", "id": 1, "result": encoded(["string"], [fixture["additionalInfo"]])}
            if target == market.lower() and selector == "d6a05e67":
                return {"jsonrpc": "2.0", "id": 1, "result": encoded(["uint256"], [fixture["endOfTrading"]])}
            if target == market.lower() and selector in {"a3dd2619", "2486d671"}:
                value = fixture["status"] if selector == "a3dd2619" else fixture["winningPosition"]
                return {"jsonrpc": "2.0", "id": 1, "result": encoded(["uint256"], [value])}
            if target == market.lower() and selector in {"f0d9bb20", "11a9f10a", "3013ce29"}:
                value = {"f0d9bb20": yes_token, "11a9f10a": no_token, "3013ce29": payment_token}[selector]
                return {"jsonrpc": "2.0", "id": 1, "result": encoded(["address"], [value])}
            if target == market.lower() and selector == "e4b6db4c":
                return {"jsonrpc": "2.0", "id": 1, "result": encoded(["address", "address"], [yes_pool, no_pool])}
            if target in {yes_pool.lower(), no_pool.lower()} and selector in {"0dfe1681", "d21220a7"}:
                value = yes_token if selector == "0dfe1681" else payment_token
                return {"jsonrpc": "2.0", "id": 1, "result": encoded(["address"], [value])}
            if target in {yes_pool.lower(), no_pool.lower()} and selector == "3850c7bd":
                return {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": encoded(["uint160", "int24", "uint16", "uint16", "uint16", "uint8", "bool"], [int(fixture["poolSqrtPriceX96"]), 0, 0, 0, 0, 0, True]),
                }
            if target == yes_token.lower() or target == payment_token.lower():
                return {"jsonrpc": "2.0", "id": 1, "result": encoded(["uint256"], [18])}
            raise AssertionError(f"unexpected Trueo RPC call: {json_body}")

        adapter.runtime.request_json = fake_request_json  # type: ignore[method-assign]
        order = PaperOrderRequest("trueo", f"{market}:0", "BUY", 5, 0.5)
        events = adapter.list_events("BTC")
        contracts = adapter.list_contracts(market)
        price = adapter.get_price(order.contract_id)
        paper = adapter.place_paper_order(order)

        self.assertEqual(events[0].event_id.lower(), market.lower())
        self.assertEqual([contract.outcome for contract in contracts], ["YES", "NO"])
        self.assertAlmostEqual(price.last or 0.0, 1.0)
        self.assertTrue(paper.accepted)
        with self.assertRaises(UnsupportedFeatureError):
            adapter.get_orderbook(order.contract_id)

        live = TrueoAdapter(
            {
                "live_trading_enabled": True,
                "live_trading_confirmed": True,
                "trueo_submit_signed_transactions": True,
                "trueo_chain_id": fixture["transactionChainId"],
                "trueo_live_transaction_targets": [fixture["transactionTo"]],
            }
        )
        live.runtime.request_json = fake_request_json  # type: ignore[method-assign]
        reviewed_metadata = {
            "signed_transaction": fixture["signedTransaction"],
            "chain_id": fixture["transactionChainId"],
            "transaction_to": fixture["transactionTo"],
            "transaction_data": fixture["transactionData"],
            "transaction_value": fixture["transactionValue"],
            "market_address": market,
            "outcome_index": 0,
            "side": "BUY",
            "size": 1,
            "limit_price": 0.5,
        }
        result = live.place_live_order(
            PaperOrderRequest("trueo", f"{market}:0", "BUY", 1, 0.5, reviewed_metadata)
        )
        self.assertTrue(result["live"])
        self.assertEqual(result["tx_hash"], fixture["transactionHash"])
        self.assertEqual(result["chain_id"], fixture["transactionChainId"])
        self.assertEqual(result["transaction_to"].lower(), fixture["transactionTo"].lower())
        self.assertEqual(result["transaction_value"], fixture["transactionValue"])
        self.assertEqual(result["calldata_selector"], fixture["transactionData"])

        rejected_cases = {
            "chain": {**reviewed_metadata, "chain_id": 1},
            "recipient": {**reviewed_metadata, "transaction_to": no_pool},
            "calldata": {**reviewed_metadata, "transaction_data": "0x87654321"},
            "value": {**reviewed_metadata, "transaction_value": 1},
            "market": {**reviewed_metadata, "market_address": no_pool},
            "outcome": {**reviewed_metadata, "outcome_index": 1},
            "side": {**reviewed_metadata, "side": "SELL"},
            "size": {**reviewed_metadata, "size": 2},
            "limit_price": {**reviewed_metadata, "limit_price": 0.6},
        }
        for label, metadata in rejected_cases.items():
            with self.subTest(label=label), self.assertRaises(MarketConfigurationError):
                live.place_live_order(PaperOrderRequest("trueo", f"{market}:0", "BUY", 1, 0.5, metadata))

        without_allowlist = TrueoAdapter(
            {
                "live_trading_enabled": True,
                "live_trading_confirmed": True,
                "trueo_submit_signed_transactions": True,
            }
        )
        with self.assertRaises(MarketConfigurationError):
            without_allowlist.place_live_order(
                PaperOrderRequest("trueo", f"{market}:0", "BUY", 1, 0.5, reviewed_metadata)
            )

        with self.assertRaises(MarketConfigurationError):
            live.place_live_order(
                PaperOrderRequest(
                    "trueo",
                    f"{market}:0",
                    "BUY",
                    1,
                    0.5,
                    {**reviewed_metadata, "signed_transaction": "0xdeadbeef"},
                )
            )
        with self.assertRaises(UnsupportedFeatureError):
            adapter.copy_trade_from_activity({"side": "BUY"})

    def test_metadao_adapter_maps_official_tickers_prices_and_paper_orders(self) -> None:
        adapter = MetaDAOAdapter()
        tickers = load_fixture("metadao", "tickers")
        ticker_id = tickers["tickers"][0]["ticker_id"]

        def fake_get_json(url: str, *, params=None, headers=None):
            self.assertEqual(url, "https://market-api.metadao.fi/api/tickers")
            self.assertEqual(headers, {})
            self.assertIsNone(params)
            return tickers

        adapter.runtime.get_json = fake_get_json  # type: ignore[method-assign]
        order = PaperOrderRequest("metadao", f"{ticker_id}:0", "BUY", 3, 0.08)

        events = adapter.list_events("META")
        contracts = adapter.list_contracts(ticker_id)
        price = adapter.get_price(order.contract_id)
        paper = adapter.place_paper_order(order)

        self.assertEqual(events[0].event_id, ticker_id)
        self.assertEqual(contracts[0].outcome, "META")
        self.assertAlmostEqual(price.last or 0.0, 0.081340728222)
        self.assertAlmostEqual(price.bid or 0.0, 0.080934024581)
        self.assertTrue(paper.accepted)
        self.assertEqual(paper.raw["request"]["ticker_id"], ticker_id)

        with self.assertRaises(UnsupportedFeatureError):
            adapter.get_orderbook(order.contract_id)
        with self.assertRaises(MarketConfigurationError):
            adapter.place_live_order(order)
        with self.assertRaises(UnsupportedFeatureError):
            adapter.copy_trade_from_activity({"side": "BUY"})

    def test_metadao_guarded_live_order_forwards_reviewed_signed_router_transaction(self) -> None:
        tickers = load_fixture("metadao", "tickers")
        row = tickers["tickers"][0]
        ticker_id = row["ticker_id"]
        router = "11111111111111111111111111111111"
        signature = "1" * 64
        adapter = MetaDAOAdapter(
            {
                "live_trading_enabled": True,
                "live_trading_confirmed": True,
                "metadao_submit_signed_transactions": True,
                "metadao_solana_rpc_url": "https://rpc.example.invalid/metadao",
                "metadao_router_program_ids": [router],
            }
        )

        def fake_get_json(url: str, *, params=None, headers=None):
            self.assertEqual(url, "https://market-api.metadao.fi/api/tickers")
            self.assertEqual(headers, {})
            self.assertIsNone(params)
            return tickers

        def fake_request_json(method: str, url: str, *, params=None, json_body=None, headers=None):
            self.assertEqual(method, "POST")
            self.assertEqual(url, "https://rpc.example.invalid/metadao")
            self.assertIsNone(params)
            self.assertEqual(headers, {"Content-Type": "application/json"})
            self.assertEqual(json_body["method"], "sendTransaction")
            return {"jsonrpc": "2.0", "id": 1, "result": signature}

        adapter.runtime.get_json = fake_get_json  # type: ignore[method-assign]
        adapter.runtime.request_json = fake_request_json  # type: ignore[method-assign]
        order = PaperOrderRequest(
            "metadao",
            f"{ticker_id}:0",
            "BUY",
            3,
            0.08,
            {
                "signed_transaction": base64.b64encode(b"\x01" * 96).decode("ascii"),
                "router_program_id": router,
                "ticker_id": ticker_id,
                "pool_id": row["pool_id"],
                "instruction": "swap",
                "instruction_data": "AQIDBA==",
            },
        )
        result = adapter.place_live_order(order)
        self.assertTrue(result["live"])
        self.assertEqual(result["signature"], signature)
        self.assertEqual(result["ticker_id"], ticker_id)
        self.assertEqual(result["router_program_id"], router)

        with self.assertRaises(MarketConfigurationError):
            adapter.place_live_order(
                PaperOrderRequest(
                    "metadao",
                    order.contract_id,
                    "BUY",
                    3,
                    0.08,
                    {**order.metadata, "router_program_id": "22222222222222222222222222222222"},
                )
            )

    def test_thales_adapter_maps_amm_markets_prices_paper_orders_and_safety_gates(self) -> None:
        adapter = ThalesMarketAdapter()
        self.assertTrue(adapter.capabilities.live_trading)
        self.assertFalse(adapter.health_check()["live_trading_enabled"])
        self.assertTrue(adapter.health_check()["wallet_transaction_required"])
        markets = load_fixture("thales_market", "markets")
        market = load_fixture("thales_market", "market")
        quote = load_fixture("thales_market", "buy_quote")
        address = "0x1111111111111111111111111111111111111111"

        def fake_get_json(url: str, *, params=None, headers=None):
            self.assertEqual(headers, {})
            self.assertIn("/thales/networks/10/", url)
            if url.endswith("/markets"):
                return markets
            if url.endswith(f"/markets/{address}"):
                return market
            if url.endswith(f"/markets/{address}/buy-quote"):
                return quote
            raise AssertionError(f"unexpected Thales URL: {url}")

        adapter.runtime.get_json = fake_get_json  # type: ignore[method-assign]
        order = PaperOrderRequest("thales_market", f"{address}:0", "BUY", 5, 0.57)

        events = adapter.list_events("BTC")
        contracts = adapter.list_contracts(address)
        price = adapter.get_price(order.contract_id)
        paper = adapter.place_paper_order(order)

        self.assertEqual(events[0].event_id, address)
        self.assertEqual([contract.outcome for contract in contracts], ["UP", "DOWN"])
        self.assertAlmostEqual(price.last or 0.0, 0.58)
        self.assertTrue(paper.accepted)
        self.assertEqual(paper.raw["request"]["network"], "10")
        self.assertEqual(paper.raw["request"]["position"], "UP")

        with self.assertRaises(UnsupportedFeatureError):
            adapter.get_orderbook(order.contract_id)
        with self.assertRaises(MarketConfigurationError):
            adapter.place_live_order(order)
        with self.assertRaises(UnsupportedFeatureError):
            adapter.copy_trade_from_activity({"side": "BUY"})

        amm_address = "0x2222222222222222222222222222222222222222"
        signed = "0x" + ("11" * 32)
        tx_hash = "0x" + ("aa" * 32)
        live_adapter = ThalesMarketAdapter(
            {
                "thales_network": "10",
                "thales_rpc_url": "https://rpc.example",
                "thales_amm_address": amm_address,
                "live_trading_enabled": True,
                "live_trading_confirmed": True,
                "thales_submit_signed_transactions": True,
            }
        )
        rpc_calls = []

        def fake_request_json(method: str, url: str, *, json_body=None, headers=None, params=None):
            rpc_calls.append((method, url, json_body, headers, params))
            return {"jsonrpc": "2.0", "id": 1, "result": tx_hash}

        live_adapter.runtime.request_json = fake_request_json  # type: ignore[method-assign]
        live = live_adapter.place_live_order(
            PaperOrderRequest(
                "thales_market",
                order.contract_id,
                "BUY",
                5,
                0.57,
                {
                    "signed_transaction": signed,
                    "transaction_to": amm_address,
                    "chain_id": 10,
                    "method": "buyFromAmm",
                    "data": "0x12345678" + ("00" * 32),
                    "market_address": address,
                    "position": "UP",
                },
            )
        )
        self.assertTrue(live["live"])
        self.assertEqual(live["tx_hash"], tx_hash)
        self.assertEqual(live["method"], "buyFromAmm")
        self.assertEqual(rpc_calls[0][0], "POST")
        self.assertEqual(rpc_calls[0][2]["method"], "eth_sendRawTransaction")
        self.assertEqual(rpc_calls[0][2]["params"], [signed])

        with self.assertRaises(MarketConfigurationError):
            live_adapter.place_live_order(
                PaperOrderRequest(
                    "thales_market",
                    order.contract_id,
                    "BUY",
                    5,
                    0.57,
                    {
                        "signed_transaction": signed,
                        "transaction_to": "0x3333333333333333333333333333333333333333",
                        "chain_id": 10,
                        "method": "buyFromAmm",
                        "data": "0x12345678",
                    },
                )
            )

    def test_smarkets_adapter_maps_events_contracts_quotes_paper_and_guarded_orders(self) -> None:
        adapter = SmarketsAdapter()
        events = load_fixture("smarkets", "events")
        markets = load_fixture("smarkets", "markets")
        contracts = load_fixture("smarkets", "contracts")
        quotes = load_fixture("smarkets", "quotes")

        def fake_get_json(url: str, *, params=None, headers=None):
            self.assertEqual(headers, {"Authorization": "Session-Token smk-token"})
            if url.endswith("/events/"):
                return events
            if url.endswith("/events/event-1/markets/"):
                return markets
            if url.endswith("/markets/market-1/contracts/"):
                return contracts
            if url.endswith("/markets/market-1/quotes/"):
                return quotes
            raise AssertionError(f"unexpected Smarkets URL: {url}")

        adapter.runtime.get_json = fake_get_json  # type: ignore[method-assign]
        order = PaperOrderRequest("smarkets", "market-1:contract-yes", "BUY", 5, 0.44)
        with patch.dict("os.environ", {"SMARKETS_SESSION_TOKEN": "smk-token"}):
            events_result = adapter.list_events("Bitcoin")
            contract_rows = adapter.list_contracts("event-1")
            book = adapter.get_orderbook(order.contract_id)
            price = adapter.get_price(order.contract_id)
            paper = adapter.place_paper_order(order)

        self.assertEqual(events_result[0].event_id, "event-1")
        self.assertEqual([contract.contract_id for contract in contract_rows], ["market-1:contract-yes", "market-1:contract-no"])
        self.assertEqual([level.price for level in book.bids], [0.42, 0.4])
        self.assertEqual([level.price for level in book.asks], [0.46, 0.48])
        self.assertAlmostEqual(price.last or 0.0, 0.43)
        self.assertTrue(paper.accepted)
        self.assertEqual(paper.raw["request"]["price"], "4400")
        with self.assertRaises(MarketConfigurationError):
            adapter.place_live_order(order)

        live_adapter = SmarketsAdapter({"live_trading_enabled": True, "live_trading_confirmed": True})
        calls = []

        def fake_request(method: str, url: str, *, json=None, headers=None, timeout=None):
            calls.append((method, url, json, headers, timeout))
            return FakeResponse(load_fixture("smarkets", "order_response"))

        live_adapter.runtime.session.request = fake_request  # type: ignore[method-assign]
        with patch.dict("os.environ", {"SMARKETS_SESSION_TOKEN": "smk-token"}):
            result = live_adapter.place_live_order(order)
        self.assertTrue(result["live"])
        self.assertEqual(result["response"]["orders"][0]["id"], "order-1")
        self.assertEqual(calls[0][0], "POST")
        self.assertTrue(calls[0][1].endswith("/orders/"))
        self.assertEqual(calls[0][2]["side"], "buy")
        self.assertEqual(calls[0][3]["Authorization"], "Session-Token smk-token")

        with self.assertRaises(UnsupportedFeatureError):
            live_adapter.copy_trade_from_activity({"side": "BUY"})

    def test_context_v2_adapter_maps_markets_prices_orderbooks_paper_and_guarded_signed_orders(self) -> None:
        adapter = ContextV2Adapter()
        markets = load_fixture("context_v2", "markets")
        market = load_fixture("context_v2", "market")
        orderbook = load_fixture("context_v2", "orderbook")
        market_id = "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"

        def fake_get_json(url: str, *, params=None, headers=None):
            self.assertEqual(headers, {"Authorization": "Bearer context-key"})
            if url.endswith("/markets"):
                return markets
            if url.endswith(f"/markets/{market_id}"):
                return market
            if url.endswith(f"/markets/{market_id}/orderbook"):
                return orderbook
            raise AssertionError(f"unexpected Context URL: {url}")

        adapter.runtime.get_json = fake_get_json  # type: ignore[method-assign]
        order = PaperOrderRequest("context_v2", f"{market_id}:0", "BUY", 5, 0.44)
        with patch.dict("os.environ", {"CONTEXT_API_KEY": "context-key"}):
            events = adapter.list_events("BTC")
            contracts = adapter.list_contracts(market_id)
            book = adapter.get_orderbook(order.contract_id)
            price = adapter.get_price(order.contract_id)
            paper = adapter.place_paper_order(order)

        self.assertEqual(events[0].event_id, market_id)
        self.assertEqual([contract.outcome for contract in contracts], ["Yes", "No"])
        self.assertEqual([level.price for level in book.bids], [0.42, 0.4])
        self.assertEqual([level.price for level in book.asks], [0.46, 0.48])
        self.assertAlmostEqual(price.midpoint or 0.0, 0.44)
        self.assertTrue(paper.accepted)
        self.assertEqual(paper.raw["request"]["marketId"], market_id)

        with self.assertRaises(MarketConfigurationError):
            adapter.place_live_order(order)

        live_adapter = ContextV2Adapter({"live_trading_enabled": True, "live_trading_confirmed": True})
        calls = []

        def fake_request(method: str, url: str, *, json=None, headers=None, timeout=None):
            calls.append((method, url, json, headers, timeout))
            return FakeResponse(load_fixture("context_v2", "order_response"))

        live_adapter.runtime.session.request = fake_request  # type: ignore[method-assign]
        signed_order = {
            "trader": "0x3333333333333333333333333333333333333333",
            "nonce": "0x1",
            "signature": "0xsignature",
        }
        with patch.dict("os.environ", {"CONTEXT_API_KEY": "context-key"}):
            result = live_adapter.place_live_order(
                PaperOrderRequest(
                    "context_v2",
                    order.contract_id,
                    "BUY",
                    5,
                    0.44,
                    {"signed_order": signed_order},
                )
            )
        self.assertTrue(result["live"])
        self.assertTrue(result["response"]["success"])
        self.assertEqual(calls[0][0], "POST")
        self.assertTrue(calls[0][1].endswith("/orders"))
        self.assertEqual(calls[0][3]["Authorization"], "Bearer context-key")
        self.assertEqual(calls[0][2]["outcomeIndex"], 0)

        with self.assertRaises(UnsupportedFeatureError):
            live_adapter.copy_trade_from_activity({"side": "BUY"})

    def test_dflow_adapter_maps_nested_markets_orderbooks_paper_orders_and_guarded_rpc_submission(self) -> None:
        adapter = DFlowAdapter()
        events = load_fixture("dflow", "events")
        orderbook = load_fixture("dflow", "orderbook")

        def fake_get_json(url: str, *, params=None, headers=None):
            if url.endswith("/api/v1/events"):
                self.assertEqual(headers, {})
                return events
            if url.endswith("/api/v1/orderbook/by-mint/mint-yes"):
                return orderbook
            raise AssertionError(f"unexpected DFlow URL: {url}")

        adapter.runtime.get_json = fake_get_json  # type: ignore[method-assign]
        listed = adapter.list_events("bitcoin")
        contracts = adapter.list_contracts("KXBTC-26DEC31")
        order = PaperOrderRequest("dflow", "KXBTC-26DEC31-100K:mint-yes", "BUY", 5, 0.44)
        book = adapter.get_orderbook(order.contract_id)
        price = adapter.get_price(order.contract_id)
        paper = adapter.place_paper_order(order)

        self.assertEqual(listed[0].event_id, "KXBTC-26DEC31")
        self.assertEqual(
            [contract.contract_id for contract in contracts],
            ["KXBTC-26DEC31-100K:mint-yes", "KXBTC-26DEC31-100K:mint-no"],
        )
        self.assertEqual([level.price for level in book.bids], [0.42, 0.4])
        self.assertEqual([round(level.price, 6) for level in book.asks], [0.45, 0.47])
        self.assertAlmostEqual(price.midpoint or 0.0, 0.435)
        self.assertTrue(paper.accepted)
        self.assertEqual(paper.raw["trade_request"]["outputMint"], "mint-yes")

        with self.assertRaises(MarketConfigurationError):
            adapter.place_live_order(order)

        live_adapter = DFlowAdapter(
            {
                "live_trading_enabled": True,
                "live_trading_confirmed": True,
                "dflow_solana_rpc_url": "https://rpc.example",
            }
        )
        live_adapter._market_cache = adapter._market_cache
        calls = []

        def fake_request(method: str, url: str, *, params=None, json_body=None, headers=None, timeout=None):
            calls.append((method, url, params, json_body, headers, timeout))
            return load_fixture("dflow", "rpc_response")

        live_adapter.runtime.request_json = fake_request  # type: ignore[method-assign]
        with patch.dict("os.environ", {"DFLOW_API_KEY": "dflow-key"}):
            result = live_adapter.place_live_order(
                PaperOrderRequest(
                    "dflow",
                    order.contract_id,
                    "BUY",
                    1,
                    0.44,
                    {"signed_transaction": "c2lnbmVk", "user_public_key": "wallet-1"},
                )
            )
        self.assertTrue(result["live"])
        self.assertEqual(result["response"]["result"], "signature-123")
        self.assertEqual(calls[0][0], "POST")
        self.assertEqual(calls[0][1], "https://rpc.example")
        self.assertEqual(calls[0][3]["method"], "sendTransaction")

        with self.assertRaises(UnsupportedFeatureError):
            live_adapter.copy_trade_from_activity({"side": "BUY"})

    def test_matchbook_adapter_maps_events_markets_odds_paper_orders_and_guarded_offers(self) -> None:
        adapter = MatchbookAdapter()
        events = load_fixture("matchbook", "events")
        markets = load_fixture("matchbook", "markets")
        market = load_fixture("matchbook", "market")

        def fake_get_json(url: str, *, params=None, headers=None):
            if url.endswith("/events"):
                return events
            if url.endswith("/events/101/markets"):
                return markets
            if url.endswith("/events/101/markets/202"):
                return market
            raise AssertionError(f"unexpected Matchbook URL: {url}")

        adapter.runtime.get_json = fake_get_json  # type: ignore[method-assign]
        order = PaperOrderRequest("matchbook", "101:202:303", "BUY", 5, 0.5)

        listed = adapter.list_events("BTC")
        contracts = adapter.list_contracts("101")
        book = adapter.get_orderbook(order.contract_id)
        price = adapter.get_price(order.contract_id)
        paper = adapter.place_paper_order(order)

        self.assertEqual(listed[0].event_id, "101")
        self.assertEqual([contract.contract_id for contract in contracts], ["101:202:303", "101:202:304"])
        self.assertEqual([round(level.price, 6) for level in book.bids], [0.5, round(1 / 2.1, 6)])
        self.assertEqual([round(level.price, 6) for level in book.asks], [round(1 / 2.3, 6), round(1 / 2.2, 6)])
        self.assertAlmostEqual(price.midpoint or 0.0, (0.5 + 1 / 2.3) / 2)
        self.assertTrue(paper.accepted)

        with self.assertRaises(MarketConfigurationError):
            adapter.place_live_order(order)

        live_adapter = MatchbookAdapter({"live_trading_enabled": True, "live_trading_confirmed": True})
        calls = []

        def fake_request(method: str, url: str, *, json=None, headers=None, timeout=None):
            calls.append((method, url, json, headers, timeout))
            if url.endswith("/security/session"):
                return FakeResponse(load_fixture("matchbook", "login_response"))
            if url.endswith("/v2/offers"):
                return FakeResponse(load_fixture("matchbook", "order_response"))
            raise AssertionError(f"unexpected Matchbook request URL: {url}")

        live_adapter.runtime.session.request = fake_request  # type: ignore[method-assign]
        with patch.dict(
            "os.environ",
            {"MATCHBOOK_USERNAME": "user", "MATCHBOOK_PASSWORD": "pass"},
        ):
            result = live_adapter.place_live_order(order)

        self.assertEqual(result["response"]["offers"][0]["id"], 404)
        self.assertEqual(calls[0][0], "POST")
        self.assertTrue(calls[0][1].endswith("/security/session"))
        self.assertEqual(calls[0][2]["username"], "user")
        self.assertTrue(calls[1][1].endswith("/v2/offers"))
        self.assertEqual(calls[1][2]["offers"][0]["runner-id"], 303)
        self.assertEqual(calls[1][2]["offers"][0]["odds"], 2.0)
        self.assertEqual(calls[1][3]["session-token"], "session-123")

        with self.assertRaises(UnsupportedFeatureError):
            live_adapter.copy_trade_from_activity({"side": "BUY"})

    def test_probable_adapter_maps_events_tokens_orderbooks_paper_orders_and_guarded_signed_orders(self) -> None:
        adapter = ProbableAdapter()
        events = load_fixture("probable", "events")
        event = load_fixture("probable", "event")
        market = load_fixture("probable", "market")
        orderbook = load_fixture("probable", "orderbook")
        order_response = load_fixture("probable", "order_response")

        def fake_get_json(url: str, *, params=None, headers=None):
            if url.endswith("/events"):
                return events
            if url.endswith("/events/event-1"):
                return event
            if url.endswith("/markets/market-1"):
                return market
            if url.endswith("/book"):
                self.assertEqual(params["token_id"], "token-yes")
                return orderbook
            raise AssertionError(f"unexpected Probable URL: {url}")

        adapter.runtime.get_json = fake_get_json  # type: ignore[method-assign]
        order = PaperOrderRequest("probable", "market-1:token-yes", "BUY", 5, 0.44)

        events_result = adapter.list_events("BTC")
        contracts = adapter.list_contracts("event-1")
        book = adapter.get_orderbook(order.contract_id)
        price = adapter.get_price(order.contract_id)
        paper = adapter.place_paper_order(order)

        self.assertEqual(events_result[0].event_id, "event-1")
        self.assertEqual([contract.contract_id for contract in contracts], ["market-1:token-yes", "market-1:token-no"])
        self.assertEqual([level.price for level in book.bids], [0.42, 0.4])
        self.assertEqual([level.price for level in book.asks], [0.45, 0.47])
        self.assertAlmostEqual(price.midpoint or 0.0, 0.435)
        self.assertTrue(paper.accepted)

        with self.assertRaises(MarketConfigurationError):
            adapter.place_live_order(order)

        live_adapter = ProbableAdapter({"live_trading_enabled": True, "live_trading_confirmed": True})
        calls = []

        def fake_request(method: str, url: str, *, data=None, headers=None, timeout=None):
            calls.append((method, url, data, headers, timeout))
            return FakeResponse(order_response)

        live_adapter.runtime.session.request = fake_request  # type: ignore[method-assign]
        signed_order = {
            "salt": "1",
            "maker": "0x0000000000000000000000000000000000000001",
            "signer": "0x0000000000000000000000000000000000000001",
            "taker": "0x0000000000000000000000000000000000000000",
            "tokenId": "token-yes",
            "makerAmount": "220",
            "takerAmount": "500",
            "expiration": "0",
            "nonce": "0",
            "feeRateBps": "0",
            "side": 0,
            "signatureType": 0,
            "signature": "0xsig",
        }
        live_order = PaperOrderRequest(
            "probable",
            "market-1:token-yes",
            "BUY",
            5,
            0.44,
            {"signed_order": signed_order},
        )
        with patch.dict(
            "os.environ",
            {
                "PROB_ADDRESS": "0x0000000000000000000000000000000000000001",
                "PROB_API_KEY": "prob-key",
                "PROB_API_SECRET": "c2VjcmV0",
                "PROB_PASSPHRASE": "prob-pass",
            },
        ):
            result = live_adapter.place_live_order(live_order)

        self.assertEqual(result["response"]["orderId"], 123)
        self.assertEqual(calls[0][0], "POST")
        self.assertTrue(calls[0][1].endswith("/orders/56"))
        self.assertEqual(calls[0][3]["PROB_API_KEY"], "prob-key")
        self.assertTrue(calls[0][3]["PROB_SIGNATURE"])
        self.assertEqual(json.loads(calls[0][2])["order"]["tokenId"], "token-yes")

        with self.assertRaises(UnsupportedFeatureError):
            live_adapter.copy_trade_from_activity({"side": "BUY"})

    def test_xmarket_adapter_maps_markets_orderbooks_paper_orders_and_guarded_live_orders(self) -> None:
        adapter = XMarketAdapter()
        markets = load_fixture("xmarket", "markets")
        market = load_fixture("xmarket", "market")
        orderbook = load_fixture("xmarket", "orderbook")

        def fake_get_json(url: str, *, params=None, headers=None):
            self.assertEqual(headers["x-api-key"], "xmarket-key")
            if url.endswith("/markets"):
                return markets
            if url.endswith("/markets/market-1"):
                return market
            if url.endswith("/orderbook/outcome-yes"):
                return orderbook
            raise AssertionError(f"unexpected Xmarket URL: {url}")

        adapter.runtime.get_json = fake_get_json  # type: ignore[method-assign]
        order = PaperOrderRequest("xmarket", "market-1:outcome-yes", "BUY", 10, 0.44)

        with patch.dict("os.environ", {"XMARKET_API_KEY": "xmarket-key"}):
            events = adapter.list_events("election")
            contracts = adapter.list_contracts("market-1")
            book = adapter.get_orderbook("market-1:outcome-yes")
            price = adapter.get_price("market-1:outcome-yes")
            paper = adapter.place_paper_order(order)
            with self.assertRaises(MarketConfigurationError):
                adapter.place_live_order(order)

        self.assertEqual(events[0].event_id, "market-1")
        self.assertEqual([contract.contract_id for contract in contracts], ["market-1:outcome-yes", "market-1:outcome-no"])
        self.assertEqual([level.price for level in book.bids], [0.43, 0.41])
        self.assertEqual([level.price for level in book.asks], [0.45, 0.47])
        self.assertAlmostEqual(price.midpoint or 0.0, 0.44)
        self.assertTrue(paper.accepted)

        live_adapter = XMarketAdapter({"live_trading_enabled": True, "live_trading_confirmed": True})
        calls = []

        def fake_request(method: str, url: str, *, params=None, json=None, headers=None, timeout=None):
            calls.append((method, url, params, json, headers, timeout))
            return FakeResponse(load_fixture("xmarket", "order_response"))

        live_adapter.runtime.session.request = fake_request  # type: ignore[method-assign]
        with patch.dict("os.environ", {"XMARKET_API_KEY": "xmarket-key"}):
            result = live_adapter.place_live_order(order)

        self.assertEqual(result["response"]["id"], "xorder-1")
        self.assertEqual(calls[0][0], "POST")
        self.assertTrue(calls[0][1].endswith("/openapi/v1/order"))
        self.assertEqual(calls[0][3]["outcomeId"], "outcome-yes")
        self.assertEqual(calls[0][4]["x-api-key"], "xmarket-key")

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

        with self.assertRaises(MarketConfigurationError):
            adapter.place_live_order(
                PaperOrderRequest("gemini_titan", "BTC100K2026:GEMI-BTC100K26-YES", "BUY", 3, 0.44)
            )

        live_adapter = GeminiPredictionAdapter({"live_trading_enabled": True, "live_trading_confirmed": True})
        calls = []

        def fake_request(method: str, url: str, *, data=None, headers=None, timeout=None):
            calls.append((method, url, data, headers, timeout))
            return FakeResponse(load_fixture("gemini", "order_response"))

        live_adapter.runtime.session.request = fake_request  # type: ignore[method-assign]
        with patch.dict("os.environ", {"GEMINI_API_KEY": "gemini-key", "GEMINI_API_SECRET": "gemini-secret"}):
            result = live_adapter.place_live_order(
                PaperOrderRequest(
                    "gemini_titan",
                    "BTC100K2026:GEMI-BTC100K26-YES",
                    "BUY",
                    3,
                    0.44,
                    {"nonce": 123, "client_order_id": "client-1"},
                )
            )

        self.assertEqual(result["response"]["order_id"], "106817811")
        self.assertEqual(calls[0][0], "POST")
        self.assertTrue(calls[0][1].endswith("/v1/order/new"))
        self.assertEqual(calls[0][2], "")
        self.assertEqual(calls[0][3]["X-GEMINI-APIKEY"], "gemini-key")
        self.assertTrue(calls[0][3]["X-GEMINI-SIGNATURE"])

    def test_myriad_adapter_maps_questions_outcomes_prices_orderbooks_and_dry_run_quotes(self) -> None:
        adapter = MyriadAdapter()
        questions = load_fixture("myriad_markets", "questions")
        question = load_fixture("myriad_markets", "question")
        market = load_fixture("myriad_markets", "market")
        orderbook = load_fixture("myriad_markets", "orderbook")

        def fake_get_json(url: str, *, params=None, headers=None):
            if url.endswith("/questions"):
                return questions
            if url.endswith("/questions/10"):
                return question
            if url.endswith("/markets/501"):
                return market
            if url.endswith("/markets/501/orderbook"):
                self.assertEqual(params["outcome"], 1)
                return orderbook
            raise AssertionError(f"unexpected Myriad URL: {url}")

        adapter.runtime.get_json = fake_get_json  # type: ignore[method-assign]

        events = adapter.list_events("BTC")
        contracts = adapter.list_contracts("10")
        price = adapter.get_price("501:1")
        book = adapter.get_orderbook("501:1")
        paper = adapter.place_paper_order(PaperOrderRequest("myriad_markets", "501:1", "BUY", 20))

        self.assertEqual(events[0].event_id, "10")
        self.assertEqual([contract.contract_id for contract in contracts], ["501:1", "501:2"])
        self.assertEqual(price.last, 0.61)
        self.assertEqual([level.price for level in book.bids], [0.62, 0.6])
        self.assertEqual([level.size for level in book.asks], [2.0, 1.0])
        self.assertEqual(paper.raw["request"]["action"], "buy")
        self.assertEqual(paper.raw["request"]["value"], 20.0)
        with self.assertRaises(MarketConfigurationError):
            adapter.place_live_order(PaperOrderRequest("myriad_markets", "501:1", "BUY", 20))

        live_adapter = MyriadAdapter(
            {"live_trading_enabled": True, "live_trading_confirmed": True, "myriad_network_id": 56}
        )
        calls = []

        def fake_request(method: str, url: str, *, json=None, headers=None, timeout=None):
            calls.append((method, url, json, headers, timeout))
            return FakeResponse(load_fixture("myriad_markets", "order_response"))

        live_adapter.runtime.session.request = fake_request  # type: ignore[method-assign]
        with patch.dict("os.environ", {"MYRIAD_API_KEY": "myriad-key"}):
            result = live_adapter.place_live_order(
                PaperOrderRequest(
                    "myriad_markets",
                    "501:1",
                    "BUY",
                    20,
                    0.62,
                    {
                        "order": {"trader": "0xabc", "marketId": "501", "outcomeId": 1},
                        "signature": "0xsig",
                    },
                )
            )

        self.assertEqual(result["response"]["orderHash"], "0xmyriadorder")
        self.assertEqual(calls[0][0], "POST")
        self.assertTrue(calls[0][1].endswith("/orders"))
        self.assertEqual(calls[0][2]["network_id"], 56)
        self.assertEqual(calls[0][3]["x-api-key"], "myriad-key")

    def test_myriad_public_wallet_events_support_safe_simulation_copy(self) -> None:
        wallet = "0xabcdefabcdefabcdefabcdefabcdefabcdefabcd"
        adapter = MyriadAdapter({"myriad_network_id": 56})
        events = load_fixture("myriad_markets", "user_events")

        def fake_get_json(url: str, *, params=None, headers=None):
            self.assertTrue(url.endswith(f"/users/{wallet}/events"))
            self.assertEqual(params["page"], 1)
            self.assertEqual(params["limit"], 25)
            self.assertEqual(params["trading_model"], "all")
            self.assertEqual(params["only_relevant"], "true")
            self.assertEqual(params["network_id"], 56)
            return events

        adapter.runtime.get_json = fake_get_json  # type: ignore[method-assign]
        activities = adapter.list_activity(wallet)

        self.assertTrue(adapter.capabilities.copy_trading)
        self.assertEqual(len(activities), 2)
        buy, sell = activities
        self.assertEqual(buy["asset"], "501:1")
        self.assertEqual(buy["side"], "BUY")
        self.assertAlmostEqual(buy["size"], 12.2)
        self.assertAlmostEqual(buy["price"], 0.61)
        self.assertEqual(sell["asset"], "501:2")
        self.assertEqual(sell["side"], "SELL")
        self.assertAlmostEqual(sell["size"], 4.0)
        self.assertAlmostEqual(sell["price"], 0.39)

        result = adapter.copy_trade_from_activity(sell)
        self.assertTrue(result.accepted)
        self.assertEqual(result.raw["request"]["action"], "sell")
        self.assertEqual(result.raw["request"]["shares"], 4.0)

        with self.assertRaises(MarketConfigurationError):
            adapter.list_activity("not-a-wallet")

    def test_opinion_adapter_requires_key_and_maps_market_data(self) -> None:
        adapter = OpinionAdapter()
        markets = load_fixture("opinion_labs", "markets")
        market = load_fixture("opinion_labs", "market")
        price_payload = load_fixture("opinion_labs", "price")
        orderbook = load_fixture("opinion_labs", "orderbook")
        trades = load_fixture("opinion_labs", "trades")
        price_history = load_fixture("opinion_labs", "price_history")

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
            if url.endswith("/token/price-history"):
                self.assertEqual(params["token_id"], "0xyes")
                self.assertEqual(params["interval"], "1d")
                self.assertEqual(params["start_at"], 1733184000)
                self.assertEqual(params["end_at"], 1733356800)
                return price_history
            if url.endswith("/trade/user/0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"):
                return trades
            raise AssertionError(f"unexpected Opinion URL: {url}")

        adapter.runtime.get_json = fake_get_json  # type: ignore[method-assign]

        with self.assertRaises(MarketConfigurationError):
            adapter.list_events()

        with patch.dict("os.environ", {"OPINION_API_KEY": "opinion-key"}):
            events = adapter.list_events("ETH")
            contracts = adapter.list_contracts("77")
            price = adapter.get_price("77:YES:0xyes")
            book = adapter.get_orderbook("77:YES:0xyes")
            candles = adapter.list_candles(
                "77:YES:0xyes",
                resolution="1d",
                from_timestamp=1733184000,
                to_timestamp=1733356800,
            )
            paper = adapter.place_paper_order(PaperOrderRequest("opinion_labs", "77:YES:0xyes", "SELL", 4, 0.64))
            activity = adapter.list_activity("0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb")
            copied = adapter.copy_trade_from_activity(activity[0])

        self.assertEqual(events[0].event_id, "77")
        self.assertEqual([contract.contract_id for contract in contracts], ["77:YES:0xyes", "77:NO:0xno"])
        self.assertEqual(price.last, 0.65)
        self.assertEqual([level.price for level in book.bids], [0.64, 0.62])
        self.assertEqual([candle.timestamp for candle in candles], [1733184000.0, 1733270400.0, 1733356800.0])
        self.assertEqual([candle.close for candle in candles], [0.58, 0.62, 0.65])
        self.assertTrue(all(candle.volume is None for candle in candles))
        self.assertTrue(paper.accepted)
        self.assertEqual(len(activity), 2)
        self.assertEqual(activity[0]["asset"], "77:YES:0xyes")
        self.assertEqual(activity[0]["side"], "BUY")
        self.assertEqual(activity[0]["timestamp"], 1733312400)
        self.assertTrue(copied.accepted)

        with self.assertRaises(MarketConfigurationError):
            adapter.list_activity("not-a-wallet")

    def test_opinion_guarded_clob_orders_build_signed_limit_and_market_requests(self) -> None:
        adapter = OpinionAdapter(
            {
                "live_trading_enabled": True,
                "live_trading_confirmed": True,
                "live_trading_max_size": 25,
                "opinion_live_check_approval": False,
            }
        )
        submitted = []

        class FakeClient:
            def place_order(self, payload, *, check_approval=False):
                submitted.append((payload, check_approval))
                return {"order_id": "opinion-order-1", "status": "submitted"}

        def fake_builder(**kwargs):
            return kwargs

        with patch.object(adapter, "_create_clob_client", return_value=FakeClient()), patch.object(
            OpinionAdapter, "_build_sdk_order", side_effect=fake_builder
        ):
            limit = adapter.place_live_order(
                PaperOrderRequest("opinion_labs", "77:YES:0xyes", "BUY", 4, 0.64)
            )
            market = adapter.place_live_order(
                PaperOrderRequest(
                    "opinion_labs",
                    "77:NO:0xno",
                    "SELL",
                    3,
                    None,
                    {"order_type": "market", "maker_amount_in_base_token": "3"},
                )
            )

        self.assertTrue(limit["live"])
        self.assertEqual(limit["request"]["marketId"], 77)
        self.assertEqual(limit["request"]["tokenId"], "0xyes")
        self.assertEqual(limit["request"]["price"], "0.64")
        self.assertEqual(limit["request"]["makerAmountInQuoteToken"], "4")
        self.assertIsNone(limit["request"]["makerAmountInBaseToken"])
        self.assertEqual(limit["response"]["order_id"], "opinion-order-1")
        self.assertEqual(market["order_type"], "market")
        self.assertEqual(market["request"]["price"], "0")
        self.assertEqual(market["request"]["makerAmountInBaseToken"], "3")
        self.assertEqual(len(submitted), 2)
        self.assertFalse(submitted[0][1])

        with patch.object(adapter, "_create_clob_client", return_value=FakeClient()), patch.object(
            OpinionAdapter, "_build_sdk_order", side_effect=fake_builder
        ):
            with self.assertRaises(MarketConfigurationError):
                adapter.place_live_order(
                    PaperOrderRequest("opinion_labs", "77:YES:0xyes", "BUY", 1, 0.005)
                )
            with self.assertRaises(MarketConfigurationError):
                adapter.place_live_order(
                    PaperOrderRequest(
                        "opinion_labs",
                        "77:YES:0xyes",
                        "BUY",
                        1,
                        0.5,
                        {
                            "maker_amount_in_quote_token": "1",
                            "maker_amount_in_base_token": "1",
                        },
                    )
                )

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
            with self.assertRaises(MarketConfigurationError):
                adapter.place_live_order(PaperOrderRequest("predict_fun", "9001:YES", "BUY", 5, 0.56))

        self.assertEqual(events[0].event_id, "9001")
        self.assertEqual([contract.contract_id for contract in contracts], ["9001:YES", "9001:NO"])
        self.assertEqual([level.price for level in yes_book.bids], [0.56, 0.54])
        self.assertEqual([level.price for level in no_book.bids], [0.42, 0.4])
        self.assertAlmostEqual(price.midpoint or 0.0, 0.57)
        self.assertTrue(paper.accepted)

        live_adapter = PredictFunAdapter({"live_trading_enabled": True, "live_trading_confirmed": True})
        calls = []

        def fake_request(method: str, url: str, *, json=None, headers=None, timeout=None):
            calls.append((method, url, json, headers, timeout))
            return FakeResponse(load_fixture("predict_fun", "order_response"))

        live_adapter.runtime.session.request = fake_request  # type: ignore[method-assign]
        with patch.dict("os.environ", {"PREDICT_FUN_API_KEY": "predict-key"}):
            result = live_adapter.place_live_order(
                PaperOrderRequest(
                    "predict_fun",
                    "9001:YES",
                    "BUY",
                    5,
                    0.56,
                    {
                        "order": {
                            "hash": "0xhash",
                            "maker": "0xmaker",
                            "tokenId": "token-yes",
                            "signature": "0xsig",
                        },
                        "slippage_bps": 25,
                    },
                )
            )

        self.assertEqual(result["response"]["data"]["orderId"], "pf_order_123")
        self.assertEqual(calls[0][0], "POST")
        self.assertTrue(calls[0][1].endswith("/orders"))
        self.assertEqual(calls[0][2]["data"]["pricePerShare"], "0.56")
        self.assertEqual(calls[0][3]["x-api-key"], "predict-key")

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

        live_adapter = XOMarketAdapter({"live_trading_enabled": True, "live_trading_confirmed": True})
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
        place_response = load_fixture("betfair_exchange", "place_order_response")

        def fake_request(method: str, url: str, *, json=None, headers=None, timeout=None):
            self.assertEqual(headers["X-Application"], "betfair-app")
            self.assertEqual(headers["X-Authentication"], "betfair-session")
            if json["method"].endswith("listMarketCatalogue"):
                return FakeResponse({"jsonrpc": "2.0", "result": catalogue, "id": 1})
            if json["method"].endswith("listMarketBook"):
                return FakeResponse({"jsonrpc": "2.0", "result": market_book, "id": 1})
            if json["method"].endswith("placeOrders"):
                return FakeResponse({"jsonrpc": "2.0", "result": place_response, "id": 1})
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
            with self.assertRaises(MarketConfigurationError):
                adapter.place_live_order(PaperOrderRequest("betfair_exchange", "1.234:101", "BACK", 10, 0.5))

        self.assertEqual(events[0].event_id, "1.234")
        self.assertEqual([contract.contract_id for contract in contracts], ["1.234:101", "1.234:102"])
        self.assertEqual([round(level.price, 4) for level in book.bids], [0.5, 0.4545])
        self.assertEqual([round(level.price, 4) for level in book.asks], [0.5556, 0.5882])
        self.assertAlmostEqual(price.midpoint or 0.0, (0.5 + (1 / 1.8)) / 2)
        self.assertTrue(paper.accepted)

        live_adapter = BetfairExchangeAdapter({"live_trading_enabled": True, "live_trading_confirmed": True})
        calls = []

        def fake_live_request(method: str, url: str, *, json=None, headers=None, timeout=None):
            calls.append((method, url, json, headers, timeout))
            return FakeResponse({"jsonrpc": "2.0", "result": place_response, "id": 1})

        live_adapter.runtime.session.request = fake_live_request  # type: ignore[method-assign]
        with patch.dict(
            "os.environ",
            {"BETFAIR_APP_KEY": "betfair-app", "BETFAIR_SESSION_TOKEN": "betfair-session"},
        ):
            result = live_adapter.place_live_order(
                PaperOrderRequest("betfair_exchange", "1.234:101", "BACK", 10, 0.5, {"customer_ref": "client-1"})
            )

        self.assertEqual(result["response"]["status"], "SUCCESS")
        self.assertEqual(calls[0][2]["method"], "SportsAPING/v1.0/placeOrders")
        instruction = calls[0][2]["params"]["instructions"][0]
        self.assertEqual(instruction["side"], "BACK")
        self.assertEqual(instruction["limitOrder"]["price"], "2.0")


if __name__ == "__main__":
    unittest.main()

