from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import closing, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import market_sentinel_cli as cli
import web_api
from polymarket import data_api
from polymarket.http_client import PolymarketResponseError
from polymarket.leaderboard_state import LeaderboardStateStore
from polymarket.leaderboard_validation import PNL_ALIASES, VOLUME_ALIASES, WALLET_ALIASES, source_leaderboard_fields


WALLET = "0x" + "1" * 40
SECOND = "0x" + "2" * 40
ROW = {"rank": "1", "proxyWallet": WALLET, "pnl": -5, "vol": 100}
V2_ROW = {"rank": 1, "user_id": WALLET, "pnl": -5, "volume": 200}
STORED_ROW = {"rank": 1, "wallet": WALLET, "pnl_usd": -5, "volume_usd": 100, "roi_pct": -5}


def invalid_rows(version=1):
    valid = V2_ROW if version == 2 else ROW
    wallet_key, volume_key = ("user_id", "volume") if version == 2 else ("proxyWallet", "vol")
    yield {}
    for field in (wallet_key, "pnl", volume_key):
        yield {key: value for key, value in valid.items() if key != field}
    for value in (None, "", "bad", "0xabc", True, 123, [], {}):
        yield {**valid, wallet_key: value}
    for field in ("pnl", volume_key):
        for value in (None, "", "bad", True, False, "nan", "inf", "-inf", 10 ** 400, [], {}):
            yield {**valid, field: value}
    yield {**valid, volume_key: -1}
    yield {**valid, "rank": 0}
    yield {**valid, "rank": 1.5}
    if version == 1:
        yield {**valid, "pnlUsd": 500}
        yield {**valid, "volumeUsd": 500}
        yield {**valid, "wallet": SECOND}
        yield {**valid, "pnl": 1e308, "vol": 1e-308}
        yield {**valid, "mdd_pct": "nan"}


