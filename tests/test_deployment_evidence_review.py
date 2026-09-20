from __future__ import annotations

import copy
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts.review_deployment_evidence import (
    DeploymentEvidenceError,
    required_check_names,
    review_deployment_report,
)
from core.unattended_worker import worker_invocation_sha256
from scripts.verify_production_deployment import (
    DEFAULT_WORKER_ENVIRONMENT_PATH,
    DEFAULT_WORKER_LOCK_PATH,
    DEFAULT_WORKER_STATE_PATH,
    HEALTH_CHECK_MAX_AGE_SECONDS,
    REQUIRED_HEALTH_SERVICE_PROPERTIES,
    REQUIRED_UNATTENDED_SERVICE_CONTRACTS,
    REQUIRED_WORKER_UNIT_CONTRACT_SHA256,
    REQUIRED_SYSTEMD_TIMER_CONTRACTS,
    REQUIRED_WEB_EXEC_START_PRE_COMMANDS,
    UNATTENDED_WORKER_SERVICES,
    UNATTENDED_WORKER_TASK_MAX_AGE_SECONDS,
)


REVISION = "a" * 40
FRONTEND_SHA256 = "b" * 64
VERSION = "1.0.11"
NOW = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _unattended_worker_check(collected_at: datetime) -> tuple[dict[str, object], dict[str, dict[str, object]]]:
    finish_times = {
        "alerts-refresh": collected_at - timedelta(seconds=30.75),
        "wallets-poll": collected_at - timedelta(seconds=90.25),
    }
    run_ids = {
        "alerts-refresh": "00000000-0000-4000-8000-000000000201",
        "wallets-poll": "00000000-0000-4000-8000-000000000202",
    }
    tasks: dict[str, dict[str, object]] = {}
    services: dict[str, dict[str, object]] = {}
    recent: dict[str, dict[str, object]] = {}
    for service, identity in UNATTENDED_WORKER_SERVICES.items():
        task = identity["task"]
        finished = finish_times[task]
        started = finished - timedelta(seconds=2)
        attempted = finished - timedelta(seconds=1)
        # systemctl commonly renders this property only to whole seconds;
        # tolerate that representation without weakening either timestamp pair.
        completed = finished.replace(microsecond=0)
        max_age = UNATTENDED_WORKER_TASK_MAX_AGE_SECONDS[task]
        tasks[task] = {
            "service": service,
            "timer": identity["timer"],
            "state": "succeeded",
            "run_id": run_ids[task],
            "source_revision": REVISION,
            "service_unit": service,
            "unit_contract_sha256": REQUIRED_WORKER_UNIT_CONTRACT_SHA256[service],
            "invocation_sha256": worker_invocation_sha256(
                task=task,
                service_unit=service,
                source_revision=REVISION,
                unit_contract_sha256=REQUIRED_WORKER_UNIT_CONTRACT_SHA256[service],
            ),
            "last_started_at": _iso(started),
            "last_started_at_unix_seconds": started.timestamp(),
            "last_attempt_at": _iso(attempted),
            "last_attempt_at_unix_seconds": attempted.timestamp(),
            "last_finished_at": _iso(finished),
            "last_finished_at_unix_seconds": finished.timestamp(),
            "last_success_at": _iso(finished),
            "last_success_at_unix_seconds": finished.timestamp(),
            "last_duration_seconds": 2.0,
            "deadline_seconds": 90.0,
            "max_attempts": 3,
            "attempts_completed": 1,
            "last_attempt_outcome": "succeeded",
            "last_outcome": "succeeded",
            "last_attempt_processed": 2,
            "last_attempt_problems": 0,
            "last_attempt_emitted": 1,
            "processed": 2,
            "problems": 0,
            "emitted": 1,
            "consecutive_failures": 0,
            "abandoned_runs": 0,
            "total_runs": 4,
            "total_successes": 3,
            "total_failures": 1,
            "freshness_age_seconds": (collected_at - finished).total_seconds(),
            "max_age_seconds": max_age,
        }
        completed_text = completed.strftime("%a %Y-%m-%d %H:%M:%S UTC")
        service_evidence = {
            "task": task,
            "timer": identity["timer"],
            "completed_at": completed_text,
            "completed_at_unix_seconds": completed.timestamp(),
            "age_seconds": (collected_at - completed).total_seconds(),
            "max_age_seconds": max_age,
            "source_revision": REVISION,
            "unit_contract_sha256": REQUIRED_WORKER_UNIT_CONTRACT_SHA256[service],
        }
        services[service] = service_evidence
        recent[f"systemd_recent_success_{service}"] = {
            "unit": service,
            "completed_at": completed_text,
            "completed_at_unix_seconds": completed.timestamp(),
            "age_seconds": (collected_at - completed).total_seconds(),
            "max_age_seconds": max_age,
        }
    latest = max(finish_times.values())
    return (
        {
            "state_file": DEFAULT_WORKER_STATE_PATH.as_posix(),
            "state_schema_version": 1,
            "state_updated_at": _iso(latest),
            "state_updated_at_unix_seconds": latest.timestamp(),
            "state_sha256": "9" * 64,
            "lock_file": DEFAULT_WORKER_LOCK_PATH.as_posix(),
            "environment_file": DEFAULT_WORKER_ENVIRONMENT_PATH.as_posix(),
            "environment_keys": ["MARKET_SENTINEL_SOURCE_REVISION"],
            "expected_service_count": len(UNATTENDED_WORKER_SERVICES),
            "service_count": len(UNATTENDED_WORKER_SERVICES),
            "service_contracts": copy.deepcopy(REQUIRED_UNATTENDED_SERVICE_CONTRACTS),
            "services": services,
            "tasks": tasks,
        },
        recent,
    )


