from __future__ import annotations

import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from core.models import AlertEvent, AlertEventCapacityError, AppConfig, MAX_ALERT_EVENTS, PriceAlert
from core.storage import ConfigCommitError, ConfigConflictError, ConfigLoadError, load_config, save_config
from core import unattended_worker as worker
import market_sentinel_cli as cli
from web_api import (
    ReactGuiHandler, ReactGuiServer, alert_events_payload, alert_from_payload, evaluate_alerts_for_contract,
    price_snapshot_values, refresh_alert_price,
)
from market_adapters.types import PriceSnapshot
from scripts.backup_state import create_backup
from scripts.restore_state_backup import restore_backup
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def alert(*, once: bool = True, label: str = "Price crossing") -> PriceAlert:
    return PriceAlert(token_id="token", label=label, direction="above", threshold=0.5, once=once)


def cross(cfg: AppConfig, value: float = 0.75) -> list[str]:
    return evaluate_alerts_for_contract(
        cfg, "polymarket", "token", {("polymarket", "token"): {"last_trade": value}},
    )


def event(number: int = 0, *, acknowledged: bool = False) -> AlertEvent:
    return AlertEvent(
        id=f"event-{number}", alert_id="alert", market_id="polymarket", contract_id="token",
        label="Price crossing", direction="above", threshold=0.5, source="last_trade",
        value=0.75, message="polymarket:Price crossing last_trade=0.75 crossed above 0.5",
        created_at=100 + number, acknowledged_at=100 + number if acknowledged else 0,
    )


