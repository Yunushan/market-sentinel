from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import market_sentinel_cli as cli
from polymarket import data_api
from polymarket.endpoints import DATA_ENDPOINTS
from polymarket.http_client import PolymarketResponseError, PolymarketValidationError


WALLET = "0x" + "a" * 40
ROW = {"rank": 1, "user_id": WALLET, "pnl": 12.0, "volume": 30.0}


def page(*, rows=None, has_more=False, cursor=None):
    return {
        "data": list(rows if rows is not None else [ROW]),
        "pagination": {"limit": 1, "offset": 0, "has_more": has_more, "next_cursor": cursor},
    }


class LeaderboardV2Tests(unittest.TestCase):
    def run_cli(self, *args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch("sys.stdout", stdout), patch("sys.stderr", stderr):
            code = cli.main(["polymarket-leaderboard-v2", *args])
        return code, stdout.getvalue(), stderr.getvalue()

    def test_first_page_uses_v2_board_contract_and_preserves_share_volume(self) -> None:
        raw_page = page()
        with patch.object(data_api, "_get_json", return_value=raw_page) as fetch:
            result = data_api.get_leaderboard_v2_page(
                limit=1, sort_by="VOLUME", period="week", category="SPORTS", timeout=4,
            )
        self.assertIs(result, raw_page)
        self.assertEqual(result["data"][0]["volume"], 30.0)
        self.assertNotIn("volume_usd", result["data"][0])
        self.assertEqual(DATA_ENDPOINTS["leaderboard_v2"].path, "/v2/leaderboard")
        self.assertEqual(fetch.call_args.args, ("leaderboard_v2",))
        self.assertEqual(fetch.call_args.kwargs["params"], {
            "limit": 1, "sort_by": "VOLUME", "time_period": "week", "category": "sports",
        })
        self.assertEqual(fetch.call_args.kwargs["timeout"], 4)

    def test_cursor_page_sends_only_the_opaque_cursor(self) -> None:
        with patch.object(data_api, "_get_json", return_value=page()) as fetch:
            data_api.get_leaderboard_v2_page(cursor="signed-cursor")
        self.assertEqual(fetch.call_args.kwargs["params"], {"cursor": "signed-cursor"})

    def test_invalid_first_page_options_and_cursor_fail_before_http(self) -> None:
        invalid = (
            {"limit": 0}, {"limit": 1001}, {"limit": True},
            {"sort_by": "VOL"}, {"period": "year"}, {"category": "UNKNOWN"},
            {"category": "COMBOS", "sort_by": "VOLUME"},
            {"cursor": ""}, {"cursor": " padded "},
            {"cursor": "signed", "category": "SPORTS"},
        )
        with patch.object(data_api, "_get_json") as fetch:
            for options in invalid:
                with self.subTest(options=options), self.assertRaises(PolymarketValidationError):
                    data_api.get_leaderboard_v2_page(**options)
        fetch.assert_not_called()

    def test_malformed_rows_and_pagination_fail_closed(self) -> None:
        malformed = (
            [],
            {"data": {}, "pagination": {"has_more": False, "next_cursor": None}},
            {"data": [None], "pagination": {"has_more": False, "next_cursor": None}},
            {"data": [ROW]},
            {"data": [ROW], "pagination": {"has_more": 0, "next_cursor": None}},
            page(has_more=True, cursor=None),
            page(has_more=False, cursor="unexpected"),
        )
        for response in malformed:
            with self.subTest(response=response), patch.object(data_api, "_get_json", return_value=response):
                with self.assertRaisesRegex(PolymarketResponseError, "coverage is unknown"):
                    data_api.get_leaderboard_v2_page()

    def test_walk_follows_cursor_even_after_an_empty_page(self) -> None:
        first = page(rows=[], has_more=True, cursor="one")
        second = page(rows=[ROW], has_more=False, cursor=None)
        with patch.object(data_api, "_get_json", side_effect=[first, second]) as fetch:
            pages = list(data_api.iter_leaderboard_v2_pages(limit=1, category="SPORTS"))
        self.assertEqual(pages, [first, second])
        self.assertEqual(fetch.call_args_list[0].kwargs["params"]["category"], "sports")
        self.assertEqual(fetch.call_args_list[1].kwargs["params"], {"cursor": "one"})

    def test_walk_rejects_repeated_cursor_and_unfinished_page_budget(self) -> None:
        repeated = page(has_more=True, cursor="same")
        with patch.object(data_api, "_get_json", side_effect=[repeated, repeated]) as fetch:
            with self.assertRaisesRegex(PolymarketResponseError, "repeated a cursor"):
                list(data_api.iter_leaderboard_v2_pages(limit=1))
        self.assertEqual(fetch.call_count, 2)

        with patch.object(data_api, "_get_json", return_value=repeated) as fetch:
            with self.assertRaisesRegex(PolymarketResponseError, "page budget reached"):
                list(data_api.iter_leaderboard_v2_pages(limit=1, max_pages=1))
        self.assertEqual(fetch.call_count, 1)

    def test_cli_exposes_bounded_raw_board_pages_and_explicit_partial_scope(self) -> None:
        first = page(rows=[ROW], has_more=True, cursor="one")
        second = page(rows=[{**ROW, "rank": 2, "user_id": "0x" + "b" * 40}], has_more=True, cursor="two")
        with patch.object(data_api, "_get_json", side_effect=[first, second]) as fetch:
            code, stdout, stderr = self.run_cli(
                "--page-size", "1", "--max-pages", "2", "--sort", "VOLUME",
                "--period", "week", "--category", "SPORTS",
            )
        self.assertEqual(code, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["rows_observed"], 2)
        self.assertEqual(payload["next_cursor"], "two")
        self.assertEqual(payload["completion_reason"], "page_budget_reached")
        self.assertFalse(payload["cursor_exhausted"])
        self.assertFalse(payload["consistent_snapshot_verified"])
        self.assertFalse(payload["all_accounts_covered"])
        self.assertEqual(payload["volume_unit"], "outcome_shares")
        self.assertEqual(payload["pnl_basis"], "marked_equity_change_net_flows")
        self.assertEqual(payload["rows"][0], ROW)
        self.assertEqual(fetch.call_args_list[1].kwargs["params"], {"cursor": "one"})

    def test_cli_cursor_resume_does_not_invent_board_parameters(self) -> None:
        with patch.object(data_api, "_get_json", return_value=page(rows=[ROW])) as fetch:
            code, stdout, stderr = self.run_cli("--cursor", "signed")
        self.assertEqual(code, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(fetch.call_args.kwargs["params"], {"cursor": "signed"})
        self.assertEqual(payload["completion_reason"], "cursor_exhausted")
        self.assertFalse(payload["board_parameters_known"])
        self.assertIsNone(payload["category"])
        self.assertIsNone(payload["period"])
        self.assertIsNone(payload["sort_by"])
        self.assertEqual(payload["pnl_basis"], "unknown_cursor_bound_window")

    def test_cli_invalid_second_page_preserves_previous_export(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "ranked.json"
            output.write_text("previous export", encoding="utf-8")
            responses = [page(has_more=True, cursor="one"), page(has_more=True, cursor=None)]
            with patch.object(data_api, "_get_json", side_effect=responses):
                code, stdout, stderr = self.run_cli("--page-size", "1", "--max-pages", "2", "--output", str(output))
            self.assertEqual(code, 1)
            self.assertEqual(stdout, "")
            self.assertIn("coverage is unknown", stderr)
            self.assertEqual(output.read_text(encoding="utf-8"), "previous export")


if __name__ == "__main__":
    unittest.main()