def _report() -> dict[str, object]:
    checks: list[dict[str, object]] = [
        {"name": name, "status": "pass", "detail": "verified"}
        for name in sorted(required_check_names())
    ]
    indexed = {str(check["name"]): check for check in checks}
    indexed["loopback_health"].update(
        {
            "api_version": VERSION,
            "runtime_source_revision": REVISION,
            "runtime_frontend_sha256": FRONTEND_SHA256,
            "disk_frontend_sha256": FRONTEND_SHA256,
        }
    )
    indexed["public_https_proxy"].update(
        {
            "api_version": VERSION,
            "runtime_source_revision": REVISION,
            "runtime_frontend_sha256": FRONTEND_SHA256,
            "unauthenticated_probes": 5,
        }
    )
    indexed["deployment_host_identity"].update(
        {"deployment_provider": "bare-metal", "host_identity_sha256": "d" * 64}
    )
    indexed["durable_state_wiring"].update(
        {
            "durable_store_count": 4,
            "state_directory": "/var/lib/market-sentinel",
            "backup_source": "/var/lib/market-sentinel",
        }
    )
    indexed["health_credential_isolation"].update(
        {
            "environment_path": "/etc/market-sentinel/market-sentinel-health.env",
            "service_user": "market-sentinel-health",
            "service_group": "market-sentinel-health",
            "private_user_namespace": True,
            "process_visibility": "invisible",
            "verified_service_property_count": len(REQUIRED_HEALTH_SERVICE_PROPERTIES) + 1,
            "environment_variable_count": 1,
            "admin_environment_unset": True,
            "token_preflight_removed": True,
            "probe_requires_observability": True,
            "web_startup_probe_removed": True,
            "inline_environment_empty": True,
            "manager_environment_not_passed": True,
        }
    )
    indexed["web_startup_preflight"].update(
        {
            "expected_command_count": len(REQUIRED_WEB_EXEC_START_PRE_COMMANDS),
            "command_count": len(REQUIRED_WEB_EXEC_START_PRE_COMMANDS),
            "commands": [list(command) for command in REQUIRED_WEB_EXEC_START_PRE_COMMANDS],
            "commands_succeeded": True,
        }
    )
    indexed["systemd_timer_contracts"].update(
        {
            "expected_timer_count": len(REQUIRED_SYSTEMD_TIMER_CONTRACTS),
            "timer_count": len(REQUIRED_SYSTEMD_TIMER_CONTRACTS),
            "timers": copy.deepcopy(REQUIRED_SYSTEMD_TIMER_CONTRACTS),
        }
    )
    monitoring_completed_at = datetime(2026, 8, 26, 11, 29, tzinfo=timezone.utc)
    indexed["systemd_recent_success_market-sentinel-health.service"].update(
        {
            "unit": "market-sentinel-health.service",
            "completed_at": "Wed 2026-08-26 11:29:00 UTC",
            "completed_at_unix_seconds": monitoring_completed_at.timestamp(),
            "age_seconds": 60,
            "max_age_seconds": HEALTH_CHECK_MAX_AGE_SECONDS,
        }
    )
    collected_at = datetime(2026, 8, 26, 11, 30, tzinfo=timezone.utc)
    worker_check, recent_worker_checks = _unattended_worker_check(collected_at)
    indexed["unattended_workers"].update(worker_check)
    for name, recent_check in recent_worker_checks.items():
        indexed[name].update(recent_check)
    indexed["verified_recent_state_backup"].update(
        {
            "created_at": "2026-08-26T11:00:00Z",
            "archive": "market-sentinel-state-20260826T110000Z.tar.gz",
            "backup_age_seconds": 1800,
            "sha256": "c" * 64,
            "file_count": 3,
            "verified_bytes": 42,
            "verified_pairs": 1,
            "invalid_pairs": 0,
            "orphan_archives": 0,
            "orphan_manifests": 0,
        }
    )
    indexed["verified_restore_drill"].update(
        {
            "mode": "isolated_full_restore",
            "archive": "market-sentinel-state-20260826T110000Z.tar.gz",
            "backup_created_at": "2026-08-26T11:00:00Z",
            "backup_sha256": "c" * 64,
            "restored_file_count": 3,
            "restored_bytes": 42,
            "completed_at": "2026-08-26T11:25:00Z",
            "application": {
                "schema_version": 1, "config_loaded": True, "health_ready": True,
                "state_readable": True, "mutations_blocked": True, "outbound_attempts": 0,
                "files_unchanged": True, "sqlite_databases_checked": 0,
                "api_version": VERSION, "runtime_source_revision": REVISION,
                "runtime_frontend_sha256": FRONTEND_SHA256,
            },
        }
    )
    indexed["verified_production_rollback_drill"].update(
        {
            "drill_id": "00000000-0000-4000-8000-000000000001",
            "report_sha256": "f" * 64,
            "completed_at": "2026-08-26T10:30:00Z",
            "rollback_revision": "e" * 40,
            "final_revision": REVISION,
            "step_count": 5,
        }
    )
    return {
        "schema_version": 1,
        "collected_at": "2026-08-26T11:30:00Z",
        "source": {
            "project_version": VERSION,
            "git_revision": REVISION,
            "git_revision_status": "ok",
            "git_worktree_status": "clean",
        },
        "collection": {
            "mode": "production",
            "systemd_requested": True,
            "public_proxy_requested": True,
            "public_origin": "https://markets.example.net",
            "expected_version": VERSION,
            "expected_source_revision": REVISION,
            "expected_frontend_sha256": FRONTEND_SHA256,
            "deployment_provider": "bare-metal",
            "host_identity_sha256": "d" * 64,
            "restore_drill_requested": True,
            "rollback_drill_requested": True,
            "run_id": 0,
            "run_attempt": 0,
            "nonce": "",
        },
        "status": "ok",
        "checks": checks,
    }


