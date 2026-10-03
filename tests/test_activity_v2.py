from __future__ import annotations

import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from polymarket import data_api
from polymarket.endpoints import DATA_ENDPOINTS
from polymarket.http_client import PolymarketResponseError, PolymarketValidationError


FIXTURE = Path(__file__).parent / "fixtures" / "polymarket" / "activity_v2_page.json"
WALLET = "0x" + "b" * 40
CONDITION = "0x" + "a" * 64


def page():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


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


class ActivityV2Tests(unittest.TestCase):
    def test_reviewed_v2_endpoint_and_wire_preserve_raw_and_canonical_fields(self):
        raw = page()
        response = WireResponse(raw)
        with patch("polymarket.http_client.requests.request", return_value=response) as fetch:
            result = data_api.get_activity_page_v2(
                WALLET, limit=2, types=["TRADE", "REDEEM"], side="BUY", market=[CONDITION],
                start=1, end=100, timeout=4,
            )
        self.assertEqual(fetch.call_args.args, ("GET", "https://data-api.polymarket.com/v2/activity"))
        self.assertEqual(fetch.call_args.kwargs["params"], {
            "user": WALLET, "limit": 2, "type": "TRADE,REDEEM", "side": "BUY", "condition": CONDITION,
            "start": 1, "end": 100, "sort_by": "TIMESTAMP", "sort_direction": "DESC",
            "exclude_deposits_withdrawals": "true",
        })
        self.assertEqual(fetch.call_args.kwargs["timeout"], 4)
        self.assertFalse(fetch.call_args.kwargs["allow_redirects"])
        self.assertTrue(response.closed)
        endpoint = DATA_ENDPOINTS["activity_v2"]
        self.assertEqual((endpoint.auth, endpoint.max_items), ("none", 1000))
        self.assertEqual(endpoint.doc_url, "https://docs.polymarket.com/api-reference/feeds/list-account-activity")
        row = result["data"][0]
        for key, value in raw["data"][0].items():
            self.assertEqual(row[key], value)
        self.assertEqual(row["asset"], row["token_id"])
        self.assertEqual(row["proxyWallet"], row["proxy_wallet"])
        self.assertEqual(row["transactionHash"], row["transaction_hash"])
        self.assertEqual(row["usdcSize"], row["usdc_size"])
        self.assertEqual(result["pagination"], raw["pagination"])

    def test_cursor_keeps_filters_and_bounds_and_never_sends_offset(self):
        raw = page()
        raw["pagination"].update(next_cursor=None, has_more=False)
        with patch.object(data_api, "_get_json", return_value=raw) as fetch:
            data_api.get_activity_page_v2(
                WALLET, cursor="fixture-opaque-cursor", limit=2, types=["TRADE"], event_id=["123"],
                start=50, end=100, sort_direction="ASC", exclude_deposits_withdrawals=False,
            )
        self.assertEqual(fetch.call_args.kwargs["params"], {
            "user": WALLET, "cursor": "fixture-opaque-cursor", "type": "TRADE", "event_id": "123",
            "start": 50, "end": 100, "sort_by": "TIMESTAMP", "sort_direction": "ASC",
            "exclude_deposits_withdrawals": "false",
        })

    def test_default_start_requests_full_history_and_empty_page_can_continue(self):
        raw = page()
        raw["data"] = []
        with patch.object(data_api, "_get_json", return_value=raw) as fetch:
            result = data_api.get_activity_page_v2(WALLET)
        self.assertEqual(fetch.call_args.kwargs["params"]["start"], 1)
        self.assertEqual(result["pagination"]["next_cursor"], "fixture-opaque-cursor")
        self.assertTrue(result["pagination"]["has_more"])

    def test_explicit_zero_start_retains_documented_three_year_semantics(self):
        with patch.object(data_api, "_get_json", return_value=page()) as fetch:
            data_api.get_activity_page_v2(WALLET, start=0)
        self.assertEqual(fetch.call_args.kwargs["params"]["start"], 0)

    def test_invalid_request_options_fail_before_http(self):
        invalid = (
            {"user": "0xinvalid"}, {"limit": 0}, {"limit": 1001}, {"limit": True},
            {"cursor": ""}, {"cursor": " padded "}, {"cursor": "line\nbreak"},
            {"sort_by": "CASH"}, {"sort_direction": "bad"}, {"start": None}, {"start": True},
            {"start": -1}, {"start": 2 ** 63}, {"end": 1.5}, {"start": 200, "end": 100},
            {"types": []}, {"types": "TRADE"}, {"types": [None]}, {"side": "IN"},
            {"market": [[]]}, {"market": ["invalid"]}, {"event_id": ["-1"]}, {"event_id": [1]},
            {"market": [CONDITION], "event_id": ["1"]}, {"exclude_deposits_withdrawals": 1},
            {"market": ["0x" + f"{index:064x}" for index in range(21)]},
        )
        with patch.object(data_api, "_get_json") as fetch:
            for options in invalid:
                options = dict(options)
                user = options.pop("user", WALLET)
                with self.subTest(options=options, user=user), self.assertRaises(PolymarketValidationError):
                    data_api.get_activity_page_v2(user, **options)
        fetch.assert_not_called()

    def test_malformed_envelopes_never_become_exhaustion(self):
        variants = [None, [], {}, {"data": None, "pagination": page()["pagination"]}]
        for transform in (
            lambda raw: raw.update(error="unavailable"),
            lambda raw: raw.update(status="failed"),
            lambda raw: raw.update(status_code=503),
            lambda raw: raw.update(ok="false"),
            lambda raw: raw.update(data=[None]),
            lambda raw: raw.pop("pagination"),
            lambda raw: raw["pagination"].pop("next_cursor"),
            lambda raw: raw["pagination"].update(has_more=1),
            lambda raw: raw["pagination"].update(has_more=False),
            lambda raw: raw["pagination"].update(next_cursor=None),
            lambda raw: raw["pagination"].update(next_cursor=" padded "),
            lambda raw: raw["pagination"].update(limit=True),
            lambda raw: raw["pagination"].update(limit=1001),
            lambda raw: raw["pagination"].update(offset=-1),
            lambda raw: raw["pagination"].update(offset=True),
            lambda raw: raw.update(data=raw["data"] * 3),
        ):
            raw = page()
            transform(raw)
            variants.append(raw)
        for raw in variants:
            with self.subTest(raw=raw), patch.object(data_api, "_get_json", return_value=raw):
                with self.assertRaisesRegex(PolymarketResponseError, "completeness is unknown"):
                    data_api.get_activity_page_v2(WALLET)

    def test_malformed_rows_and_conflicting_aliases_fail_closed(self):
        bad_fields = (
            ("timestamp", None), ("timestamp", True), ("timestamp", "100"), ("timestamp", 0),
            ("type", None), ("type", ""), ("size", "100"), ("price", True), ("usdc_size", -1),
            ("price", float("nan")), ("size", float("inf")), ("size", 10 ** 400),
            ("proxy_wallet", "0x" + "c" * 40), ("token_id", 123), ("token_id", ""),
            ("condition_id", ""), ("transaction_hash", ""),
            ("outcome_index", True), ("is_combo", 1), ("asset", "contradictory"),
        )
        for key, value in bad_fields:
            raw = page()
            raw["data"][0][key] = value
            with self.subTest(key=key, value=value), patch.object(data_api, "_get_json", return_value=raw):
                with self.assertRaises(PolymarketResponseError):
                    data_api.get_activity_page_v2(WALLET)

    def test_out_of_window_wrong_order_and_repeated_cursor_are_rejected(self):
        with patch.object(data_api, "_get_json", return_value=page()):
            for kwargs in ({"start": 101}, {"end": 99}, {"cursor": "fixture-opaque-cursor"}):
                with self.subTest(kwargs=kwargs), self.assertRaises(PolymarketResponseError):
                    data_api.get_activity_page_v2(WALLET, **kwargs)
        raw = page()
        raw["data"].append({**raw["data"][0], "timestamp": 101})
        with patch.object(data_api, "_get_json", return_value=raw):
            with self.assertRaises(PolymarketResponseError):
                data_api.get_activity_page_v2(WALLET)

    def test_unknown_fields_and_null_numbers_remain_lossless_without_mutating_source(self):
        raw = page()
        raw["data"][0].update(price=None, usdc_size=None, provider_future_field={"preserved": True})
        previous = copy.deepcopy(raw)
        with patch.object(data_api, "_get_json", return_value=raw):
            result = data_api.get_activity_page_v2(WALLET)
        self.assertEqual(raw, previous)
        self.assertIsNone(result["data"][0]["price"])
        self.assertIsNone(result["data"][0]["usdcSize"])
        self.assertEqual(result["data"][0]["provider_future_field"], {"preserved": True})

    def test_native_condition_redemption_preserves_empty_token_without_inventing_identity(self):
        # Public v2 response observed 2026-10-03: a payout names a condition,
        # with an empty token_id and side, rather than a BUY/SELL fill.
        raw = page()
        raw["data"][0].update(type="REDEEM", token_id="", side="", price=0.0,
                              size=120469.38883, usdc_size=120469.38883, outcome_index=1)
        previous = copy.deepcopy(raw)
        with patch.object(data_api, "_get_json", return_value=raw):
            result = data_api.get_activity_page_v2(WALLET)
        self.assertEqual(raw, previous)
        row = result["data"][0]
        self.assertEqual((row["type"], row["token_id"], row["asset"], row["side"]), ("REDEEM", "", "", ""))
        self.assertEqual(row["conditionId"], CONDITION)
        self.assertEqual(row["usdcSize"], 120469.38883)

    def test_nontrade_empty_token_does_not_relax_other_identities_or_aliases(self):
        for key, value in (("token_id", 123), ("transaction_hash", ""), ("condition_id", "invalid"),
                           ("proxy_wallet", ""), ("asset", "invented")):
            raw = page()
            raw["data"][0].update(type="REDEEM", token_id="")
            raw["data"][0][key] = value
            with self.subTest(key=key), patch.object(data_api, "_get_json", return_value=raw):
                with self.assertRaises(PolymarketResponseError):
                    data_api.get_activity_page_v2(WALLET)

    def test_native_account_reward_preserves_empty_token_and_condition(self):
        raw = page()
        raw["data"][0].update(type="REWARD", token_id="", condition_id="", side="", price=0.0,
                              usdc_size=1.25, size=0.0)
        with patch.object(data_api, "_get_json", return_value=raw):
            result = data_api.get_activity_page_v2(WALLET)
        row = result["data"][0]
        self.assertEqual((row["token_id"], row["asset"], row["condition_id"], row["conditionId"]),
                         ("", "", "", ""))
        self.assertEqual(row["type"], "REWARD")
        self.assertEqual(row["usdcSize"], 1.25)


if __name__ == "__main__":
    unittest.main()