class LeaderboardSourceIntegrityTests(unittest.TestCase):
    def test_both_data_api_versions_reject_untrustworthy_dictionary_rows(self):
        for version in (1, 2):
            for row in invalid_rows(version):
                raw = [row] if version == 1 else {"data": [row], "pagination": {"has_more": False, "next_cursor": None}}
                with self.subTest(version=version, row=row), patch.object(data_api, "_get_json", return_value=raw):
                    with self.assertRaisesRegex(PolymarketResponseError, "coverage is unknown"):
                        (data_api.get_leaderboard if version == 1 else data_api.get_leaderboard_v2_page)()

    def test_error_envelopes_cannot_hide_behind_valid_empty_data(self):
        for version in (1, 2):
            for error in ({"error": "upstream failure"}, {"errors": ["failure"]}, {"status": "failed"},
                          {"status": "ERROR"}, {"status_code": 503}, {"code": "invalid_request"},
                          {"success": False}, {"success": 0}, {"status": False}, {"ok": "false"}):
                raw = {"data": [], "pagination": {"has_more": False, "next_cursor": None}, **error}
                with self.subTest(version=version, error=error), patch.object(data_api, "_get_json", return_value=raw):
                    with self.assertRaisesRegex(PolymarketResponseError, "coverage is unknown"):
                        (data_api.get_leaderboard if version == 1 else data_api.get_leaderboard_v2_page)()

    def test_success_metadata_and_empty_error_aliases_preserve_empty_page_semantics(self):
        for version in (1, 2):
            for metadata in ({"error": None}, {"error": "", "errors": []}, {"status": "success", "success": True},
                             {"status_code": 200, "ok": True}):
                raw = {"data": [], "pagination": {"has_more": False, "next_cursor": None}, **metadata}
                with self.subTest(version=version, metadata=metadata), patch.object(data_api, "_get_json", return_value=raw):
                    result = (data_api.get_leaderboard if version == 1 else data_api.get_leaderboard_v2_page)()
                self.assertEqual(result if version == 1 else result["data"], [])

    def test_established_aliases_nested_rows_zero_volume_and_numeric_strings_work(self):
        for wallet_key in WALLET_ALIASES:
            for pnl_key in PNL_ALIASES:
                for volume_key in VOLUME_ALIASES:
                    row = {"user": {wallet_key: " " + WALLET.upper() + " "}, "profile": {pnl_key: "-5.0", volume_key: "100"}}
                    with self.subTest(wallet=wallet_key, pnl=pnl_key, volume=volume_key):
                        result = web_api.normalize_polymarket_leaderboard_row(row, 5)
                        self.assertEqual((result["wallet"], result["pnl_usd"], result["volume_usd"], result["roi_pct"]),
                                         (WALLET, -5, 100, -5))
                        self.assertEqual(result["rank"], 5)
        zero = web_api.normalize_polymarket_leaderboard_row({**ROW, "vol": 0}, 1)
        self.assertIsNone(zero["roi_pct"])
        self.assertEqual(source_leaderboard_fields(V2_ROW, version=2)["volume"], 200)

    def test_api_pages_and_direct_normalization_fail_before_mdd_or_completion(self):
        for row in invalid_rows():
            with self.subTest(row=row):
                with self.assertRaises(PolymarketResponseError):
                    web_api.normalize_polymarket_leaderboard_row(row, 1)
                with patch.object(data_api, "get_leaderboard", return_value=[row]), patch.object(
                    web_api, "polymarket_user_mdd_payload"
                ) as mdd:
                    with self.assertRaises(PolymarketResponseError):
                        web_api.polymarket_leaderboard_payload({"max_mdd_pct": ["20"]})
                    mdd.assert_not_called()

    def test_invalid_later_page_preserves_committed_progress_and_previous_export(self):
        first = [{**ROW, "rank": index + 1, "proxyWallet": f"0x{index + 1:040x}"} for index in range(50)]
        with tempfile.TemporaryDirectory() as temporary:
            state, output = Path(temporary) / "scan.db", Path(temporary) / "result.json"
            output.write_text("previous completed export", encoding="utf-8")
            with patch.object(data_api, "_get_json", side_effect=[first, [{}]]), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                code = cli.main(["polymarket-leaderboard", "--state-db", str(state), "--scanned", "unlimited",
                                 "--returned", "unlimited", "--output", str(output), "--format", "json", "--quiet",
                                 "--scan-retry-attempts", "1"])
            self.assertEqual(code, 1)
            self.assertEqual(output.read_text(encoding="utf-8"), "previous completed export")
            with closing(LeaderboardStateStore(state, read_only=True)) as store:
                progress = store.progress()
                self.assertEqual((progress["rows"], progress["pages"], progress["next_offset"]), (50, 1, 50))
                self.assertFalse(progress["scan_complete"])

    def test_v2_invalid_export_preserves_previous_file_without_claiming_cursor_exhaustion(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "result.json"
            output.write_text("previous completed export", encoding="utf-8")
            with patch.object(data_api, "_get_json", return_value={"data": [{}], "pagination": {"has_more": False, "next_cursor": None}}), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                code = cli.main(["polymarket-leaderboard-v2", "--output", str(output)])
            self.assertEqual(code, 1)
            self.assertEqual(output.read_text(encoding="utf-8"), "previous completed export")

    def test_checkpoint_dictionary_integrity_is_required_before_resuming(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "checkpoint.jsonl"
            for rows in ([{}], [ROW, None], [{**ROW, "pnl": True}], [{**ROW, "proxyWallet": "bad"}]):
                content = json.dumps({"type": "leaderboard_scan", "version": 1, "signature": {}}) + "\n"
                content += json.dumps({"type": "leaderboard_page", "offset": 0, "limit": 50, "rows": rows}) + "\n"
                path.write_text(content, encoding="utf-8")
                with self.subTest(rows=rows), self.assertRaises(PolymarketResponseError):
                    cli._load_leaderboard_checkpoint(path, signature={})
                self.assertEqual(path.read_text(encoding="utf-8"), content)
            content = json.dumps({"type": "leaderboard_scan", "version": 1, "signature": {}}) + "\n"
            content += '{"type":"leaderboard_page","offset":0,"limit":50,"rows":['
            content += '{"proxyWallet":"' + WALLET + '","pnl":-5,"pnl":100,"vol":100}]}\n'
            path.write_text(content, encoding="utf-8")
            with self.assertRaises(ValueError):
                cli._load_leaderboard_checkpoint(path, signature={})

    def test_state_writer_rejects_invalid_rows_before_storing_page_or_risk(self):
        bad = [{}, {**STORED_ROW, "wallet": "bad"}, {**STORED_ROW, "pnl_usd": True},
               {**STORED_ROW, "pnl_usd": "nan"}, {**STORED_ROW, "volume_usd": -1},
               {**STORED_ROW, "volume_usd": "bad"}, {**STORED_ROW, "roi_pct": 500},
               {**STORED_ROW, "raw": {**ROW, "pnl": True}}, {**STORED_ROW, "raw": []}]
        for nested in ("user", "profile", "trader"):
            bad.extend([
                {**STORED_ROW, "raw": {nested: {"proxyWallet": SECOND, "pnl": -5, "vol": 100}}},
                {**STORED_ROW, "raw": {nested: {"proxyWallet": WALLET, "pnl": -50, "vol": 100}}},
                {**STORED_ROW, "raw": {nested: {"proxyWallet": WALLET, "pnl": -5, "vol": "nan"}}},
                {**STORED_ROW, "raw": {nested: {"pnl": -5, "vol": 100}}},
            ])
        with tempfile.TemporaryDirectory() as temporary, closing(LeaderboardStateStore(Path(temporary) / "state.db")) as store:
            store.prepare({}, resume=False)
            for row in bad:
                with self.subTest(row=row), self.assertRaises(PolymarketResponseError):
                    store.record_page(0, 1, [row])
                self.assertEqual(store.progress()["pages"], 0)
                self.assertEqual(store.result_count({"max_mdd_pct": 20}, require_mdd=True), 0)

    def test_matching_nested_source_binding_survives_durable_resume(self):
        raw = {"user": {"proxyWallet": WALLET}, "profile": {"pnl": "-5", "vol": "100"}}
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "state.db"
            with closing(LeaderboardStateStore(path)) as store:
                store.prepare({}, resume=False)
                store.record_page(0, 1, [{**STORED_ROW, "raw": raw}])
            with closing(LeaderboardStateStore(path, read_only=True)) as store:
                row = next(store.iter_results({}, require_mdd=False, sort="roi_pct", direction="DESC", limit=None))
                self.assertEqual(row["wallet"], WALLET)
                self.assertEqual(row["raw"], raw)

    def test_corrupt_legacy_state_fails_before_migration_export_or_resume(self):
        for assignment, value in (("wallet", "bad"), ("pnl_usd", None), ("volume_usd", float("inf")),
                                  ("roi_pct", 500), ("raw_json", json.dumps({**ROW, "pnl": True})),
                                  ("raw_json", json.dumps({"user": {"proxyWallet": SECOND},
                                                           "profile": {"pnl": -50, "vol": 100}}))):
            with self.subTest(assignment=assignment), tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary) / "state.db"
                with closing(LeaderboardStateStore(path)) as store:
                    store.prepare({}, resume=False)
                    store.record_page(0, 50, [STORED_ROW])
                    # These fixed SQL column names are the controlled corruption fixture.
                    store.connection.execute(f"UPDATE rows SET {assignment} = ?", (value,))  # noqa: S608
                    store.connection.commit()
                before = path.read_bytes()
                for read_only in (True, False):
                    with self.assertRaises(PolymarketResponseError):
                        with closing(LeaderboardStateStore(path, read_only=read_only)):
                            pass
                    self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
