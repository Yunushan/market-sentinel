from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from stat import S_IFLNK, S_IFMT, S_IFREG
from typing import Any, Mapping
from urllib.parse import urlparse
from uuid import UUID
from zipfile import BadZipFile, ZipFile

try:
    from scripts.release_version import normalize_release_tag, normalize_release_version
    from scripts.review_deployment_evidence import review_deployment_report, review_external_probe_report
    from scripts.review_prometheus_delivery_evidence import review_report as review_alert_delivery_report
    from scripts.verify_production_deployment import (
        REQUIRED_WORKER_UNIT_CONTRACT_SHA256,
        worker_invocation_sha256,
    )
except ModuleNotFoundError:  # Direct execution adds scripts/ to sys.path.
    from release_version import normalize_release_tag, normalize_release_version
    from review_deployment_evidence import review_deployment_report, review_external_probe_report
    from review_prometheus_delivery_evidence import review_report as review_alert_delivery_report
    from verify_production_deployment import (
        REQUIRED_WORKER_UNIT_CONTRACT_SHA256,
        worker_invocation_sha256,
    )

from core.deployment_identity import canonical_https_origin


REPOSITORY = "Yunushan/market-sentinel"
WORKFLOW = ".github/workflows/deployment-evidence.yml"
WORKFLOW_NAME = "Production deployment evidence"
REPORT_TYPE = "market-sentinel-deployment-evidence"
IDENTITY_TYPE = "market-sentinel-deployment-release-identity"
COLLECTOR_JOB = "Collect production deployment evidence"
REVIEW_JOB = "Review and attest production deployment evidence"
EXTERNAL_PROBE_JOB = "Probe production externally from GitHub-hosted runner"
EXTERNAL_PROBE_REPORT_NAME = "external-probe.json"
COLLECTOR_LABELS = ("self-hosted", "linux", "x64", "market-sentinel-production")
COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")
SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
MAX_JSON_BYTES = 1024 * 1024
MAX_FRONTEND_ARCHIVE_BYTES = 128 * 1024 * 1024
MAX_FRONTEND_FILES = 10_000
MAX_FRONTEND_EXPANDED_BYTES = 512 * 1024 * 1024
ALERT_DELIVERY_EVIDENCE_TYPE = "reviewed-prometheus-alert-delivery"
ALERT_DELIVERY_RULE_DIRECTORY = Path("/var/lib/prometheus/market-sentinel-attestation")
ALERT_DELIVERY_PROMETHEUS_ORIGIN = "http://127.0.0.1:9090"
ALERT_DELIVERY_ALERTMANAGER_ORIGIN = "http://127.0.0.1:9093"
ALERT_DELIVERY_RECEIVER_NAME = "market-sentinel-attestation"
ALERT_DELIVERY_RECEIVER_PORT = 19094
ALERT_DELIVERY_MAX_CLOCK_SKEW_SECONDS = 60
ALERT_FINGERPRINT = re.compile(r"^[0-9a-f]{16,64}$")
ALERT_ONCALL_PROVIDER = re.compile(r"^[a-z][a-z0-9_-]{1,62}$")
DISALLOWED_ALERT_ONCALL_PROVIDERS = frozenset({"local", "loopback", "mock", "none", "test"})
ENVIRONMENT_KEY = re.compile(r"^[A-Z][A-Z0-9_]*$")
UNATTENDED_STATE_FILE = "/var/lib/market-sentinel/unattended-worker-state.json"
UNATTENDED_LOCK_FILE = "/var/lib/market-sentinel/.unattended-worker.lock"
UNATTENDED_ENVIRONMENT_FILE = "/etc/market-sentinel/market-sentinel-worker.env"
ALLOWED_UNATTENDED_ENVIRONMENT_KEYS = frozenset(
    {
        "MARKET_SENTINEL_SOURCE_REVISION",
        "CONTEXT_API_KEY",
        "CRYPTO_COM_PREDICTIONS_API_KEY",
        "DFLOW_API_KEY",
        "DRAFTKINGS_PREDICTIONS_API_KEY",
        "FANDUEL_PREDICTS_API_KEY",
        "GJOPEN_API_TOKEN",
        "MANIFOLD_API_KEY",
        "METACULUS_API_TOKEN",
        "NADEX_PREDICTIONS_API_KEY",
        "OPINION_API_KEY",
        "PREDICT_FUN_API_KEY",
        "SCICAST_API_KEY",
        "XMARKET_API_KEY",
    }
)
UNATTENDED_SERVICES = {
    "market-sentinel-alerts-refresh.service": ("alerts-refresh", "market-sentinel-alerts-refresh.timer"),
    "market-sentinel-wallets-poll.service": ("wallets-poll", "market-sentinel-wallets-poll.timer"),
}
UNATTENDED_TASK_MAX_AGE_SECONDS = {"alerts-refresh": 5 * 60, "wallets-poll": 10 * 60}
UNATTENDED_MAX_FUTURE_SKEW_SECONDS = 5


class DeploymentEvidenceGenerationError(ValueError):
    pass


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DeploymentEvidenceGenerationError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_nonfinite(value: str) -> None:
    raise DeploymentEvidenceGenerationError(f"non-finite JSON number: {value}")


