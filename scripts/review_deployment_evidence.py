from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    from scripts.verify_production_deployment import (
        ALLOWED_WORKER_ENVIRONMENT_KEYS,
        BACKUP_MAX_AGE_SECONDS,
        BACKUP_MAX_FUTURE_SKEW_SECONDS,
        DEFAULT_WORKER_ENVIRONMENT_PATH,
        DEFAULT_WORKER_LOCK_PATH,
        DEFAULT_WORKER_STATE_PATH,
        DURABLE_STATE_PATHS,
        EVIDENCE_SCHEMA_VERSION,
        EXTERNAL_PROBE_REPORT_TYPE,
        HEALTH_CHECK_MAX_AGE_SECONDS,
        PUBLIC_PROXY_AUTH_PROBES,
        PROVIDER_SLUG,
        REQUIRED_HEALTH_SERVICE_PROPERTIES,
        REQUIRED_UNATTENDED_SERVICE_CONTRACTS,
        REQUIRED_WORKER_UNIT_CONTRACT_SHA256,
        REQUIRED_SYSTEMD_TIMER_CONTRACTS,
        REQUIRED_WEB_EXEC_START_PRE_COMMANDS,
        ROLLBACK_DRILL_STEPS,
        REQUIRED_UNITS,
        UNATTENDED_WORKER_COMPLETION_SKEW_SECONDS,
        UNATTENDED_WORKER_SERVICES,
        UNATTENDED_WORKER_TASK_MAX_AGE_SECONDS,
        UNATTENDED_WORKER_MAX_FUTURE_SKEW_SECONDS,
        worker_invocation_sha256,
    )
    from scripts.verify_restored_state import application_check_valid
except ModuleNotFoundError:  # Direct execution adds scripts/ to sys.path.
    from verify_production_deployment import (  # type: ignore[no-redef]
        ALLOWED_WORKER_ENVIRONMENT_KEYS,
        BACKUP_MAX_AGE_SECONDS,
        BACKUP_MAX_FUTURE_SKEW_SECONDS,
        DEFAULT_WORKER_ENVIRONMENT_PATH,
        DEFAULT_WORKER_LOCK_PATH,
        DEFAULT_WORKER_STATE_PATH,
        DURABLE_STATE_PATHS,
        EVIDENCE_SCHEMA_VERSION,
        EXTERNAL_PROBE_REPORT_TYPE,
        HEALTH_CHECK_MAX_AGE_SECONDS,
        PUBLIC_PROXY_AUTH_PROBES,
        PROVIDER_SLUG,
        REQUIRED_HEALTH_SERVICE_PROPERTIES,
        REQUIRED_UNATTENDED_SERVICE_CONTRACTS,
        REQUIRED_WORKER_UNIT_CONTRACT_SHA256,
        REQUIRED_SYSTEMD_TIMER_CONTRACTS,
        REQUIRED_WEB_EXEC_START_PRE_COMMANDS,
        ROLLBACK_DRILL_STEPS,
        REQUIRED_UNITS,
        UNATTENDED_WORKER_COMPLETION_SKEW_SECONDS,
        UNATTENDED_WORKER_SERVICES,
        UNATTENDED_WORKER_TASK_MAX_AGE_SECONDS,
        UNATTENDED_WORKER_MAX_FUTURE_SKEW_SECONDS,
        worker_invocation_sha256,
    )
    from verify_restored_state import application_check_valid

from core.deployment_identity import canonical_https_origin


MAX_REPORT_BYTES = 1024 * 1024
MAX_REPORT_AGE_SECONDS = 24 * 60 * 60
MAX_REPORT_FUTURE_SKEW_SECONDS = 5 * 60
COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")
SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")


class DeploymentEvidenceError(ValueError):
    """Raised when a deployment report cannot prove the production gate."""


def required_check_names() -> frozenset[str]:
    systemd = {
        f"systemd_{command}_{unit}"
        for unit in REQUIRED_UNITS
        for command in ("is-active", "is-enabled")
    }
    return frozenset(
        {
            "source_revision",
            *systemd,
            "systemd_recent_success_market-sentinel-health.service",
            "systemd_recent_success_market-sentinel-backup.service",
            "systemd_recent_success_market-sentinel-alerts-refresh.service",
            "systemd_recent_success_market-sentinel-wallets-poll.service",
            "filesystem_private_market-sentinel.env",
            "filesystem_private_market-sentinel-health.env",
            "filesystem_private_market-sentinel-worker.env",
            "filesystem_private_market-sentinel",
            "filesystem_private_market-sentinel-backups",
            "health_credential_isolation",
            "web_startup_preflight",
            "systemd_timer_contracts",
            "durable_state_wiring",
            "unattended_workers",
            "verified_recent_state_backup",
            "deployment_host_identity",
            "verified_restore_drill",
            "verified_production_rollback_drill",
            "loopback_health",
            "loopback_metrics",
            "loopback_token_auth",
            "public_https_proxy",
            "source_revision_final",
            "evidence_output_directory",
            "source_revision_pre_write",
        }
    )


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DeploymentEvidenceError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_nonfinite(value: str) -> None:
    raise DeploymentEvidenceError(f"non-finite JSON number: {value}")


