from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import market_sentinel_cli
import web_api
from polymarket import data_api
from polymarket.http_client import PolymarketResponseError
from polymarket.leaderboard import LEADERBOARD_MAX_OFFSET, wallet_membership_fingerprint
from polymarket.leaderboard_state import LeaderboardStateStore


def page(offset: int, count: int = 50) -> list[dict]:
    return [{"proxyWallet": "0x" + format(index + 1, "040x"), "rank": index + 1, "pnl": 100, "vol": 100}
            for index in range(offset, offset + count)]


def native_rows(offset: int, count: int = 50) -> list[dict]:
    return [{"user_id": "0x" + format(index + 1, "040x"), "rank": index + 1, "pnl": 100, "volume": 100}
            for index in range(offset, offset + count)]


def cursor_page(rows, *, next_cursor=None, limit=1000):
    return {"data": rows, "pagination": {
        "has_more": next_cursor is not None, "next_cursor": next_cursor, "limit": limit, "offset": 0,
    }}


def complete_pages():
    return [cursor_page(native_rows(0, 1000), next_cursor="fixture-page-two"),
            cursor_page(native_rows(1000, 1000), next_cursor="fixture-page-three"),
            cursor_page(native_rows(2000, 1000))]


class LeaderboardPaginationTests(unittest.TestCase):
    def scan(self, **options):
        summary, warnings = {}, []
        arguments = {"scan_limit": None, "remote_sort": "PNL", "direction": "DESC", "period": "all",
                     "category": "OVERALL", "scan_concurrency": 1, "is_cancelled": lambda: False,
                     "emit_progress": lambda *args, **kwargs: None, "warnings": warnings, "scan_summary": summary}
        arguments.update(options)
        rows, cancelled = web_api._fetch_polymarket_leaderboard_scan_rows(**arguments)
        return rows, cancelled, summary, warnings

    def test_serial_and_concurrent_unlimited_scans_follow_exact_cursors_past_legacy_bound(self) -> None:
        for concurrency in (1, 6, 12):
            with self.subTest(concurrency=concurrency), patch.object(
                data_api, "get_leaderboard_v2_page", side_effect=complete_pages()
            ) as get:
                rows, cancelled, summary, warnings = self.scan(scan_concurrency=concurrency)
            self.assertEqual([call.kwargs.get("cursor") for call in get.call_args_list],
                             [None, "fixture-page-two", "fixture-page-three"])
            self.assertTrue(all("offset" not in call.kwargs for call in get.call_args_list))
            self.assertEqual(len(rows), 3000)
            self.assertFalse(cancelled)
            self.assertEqual(summary["completion_reason"], "end_of_results")
            self.assertTrue(summary["source_enumeration_complete"])
            self.assertEqual(summary["source_api_version"], 2)
            if concurrency > 1:
                self.assertTrue(warnings)

    def test_legacy_resume_offset_past_bound_does_not_make_a_network_request(self) -> None:
        with patch.object(data_api, "get_leaderboard_v2_page") as get:
            with self.assertRaisesRegex(ValueError, "continuation cursor"):
                self.scan(scan_start_offset=12335250)
        get.assert_not_called()

    def test_finite_budget_and_short_page_retain_their_own_stop_reasons(self) -> None:
        with patch.object(data_api, "get_leaderboard_v2_page", return_value=cursor_page(
            native_rows(0, 75), next_cursor="fixture-more", limit=75
        )):
            rows, _cancelled, summary, _warnings = self.scan(scan_limit=75)
        self.assertEqual(len(rows), 75)
        self.assertEqual(summary["completion_reason"], "scan_limit_reached")
        self.assertFalse(summary["source_enumeration_complete"])
        self.assertEqual(summary["next_cursor"], "fixture-more")
        pages = [cursor_page(native_rows(0, 2), next_cursor="fixture-after-short"), cursor_page([])]
        with patch.object(data_api, "get_leaderboard_v2_page", side_effect=pages) as get:
            _rows, _cancelled, summary, _warnings = self.scan()
        self.assertEqual(get.call_count, 2)
        self.assertEqual(summary["completion_reason"], "end_of_results")
        self.assertTrue(summary["source_enumeration_complete"])

    def test_same_wallets_with_reordered_membership_and_changed_metrics_stop(self) -> None:
        original = native_rows(0)
        changed = [{**row, "pnl": 900, "rank": index + 51} for index, row in enumerate(reversed(original))]
        with patch.object(data_api, "get_leaderboard_v2_page", side_effect=[
            cursor_page(original, next_cursor="fixture-repeat"), cursor_page(changed)
        ]) as get:
            rows, _cancelled, summary, _warnings = self.scan()
        self.assertEqual(get.call_count, 2)
        self.assertEqual(len(rows), 50)
        self.assertEqual(summary["completion_reason"], "repeated_page")

    def test_finite_threshold_commits_full_cursor_pages_and_reports_overrun(self) -> None:
        pages = [cursor_page(native_rows(0, 50), next_cursor="fixture-after-short", limit=50),
                 cursor_page(native_rows(50, 50), limit=50)]
        committed = []
        def save(index, limit, rows, source_cursor, next_cursor):
            committed.append((index, len(rows), source_cursor, next_cursor))
        with patch.object(data_api, "get_leaderboard_v2_page", side_effect=pages):
            rows, _cancelled, summary, warnings = self.scan(scan_limit=75, cursor_page_callback=save)
        self.assertEqual(len(rows), 100)
        self.assertEqual(summary["scanned"], 100)
        self.assertEqual(summary["scan_limit_overrun"], 25)
        self.assertEqual(summary["scan_limit_policy"], "complete_provider_cursor_pages")
        self.assertEqual(committed, [(0, 50, None, "fixture-after-short"), (1, 50, "fixture-after-short", None)])
        self.assertTrue(any("overrun" in warning.lower() or "threshold" in warning.lower() for warning in warnings))

    def test_partial_overlap_with_new_wallets_is_not_a_repeat(self) -> None:
        with patch.object(data_api, "get_leaderboard_v2_page", side_effect=[
            cursor_page(native_rows(0), next_cursor="fixture-overlap"), cursor_page(native_rows(25))
        ]):
            rows, _cancelled, summary, _warnings = self.scan()
        self.assertEqual(len(rows), 100)
        self.assertEqual(summary["completion_reason"], "end_of_results")

    def test_membership_uses_normalized_wallets_and_refuses_unknown_membership(self) -> None:
        self.assertEqual(wallet_membership_fingerprint([{"wallet": " 0xABC "}]), wallet_membership_fingerprint([{"proxyWallet": "0xabc"}]))
        self.assertEqual(wallet_membership_fingerprint([{"wallet": "0xabc"}, {"name": "unknown"}]), "")
        self.assertEqual(wallet_membership_fingerprint([]), "")

    def test_unknown_wallets_cannot_claim_complete_discovery(self) -> None:
        with patch.object(data_api, "get_leaderboard_v2_page", return_value=cursor_page([{"rank": 1}])):
            with self.assertRaisesRegex(PolymarketResponseError, "wallet identity"):
                self.scan()

    def test_durable_membership_survives_resume_and_legacy_column_migration(self) -> None:
        for legacy in (False, True):
            with self.subTest(legacy=legacy), tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary) / "state.sqlite3"
                with closing(LeaderboardStateStore(path)) as store:
                    store.prepare({}, resume=False)
                    store.record_page(0, 50, [web_api.normalize_polymarket_leaderboard_row(row, 1) for row in page(0)])
                    if legacy:
                        store.connection.executescript("""
                            CREATE TABLE legacy_pages (
                                page_offset INTEGER PRIMARY KEY, page_limit INTEGER NOT NULL,
                                row_count INTEGER NOT NULL, fingerprint TEXT NOT NULL DEFAULT '', saved_at INTEGER NOT NULL
                            );
                            INSERT INTO legacy_pages SELECT page_offset, page_limit, row_count, fingerprint, saved_at FROM pages;
                            DROP TABLE pages;
                            ALTER TABLE legacy_pages RENAME TO pages;
                        """)
                        store.connection.commit()
                with closing(LeaderboardStateStore(path)) as store:
                    changed = [{**row, "pnl": 999} for row in reversed(page(0))]
                    self.assertFalse(store.record_page(50, 50, [web_api.normalize_polymarket_leaderboard_row(row, 1) for row in changed]))
                    self.assertEqual(store.progress()["stop_reason"], "repeated_page")
                    self.assertEqual(store.progress()["scanned"], 50)

    def test_api_and_cli_preserve_unlimited_budgets_and_report_real_cursor_exhaustion(self) -> None:
        with patch.object(data_api, "get_leaderboard_v2_page", side_effect=complete_pages()):
            result = web_api.polymarket_leaderboard_payload({"limit": ["unlimited"], "scan_limit": ["unlimited"], "sort": ["pnl_usd"]})
        self.assertIsNone(result["limit"])
        self.assertIsNone(result["scan_limit"])
        self.assertEqual(result["completion_reason"], "end_of_results")
        self.assertTrue(result["source_enumeration_complete"])
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "result.json"
            args = ["polymarket-leaderboard", "--state-db", str(Path(temporary) / "state.sqlite3"),
                    "--scanned", "unlimited", "--returned", "unlimited", "--sort", "pnl", "--format", "json", "--output", str(output), "--quiet"]
            with patch.object(data_api, "get_leaderboard_v2_page", side_effect=complete_pages()):
                self.assertEqual(market_sentinel_cli.main(args), 0)
            result = json.loads(output.read_text())
            self.assertEqual(result["completion_reason"], "end_of_results")
            self.assertTrue(result["source_enumeration_complete"])
            self.assertIsNone(result["scan_limit"])
            self.assertEqual(result["counts"]["scanned"], 3000)
            with patch.object(data_api, "get_leaderboard_v2_page") as get:
                self.assertEqual(market_sentinel_cli.main(args + ["--resume"]), 0)
            get.assert_not_called()

    def test_data_wrapper_accepts_maximum_offset_and_rejects_one_past_it(self) -> None:
        with patch.object(data_api, "_get_json", return_value=[]) as get:
            data_api.get_leaderboard(offset=LEADERBOARD_MAX_OFFSET)
            self.assertEqual(get.call_args.kwargs["params"]["offset"], LEADERBOARD_MAX_OFFSET)
            with self.assertRaises(ValueError):
                data_api.get_leaderboard(offset=LEADERBOARD_MAX_OFFSET + 1)
            self.assertEqual(get.call_count, 1)

    def test_malformed_upstream_pages_cannot_claim_source_exhaustion(self) -> None:
        for raw in (None, {}, {"error": "temporary upstream failure"}, {"data": None},
                    [page(0, 1)[0], None], {"data": [None]}, {"data": [], "users": page(0, 1)}):
            with self.subTest(raw=raw), patch.object(data_api, "_get_json", return_value=raw):
                with self.assertRaisesRegex(PolymarketResponseError, "coverage is unknown"):
                    self.scan()

    def test_legacy_wrapper_retains_supported_bare_and_wrapped_arrays(self) -> None:
        for key in (None, "data", "leaderboard", "users", "results"):
            for rows in ([], page(0, 1)):
                raw = rows if key is None else {key: rows}
                with self.subTest(key=key, rows=rows), patch.object(data_api, "_get_json", return_value=raw):
                    result = data_api.get_leaderboard()
                self.assertEqual(result, rows)


if __name__ == "__main__":
    unittest.main()
