"""Real CLI/scanner/state integration for durable native v2 observations."""
from __future__ import annotations

import csv
import io
import json
import tempfile
import unittest
from contextlib import closing, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import market_sentinel_cli as cli
import web_api
from polymarket.http_client import PolymarketHTTPError
from polymarket.leaderboard_state import LeaderboardStateStore, _acquire_writer_lock


def native_row(identity: str, pnl: float = 5, volume: float = 10) -> dict:
    return {"user_id": "0x" + identity * 40, "pnl": pnl, "volume": volume}


def page(rows: list[dict], next_cursor: str | None) -> dict:
    return {"data": rows, "pagination": {"limit": 50, "offset": 0, "has_more": next_cursor is not None, "next_cursor": next_cursor}}


def run(arguments: list[str]) -> int:
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        return cli.main(arguments)


class CliLeaderboardV2ResumeTests(unittest.TestCase):
    def test_explicit_base_currency_reaches_live_query_disk_signature_and_direct_mdd(self):
        parser = cli.build_parser()
        for currency in ("USD", "USDC"):
            extra = [] if currency == "USD" else ["--equity-base-currency", currency]
            args = parser.parse_args(["polymarket-leaderboard", "--equity-base-usd", "500", *extra])
            self.assertEqual(cli.build_polymarket_leaderboard_params(args)["equity_base_currency"], [currency])
            options = cli._disk_backed_mdd_options(args)
            self.assertEqual((options["equity_base_usd"], options["equity_base_currency"]), (500, currency))
            with patch("market_sentinel_cli.polymarket_user_mdd_payload", return_value={}) as calculate:
                self.assertEqual(run(["polymarket-user-mdd", "--wallet", "0x" + "1" * 40,
                                      "--equity-base-usd", "500", *extra]), 0)
                self.assertEqual(calculate.call_args.kwargs["equity_base_currency"], currency)

    def command(self, state: Path, output: Path, *, checkpoint: bool = False) -> list[str]:
        return ["polymarket-leaderboard", "--checkpoint" if checkpoint else "--state-db", str(state),
                "--scanned", "unlimited", "--returned", "unlimited", "--scan-retry-attempts", "1",
                "--format", "json", "--output", str(output), "--quiet"]

    def test_sqlite_commits_native_rows_and_cursor_before_failure_then_resumes_to_eof(self):
        with tempfile.TemporaryDirectory() as temporary:
            state, output = Path(temporary) / "state.sqlite3", Path(temporary) / "result.json"
            output.write_text("previous export", encoding="utf-8")
            arguments = self.command(state, output)
            with patch("polymarket.data_api.get_leaderboard_v2_page", side_effect=[
                page([native_row("1")], "opaque:continue"), PolymarketHTTPError("offline", service="data", method="GET", url="https://data-api.polymarket.com/v2/leaderboard", status_code=503)
            ]) as fetch:
                self.assertEqual(run(arguments), 1)
            self.assertEqual(fetch.call_args_list[1].kwargs, {"cursor": "opaque:continue"})
            self.assertEqual(output.read_text(encoding="utf-8"), "previous export")
            with closing(LeaderboardStateStore(state, read_only=True)) as store:
                progress = store.progress()
                self.assertEqual((progress["source_api_version"], progress["next_offset"], progress["next_cursor"]),
                                 (2, 1, "opaque:continue"))
                self.assertFalse(progress["scan_complete"])
            with patch("polymarket.data_api.get_leaderboard_v2_page", return_value=page([native_row("2", 8, 20)], None)) as fetch:
                self.assertEqual(run(arguments + ["--resume"]), 0)
                fetch.assert_called_once_with(cursor="opaque:continue")
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(payload["source_api_version"], 2)
            self.assertEqual(payload["volume_unit"], "shares")
            self.assertTrue(payload["source_enumeration_complete"])
            self.assertEqual(payload["scan_limit_policy"], "complete_provider_cursor_pages")
            self.assertEqual(sorted(row["rank"] for row in payload["rows"]), [1, 2])
            for row in payload["rows"]:
                self.assertIsNone(row["volume_usd"])
                self.assertIsNone(row["roi_pct"])
            with patch("polymarket.data_api.get_leaderboard_v2_page") as fetch:
                self.assertEqual(run(arguments + ["--resume"]), 0)
                fetch.assert_not_called()

    def test_jsonl_rows_and_cursors_resume_atomically_and_explicit_eof_skips_fetch(self):
        with tempfile.TemporaryDirectory() as temporary:
            state, output = Path(temporary) / "state.jsonl", Path(temporary) / "result.json"
            output.write_text("previous export", encoding="utf-8")
            arguments = self.command(state, output, checkpoint=True)
            with patch("polymarket.data_api.get_leaderboard_v2_page", side_effect=[
                page([native_row("1")], "opaque-next"), PolymarketHTTPError("offline", service="data", method="GET", url="https://data-api.polymarket.com/v2/leaderboard", status_code=503)
            ]):
                self.assertEqual(run(arguments), 1)
            records = [json.loads(line) for line in state.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(records[0]["version"], 2)
            self.assertEqual(records[0]["signature"]["source_api_version"], 2)
            self.assertEqual((records[1]["page_index"], records[1]["source_cursor"], records[1]["next_cursor"]),
                             (0, None, "opaque-next"))
            self.assertEqual(records[1]["rows"], [native_row("1")])
            self.assertEqual(output.read_text(encoding="utf-8"), "previous export")
            # Only an unterminated final append can be repaired after a crash.
            with state.open("ab") as stream:
                stream.write(b'{"type":"leaderboard_page","rows":[')
            with patch("polymarket.data_api.get_leaderboard_v2_page", return_value=page([native_row("2")], None)) as fetch:
                self.assertEqual(run(arguments + ["--resume"]), 0)
                fetch.assert_called_once_with(cursor="opaque-next")
            records = [json.loads(line) for line in state.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(records), 3)
            self.assertEqual((records[2]["page_index"], records[2]["source_cursor"], records[2]["next_cursor"]),
                             (1, "opaque-next", None))
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(payload["counts"]["scanned"], 2)
            self.assertTrue(payload["source_enumeration_complete"])
            with patch("polymarket.data_api.get_leaderboard_v2_page") as fetch:
                self.assertEqual(run(arguments + ["--resume"]), 0)
                fetch.assert_not_called()

    def test_checkpoint_replay_rejects_cursor_chain_and_complete_record_corruption(self):
        signature = {"source_api_version": 2}
        header = {"type": "leaderboard_scan", "version": 2, "signature": signature}
        first = {"type": "leaderboard_page", "source_api_version": 2, "page_index": 0, "limit": 50,
                 "row_count": 1, "source_cursor": None, "next_cursor": "continue", "rows": [native_row("1")]}
        second = {**first, "page_index": 1, "source_cursor": "continue", "next_cursor": None, "rows": [native_row("2")]}
        invalid = [
            [{**first, "page_index": 1}], [{**first, "source_cursor": "invented"}],
            [first, {**second, "source_cursor": "wrong"}], [first, {**second, "next_cursor": "continue"}],
            [first, {**second, "page_index": 0}], [first, {**second, "row_count": 2}],
            [{**first, "limit": True}], [{**first, "source_api_version": 1}],
            [{**first, "next_cursor": None}, second],
            [{key: value for key, value in first.items() if key != "source_cursor"}],
        ]
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary) / "state.jsonl"
            for records in invalid:
                text = "\n".join(json.dumps(record) for record in [header, *records]) + "\n"
                state.write_text(text, encoding="utf-8")
                with self.subTest(records=records), self.assertRaises(ValueError):
                    cli._load_leaderboard_checkpoint(state, signature=signature)
                self.assertEqual(state.read_text(encoding="utf-8"), text)
            for tail in ('{"invalid":', '{"type":"leaderboard_page","page_index":0,"page_index":1}'):
                text = json.dumps(header) + "\n" + tail + "\n"
                state.write_text(text, encoding="utf-8")
                with self.subTest(tail=tail), self.assertRaises(ValueError):
                    cli._load_leaderboard_checkpoint(state, signature=signature)

    def test_live_financial_sorts_filters_and_legacy_checkpoint_cannot_start_or_overwrite(self):
        with tempfile.TemporaryDirectory() as temporary:
            state, output = Path(temporary) / "state.jsonl", Path(temporary) / "result.json"
            arguments = self.command(state, output, checkpoint=True)
            output.write_text("previous export", encoding="utf-8")
            for option in (["--sort", "roi_pct"], ["--sort", "volume_usd"], ["--min-volume-usd", "0"],
                           ["--max-roi-pct", "25"], ["--param", "min_roi_pct=0"]):
                with self.subTest(option=option), patch("polymarket.data_api.get_leaderboard_v2_page") as fetch:
                    self.assertEqual(run(arguments + option), 1)
                    fetch.assert_not_called()
                    self.assertFalse(state.exists())
                    self.assertEqual(output.read_text(encoding="utf-8"), "previous export")
            state.write_text(json.dumps({"type": "leaderboard_scan", "version": 1, "signature": {}}) + "\n", encoding="utf-8")
            original = state.read_bytes()
            with patch("polymarket.data_api.get_leaderboard_v2_page") as fetch:
                self.assertEqual(run(arguments + ["--resume"]), 1)
                fetch.assert_not_called()
            self.assertEqual(state.read_bytes(), original)

    def test_csv_share_sort_preserves_native_units_and_blank_unavailable_money(self):
        with tempfile.TemporaryDirectory() as temporary:
            state, output = Path(temporary) / "state.sqlite3", Path(temporary) / "result.csv"
            arguments = self.command(state, output)
            arguments[arguments.index("json")] = "csv"
            with patch("polymarket.data_api.get_leaderboard_v2_page", return_value=page([native_row("1", 20, 10), native_row("2", 5, 30)], None)):
                self.assertEqual(run(arguments + ["--sort", "volume_shares"]), 0)
            with output.open(encoding="utf-8", newline="") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual([float(row["volume_shares"]) for row in rows], [30, 10])
            for row in rows:
                self.assertEqual((row["source_api_version"], row["quote_currency"], row["volume_unit"]), ("2", "USDC", "shares"))
                self.assertEqual((row["volume_usd"], row["roi_pct"]), ("", ""))

    def test_checkpoint_writer_lock_rejects_a_second_scan_before_replay_or_publication(self):
        with tempfile.TemporaryDirectory() as temporary:
            state, output = Path(temporary) / "state.jsonl", Path(temporary) / "result.json"
            output.write_text("previous export", encoding="utf-8")
            with closing(_acquire_writer_lock(state)), patch("polymarket.data_api.get_leaderboard_v2_page") as fetch:
                self.assertEqual(run(self.command(state, output, checkpoint=True)), 1)
                fetch.assert_not_called()
                self.assertFalse(state.exists())
            self.assertEqual(output.read_text(encoding="utf-8"), "previous export")

    def test_native_share_filters_have_the_same_meaning_for_memory_sqlite_and_saved_export(self):
        for use_sqlite in (False, True):
            with self.subTest(use_sqlite=use_sqlite), tempfile.TemporaryDirectory() as temporary:
                state, output = Path(temporary) / "state.sqlite3", Path(temporary) / "result.json"
                arguments = self.command(state, output)
                if not use_sqlite:
                    arguments = [arguments[0], *arguments[3:]]
                with patch("polymarket.data_api.get_leaderboard_v2_page", return_value=page([native_row("1", 10, 10), native_row("2", 5, 30)], None)):
                    self.assertEqual(run(arguments + ["--min-volume-shares", "20", "--max-volume-shares", "40"]), 0)
                payload = json.loads(output.read_text(encoding="utf-8"))
                self.assertEqual(payload["counts"]["returned"], 1)
                self.assertEqual(payload["rows"][0]["volume_shares"], 30)
                if use_sqlite:
                    self.assertEqual(run(["polymarket-leaderboard-export", "--state-db", str(state), "--min-volume-shares", "20",
                                          "--format", "json", "--output", str(output)]), 0)
                    self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["rows"][0]["volume_shares"], 30)

    def test_declared_legacy_database_export_keeps_its_monetary_sort_and_basis(self):
        with tempfile.TemporaryDirectory() as temporary:
            state, output = Path(temporary) / "legacy.sqlite3", Path(temporary) / "result.json"
            with closing(LeaderboardStateStore(state)) as store:
                store.prepare({"remote_sort": "PNL", "direction": "DESC", "period": "all", "category": "OVERALL"}, resume=False)
                source = {"proxyWallet": "0x" + "1" * 40, "pnl": 5, "volume": 10}
                store.record_page(0, 50, [web_api.normalize_polymarket_leaderboard_row(source, 1)])
            with patch("polymarket.data_api.get_leaderboard_v2_page") as fetch:
                self.assertEqual(run(["polymarket-leaderboard-export", "--state-db", str(state), "--sort", "roi_pct",
                                      "--format", "json", "--output", str(output)]), 0)
                fetch.assert_not_called()
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(payload["source_api_version"], 1)
            self.assertEqual((payload["rows"][0]["volume_usd"], payload["rows"][0]["roi_pct"]), (10, 50))


if __name__ == "__main__":
    unittest.main()
