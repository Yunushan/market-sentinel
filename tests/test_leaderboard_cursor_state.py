from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import tempfile
import unittest

import web_api
from polymarket.http_client import PolymarketResponseError
from polymarket.leaderboard_state import LeaderboardStateStore


def normalized(index):
    return web_api.normalize_polymarket_leaderboard_row(
        {"user_id": f"0x{index:040x}", "rank": index, "pnl": -5, "volume": 200}, index,
    )


def populate(path):
    with closing(LeaderboardStateStore(path)) as store:
        store.prepare({"source_api_version": 2}, resume=False)
        store.record_page(0, 100, [normalized(1)], source_version=2, next_cursor="fixture-page-one")
        store.record_page(1, 100, [normalized(2)], source_version=2,
                          source_cursor="fixture-page-one", next_cursor="fixture-page-two")


class LeaderboardCursorStateTests(unittest.TestCase):
    def test_native_rows_and_exact_cursor_survive_close_and_resume(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "state.db"
            populate(path)
            with closing(LeaderboardStateStore(path, read_only=True)) as store:
                progress = store.progress()
                self.assertEqual((progress["rows"], progress["pages"], progress["next_offset"]), (2, 2, 2))
                self.assertEqual(progress["next_cursor"], "fixture-page-two")
                self.assertEqual(progress["source_api_version"], 2)
                self.assertFalse(progress["scan_complete"])
                rows = list(store.iter_results({}, require_mdd=False, sort="pnl_usd", direction="DESC", limit=None))
                self.assertEqual([row["volume_shares"] for row in rows], [200, 200])
                self.assertTrue(all(row["source_api_version"] == 2 and row["volume_usd"] is None for row in rows))

    def test_saved_cursor_chain_and_metadata_corruption_fail_before_resume_or_export(self):
        corruptions = (
            ("UPDATE metadata SET value=? WHERE key='v2_next_cursor'", (json.dumps("fixture-corrupt"),)),
            ("UPDATE metadata SET value=? WHERE key='v2_next_page'", ("999",)),
            ("UPDATE metadata SET value=? WHERE key='source_api_version'", ("1",)),
            ("UPDATE pages SET source_cursor=? WHERE page_offset=1", ("fixture-corrupt",)),
            ("UPDATE pages SET next_cursor=? WHERE page_offset=0", ("fixture-corrupt",)),
            ("UPDATE pages SET next_cursor=? WHERE page_offset=1", ("fixture-page-one",)),
            ("UPDATE pages SET source_version=? WHERE page_offset=1", (1,)),
            ("UPDATE pages SET page_offset=? WHERE page_offset=1", (99,)),
            ("UPDATE metadata SET value=? WHERE key='scan_complete'", ("1",)),
            ("UPDATE pages SET row_count=? WHERE page_offset=0", (0,)),
            ("UPDATE rows SET page_offset=? WHERE page_offset=0", (99,)),
            ("UPDATE rows SET page_index=? WHERE page_offset=0", (99,)),
        )
        for sql, values in corruptions:
            with self.subTest(sql=sql), tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary) / "state.db"
                populate(path)
                with closing(LeaderboardStateStore(path)) as store:
                    store.connection.execute(sql, values)
                    store.connection.commit()
                for read_only in (True, False):
                    with self.subTest(read_only=read_only), self.assertRaises((ValueError, PolymarketResponseError)):
                        with closing(LeaderboardStateStore(path, read_only=read_only)):
                            pass

    def test_cursor_persistence_failure_rolls_back_rows_page_and_metadata_together(self):
        with tempfile.TemporaryDirectory() as temporary, closing(LeaderboardStateStore(Path(temporary) / "state.db")) as store:
            store.prepare({"source_api_version": 2}, resume=False)
            store.connection.execute(
                "CREATE TEMP TRIGGER reject_cursor BEFORE INSERT ON metadata WHEN NEW.key='v2_next_cursor' "
                "BEGIN SELECT RAISE(ABORT,'injected cursor persistence failure'); END"
            )
            with self.assertRaisesRegex(Exception, "injected cursor persistence failure"):
                store.record_page(0, 100, [normalized(1)], source_version=2, next_cursor="fixture-next")
            progress = store.progress()
            self.assertEqual((progress["rows"], progress["pages"], progress["next_offset"]), (0, 0, 0))
            self.assertIsNone(progress["next_cursor"])
            self.assertFalse(progress["scan_complete"])

    def test_exact_replay_is_idempotent_but_changed_cursor_or_ordinal_cannot_commit(self):
        with tempfile.TemporaryDirectory() as temporary, closing(LeaderboardStateStore(Path(temporary) / "state.db")) as store:
            store.prepare({"source_api_version": 2}, resume=False)
            row = normalized(1)
            self.assertTrue(store.record_page(0, 100, [row], source_version=2, next_cursor="fixture-next"))
            previous = store.progress()
            self.assertTrue(store.record_page(0, 100, [row], source_version=2, next_cursor="fixture-next"))
            for options in ({"offset": 0, "next_cursor": "fixture-changed"},
                            {"offset": 99, "source_cursor": "fixture-next", "next_cursor": "fixture-later"},
                            {"offset": 1, "source_cursor": "fixture-wrong", "next_cursor": "fixture-later"}):
                with self.subTest(options=options), self.assertRaises(ValueError):
                    store.record_page(limit=100, rows=[normalized(2)], source_version=2, **options)
                self.assertEqual(store.progress(), previous)

    def test_empty_page_with_cursor_is_durable_continuation_not_exhaustion(self):
        with tempfile.TemporaryDirectory() as temporary, closing(LeaderboardStateStore(Path(temporary) / "state.db")) as store:
            store.prepare({"source_api_version": 2}, resume=False)
            store.record_page(0, 100, [], source_version=2, next_cursor="fixture-after-empty")
            self.assertFalse(store.progress()["scan_complete"])
            store.record_page(1, 100, [normalized(1)], source_version=2,
                              source_cursor="fixture-after-empty", next_cursor=None)
            self.assertTrue(store.progress()["scan_complete"])
            self.assertEqual(store.progress()["stop_reason"], "end_of_results")


if __name__ == "__main__":
    unittest.main()