def _read_report(path: Path) -> tuple[dict[str, Any], str]:
    if path.is_symlink() or not path.is_file():
        raise DeploymentEvidenceError("deployment evidence must be a regular, non-symbolic-link file")
    size = path.stat().st_size
    if size <= 0 or size > MAX_REPORT_BYTES:
        raise DeploymentEvidenceError(
            f"deployment evidence size must be between 1 and {MAX_REPORT_BYTES} bytes"
        )
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DeploymentEvidenceError("deployment evidence must be UTF-8 JSON") from exc
    try:
        payload = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonfinite,
        )
    except json.JSONDecodeError as exc:
        raise DeploymentEvidenceError(f"invalid deployment evidence JSON: {exc.msg}") from exc
    if not isinstance(payload, dict):
        raise DeploymentEvidenceError("deployment evidence must be a JSON object")
    return payload, hashlib.sha256(raw).hexdigest()


def _utc_timestamp(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise DeploymentEvidenceError(f"{label} must be a non-empty UTC timestamp")
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise DeploymentEvidenceError(f"{label} is not a valid ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise DeploymentEvidenceError(f"{label} must include a UTC offset")
    return parsed.astimezone(timezone.utc)


def _require_exact_keys(payload: dict[str, Any], expected: set[str], label: str) -> None:
    actual = set(payload)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        details: list[str] = []
        if missing:
            details.append("missing=" + ",".join(missing))
        if unknown:
            details.append("unknown=" + ",".join(unknown))
        raise DeploymentEvidenceError(f"{label} fields are not exact ({'; '.join(details)})")


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise DeploymentEvidenceError(f"{label} must be a positive integer")
    return value


def _finite_number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DeploymentEvidenceError(f"{label} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise DeploymentEvidenceError(f"{label} must be a finite number")
    return result


def _nonnegative_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise DeploymentEvidenceError(f"{label} must be a non-negative integer")
    return value


def _canonical_uuid4(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise DeploymentEvidenceError(f"{label} must be a canonical UUIDv4")
    try:
        parsed = uuid.UUID(value)
    except ValueError as exc:
        raise DeploymentEvidenceError(f"{label} must be a canonical UUIDv4") from exc
    if str(parsed) != value or parsed.version != 4:
        raise DeploymentEvidenceError(f"{label} must be a canonical UUIDv4")
    return value


def _timestamp_pair(
    payload: dict[str, Any],
    text_key: str,
    unix_key: str,
    label: str,
) -> tuple[str, float]:
    parsed = _utc_timestamp(payload.get(text_key), f"{label} {text_key}")
    unix_seconds = _finite_number(payload.get(unix_key), f"{label} {unix_key}")
    if abs(parsed.timestamp() - unix_seconds) > 1:
        raise DeploymentEvidenceError(f"{label} timestamp representations disagree")
    return payload[text_key], unix_seconds


def _systemd_utc_timestamp(value: Any, label: str) -> float:
    if not isinstance(value, str) or not value.strip():
        raise DeploymentEvidenceError(f"{label} must be a systemd UTC timestamp")
    for pattern in ("%a %Y-%m-%d %H:%M:%S UTC", "%a %Y-%m-%d %H:%M:%S.%f UTC"):
        try:
            return datetime.strptime(value.strip(), pattern).replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            continue
    raise DeploymentEvidenceError(f"{label} must be a systemd UTC timestamp")


def _canonical_origin(value: str) -> str:
    try:
        return canonical_https_origin(value)
    except ValueError as exc:
        raise DeploymentEvidenceError(str(exc)) from exc


def review_external_probe_report(
    path: Path,
    *,
    expected_version: str,
    expected_revision: str,
    expected_frontend_sha256: str,
    expected_origin: str,
    expected_run_id: int,
    expected_run_attempt: int,
    expected_nonce: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Validate the independently collected GitHub-hosted public deployment probe."""

    report, report_sha256 = _read_report(Path(path))
    _require_exact_keys(
        report,
        {"schema_version", "report_type", "probed_at", "status", "source_revision", "collection", "checks"},
        "external deployment probe",
    )
    revision = expected_revision.strip().lower()
    frontend_sha256 = expected_frontend_sha256.strip().lower()
    origin = _canonical_origin(expected_origin)
    if (
        report.get("schema_version") != 1
        or report.get("report_type") != EXTERNAL_PROBE_REPORT_TYPE
        or report.get("status") != "ok"
        or report.get("source_revision") != revision
        or not COMMIT_SHA.fullmatch(revision)
        or not SHA256_HEX.fullmatch(frontend_sha256)
    ):
        raise DeploymentEvidenceError("external deployment probe identity is invalid")
    observed_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    probed_at = _utc_timestamp(report.get("probed_at"), "external probed_at")
    age_seconds = (observed_at - probed_at).total_seconds()
    if age_seconds < -MAX_REPORT_FUTURE_SKEW_SECONDS or age_seconds > MAX_REPORT_AGE_SECONDS:
        raise DeploymentEvidenceError("external deployment probe is stale or future-dated")
    collection = report.get("collection")
    if not isinstance(collection, dict):
        raise DeploymentEvidenceError("external deployment probe collection must be an object")
    _require_exact_keys(
        collection,
        {
            "mode",
            "public_origin",
            "expected_version",
            "expected_source_revision",
            "expected_frontend_sha256",
            "run_id",
            "run_attempt",
            "nonce",
            "runner_environment",
        },
        "external deployment probe collection",
    )
    expected_collection = {
        "mode": "github_hosted_external_public_probe",
        "public_origin": origin,
        "expected_version": expected_version,
        "expected_source_revision": revision,
        "expected_frontend_sha256": frontend_sha256,
        "run_id": expected_run_id,
        "run_attempt": expected_run_attempt,
        "nonce": expected_nonce,
        "runner_environment": "github-hosted",
    }
    if any(collection.get(key) != value for key, value in expected_collection.items()):
        raise DeploymentEvidenceError("external deployment probe is not bound to the exact workflow run")
    checks = report.get("checks")
    if not isinstance(checks, list) or len(checks) != 1 or not isinstance(checks[0], dict):
        raise DeploymentEvidenceError("external deployment probe must contain exactly one public check")
    check = checks[0]
    if (
        set(check) != {
            "name",
            "status",
            "api_version",
            "runtime_source_revision",
            "runtime_frontend_sha256",
            "unauthenticated_probes",
        }
        or check.get("name") != "public_https_proxy"
        or check.get("status") != "pass"
        or check.get("api_version") != expected_version
        or check.get("runtime_source_revision") != revision
        or check.get("runtime_frontend_sha256") != frontend_sha256
        or check.get("unauthenticated_probes") != len(PUBLIC_PROXY_AUTH_PROBES)
    ):
        raise DeploymentEvidenceError("external public proxy result is incomplete or misbound")
    return {
        "status": "ok",
        "probed_at": report["probed_at"],
        "report_sha256": report_sha256,
        "public_origin": origin,
        "api_version": expected_version,
        "source_revision": revision,
        "frontend_sha256": frontend_sha256,
        "unauthenticated_probes": len(PUBLIC_PROXY_AUTH_PROBES),
    }


def _review_unattended_workers(
    check: dict[str, Any],
    indexed: dict[str, dict[str, Any]],
    *,
    collected_at: datetime,
    expected_revision: str,
) -> dict[str, Any]:
    """Validate the worker collector's normalized, secret-free raw evidence."""
    _require_exact_keys(
        check,
        {
            "name",
            "status",
            "detail",
            "state_file",
            "state_schema_version",
            "state_updated_at",
            "state_updated_at_unix_seconds",
            "state_sha256",
            "lock_file",
            "environment_file",
            "environment_keys",
            "expected_service_count",
            "service_count",
            "service_contracts",
            "services",
            "tasks",
        },
        "unattended_workers",
    )
    if (
        check["name"] != "unattended_workers"
        or check["status"] != "pass"
        or not isinstance(check["detail"], str)
        or not check["detail"].strip()
        or check["state_file"] != DEFAULT_WORKER_STATE_PATH.as_posix()
        or check["lock_file"] != DEFAULT_WORKER_LOCK_PATH.as_posix()
        or check["environment_file"] != DEFAULT_WORKER_ENVIRONMENT_PATH.as_posix()
        or isinstance(check["state_schema_version"], bool)
        or check["state_schema_version"] != 1
        or not isinstance(check["state_sha256"], str)
        or SHA256_HEX.fullmatch(check["state_sha256"]) is None
    ):
        raise DeploymentEvidenceError("unattended worker paths or durable state identity are invalid")

    environment_keys = check["environment_keys"]
    if (
        not isinstance(environment_keys, list)
        or any(not isinstance(key, str) for key in environment_keys)
        or environment_keys != sorted(set(environment_keys))
        or not set(environment_keys).issubset(ALLOWED_WORKER_ENVIRONMENT_KEYS)
    ):
        raise DeploymentEvidenceError("unattended worker environment key inventory is unsafe")

    expected_service_count = len(UNATTENDED_WORKER_SERVICES)
    if (
        _positive_int(check["expected_service_count"], "worker expected_service_count")
        != expected_service_count
        or _positive_int(check["service_count"], "worker service_count")
        != expected_service_count
        or json.dumps(check["service_contracts"], sort_keys=True, separators=(",", ":"))
        != json.dumps(REQUIRED_UNATTENDED_SERVICE_CONTRACTS, sort_keys=True, separators=(",", ":"))
    ):
        raise DeploymentEvidenceError(
            "unattended worker services do not attest the exact commands, credential stripping, and sandbox"
        )

    state_updated_at_text, state_updated_at = _timestamp_pair(
        check,
        "state_updated_at",
        "state_updated_at_unix_seconds",
        "worker state",
    )
    collection_seconds = collected_at.timestamp()
    if state_updated_at > collection_seconds + UNATTENDED_WORKER_MAX_FUTURE_SKEW_SECONDS:
        raise DeploymentEvidenceError("unattended worker state is implausibly future-dated")

    services = check["services"]
    tasks = check["tasks"]
    if not isinstance(services, dict) or set(services) != set(UNATTENDED_WORKER_SERVICES):
        raise DeploymentEvidenceError("unattended worker service evidence is incomplete")
    if not isinstance(tasks, dict) or set(tasks) != set(UNATTENDED_WORKER_TASK_MAX_AGE_SECONDS):
        raise DeploymentEvidenceError("unattended worker task evidence is incomplete")

    raw_task_keys = {
        "service",
        "timer",
        "state",
        "run_id",
        "last_started_at",
        "last_started_at_unix_seconds",
        "last_attempt_at",
        "last_attempt_at_unix_seconds",
        "last_finished_at",
        "last_finished_at_unix_seconds",
        "last_success_at",
        "last_success_at_unix_seconds",
        "last_duration_seconds",
        "deadline_seconds",
        "max_attempts",
        "attempts_completed",
        "last_attempt_outcome",
        "last_outcome",
        "last_attempt_processed",
        "last_attempt_problems",
        "last_attempt_emitted",
        "processed",
        "problems",
        "emitted",
        "consecutive_failures",
        "abandoned_runs",
        "total_runs",
        "total_successes",
        "total_failures",
        "freshness_age_seconds",
        "max_age_seconds",
        "source_revision",
        "service_unit",
        "unit_contract_sha256",
        "invocation_sha256",
    }
    raw_service_keys = {
        "task",
        "timer",
        "completed_at",
        "completed_at_unix_seconds",
        "age_seconds",
        "max_age_seconds",
        "source_revision",
        "unit_contract_sha256",
    }
    reviewed_services: dict[str, dict[str, Any]] = {}
    reviewed_tasks: dict[str, dict[str, Any]] = {}
    finished_times: list[float] = []
    for service, identity in UNATTENDED_WORKER_SERVICES.items():
        task = identity["task"]
        task_evidence = tasks[task]
        service_evidence = services[service]
        if not isinstance(task_evidence, dict) or not isinstance(service_evidence, dict):
            raise DeploymentEvidenceError("unattended worker task and service entries must be objects")
        _require_exact_keys(task_evidence, raw_task_keys, f"unattended worker {task}")
        _require_exact_keys(service_evidence, raw_service_keys, f"unattended worker {service}")
        if (
            task_evidence["service"] != service
            or task_evidence["timer"] != identity["timer"]
            or service_evidence["task"] != task
            or service_evidence["timer"] != identity["timer"]
            or task_evidence["state"] != "succeeded"
            or task_evidence["last_outcome"] != "succeeded"
            or task_evidence["last_attempt_outcome"] != "succeeded"
        ):
            raise DeploymentEvidenceError(f"unattended worker {task} identity or outcome is invalid")
        expected_contract_sha256 = REQUIRED_WORKER_UNIT_CONTRACT_SHA256[service]
        expected_invocation_sha256 = worker_invocation_sha256(
            task=task,
            service_unit=service,
            source_revision=expected_revision,
            unit_contract_sha256=expected_contract_sha256,
        )
        if (
            task_evidence["source_revision"] != expected_revision
            or task_evidence["service_unit"] != service
            or task_evidence["unit_contract_sha256"] != expected_contract_sha256
            or task_evidence["invocation_sha256"] != expected_invocation_sha256
            or service_evidence["source_revision"] != expected_revision
            or service_evidence["unit_contract_sha256"] != expected_contract_sha256
        ):
            raise DeploymentEvidenceError(
                f"unattended worker {task} is not bound to the reviewed revision and unit invocation"
            )

        run_id = _canonical_uuid4(task_evidence["run_id"], f"unattended worker {task} run_id")
        started_text, started_at = _timestamp_pair(
            task_evidence,
            "last_started_at",
            "last_started_at_unix_seconds",
            f"unattended worker {task}",
        )
        del started_text
        attempted_text, attempted_at = _timestamp_pair(
            task_evidence,
            "last_attempt_at",
            "last_attempt_at_unix_seconds",
            f"unattended worker {task}",
        )
        del attempted_text
        finished_text, finished_at = _timestamp_pair(
            task_evidence,
            "last_finished_at",
            "last_finished_at_unix_seconds",
            f"unattended worker {task}",
        )
        success_text, success_at = _timestamp_pair(
            task_evidence,
            "last_success_at",
            "last_success_at_unix_seconds",
            f"unattended worker {task}",
        )
        duration = _finite_number(
            task_evidence["last_duration_seconds"],
            f"unattended worker {task} last_duration_seconds",
        )
        deadline = _finite_number(
            task_evidence["deadline_seconds"],
            f"unattended worker {task} deadline_seconds",
        )
        max_attempts = _positive_int(
            task_evidence["max_attempts"], f"unattended worker {task} max_attempts"
        )
        attempts_completed = _positive_int(
            task_evidence["attempts_completed"],
            f"unattended worker {task} attempts_completed",
        )
        if (
            duration < 0
            or deadline != 90
            or max_attempts != 3
            or attempts_completed > max_attempts
            or started_at > attempted_at + 1
            or attempted_at > finished_at + 1
            or abs(finished_at - success_at) > 1
            or abs((finished_at - started_at) - duration) > 2
        ):
            raise DeploymentEvidenceError(f"unattended worker {task} timing or retry telemetry is invalid")

        counters = {
            key: _nonnegative_int(
                task_evidence[key],
                f"unattended worker {task} {key}",
            )
            for key in (
                "last_attempt_processed",
                "last_attempt_problems",
                "last_attempt_emitted",
                "processed",
                "problems",
                "emitted",
                "consecutive_failures",
                "abandoned_runs",
                "total_runs",
                "total_successes",
                "total_failures",
            )
        }
        if (
            counters["last_attempt_problems"] != 0
            or counters["problems"] != 0
            or counters["consecutive_failures"] != 0
            or counters["last_attempt_processed"] != counters["processed"]
            or counters["processed"] < 1
            or counters["last_attempt_emitted"] != counters["emitted"]
            or counters["total_runs"] < 1
            or counters["total_successes"] < 1
            or counters["total_successes"] + counters["total_failures"] != counters["total_runs"]
        ):
            raise DeploymentEvidenceError(f"unattended worker {task} success counters are invalid")

        max_age = UNATTENDED_WORKER_TASK_MAX_AGE_SECONDS[task]
        freshness_age = _finite_number(
            task_evidence["freshness_age_seconds"],
            f"unattended worker {task} freshness_age_seconds",
        )
        collected_freshness_age = collection_seconds - success_at
        if (
            isinstance(task_evidence["max_age_seconds"], bool)
            or task_evidence["max_age_seconds"] != max_age
            or freshness_age < -UNATTENDED_WORKER_MAX_FUTURE_SKEW_SECONDS
            or freshness_age > max_age
            or collected_freshness_age < -UNATTENDED_WORKER_MAX_FUTURE_SKEW_SECONDS
            or collected_freshness_age > max_age
        ):
            raise DeploymentEvidenceError(f"unattended worker {task} freshness is invalid")

        completed_text = service_evidence["completed_at"]
        completed_at = _finite_number(
            service_evidence["completed_at_unix_seconds"],
            f"unattended worker {service} completed_at_unix_seconds",
        )
        if abs(_systemd_utc_timestamp(completed_text, f"unattended worker {service} completed_at") - completed_at) > 1:
            raise DeploymentEvidenceError(f"unattended worker {service} completion timestamps disagree")
        service_age = _finite_number(
            service_evidence["age_seconds"],
            f"unattended worker {service} age_seconds",
        )
        collected_service_age = collection_seconds - completed_at
        if (
            isinstance(service_evidence["max_age_seconds"], bool)
            or service_evidence["max_age_seconds"] != max_age
            or service_age < -UNATTENDED_WORKER_MAX_FUTURE_SKEW_SECONDS
            or service_age > max_age
            or collected_service_age < -UNATTENDED_WORKER_MAX_FUTURE_SKEW_SECONDS
            or collected_service_age > max_age
            or completed_at - finished_at < -2
            or completed_at - finished_at > UNATTENDED_WORKER_COMPLETION_SKEW_SECONDS
        ):
            raise DeploymentEvidenceError(
                f"unattended worker {service} completion is inconsistent with its durable state"
            )

        recent = indexed[f"systemd_recent_success_{service}"]
        _require_exact_keys(
            recent,
            {
                "name",
                "status",
                "detail",
                "unit",
                "completed_at",
                "completed_at_unix_seconds",
                "age_seconds",
                "max_age_seconds",
            },
            f"recent unattended worker {service}",
        )
        recent_completed_at = _finite_number(
            recent.get("completed_at_unix_seconds"),
            f"recent {service} completed_at_unix_seconds",
        )
        recent_age = _finite_number(recent.get("age_seconds"), f"recent {service} age_seconds")
        if (
            recent.get("unit") != service
            or recent.get("completed_at") != completed_text
            or abs(recent_completed_at - completed_at) > 0.001
            or abs(recent_age - service_age) > 0.001
            or isinstance(recent.get("max_age_seconds"), bool)
            or recent.get("max_age_seconds") != max_age
        ):
            raise DeploymentEvidenceError(
                f"unattended worker {service} summary is not bound to the raw systemd check"
            )

        reviewed_services[service] = {
            "task": task,
            "timer": identity["timer"],
            "completed_at": completed_text,
            "completed_at_unix_seconds": completed_at,
            "source_revision": expected_revision,
            "unit_contract_sha256": expected_contract_sha256,
        }
        reviewed_tasks[task] = {
            "service": service,
            "timer": identity["timer"],
            "run_id": run_id,
            "source_revision": expected_revision,
            "service_unit": service,
            "unit_contract_sha256": expected_contract_sha256,
            "invocation_sha256": expected_invocation_sha256,
            "last_success_at": success_text,
            "last_success_at_unix_seconds": success_at,
            "freshness_age_seconds": round(collected_freshness_age, 6),
            "max_age_seconds": max_age,
            "attempts_completed": attempts_completed,
            "processed": counters["processed"],
            "emitted": counters["emitted"],
            "total_runs": counters["total_runs"],
            "total_successes": counters["total_successes"],
            "total_failures": counters["total_failures"],
            "abandoned_runs": counters["abandoned_runs"],
        }
        finished_times.append(finished_at)
        del finished_text

    if abs(state_updated_at - max(finished_times)) > 1:
        raise DeploymentEvidenceError(
            "unattended worker state update does not identify the latest terminal task run"
        )

    return {
        "state_file": check["state_file"],
        "state_sha256": check["state_sha256"],
        "lock_file": check["lock_file"],
        "environment_file": check["environment_file"],
        "environment_keys": environment_keys,
        "services": reviewed_services,
        "tasks": reviewed_tasks,
    }


def review_deployment_report(
    path: Path,
    *,
    expected_version: str,
    expected_revision: str,
    expected_run_id: int | None = None,
    expected_run_attempt: int | None = None,
    expected_nonce: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Validate a raw real-host collector report without trusting a wrapper manifest."""

    expected_version = expected_version.strip()
    expected_revision = expected_revision.strip().lower()
    if not expected_version or "\n" in expected_version or "\r" in expected_version:
        raise DeploymentEvidenceError("expected version must be a non-empty single-line value")
    if not COMMIT_SHA.fullmatch(expected_revision):
        raise DeploymentEvidenceError("expected revision must be a lowercase 40-character Git SHA")

    report, report_sha256 = _read_report(Path(path))
    _require_exact_keys(
        report,
        {"schema_version", "collected_at", "source", "status", "checks", "collection"},
        "deployment evidence",
    )
    if report["schema_version"] != EVIDENCE_SCHEMA_VERSION or isinstance(report["schema_version"], bool):
        raise DeploymentEvidenceError(
            f"deployment evidence schema_version must equal {EVIDENCE_SCHEMA_VERSION}"
        )
    if report["status"] != "ok":
        raise DeploymentEvidenceError("deployment evidence status must equal ok")

    observed_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    collected_at = _utc_timestamp(report["collected_at"], "collected_at")
    age_seconds = (observed_at - collected_at).total_seconds()
    if age_seconds < -MAX_REPORT_FUTURE_SKEW_SECONDS or age_seconds > MAX_REPORT_AGE_SECONDS:
        raise DeploymentEvidenceError(
            "deployment evidence is stale or implausibly future-dated "
            f"(age_seconds={age_seconds:.0f})"
        )

    source = report["source"]
    if not isinstance(source, dict):
        raise DeploymentEvidenceError("source must be an object")
    _require_exact_keys(
        source,
        {"project_version", "git_revision", "git_revision_status", "git_worktree_status"},
        "source",
    )
    if source["project_version"] != expected_version:
        raise DeploymentEvidenceError("source project_version does not match the expected version")
    if source["git_revision"] != expected_revision:
        raise DeploymentEvidenceError("source git_revision does not match the expected revision")
    if source["git_revision_status"] != "ok" or source["git_worktree_status"] != "clean":
        raise DeploymentEvidenceError("source revision must be available and the deployed checkout clean")

    collection = report["collection"]
    if not isinstance(collection, dict):
        raise DeploymentEvidenceError("collection must be an object")
    _require_exact_keys(
        collection,
        {
            "mode",
            "systemd_requested",
            "public_proxy_requested",
            "public_origin",
            "expected_version",
            "expected_source_revision",
            "expected_frontend_sha256",
            "deployment_provider",
            "host_identity_sha256",
            "restore_drill_requested",
            "rollback_drill_requested",
            "run_id",
            "run_attempt",
            "nonce",
        },
        "collection",
    )
    if collection["mode"] != "production":
        raise DeploymentEvidenceError("collection mode must equal production")
    if collection["systemd_requested"] is not True:
        raise DeploymentEvidenceError("production evidence must request systemd checks")
    if collection["public_proxy_requested"] is not True:
        raise DeploymentEvidenceError("production evidence must request the public HTTPS proxy checks")
    if collection["restore_drill_requested"] is not True:
        raise DeploymentEvidenceError("production evidence must request an actual restore drill")
    if collection["rollback_drill_requested"] is not True:
        raise DeploymentEvidenceError("production evidence must request a production rollback drill")
    if not isinstance(collection["public_origin"], str) or not collection["public_origin"].startswith("https://"):
        raise DeploymentEvidenceError("production evidence must bind the exact public HTTPS origin")
    if collection["expected_version"] != expected_version:
        raise DeploymentEvidenceError("collection expected_version does not match")
    if collection["expected_source_revision"] != expected_revision:
        raise DeploymentEvidenceError("collection expected_source_revision does not match")
    if expected_run_id is not None and collection["run_id"] != expected_run_id:
        raise DeploymentEvidenceError("collection workflow run id does not match")
    if expected_run_attempt is not None and collection["run_attempt"] != expected_run_attempt:
        raise DeploymentEvidenceError("collection workflow run attempt does not match")
    if expected_nonce is not None and collection["nonce"] != expected_nonce:
        raise DeploymentEvidenceError("collection workflow nonce does not match")
    frontend_sha256 = collection["expected_frontend_sha256"]
    if not isinstance(frontend_sha256, str) or not SHA256_HEX.fullmatch(frontend_sha256):
        raise DeploymentEvidenceError("collection expected_frontend_sha256 must be a lowercase SHA-256")
    deployment_provider = collection["deployment_provider"]
    host_identity_sha256 = collection["host_identity_sha256"]
    if not isinstance(deployment_provider, str) or not PROVIDER_SLUG.fullmatch(deployment_provider):
        raise DeploymentEvidenceError("collection deployment_provider must be a lowercase provider slug")
    if not isinstance(host_identity_sha256, str) or not SHA256_HEX.fullmatch(host_identity_sha256):
        raise DeploymentEvidenceError("collection host_identity_sha256 must be a lowercase SHA-256")

    checks = report["checks"]
    if not isinstance(checks, list):
        raise DeploymentEvidenceError("checks must be a list")
    indexed: dict[str, dict[str, Any]] = {}
    for position, check in enumerate(checks):
        if not isinstance(check, dict):
            raise DeploymentEvidenceError(f"check {position} must be an object")
        name = check.get("name")
        if not isinstance(name, str) or not name:
            raise DeploymentEvidenceError(f"check {position} has an invalid name")
        if name in indexed:
            raise DeploymentEvidenceError(f"duplicate deployment check: {name}")
        if check.get("status") != "pass":
            raise DeploymentEvidenceError(f"deployment check did not pass: {name}")
        indexed[name] = check
    required = required_check_names()
    actual = set(indexed)
    if actual != set(required):
        missing = sorted(set(required) - actual)
        unknown = sorted(actual - set(required))
        details: list[str] = []
        if missing:
            details.append("missing=" + ",".join(missing))
        if unknown:
            details.append("unknown=" + ",".join(unknown))
        raise DeploymentEvidenceError(
            "deployment check inventory is not exact (" + "; ".join(details) + ")"
        )

    startup_preflight = indexed["web_startup_preflight"]
    expected_startup_commands = [list(command) for command in REQUIRED_WEB_EXEC_START_PRE_COMMANDS]
    if (
        _positive_int(
            startup_preflight.get("expected_command_count"),
            "web startup expected_command_count",
        )
        != len(expected_startup_commands)
        or _positive_int(startup_preflight.get("command_count"), "web startup command_count")
        != len(expected_startup_commands)
        or startup_preflight.get("commands") != expected_startup_commands
        or startup_preflight.get("commands_succeeded") is not True
    ):
        raise DeploymentEvidenceError(
            "web startup preflight does not attest the exact ordered path guards and strict doctor command"
        )

    timer_contracts = indexed["systemd_timer_contracts"]
    expected_timer_count = len(REQUIRED_SYSTEMD_TIMER_CONTRACTS)
    if (
        _positive_int(
            timer_contracts.get("expected_timer_count"),
            "systemd timer expected_timer_count",
        )
        != expected_timer_count
        or _positive_int(timer_contracts.get("timer_count"), "systemd timer timer_count")
        != expected_timer_count
        or json.dumps(timer_contracts.get("timers"), sort_keys=True, separators=(",", ":"))
        != json.dumps(REQUIRED_SYSTEMD_TIMER_CONTRACTS, sort_keys=True, separators=(",", ":"))
    ):
        raise DeploymentEvidenceError(
            "systemd timer evidence does not attest the exact effective targets and schedules"
        )

    health_service = "market-sentinel-health.service"
    recent_health = indexed[f"systemd_recent_success_{health_service}"]
    health_completed_at_seconds = _finite_number(
        recent_health.get("completed_at_unix_seconds"),
        f"{health_service} completed_at_unix_seconds",
    )
    observed_health_age_seconds = collected_at.timestamp() - health_completed_at_seconds
    reported_health_age_seconds = _finite_number(
        recent_health.get("age_seconds"),
        f"{health_service} age_seconds",
    )
    if (
        recent_health.get("unit") != health_service
        or _finite_number(recent_health.get("max_age_seconds"), f"{health_service} max_age_seconds")
        != HEALTH_CHECK_MAX_AGE_SECONDS
        or observed_health_age_seconds < -BACKUP_MAX_FUTURE_SKEW_SECONDS
        or observed_health_age_seconds > HEALTH_CHECK_MAX_AGE_SECONDS
        or abs(reported_health_age_seconds - observed_health_age_seconds) > 5
    ):
        raise DeploymentEvidenceError(
            "isolated health service does not have a recent successful observability-token probe"
        )

    unattended_workers = _review_unattended_workers(
        indexed["unattended_workers"],
        indexed,
        collected_at=collected_at,
        expected_revision=expected_revision,
    )

    loopback = indexed["loopback_health"]
    if loopback.get("api_version") != expected_version:
        raise DeploymentEvidenceError("loopback health version does not match")
    if loopback.get("runtime_source_revision") != expected_revision:
        raise DeploymentEvidenceError("loopback runtime revision does not match")
    if loopback.get("runtime_frontend_sha256") != frontend_sha256:
        raise DeploymentEvidenceError("loopback runtime frontend digest does not match")
    if loopback.get("disk_frontend_sha256") != frontend_sha256:
        raise DeploymentEvidenceError("on-disk frontend digest does not match")

    public_proxy = indexed["public_https_proxy"]
    if public_proxy.get("api_version") != expected_version:
        raise DeploymentEvidenceError("public proxy version does not match")
    if public_proxy.get("unauthenticated_probes") != len(PUBLIC_PROXY_AUTH_PROBES):
        raise DeploymentEvidenceError("public proxy did not run the complete unauthenticated probe set")
    if public_proxy.get("runtime_source_revision") != expected_revision:
        raise DeploymentEvidenceError("public proxy runtime revision does not match")
    if public_proxy.get("runtime_frontend_sha256") != frontend_sha256:
        raise DeploymentEvidenceError("public proxy runtime frontend digest does not match")

    host_identity = indexed["deployment_host_identity"]
    if (
        host_identity.get("deployment_provider") != deployment_provider
        or host_identity.get("host_identity_sha256") != host_identity_sha256
    ):
        raise DeploymentEvidenceError("deployment host identity is not bound to protected configuration")

    durable = indexed["durable_state_wiring"]
    if durable.get("durable_store_count") != len(DURABLE_STATE_PATHS):
        raise DeploymentEvidenceError("durable-state check does not cover every required store")
    if durable.get("state_directory") != "/var/lib/market-sentinel":
        raise DeploymentEvidenceError("durable-state directory is not the production path")
    if durable.get("backup_source") != "/var/lib/market-sentinel":
        raise DeploymentEvidenceError("backup source does not match the production state path")

    health_credentials = indexed["health_credential_isolation"]
    if health_credentials.get("environment_path") != "/etc/market-sentinel/market-sentinel-health.env":
        raise DeploymentEvidenceError("health credential environment is not the production path")
    if (
        health_credentials.get("service_user") != "market-sentinel-health"
        or health_credentials.get("service_group") != "market-sentinel-health"
        or health_credentials.get("private_user_namespace") is not True
        or health_credentials.get("process_visibility") != "invisible"
        or health_credentials.get("verified_service_property_count")
        != len(REQUIRED_HEALTH_SERVICE_PROPERTIES) + 1
    ):
        raise DeploymentEvidenceError("health probe is not isolated from the privileged web process")
    if health_credentials.get("environment_variable_count") != 1:
        raise DeploymentEvidenceError("health credential environment is not single-purpose")
    if health_credentials.get("admin_environment_unset") is not True:
        raise DeploymentEvidenceError("health service may inherit the admin API token")
    if health_credentials.get("token_preflight_removed") is not True:
        raise DeploymentEvidenceError("health service may expose its observer token through a pre-start command")
    if health_credentials.get("probe_requires_observability") is not True:
        raise DeploymentEvidenceError("health probe does not fail closed on its observer credential")
    if health_credentials.get("web_startup_probe_removed") is not True:
        raise DeploymentEvidenceError("web service still launches a probe with privileged credentials")
    if health_credentials.get("inline_environment_empty") is not True:
        raise DeploymentEvidenceError("health service has unexpected inline environment assignments")
    if health_credentials.get("manager_environment_not_passed") is not True:
        raise DeploymentEvidenceError("health service may inherit manager credential variables")

    backup = indexed["verified_recent_state_backup"]
    backup_created_at = _utc_timestamp(backup.get("created_at"), "backup created_at")
    backup_age_seconds = (observed_at - backup_created_at).total_seconds()
    if backup_age_seconds < -BACKUP_MAX_FUTURE_SKEW_SECONDS or backup_age_seconds > BACKUP_MAX_AGE_SECONDS:
        raise DeploymentEvidenceError("the verified backup is stale or future-dated at review time")
    if not isinstance(backup.get("sha256"), str) or not SHA256_HEX.fullmatch(backup["sha256"]):
        raise DeploymentEvidenceError("verified backup SHA-256 is invalid")
    _positive_int(backup.get("file_count"), "verified backup file_count")
    _positive_int(backup.get("verified_pairs"), "verified backup verified_pairs")
    if backup.get("invalid_pairs") != 0 or backup.get("orphan_archives") != 0 or backup.get("orphan_manifests") != 0:
        raise DeploymentEvidenceError("backup catalog contains invalid or orphaned artifacts")
    collected_backup_age_seconds = (collected_at - backup_created_at).total_seconds()
    if abs(
        _finite_number(backup.get("backup_age_seconds"), "backup_age_seconds")
        - collected_backup_age_seconds
    ) > 5:
        raise DeploymentEvidenceError("reported backup age is inconsistent with its timestamp")

    restore = indexed["verified_restore_drill"]
    restore_completed_at = _utc_timestamp(restore.get("completed_at"), "restore completed_at")
    if (
        restore.get("mode") != "isolated_full_restore"
        or not application_check_valid(
            restore.get("application"), version=expected_version,
            revision=expected_revision, frontend_sha256=frontend_sha256,
        )
        or restore.get("archive") != backup.get("archive")
        or restore.get("backup_created_at") != backup.get("created_at")
        or restore.get("backup_sha256") != backup.get("sha256")
        or restore.get("restored_file_count") != backup.get("file_count")
        or restore.get("restored_bytes") != backup.get("verified_bytes")
        or restore_completed_at < backup_created_at
        or restore_completed_at > observed_at + timedelta(seconds=MAX_REPORT_FUTURE_SKEW_SECONDS)
    ):
        raise DeploymentEvidenceError("restore drill is not bound to the complete reviewed backup")

    rollback = indexed["verified_production_rollback_drill"]
    rollback_completed_at = _utc_timestamp(rollback.get("completed_at"), "rollback completed_at")
    rollback_revision = rollback.get("rollback_revision")
    if (
        not isinstance(rollback.get("drill_id"), str)
        or not rollback["drill_id"].strip()
        or not isinstance(rollback.get("report_sha256"), str)
        or not SHA256_HEX.fullmatch(rollback["report_sha256"])
        or not isinstance(rollback_revision, str)
        or not COMMIT_SHA.fullmatch(rollback_revision)
        or rollback_revision == expected_revision
        or rollback.get("final_revision") != expected_revision
        or rollback.get("step_count") != len(ROLLBACK_DRILL_STEPS)
        or rollback_completed_at > observed_at + timedelta(seconds=MAX_REPORT_FUTURE_SKEW_SECONDS)
        or (observed_at - rollback_completed_at).total_seconds() > MAX_REPORT_AGE_SECONDS
    ):
        raise DeploymentEvidenceError("production rollback drill identity or freshness is invalid")

    return {
        "schema_version": 1,
        "evidence_type": "reviewed-raw-deployment",
        "status": "ok",
        "environment": "production",
        "collected_at": report["collected_at"],
        "reviewed_at": observed_at.isoformat().replace("+00:00", "Z"),
        "source_revision": expected_revision,
        "expected_version": expected_version,
        "frontend_sha256": frontend_sha256,
        "raw_report_sha256": report_sha256,
        "check_count": len(indexed),
        "deployment_provider": deployment_provider,
        "host_identity_sha256": host_identity_sha256,
        "unattended_workers": unattended_workers,
        "restore_drill": {
            "completed_at": restore["completed_at"],
            "backup_sha256": restore["backup_sha256"],
            "restored_file_count": restore["restored_file_count"],
            "restored_bytes": restore["restored_bytes"],
            "application": restore["application"],
        },
        "rollback_drill": {
            "drill_id": rollback["drill_id"],
            "report_sha256": rollback["report_sha256"],
            "completed_at": rollback["completed_at"],
            "rollback_revision": rollback_revision,
            "final_revision": expected_revision,
            "step_count": rollback["step_count"],
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Review a raw MarketSentinel real-host deployment report without trusting a wrapper manifest."
    )
    parser.add_argument("report", type=Path)
    parser.add_argument("--expected-version", required=True)
    parser.add_argument("--expected-revision", required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        result = review_deployment_report(
            args.report,
            expected_version=args.expected_version,
            expected_revision=args.expected_revision,
        )
    except (OSError, DeploymentEvidenceError) as exc:
        failure = {"status": "failed", "detail": str(exc)}
        if args.json:
            print(json.dumps(failure, sort_keys=True))
        else:
            print(f"[fail] {exc}")
        return 1
    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        print(
            "[ok] production deployment evidence "
            f"({result['source_revision']}, raw_sha256={result['raw_report_sha256']})"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
