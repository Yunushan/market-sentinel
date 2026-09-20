from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from core import unattended_worker as worker
from core.storage import ConfigConflictError


ROOT = Path(__file__).resolve().parent.parent


def run_args(root: Path, *, task: str = worker.TASK_ALERTS, attempts: int = 3) -> argparse.Namespace:
    return argparse.Namespace(
        task=task,
        source_revision="a" * 40,
        service_unit=worker.SERVICE_UNIT_BY_TASK[task],
        unit_contract_sha256="b" * 64,
        config=root / "config.json",
        state_file=root / "worker-state.json",
        lock_file=root / ".worker.lock",
        deadline_seconds=10.0,
        attempt_timeout_seconds=1.0,
        lock_timeout_seconds=0.2,
        max_attempts=attempts,
        initial_backoff_seconds=0.0,
        max_backoff_seconds=0.0,
        wallet_limit=25,
    )


class UnattendedWorkerStateTests(unittest.TestCase):
    def test_state_round_trip_is_strict_and_atomic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            state = {
                "schema_version": worker.SCHEMA_VERSION,
                "tasks": {worker.TASK_ALERTS: {"state": "succeeded", "last_success_at_unix": 10.0}},
            }
            worker.write_worker_state(path, state)

            self.assertEqual(worker.read_worker_state(path), state)
            self.assertFalse(any(path.parent.glob(f".{path.name}.*.tmp")))
            if os.name == "posix":
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_state_rejects_non_regular_oversized_and_ambiguous_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            directory = root / "directory"
            directory.mkdir()
            with self.assertRaises(worker.WorkerStateError):
                worker.read_worker_state(directory)

            oversized = root / "oversized.json"
            oversized.write_bytes(b"x" * (worker.MAX_STATE_BYTES + 1))
            with self.assertRaises(worker.WorkerStateError):
                worker.read_worker_state(oversized)

            duplicate = root / "duplicate.json"
            duplicate.write_text('{"schema_version":1,"tasks":{},"tasks":{}}', encoding="utf-8")
            with self.assertRaises(worker.WorkerStateError):
                worker.read_worker_state(duplicate)

    def test_state_rejects_unknown_schema_and_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text('{"schema_version":2,"tasks":{}}', encoding="utf-8")
            with self.assertRaises(worker.WorkerStateError):
                worker.read_worker_state(path)

            path.write_text('{"schema_version":1,"tasks":{"unknown":{}}}', encoding="utf-8")
            with self.assertRaises(worker.WorkerStateError):
                worker.read_worker_state(path)

    def test_status_requires_recent_terminal_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            now = time.time()
            worker.write_worker_state(
                path,
                {
                    "schema_version": worker.SCHEMA_VERSION,
                    "tasks": {
                        worker.TASK_ALERTS: {
                            "state": "succeeded",
                            "last_success_at": worker._utc_timestamp(now),
                            "last_success_at_unix": now,
                        },
                        worker.TASK_WALLETS: {
                            "state": "failed",
                            "last_success_at": worker._utc_timestamp(now),
                            "last_success_at_unix": now,
                            "consecutive_failures": 1,
                        },
                    },
                },
            )
            args = argparse.Namespace(
                state_file=path,
                task=worker.TASK_ALERTS,
                max_age_seconds=60.0,
                compact=True,
            )
            with patch("builtins.print") as output:
                self.assertEqual(worker.run_status(args), 0)
            payload = json.loads(output.call_args.args[0])
            self.assertTrue(payload["healthy"])

            args.task = "all"
            with patch("builtins.print") as output:
                self.assertEqual(worker.run_status(args), 1)
            payload = json.loads(output.call_args.args[0])
            self.assertFalse(payload["healthy"])
            self.assertEqual(payload["tasks"][worker.TASK_WALLETS]["consecutive_failures"], 1)

            state = worker.read_worker_state(path)
            state["tasks"][worker.TASK_ALERTS]["last_success_at_unix"] = now + 3600
            worker.write_worker_state(path, state)
            args.task = worker.TASK_ALERTS
            with patch("builtins.print") as output:
                self.assertEqual(worker.run_status(args), 1)
            future_payload = json.loads(output.call_args.args[0])
            self.assertIsNone(
                future_payload["tasks"][worker.TASK_ALERTS]["last_success_age_seconds"]
            )


