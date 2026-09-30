from __future__ import annotations

import queue
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from tkinter import TclError
from unittest.mock import Mock, patch

import test_app_logic as helpers
from app import App, CopyActivityOutcome, WalletActivityTask
from core.models import AppConfig, MAX_ALERTS, PaperTradeRecord, PriceAlert, WalletWatch
from core.storage import load_config, save_config


class DesktopConfigPersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "config.json"
        self.contexts = ExitStack()
        self.addCleanup(self.contexts.close)
        self.error = self.contexts.enter_context(patch("app.messagebox.showerror"))
        self.warning = self.contexts.enter_context(patch("app.messagebox.showwarning"))

    def bind_store(self, harness) -> None:
        save_config(harness.cfg, self.path)
        self.writer = self.contexts.enter_context(patch("app.save_config", side_effect=lambda cfg: save_config(cfg, self.path)))

    @staticmethod
    def alert_creation_harness():
        registry = Mock()
        registry.create.return_value = SimpleNamespace(capabilities=SimpleNamespace(alerts=True))
        return SimpleNamespace(
            cfg=AppConfig(), adapter_registry=registry,
            _selected_token_id="token", _selected_alert_market_id="polymarket",
            alert_label_entry=helpers.FakeEntry("Price crossing"),
            alert_threshold_entry=helpers.FakeEntry("0.5"),
            alert_dir_var=helpers.FakeVar("above"), alert_src_var=helpers.FakeVar("last_trade"),
            alert_once_var=helpers.FakeVar(True), market_ws=SimpleNamespace(subscribe=Mock()),
            _refresh_alert_table=Mock(), status_var=helpers.FakeVar(), ui_queue=queue.Queue(),
        )

    def test_invalid_alert_input_preserves_storage_and_allows_corrected_add_without_restart(self) -> None:
        invalid_inputs = (
            ("label", "x" * 513), ("token", "x" * 257),
            ("label", "Price\tcrossing"), ("token", "token\nsecond"),
            ("direction", "sideways"), ("source", "unknown"),
        )
        for index, (field, value) in enumerate(invalid_inputs):
            with self.subTest(field=field, value=value):
                self.path = Path(self.directory.name) / f"invalid-alert-{index}.json"
                harness = self.alert_creation_harness()
                if field == "token":
                    harness._selected_token_id = value
                else:
                    {
                        "label": harness.alert_label_entry,
                        "direction": harness.alert_dir_var,
                        "source": harness.alert_src_var,
                    }[field].set(value)
                self.bind_store(harness)
                before = self.path.read_bytes()
                original = harness.cfg.to_dict()
                self.error.reset_mock()
                App.add_alert(harness)
                self.writer.assert_not_called()
                self.assertEqual(harness.cfg.to_dict(), original)
                self.assertEqual(self.path.read_bytes(), before)
                self.assertFalse(getattr(harness, "_config_persistence_error", ""))
                self.error.assert_called_once()
                self.assertEqual(self.error.call_args.args[0], "Invalid alert")
                harness.market_ws.subscribe.assert_not_called()
                harness._refresh_alert_table.assert_not_called()
                self.assertTrue(harness.ui_queue.empty())

                harness._selected_token_id = "corrected-token"
                harness.alert_label_entry.set("Corrected crossing")
                harness.alert_dir_var.set("above")
                harness.alert_src_var.set("last_trade")
                self.error.reset_mock()
                App.add_alert(harness)
                self.writer.assert_called_once()
                self.error.assert_not_called()
                self.assertFalse(getattr(harness, "_config_persistence_error", ""))
                self.assertEqual(len(harness.cfg.alerts), 1)
                self.assertEqual(harness.cfg.to_dict(), load_config(self.path).to_dict())
                harness.market_ws.subscribe.assert_called_once_with(["corrected-token"])
                harness._refresh_alert_table.assert_called_once()
                self.assertEqual(harness.status_var.get(), "Alert added.")

    def test_alert_text_and_probability_boundaries_persist_without_truncation(self) -> None:
        for threshold in ("0", "1"):
            with self.subTest(threshold=threshold):
                self.path = Path(self.directory.name) / f"boundary-alert-{threshold}.json"
                harness = self.alert_creation_harness()
                harness._selected_token_id = "t" * 256
                harness.alert_label_entry.set("x" * 512)
                harness.alert_threshold_entry.set(threshold)
                self.bind_store(harness)
                App.add_alert(harness)
                self.writer.assert_called_once()
                self.error.assert_not_called()
                saved = load_config(self.path).alerts[0]
                self.assertEqual(saved.token_id, "t" * 256)
                self.assertEqual(saved.label, "x" * 512)
                self.assertEqual(saved.threshold, float(threshold))
                self.assertFalse(getattr(harness, "_config_persistence_error", ""))

    def test_alert_capacity_rejection_preserves_a_valid_store_without_pausing(self) -> None:
        harness = self.alert_creation_harness()
        harness.cfg.alerts = [
            PriceAlert(id=f"alert-{index}", token_id=f"token-{index}", label="Existing",
                       direction="above", threshold=0.5)
            for index in range(MAX_ALERTS)
        ]
        self.bind_store(harness)
        before = self.path.read_bytes()
        original = harness.cfg.to_dict()
        App.add_alert(harness)
        self.writer.assert_not_called()
        self.assertEqual(harness.cfg.to_dict(), original)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse(getattr(harness, "_config_persistence_error", ""))
        self.error.assert_called_once()
        self.assertEqual(self.error.call_args.args[0], "Alert limit reached")
        harness.market_ws.subscribe.assert_not_called()
        harness._refresh_alert_table.assert_not_called()
        self.assertTrue(harness.ui_queue.empty())

    @staticmethod
    def armed_copy_harness():
        harness = helpers.AppLogicTests.copy_settings_harness()
        harness.ct_live_var.set(True)
        harness.status_var = helpers.FakeVar()
        return harness

    @staticmethod
    def armed_market_harness():
        harness = helpers.SafetyHarness()
        harness.cfg.markets["kalshi"].settings["live_trading_kill_switch"] = True
        harness.safety_live_enabled_var.set(True)
        harness.safety_live_confirmed_var.set(True)
        harness.safety_kill_switch_var.set(False)
        return harness

    def test_copy_precommit_failure_keeps_old_policy_and_blocks_later_writes(self) -> None:
        harness = self.armed_copy_harness()
        self.bind_store(harness)
        original = harness.cfg.copytrading
        before = self.path.read_bytes()
        with patch("core.storage.replace_file", side_effect=PermissionError("private disk detail")):
            App.save_copy_settings(harness)
        self.assertIs(harness.cfg.copytrading, original)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse(harness.ct_live_var.get())
        self.assertTrue(harness._config_persistence_error)
        self.warning.assert_not_called()
        self.error.assert_called_once()
        self.assertNotIn("private disk detail", str(self.error.call_args))
        self.assertTrue(harness.ui_queue.empty())
        self.writer.reset_mock()
        App.save_copy_settings(harness)
        self.writer.assert_not_called()
        self.assertEqual(self.path.read_bytes(), before)

    def test_market_precommit_failure_keeps_kill_switch_and_cache(self) -> None:
        harness = self.armed_market_harness()
        harness.polymarket_adapter = object()
        cached = harness.polymarket_adapter
        self.bind_store(harness)
        before = self.path.read_bytes()
        original = harness.cfg.markets["kalshi"]
        with patch("core.storage.replace_file", side_effect=OSError("disk full")):
            App.save_market_safety_settings(harness)
        self.assertIs(harness.cfg.markets["kalshi"], original)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertTrue(original.settings["live_trading_kill_switch"])
        self.assertFalse(harness.safety_live_enabled_var.get())
        self.assertTrue(harness.safety_kill_switch_var.get())
        self.assertIs(harness.polymarket_adapter, cached)
        self.assertTrue(harness._config_persistence_error)

    def test_invalid_caps_are_input_errors_without_a_persistence_pause(self) -> None:
        for value in ("NaN", "Infinity", "-Infinity", "0", "-1", True, 0):
            with self.subTest(value=value):
                harness = self.armed_market_harness()
                harness.safety_max_size_var.set(value)
                before = harness.cfg.to_dict()
                with patch("app.save_config") as writer:
                    App.save_market_safety_settings(harness)
                writer.assert_not_called()
                self.assertEqual(harness.cfg.to_dict(), before)
                self.assertFalse(getattr(harness, "_config_persistence_error", ""))

    def test_stale_writer_does_not_publish_or_overwrite_newer_state(self) -> None:
        harness = self.armed_copy_harness()
        self.bind_store(harness)
        newer = load_config(self.path)
        newer.theme = "dark"
        save_config(newer, self.path)
        before = self.path.read_bytes()
        App.save_copy_settings(harness)
        self.assertFalse(harness.cfg.copytrading.live)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertTrue(harness._config_persistence_error)
        self.assertIn("restart", str(self.error.call_args).lower())

    def test_postcommit_failure_publishes_committed_snapshot_but_pauses_execution(self) -> None:
        harness = self.armed_copy_harness()
        self.bind_store(harness)
        root = harness.cfg
        journal = root.copy_activity_outbox
        with patch("core.storage._fsync_parent_directory", side_effect=OSError("directory sync failed")):
            App.save_copy_settings(harness)
        self.assertIs(harness.cfg, root)
        self.assertIs(root.copy_activity_outbox, journal)
        self.assertTrue(root.copytrading.live)
        self.assertEqual(root.to_dict(), load_config(self.path).to_dict())
        self.assertTrue(harness._config_persistence_error)
        self.assertIn("replaced", str(self.error.call_args).lower())
        self.warning.assert_not_called()
        result = App._copy_trade_from_activity(harness, {})
        self.assertEqual(result.code, "config_persistence_paused")
        self.writer.reset_mock()
        App.save_copy_settings(harness)
        self.writer.assert_not_called()

    def test_success_publishes_only_after_save_and_preserves_worker_references(self) -> None:
        harness = self.armed_copy_harness()
        self.bind_store(harness)
        root = harness.cfg
        worker = SimpleNamespace(cfg=root)
        original = root.copytrading
        journal = root.copy_activity_outbox

        def observe(candidate):
            self.assertIsNot(candidate, root)
            self.assertIs(root.copytrading, original)
            self.assertFalse(worker.cfg.copytrading.live)
            self.assertTrue(candidate.copytrading.live)
            save_config(candidate, self.path)

        self.writer.side_effect = observe
        App.save_copy_settings(harness)
        self.assertIs(harness.cfg, root)
        self.assertIs(worker.cfg, root)
        self.assertIs(root.copy_activity_outbox, journal)
        self.assertTrue(worker.cfg.copytrading.live)
        self.assertEqual(root.to_dict(), load_config(self.path).to_dict())
        self.warning.assert_called_once()
        self.error.assert_not_called()
        root.theme = "dark"
        save_config(root, self.path)
        self.assertEqual(load_config(self.path).theme, "dark")

    def test_destroyed_status_widget_cannot_hide_a_committed_save_error(self) -> None:
        harness = self.armed_copy_harness()
        harness.status_var = SimpleNamespace(set=Mock(side_effect=TclError("widget destroyed")))
        self.bind_store(harness)
        with patch("core.storage._fsync_parent_directory", side_effect=OSError("directory sync failed")):
            App.save_copy_settings(harness)
        self.assertEqual(harness.cfg.to_dict(), load_config(self.path).to_dict())
        self.assertTrue(harness.cfg.copytrading.live)
        self.assertTrue(harness._config_persistence_error)

    def test_market_success_invalidates_adapter_only_after_commit(self) -> None:
        harness = self.armed_market_harness()
        harness.polymarket_adapter = object()
        cached = harness.polymarket_adapter
        self.bind_store(harness)
        original = harness.cfg.markets

        def observe(candidate):
            self.assertIs(harness.cfg.markets, original)
            self.assertIs(harness.polymarket_adapter, cached)
            self.assertTrue(original["kalshi"].settings["live_trading_kill_switch"])
            save_config(candidate, self.path)

        self.writer.side_effect = observe
        App.save_market_safety_settings(harness)
        self.assertIsNone(harness.polymarket_adapter)
        self.assertFalse(harness.cfg.markets["kalshi"].settings["live_trading_kill_switch"])

    def test_failed_market_selection_restores_selection(self) -> None:
        harness = helpers.MarketSelectionHarness()
        self.bind_store(harness)
        original = harness.cfg.selected_market_id
        harness.market_var.set("Kalshi (kalshi)")
        with patch("core.storage.replace_file", side_effect=OSError("disk full")):
            App._on_market_change(harness)
        self.assertEqual(harness.cfg.selected_market_id, original)
        self.assertEqual(harness.market_var.get(), harness._market_label_for_id(original))
        self.assertTrue(harness.ui_queue.empty())

    def test_failed_follow_does_not_partially_add_watch_or_follow(self) -> None:
        harness = helpers.AnalyticsHarness()
        harness._selected_leaderboard_wallet = lambda: helpers.WALLET
        harness._selected_leaderboard_display_name = lambda: "Trader"
        self.bind_store(harness)
        before = self.path.read_bytes()
        with patch("core.storage.replace_file", side_effect=OSError("disk full")):
            App.follow_selected_leaderboard_for_copy_trading(harness)
        self.assertEqual(harness.cfg.wallets, [])
        self.assertEqual(harness.cfg.copytrading.normalized_follow_wallets(), [])
        self.assertEqual(harness.ct_follow_var.get(), "")
        self.assertEqual(self.path.read_bytes(), before)
        self.assertTrue(harness.ui_queue.empty())
        self.assertNotIn("added to copy", harness.status_var.get().lower())

    def test_failed_wallet_alert_and_history_edits_do_not_mutate_shared_collections(self) -> None:
        actions = (
            App.toggle_selected_wallet, App.delete_selected_wallet,
            App.toggle_selected_alert, App.delete_selected_alert, App.clear_paper_history,
            lambda h: App._add_wallet_watch(h, "0x" + "a" * 40, "new watch"),
        )
        for action in actions:
            with self.subTest(action=action.__name__):
                harness = SimpleNamespace(
                    cfg=AppConfig(
                        wallets=[WalletWatch(id="watch", wallet=helpers.WALLET)],
                        alerts=[PriceAlert(id="alert", token_id="token", label="test", direction="above", threshold=0.5)],
                        paper_trades=[PaperTradeRecord(
                            market_id="polymarket", contract_id="token", side="BUY", size=1,
                            limit_price=0.5, accepted=True, message="paper test",
                        )],
                    ),
                    ui_queue=queue.Queue(), status_var=helpers.FakeVar(),
                    _selected_wallet_id=lambda: "watch", _selected_alert_id=lambda: "alert",
                    market_ws=SimpleNamespace(subscribe=Mock()),
                )
                before = harness.cfg.to_dict()
                with patch("app.save_config", side_effect=OSError("disk full")), patch("app.messagebox.askyesno", return_value=True):
                    action(harness)
                self.assertEqual(harness.cfg.to_dict(), before)
                self.assertTrue(harness._config_persistence_error)
                self.assertTrue(harness.ui_queue.empty())
                harness.market_ws.subscribe.assert_not_called()

    def test_paused_alerts_do_not_fire_or_change_durable_crossing_state(self) -> None:
        alert = PriceAlert(token_id="token", label="test", direction="above", threshold=0.5)
        harness = helpers.AlertHarness(alert, 0.8)
        harness._config_persistence_error = "Restart required"
        before = harness.cfg.to_dict()
        with patch("app.save_config") as writer:
            App._eval_alerts_for_contract(harness, "polymarket", "token")
        self.assertEqual(harness.cfg.to_dict(), before)
        self.assertEqual(harness.fired, [])
        writer.assert_not_called()

    def test_alert_popup_follows_complete_durable_notification_commit(self) -> None:
        alert = PriceAlert(token_id="token", label="test", direction="above", threshold=0.5)
        harness = helpers.AlertHarness(alert, 0.8)
        self.bind_store(harness)

        def observe(candidate):
            self.assertEqual(harness.fired, [])
            self.assertTrue(harness.cfg.alerts[0].enabled)
            self.assertEqual(harness.cfg.alert_events, [])
            self.assertEqual(len(candidate.alert_events), 1)
            save_config(candidate, self.path)

        self.writer.side_effect = observe
        App._eval_alerts_for_contract(harness, "polymarket", "token")
        self.assertEqual(harness.fired, [(alert.id, 0.8)])
        self.assertEqual(harness.cfg.to_dict(), load_config(self.path).to_dict())
        self.assertEqual(len(harness.cfg.alert_events), 1)

    def test_failed_alert_commit_preserves_trigger_and_suppresses_popup(self) -> None:
        alert = PriceAlert(token_id="token", label="test", direction="above", threshold=0.5)
        harness = helpers.AlertHarness(alert, 0.8)
        self.bind_store(harness)
        before = harness.cfg.to_dict()
        with patch("core.storage.replace_file", side_effect=OSError("disk unavailable")):
            App._eval_alerts_for_contract(harness, "polymarket", "token")
        self.assertEqual(harness.fired, [])
        self.assertEqual(harness.cfg.to_dict(), before)
        self.assertEqual(load_config(self.path).to_dict(), before)
        self.assertTrue(harness._config_persistence_error)

    def test_invalid_desktop_quotes_never_coerce_or_consume_a_crossing(self) -> None:
        for source in ("last_trade", "midpoint", "best_bid", "best_ask"):
            for value in (True, False, float("nan"), -1, 2, "bad"):
                with self.subTest(source=source, value=value):
                    alert = PriceAlert(token_id="token", label="test", direction="above", threshold=0.5, source=source)
                    harness = helpers.AlertHarness(alert, 0.25)
                    harness.ui_queue = queue.Queue()
                    harness._eval_alerts_for_contract = lambda *args, target=harness: App._eval_alerts_for_contract(target, *args)
                    before = harness.cfg.to_dict()
                    price_before = {key: dict(values) for key, values in harness.price_state.items()}
                    with patch("app.save_config") as writer:
                        App._update_adapter_price_state(harness, {
                            "market_id": "polymarket", "contract_id": "token", "values": {source: value},
                        })
                    self.assertEqual(harness.cfg.to_dict(), before)
                    self.assertEqual(harness.price_state, price_before)
                    self.assertEqual(harness.fired, [])
                    writer.assert_not_called()
        for update in (
            {"event_type": "last_trade_price", "asset_id": "token", "price": True},
            {"event_type": "best_bid_ask", "asset_id": "token", "best_bid": 0.75, "best_ask": True},
            {"event_type": "price_change", "price_changes": [{"asset_id": "token", "best_bid": True}]},
            {"event_type": "book", "asset_id": "token", "bids": [{"price": True}]},
        ):
            harness = helpers.AlertHarness(PriceAlert(token_id="token", label="test", direction="above", threshold=0.5), 0.25)
            harness.ui_queue = queue.Queue()
            harness._eval_alerts_for_contract = lambda *args, target=harness: App._eval_alerts_for_contract(target, *args)
            before = harness.cfg.to_dict()
            with patch("app.save_config") as writer:
                App._update_price_state(harness, update)
            self.assertEqual(harness.cfg.to_dict(), before)
            self.assertEqual(harness.price_state, {"token": {"last_trade": 0.25}})
            writer.assert_not_called()

    def test_invalid_retained_desktop_price_preserves_config_without_popup(self) -> None:
        alert = PriceAlert(token_id="token", label="test", direction="above", threshold=0.5)
        alert.last_value = True
        harness = helpers.AlertHarness(alert, 0.75)
        harness.ui_queue = queue.Queue()
        before = harness.cfg.to_dict()
        with patch("app.save_config") as writer:
            App._eval_alerts_for_contract(harness, "polymarket", "token")
        self.assertEqual(harness.cfg.to_dict(), before)
        self.assertEqual(harness.fired, [])
        writer.assert_not_called()

    def test_failed_paper_history_does_not_report_success_or_admit_more_orders(self) -> None:
        harness = SimpleNamespace(cfg=AppConfig(), status_var=helpers.FakeVar())
        order = SimpleNamespace(market_id="polymarket", contract_id="token", side="BUY", size=1, limit_price=0.5)
        result = SimpleNamespace(accepted=True, message="paper", filled_size=1, average_price=0.5, raw={})
        self.bind_store(harness)
        with patch("core.storage.replace_file", side_effect=OSError("disk full")):
            self.assertIsNone(App._record_paper_trade(harness, order, result))
        self.assertEqual(harness.cfg.paper_trades, [])
        self.assertEqual(load_config(self.path).paper_trades, [])
        with patch.object(App, "_paper_order_from_form") as build_order:
            App.submit_paper_order(harness)
        build_order.assert_not_called()

    def queue_harness(self):
        harness = SimpleNamespace(
            cfg=AppConfig(wallets=[WalletWatch(id="watch", wallet=helpers.WALLET)]),
            ui_queue=queue.Queue(), _shutdown_started=True, log=Mock(),
            _handle_wallet_activity=Mock(return_value=CopyActivityOutcome("completed", "test", "test")),
        )
        task = WalletActivityTask("watch", {"timestamp":100,"transactionHash":"tx"}, "tx:tx")
        harness.ui_queue.put(("wallet_activity", task))
        return harness, task

    def test_paused_queue_does_not_checkpoint_or_handle_more_activity(self) -> None:
        harness, task = self.queue_harness()
        harness._config_persistence_error = "Restart required"
        with patch("app.save_config") as writer:
            App._process_queue(harness)
        writer.assert_not_called()
        harness._handle_wallet_activity.assert_not_called()
        self.assertEqual(harness.cfg.copy_activity_outbox, [])
        self.assertFalse(task.completion.get_nowait())

    def test_committed_checkpoint_is_not_rolled_back_in_memory_after_fsync_failure(self) -> None:
        harness, task = self.queue_harness()
        self.bind_store(harness)
        with patch("core.storage._fsync_parent_directory", side_effect=OSError("directory sync failed")):
            App._process_queue(harness)
        self.assertEqual(harness.cfg.to_dict(), load_config(self.path).to_dict())
        self.assertEqual(harness.cfg.copy_activity_outbox[0].state, "pending")
        self.assertTrue(harness._config_persistence_error)
        harness._handle_wallet_activity.assert_not_called()
        self.assertFalse(task.completion.get_nowait())

    def test_dispatch_intent_sync_failure_never_dispatches_or_clears_ambiguity(self) -> None:
        harness, task = self.queue_harness()
        self.bind_store(harness)
        dispatched = Mock()

        def handle(*_args, before_live_dispatch, **_kwargs):
            with patch("core.storage._fsync_parent_directory", side_effect=OSError("directory sync failed")):
                before_live_dispatch({"market_id":"polymarket", "contract_id":"token", "side":"BUY", "size":1, "limit_price":0.5})
            dispatched()
            return CopyActivityOutcome("completed", "test", "test")

        harness._handle_wallet_activity.side_effect = handle
        App._process_queue(harness)
        dispatched.assert_not_called()
        self.assertTrue(harness._config_persistence_error)
        self.assertEqual(harness.cfg.copy_activity_outbox[0].state, "ambiguous")
        self.assertEqual(load_config(self.path).copy_activity_outbox[0].state, "ambiguous")
        self.assertEqual(self.writer.call_count, 2)
        self.assertTrue(task.completion.get_nowait())


if __name__ == "__main__":
    unittest.main()
