from __future__ import annotations

import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from polymarket import data_api
from polymarket.endpoints import DATA_ENDPOINTS
from polymarket.http_client import PolymarketResponseError, PolymarketValidationError


FIXTURES = Path(__file__).parent / "fixtures" / "polymarket"
WALLET = "0x" + "b" * 40
CONDITION = "0x" + "a" * 64


def page(name="positions"):
    return json.loads((FIXTURES / f"{name}_v2_page.json").read_text(encoding="utf-8"))


class WireResponse:
    status_code = 200
    headers = {}

    def __init__(self, payload):
        self.content = json.dumps(payload, allow_nan=False).encode("utf-8")
        self.closed = False

    def iter_content(self, chunk_size):
        for offset in range(0, len(self.content), chunk_size):
            yield self.content[offset:offset + chunk_size]

    def close(self):
        self.closed = True


class DataV2Tests(unittest.TestCase):
    def test_positions_wire_preserves_native_economics_and_identity_aliases(self):
        raw = page()
        response = WireResponse(raw)
        with patch("polymarket.http_client.requests.request", return_value=response) as fetch:
            result = data_api.get_positions_page_v2(
                WALLET, status="OPEN", market=[CONDITION], title="fixture", filter_amount=0,
                include_archived=True, sort_by="TIMESTAMP", sort_direction="ASC", start=1, end=100, limit=2,
            )
        self.assertEqual(fetch.call_args.args, ("GET", "https://data-api.polymarket.com/v2/positions"))
        self.assertEqual(fetch.call_args.kwargs["params"], {
            "user": WALLET, "condition": CONDITION, "status": "OPEN", "title": "fixture", "filter_type": "TOKENS",
            "filter_amount": 0, "include_archived": "true", "sort_by": "TIMESTAMP", "sort_direction": "ASC",
            "start": 1, "end": 100, "limit": 2,
        })
        self.assertTrue(response.closed)
        row = result["data"][0]
        for key, value in raw["data"][0].items():
            self.assertEqual(row[key], value)
        self.assertEqual(row["asset"], row["token_id"])
        self.assertEqual(row["proxyWallet"], row["proxy_wallet"])
        for manufactured in ("size", "totalBought", "initialValue", "cashPnl", "timestamp"):
            self.assertNotIn(manufactured, row)
        self.assertEqual(DATA_ENDPOINTS["positions_v2"].max_items, 1000)

    def test_positions_cursor_resends_anchor_and_narrowing_filters_without_default_spine(self):
        with patch.object(data_api, "_get_json", return_value=page()) as fetch:
            data_api.get_positions_page_v2(
                WALLET, cursor="fixture-position-cursor", market=[CONDITION], event_id=["123"],
                title="fixture", start=1, end=100,
            )
        self.assertEqual(fetch.call_args.kwargs["params"], {
            "user": WALLET, "condition": CONDITION, "event_id": "123", "cursor": "fixture-position-cursor",
            "title": "fixture", "start": 1, "end": 100, "filter_type": "TOKENS", "filter_amount": 0,
            "include_archived": "false",
        })

    def test_positions_market_anchor_and_closed_native_fields(self):
        raw = page()
        raw["data"][0].update(status="CLOSED", current_size=0, entry_cost_usdc=0, current_value=0)
        with patch.object(data_api, "_get_json", return_value=raw) as fetch:
            result = data_api.get_positions_page_v2(market=[CONDITION], status="CLOSED")
        self.assertNotIn("user", fetch.call_args.kwargs["params"])
        self.assertEqual(result["data"][0]["entry_cost_usdc"], 0)
        self.assertEqual(result["data"][0]["total_size"], 100)
        self.assertNotIn("totalBought", result["data"][0])

    def test_invalid_position_options_fail_before_transport(self):
        invalid = (
            {"user": None}, {"status": "ALL"}, {"status": "CLOSED", "include_archived": True},
            {"sort_by": "AVGPRICE"}, {"sort_direction": "bad"}, {"title": " "}, {"title": "x" * 201},
            {"filter_type": "USD"}, {"filter_amount": float("nan")}, {"filter_amount": -1},
            {"filter_amount": True}, {"include_archived": "true"}, {"start": -1}, {"end": True},
            {"user": None, "market": [CONDITION, "0x" + "c" * 64]},
            {"user": None, "market": [CONDITION], "event_id": ["123"]},
            {"user": None, "market": [CONDITION], "status": "REDEEMABLE_LOST"},
        )
        with patch.object(data_api, "_get_json") as fetch:
            for kwargs in invalid:
                kwargs = dict(kwargs)
                user = kwargs.pop("user", WALLET)
                with self.subTest(kwargs=kwargs, user=user), self.assertRaises(PolymarketValidationError):
                    data_api.get_positions_page_v2(user, **kwargs)
        fetch.assert_not_called()

    def test_invalid_native_position_rows_are_not_dropped_or_coerced(self):
        bad_fields = (("status", "UNKNOWN"), ("token_id", None), ("current_size", "10"),
                      ("total_size", True), ("total_cost_usdc", -1), ("realized_pnl", float("nan")),
                      ("last_event_at", "100"), ("entry_fees_usdc", float("inf")), ("verified", 1),
                      ("proxy_wallet", "0x" + "c" * 40), ("condition_id", "bad"), ("asset", "conflicting"))
        for key, value in bad_fields:
            raw = page()
            raw["data"][0][key] = value
            with self.subTest(key=key), patch.object(data_api, "_get_json", return_value=raw):
                with self.assertRaises(PolymarketResponseError):
                    data_api.get_positions_page_v2(WALLET)

    def test_null_native_economics_and_unknown_fields_are_preserved(self):
        raw = page()
        raw["data"][0].update(entry_cost_usdc=None, realized_pnl=None, last_event_at=None, new_field="preserved")
        previous = copy.deepcopy(raw)
        with patch.object(data_api, "_get_json", return_value=raw):
            result = data_api.get_positions_page_v2(WALLET)
            with self.assertRaises(PolymarketResponseError):
                data_api.get_positions_page_v2(WALLET, start=1)
        self.assertEqual(raw, previous)
        self.assertIsNone(result["data"][0]["entry_cost_usdc"])
        self.assertIsNone(result["data"][0]["realized_pnl"])
        self.assertEqual(result["data"][0]["new_field"], "preserved")

    def test_trade_wire_resends_same_user_filters_and_includes_maker_fills_when_requested(self):
        response = WireResponse(page("trades"))
        with patch("polymarket.http_client.requests.request", return_value=response) as fetch:
            result = data_api.get_trades_page_v2(
                WALLET, cursor="fixture-trade-cursor", market=[CONDITION], taker_only=False,
                side="BUY", start=1, end=100, filter_type="CASH", filter_amount=0.1,
            )
        self.assertEqual(fetch.call_args.args, ("GET", "https://data-api.polymarket.com/v2/trades"))
        self.assertEqual(fetch.call_args.kwargs["params"], {
            "user": WALLET, "condition": CONDITION, "cursor": "fixture-trade-cursor", "taker_only": "false",
            "side": "BUY", "start": 1, "end": 100, "filter_type": "CASH", "filter_amount": 0.1,
        })
        row = result["data"][0]
        self.assertEqual(row["asset"], row["token_id"])
        self.assertEqual(row["transactionHash"], row["transaction_hash"])
        self.assertEqual(row["size"], 100)
        self.assertNotIn("volume_usd", row)
        self.assertTrue(response.closed)

    def test_trade_invalid_options_fail_before_transport(self):
        with patch.object(data_api, "_get_json") as fetch:
            for kwargs in ({"start": None}, {"taker_only": 1}, {"side": "bad"},
                           {"event_id": ["123"], "market": [CONDITION]}, {"filter_amount": float("inf")},
                           {"limit": True}, {"cursor": ""}):
                with self.subTest(kwargs=kwargs), self.assertRaises(PolymarketValidationError):
                    data_api.get_trades_page_v2(WALLET, **kwargs)
        fetch.assert_not_called()

    def test_global_trade_feed_does_not_pretend_bounds_are_honored(self):
        with patch.object(data_api, "_get_json", return_value=page("trades")):
            result = data_api.get_trades_page_v2(start=500, end=600)
            with self.assertRaises(PolymarketResponseError):
                data_api.get_trades_page_v2(WALLET, start=500, end=600)
        self.assertEqual(result["data"][0]["timestamp"], 100)

    def test_trade_malformed_source_cannot_be_empty_success(self):
        for key, value in (("timestamp", True), ("timestamp", None), ("side", "bad"),
                           ("size", "100"), ("price", float("nan")), ("proxy_wallet", "bad")):
            raw = page("trades")
            raw["data"][0][key] = value
            with self.subTest(key=key), patch.object(data_api, "_get_json", return_value=raw):
                with self.assertRaises(PolymarketResponseError):
                    data_api.get_trades_page_v2(WALLET)

    def test_both_page_clients_reject_malformed_pagination_even_on_empty_pages(self):
        malformed = ({"has_more": False, "next_cursor": None},
                     {"has_more": False, "next_cursor": None, "limit": 2, "offset": True},
                     {"has_more": True, "next_cursor": None, "limit": 2, "offset": 0},
                     {"has_more": False, "next_cursor": "fixture-cursor", "limit": 2, "offset": 0})
        for getter in (data_api.get_positions_page_v2, data_api.get_trades_page_v2):
            for pagination in malformed:
                raw = {"data": [], "pagination": pagination}
                with self.subTest(getter=getter.__name__, pagination=pagination):
                    with patch.object(data_api, "_get_json", return_value=raw):
                        with self.assertRaises(PolymarketResponseError):
                            getter(WALLET)
            raw = {"data": [], "pagination": {"has_more": True, "next_cursor": "fixture-next", "limit": 2, "offset": 0}}
            with patch.object(data_api, "_get_json", return_value=raw):
                self.assertTrue(getter(WALLET)["pagination"]["has_more"])

    def test_aggregate_value_wire_and_documented_zero_state(self):
        raw = json.loads((FIXTURES / "value_v2.json").read_text(encoding="utf-8"))
        response = WireResponse(raw)
        with patch("polymarket.http_client.requests.request", return_value=response) as fetch:
            result = data_api.get_value_v2(WALLET, market=[CONDITION])
        self.assertEqual(fetch.call_args.args, ("GET", "https://data-api.polymarket.com/v2/value"))
        self.assertEqual(fetch.call_args.kwargs["params"], {"user": WALLET, "condition": CONDITION})
        self.assertEqual(result["data"], {"proxy_wallet": WALLET, "value": 0, "proxyWallet": WALLET})
        self.assertNotIn("pagination", result)

    def test_value_invalid_or_unavailable_never_becomes_zero(self):
        variants = [None, [], {"data": []}, {"data": None}, {"data": {"proxy_wallet": WALLET}},
                    {"data": {"proxy_wallet": WALLET, "value": 0}, "error": "upstream failure"},
                    {"data": {"proxy_wallet": WALLET, "value": 0}, "pagination": {}},
                    {"data": {"proxy_wallet": "0x" + "c" * 40, "value": 0}}]
        variants += [{"data": {"proxy_wallet": WALLET, "value": value}}
                     for value in (None, "0", True, -1, float("nan"), float("inf"))]
        for raw in variants:
            with self.subTest(raw=raw), patch.object(data_api, "_get_json", return_value=raw):
                with self.assertRaises(PolymarketResponseError):
                    data_api.get_value_v2(WALLET)


if __name__ == "__main__":
    unittest.main()