class DeploymentEvidenceReviewTests(unittest.TestCase):
    def test_inventory_only_or_wrong_runtime_restore_cannot_pass_review(self) -> None:
        for replacement in (None, {}, {"health_ready": True}):
            report = _report()
            restore = next(item for item in report["checks"] if item["name"] == "verified_restore_drill")
            restore["application"] = replacement
            with self.subTest(replacement=replacement), self.assertRaises(DeploymentEvidenceError):
                self._review(report)
        for key, bad_value in (
            ("health_ready", False), ("outbound_attempts", 1), ("files_unchanged", False),
            ("mutations_blocked", False), ("runtime_source_revision", "f" * 40),
            ("runtime_frontend_sha256", "f" * 64), ("sqlite_databases_checked", True),
        ):
            report = _report()
            restore = next(item for item in report["checks"] if item["name"] == "verified_restore_drill")
            restore["application"][key] = bad_value
            with self.subTest(key=key), self.assertRaises(DeploymentEvidenceError):
                self._review(report)

    def _review(self, report: dict[str, object], *, now: datetime = NOW) -> dict[str, object]:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "deployment.json"
            path.write_text(json.dumps(report, sort_keys=True), encoding="utf-8")
            return review_deployment_report(
                path,
                expected_version=VERSION,
                expected_revision=REVISION,
                now=now,
            )

    def _review_text(self, text: str) -> dict[str, object]:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "deployment.json"
            path.write_text(text, encoding="utf-8")
            return review_deployment_report(
                path,
                expected_version=VERSION,
                expected_revision=REVISION,
                now=NOW,
            )

    def test_accepts_complete_fresh_production_report(self) -> None:
        result = self._review(_report())

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["environment"], "production")
        self.assertEqual(result["source_revision"], REVISION)
        self.assertEqual(result["check_count"], len(required_check_names()))
        self.assertEqual(len(str(result["raw_report_sha256"])), 64)
        workers = result["unattended_workers"]
        self.assertEqual(workers["state_file"], DEFAULT_WORKER_STATE_PATH.as_posix())
        self.assertEqual(workers["lock_file"], DEFAULT_WORKER_LOCK_PATH.as_posix())
        self.assertEqual(set(workers["services"]), set(UNATTENDED_WORKER_SERVICES))
        self.assertEqual(set(workers["tasks"]), set(UNATTENDED_WORKER_TASK_MAX_AGE_SECONDS))
        self.assertEqual(
            set(workers["tasks"]["alerts-refresh"]),
            {
                "service",
                "timer",
                "run_id",
                "source_revision",
                "service_unit",
                "unit_contract_sha256",
                "invocation_sha256",
                "last_success_at",
                "last_success_at_unix_seconds",
                "freshness_age_seconds",
                "max_age_seconds",
                "attempts_completed",
                "processed",
                "emitted",
                "total_runs",
                "total_successes",
                "total_failures",
                "abandoned_runs",
            },
        )

    def test_rejects_local_smoke_or_missing_public_proxy(self) -> None:
        for field, value in (("mode", "local_smoke"), ("systemd_requested", False), ("public_proxy_requested", False)):
            with self.subTest(field=field):
                report = _report()
                collection = report["collection"]
                assert isinstance(collection, dict)
                collection[field] = value
                with self.assertRaises(DeploymentEvidenceError):
                    self._review(report)

    def test_rejects_stale_report_even_if_reviewed_now(self) -> None:
        report = _report()
        report["collected_at"] = "2026-08-24T11:30:00Z"

        with self.assertRaisesRegex(DeploymentEvidenceError, "stale"):
            self._review(report)

    def test_worker_future_skew_accepts_five_seconds_and_rejects_more(self) -> None:
        collected_at = datetime(2026, 8, 26, 11, 30, tzinfo=timezone.utc)
        for offset, accepted in ((5.0, True), (5.001, False)):
            with self.subTest(offset=offset):
                report = _report()
                checks = report["checks"]
                assert isinstance(checks, list)
                worker = next(item for item in checks if item["name"] == "unattended_workers")
                task = worker["tasks"]["alerts-refresh"]
                success = collected_at + timedelta(seconds=offset)
                started = success - timedelta(seconds=2)
                attempted = success - timedelta(seconds=1)
                task.update(
                    {
                        "last_started_at": _iso(started),
                        "last_started_at_unix_seconds": started.timestamp(),
                        "last_attempt_at": _iso(attempted),
                        "last_attempt_at_unix_seconds": attempted.timestamp(),
                        "last_finished_at": _iso(success),
                        "last_finished_at_unix_seconds": success.timestamp(),
                        "last_success_at": _iso(success),
                        "last_success_at_unix_seconds": success.timestamp(),
                        "freshness_age_seconds": -offset,
                    }
                )
                worker["state_updated_at"] = _iso(success)
                worker["state_updated_at_unix_seconds"] = success.timestamp()
                service_name = "market-sentinel-alerts-refresh.service"
                service = worker["services"][service_name]
                completed = success - timedelta(seconds=2)
                completed_text = completed.strftime("%a %Y-%m-%d %H:%M:%S.%f UTC")
                service.update(
                    {
                        "completed_at": completed_text,
                        "completed_at_unix_seconds": completed.timestamp(),
                        "age_seconds": (collected_at - completed).total_seconds(),
                    }
                )
                recent = next(
                    item
                    for item in checks
                    if item["name"] == f"systemd_recent_success_{service_name}"
                )
                recent.update(
                    {
                        "completed_at": completed_text,
                        "completed_at_unix_seconds": completed.timestamp(),
                        "age_seconds": (collected_at - completed).total_seconds(),
                    }
                )
                if accepted:
                    self.assertEqual(self._review(report)["status"], "ok")
                else:
                    with self.assertRaises(DeploymentEvidenceError):
                        self._review(report)

    def test_rejects_source_or_runtime_identity_tampering(self) -> None:
        mutations = (
            ("source", "git_revision", "d" * 40),
            ("collection", "expected_source_revision", "d" * 40),
        )
        for section, field, value in mutations:
            with self.subTest(section=section, field=field):
                report = _report()
                payload = report[section]
                assert isinstance(payload, dict)
                payload[field] = value
                with self.assertRaises(DeploymentEvidenceError):
                    self._review(report)

        report = _report()
        checks = report["checks"]
        assert isinstance(checks, list)
        loopback = next(check for check in checks if check["name"] == "loopback_health")
        loopback["runtime_frontend_sha256"] = "d" * 64
        with self.assertRaisesRegex(DeploymentEvidenceError, "frontend"):
            self._review(report)

    def test_rejects_missing_unknown_duplicate_or_failed_checks(self) -> None:
        base = _report()
        checks = base["checks"]
        assert isinstance(checks, list)

        missing = copy.deepcopy(base)
        missing_checks = missing["checks"]
        assert isinstance(missing_checks, list)
        missing_checks.pop()

        unknown = copy.deepcopy(base)
        unknown_checks = unknown["checks"]
        assert isinstance(unknown_checks, list)
        unknown_checks.append({"name": "self_asserted_extra", "status": "pass"})

        duplicate = copy.deepcopy(base)
        duplicate_checks = duplicate["checks"]
        assert isinstance(duplicate_checks, list)
        duplicate_checks.append(copy.deepcopy(duplicate_checks[0]))

        failed = copy.deepcopy(base)
        failed_checks = failed["checks"]
        assert isinstance(failed_checks, list)
        failed_checks[0]["status"] = "fail"

        for label, report in (("missing", missing), ("unknown", unknown), ("duplicate", duplicate), ("failed", failed)):
            with self.subTest(label=label), self.assertRaises(DeploymentEvidenceError):
                self._review(report)

    def test_rejects_forged_startup_preflight_or_timer_contract_evidence(self) -> None:
        mutations = (
            (
                "missing strict doctor flag",
                "web_startup_preflight",
                lambda check: check["commands"][-1].remove("--strict"),
            ),
            (
                "preflight count mismatch",
                "web_startup_preflight",
                lambda check: check.update(command_count=4),
            ),
            (
                "preflight never completed",
                "web_startup_preflight",
                lambda check: check.update(commands_succeeded=False),
            ),
            (
                "timer target mismatch",
                "systemd_timer_contracts",
                lambda check: check["timers"]["market-sentinel-health.timer"].update(
                    unit="market-sentinel-web.service"
                ),
            ),
            (
                "timer persistence type confusion",
                "systemd_timer_contracts",
                lambda check: check["timers"]["market-sentinel-backup.timer"].update(persistent=1),
            ),
            (
                "timer count mismatch",
                "systemd_timer_contracts",
                lambda check: check.update(timer_count=1),
            ),
        )
        for label, check_name, mutate in mutations:
            with self.subTest(label=label):
                report = _report()
                checks = report["checks"]
                assert isinstance(checks, list)
                check = next(item for item in checks if item["name"] == check_name)
                mutate(check)
                with self.assertRaises(DeploymentEvidenceError):
                    self._review(report)

    def test_rejects_forged_or_unsafe_unattended_worker_service_evidence(self) -> None:
        alerts_service = "market-sentinel-alerts-refresh.service"
        mutations = (
            (
                "shell wrapped command",
                lambda check: check["service_contracts"][alerts_service].update(
                    exec_start=["/bin/sh", "-c", "python -m core.unattended_worker"]
                ),
            ),
            (
                "weakened sandbox",
                lambda check: check["service_contracts"][alerts_service]["properties"].update(
                    NoNewPrivileges="no"
                ),
            ),
            (
                "privileged environment",
                lambda check: check.update(environment_file="/etc/market-sentinel/market-sentinel.env"),
            ),
            (
                "disallowed environment key",
                lambda check: check.update(environment_keys=["PRIVATE_KEY"]),
            ),
            (
                "forged systemd completion",
                lambda check: check["services"][alerts_service].update(
                    completed_at_unix_seconds=check["services"][alerts_service][
                        "completed_at_unix_seconds"
                    ]
                    + 1
                ),
            ),
        )
        for label, mutate in mutations:
            with self.subTest(label=label):
                report = _report()
                checks = report["checks"]
                assert isinstance(checks, list)
                worker = next(item for item in checks if item["name"] == "unattended_workers")
                mutate(worker)
                with self.assertRaises(DeploymentEvidenceError):
                    self._review(report)

    def test_rejects_partial_stale_failed_or_type_confused_worker_state_evidence(self) -> None:
        def remove_task(check: dict[str, object]) -> None:
            check["tasks"].pop("wallets-poll")

        def stale_task(check: dict[str, object]) -> None:
            check["tasks"]["alerts-refresh"]["freshness_age_seconds"] = 10_000

        def failed_task(check: dict[str, object]) -> None:
            check["tasks"]["alerts-refresh"]["state"] = "failed"

        def stringified_problem_count(check: dict[str, object]) -> None:
            check["tasks"]["alerts-refresh"]["problems"] = "0"

        def non_v4_run_id(check: dict[str, object]) -> None:
            check["tasks"]["alerts-refresh"]["run_id"] = "00000000-0000-1000-8000-000000000201"

        def nonfinite_timestamp(check: dict[str, object]) -> None:
            check["tasks"]["alerts-refresh"]["last_success_at_unix_seconds"] = float("inf")

        def future_timestamp(check: dict[str, object]) -> None:
            future = datetime(2026, 8, 26, 12, 30, tzinfo=timezone.utc)
            task = check["tasks"]["alerts-refresh"]
            task["last_success_at"] = _iso(future)
            task["last_success_at_unix_seconds"] = future.timestamp()

        def stale_source_revision(check: dict[str, object]) -> None:
            check["tasks"]["alerts-refresh"]["source_revision"] = "d" * 40

        def invocation_mismatch(check: dict[str, object]) -> None:
            check["tasks"]["alerts-refresh"]["invocation_sha256"] = "0" * 64

        def zero_processed(check: dict[str, object]) -> None:
            task = check["tasks"]["alerts-refresh"]
            task["last_attempt_processed"] = 0
            task["processed"] = 0

        for label, mutate in (
            ("missing task", remove_task),
            ("stale task", stale_task),
            ("failed task", failed_task),
            ("stringified problem count", stringified_problem_count),
            ("non-v4 run id", non_v4_run_id),
            ("nonfinite timestamp", nonfinite_timestamp),
            ("future timestamp", future_timestamp),
            ("stale prior revision", stale_source_revision),
            ("invocation mismatch", invocation_mismatch),
            ("zero processed", zero_processed),
        ):
            with self.subTest(label=label):
                report = _report()
                checks = report["checks"]
                assert isinstance(checks, list)
                worker = next(item for item in checks if item["name"] == "unattended_workers")
                mutate(worker)
                with self.assertRaises(DeploymentEvidenceError):
                    self._review(report)

    def test_rejects_stale_isolated_health_probe(self) -> None:
        report = _report()
        checks = report["checks"]
        assert isinstance(checks, list)
        recent = next(
            check
            for check in checks
            if check["name"] == "systemd_recent_success_market-sentinel-health.service"
        )
        completed = NOW - timedelta(minutes=10)
        recent["completed_at_unix_seconds"] = completed.timestamp()
        recent["age_seconds"] = 10 * 60

        with self.assertRaisesRegex(DeploymentEvidenceError, "observability-token probe"):
            self._review(report)

    def test_rejects_stale_or_inconsistent_backup(self) -> None:
        report = _report()
        checks = report["checks"]
        assert isinstance(checks, list)
        backup = next(check for check in checks if check["name"] == "verified_recent_state_backup")
        backup["created_at"] = (NOW - timedelta(days=2)).isoformat().replace("+00:00", "Z")

        with self.assertRaisesRegex(DeploymentEvidenceError, "backup"):
            self._review(report)

    def test_rejects_handwritten_wrapper_manifest(self) -> None:
        wrapper = {
            "schema_version": 1,
            "verified": True,
            "reviewed_at": "2026-08-26T12:00:00Z",
            "source_revision": REVISION,
            "checks": [{"name": name, "status": "pass"} for name in sorted(required_check_names())],
        }

        with self.assertRaises(DeploymentEvidenceError):
            self._review(wrapper)

    def test_rejects_duplicate_keys_and_nonfinite_numbers(self) -> None:
        valid = json.dumps(_report(), sort_keys=True)
        duplicate = valid.replace('"schema_version": 1', '"schema_version": 1, "schema_version": 1', 1)
        nonfinite = valid.replace('"schema_version": 1', '"schema_version": NaN', 1)

        for label, text in (("duplicate", duplicate), ("nonfinite", nonfinite)):
            with self.subTest(label=label), self.assertRaises(DeploymentEvidenceError):
                self._review_text(text)


if __name__ == "__main__":
    unittest.main()