class UnattendedWorkerAttemptTests(unittest.TestCase):
    def test_attempt_environment_drops_admin_and_signing_secrets(self) -> None:
        source = {
            "MARKET_SENTINEL_API_TOKEN": "admin",
            "MARKET_SENTINEL_OBSERVABILITY_TOKEN": "observer",
            "POLYMARKET_PRIVATE_KEY": "signing-key",
            "KALSHI_PRIVATE_KEY_PEM": "pem",
            "SSLKEYLOGFILE": "tls-secrets.log",
            "PYTHONINSPECT": "1",
            "PYTHONPATH": "attacker-controlled-import-path",
            "BETFAIR_SESSION_TOKEN": "future-mutation-capable-secret",
            "CONTEXT_API_KEY": "read-key",
        }
        with patch.dict(os.environ, source, clear=True):
            environment = worker._attempt_child_environment()

        self.assertEqual(environment["CONTEXT_API_KEY"], "read-key")
        self.assertEqual(environment["PYTHONUNBUFFERED"], "1")
        for key in source.keys() - {"CONTEXT_API_KEY"}:
            self.assertNotIn(key, environment)

    def test_alert_attempt_commits_success_and_surfaces_partial_failure(self) -> None:
        cfg = SimpleNamespace()
        config_path = Path("config.json")
        with patch("core.unattended_worker.load_config", return_value=cfg), patch(
            "market_adapters.build_default_registry", return_value="registry"
        ), patch(
            "web_api.refresh_all_alert_prices",
            return_value={"refreshed": [{"messages": ["crossed"]}], "problems": []},
        ) as refresh, patch("core.unattended_worker.save_config") as save:
            result = worker.execute_task_once(worker.TASK_ALERTS, config_path, 25)

        self.assertEqual(result.outcome, "succeeded")
        self.assertEqual((result.processed, result.emitted, result.problems), (1, 1, 0))
        refresh.assert_called_once_with(cfg, "registry", {})
        save.assert_called_once_with(cfg, config_path)

        with patch("core.unattended_worker.load_config", return_value=cfg), patch(
            "market_adapters.build_default_registry", return_value="registry"
        ), patch(
            "web_api.refresh_all_alert_prices",
            return_value={"refreshed": [{"messages": []}], "problems": ["redacted upstream detail"]},
        ), patch("core.unattended_worker.save_config") as save:
            partial = worker.execute_task_once(worker.TASK_ALERTS, config_path, 25)

        self.assertEqual(partial.outcome, "partial_feed_failure")
        self.assertTrue(partial.retryable)
        self.assertEqual(partial.exit_code, worker.EXIT_TEMPFAIL)
        save.assert_called_once_with(cfg, config_path)
        self.assertNotIn("redacted upstream detail", json.dumps(partial.to_dict()))

    def test_wallet_attempt_is_bounded_to_requested_limit_and_commits_seen_state(self) -> None:
        cfg = SimpleNamespace()
        config_path = Path("config.json")
        with patch("core.unattended_worker.load_config", return_value=cfg), patch(
            "market_adapters.build_default_registry", return_value="registry"
        ), patch(
            "web_api.poll_wallet_activity",
            return_value={"polled_wallets": 2, "activity": [{"safe": True}], "problems": []},
        ) as poll, patch("core.unattended_worker.save_config") as save:
            result = worker.execute_task_once(worker.TASK_WALLETS, config_path, 17)

        self.assertEqual(result.outcome, "succeeded")
        self.assertEqual((result.processed, result.emitted), (2, 1))
        poll.assert_called_once_with(cfg, "registry", [], limit=17, advance_seen=False)
        save.assert_not_called()

    def test_config_conflict_is_retryable_and_uncertain_commit_is_not(self) -> None:
        cfg = SimpleNamespace()
        with patch("core.unattended_worker.load_config", return_value=cfg), patch(
            "market_adapters.build_default_registry", return_value="registry"
        ), patch(
            "web_api.refresh_all_alert_prices", return_value={"refreshed": [], "problems": []}
        ), patch(
            "core.unattended_worker.save_config", side_effect=ConfigConflictError("conflict")
        ):
            conflict = worker.execute_task_once(worker.TASK_ALERTS, Path("config.json"), 25)

        self.assertEqual(conflict.outcome, "config_conflict")
        self.assertTrue(conflict.retryable)

        with patch("core.unattended_worker.load_config", return_value=cfg), patch(
            "market_adapters.build_default_registry", return_value="registry"
        ), patch(
            "web_api.refresh_all_alert_prices", return_value={"refreshed": [], "problems": []}
        ), patch(
            "core.unattended_worker.save_config", side_effect=worker.ConfigCommitError("uncertain")
        ):
            uncertain = worker.execute_task_once(worker.TASK_ALERTS, Path("config.json"), 25)

        self.assertEqual(uncertain.outcome, "config_durability_uncertain")
        self.assertFalse(uncertain.retryable)

    def test_attempt_result_rejects_exit_code_or_task_spoofing(self) -> None:
        valid = worker.AttemptResult(worker.TASK_ALERTS, "succeeded", False).to_dict()
        valid["task"] = worker.TASK_WALLETS
        result = worker._decode_attempt_result(
            worker.TASK_ALERTS,
            json.dumps(valid).encode(),
            0,
        )
        self.assertEqual(result.outcome, "invalid_attempt_result")

        valid["task"] = worker.TASK_ALERTS
        valid["exit_code"] = 0
        result = worker._decode_attempt_result(
            worker.TASK_ALERTS,
            json.dumps(valid).encode(),
            worker.EXIT_TEMPFAIL,
        )
        self.assertEqual(result.outcome, "invalid_attempt_result")

    def test_attempt_timeout_terminates_the_isolated_child(self) -> None:
        process = MagicMock()
        process.communicate.side_effect = subprocess.TimeoutExpired(cmd="worker", timeout=0.01)
        shutdown = worker.ShutdownRequest()
        with patch("core.unattended_worker.subprocess.Popen", return_value=process), patch(
            "core.unattended_worker._terminate_process"
        ) as terminate:
            result = worker.run_attempt_process(
                task=worker.TASK_ALERTS,
                config_path=Path("config.json"),
                wallet_limit=25,
                timeout_seconds=0.01,
                shutdown=shutdown,
            )

        self.assertEqual(result.outcome, "attempt_timeout")
        terminate.assert_called_once_with(process)


