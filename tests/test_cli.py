from __future__ import annotations

import io
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from core.storage import load_config

import market_sentinel_cli
from polymarket.http_client import PolymarketHTTPError
from polymarket.leaderboard_state import LeaderboardStateStore


def run_cli_silent(args: list[str]) -> int:
    stdout = io.StringIO()
    with patch("sys.stdout", stdout):
        return market_sentinel_cli.main(args)


class MarketSentinelCliTests(unittest.TestCase):
    def test_polymarket_leaderboard_cli_builds_unlimited_scan_params(self) -> None:
        parser = market_sentinel_cli.build_parser()
        args = parser.parse_args(
            [
                "polymarket-leaderboard",
                "--sort",
                "roi",
                "--returned",
                "unlimited",
                "--scanned",
                "all",
                "--compute-mdd",
                "--fast-scan",
                "--mdd-scan",
                "0",
                "--max-mdd-pct",
                "20",
                "--param",
                "mdd_cache_ttl_seconds=120",
            ]
        )

        params = market_sentinel_cli.build_polymarket_leaderboard_params(args)

        self.assertEqual(params["sort"], ["roi_pct"])
        self.assertEqual(params["limit"], ["unlimited"])
        self.assertEqual(params["scan_limit"], ["all"])
        self.assertEqual(params["compute_mdd"], ["true"])
        self.assertEqual(params["fast_scan"], ["true"])
        self.assertEqual(params["mdd_scan_limit"], ["0"])
        self.assertEqual(params["max_mdd_pct"], ["20"])
        self.assertEqual(params["mdd_cache_ttl_seconds"], ["120"])
        self.assertEqual(params["scan_retry_attempts"], ["5"])
        self.assertEqual(params["scan_retry_delay_seconds"], ["30"])

    def test_polymarket_leaderboard_cli_runs_headless_json_output(self) -> None:
        payload = {
            "rows": [{"rank": 1, "display_name": "alpha", "wallet": "0xabc", "roi_pct": 12.5}],
            "counts": {"returned": 1, "filtered": 1, "scanned": 5, "mdd_computed": 0},
            "warnings": [],
        }

        stdout = io.StringIO()
        with patch("market_sentinel_cli.polymarket_leaderboard_payload", return_value=payload) as mock_payload, patch(
            "sys.stdout",
            stdout,
        ):
            exit_code = market_sentinel_cli.main(
                [
                    "polymarket-leaderboard",
                    "--returned",
                    "all",
                    "--scanned",
                    "all",
                    "--format",
                    "json",
                    "--quiet",
                ]
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(json.loads(stdout.getvalue())["counts"]["scanned"], 5)
        called_params = mock_payload.call_args.args[0]
        self.assertEqual(called_params["limit"], ["all"])
        self.assertEqual(called_params["scan_limit"], ["all"])
        self.assertIsNone(mock_payload.call_args.kwargs["progress_callback"])

    def test_polymarket_leaderboard_progress_logs_runtime_data(self) -> None:
        stderr = io.StringIO()
        emit = market_sentinel_cli._progress_printer(True, started_at=time.monotonic() - 10)

        with patch("sys.stderr", stderr):
            emit(
                {
                    "phase": "leaderboard",
                    "percent": 12.5,
                    "scanned": 100,
                    "scan_limit": 1000,
                    "scan_limit_unlimited": False,
                    "mdd_attempted": 0,
                    "mdd_total": 0,
                    "message": "Scanning leaderboard rows 100/1000.",
                }
            )

        line = stderr.getvalue()
        self.assertIn("status=running", line)
        self.assertIn("elapsed=", line)
        self.assertIn("phase=leaderboard", line)
        self.assertIn("percent=12.5%", line)
        self.assertIn("scan_rate=", line)
        self.assertIn("eta=", line)
        self.assertIn("Scanning leaderboard rows 100/1000.", line)

    def test_disk_backed_leaderboard_can_resume_after_a_transient_http_failure(self) -> None:
        parser = market_sentinel_cli.build_parser()
        args = parser.parse_args(
            [
                "polymarket-leaderboard",
                "--state-db",
                "data/leaderboard.sqlite3",
                "--resume-on-failure",
                "--resume-backoff-seconds",
                "1",
                "--quiet",
            ]
        )
        transient_error = PolymarketHTTPError("ssl eof", service="data", method="GET", url="https://data-api.polymarket.com")

        with patch("market_sentinel_cli._run_disk_backed_polymarket_leaderboard", side_effect=[transient_error, 0]) as mock_run, patch(
            "market_sentinel_cli.time.sleep"
        ) as mock_sleep:
            exit_code = market_sentinel_cli.run_polymarket_leaderboard(args)

        self.assertEqual(exit_code, 0)
        self.assertEqual(mock_run.call_count, 2)
        self.assertTrue(args.resume)
        mock_sleep.assert_called_once_with(1.0)

    def test_resume_on_failure_requires_disk_backed_state(self) -> None:
        self.assertEqual(run_cli_silent(["polymarket-leaderboard", "--resume-on-failure", "--quiet"]), 1)

    def test_live_validation_cli_covers_local_report_review_workflows(self) -> None:
        parser = market_sentinel_cli.build_parser()
        commands = {
            ("polymarket-live-reports", "list"): "run_polymarket_live_reports_list",
            ("polymarket-live-reports", "store"): "run_polymarket_live_reports_store",
            ("polymarket-live-reports", "review", "report-key", "--format", "markdown"): "run_polymarket_live_reports_review",
            ("polymarket-live-decisions", "list"): "run_polymarket_live_decisions_list",
            ("polymarket-live-decisions", "record", "--report-key", "report-key", "--payload-hash", "payload", "--target-tier", "credential_live_verified", "--decision", "rejected", "--reviewer-note", "no evidence", "--review-bundle-hash", "bundle"): "run_polymarket_live_decisions_record",
            ("polymarket-promotion-proposal", "show"): "run_polymarket_live_proposal_show",
            ("polymarket-promotion-proposal", "snapshots", "diff", "snapshot-key"): "run_polymarket_live_snapshots_diff",
        }

        for command, expected in commands.items():
            with self.subTest(command=command):
                args = parser.parse_args(list(command))
                self.assertEqual(args.func.__name__, expected)

    def test_live_validation_cli_lists_reports_and_writes_markdown_review(self) -> None:
        stdout = io.StringIO()
        with patch("market_sentinel_cli.polymarket_live_validation_reports_payload", return_value={"entries": []}), patch("sys.stdout", stdout):
            exit_code = market_sentinel_cli.main(["polymarket-live-reports", "list", "--compact"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(json.loads(stdout.getvalue()), {"entries": []})

        stdout = io.StringIO()
        review = {"bundle": {"review": "safe"}}
        with patch("market_sentinel_cli.polymarket_live_validation_report_review_payload", return_value=review), patch(
            "market_sentinel_cli.live_validation_report_review_markdown", return_value="# Review\n"
        ), patch("sys.stdout", stdout):
            exit_code = market_sentinel_cli.main(
                ["polymarket-live-reports", "review", "report-key", "--format", "markdown"]
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(stdout.getvalue(), "# Review\n")

    def test_paper_marks_cli_refreshes_durable_sidecar_and_supports_mark_commands(self) -> None:
        parser = market_sentinel_cli.build_parser()
        commands = {
            ("paper", "marks", "refresh"): "run_paper_marks_refresh",
            ("paper", "marks", "refresh-selected", "--market", "kalshi", "--contract", "KX"): "run_paper_marks_refresh_selected",
            ("paper", "marks", "clear"): "run_paper_marks_clear",
            ("paper", "marks", "clear-selected", "--market", "kalshi", "--contract", "KX"): "run_paper_marks_clear_selected",
        }
        for command, expected in commands.items():
            with self.subTest(command=command):
                self.assertEqual(parser.parse_args(list(command)).func.__name__, expected)

        cfg = SimpleNamespace(paper_trades=[])
        rows = [{"market_id": "kalshi", "contract_id": "KX", "net_size": 1.0}]
        marks = {("kalshi", "KX"): {"mark_price": 0.61, "source": "bid", "marked_at": 123}}
        with tempfile.TemporaryDirectory() as tmp:
            marks_path = Path(tmp) / "marks.json"
            stdout = io.StringIO()
            with patch("market_sentinel_cli._load_cfg", return_value=cfg), patch(
                "market_sentinel_cli.paper_position_rows", return_value=rows
            ), patch("market_sentinel_cli.refresh_paper_marks", return_value=(marks, [])), patch(
                "market_sentinel_cli.paper_payload", return_value={"summary": {"marked": 1}}
            ), patch("sys.stdout", stdout):
                exit_code = market_sentinel_cli.main(["paper", "marks", "refresh", "--marks-file", str(marks_path)])

            self.assertEqual(exit_code, 0)
            self.assertEqual(json.loads(stdout.getvalue())["paper"]["summary"]["marked"], 1)
            self.assertEqual(market_sentinel_cli._load_paper_marks(marks_path), marks)

    def test_leaderboard_status_reads_existing_state_without_starting_a_scan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "leaderboard.sqlite3"
            pid_file = Path(tmp) / "scan.pid"
            pid_file.write_text(f"{os.getpid()}\n", encoding="utf-8")
            store = LeaderboardStateStore(state_path)
            try:
                store.prepare({"remote_sort": "PNL", "direction": "DESC", "period": "all", "category": "OVERALL"}, resume=False)
                store.record_page(
                    0,
                    50,
                    [{"rank": 1, "display_name": "leader", "wallet": "0x" + "1" * 40, "pnl_usd": 10.0, "volume_usd": 100.0, "roi_pct": 10.0, "trade_count": 1, "raw": {}}],
                )
            finally:
                store.close()

            stdout = io.StringIO()
            with patch("sys.stdout", stdout):
                exit_code = market_sentinel_cli.main(
                    ["polymarket-leaderboard-status", "--state-db", str(state_path), "--pid-file", str(pid_file), "--compact"]
                )

            payload = json.loads(stdout.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertEqual(payload["rows"], 1)
            self.assertEqual(payload["pages"], 1)
            self.assertEqual(payload["mdd_pending"], 1)
            self.assertEqual(payload["signature"]["remote_sort"], "PNL")
            self.assertEqual(payload["process"]["status"], "running")
            self.assertEqual(payload["process"]["pid"], os.getpid())

    def test_leaderboard_export_streams_partial_completed_mdd_rows_without_scanning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "leaderboard.sqlite3"
            store = LeaderboardStateStore(state_path)
            try:
                store.prepare({"remote_sort": "PNL", "direction": "DESC", "period": "all", "category": "OVERALL"}, resume=False)
                store.record_page(
                    0,
                    2,
                    [
                        {"rank": 1, "display_name": "eligible", "wallet": "0x" + "1" * 40, "pnl_usd": 20.0, "volume_usd": 100.0, "roi_pct": 20.0, "trade_count": 1, "raw": {}},
                        {"rank": 2, "display_name": "pending", "wallet": "0x" + "2" * 40, "pnl_usd": 10.0, "volume_usd": 100.0, "roi_pct": 10.0, "trade_count": 1, "raw": {}},
                    ],
                )
                row = next(store.iter_mdd_candidates({}, sort="roi_pct", direction="DESC", limit=1))
                store.set_mdd(row["id"], {"mdd_usd": 5.0, "mdd_pct": 10.0, "mdd_method": "test", "mdd_pct_basis": "test"})
            finally:
                store.close()

            stdout = io.StringIO()
            with patch("sys.stdout", stdout):
                exit_code = market_sentinel_cli.main(
                    [
                        "polymarket-leaderboard-export",
                        "--state-db",
                        str(state_path),
                        "--require-mdd",
                        "--max-mdd-pct",
                        "20",
                        "--format",
                        "json",
                    ]
                )

            payload = json.loads(stdout.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertTrue(payload["partial"])
            self.assertEqual(payload["counts"]["returned"], 1)
            self.assertEqual(payload["rows"][0]["display_name"], "eligible")
            self.assertEqual(payload["counts"]["mdd_pending"], 1)

    def test_polymarket_leaderboard_cli_checkpoints_and_resumes_pages(self) -> None:
        payload = {
            "rows": [],
            "counts": {"returned": 0, "filtered": 0, "scanned": 1, "mdd_computed": 0},
            "warnings": [],
        }
        checkpoint_row = {
            "rank": 1,
            "proxyWallet": "0x" + "1" * 40,
            "pnl": "10",
            "volume": "100",
        }

        with tempfile.TemporaryDirectory() as tmp:
            checkpoint = Path(tmp) / "leaderboard.checkpoint.jsonl"

            def fake_payload(_params, **kwargs):
                kwargs["leaderboard_page_callback"](0, 1, [checkpoint_row])
                return payload

            stdout = io.StringIO()
            with patch("market_sentinel_cli.polymarket_leaderboard_payload", side_effect=fake_payload), patch(
                "sys.stdout",
                stdout,
            ):
                exit_code = market_sentinel_cli.main(
                    [
                        "polymarket-leaderboard",
                        "--checkpoint",
                        str(checkpoint),
                        "--format",
                        "json",
                        "--quiet",
                    ]
                )

            self.assertEqual(exit_code, 0)
            self.assertIn('"type":"leaderboard_page"', checkpoint.read_text(encoding="utf-8"))

            stdout = io.StringIO()
            with patch("market_sentinel_cli.polymarket_leaderboard_payload", return_value=payload) as mock_payload, patch(
                "sys.stdout",
                stdout,
            ):
                exit_code = market_sentinel_cli.main(
                    [
                        "polymarket-leaderboard",
                        "--checkpoint",
                        str(checkpoint),
                        "--resume",
                        "--format",
                        "json",
                        "--quiet",
                    ]
                )

            self.assertEqual(exit_code, 0)
            called_params = mock_payload.call_args.args[0]
            self.assertEqual(called_params["scan_start_offset"], ["1"])
            self.assertEqual(mock_payload.call_args.kwargs["initial_raw_rows"], [checkpoint_row])
            self.assertTrue(callable(mock_payload.call_args.kwargs["leaderboard_page_callback"]))

    def test_polymarket_leaderboard_cli_state_db_streams_csv_and_resumes(self) -> None:
        raw_rows = [
            {"rank": 2, "proxyWallet": "0x" + "2" * 40, "pseudonym": "second", "pnl": "20", "volume": "200", "trades": 4},
            {"rank": 1, "proxyWallet": "0x" + "1" * 40, "pseudonym": "first", "pnl": "30", "volume": "100", "trades": 7},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            state_db = Path(tmp) / "leaderboard.sqlite3"
            output = Path(tmp) / "leaderboard.csv"

            def fake_scan(*_args, **kwargs):
                kwargs["page_callback"](0, 50, raw_rows)
                kwargs["page_callback"](2, 50, [])
                return [], False

            with patch("market_sentinel_cli._fetch_polymarket_leaderboard_scan_rows", side_effect=fake_scan) as mock_scan:
                exit_code = market_sentinel_cli.main(
                    [
                        "polymarket-leaderboard",
                        "--state-db",
                        str(state_db),
                        "--scanned",
                        "unlimited",
                        "--returned",
                        "unlimited",
                        "--format",
                        "csv",
                        "--output",
                        str(output),
                        "--quiet",
                    ]
                )

            self.assertEqual(exit_code, 0)
            self.assertTrue(state_db.exists())
            self.assertFalse(mock_scan.call_args.kwargs["retain_rows"])
            csv_lines = output.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(csv_lines), 3)
            self.assertIn("first", csv_lines[1])
            self.assertIn(",7,", csv_lines[1])

            with patch("market_sentinel_cli._fetch_polymarket_leaderboard_scan_rows") as mock_scan:
                exit_code = market_sentinel_cli.main(
                    [
                        "polymarket-leaderboard",
                        "--state-db",
                        str(state_db),
                        "--resume",
                        "--scanned",
                        "unlimited",
                        "--returned",
                        "unlimited",
                        "--format",
                        "csv",
                        "--output",
                        str(output),
                        "--quiet",
                    ]
                )

            self.assertEqual(exit_code, 0)
            mock_scan.assert_not_called()

    def test_polymarket_leaderboard_state_db_resumes_mdd_filtering(self) -> None:
        raw_rows = [
            {"rank": 1, "proxyWallet": "0x" + "1" * 40, "pnl": "30", "volume": "100"},
            {"rank": 2, "proxyWallet": "0x" + "2" * 40, "pnl": "20", "volume": "100"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            state_db = Path(tmp) / "leaderboard.sqlite3"
            output = Path(tmp) / "leaderboard.json"

            def fake_scan(*_args, **kwargs):
                kwargs["page_callback"](0, 50, raw_rows)
                kwargs["page_callback"](2, 50, [])
                return [], False

            def fake_mdd(wallet, **_kwargs):
                return {
                    "mdd_usd": 10.0,
                    "mdd_pct": 10.0 if wallet.endswith("1") else 25.0,
                    "mdd_method": "public_data_historical_equity_curve_v2",
                    "mdd_pct_basis": "public equity basis",
                    "points": [{"timestamp": 1, "value": 1.0}],
                }

            with patch("market_sentinel_cli._fetch_polymarket_leaderboard_scan_rows", side_effect=fake_scan), patch(
                "market_sentinel_cli.polymarket_user_mdd_payload", side_effect=fake_mdd
            ) as mock_mdd:
                exit_code = market_sentinel_cli.main(
                    [
                        "polymarket-leaderboard",
                        "--state-db",
                        str(state_db),
                        "--scanned",
                        "unlimited",
                        "--returned",
                        "unlimited",
                        "--compute-mdd",
                        "--mdd-scan",
                        "unlimited",
                        "--max-mdd-pct",
                        "20",
                        "--format",
                        "json",
                        "--output",
                        str(output),
                        "--quiet",
                    ]
                )

            self.assertEqual(exit_code, 0)
            self.assertEqual(mock_mdd.call_count, 2)
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(payload["counts"]["returned"], 1)
            self.assertEqual(payload["rows"][0]["mdd_pct"], 10.0)
            self.assertNotIn("points", payload["rows"][0])
            self.assertEqual(payload["completion_reason"], "end_of_results")
            self.assertTrue(payload["source_enumeration_complete"])

    def test_config_and_market_cli_update_persisted_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = str(Path(tmp) / "config.json")

            self.assertEqual(
                run_cli_silent(
                    [
                        "config",
                        "set",
                        "--config",
                        config_path,
                        "--theme",
                        "dark",
                        "--design",
                        "sentinel_2027",
                        "--compact",
                    ]
                ),
                0,
            )
            self.assertEqual(
                run_cli_silent(
                    [
                        "markets",
                        "set",
                        "polymarket",
                        "--config",
                        config_path,
                        "--enabled",
                        "--live-trading-enabled",
                        "--no-live-trading-kill-switch",
                        "--live-trading-max-size",
                        "5",
                        "--compact",
                    ]
                ),
                0,
            )

            cfg = load_config(Path(config_path))
            self.assertEqual(cfg.theme, "dark")
            self.assertEqual(cfg.ui_design, "sentinel_2027")
            self.assertTrue(cfg.markets["polymarket"].enabled)
            self.assertEqual(cfg.markets["polymarket"].settings["live_trading_max_size"], 5.0)

    def test_wallet_and_copy_cli_manage_persisted_state(self) -> None:
        wallet = "0x" + "1" * 40
        with tempfile.TemporaryDirectory() as tmp:
            config_path = str(Path(tmp) / "config.json")

            self.assertEqual(
                run_cli_silent(
                    [
                        "wallets",
                        "add",
                        "--config",
                        config_path,
                        "--wallet",
                        wallet,
                        "--display-name",
                        "leader",
                        "--compact",
                    ]
                ),
                0,
            )
            self.assertEqual(
                run_cli_silent(
                    [
                        "copy",
                        "set",
                        "--config",
                        config_path,
                        "--enabled",
                        "--follow-wallet",
                        wallet,
                        "--copy-percentage",
                        "25",
                        "--max-usdc-per-trade",
                        "10",
                        "--no-live",
                        "--compact",
                    ]
                ),
                0,
            )

            cfg = load_config(Path(config_path))
            self.assertEqual(cfg.wallets[0].wallet, wallet.lower())
            self.assertEqual(cfg.wallets[0].display_name, "leader")
            self.assertTrue(cfg.copytrading.enabled)
            self.assertEqual(cfg.copytrading.normalized_follow_wallets(), [wallet.lower()])
            self.assertEqual(cfg.copytrading.scale, 0.25)

    def test_paper_impact_cli_runs_without_gui(self) -> None:
        stdout = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, patch("sys.stdout", stdout):
            exit_code = market_sentinel_cli.main(
                [
                    "paper",
                    "impact",
                    "--config",
                    str(Path(tmp) / "config.json"),
                    "--market",
                    "polymarket",
                    "--contract",
                    "token-1",
                    "--side",
                    "BUY",
                    "--size",
                    "3",
                    "--limit-price",
                    "0.42",
                    "--compact",
                ]
            )

        self.assertEqual(exit_code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["impact"]["projected_net"], 3.0)
        self.assertEqual(payload["impact"]["order_notional"], 1.26)

    def test_wallet_watch_cli_runs_one_headless_poll(self) -> None:
        stdout = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, patch("sys.stdout", stdout):
            exit_code = market_sentinel_cli.main(
                [
                    "wallets",
                    "watch",
                    "--config",
                    str(Path(tmp) / "config.json"),
                    "--iterations",
                    "1",
                    "--interval",
                    "1",
                    "--compact",
                ]
            )

        self.assertEqual(exit_code, 0)
        payload = json.loads(stdout.getvalue().strip())
        self.assertEqual(payload["polled_wallets"], 0)
        self.assertEqual(payload["activity"], [])

    def test_dependency_cli_skips_inactive_markers_and_import_fallbacks(self) -> None:
        self.assertIsNone(market_sentinel_cli._parse_requirement_entry("tomli>=2.0.0; python_version < '0'"))
        with patch("market_sentinel_cli.importlib_metadata.version", side_effect=market_sentinel_cli.importlib_metadata.PackageNotFoundError):
            with patch("market_sentinel_cli.importlib.import_module") as mock_import:
                fake_module = type("FakeModule", (), {"__version__": "1.9.0"})()
                mock_import.return_value = fake_module
                self.assertEqual(market_sentinel_cli._installed_version("websocket-client"), "1.9.0")
                mock_import.assert_called_with("websocket")

    def test_full_app_cli_command_groups_are_registered(self) -> None:
        parser = market_sentinel_cli.build_parser()
        stdout = io.StringIO()
        with patch("sys.stdout", stdout):
            with self.assertRaises(SystemExit) as raised:
                parser.parse_args(["--help"])

        self.assertEqual(raised.exception.code, 0)
        help_text = stdout.getvalue()
        for command in ("config", "markets", "alerts", "wallets", "copy", "paper", "dependencies", "serve"):
            self.assertIn(command, help_text)


if __name__ == "__main__":
    unittest.main()
