from __future__ import annotations

import queue
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app import App
from core.models import MAX_WALLETS, AppConfig, CopyTradeSettings, WalletWatch
from core.storage import load_config, save_config


NEW_WALLET = "0x" + "f" * 40


class Value:
    def __init__(self, value: str):
        self.value = value

    def get(self) -> str:
        return self.value

    def set(self, value: str) -> None:
        self.value = value


def wallet_harness(count: int = MAX_WALLETS):
    wallets = [WalletWatch(id=f"watch-{index}", wallet=f"0x{index + 1:040x}") for index in range(count)]
    first = wallets[0].wallet
    cfg = AppConfig(wallets=wallets, copytrading=CopyTradeSettings(follow_wallet=first, follow_wallets=[first]))
    harness = SimpleNamespace(
        cfg=cfg, ui_queue=queue.Queue(), status_var=Value("Ready"), lb_status_var=Value("Ready"),
        ct_follow_var=Value(first), selected_wallet=NEW_WALLET,
        _refresh_wallet_table=Mock(),
    )
    harness._selected_wallet_id = lambda: harness.cfg.wallets[0].id
    harness._selected_leaderboard_wallet = lambda: harness.selected_wallet
    harness._selected_leaderboard_display_name = lambda: "Selected trader"
    harness._ensure_wallet_watch_from_leaderboard = lambda *args: App._ensure_wallet_watch_from_leaderboard(harness, *args)
    harness._copy_follow_wallets_from_text = lambda: App._copy_follow_wallets_from_text(harness)
    return harness


def add_wallet(harness):
    return App._add_wallet_watch(harness, harness.selected_wallet, "Selected trader")


def ensure_wallet(harness):
    return App._ensure_wallet_watch_from_leaderboard(harness, harness.selected_wallet, "Selected trader")


NEW_WATCH_ACTIONS = (add_wallet, ensure_wallet, App.track_selected_leaderboard_wallet,
                     App.follow_selected_leaderboard_for_copy_trading)


class DesktopWalletCapacityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.contexts = ExitStack()
        self.addCleanup(self.contexts.close)
        self.error = self.contexts.enter_context(patch("app.messagebox.showerror"))
        self.info = self.contexts.enter_context(patch("app.messagebox.showinfo"))

    def persist_initial(self, harness, name: str) -> Path:
        path = self.root / f"{name}.json"
        save_config(harness.cfg, path)
        self.assertEqual(load_config(path).to_dict(), harness.cfg.to_dict())
        return path

    def test_capacity_rejection_preserves_state_and_allows_retry_after_deleting_a_watch(self):
        for action in NEW_WATCH_ACTIONS:
            with self.subTest(action=action.__name__):
                harness = wallet_harness()
                # An uncommitted follow-form edit must also survive a blocked
                # operation without changing the durable copy policy.
                if action is App.follow_selected_leaderboard_for_copy_trading:
                    harness.ct_follow_var.set(harness.cfg.wallets[1].wallet)
                path = self.persist_initial(harness, action.__name__)
                before_bytes, before = path.read_bytes(), harness.cfg.to_dict()
                follow_before = harness.ct_follow_var.get()
                self.error.reset_mock()
                with patch("app.save_config", side_effect=lambda cfg, target=path: save_config(cfg, target)) as writer:
                    action(harness)
                    self.assertEqual(writer.call_count, 0)
                    self.assertEqual(harness.cfg.to_dict(), before)
                    self.assertEqual(path.read_bytes(), before_bytes)
                    self.assertEqual(harness.ct_follow_var.get(), follow_before)
                    self.assertFalse(getattr(harness, "_config_persistence_error", ""))
                    self.assertEqual(harness.status_var.get(), "Ready")
                    self.assertEqual(harness.lb_status_var.get(), "Ready")
                    self.assertTrue(harness.ui_queue.empty())
                    harness._refresh_wallet_table.assert_not_called()
                    self.error.assert_called_once()
                    self.assertNotIn("restart", str(self.error.call_args).lower())

                    App.delete_selected_wallet(harness)
                    self.assertEqual(writer.call_count, 1)
                    self.assertEqual(len(harness.cfg.wallets), MAX_WALLETS - 1)
                    harness.ui_queue.get_nowait()
                    harness._refresh_wallet_table.reset_mock()
                    self.error.reset_mock()
                    action(harness)
                    self.assertEqual(writer.call_count, 2)
                    self.assertEqual(len(harness.cfg.wallets), MAX_WALLETS)
                    self.assertEqual(sum(watch.wallet == NEW_WALLET for watch in harness.cfg.wallets), 1)
                    self.assertFalse(getattr(harness, "_config_persistence_error", ""))
                    self.assertEqual(load_config(path).to_dict(), harness.cfg.to_dict())
                    harness._refresh_wallet_table.assert_called_once()
                    self.assertFalse(harness.ui_queue.empty())
                    self.error.assert_not_called()
                    if action is App.follow_selected_leaderboard_for_copy_trading:
                        self.assertEqual(harness.cfg.copytrading.normalized_follow_wallets(), [follow_before, NEW_WALLET])
                        self.assertEqual(harness.ct_follow_var.get(), f"{follow_before}, {NEW_WALLET}")

    def test_existing_watches_can_be_reused_and_followed_at_capacity(self):
        harness = wallet_harness()
        harness.selected_wallet = harness.cfg.wallets[1].wallet
        path = self.persist_initial(harness, "existing")
        before = harness.cfg.to_dict()
        before_bytes = path.read_bytes()
        with patch("app.save_config", side_effect=lambda cfg: save_config(cfg, path)) as writer:
            for action in (add_wallet, ensure_wallet, App.track_selected_leaderboard_wallet):
                action(harness)
                self.assertEqual(writer.call_count, 0)
                self.assertEqual(harness.cfg.to_dict(), before)
                self.assertEqual(path.read_bytes(), before_bytes)
            self.assertIn("already tracked", harness.status_var.get())
            self.assertTrue(harness.ui_queue.empty())
            App.follow_selected_leaderboard_for_copy_trading(harness)
            self.assertEqual(writer.call_count, 1)
        self.assertEqual(len(harness.cfg.wallets), MAX_WALLETS)
        self.assertEqual([watch.to_dict() for watch in harness.cfg.wallets], before["wallets"])
        self.assertEqual(harness.cfg.copytrading.normalized_follow_wallets(),
                         [before["wallets"][0]["wallet"], harness.selected_wallet])
        self.assertEqual(load_config(path).to_dict(), harness.cfg.to_dict())
        self.assertFalse(getattr(harness, "_config_persistence_error", ""))
        self.error.assert_not_called()

    def test_storage_failure_still_pauses_and_preserves_watch_and_follow_policy(self):
        harness = wallet_harness(count=1)
        path = self.persist_initial(harness, "storage_failure")
        before = harness.cfg.to_dict()
        before_bytes = path.read_bytes()
        with patch("app.save_config", side_effect=lambda cfg: save_config(cfg, path)), patch(
            "core.storage.replace_file", side_effect=OSError("private disk detail"),
        ):
            App.follow_selected_leaderboard_for_copy_trading(harness)
        self.assertEqual(harness.cfg.to_dict(), before)
        self.assertEqual(path.read_bytes(), before_bytes)
        self.assertTrue(harness._config_persistence_error)
        self.assertTrue(harness.ui_queue.empty())
        harness._refresh_wallet_table.assert_not_called()
        self.assertNotIn("private disk detail", str(self.error.call_args))

    def test_stale_writer_still_pauses_without_replacing_newer_state(self):
        harness = wallet_harness(count=1)
        path = self.persist_initial(harness, "conflict")
        before = harness.cfg.to_dict()
        newer = load_config(path)
        newer.theme = "dark"
        save_config(newer, path)
        newer_bytes = path.read_bytes()
        with patch("app.save_config", side_effect=lambda cfg: save_config(cfg, path)):
            add_wallet(harness)
        self.assertEqual(harness.cfg.to_dict(), before)
        self.assertEqual(path.read_bytes(), newer_bytes)
        self.assertTrue(harness._config_persistence_error)
        self.assertTrue(harness.ui_queue.empty())
        harness._refresh_wallet_table.assert_not_called()

    def test_uncertain_committed_save_still_publishes_durable_state_and_pauses(self):
        harness = wallet_harness(count=1)
        path = self.persist_initial(harness, "postcommit")
        first_wallet = harness.cfg.wallets[0].wallet
        with patch("app.save_config", side_effect=lambda cfg: save_config(cfg, path)), patch(
            "core.storage._fsync_parent_directory", side_effect=OSError("directory sync failed"),
        ):
            App.follow_selected_leaderboard_for_copy_trading(harness)
        self.assertEqual(harness.cfg.to_dict(), load_config(path).to_dict())
        self.assertEqual(len(harness.cfg.wallets), 2)
        self.assertEqual(harness.cfg.copytrading.normalized_follow_wallets(), [first_wallet, NEW_WALLET])
        self.assertTrue(harness._config_persistence_error)
        self.assertTrue(harness.ui_queue.empty())
        harness._refresh_wallet_table.assert_not_called()
        self.assertIn("replaced", str(self.error.call_args).lower())