class UnattendedWorkerSupervisorTests(unittest.TestCase):
    def test_real_supervisor_child_smoke_commits_config_and_last_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            args = run_args(root, attempts=1)
            args.deadline_seconds = 15.0
            args.attempt_timeout_seconds = 10.0

            self.assertEqual(worker.run_worker(args), 0)

            self.assertTrue(args.config.exists())
            entry = worker.read_worker_state(args.state_file)["tasks"][worker.TASK_ALERTS]
            self.assertEqual(entry["state"], "succeeded")
            self.assertEqual(entry["attempts_completed"], 1)
            self.assertEqual(entry["last_problems"], 0)
            self.assertGreater(entry["last_success_at_unix"], 0)

    def test_conflict_reloads_in_a_new_attempt_then_records_durable_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            args = run_args(root)
            outcomes = [
                worker.AttemptResult(worker.TASK_ALERTS, "config_conflict", True, exit_code=worker.EXIT_TEMPFAIL),
                worker.AttemptResult(worker.TASK_ALERTS, "succeeded", False, processed=2),
            ]
            with patch("core.unattended_worker.run_attempt_process", side_effect=outcomes) as attempt:
                self.assertEqual(worker.run_worker(args), 0)

            self.assertEqual(attempt.call_count, 2)
            state = worker.read_worker_state(args.state_file)["tasks"][worker.TASK_ALERTS]
            self.assertEqual(state["state"], "succeeded")
            self.assertEqual(state["attempts_completed"], 2)
            self.assertEqual(state["last_processed"], 2)
            self.assertEqual(state["source_revision"], "a" * 40)
            self.assertEqual(state["service_unit"], worker.SERVICE_UNIT_BY_TASK[worker.TASK_ALERTS])
            self.assertEqual(
                state["invocation_sha256"],
                worker.worker_invocation_sha256(
                    task=worker.TASK_ALERTS,
                    service_unit=worker.SERVICE_UNIT_BY_TASK[worker.TASK_ALERTS],
                    source_revision="a" * 40,
                    unit_contract_sha256="b" * 64,
                ),
            )
            self.assertEqual(state["consecutive_failures"], 0)
            self.assertIn("last_success_at", state)

    def test_partial_failures_exhaust_retries_and_preserve_prior_last_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            args = run_args(root, attempts=1)
            with patch(
                "core.unattended_worker.run_attempt_process",
                return_value=worker.AttemptResult(worker.TASK_ALERTS, "succeeded", False),
            ):
                self.assertEqual(worker.run_worker(args), 0)
            first = worker.read_worker_state(args.state_file)["tasks"][worker.TASK_ALERTS]
            last_success = first["last_success_at"]

            args.max_attempts = 2
            partial = worker.AttemptResult(
                worker.TASK_ALERTS,
                "partial_feed_failure",
                True,
                problems=1,
                exit_code=worker.EXIT_TEMPFAIL,
            )
            with patch("core.unattended_worker.run_attempt_process", return_value=partial) as attempt:
                self.assertEqual(worker.run_worker(args), worker.EXIT_TEMPFAIL)

            self.assertEqual(attempt.call_count, 2)
            failed = worker.read_worker_state(args.state_file)["tasks"][worker.TASK_ALERTS]
            self.assertEqual(failed["state"], "failed")
            self.assertEqual(failed["last_outcome"], "partial_feed_failure")
            self.assertEqual(failed["consecutive_failures"], 1)
            self.assertEqual(failed["last_success_at"], last_success)
            self.assertEqual(failed["last_problems"], 1)

    def test_stale_running_record_is_counted_as_abandoned(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            args = run_args(root, attempts=1)
            worker.write_worker_state(
                args.state_file,
                {
                    "schema_version": worker.SCHEMA_VERSION,
                    "tasks": {
                        worker.TASK_ALERTS: {
                            "state": "running",
                            "consecutive_failures": 2,
                            "abandoned_runs": 1,
                        }
                    },
                },
            )
            failure = worker.AttemptResult(
                worker.TASK_ALERTS,
                "operation_failed",
                False,
                exit_code=worker.EXIT_SOFTWARE,
            )
            with patch("core.unattended_worker.run_attempt_process", return_value=failure):
                self.assertEqual(worker.run_worker(args), worker.EXIT_SOFTWARE)

            entry = worker.read_worker_state(args.state_file)["tasks"][worker.TASK_ALERTS]
            self.assertEqual(entry["abandoned_runs"], 2)
            self.assertEqual(entry["consecutive_failures"], 4)
            self.assertEqual(entry["previous_run_outcome"], "abandoned_without_terminal_telemetry")

    def test_busy_shared_lock_fails_without_clobbering_the_active_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            args = run_args(Path(tmp), attempts=1)
            with patch(
                "core.unattended_worker.exclusive_worker_lock",
                side_effect=worker.WorkerBusyError("busy"),
            ):
                self.assertEqual(worker.run_worker(args), worker.EXIT_TEMPFAIL)
            self.assertFalse(args.state_file.exists())

    def test_invalid_bounds_fail_before_spawning_or_writing_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            args = run_args(Path(tmp))
            args.attempt_timeout_seconds = args.deadline_seconds + 1
            with patch("core.unattended_worker.run_attempt_process") as attempt:
                self.assertEqual(worker.run_worker(args), worker.EXIT_USAGE)
            attempt.assert_not_called()
            self.assertFalse(args.state_file.exists())

    def test_revision_and_unit_binding_are_required_before_state_is_written(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for field, value in (
                ("source_revision", "stale"),
                ("service_unit", worker.SERVICE_UNIT_BY_TASK[worker.TASK_WALLETS]),
                ("unit_contract_sha256", "0" * 63),
            ):
                args = run_args(root)
                setattr(args, field, value)
                with self.subTest(field=field), patch(
                    "core.unattended_worker.run_attempt_process"
                ) as attempt:
                    self.assertEqual(worker.run_worker(args), worker.EXIT_USAGE)
                    attempt.assert_not_called()


class UnattendedWorkerSystemdTests(unittest.TestCase):
    def test_two_timers_share_one_lock_and_dedicated_least_privilege_environment(self) -> None:
        from scripts.verify_production_deployment import REQUIRED_WORKER_EXEC_START_COMMANDS

        services = [
            ROOT / "deploy/systemd/market-sentinel-alerts-refresh.service",
            ROOT / "deploy/systemd/market-sentinel-wallets-poll.service",
        ]
        contents = [path.read_text(encoding="utf-8") for path in services]
        commands = [next(line for line in text.splitlines() if line.startswith("ExecStart=")) for text in contents]
        for text in contents:
            for fragment in (
                "User=market-sentinel",
                "Group=market-sentinel",
                "EnvironmentFile=/etc/market-sentinel/market-sentinel-worker.env",
                "--state-file /var/lib/market-sentinel/unattended-worker-state.json",
                "--lock-file /var/lib/market-sentinel/.unattended-worker.lock",
                "--deadline-seconds 90",
                "--attempt-timeout-seconds 40",
                "--max-attempts 3",
                "--source-revision ${MARKET_SENTINEL_SOURCE_REVISION}",
                "--service-unit market-sentinel-",
                "--unit-contract-sha256 ",
                "ExecStartPre=/usr/bin/test -f /var/lib/market-sentinel/config.json",
                "ExecStartPre=/usr/bin/test ! -L /var/lib/market-sentinel/config.json",
                "KillMode=mixed",
                "TimeoutStartSec=105",
                "NoNewPrivileges=true",
                "ProtectSystem=strict",
                "ProtectHome=true",
                "ProtectProc=invisible",
                "CapabilityBoundingSet=",
                "ReadWritePaths=/var/lib/market-sentinel",
            ):
                with self.subTest(fragment=fragment):
                    self.assertIn(fragment, text)
            self.assertNotIn("EnvironmentFile=/etc/market-sentinel/market-sentinel.env", text)
        self.assertIn("--task alerts-refresh", commands[0])
        self.assertIn("--task wallets-poll", commands[1])
        for path, command in zip(services, commands, strict=True):
            self.assertEqual(
                shlex.split(command.removeprefix("ExecStart="), posix=True),
                list(REQUIRED_WORKER_EXEC_START_COMMANDS[path.name]),
            )

        environment = (ROOT / "deploy/systemd/market-sentinel-worker.env.example").read_text(encoding="utf-8")
        for forbidden in (
            "POLYMARKET_PRIVATE_KEY=",
            "KALSHI_PRIVATE_KEY_PEM=",
            "OPINION_PRIVATE_KEY=",
            "SX_BET_PRIVATE_KEY=",
            "MARKET_SENTINEL_API_TOKEN=",
        ):
            self.assertNotIn(forbidden, environment)
        self.assertIn("read-only credential", environment)

    def test_timers_are_persistent_staggered_and_slower_than_the_hard_deadline(self) -> None:
        alerts = (ROOT / "deploy/systemd/market-sentinel-alerts-refresh.timer").read_text(encoding="utf-8")
        wallets = (ROOT / "deploy/systemd/market-sentinel-wallets-poll.timer").read_text(encoding="utf-8")
        self.assertIn("OnCalendar=*-*-* *:0/2:15", alerts)
        self.assertIn("Unit=market-sentinel-alerts-refresh.service", alerts)
        self.assertIn("OnCalendar=*-*-* *:0/5:45", wallets)
        self.assertIn("Unit=market-sentinel-wallets-poll.service", wallets)
        for timer in (alerts, wallets):
            self.assertIn("Persistent=true", timer)
            self.assertIn("AccuracySec=10s", timer)
            self.assertIn("RandomizedDelaySec=10s", timer)

    def test_distribution_exposes_the_dedicated_worker_entry_point(self) -> None:
        metadata = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        manifest = (ROOT / "MANIFEST.in").read_text(encoding="utf-8")
        self.assertIn('market-sentinel-worker = "core.unattended_worker:main"', metadata)
        self.assertIn("recursive-include deploy *", manifest)


if __name__ == "__main__":
    unittest.main()