class AlertNotificationTests(unittest.TestCase):
    def test_invalid_price_controls_fail_load_and_api_update_without_consumption(self) -> None:
        invalid = (True, False, -0.01, 1.01, float("nan"), float("inf"), "nan", "inf", "bad", "", [], {}, 10 ** 400)
        original = alert().to_dict()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            for field in ("threshold", "last_value"):
                for value in invalid:
                    with self.subTest(field=field, value=str(value)[:30]):
                        raw = {**original, field: value}
                        with self.assertRaises(ValueError):
                            AppConfig.from_dict({"alerts": [raw]})
                        path.write_text(json.dumps({"alerts": [raw]}), encoding="utf-8")
                        before = path.read_bytes()
                        with self.assertRaises(ConfigLoadError):
                            load_config(path)
                        self.assertEqual(path.read_bytes(), before)
                        if field == "threshold":
                            cfg = AppConfig(alerts=[PriceAlert.from_dict(original)])
                            with self.assertRaises(ValueError):
                                alert_from_payload(cfg, object(), {"threshold": value}, existing=cfg.alerts[0])
                            self.assertEqual(cfg.alerts[0].to_dict(), original)
            normalized = PriceAlert.from_dict({**original, "threshold": "0.5", "last_value": "0.25"})
            self.assertEqual((normalized.threshold, normalized.last_value), (0.5, 0.25))
            for boundary in (0, 1):
                PriceAlert.from_dict({**original, "threshold": boundary, "last_value": boundary})
            with self.assertRaises(ValueError):
                PriceAlert.from_dict({**original, "threshold": None})

    def test_invalid_incoming_quotes_cannot_partially_consume_any_contract_alert(self) -> None:
        for source in ("last_trade", "midpoint", "best_bid", "best_ask"):
            for value in (True, False, -0.01, 1.01, float("nan"), float("inf"), "nan", "inf", "bad", [], {}, 10 ** 400):
                cfg = AppConfig(alerts=[alert(), PriceAlert(
                    token_id="token", label="Second", direction="below", threshold=0.5, source=source,
                )])
                before = cfg.to_dict()
                values = {"last_trade": 0.75, source: value}
                with self.subTest(source=source, value=str(value)[:30]), self.assertRaises(ValueError):
                    evaluate_alerts_for_contract(cfg, "polymarket", "token", {("polymarket", "token"): values})
                self.assertEqual(cfg.to_dict(), before)
        for field in ("threshold", "last_value"):
            cfg = AppConfig(alerts=[alert(), alert()])
            setattr(cfg.alerts[1], field, True)
            before = cfg.to_dict()
            with self.assertRaises(ValueError):
                cross(cfg)
            self.assertEqual(cfg.to_dict(), before)

    def test_unavailable_optional_quotes_preserve_state_and_valid_strings_cross(self) -> None:
        cfg = AppConfig(alerts=[alert()])
        cfg.alerts[0].last_value = 0.25
        before = cfg.to_dict()
        for values in ({}, {"last_trade": None}, {"last_trade": ""}):
            self.assertEqual(evaluate_alerts_for_contract(cfg, "polymarket", "token", {("polymarket", "token"): values}), [])
            self.assertEqual(cfg.to_dict(), before)
        self.assertEqual(len(cross(cfg, "0.75")), 1)
        self.assertEqual(cfg.alert_events[0].value, 0.75)

    def test_snapshot_validation_precedes_coercion_state_update_and_worker_commit(self) -> None:
        for field in ("last", "bid", "ask", "midpoint"):
            snapshot = PriceSnapshot("polymarket", "token", **{field: True})
            with self.subTest(field=field), self.assertRaises(ValueError):
                price_snapshot_values(snapshot)
        values = price_snapshot_values(PriceSnapshot("polymarket", "token", last=None, bid="0.4", ask="0.6"))
        self.assertEqual(values, {"last_trade": None, "midpoint": 0.5, "best_bid": 0.4, "best_ask": 0.6})
        adapter = SimpleNamespace(
            capabilities=SimpleNamespace(alerts=True, price_reading=True), display_name="Polymarket",
            get_price=lambda _token: PriceSnapshot("polymarket", "token", last=True),
        )
        registry = SimpleNamespace(create=lambda *_args: adapter)
        cfg = AppConfig(alerts=[alert()])
        prices = {("polymarket", "token"): {"last_trade": 0.25}}
        before = cfg.to_dict()
        with self.assertRaises(ValueError):
            refresh_alert_price(cfg, registry, cfg.alerts[0], prices)
        self.assertEqual(prices, {("polymarket", "token"): {"last_trade": 0.25}})
        self.assertEqual(cfg.to_dict(), before)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            save_config(cfg, path)
            durable = path.read_bytes()
            with patch("market_adapters.build_default_registry", return_value=registry):
                outcome = worker.execute_task_once(worker.TASK_ALERTS, path, 25)
            self.assertEqual((outcome.outcome, outcome.emitted), ("partial_feed_failure", 0))
            self.assertEqual(path.read_bytes(), durable)

    def test_alert_input_limits_match_the_durable_notification_contract(self) -> None:
        cfg = AppConfig(alerts=[alert()])
        original = cfg.alerts[0].to_dict()
        for field, invalid in (("label", "x" * 513), ("contract_id", "x" * 257),
                               ("label", "line\nbreak"), ("contract_id", "token\tvalue")):
            with self.subTest(field=field, invalid=invalid[:20]):
                payload = {"market_id": "polymarket", "contract_id": "token", "threshold": 0.5, field: invalid}
                with self.assertRaisesRegex(ValueError, "durable notification limit"):
                    alert_from_payload(cfg, object(), payload, existing=cfg.alerts[0])
                self.assertEqual(cfg.alerts[0].to_dict(), original)
                raw = {**original, "token_id" if field == "contract_id" else field: invalid}
                with self.assertRaises(ValueError):
                    AppConfig.from_dict({"alerts": [raw]})

    def test_backup_restore_preserves_pending_and_acknowledged_notifications(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state, backups, restored = root / "state", root / "backups", root / "restored"
            state.mkdir()
            save_config(AppConfig(alert_events=[event(1), event(2, acknowledged=True)]), state / "config.json")
            manifest = create_backup(state, backups)
            restore_backup(backups / manifest["archive"], restored)
            cfg = load_config(restored / "config.json")
            self.assertEqual([item.to_dict() for item in cfg.alert_events], [event(1).to_dict(), event(2, acknowledged=True).to_dict()])
            self.assertEqual(alert_events_payload(cfg)["counts"], {"total": 2, "unacknowledged": 1})

    def test_unattended_crossing_survives_child_completion_and_alert_deletion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            save_config(AppConfig(alerts=[alert()]), path)

            def refresh(cfg, _registry, _prices):
                return {"refreshed": [{"messages": cross(cfg)}], "problems": []}

            with patch("market_adapters.build_default_registry", return_value=object()), patch(
                "web_api.refresh_all_alert_prices", side_effect=refresh,
            ):
                result = worker.execute_task_once(worker.TASK_ALERTS, path, 25)
            self.assertEqual((result.outcome, result.emitted), ("succeeded", 1))
            loaded = load_config(path)
            self.assertFalse(loaded.alerts[0].enabled)
            self.assertEqual(cross(loaded), [])
            notification = alert_events_payload(loaded)["events"][0]
            self.assertIn("crossed above 0.5", notification["message"])
            self.assertEqual(notification["acknowledged_at"], 0)
            loaded.alerts.clear()
            save_config(loaded, path)
            self.assertEqual(load_config(path).alert_events[0].to_dict(), notification)

    def test_repeat_crossings_have_distinct_retained_identities(self) -> None:
        cfg = AppConfig(alerts=[alert(once=False)])
        self.assertEqual(len(cross(cfg)), 1)
        first = cfg.alert_events[0].id
        self.assertEqual(cross(cfg), [])
        self.assertEqual(cross(cfg, 0.25), [])
        self.assertEqual(len(cross(cfg)), 1)
        self.assertEqual(len(cfg.alert_events), 2)
        self.assertNotEqual(first, cfg.alert_events[1].id)

    def test_full_unread_history_does_not_consume_any_crossing(self) -> None:
        cfg = AppConfig(alerts=[alert(), alert(label="Second alert")])
        cfg.alert_events = [event(number) for number in range(MAX_ALERT_EVENTS - 1)]
        before = cfg.to_dict()
        with self.assertRaises(AlertEventCapacityError):
            cross(cfg)
        self.assertEqual(cfg.to_dict(), before)
        cfg.acknowledge_alert_event(cfg.alert_events[0].id)
        self.assertEqual(len(cross(cfg)), 2)
        self.assertEqual(len(cfg.alert_events), MAX_ALERT_EVENTS)
        self.assertNotIn("event-0", {item.id for item in cfg.alert_events})
        self.assertTrue(all(item.triggered for item in cfg.alerts))

    def test_backlog_full_worker_reports_failure_and_preserves_durable_config(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            cfg = AppConfig(alerts=[alert()], alert_events=[event(number) for number in range(MAX_ALERT_EVENTS)])
            save_config(cfg, path)
            before = path.read_bytes()
            with patch("market_adapters.build_default_registry", return_value=object()), patch(
                "web_api.refresh_all_alert_prices", side_effect=lambda cfg, *_args: cross(cfg),
            ):
                result = worker.execute_task_once(worker.TASK_ALERTS, path, 25)
            self.assertEqual(result.outcome, "alert_notification_history_full")
            self.assertEqual(result.exit_code, worker.EXIT_TEMPFAIL)
            self.assertFalse(result.retryable)
            self.assertEqual(path.read_bytes(), before)

    def test_failed_commit_leaves_notification_and_trigger_unconsumed_on_disk(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            save_config(AppConfig(alerts=[alert()]), path)
            cfg = load_config(path)
            cross(cfg)
            with patch("core.storage.replace_file", side_effect=OSError("disk unavailable")):
                with self.assertRaises(OSError):
                    save_config(cfg, path)
            durable = load_config(path)
            self.assertTrue(durable.alerts[0].enabled)
            self.assertEqual(durable.alert_events, [])
            cross(durable)
            save_config(durable, path)
            self.assertEqual(len(load_config(path).alert_events), 1)

    def test_uncertain_commit_keeps_event_and_trigger_together(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            save_config(AppConfig(alerts=[alert()]), path)
            cfg = load_config(path)
            cross(cfg)
            with patch("core.storage._fsync_parent_directory", side_effect=OSError("sync uncertain")):
                with self.assertRaises(ConfigCommitError):
                    save_config(cfg, path)
            durable = load_config(path)
            self.assertFalse(durable.alerts[0].enabled)
            self.assertEqual(len(durable.alert_events), 1)
            self.assertEqual(cross(durable), [])

    def test_concurrent_acknowledgement_cannot_overwrite_new_events(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            save_config(AppConfig(alerts=[alert(once=False)], alert_events=[event()]), path)
            reader, producer = load_config(path), load_config(path)
            reader.acknowledge_alert_event("event-0")
            cross(producer)
            save_config(producer, path)
            with self.assertRaises(ConfigConflictError):
                save_config(reader, path)
            retry = load_config(path)
            retry.acknowledge_alert_event("event-0")
            save_config(retry, path)
            self.assertEqual(len(load_config(path).alert_events), 2)

    def test_invalid_durable_event_history_fails_closed_without_rewriting(self) -> None:
        original = event().to_dict()
        invalid = [None, [{}], [original, original], [original] * (MAX_ALERT_EVENTS + 1)]
        invalid += [[{**original, key: value}] for key, value in (
            ("id", ""), ("created_at", True), ("acknowledged_at", "100"),
            ("acknowledged_at", 99), ("value", float("nan")), ("value", 1.5),
            ("message", "x" * 2049), ("message", "line\nbreak"),
        )]
        invalid.append([{key: value for key, value in original.items() if key != "id"}])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            for history in invalid:
                with self.subTest(history=str(history)[:80]):
                    path.write_text(json.dumps({"alert_events": history}), encoding="utf-8")
                    before = path.read_bytes()
                    with self.assertRaises(ConfigLoadError):
                        load_config(path)
                    self.assertEqual(path.read_bytes(), before)
        self.assertEqual(AppConfig.from_dict({}).alert_events, [])

    def test_cli_list_does_not_acknowledge_and_explicit_ack_survives_reload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            save_config(AppConfig(alert_events=[event()]), path)
            stream = io.StringIO()
            with patch("sys.stdout", stream):
                self.assertEqual(cli.main(["alerts", "events", "list", "--config", str(path), "--compact"]), 0)
            self.assertEqual(json.loads(stream.getvalue())["counts"]["unacknowledged"], 1)
            before = path.read_bytes()
            self.assertEqual(load_config(path).alert_events[0].acknowledged_at, 0)
            with patch("sys.stdout", io.StringIO()):
                self.assertEqual(cli.main(["alerts", "events", "acknowledge", "event-0", "--config", str(path)]), 0)
            self.assertNotEqual(path.read_bytes(), before)
            acknowledged = load_config(path).alert_events[0].acknowledged_at
            with patch("sys.stdout", io.StringIO()):
                self.assertEqual(cli.main(["alerts", "events", "acknowledge", "event-0", "--config", str(path)]), 0)
            self.assertEqual(load_config(path).alert_events[0].acknowledged_at, acknowledged)

    def test_http_notifications_require_auth_and_acknowledgement_is_durable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "config.json"
            save_config(AppConfig(alert_events=[event()]), path)
            with patch("web_api.DEFAULT_FRONTEND_DIR", root):
                server = ReactGuiServer(
                    ("127.0.0.1", 0), ReactGuiHandler, config_path=path,
                    frontend_dir=root, api_token="test-api-token",
                )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_address[1]}"

            def request(route, *, method="GET", authenticated=True, payload=None, extra_headers=None):
                headers = {"Authorization": "Bearer test-api-token"} if authenticated else {}
                headers.update(extra_headers or {})
                if method == "POST":
                    headers["Content-Type"] = "application/json"
                body = json.dumps(payload or {}).encode("utf-8") if method == "POST" else None
                req = Request(base + route, data=body, headers=headers, method=method)
                try:
                    with urlopen(req, timeout=5) as response:
                        return response.status, json.loads(response.read())
                except HTTPError as response:
                    with response:
                        return response.code, json.loads(response.read())

            try:
                self.assertEqual(request("/api/alerts/events", authenticated=False)[0], 401)
                status, listing = request("/api/alerts/events")
                self.assertEqual(status, 200)
                self.assertEqual(listing["events"][0]["id"], "event-0")
                self.assertEqual(load_config(path).alert_events[0].acknowledged_at, 0)
                before = path.read_bytes()
                for threshold in (True, False, -1, 2, "nan", "inf", "invalid", None):
                    status, _failed = request("/api/alerts", method="POST", payload={
                        "market_id": "polymarket", "contract_id": "token", "threshold": threshold,
                    })
                    self.assertEqual(status, 400)
                    self.assertEqual(path.read_bytes(), before)
                with patch("core.storage.replace_file", side_effect=OSError("private disk detail")):
                    status, failed = request("/api/alerts/events/event-0/acknowledge", method="POST")
                self.assertEqual(status, 500)
                self.assertNotIn("private disk detail", json.dumps(failed))
                self.assertEqual(load_config(path).alert_events[0].acknowledged_at, 0)
                status, acknowledgement = request("/api/alerts/events/event-0/acknowledge", method="POST")
                self.assertEqual(status, 200)
                self.assertEqual(acknowledgement["counts"]["unacknowledged"], 0)
                self.assertGreater(load_config(path).alert_events[0].acknowledged_at, 0)
                status, listing = request("/api/alerts")
                self.assertEqual(status, 200)
                self.assertEqual(listing["event_history"]["counts"]["unacknowledged"], 0)
                cfg = load_config(path)
                cfg.alert_events = [event(number) for number in range(140)]
                for item in cfg.alert_events:
                    item.message = "x" * 2048
                save_config(cfg, path)
                creation = {"market_id": "polymarket", "contract_id": "new-token", "label": "New alert", "threshold": 0.6}
                replay_headers = {"Idempotency-Key": "alert-large-backlog"}
                status, created = request("/api/alerts", method="POST", payload=creation, extra_headers=replay_headers)
                self.assertEqual(status, 200)
                self.assertEqual(created["counts"]["total"], 1)
                self.assertEqual(len(created["event_history"]["events"]), 140)
                durable = load_config(path)
                self.assertEqual(len(durable.alerts), 1)
                self.assertNotIn("event_history", durable.mutation_journal[0].response)
                self.assertEqual(request("/api/alerts/events/event-0/acknowledge", method="POST")[0], 200)
                status, replayed = request("/api/alerts", method="POST", payload=creation, extra_headers=replay_headers)
                self.assertEqual(status, 200)
                self.assertEqual(replayed["alerts"], created["alerts"])
                self.assertEqual(replayed["event_history"]["counts"]["unacknowledged"], 139)
                self.assertEqual(len(load_config(path).alerts), 1)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