def _read_json(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise DeploymentEvidenceGenerationError(f"not a regular JSON file: {path}")
    raw = path.read_bytes()
    if not raw or len(raw) > MAX_JSON_BYTES:
        raise DeploymentEvidenceGenerationError("JSON input is empty or oversized")
    try:
        payload = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonfinite,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DeploymentEvidenceGenerationError("JSON input is malformed") from exc
    if not isinstance(payload, dict):
        raise DeploymentEvidenceGenerationError("JSON input must be an object")
    return payload


def _read_json_value(path: Path) -> Any:
    if path.is_symlink() or not path.is_file():
        raise DeploymentEvidenceGenerationError(f"not a regular JSON file: {path}")
    raw = path.read_bytes()
    if not raw or len(raw) > MAX_JSON_BYTES:
        raise DeploymentEvidenceGenerationError("JSON input is empty or oversized")
    try:
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonfinite,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DeploymentEvidenceGenerationError("JSON input is malformed") from exc


def canonical_external_probe_bytes(payload: Mapping[str, Any]) -> bytes:
    """Recreate the exact canonical bytes written by the external probe collector."""

    return (json.dumps(dict(payload), sort_keys=True, allow_nan=False) + "\n").encode("utf-8")


def _recent_attestation_timestamp(value: Any, *, now: datetime) -> bool:
    if not isinstance(value, str) or not value.strip() or "\n" in value or "\r" in value:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return False
    age = now.astimezone(timezone.utc) - parsed.astimezone(timezone.utc)
    return -timedelta(minutes=5) <= age <= timedelta(hours=24)


def _external_probe_attestation_matches(
    report: Mapping[str, Any],
    result: Any,
    *,
    revision: str,
    run_id: int,
    run_attempt: int,
    workflow_ref: str,
    source_ref: str,
    now: datetime,
) -> bool:
    """Require one exact GitHub-hosted SLSA attestation for the probe bytes."""

    expected_workflow_ref = f"{REPOSITORY}/{WORKFLOW}@refs/heads/main"
    if (
        not isinstance(result, list)
        or not COMMIT_SHA.fullmatch(revision)
        or type(run_id) is not int
        or run_id <= 0
        or type(run_attempt) is not int
        or run_attempt <= 0
        or workflow_ref != expected_workflow_ref
        or source_ref != "refs/heads/main"
    ):
        return False
    report_hash = hashlib.sha256(canonical_external_probe_bytes(report)).hexdigest()
    workflow_uri = f"https://github.com/{workflow_ref}"
    repository_uri = f"https://github.com/{REPOSITORY}"
    invocation_uri = f"{repository_uri}/actions/runs/{run_id}/attempts/{run_attempt}"
    owner = REPOSITORY.split("/", 1)[0]
    matches: list[Any] = []
    for item in result:
        if (
            not isinstance(item, Mapping)
            or not isinstance(item.get("attestation"), Mapping)
            or not item["attestation"]
        ):
            continue
        verification = item.get("verificationResult")
        if not isinstance(verification, Mapping) or verification.get("mediaType") != (
            "application/vnd.dev.sigstore.verificationresult+json;version=0.1"
        ):
            continue
        statement = verification.get("statement")
        signature = verification.get("signature")
        if not isinstance(statement, Mapping) or not isinstance(signature, Mapping):
            continue
        subjects = statement.get("subject")
        if (
            statement.get("_type") != "https://in-toto.io/Statement/v1"
            or statement.get("predicateType") != "https://slsa.dev/provenance/v1"
            or not isinstance(subjects, list)
            or len(subjects) != 1
            or not isinstance(subjects[0], Mapping)
            or subjects[0].get("name") != EXTERNAL_PROBE_REPORT_NAME
            or subjects[0].get("digest") != {"sha256": report_hash}
        ):
            continue
        certificate = signature.get("certificate")
        certificate_contract = {
            "subjectAlternativeName": workflow_uri,
            "issuer": "https://token.actions.githubusercontent.com",
            "buildSignerURI": workflow_uri,
            "buildSignerDigest": revision,
            "runnerEnvironment": "github-hosted",
            "sourceRepositoryURI": repository_uri,
            "sourceRepositoryDigest": revision,
            "sourceRepositoryRef": source_ref,
            "sourceRepositoryOwnerURI": f"https://github.com/{owner}",
            "buildConfigURI": workflow_uri,
            "buildConfigDigest": revision,
            "buildTrigger": "workflow_dispatch",
            "runInvocationURI": invocation_uri,
            "sourceRepositoryVisibilityAtSigning": "public",
        }
        if not isinstance(certificate, Mapping) or any(
            certificate.get(key) != value for key, value in certificate_contract.items()
        ):
            continue
        timestamps = verification.get("verifiedTimestamps")
        if (
            not isinstance(timestamps, list)
            or not timestamps
            or any(
                not isinstance(timestamp, Mapping)
                or not _recent_attestation_timestamp(timestamp.get("timestamp"), now=now)
                for timestamp in timestamps
            )
        ):
            continue
        predicate = statement.get("predicate")
        build_definition = predicate.get("buildDefinition") if isinstance(predicate, Mapping) else None
        run_details = predicate.get("runDetails") if isinstance(predicate, Mapping) else None
        if not isinstance(build_definition, Mapping) or not isinstance(run_details, Mapping):
            continue
        external = build_definition.get("externalParameters")
        internal = build_definition.get("internalParameters")
        dependencies = build_definition.get("resolvedDependencies")
        workflow = external.get("workflow") if isinstance(external, Mapping) else None
        github = internal.get("github") if isinstance(internal, Mapping) else None
        if (
            build_definition.get("buildType") != "https://actions.github.io/buildtypes/workflow/v1"
            or workflow != {"path": WORKFLOW, "ref": source_ref, "repository": repository_uri}
            or not isinstance(github, Mapping)
            or github.get("event_name") != "workflow_dispatch"
            or github.get("runner_environment") != "github-hosted"
            or not isinstance(dependencies, list)
        ):
            continue
        dependency_uri = f"git+{repository_uri}@{source_ref}"
        matching_dependencies = [
            dependency
            for dependency in dependencies
            if isinstance(dependency, Mapping)
            and dependency.get("uri") == dependency_uri
            and dependency.get("digest") == {"gitCommit": revision}
        ]
        builder = run_details.get("builder")
        metadata = run_details.get("metadata")
        if (
            len(matching_dependencies) != 1
            or not isinstance(builder, Mapping)
            or builder.get("id") != workflow_uri
            or not isinstance(metadata, Mapping)
            or metadata.get("invocationId") != invocation_uri
        ):
            continue
        matches.append(item)
    return len(matches) == 1


def verify_external_probe_attestation(
    report_path: Path,
    attestation_path: Path,
    *,
    revision: str,
    run_id: int,
    run_attempt: int,
    workflow_ref: str,
    source_ref: str,
    now: datetime | None = None,
) -> str:
    """Validate the exact source-job attestation already verified by ``gh``."""

    if report_path.name != EXTERNAL_PROBE_REPORT_NAME:
        raise DeploymentEvidenceGenerationError(
            f"external probe report must be named {EXTERNAL_PROBE_REPORT_NAME}"
        )
    report = _read_json(report_path)
    raw = report_path.read_bytes()
    canonical = canonical_external_probe_bytes(report)
    if raw != canonical:
        raise DeploymentEvidenceGenerationError("external probe report is not canonical JSON")
    result = _read_json_value(attestation_path)
    if not _external_probe_attestation_matches(
        report,
        result,
        revision=revision,
        run_id=run_id,
        run_attempt=run_attempt,
        workflow_ref=workflow_ref,
        source_ref=source_ref,
        now=now or datetime.now(timezone.utc),
    ):
        raise DeploymentEvidenceGenerationError(
            "external probe source attestation is missing, duplicated, self-hosted, or misbound"
        )
    return hashlib.sha256(raw).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _exact_object(value: Any, expected: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        raise DeploymentEvidenceGenerationError(f"{label} does not match the exact reviewed schema")
    return value


def _utc_timestamp(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise DeploymentEvidenceGenerationError(f"{label} must be a UTC timestamp ending in Z")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise DeploymentEvidenceGenerationError(f"{label} is not a valid ISO-8601 timestamp") from exc
    return parsed.astimezone(timezone.utc)


def _systemd_utc_timestamp(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise DeploymentEvidenceGenerationError(f"{label} must be a systemd UTC timestamp")
    for pattern in ("%a %Y-%m-%d %H:%M:%S UTC", "%a %Y-%m-%d %H:%M:%S.%f UTC"):
        try:
            return datetime.strptime(value.strip(), pattern).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    raise DeploymentEvidenceGenerationError(f"{label} must be a systemd UTC timestamp")


def _finite_number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise DeploymentEvidenceGenerationError(f"{label} must be a finite number")
    return float(value)


def _nonnegative_int(value: Any, label: str) -> int:
    if type(value) is not int or value < 0:
        raise DeploymentEvidenceGenerationError(f"{label} must be a non-negative integer")
    return value


def _reviewed_alert_delivery_summary(
    value: Any,
    *,
    expected_revision: str,
    expected_identity_sha256: str,
    expected_run_id: int,
    expected_run_attempt: int,
    expected_nonce: str,
    expected_receipt_origin_sha256: str,
    expected_raw_sha256: str,
    expected_review_sha256: str,
) -> dict[str, Any]:
    review = _exact_object(
        value,
        {
            "acknowledged_at",
            "acknowledger_sha256",
            "alert_fingerprint",
            "alertmanager_config_sha256",
            "alertmanager_status_sha256",
            "alert_name",
            "binding_sha256",
            "deployment_identity_sha256",
            "delivery_id_sha256",
            "evidence_type",
            "nonce",
            "oncall_channel_sha256",
            "oncall_provider",
            "raw_report_sha256",
            "receipt_origin_sha256",
            "receipt_sha256",
            "reviewed_at",
            "run_attempt",
            "run_id",
            "schema_version",
            "source_revision",
            "status",
            "timeline",
            "transcript_sha256",
            "webhook_body_sha256",
            "webhook_event_sha256",
        },
        "alert-delivery review",
    )
    if (
        review.get("schema_version") != 1
        or review.get("evidence_type") != ALERT_DELIVERY_EVIDENCE_TYPE
        or review.get("status") != "ok"
        or review.get("source_revision") != expected_revision
        or review.get("deployment_identity_sha256") != expected_identity_sha256
        or review.get("run_id") != expected_run_id
        or review.get("run_attempt") != expected_run_attempt
        or review.get("nonce") != expected_nonce
        or review.get("raw_report_sha256") != expected_raw_sha256
        or review.get("receipt_origin_sha256") != expected_receipt_origin_sha256
    ):
        raise DeploymentEvidenceGenerationError("alert-delivery review is not bound to the exact deployment run")
    if not SHA256_HEX.fullmatch(expected_review_sha256):
        raise DeploymentEvidenceGenerationError("alert-delivery review artifact digest is invalid")
    for field in (
        "acknowledger_sha256",
        "alertmanager_config_sha256",
        "alertmanager_status_sha256",
        "binding_sha256",
        "delivery_id_sha256",
        "oncall_channel_sha256",
        "raw_report_sha256",
        "receipt_origin_sha256",
        "receipt_sha256",
        "transcript_sha256",
        "webhook_body_sha256",
        "webhook_event_sha256",
        "deployment_identity_sha256",
    ):
        if not SHA256_HEX.fullmatch(str(review.get(field) or "")):
            raise DeploymentEvidenceGenerationError(f"alert-delivery review {field} is invalid")
    provider = review.get("oncall_provider")
    if (
        not isinstance(provider, str)
        or ALERT_ONCALL_PROVIDER.fullmatch(provider) is None
        or provider in DISALLOWED_ALERT_ONCALL_PROVIDERS
    ):
        raise DeploymentEvidenceGenerationError("alert-delivery review on-call provider is invalid")
    binding = str(review["binding_sha256"])
    if review.get("alert_name") != f"MarketSentinelDeliveryAttestation_{binding[:24]}":
        raise DeploymentEvidenceGenerationError("alert-delivery review alert name is not challenge-bound")
    if not ALERT_FINGERPRINT.fullmatch(str(review.get("alert_fingerprint") or "")):
        raise DeploymentEvidenceGenerationError("alert-delivery review fingerprint is invalid")
    timeline = _exact_object(
        review.get("timeline"),
        {
            "alertmanager_observed_at",
            "alertmanager_config_observed_at",
            "cleanup_rule_absent_at",
            "completed_at",
            "oncall_acknowledged_at",
            "oncall_dispatched_at",
            "oncall_receipt_observed_at",
            "oncall_received_at",
            "prometheus_alert_observed_at",
            "prometheus_rule_observed_at",
            "receiver_observed_at",
            "started_at",
        },
        "alert-delivery timeline",
    )
    started = _utc_timestamp(timeline["started_at"], "alert-delivery started_at")
    rule_seen = _utc_timestamp(timeline["prometheus_rule_observed_at"], "alert-delivery rule timestamp")
    prometheus_seen = _utc_timestamp(
        timeline["prometheus_alert_observed_at"], "alert-delivery Prometheus timestamp"
    )
    alertmanager_seen = _utc_timestamp(
        timeline["alertmanager_observed_at"], "alert-delivery Alertmanager timestamp"
    )
    alertmanager_config_seen = _utc_timestamp(
        timeline["alertmanager_config_observed_at"],
        "alert-delivery Alertmanager configuration timestamp",
    )
    receiver_seen = _utc_timestamp(timeline["receiver_observed_at"], "alert-delivery receiver timestamp")
    oncall_received = _utc_timestamp(
        timeline["oncall_received_at"], "alert-delivery on-call received timestamp"
    )
    oncall_dispatched = _utc_timestamp(
        timeline["oncall_dispatched_at"], "alert-delivery on-call dispatched timestamp"
    )
    oncall_acknowledged = _utc_timestamp(
        timeline["oncall_acknowledged_at"], "alert-delivery on-call acknowledged timestamp"
    )
    oncall_observed = _utc_timestamp(
        timeline["oncall_receipt_observed_at"], "alert-delivery on-call receipt timestamp"
    )
    cleanup_seen = _utc_timestamp(timeline["cleanup_rule_absent_at"], "alert-delivery cleanup timestamp")
    completed = _utc_timestamp(timeline["completed_at"], "alert-delivery completed_at")
    reviewed_at = _utc_timestamp(review.get("reviewed_at"), "alert-delivery reviewed_at")
    acknowledged_at = _utc_timestamp(review.get("acknowledged_at"), "alert-delivery acknowledged_at")
    clock_skew = timedelta(seconds=ALERT_DELIVERY_MAX_CLOCK_SKEW_SECONDS)
    if not (
        started
        <= rule_seen
        <= prometheus_seen
        <= alertmanager_seen
        <= alertmanager_config_seen
        <= cleanup_seen
        <= completed
        and completed <= reviewed_at + clock_skew
        and started <= receiver_seen <= oncall_observed <= cleanup_seen
        and alertmanager_config_seen <= oncall_observed
        and oncall_received <= oncall_dispatched < oncall_acknowledged
        and oncall_received >= started - clock_skew
        and oncall_acknowledged <= oncall_observed + clock_skew
        and acknowledged_at == oncall_acknowledged
    ):
        raise DeploymentEvidenceGenerationError("alert-delivery review timestamps are out of order")
    return {
        "acknowledged_at": review["acknowledged_at"],
        "acknowledger_sha256": review["acknowledger_sha256"],
        "alert_fingerprint": review["alert_fingerprint"],
        "alertmanager_config_sha256": review["alertmanager_config_sha256"],
        "alertmanager_status_sha256": review["alertmanager_status_sha256"],
        "binding_sha256": binding,
        "deployment_identity_sha256": expected_identity_sha256,
        "delivery_id_sha256": review["delivery_id_sha256"],
        "nonce_sha256": hashlib.sha256(expected_nonce.encode("ascii")).hexdigest(),
        "oncall_channel_sha256": review["oncall_channel_sha256"],
        "oncall_provider": provider,
        "raw_report_sha256": expected_raw_sha256,
        "receipt_origin_sha256": review["receipt_origin_sha256"],
        "receipt_sha256": review["receipt_sha256"],
        "review_report_sha256": expected_review_sha256,
        "run_attempt": expected_run_attempt,
        "run_id": expected_run_id,
        "source_revision": expected_revision,
        "status": "ok",
        "timeline": dict(timeline),
        "transcript_sha256": review["transcript_sha256"],
        "webhook_body_sha256": review["webhook_body_sha256"],
        "webhook_event_sha256": review["webhook_event_sha256"],
    }


def _reviewed_unattended_workers(
    value: Any,
    *,
    expected_revision: str,
    collected_at: datetime,
) -> dict[str, Any]:
    summary = _exact_object(
        value,
        {
            "environment_file",
            "environment_keys",
            "lock_file",
            "services",
            "state_file",
            "state_sha256",
            "tasks",
        },
        "unattended-workers summary",
    )
    expected_paths = {
        "state_file": UNATTENDED_STATE_FILE,
        "lock_file": UNATTENDED_LOCK_FILE,
        "environment_file": UNATTENDED_ENVIRONMENT_FILE,
    }
    if any(summary.get(field) != expected for field, expected in expected_paths.items()):
        raise DeploymentEvidenceGenerationError("unattended-workers paths do not match the reviewed production paths")
    if not SHA256_HEX.fullmatch(str(summary.get("state_sha256") or "")):
        raise DeploymentEvidenceGenerationError("unattended-workers state digest is invalid")
    environment_keys = summary.get("environment_keys")
    if (
        not isinstance(environment_keys, list)
        or any(not isinstance(item, str) or not ENVIRONMENT_KEY.fullmatch(item) for item in environment_keys)
        or environment_keys != sorted(set(environment_keys))
        or any(item not in ALLOWED_UNATTENDED_ENVIRONMENT_KEYS for item in environment_keys)
        or "MARKET_SENTINEL_SOURCE_REVISION" not in environment_keys
    ):
        raise DeploymentEvidenceGenerationError("unattended-workers environment key inventory is invalid")
    services = _exact_object(summary.get("services"), set(UNATTENDED_SERVICES), "unattended-workers services")
    tasks = _exact_object(
        summary.get("tasks"),
        {task for task, _timer in UNATTENDED_SERVICES.values()},
        "unattended-workers tasks",
    )
    for service_name, (task_name, timer_name) in UNATTENDED_SERVICES.items():
        service = _exact_object(
            services[service_name],
            {
                "completed_at",
                "completed_at_unix_seconds",
                "source_revision",
                "task",
                "timer",
                "unit_contract_sha256",
            },
            f"unattended-workers service {service_name}",
        )
        if service.get("task") != task_name or service.get("timer") != timer_name:
            raise DeploymentEvidenceGenerationError("unattended-workers service linkage is invalid")
        expected_contract_sha256 = REQUIRED_WORKER_UNIT_CONTRACT_SHA256[service_name]
        if (
            service.get("source_revision") != expected_revision
            or service.get("unit_contract_sha256") != expected_contract_sha256
        ):
            raise DeploymentEvidenceGenerationError(
                "unattended-workers service revision or unit contract binding is invalid"
            )
        completed = _systemd_utc_timestamp(service.get("completed_at"), f"{service_name} completed_at")
        completed_seconds = _finite_number(
            service.get("completed_at_unix_seconds"), f"{service_name} completed_at_unix_seconds"
        )
        if abs(completed.timestamp() - completed_seconds) > 1:
            raise DeploymentEvidenceGenerationError("unattended-workers service timestamp fields disagree")
        service_age = (collected_at - completed).total_seconds()
        if (
            service_age < -UNATTENDED_MAX_FUTURE_SKEW_SECONDS
            or service_age > UNATTENDED_TASK_MAX_AGE_SECONDS[task_name]
        ):
            raise DeploymentEvidenceGenerationError("unattended-workers service completion is stale or future-dated")

        task = _exact_object(
            tasks[task_name],
            {
                "abandoned_runs",
                "attempts_completed",
                "emitted",
                "freshness_age_seconds",
                "last_success_at",
                "last_success_at_unix_seconds",
                "max_age_seconds",
                "processed",
                "run_id",
                "service",
                "service_unit",
                "source_revision",
                "timer",
                "total_failures",
                "total_runs",
                "total_successes",
                "unit_contract_sha256",
                "invocation_sha256",
            },
            f"unattended-workers task {task_name}",
        )
        if task.get("service") != service_name or task.get("timer") != timer_name:
            raise DeploymentEvidenceGenerationError("unattended-workers task linkage is invalid")
        expected_invocation_sha256 = worker_invocation_sha256(
            task=task_name,
            service_unit=service_name,
            source_revision=expected_revision,
            unit_contract_sha256=expected_contract_sha256,
        )
        if (
            task.get("source_revision") != expected_revision
            or task.get("service_unit") != service_name
            or task.get("unit_contract_sha256") != expected_contract_sha256
            or task.get("invocation_sha256") != expected_invocation_sha256
        ):
            raise DeploymentEvidenceGenerationError(
                "unattended-workers task revision or invocation binding is invalid"
            )
        run_id = task.get("run_id")
        try:
            parsed_run_id = UUID(run_id) if isinstance(run_id, str) else None
        except ValueError as exc:
            raise DeploymentEvidenceGenerationError("unattended-workers task run ID is invalid") from exc
        if parsed_run_id is None or parsed_run_id.version != 4 or str(parsed_run_id) != run_id:
            raise DeploymentEvidenceGenerationError("unattended-workers task run ID is not a canonical UUIDv4")
        last_success = _utc_timestamp(task.get("last_success_at"), f"{task_name} last_success_at")
        last_success_seconds = _finite_number(
            task.get("last_success_at_unix_seconds"), f"{task_name} last_success_at_unix_seconds"
        )
        completion_delta = (completed - last_success).total_seconds()
        if (
            abs(last_success.timestamp() - last_success_seconds) > 1
            or not -2 <= completion_delta <= 60
        ):
            raise DeploymentEvidenceGenerationError("unattended-workers success timestamp fields disagree")
        freshness = _finite_number(task.get("freshness_age_seconds"), f"{task_name} freshness age")
        observed_freshness = (collected_at - last_success).total_seconds()
        maximum_age = _finite_number(task.get("max_age_seconds"), f"{task_name} maximum age")
        if (
            maximum_age != UNATTENDED_TASK_MAX_AGE_SECONDS[task_name]
            or freshness < -UNATTENDED_MAX_FUTURE_SKEW_SECONDS
            or freshness > maximum_age
            or observed_freshness < -UNATTENDED_MAX_FUTURE_SKEW_SECONDS
            or observed_freshness > maximum_age
            or abs(freshness - observed_freshness) > 5
        ):
            raise DeploymentEvidenceGenerationError("unattended-workers task freshness is invalid")
        for field in (
            "abandoned_runs",
            "attempts_completed",
            "emitted",
            "processed",
            "total_failures",
            "total_runs",
            "total_successes",
        ):
            _nonnegative_int(task.get(field), f"{task_name} {field}")
        if (
            task["attempts_completed"] < 1
            or task["attempts_completed"] > 3
            or task["processed"] < 1
            or task["total_runs"] < 1
            or task["total_successes"] < 1
            or task["total_successes"] + task["total_failures"] != task["total_runs"]
        ):
            raise DeploymentEvidenceGenerationError("unattended-workers task counters are invalid")
    return summary


def _hash_field(digest: Any, value: bytes) -> None:
    digest.update(len(value).to_bytes(8, "big"))
    digest.update(value)


def frontend_archive_sha256(path: Path) -> str:
    """Derive the runtime frontend-tree digest directly from the exact release ZIP."""

    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_FRONTEND_ARCHIVE_BYTES:
        raise DeploymentEvidenceGenerationError("frontend release asset is unavailable or oversized")
    entries: dict[str, tuple[int, bytes]] = {}
    expanded = 0
    try:
        with ZipFile(path) as archive:
            for info in archive.infolist():
                raw_name = info.filename.replace("\\", "/")
                name = PurePosixPath(raw_name)
                if (
                    info.is_dir()
                    or not raw_name
                    or name.is_absolute()
                    or ".." in name.parts
                    or raw_name != name.as_posix()
                ):
                    if info.is_dir() and raw_name and ".." not in name.parts and not name.is_absolute():
                        continue
                    raise DeploymentEvidenceGenerationError("frontend release asset has an unsafe member")
                mode_type = S_IFMT((info.external_attr >> 16) & 0xFFFF)
                if mode_type in {S_IFLNK} or mode_type not in {0, S_IFREG}:
                    raise DeploymentEvidenceGenerationError("frontend release asset has a non-regular member")
                if raw_name in entries or len(entries) >= MAX_FRONTEND_FILES:
                    raise DeploymentEvidenceGenerationError("frontend release asset has duplicate or excessive members")
                if info.file_size < 0 or expanded + info.file_size > MAX_FRONTEND_EXPANDED_BYTES:
                    raise DeploymentEvidenceGenerationError("frontend release asset expands beyond the safety limit")
                body = archive.read(info)
                if len(body) != info.file_size:
                    raise DeploymentEvidenceGenerationError("frontend release asset member size changed")
                expanded += len(body)
                entries[raw_name] = (len(body), hashlib.sha256(body).digest())
    except (BadZipFile, OSError, RuntimeError) as exc:
        raise DeploymentEvidenceGenerationError("frontend release asset is not a valid ZIP") from exc
    if "index.html" not in entries:
        raise DeploymentEvidenceGenerationError("frontend release asset is missing index.html")
    digest = hashlib.sha256(b"market-sentinel-frontend-tree-v1\0")
    for name in sorted(entries):
        size, file_digest = entries[name]
        _hash_field(digest, name.encode("utf-8"))
        _hash_field(digest, str(size).encode("ascii"))
        _hash_field(digest, file_digest)
    return digest.hexdigest()


def _canonical_origin(value: str) -> str:
    try:
        origin = canonical_https_origin(value)
    except ValueError as exc:
        raise DeploymentEvidenceGenerationError(str(exc)) from exc
    hostname = urlparse(origin).hostname
    if hostname in {"localhost", "example.com", "analytics.example.com"} or hostname.endswith(".example.com"):
        raise DeploymentEvidenceGenerationError("production origin must not use a placeholder or localhost")
    return origin


def generate_release_identity(
    release_payload: dict[str, Any],
    frontend_asset: Path,
    *,
    tag: str,
    version: str,
    revision: str,
) -> dict[str, Any]:
    version = normalize_release_version(version)
    if normalize_release_tag(tag) != version or not COMMIT_SHA.fullmatch(revision):
        raise DeploymentEvidenceGenerationError("release coordinates are invalid")
    release_id = release_payload.get("id")
    expected_url = f"https://github.com/{REPOSITORY}/releases/tag/{tag}"
    if (
        type(release_id) is not int
        or release_id <= 0
        or release_payload.get("tag_name") != tag
        or release_payload.get("target_commitish") != revision
        or release_payload.get("draft") is not False
        or release_payload.get("prerelease") is not False
        or release_payload.get("html_url") != expected_url
        or not isinstance(release_payload.get("published_at"), str)
    ):
        raise DeploymentEvidenceGenerationError("release metadata does not match the exact stable release")
    asset_name = f"market-sentinel-{tag}-frontend-dist.zip"
    assets = release_payload.get("assets")
    matches = [asset for asset in assets if isinstance(asset, dict) and asset.get("name") == asset_name] if isinstance(assets, list) else []
    if len(matches) != 1:
        raise DeploymentEvidenceGenerationError("release has no unique frontend asset")
    asset = matches[0]
    local_digest = _sha256(frontend_asset)
    if (
        type(asset.get("id")) is not int
        or asset["id"] <= 0
        or asset.get("state") != "uploaded"
        or asset.get("size") != frontend_asset.stat().st_size
        or asset.get("digest") != f"sha256:{local_digest}"
        or not SHA256_HEX.fullmatch(local_digest)
    ):
        raise DeploymentEvidenceGenerationError("downloaded frontend asset does not match GitHub metadata")
    return {
        "schema_version": 1,
        "identity_type": IDENTITY_TYPE,
        "repository": REPOSITORY,
        "release": {
            "id": release_id,
            "tag": tag,
            "version": version,
            "target_commit": revision,
            "published_at": release_payload["published_at"],
            "html_url": expected_url,
            "asset": {
                "id": asset["id"],
                "name": asset_name,
                "size": asset["size"],
                "sha256": local_digest,
            },
        },
        "frontend_sha256": frontend_archive_sha256(frontend_asset),
    }


def generate_evidence(
    raw_report_path: Path,
    external_probe_path: Path,
    alert_delivery_report_path: Path,
    alert_delivery_review_path: Path,
    identity_path: Path,
    *,
    public_origin: str,
    oncall_receipt_origin: str,
    monitoring_nonce: str,
    run_id: int,
    run_attempt: int,
    workflow_ref: str,
    source_ref: str,
    artifact_name: str,
) -> dict[str, Any]:
    if type(run_id) is not int or run_id <= 0 or type(run_attempt) is not int or run_attempt <= 0:
        raise DeploymentEvidenceGenerationError("workflow run identity is invalid")
    if not SHA256_HEX.fullmatch(monitoring_nonce):
        raise DeploymentEvidenceGenerationError("monitoring nonce must be an exact lowercase 256-bit challenge")
    identity_sha256 = _sha256(identity_path)
    identity = _read_json(identity_path)
    if identity.get("identity_type") != IDENTITY_TYPE or identity.get("repository") != REPOSITORY:
        raise DeploymentEvidenceGenerationError("trusted release identity is invalid")
    release = identity.get("release")
    if not isinstance(release, dict):
        raise DeploymentEvidenceGenerationError("trusted release identity is missing release metadata")
    canonical_origin = _canonical_origin(public_origin)
    raw_report = _read_json(raw_report_path)
    collection = raw_report.get("collection")
    expected_nonce = f"{release.get('target_commit') or ''}:{run_id}:{run_attempt}"
    if (
        not isinstance(collection, dict)
        or collection.get("public_origin") != canonical_origin
        or collection.get("run_id") != run_id
        or collection.get("run_attempt") != run_attempt
        or collection.get("nonce") != expected_nonce
    ):
        raise DeploymentEvidenceGenerationError("raw deployment report is not bound to the exact production origin")
    revision = str(release.get("target_commit") or "")
    version = str(release.get("version") or "")
    raw_alert_delivery_sha256 = _sha256(alert_delivery_report_path)
    alert_delivery_review_sha256 = _sha256(alert_delivery_review_path)
    independently_reviewed_alert_delivery = review_alert_delivery_report(
        alert_delivery_report_path,
        expected_source_revision=revision,
        expected_deployment_identity_sha256=identity_sha256,
        expected_run_id=run_id,
        expected_run_attempt=run_attempt,
        expected_nonce=monitoring_nonce,
        expected_rule_directory=ALERT_DELIVERY_RULE_DIRECTORY,
        expected_oncall_receipt_origin=oncall_receipt_origin,
        expected_prometheus_origin=ALERT_DELIVERY_PROMETHEUS_ORIGIN,
        expected_alertmanager_origin=ALERT_DELIVERY_ALERTMANAGER_ORIGIN,
        expected_receiver_name=ALERT_DELIVERY_RECEIVER_NAME,
        expected_receiver_port=ALERT_DELIVERY_RECEIVER_PORT,
    )
    alert_delivery = _reviewed_alert_delivery_summary(
        _read_json(alert_delivery_review_path),
        expected_revision=revision,
        expected_identity_sha256=identity_sha256,
        expected_run_id=run_id,
        expected_run_attempt=run_attempt,
        expected_nonce=monitoring_nonce,
        expected_receipt_origin_sha256=str(
            independently_reviewed_alert_delivery.get("receipt_origin_sha256") or ""
        ),
        expected_raw_sha256=raw_alert_delivery_sha256,
        expected_review_sha256=alert_delivery_review_sha256,
    )
    independently_reviewed_summary = _reviewed_alert_delivery_summary(
        independently_reviewed_alert_delivery,
        expected_revision=revision,
        expected_identity_sha256=identity_sha256,
        expected_run_id=run_id,
        expected_run_attempt=run_attempt,
        expected_nonce=monitoring_nonce,
        expected_receipt_origin_sha256=str(
            independently_reviewed_alert_delivery.get("receipt_origin_sha256") or ""
        ),
        expected_raw_sha256=raw_alert_delivery_sha256,
        expected_review_sha256=alert_delivery_review_sha256,
    )
    if alert_delivery != independently_reviewed_summary:
        raise DeploymentEvidenceGenerationError(
            "persisted alert-delivery review disagrees with the generator's independent review"
        )
    review = review_deployment_report(
        raw_report_path,
        expected_version=version,
        expected_revision=revision,
        expected_run_id=run_id,
        expected_run_attempt=run_attempt,
        expected_nonce=expected_nonce,
    )
    external_probe = review_external_probe_report(
        external_probe_path,
        expected_version=version,
        expected_revision=revision,
        expected_frontend_sha256=str(identity.get("frontend_sha256") or ""),
        expected_origin=canonical_origin,
        expected_run_id=run_id,
        expected_run_attempt=run_attempt,
        expected_nonce=expected_nonce,
    )
    external_probe_report = _read_json(external_probe_path)
    external_probe_raw = external_probe_path.read_bytes()
    if external_probe_raw != canonical_external_probe_bytes(external_probe_report):
        raise DeploymentEvidenceGenerationError("external probe report is not canonical JSON")
    if hashlib.sha256(external_probe_raw).hexdigest() != external_probe.get("report_sha256"):
        raise DeploymentEvidenceGenerationError(
            "external probe review is not bound to the exact canonical source bytes"
        )
    if review.get("frontend_sha256") != identity.get("frontend_sha256"):
        raise DeploymentEvidenceGenerationError("deployment frontend does not match the exact release asset")
    unattended_workers = _reviewed_unattended_workers(
        review.get("unattended_workers"),
        expected_revision=revision,
        collected_at=_utc_timestamp(review.get("collected_at"), "deployment collected_at"),
    )
    expected_workflow_ref = f"{REPOSITORY}/{WORKFLOW}@refs/heads/main"
    if workflow_ref != expected_workflow_ref or source_ref != "refs/heads/main":
        raise DeploymentEvidenceGenerationError("deployment evidence must originate from protected main")
    if artifact_name != f"deployment-evidence-{revision}-{run_id}-{run_attempt}":
        raise DeploymentEvidenceGenerationError("deployment artifact name is not run-unique")
    return {
        "schema_version": 1,
        "report_type": REPORT_TYPE,
        "deployment": {
            "environment": "production",
            "public_origin": canonical_origin,
            "collected_at": review["collected_at"],
            "raw_report_sha256": review["raw_report_sha256"],
            "workflow_nonce": expected_nonce,
            "check_count": review["check_count"],
            "frontend_sha256": review["frontend_sha256"],
            "deployment_provider": review["deployment_provider"],
            "host_identity_sha256": review["host_identity_sha256"],
            "restore_drill": review["restore_drill"],
            "rollback_drill": review["rollback_drill"],
            "external_probe": {
                "probed_at": external_probe["probed_at"],
                "raw_report_sha256": external_probe["report_sha256"],
                "runner_environment": "github-hosted",
                "api_version": external_probe["api_version"],
                "source_revision": external_probe["source_revision"],
                "frontend_sha256": external_probe["frontend_sha256"],
                "unauthenticated_probes": external_probe["unauthenticated_probes"],
            },
            "release": release,
        },
        "operations": {
            "alert_delivery": alert_delivery,
            "unattended_workers": unattended_workers,
        },
        "external_probe_report": external_probe_report,
        "evidence": {
            "repository": REPOSITORY,
            "source_revision": revision,
            "run_id": run_id,
            "run_attempt": run_attempt,
            "workflow": WORKFLOW,
            "workflow_name": WORKFLOW_NAME,
            "workflow_ref": workflow_ref,
            "source_ref": source_ref,
            "event": "workflow_dispatch",
            "runner_environment": "github-hosted",
            "collector_job": COLLECTOR_JOB,
            "external_probe_job": EXTERNAL_PROBE_JOB,
            "review_job": REVIEW_JOB,
            "collector_labels": list(COLLECTOR_LABELS),
            "artifact_name": artifact_name,
        },
    }


def _write_canonical(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate canonical GitHub-attestable deployment evidence.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    identity = subparsers.add_parser("identity")
    identity.add_argument("--release-json", type=Path, required=True)
    identity.add_argument("--frontend-asset", type=Path, required=True)
    identity.add_argument("--tag", required=True)
    identity.add_argument("--version", required=True)
    identity.add_argument("--revision", required=True)
    identity.add_argument("--output", type=Path, required=True)
    evidence = subparsers.add_parser("evidence")
    evidence.add_argument("--raw-report", type=Path, required=True)
    evidence.add_argument("--external-probe-report", type=Path, required=True)
    evidence.add_argument("--alert-delivery-report", type=Path, required=True)
    evidence.add_argument("--alert-delivery-review", type=Path, required=True)
    evidence.add_argument("--identity", type=Path, required=True)
    evidence.add_argument("--monitoring-nonce", required=True)
    evidence.add_argument("--public-origin", required=True)
    evidence.add_argument("--oncall-receipt-origin", required=True)
    evidence.add_argument("--run-id", type=int, required=True)
    evidence.add_argument("--run-attempt", type=int, required=True)
    evidence.add_argument("--workflow-ref", required=True)
    evidence.add_argument("--source-ref", required=True)
    evidence.add_argument("--artifact-name", required=True)
    evidence.add_argument("--output", type=Path, required=True)
    verify_probe = subparsers.add_parser("verify-external-probe-attestation")
    verify_probe.add_argument("--external-probe-report", type=Path, required=True)
    verify_probe.add_argument("--attestation-result", type=Path, required=True)
    verify_probe.add_argument("--revision", required=True)
    verify_probe.add_argument("--run-id", type=int, required=True)
    verify_probe.add_argument("--run-attempt", type=int, required=True)
    verify_probe.add_argument("--workflow-ref", required=True)
    verify_probe.add_argument("--source-ref", required=True)
    args = parser.parse_args()
    try:
        if args.command == "identity":
            payload = generate_release_identity(
                _read_json(args.release_json),
                args.frontend_asset,
                tag=args.tag,
                version=args.version,
                revision=args.revision.lower(),
            )
        elif args.command == "evidence":
            payload = generate_evidence(
                args.raw_report,
                args.external_probe_report,
                args.alert_delivery_report,
                args.alert_delivery_review,
                args.identity,
                public_origin=args.public_origin,
                oncall_receipt_origin=args.oncall_receipt_origin,
                monitoring_nonce=args.monitoring_nonce,
                run_id=args.run_id,
                run_attempt=args.run_attempt,
                workflow_ref=args.workflow_ref,
                source_ref=args.source_ref,
                artifact_name=args.artifact_name,
            )
        else:
            report_sha256 = verify_external_probe_attestation(
                args.external_probe_report,
                args.attestation_result,
                revision=args.revision,
                run_id=args.run_id,
                run_attempt=args.run_attempt,
                workflow_ref=args.workflow_ref,
                source_ref=args.source_ref,
            )
            print(json.dumps({"ok": True, "external_probe_sha256": report_sha256}, sort_keys=True))
            return 0
        _write_canonical(args.output, payload)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
