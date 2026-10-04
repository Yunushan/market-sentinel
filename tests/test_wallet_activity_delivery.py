from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch
from core.models import AppConfig, CopyActivityOutboxEntry, WalletWatch
from app import WalletActivityTask, _apply_wallet_activity_checkpoint, _copy_activity_snapshot
from core.wallet_activity import (
    ActivityHistoryIncompleteError, ActivitySnapshot, activity_key,
    collect_adapter_activity, collect_polymarket_activity, collect_polymarket_activity_v2, cursor_for_market, remember_activity,
)
from web_api import poll_wallet_activity


def trade(timestamp: int, asset: str | None = None, tx: str | None = None) -> dict:
    return {"timestamp": timestamp, "transactionHash": tx or f"tx-{timestamp}",
            "asset": asset or f"asset-{timestamp}", "side": "BUY", "size": 1, "price": .4}


class WalletHistoryTests(unittest.TestCase):
    def test_backlog_larger_than_page_is_complete_before_cursor_moves(self) -> None:
        rows = [trade(ts) for ts in range(130, 100, -1)]
        calls: list[int] = []

        def loader(_wallet, **kwargs):
            calls.append(kwargs["offset"])
            return rows[kwargs["offset"]:kwargs["offset"] + kwargs["limit"]]

        items = collect_polymarket_activity(loader, "wallet", since=100, end=130)
        self.assertEqual([item["timestamp"] for item in reversed(items)], list(range(101, 131)))
        self.assertEqual(calls, [0, 25])

    def test_time_windows_backfill_beyond_offset_ceiling(self) -> None:
        rows = [trade(ts) for ts in range(130, 100, -1)]
        calls: list[tuple[int, int, int]] = []

        def loader(_wallet, **kwargs):
            calls.append((kwargs["start"], kwargs["end"], kwargs["offset"]))
            window = [row for row in rows if kwargs["start"] <= row["timestamp"] <= kwargs["end"]]
            return window[kwargs["offset"]:kwargs["offset"] + kwargs["limit"]]

        items = collect_polymarket_activity(loader, "wallet", since=100, end=130, page_size=5, offset_ceiling=5)
        self.assertEqual(len(items), 30)
        self.assertEqual(len({activity_key(row) for row in items}), 30)
        self.assertTrue(any(start > 100 or end < 130 for start, end, _ in calls))
        self.assertTrue(all(offset <= 5 for _, _, offset in calls))

    def test_partial_failure_returns_no_deliverable_batch(self) -> None:
        rows = [trade(ts) for ts in range(130, 100, -1)]

        def loader(_wallet, **kwargs):
            if kwargs["offset"]:
                raise OSError("upstream unavailable")
            return rows[:25]

        with self.assertRaisesRegex(OSError, "unavailable"):
            collect_polymarket_activity(loader, "wallet", since=100, end=130)

    def test_repeated_page_fails_closed(self) -> None:
        rows = [trade(ts) for ts in range(130, 105, -1)]
        with self.assertRaisesRegex(ActivityHistoryIncompleteError, "repeated|timestamp order"):
            collect_polymarket_activity(lambda *_args, **_kwargs: rows, "wallet", since=100, end=130)

    def test_inconsistent_cross_page_order_and_identical_fills_fail_closed(self) -> None:
        pages = [[trade(110), trade(109)], [trade(112), trade(111)], []]
        with self.assertRaisesRegex(ActivityHistoryIncompleteError, "timestamp order"):
            collect_polymarket_activity(lambda *_args, **_kwargs: pages.pop(0), "wallet", since=100, end=120, page_size=2)
        row = trade(110)
        with self.assertRaisesRegex(ActivityHistoryIncompleteError, "ambiguous duplicate"):
            collect_polymarket_activity(lambda *_args, **_kwargs: [row, dict(row)], "wallet", since=100, end=120)

    def test_overfull_second_is_explicit_failure(self) -> None:
        rows = [trade(100, asset=f"asset-{index}") for index in range(15)]

        def loader(_wallet, **kwargs):
            return rows[kwargs["offset"]:kwargs["offset"] + kwargs["limit"]]

        with self.assertRaisesRegex(ActivityHistoryIncompleteError, "one second"):
            collect_polymarket_activity(loader, "wallet", since=100, end=100, page_size=5, offset_ceiling=5)

    def test_two_fills_in_one_transaction_have_distinct_identity(self) -> None:
        first = trade(100, "yes", "0xABC")
        second = trade(100, "no", "0xABC")
        self.assertNotEqual(activity_key(first), activity_key(second))
        self.assertEqual(activity_key(first), activity_key(dict(first)))
        self.assertNotEqual(activity_key({**first, "activityId": "fill-1"}), activity_key({**first, "activityId": "fill-2"}))
        self.assertEqual(activity_key(first), activity_key({**first, "price": "0.4000", "size": "1.0"}))
        self.assertNotEqual(activity_key({**first, "activityId": "Abc"}), activity_key({**first, "activityId": "abc"}))
        for field in ("logIndex", "log_index", "fillId", "trade_id"):
            self.assertEqual(activity_key({**first, field: 0}), activity_key(_copy_activity_snapshot({**first, field: 0})))

    def test_same_second_identity_ledger_survives_more_than_200_and_reload(self) -> None:
        watch = WalletWatch(wallet="wallet")
        rows = [trade(100, f"asset-{index}") for index in range(300)]
        for row in rows:
            remember_activity(watch, row, activity_key(row))
        loaded = WalletWatch.from_dict(watch.to_dict())
        self.assertEqual(len(loaded.seen_activity_keys), 300)
        self.assertEqual(set(loaded.seen_activity_keys), {activity_key(row) for row in rows})
        newer = trade(101)
        remember_activity(loaded, newer, activity_key(newer))
        self.assertEqual(loaded.seen_activity_keys, [activity_key(newer)])

    def test_filtered_short_recent_feed_does_not_claim_exhaustion(self) -> None:
        calls: list[int] = []

        def loader(_wallet, *, limit):
            calls.append(limit)
            return ActivitySnapshot([trade(130)], history_complete=False)

        with self.assertRaisesRegex(ActivityHistoryIncompleteError, "does not cover"):
            collect_adapter_activity(loader, "wallet", since=100)
        self.assertEqual(calls, [25, 50])

    def test_recent_feed_expands_to_saved_cursor(self) -> None:
        rows = [trade(ts) for ts in range(130, 89, -1)]

        def loader(_wallet, *, limit):
            return ActivitySnapshot(rows[:limit], history_complete=False)

        self.assertEqual(len(collect_adapter_activity(loader, "wallet", since=100)), 41)

    def test_market_switch_has_independent_cursor_and_roundtrip_restores_it(self) -> None:
        watch = WalletWatch(wallet="wallet")
        old = trade(200)
        remember_activity(watch, old, activity_key(old), market_id="polymarket")
        other = trade(100, "other-asset")
        remember_activity(watch, other, activity_key(other), market_id="kalshi")
        self.assertEqual(watch.last_seen_ts, 100)
        self.assertEqual(cursor_for_market(watch, "polymarket")["last_seen_ts"], 200)
        loaded = WalletWatch.from_dict(watch.to_dict())
        remember_activity(loaded, old, activity_key(old), market_id="polymarket")
        self.assertEqual(loaded.last_seen_ts, 200)
        self.assertEqual(loaded.seen_activity_keys, [activity_key(old)])
        self.assertEqual(cursor_for_market(loaded, "kalshi")["last_seen_ts"], 100)

    def test_legacy_cursor_migrates_to_its_saved_selected_market(self) -> None:
        cfg = AppConfig.from_dict({"selected_market_id": "kalshi", "wallets": [{
            "wallet": "0x" + "a" * 40, "last_seen_ts": 100, "seen_activity_keys": ["tx:old"]}]})
        self.assertEqual(cursor_for_market(cfg.wallets[0], "kalshi")["last_seen_ts"], 100)
        self.assertEqual(cursor_for_market(cfg.wallets[0], "polymarket")["last_seen_ts"], 0)

    def test_api_poll_delivers_backlog_once_and_keeps_cursor_on_partial_failure(self) -> None:
        cfg = AppConfig(wallets=[WalletWatch(wallet="0x" + "a" * 40, last_seen_ts=100)])
        rows = [trade(ts) for ts in range(130, 100, -1)]
        registry = SimpleNamespace(create=lambda *_args: SimpleNamespace(display_name="Polymarket"))
        recent: list[dict] = []

        def loader(_wallet, **kwargs):
            eligible = [row for row in rows if kwargs["start"] <= row["timestamp"] <= kwargs["end"]]
            offset = int(kwargs["cursor"] or 0)
            page = eligible[offset:offset + kwargs["limit"]]
            next_cursor = str(offset + len(page)) if offset + len(page) < len(eligible) else None
            return {"data": page, "pagination": {"next_cursor": next_cursor}}

        with patch("web_api.data_api.get_activity_page_v2", side_effect=loader):
            first = poll_wallet_activity(cfg, registry, recent)
            second = poll_wallet_activity(cfg, registry, recent)
        self.assertEqual(first["problems"], [])
        self.assertEqual(len(first["activity"]), 30)
        self.assertEqual(second["activity"], [])
        self.assertEqual(cfg.wallets[0].last_seen_ts, 130)
        cfg.wallets[0].last_seen_ts = 100
        cfg.wallets[0].seen_activity_keys = []
        cfg.wallets[0].activity_key_timestamps = {}
        before = cfg.to_dict()

        def fails_after_first(_wallet, **kwargs):
            if kwargs["cursor"]:
                raise OSError("interrupted second page")
            return {"data": rows[:25], "pagination": {"next_cursor": "second"}}

        with patch("web_api.data_api.get_activity_page_v2", side_effect=fails_after_first):
            result = poll_wallet_activity(cfg, registry, [])
        self.assertEqual(result["activity"], [])
        self.assertIn("interrupted second page", result["problems"][0])
        self.assertEqual(cfg.to_dict(), before)

    def test_legacy_hash_collision_requires_reconciliation_without_cursor_mutation(self) -> None:
        wallet = WalletWatch(wallet="0x" + "a" * 40, last_seen_ts=100, seen_activity_keys=["tx:0xabc"])
        cfg = AppConfig(wallets=[wallet])
        registry = SimpleNamespace(create=lambda *_args: SimpleNamespace(display_name="Polymarket"))
        rows = [trade(100, "new-asset", "0xABC")]
        before = cfg.to_dict()
        with patch("web_api.data_api.get_activity_page_v2", return_value={"data": rows, "pagination": {"next_cursor": None}}):
            result = poll_wallet_activity(cfg, registry, [])
        self.assertEqual(result["activity"], [])
        self.assertIn("reconciliation", result["problems"][0])
        self.assertEqual(cfg.to_dict(), before)

    def test_desktop_capacity_failure_restores_pruned_outbox_and_cursor(self) -> None:
        watch = WalletWatch(wallet="wallet", last_seen_ts=100,
                            seen_activity_keys=[f"unknown-{index}" for index in range(10_000)])
        cfg = AppConfig(wallets=[watch], copy_activity_outbox=[CopyActivityOutboxEntry(
            watch_id=watch.id, activity_key=f"old-{index}", activity={"timestamp": 1}, state="completed")
            for index in range(500)])
        row = trade(101)
        before = cfg.to_dict()
        task = WalletActivityTask(watch.id, row, activity_key(row), "polymarket")
        with self.assertRaisesRegex(ActivityHistoryIncompleteError, "ledger is full"):
            _apply_wallet_activity_checkpoint(cfg, task)
        self.assertEqual(cfg.to_dict(), before)

    def test_desktop_snapshot_failure_restores_market_cursor_switch(self) -> None:
        watch = WalletWatch(wallet="wallet", last_seen_ts=200)
        cfg = AppConfig(wallets=[watch])
        row = trade(100)
        task = WalletActivityTask(watch.id, row, activity_key(row), "kalshi")
        before = cfg.to_dict()
        with patch("app._copy_activity_snapshot", side_effect=ValueError("invalid snapshot")):
            with self.assertRaisesRegex(ValueError, "invalid snapshot"):
                _apply_wallet_activity_checkpoint(cfg, task)
        self.assertEqual(cfg.to_dict(), before)

    def test_equal_activity_id_in_another_market_is_not_discarded(self) -> None:
        watch = WalletWatch(wallet="wallet", last_seen_ts=100, seen_activity_keys=["activity-id:shared"])
        cfg = AppConfig(wallets=[watch])
        row = {**trade(100), "activityId": "shared"}
        checkpoint = _apply_wallet_activity_checkpoint(cfg, WalletActivityTask(watch.id, row, activity_key(row), "opinion_labs"))
        self.assertIsNotNone(checkpoint)
        self.assertEqual(len(cfg.copy_activity_outbox), 1)
        self.assertEqual(cfg.copy_activity_outbox[0].market_id, "opinion_labs")

    def test_v2_short_and_empty_pages_continue_until_explicit_eof(self) -> None:
        pages = {
            None: {"data": [trade(130)], "pagination": {"next_cursor": "a", "has_more": True}},
            "a": {"data": [], "pagination": {"next_cursor": "b", "has_more": True}},
            "b": {"data": [trade(101)], "pagination": {"next_cursor": None, "has_more": False}},
        }
        queries = []

        def loader(_wallet, **kwargs):
            queries.append(dict(kwargs))
            return pages[kwargs["cursor"]]

        rows = collect_polymarket_activity_v2(loader, "wallet", since=100, end=130, page_size=25)
        self.assertEqual([row["timestamp"] for row in rows], [130, 101])
        self.assertEqual([query.pop("cursor") for query in queries], [None, "a", "b"])
        for query in queries:
            query.pop("timeout")
        self.assertEqual(queries, [queries[0]] * 3)

    def test_v2_repeated_cursor_and_missing_metadata_fail_closed(self) -> None:
        for payload, message in (({"data": [], "pagination": {}}, "incomplete"),
                                 ({"data": [], "pagination": {"next_cursor": "same"}}, "repeated"),
                                 ({"data": [], "pagination": {"next_cursor": None, "has_more": True}}, "contradictory")):
            with self.subTest(payload=payload):
                with self.assertRaisesRegex(ActivityHistoryIncompleteError, message):
                    collect_polymarket_activity_v2(lambda *_args, _payload=payload, **_kwargs: _payload, "wallet", since=100, end=130)

    def test_v2_initial_history_uses_positive_epoch_not_provider_default_three_years(self) -> None:
        calls = []

        def loader(_wallet, **kwargs):
            calls.append(kwargs)
            return {"data": [], "pagination": {"next_cursor": None}}

        collect_polymarket_activity_v2(loader, "wallet", since=0, end=130)
        self.assertEqual(calls[0]["start"], 1)

    def test_filtered_wallet_events_are_consumed_without_copy_or_display(self) -> None:
        watch = WalletWatch(wallet="0x" + "a" * 40, only_market_slug="wanted")
        cfg = AppConfig(wallets=[watch])
        registry = SimpleNamespace(create=lambda *_args: SimpleNamespace(display_name="Polymarket"))
        row = {**trade(100), "slug": "other"}
        with patch("web_api.data_api.get_activity_page_v2", return_value={"data": [row], "pagination": {"next_cursor": None}}):
            result = poll_wallet_activity(cfg, registry, [])
        self.assertEqual(result["activity"], [])
        self.assertEqual(watch.last_seen_ts, 100)
        from app import App
        harness = SimpleNamespace(cfg=cfg)
        outcome = App._handle_wallet_activity(harness, watch.id, row)
        self.assertEqual(outcome.code, "market_filter_skipped")


if __name__ == "__main__":
    unittest.main()
