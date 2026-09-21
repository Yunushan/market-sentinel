from __future__ import annotations

"""Build and validate narrowly scoped, attestable production-readiness evidence.

This module deliberately separates semantic evidence validation from online
GitHub provenance verification.  A manifest built here is still not
score-eligible until ``check_product_readiness.py`` verifies its exact-byte
artifact attestation, workflow run, hosted job, artifact, and source revision.
"""

import argparse
import hashlib
import json
import math
import os
import re
import sys
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

# Keep direct `python scripts/...` invocations importable from the repository root.
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from polymarket.funded_policy import (
    FUNDED_TOKEN_ALLOWLIST_VARIABLE,
    canonical_funded_token_allowlist,
    funded_token_allowlist_sha256,
    parse_funded_token_allowlist,
)

try:
    from scripts.verify_repository_settings import (
        GOVERNANCE_STATE_SCHEMA_VERSION,
        governance_state_sha256,
    )
except ModuleNotFoundError:  # Direct script execution resolves sibling modules from scripts/.
    from verify_repository_settings import (  # type: ignore[no-redef]
        GOVERNANCE_STATE_SCHEMA_VERSION,
        governance_state_sha256,
    )


SCHEMA_VERSION = 2
EVIDENCE_METADATA_SCHEMA_VERSION = 1
REPORT_TYPE = "market-sentinel-trusted-readiness-evidence"
MAX_EVIDENCE_BYTES = 1024 * 1024
MAX_FUTURE_SKEW_SECONDS = 5 * 60
MAX_AGE_HOURS = 24
REPOSITORY = "Yunushan/market-sentinel"
TRUSTED_REF = "refs/heads/main"
PLATFORM_SOURCE_WORKFLOW = ".github/workflows/ci.yml"
PLATFORM_SOURCE_WORKFLOW_NAME = "CI"
PLATFORM_RECEIPT_SCHEMA_VERSION = 1
PLATFORM_RECEIPT_REPORT_TYPE = "market-sentinel-platform-ci-job-receipt"
PLATFORM_RECEIPT_SUBJECT_NAME = "platform-ci-receipt.json"
PLATFORM_RECEIPT_ARTIFACT_PREFIX = "platform-ci-receipt"
FUNDED_COLLECTOR_JOB = "Collect bounded funded Polymarket outcome"
FUNDED_COLLECTOR_LABELS = frozenset({"self-hosted", "linux", "x64", "market-sentinel-production"})
FUNDED_ENVIRONMENT_NAME = "production"
MAX_FUNDED_ENVIRONMENT_BYTES = 256 * 1024
MAX_FUNDED_POLICY_BYTES = 16 * 1024
MAX_FUNDED_APPROVALS_BYTES = 256 * 1024
FUNDED_ENVIRONMENT_SOURCE = f"https://api.github.com/repos/{REPOSITORY}/environments/{FUNDED_ENVIRONMENT_NAME}"
FUNDED_POLICY_SOURCE = f"{FUNDED_ENVIRONMENT_SOURCE}/variables/{FUNDED_TOKEN_ALLOWLIST_VARIABLE}"
FUNDED_COLLECTOR_STEPS = (
    "Verify funded execution approval",
    "Prepare persistent funded recovery directory",
    "Verify exact clean source before funded action",
    "Run bounded funded order and immediate cancel",
    "Reverify exact clean source after funded action",
    "Collect resolved recovery journal",
    "Upload raw funded outcome and resolved journal",
)

REPOSITORY_SETTINGS_CHECKS = (
    "branch_required_status_checks",
    "branch_status_checks_bound_to_actions_app",
    "branch_require_up_to_date",
    "branch_enforce_admins",
    "branch_require_pull_request",
    "branch_minimum_approvals",
    "branch_dismiss_stale_reviews",
    "branch_require_code_owner_reviews",
    "branch_require_last_push_approval",
    "branch_require_signed_commits",
    "branch_conversation_resolution",
    "branch_linear_history",
    "branch_force_pushes_disabled",
    "branch_deletions_disabled",
)
RELEASE_ENVIRONMENT_CHECKS = (
    "release_required_reviewers",
    "release_independent_reviewers",
    "release_prevent_self_review",
    "release_deployment_refs",
    "release_signing_secrets",
    "release_windows_code_signing_required",
    "production_required_reviewers",
    "production_independent_reviewers",
    "production_prevent_self_review",
    "production_protected_branches",
    "production_secrets",
    "production_variables",
)
PLATFORM_CI_CHECKS = (
    "aggregate_python_package_build",
    "python_ubuntu_matrix",
    "python_macos_14_15_26_matrix",
    "python_windows_2025_vs2026_matrix",
    "rhel_ubi_8_9_10_and_rhel_7_abi",
    "rocky_linux_8_9_10",
    "windows_11_arm",
    "react_build",
    "mobile_web_smoke_android_and_ios",
    "tkinter_gui_lifecycle",
)
PLATFORM_CHECKS = (
    "windows_hosted_python_and_smoke",
    "windows_11_arm_python_and_smoke",
    "ubuntu_python_tkinter_and_verifier",
    "macos_14_15_26_python_and_verifier",
)
CREDENTIALED_POLYMARKET_CHECKS = (
    "report_integrity",
    "source_revision",
    "public_live_checks",
    "credentialed_read_checks",
    "credential_live_verified",
)
FUNDED_POLYMARKET_CHECKS = (
    "report_integrity",
    "source_revision",
    "public_live_checks",
    "credentialed_read_checks",
    "funded_order_cancel",
    "post_cancel_verified",
    "funded_live_verified",
)

REQUIRED_CHECKS: dict[str, tuple[str, ...]] = {
    "repository-settings": REPOSITORY_SETTINGS_CHECKS,
    "release-environment": RELEASE_ENVIRONMENT_CHECKS,
    "platform-ci": PLATFORM_CI_CHECKS,
    "platform": PLATFORM_CHECKS,
    "credentialed-polymarket": CREDENTIALED_POLYMARKET_CHECKS,
    "funded-polymarket": FUNDED_POLYMARKET_CHECKS,
}

WORKFLOW_CONTRACTS: dict[str, dict[str, Any]] = {
    "repository-settings": {
        "workflow": ".github/workflows/governance-evidence.yml",
        "workflow_name": "Governance evidence",
        "job": "Collect and attest governance evidence",
        "subject_name": "repository-settings-evidence.json",
        "required_steps": (
            "Verify exact clean source before collection",
            "Collect live repository and release-environment controls",
            "Generate exact governance evidence",
            "Review governance evidence before attestation",
            "Recollect and compare live governance state",
            "Reverify exact clean source after collection",
            "Attest repository-settings evidence",
            "Attest release-environment evidence",
            "Upload repository-settings evidence",
            "Upload release-environment evidence",
        ),
    },
    "release-environment": {
        "workflow": ".github/workflows/governance-evidence.yml",
        "workflow_name": "Governance evidence",
        "job": "Collect and attest governance evidence",
        "subject_name": "release-environment-evidence.json",
        "required_steps": (
            "Verify exact clean source before collection",
            "Collect live repository and release-environment controls",
            "Generate exact governance evidence",
            "Review governance evidence before attestation",
            "Recollect and compare live governance state",
            "Reverify exact clean source after collection",
            "Attest repository-settings evidence",
            "Attest release-environment evidence",
            "Upload repository-settings evidence",
            "Upload release-environment evidence",
        ),
    },
    "platform-ci": {
        "workflow": ".github/workflows/platform-evidence.yml",
        "workflow_name": "Platform evidence",
        "job": "Review and attest platform evidence",
        "subject_name": "platform-ci-evidence.json",
        "required_steps": (
            "Verify exact clean source before review",
            "Download exact CI run metadata",
            "Download exact source-job receipts",
            "Verify every source-job receipt attestation",
            "Generate exact platform evidence",
            "Review platform evidence before attestation",
            "Reverify exact clean source after review",
            "Attest platform-CI evidence",
            "Attest platform evidence",
            "Upload platform-CI evidence",
            "Upload platform evidence",
        ),
    },
    "platform": {
        "workflow": ".github/workflows/platform-evidence.yml",
        "workflow_name": "Platform evidence",
        "job": "Review and attest platform evidence",
        "subject_name": "platform-evidence.json",
        "required_steps": (
            "Verify exact clean source before review",
            "Download exact CI run metadata",
            "Download exact source-job receipts",
            "Verify every source-job receipt attestation",
            "Generate exact platform evidence",
            "Review platform evidence before attestation",
            "Reverify exact clean source after review",
            "Attest platform-CI evidence",
            "Attest platform evidence",
            "Upload platform-CI evidence",
            "Upload platform evidence",
        ),
    },
    "credentialed-polymarket": {
        "workflow": ".github/workflows/polymarket-evidence.yml",
        "workflow_name": "Polymarket acceptance evidence",
        "job": "Credentialed Polymarket evidence",
        "subject_name": "credentialed-polymarket-evidence.json",
        "required_steps": (
            "Verify credentialed evidence mode",
            "Verify exact clean source before probe",
            "Run credentialed public and authenticated reads",
            "Generate exact credentialed evidence",
            "Review credentialed evidence before attestation",
            "Reverify exact clean source after probe",
            "Attest credentialed evidence",
            "Upload credentialed evidence",
        ),
    },
    "funded-polymarket": {
        "workflow": ".github/workflows/polymarket-evidence.yml",
        "workflow_name": "Polymarket acceptance evidence",
        "job": "Review and attest funded Polymarket evidence",
        "subject_name": "funded-polymarket-evidence.json",
        "required_steps": (
            "Verify exact clean source before review",
            "Download raw funded outcome and resolved journal",
            "Download exact funded workflow job metadata",
            "Download production funded policy and protections",
            "Download exact funded environment approval history",
            "Generate exact funded evidence",
            "Review funded evidence before attestation",
            "Reverify exact clean source after review",
            "Attest funded evidence",
            "Upload funded evidence",
        ),
    },
}

_COMMIT_RE = re.compile(r"[0-9a-f]{40}")
_HASH_RE = re.compile(r"[0-9a-f]{64}")
_EVM_ADDRESS_RE = re.compile(r"0x[0-9a-fA-F]{40}")
_REPOSITORY_RE = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
_COMMON_FIELDS = frozenset(
    {
        "schema_version",
        "report_type",
        "evidence_type",
        "verified",
        "source",
        "scope",
        "source_revision",
        "checks",
        "evidence",
    }
)
_TYPE_FIELDS = {
    "repository-settings": _COMMON_FIELDS | {"governance_state_sha256"},
    "release-environment": _COMMON_FIELDS | {"governance_state_sha256"},
    "platform-ci": _COMMON_FIELDS | {"source_run_id", "source_run_attempt", "source_receipts"},
    "platform": _COMMON_FIELDS | {"source_run_id", "source_run_attempt", "source_receipts", "targets"},
    "credentialed-polymarket": _COMMON_FIELDS
    | {"target_tier", "report_sha256", "live_action", "live_report"},
    "funded-polymarket": _COMMON_FIELDS
    | {
        "target_tier",
        "report_sha256",
        "live_action",
        "live_report",
        "recovery_journal_sha256",
        "recovery_receipt",
        "collector_job",
        "environment_protection",
        "environment_approval",
        "funded_token_policy",
    },
}
_EVIDENCE_FIELDS = frozenset(
    {
        "schema_version",
        "repository",
        "source_revision",
        "run_id",
        "run_attempt",
        "workflow",
        "workflow_name",
        "workflow_ref",
        "event",
        "runner_environment",
        "job",
        "artifact_name",
        "generated_at",
    }
)
_PLATFORM_RECEIPT_FIELDS = frozenset(
    {
        "schema_version",
        "report_type",
        "repository",
        "source_revision",
        "run_id",
        "run_attempt",
        "workflow",
        "workflow_name",
        "workflow_ref",
        "event",
        "check_name",
        "job_key",
        "job_name",
        "matrix",
        "runner_environment",
        "runner_os",
        "runner_arch",
        "generated_at",
        "identity_sha256",
        "artifact_name",
    }
)
_FUNDED_RECOVERY_JOURNAL_FIELDS = frozenset(
    {
        "schema_version",
        "market_id",
        "token_id",
        "side",
        "price",
        "size",
        "tif",
        "post_only",
        "account_address",
        "stage",
        "order_id",
        "manual_reconciliation_required",
        "resolved",
        "zero_fill_verified",
        "run_id",
        "run_started_at",
        "source_revision",
        "sequence",
        "updated_at",
        "workflow_run_id",
        "workflow_run_attempt",
        "evidence_nonce",
    }
)
_FUNDED_RECOVERY_RECEIPT_FIELDS = frozenset(
    {
        "schema_version",
        "sha256",
        "source_revision",
        "workflow_run_id",
        "workflow_run_attempt",
        "evidence_nonce",
        "order_id",
        "stage",
        "resolved",
    }
)
_FUNDED_COLLECTOR_SUMMARY_FIELDS = frozenset(
    {
        "job_id",
        "run_id",
        "run_attempt",
        "head_sha",
        "name",
        "status",
        "conclusion",
        "runner_environment",
        "labels",
        "steps",
        "started_at",
        "completed_at",
    }
)
_FUNDED_ENVIRONMENT_SUMMARY_FIELDS = frozenset(
    {
        "schema_version",
        "environment_id",
        "environment_name",
        "source",
        "updated_at",
        "collector_started_at",
        "required_reviewers",
        "prevent_self_review",
        "protected_branches",
    }
)
_FUNDED_REVIEWER_SUMMARY_FIELDS = frozenset({"type", "id", "identity"})
_FUNDED_APPROVAL_SUMMARY_FIELDS = frozenset(
    {
        "schema_version",
        "run_id",
        "run_attempt",
        "environment_id",
        "environment_name",
        "state",
        "reviewer",
        "source",
    }
)
_FUNDED_APPROVAL_REVIEWER_SUMMARY_FIELDS = frozenset({"id", "login"})
_FUNDED_TOKEN_POLICY_SUMMARY_FIELDS = frozenset(
    {
        "schema_version",
        "environment_id",
        "environment_name",
        "variable_name",
        "source",
        "updated_at",
        "token_ids",
        "allowlist_sha256",
        "selected_token_id",
    }
)
_FUNDED_ENVIRONMENT_API_FIELDS = frozenset(
    {
        "id",
        "node_id",
        "name",
        "url",
        "html_url",
        "created_at",
        "updated_at",
        "protection_rules",
        "deployment_branch_policy",
    }
)
_FUNDED_REQUIRED_REVIEWERS_RULE_FIELDS = frozenset(
    {"id", "node_id", "prevent_self_review", "type", "reviewers"}
)
_FUNDED_REVIEWER_API_FIELDS = frozenset({"type", "reviewer"})
_FUNDED_APPROVAL_RECORD_FIELDS = frozenset({"state", "comment", "environments", "user"})
_FUNDED_APPROVAL_ENVIRONMENT_FIELDS = frozenset(
    {"id", "node_id", "name", "url", "html_url", "created_at", "updated_at"}
)
_FUNDED_APPROVAL_USER_FIELDS = frozenset(
    {
        "login",
        "id",
        "node_id",
        "avatar_url",
        "gravatar_id",
        "url",
        "html_url",
        "followers_url",
        "following_url",
        "gists_url",
        "starred_url",
        "subscriptions_url",
        "organizations_url",
        "repos_url",
        "events_url",
        "received_events_url",
        "type",
        "site_admin",
    }
)
_FUNDED_POLICY_API_FIELDS = frozenset({"name", "value", "created_at", "updated_at"})
_FUNDED_POLICY_RECEIPT_FIELDS = frozenset(
    {"schema_version", "variable_name", "token_ids", "allowlist_sha256", "selected_token_id"}
)
_EXACT_SCOPES = {
    "repository-settings": "Live protected-main repository controls",
    "release-environment": "Live protected release/production environment controls and signing prerequisites",
    "platform-ci": "Successful exact-revision hosted CI compatibility lanes",
    "platform": "Exact-revision hosted desktop platform evidence",
    "credentialed-polymarket": "Exact-revision credentialed Polymarket read acceptance",
    "funded-polymarket": "Exact-revision bounded funded Polymarket order/cancel acceptance",
}
_PLATFORM_TARGETS = ("Windows", "Windows 11", "Ubuntu Linux", "macOS")

_PYTHON_VERSIONS = ("3.10", "3.11", "3.12", "3.13", "3.14")
_PYTHON_OSES = ("ubuntu-latest", "macos-14", "macos-15", "macos-26", "windows-2025-vs2026")
_STABLE_PYTHON_JOBS = tuple(
    f"Python {version} / {operating_system}"
    for operating_system in _PYTHON_OSES
    for version in _PYTHON_VERSIONS
)
_FUTURE_PYTHON_JOBS = tuple(f"Future Python 3.x / {operating_system}" for operating_system in _PYTHON_OSES)
_RHEL_JOBS = (
    "RHEL 8 UBI / Python 3.12",
    "RHEL 9 UBI / Python 3.12",
    "RHEL 10 UBI / Python 3.12 minimal",
    "RHEL 7 ABI / manylinux2014 Python 3.10",
)
_ROCKY_JOBS = (
    "Rocky Linux 8 / Python 3.12",
    "Rocky Linux 9 / Python 3.12",
    "Rocky Linux 10 / Python 3.12",
)
_MOBILE_JOBS = tuple(
    f"Mobile web smoke / {target}"
    for target in ("android-14", "android-15", "android-16", "ios-15", "ios-16", "ios-18", "ios-26")
)


class TrustedEvidenceError(ValueError):
    """Raised when untrusted input cannot produce a trusted evidence candidate."""


class _DuplicateJsonKey(ValueError):
    pass


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in pairs:
        if key in output:
            raise _DuplicateJsonKey(f"duplicate JSON key: {key}")
        output[key] = value
    return output


def _reject_nonfinite(value: str) -> Any:
    raise ValueError(f"non-finite JSON number: {value}")


def strict_json_bytes(raw: bytes, *, maximum_bytes: int = MAX_EVIDENCE_BYTES) -> Any:
    if not raw or len(raw) > maximum_bytes:
        raise ValueError("JSON input is empty or exceeds the size limit")
    return json.loads(
        raw.decode("utf-8"),
        object_pairs_hook=_reject_duplicate_keys,
        parse_constant=_reject_nonfinite,
    )


def load_strict_json(path: Path, *, maximum_bytes: int = MAX_EVIDENCE_BYTES) -> Any:
    if path.is_symlink() or not path.is_file():
        raise ValueError("evidence input must be a regular non-symbolic-link file")
    return strict_json_bytes(path.read_bytes(), maximum_bytes=maximum_bytes)


def canonical_json_bytes(payload: Mapping[str, Any]) -> bytes:
    return (json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def artifact_name(evidence_type: str, revision: str, run_id: int, run_attempt: int) -> str:
    return f"{evidence_type}-evidence-{revision}-{run_id}-{run_attempt}"


def _utc_timestamp(value: datetime | None = None) -> str:
    current = (value or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return current.isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: Any, *, now: datetime, errors: list[str]) -> datetime | None:
    if not isinstance(value, str) or not value.strip() or "\n" in value or "\r" in value:
        errors.append("evidence.generated_at must be a non-empty single-line ISO-8601 timestamp")
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        errors.append("evidence.generated_at must be valid ISO-8601")
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        errors.append("evidence.generated_at must include a timezone")
        return None
    normalized = parsed.astimezone(timezone.utc)
    age = now.astimezone(timezone.utc) - normalized
    if age < -timedelta(seconds=MAX_FUTURE_SKEW_SECONDS):
        errors.append("evidence.generated_at is in the future")
    if age > timedelta(hours=MAX_AGE_HOURS):
        errors.append(f"evidence.generated_at is older than {MAX_AGE_HOURS} hours")
    return normalized


def _bound_utc_timestamp(value: Any, label: str) -> tuple[datetime, str]:
    if not isinstance(value, str) or not value.endswith("Z") or value != value.strip():
        raise TrustedEvidenceError(f"{label} must be a canonical UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise TrustedEvidenceError(f"{label} must be a canonical UTC timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise TrustedEvidenceError(f"{label} must include a timezone")
    normalized = parsed.astimezone(timezone.utc)
    canonical = normalized.isoformat().replace("+00:00", "Z")
    if canonical != value:
        raise TrustedEvidenceError(f"{label} must be normalized UTC")
    return normalized, canonical


def _require_revision(value: str) -> str:
    candidate = str(value or "").strip().lower()
    if not _COMMIT_RE.fullmatch(candidate):
        raise TrustedEvidenceError("source revision must be a lowercase 40-character commit SHA")
    return candidate


def _require_positive_integer(value: Any, label: str) -> int:
    if type(value) is not int or value <= 0:
        raise TrustedEvidenceError(f"{label} must be a positive integer")
    return value


def _evidence_metadata(
    evidence_type: str,
    *,
    repository: str,
    source_revision: str,
    run_id: int,
    run_attempt: int,
    workflow_ref: str,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    if repository != REPOSITORY or not _REPOSITORY_RE.fullmatch(repository):
        raise TrustedEvidenceError(f"repository must equal {REPOSITORY}")
    revision = _require_revision(source_revision)
    run = _require_positive_integer(run_id, "run_id")
    attempt = _require_positive_integer(run_attempt, "run_attempt")
    contract = WORKFLOW_CONTRACTS[evidence_type]
    expected_ref = f"{REPOSITORY}/{contract['workflow']}@{TRUSTED_REF}"
    if workflow_ref != expected_ref:
        raise TrustedEvidenceError(f"workflow_ref must equal {expected_ref}")
    return {
        "schema_version": EVIDENCE_METADATA_SCHEMA_VERSION,
        "repository": REPOSITORY,
        "source_revision": revision,
        "run_id": run,
        "run_attempt": attempt,
        "workflow": contract["workflow"],
        "workflow_name": contract["workflow_name"],
        "workflow_ref": expected_ref,
        "event": "workflow_dispatch",
        "runner_environment": "github-hosted",
        "job": contract["job"],
        "artifact_name": artifact_name(evidence_type, revision, run, attempt),
        "generated_at": _utc_timestamp(generated_at),
    }


def _base_manifest(
    evidence_type: str,
    *,
    source: str,
    source_revision: str,
    checks: Iterable[str],
    metadata: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "report_type": REPORT_TYPE,
        "evidence_type": evidence_type,
        "verified": True,
        "source": source,
        "scope": _EXACT_SCOPES[evidence_type],
        "source_revision": source_revision,
        "checks": [{"name": name, "status": "pass"} for name in checks],
        "evidence": dict(metadata),
    }


def _validated_raw_governance(payload: Any) -> tuple[dict[str, dict[str, Any]], str]:
    expected_fields = {
        "schema_version",
        "repository",
        "branch",
        "status",
        "checks",
        "governance_state",
        "governance_state_sha256",
    }
    if not isinstance(payload, Mapping) or set(payload) != expected_fields:
        raise TrustedEvidenceError("governance collector output does not match the exact schema")
    if (
        payload.get("schema_version") != 2
        or payload.get("repository") != REPOSITORY
        or payload.get("branch") != "main"
        or payload.get("status") != "ok"
    ):
        raise TrustedEvidenceError("governance collector identity or status is invalid")
    state = payload.get("governance_state")
    state_digest = payload.get("governance_state_sha256")
    if (
        not isinstance(state, Mapping)
        or state.get("schema_version") != GOVERNANCE_STATE_SCHEMA_VERSION
        or state.get("repository") != REPOSITORY
        or state.get("branch") != "main"
        or not isinstance(state_digest, str)
        or not _HASH_RE.fullmatch(state_digest)
        or governance_state_sha256(state) != state_digest
    ):
        raise TrustedEvidenceError("governance collector state snapshot or digest is invalid")
    checks = payload.get("checks")
    if not isinstance(checks, list):
        raise TrustedEvidenceError("governance collector checks must be an array")
    indexed: dict[str, dict[str, Any]] = {}
    allowed = set(REPOSITORY_SETTINGS_CHECKS) | set(RELEASE_ENVIRONMENT_CHECKS)
    for item in checks:
        if not isinstance(item, Mapping) or set(item) != {"name", "status", "detail"}:
            raise TrustedEvidenceError("governance collector check does not match the exact schema")
        name = item.get("name")
        if not isinstance(name, str) or name not in allowed or name in indexed:
            raise TrustedEvidenceError("governance collector contains an unknown or duplicate check")
        if item.get("status") != "pass" or not isinstance(item.get("detail"), str):
            raise TrustedEvidenceError(f"governance collector check {name} did not pass")
        indexed[name] = dict(item)
    if set(indexed) != allowed:
        raise TrustedEvidenceError("governance collector is missing required checks")
    return indexed, state_digest


def build_governance_manifests(
    raw_payload: Any,
    *,
    repository: str,
    source_revision: str,
    run_id: int,
    run_attempt: int,
    workflow_ref: str,
    generated_at: datetime | None = None,
) -> dict[str, dict[str, Any]]:
    _checks, state_digest = _validated_raw_governance(raw_payload)
    revision = _require_revision(source_revision)
    output: dict[str, dict[str, Any]] = {}
    sources = {
        "repository-settings": f"https://api.github.com/repos/{REPOSITORY}/branches/main/protection",
        "release-environment": f"https://api.github.com/repos/{REPOSITORY}/environments",
    }
    for evidence_type in ("repository-settings", "release-environment"):
        metadata = _evidence_metadata(
            evidence_type,
            repository=repository,
            source_revision=revision,
            run_id=run_id,
            run_attempt=run_attempt,
            workflow_ref=workflow_ref,
            generated_at=generated_at,
        )
        output[evidence_type] = _base_manifest(
            evidence_type,
            source=sources[evidence_type],
            source_revision=revision,
            checks=REQUIRED_CHECKS[evidence_type],
            metadata=metadata,
        )
        output[evidence_type]["governance_state_sha256"] = state_digest
    return output


def _required_job_names() -> dict[str, tuple[str, ...]]:
    def python_jobs(operating_system: str) -> tuple[str, ...]:
        return tuple(f"Python {version} / {operating_system}" for version in _PYTHON_VERSIONS) + (
            f"Future Python 3.x / {operating_system}",
        )

    return {
        "aggregate_python_package_build": ("Python package build",),
        "python_ubuntu_matrix": python_jobs("ubuntu-latest"),
        "python_macos_14_15_26_matrix": tuple(
            name for name in (*_STABLE_PYTHON_JOBS, *_FUTURE_PYTHON_JOBS) if "/ macos-" in name
        ),
        "python_windows_2025_vs2026_matrix": python_jobs("windows-2025-vs2026"),
        "rhel_ubi_8_9_10_and_rhel_7_abi": _RHEL_JOBS,
        "rocky_linux_8_9_10": _ROCKY_JOBS,
        "windows_11_arm": ("Windows 11 ARM runner / Python 3.12 x64",),
        "react_build": ("React build",),
        "mobile_web_smoke_android_and_ios": _MOBILE_JOBS,
        "tkinter_gui_lifecycle": ("Tkinter GUI lifecycle / Ubuntu",),
    }


def _required_job_labels() -> dict[str, tuple[str, ...]]:
    """Describe labels as API cross-checks, never as hosted-runner proof."""

    labels: dict[str, tuple[str, ...]] = {}
    for operating_system in _PYTHON_OSES:
        for version in _PYTHON_VERSIONS:
            labels[f"Python {version} / {operating_system}"] = (operating_system,)
        labels[f"Future Python 3.x / {operating_system}"] = (operating_system,)
    for name in (*_RHEL_JOBS, *_ROCKY_JOBS):
        labels[name] = ("ubuntu-24.04",)
    for name in _MOBILE_JOBS:
        labels[name] = ("ubuntu-latest",)
    labels.update(
        {
            "Python package build": ("ubuntu-latest",),
            "Windows 11 ARM runner / Python 3.12 x64": ("windows-11-arm",),
            "React build": ("ubuntu-latest",),
            "Tkinter GUI lifecycle / Ubuntu": ("ubuntu-24.04",),
        }
    )
    expected_names = {
        name for group in _required_job_names().values() for name in group
    }
    if set(labels) != expected_names:
        raise RuntimeError("reviewed CI job labels do not cover the exact required job inventory")
    return labels


def _platform_job_contracts() -> dict[str, dict[str, Any]]:
    """Return the exact job and matrix identities allowed to earn platform credit."""

    groups = _required_job_names()
    labels = _required_job_labels()
    contracts: dict[str, dict[str, Any]] = {}

    def add(
        name: str,
        *,
        check_name: str,
        job_key: str,
        matrix: Mapping[str, str],
        runner_os: str,
    ) -> None:
        if name in contracts:
            raise RuntimeError(f"duplicate platform source-job contract: {name}")
        contracts[name] = {
            "check_name": check_name,
            "job_key": job_key,
            "matrix": dict(matrix),
            "labels": labels[name],
            "runner_os": runner_os,
        }

    for operating_system in _PYTHON_OSES:
        if operating_system == "ubuntu-latest":
            check_name = "python_ubuntu_matrix"
            runner_os = "Linux"
        elif operating_system.startswith("macos-"):
            check_name = "python_macos_14_15_26_matrix"
            runner_os = "macOS"
        else:
            check_name = "python_windows_2025_vs2026_matrix"
            runner_os = "Windows"
        for version in _PYTHON_VERSIONS:
            add(
                f"Python {version} / {operating_system}",
                check_name=check_name,
                job_key="python",
                matrix={"os": operating_system, "python-version": version},
                runner_os=runner_os,
            )
        add(
            f"Future Python 3.x / {operating_system}",
            check_name=check_name,
            job_key="future-python",
            matrix={"os": operating_system, "python-version": "3.x"},
            runner_os=runner_os,
        )
    for name in _RHEL_JOBS:
        add(
            name,
            check_name="rhel_ubi_8_9_10_and_rhel_7_abi",
            job_key="enterprise-linux",
            matrix={"name": name},
            runner_os="Linux",
        )
    for name in _ROCKY_JOBS:
        add(
            name,
            check_name="rocky_linux_8_9_10",
            job_key="enterprise-linux",
            matrix={"name": name},
            runner_os="Linux",
        )
    for name in _MOBILE_JOBS:
        add(
            name,
            check_name="mobile_web_smoke_android_and_ios",
            job_key="mobile-web",
            matrix={"target": name.removeprefix("Mobile web smoke / ")},
            runner_os="Linux",
        )
    add(
        "Python package build",
        check_name="aggregate_python_package_build",
        job_key="package",
        matrix={},
        runner_os="Linux",
    )
    add(
        "Windows 11 ARM runner / Python 3.12 x64",
        check_name="windows_11_arm",
        job_key="windows-11",
        matrix={},
        runner_os="Windows",
    )
    add(
        "React build",
        check_name="react_build",
        job_key="frontend",
        matrix={},
        runner_os="Linux",
    )
    add(
        "Tkinter GUI lifecycle / Ubuntu",
        check_name="tkinter_gui_lifecycle",
        job_key="tkinter-gui-lifecycle",
        matrix={},
        runner_os="Linux",
    )
    expected_names = {name for names in groups.values() for name in names}
    if set(contracts) != expected_names:
        raise RuntimeError("platform receipt contracts do not cover the required CI inventory")
    return contracts


def _platform_receipt_identity(
    *,
    source_revision: str,
    run_id: int,
    run_attempt: int,
    check_name: str,
    job_key: str,
    job_name: str,
    matrix: Mapping[str, str],
) -> str:
    identity = {
        "repository": REPOSITORY,
        "source_revision": source_revision,
        "run_id": run_id,
        "run_attempt": run_attempt,
        "check_name": check_name,
        "job_key": job_key,
        "job_name": job_name,
        "matrix": dict(matrix),
    }
    return hashlib.sha256(canonical_json_bytes(identity)).hexdigest()


def platform_receipt_artifact_name(run_id: int, run_attempt: int, identity_sha256: str) -> str:
    _require_positive_integer(run_id, "run_id")
    _require_positive_integer(run_attempt, "run_attempt")
    if not _HASH_RE.fullmatch(identity_sha256):
        raise TrustedEvidenceError("platform receipt identity_sha256 must be a lowercase SHA-256 digest")
    return f"{PLATFORM_RECEIPT_ARTIFACT_PREFIX}-{run_id}-{run_attempt}-{identity_sha256}"


def build_platform_job_receipt(
    *,
    repository: str,
    source_revision: str,
    run_id: int,
    run_attempt: int,
    workflow_ref: str,
    event: str,
    job_key: str,
    matrix: Mapping[str, str],
    runner_environment: str,
    runner_os: str,
    runner_arch: str,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """Build the exact canonical receipt that a successful source job attests."""

    revision = _require_revision(source_revision)
    run = _require_positive_integer(run_id, "run_id")
    attempt = _require_positive_integer(run_attempt, "run_attempt")
    if repository != REPOSITORY:
        raise TrustedEvidenceError(f"repository must equal {REPOSITORY}")
    expected_ref = f"{REPOSITORY}/{PLATFORM_SOURCE_WORKFLOW}@{TRUSTED_REF}"
    if workflow_ref != expected_ref:
        raise TrustedEvidenceError(f"workflow_ref must equal {expected_ref}")
    if event != "push":
        raise TrustedEvidenceError("platform receipts require a protected-main push event")
    if runner_environment != "github-hosted":
        raise TrustedEvidenceError("platform receipts must be generated on a GitHub-hosted runner")
    if not isinstance(matrix, Mapping) or any(
        not isinstance(key, str) or not isinstance(value, str) for key, value in matrix.items()
    ):
        raise TrustedEvidenceError("platform receipt matrix must be a string-to-string object")
    normalized_matrix = dict(matrix)
    matches = [
        (name, contract)
        for name, contract in _platform_job_contracts().items()
        if contract["job_key"] == job_key and contract["matrix"] == normalized_matrix
    ]
    if len(matches) != 1:
        raise TrustedEvidenceError("platform receipt job key and matrix identity are not recognized")
    job_name, contract = matches[0]
    if runner_os != contract["runner_os"]:
        raise TrustedEvidenceError("platform receipt runner OS does not match its exact job contract")
    if not isinstance(runner_arch, str) or not runner_arch.strip() or runner_arch != runner_arch.strip():
        raise TrustedEvidenceError("platform receipt runner architecture must be a non-empty normalized string")
    check_name = str(contract["check_name"])
    identity = _platform_receipt_identity(
        source_revision=revision,
        run_id=run,
        run_attempt=attempt,
        check_name=check_name,
        job_key=job_key,
        job_name=job_name,
        matrix=normalized_matrix,
    )
    return {
        "schema_version": PLATFORM_RECEIPT_SCHEMA_VERSION,
        "report_type": PLATFORM_RECEIPT_REPORT_TYPE,
        "repository": REPOSITORY,
        "source_revision": revision,
        "run_id": run,
        "run_attempt": attempt,
        "workflow": PLATFORM_SOURCE_WORKFLOW,
        "workflow_name": PLATFORM_SOURCE_WORKFLOW_NAME,
        "workflow_ref": expected_ref,
        "event": event,
        "check_name": check_name,
        "job_key": job_key,
        "job_name": job_name,
        "matrix": normalized_matrix,
        "runner_environment": "github-hosted",
        "runner_os": runner_os,
        "runner_arch": runner_arch,
        "generated_at": _utc_timestamp(generated_at),
        "identity_sha256": identity,
        "artifact_name": platform_receipt_artifact_name(run, attempt, identity),
    }


def _platform_timestamp(value: Any, label: str, *, now: datetime) -> datetime:
    parsed, canonical = _bound_utc_timestamp(value, label)
    age = now.astimezone(timezone.utc) - parsed
    if age < -timedelta(seconds=MAX_FUTURE_SKEW_SECONDS):
        raise TrustedEvidenceError(f"{label} is in the future")
    if age > timedelta(hours=MAX_AGE_HOURS):
        raise TrustedEvidenceError(f"{label} is older than {MAX_AGE_HOURS} hours")
    if canonical != value:
        raise TrustedEvidenceError(f"{label} is not canonical UTC")
    return parsed


def _validate_platform_job_receipt(
    payload: Any,
    *,
    expected_revision: str,
    source_run_id: int,
    source_run_attempt: int,
    source_event: str,
    job: Mapping[str, Any],
    now: datetime,
) -> dict[str, Any]:
    if not isinstance(payload, Mapping) or set(payload) != _PLATFORM_RECEIPT_FIELDS:
        raise TrustedEvidenceError("platform source-job receipt does not match the exact field contract")
    job_name = payload.get("job_name")
    contracts = _platform_job_contracts()
    contract = contracts.get(str(job_name))
    if contract is None:
        raise TrustedEvidenceError("platform source-job receipt has an unknown job identity")
    revision = _require_revision(expected_revision)
    exact = {
        "schema_version": PLATFORM_RECEIPT_SCHEMA_VERSION,
        "report_type": PLATFORM_RECEIPT_REPORT_TYPE,
        "repository": REPOSITORY,
        "source_revision": revision,
        "run_id": source_run_id,
        "run_attempt": source_run_attempt,
        "workflow": PLATFORM_SOURCE_WORKFLOW,
        "workflow_name": PLATFORM_SOURCE_WORKFLOW_NAME,
        "workflow_ref": f"{REPOSITORY}/{PLATFORM_SOURCE_WORKFLOW}@{TRUSTED_REF}",
        "event": source_event,
        "check_name": contract["check_name"],
        "job_key": contract["job_key"],
        "job_name": job_name,
        "matrix": contract["matrix"],
        "runner_environment": "github-hosted",
        "runner_os": contract["runner_os"],
    }
    if any(type(payload.get(key)) is not type(value) or payload.get(key) != value for key, value in exact.items()):
        raise TrustedEvidenceError("platform source-job receipt identity or hosted-runner contract is invalid")
    runner_arch = payload.get("runner_arch")
    if not isinstance(runner_arch, str) or not runner_arch.strip() or runner_arch != runner_arch.strip():
        raise TrustedEvidenceError("platform source-job receipt runner architecture is invalid")
    identity = _platform_receipt_identity(
        source_revision=revision,
        run_id=source_run_id,
        run_attempt=source_run_attempt,
        check_name=str(contract["check_name"]),
        job_key=str(contract["job_key"]),
        job_name=str(job_name),
        matrix=contract["matrix"],
    )
    if payload.get("identity_sha256") != identity or payload.get("artifact_name") != platform_receipt_artifact_name(
        source_run_id, source_run_attempt, identity
    ):
        raise TrustedEvidenceError("platform source-job receipt digest or artifact binding is invalid")
    generated_at = _platform_timestamp(payload.get("generated_at"), "platform receipt generated_at", now=now)
    labels = job.get("labels")
    if (
        type(job.get("id")) is not int
        or job.get("id", 0) <= 0
        or job.get("run_id") != source_run_id
        or job.get("run_attempt") != source_run_attempt
        or job.get("head_sha") != revision
        or job.get("name") != job_name
        or job.get("status") != "completed"
        or job.get("conclusion") != "success"
        or not isinstance(labels, list)
        or any(not isinstance(label, str) for label in labels)
        or len(labels) != len(set(labels))
        or set(labels) != set(contract["labels"])
    ):
        raise TrustedEvidenceError("platform source job metadata does not match its signed receipt")
    steps = job.get("steps")
    if not isinstance(steps, list) or sum(
        1
        for step in steps
        if isinstance(step, Mapping)
        and step.get("name") == "Publish cryptographically attested platform receipt"
        and step.get("status") == "completed"
        and step.get("conclusion") == "success"
    ) != 1:
        raise TrustedEvidenceError("platform source job did not complete its receipt publication step")
    started_at = _platform_timestamp(job.get("started_at"), f"platform job {job_name} started_at", now=now)
    completed_at = _platform_timestamp(job.get("completed_at"), f"platform job {job_name} completed_at", now=now)
    skew = timedelta(seconds=MAX_FUTURE_SKEW_SECONDS)
    if started_at > completed_at or generated_at < started_at - skew or generated_at > completed_at + skew:
        raise TrustedEvidenceError("platform receipt timestamp falls outside its exact source-job window")
    return dict(payload)


def derive_platform_checks(
    run_payload: Any,
    jobs_payload: Any,
    artifacts_payload: Any,
    source_receipts: Any,
    *,
    expected_revision: str,
    source_run_id: int,
    now: datetime | None = None,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    revision = _require_revision(expected_revision)
    source_run = _require_positive_integer(source_run_id, "source_run_id")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("platform evidence validation clock must include a timezone")
    if not isinstance(run_payload, Mapping):
        raise TrustedEvidenceError("CI run response must be an object")
    source_attempt = run_payload.get("run_attempt")
    exact_run = {
        "id": source_run,
        "head_sha": revision,
        "name": PLATFORM_SOURCE_WORKFLOW_NAME,
        "path": PLATFORM_SOURCE_WORKFLOW,
        "status": "completed",
        "conclusion": "success",
        "head_branch": "main",
    }
    if any(type(run_payload.get(key)) is not type(value) or run_payload.get(key) != value for key, value in exact_run.items()):
        raise TrustedEvidenceError("CI run identity, revision, branch, or conclusion is invalid")
    if run_payload.get("event") != "push":
        raise TrustedEvidenceError("CI evidence must come from a push on protected main")
    if type(source_attempt) is not int or source_attempt <= 0:
        raise TrustedEvidenceError("CI source run attempt must be a positive integer")
    head_repository = run_payload.get("head_repository")
    if not isinstance(head_repository, Mapping) or head_repository.get("full_name") != REPOSITORY:
        raise TrustedEvidenceError("CI run head repository is invalid")
    run_times = {
        field: _platform_timestamp(run_payload.get(field), f"platform source run {field}", now=current)
        for field in ("created_at", "run_started_at", "updated_at")
    }
    if not run_times["created_at"] <= run_times["run_started_at"] <= run_times["updated_at"]:
        raise TrustedEvidenceError("CI source run timestamps are not monotonically ordered")
    if not isinstance(jobs_payload, Mapping):
        raise TrustedEvidenceError("CI jobs response must be an object")
    jobs = jobs_payload.get("jobs")
    total = jobs_payload.get("total_count")
    if not isinstance(jobs, list) or type(total) is not int or total != len(jobs) or total > 100:
        raise TrustedEvidenceError("CI jobs response is malformed, incomplete, or paginated")
    indexed: dict[str, Mapping[str, Any]] = {}
    for job in jobs:
        if not isinstance(job, Mapping) or not isinstance(job.get("name"), str):
            raise TrustedEvidenceError("CI jobs response contains a malformed job")
        name = str(job["name"])
        if name in indexed:
            raise TrustedEvidenceError(f"CI jobs response contains duplicate job {name}")
        indexed[name] = job

    groups = _required_job_names()
    expected_names = {name for names in groups.values() for name in names}
    if not isinstance(source_receipts, list) or len(source_receipts) != len(expected_names):
        raise TrustedEvidenceError("platform evidence requires one signed receipt for every point-bearing source job")
    receipts_by_name: dict[str, dict[str, Any]] = {}
    receipt_identities: set[str] = set()
    receipt_artifacts: set[str] = set()
    for candidate in source_receipts:
        name = candidate.get("job_name") if isinstance(candidate, Mapping) else None
        if not isinstance(name, str) or name in receipts_by_name:
            raise TrustedEvidenceError("platform source-job receipts contain a missing or duplicate job identity")
        job = indexed.get(name)
        if job is None:
            raise TrustedEvidenceError(f"platform source-job receipt references absent job {name}")
        receipt = _validate_platform_job_receipt(
            candidate,
            expected_revision=revision,
            source_run_id=source_run,
            source_run_attempt=source_attempt,
            source_event=str(run_payload["event"]),
            job=job,
            now=current,
        )
        identity = str(receipt["identity_sha256"])
        artifact_name = str(receipt["artifact_name"])
        if identity in receipt_identities or artifact_name in receipt_artifacts:
            raise TrustedEvidenceError("platform source-job receipt was reused")
        receipt_identities.add(identity)
        receipt_artifacts.add(artifact_name)
        receipts_by_name[name] = receipt
    if set(receipts_by_name) != expected_names:
        raise TrustedEvidenceError("platform source-job receipt inventory is incomplete or contains unknown jobs")

    if not isinstance(artifacts_payload, Mapping):
        raise TrustedEvidenceError("CI artifact response must be an object")
    artifacts = artifacts_payload.get("artifacts")
    artifact_total = artifacts_payload.get("total_count")
    if not isinstance(artifacts, list) or type(artifact_total) is not int or artifact_total != len(artifacts) or artifact_total > 100:
        raise TrustedEvidenceError("CI artifact response is malformed, incomplete, or paginated")
    current_prefix = f"{PLATFORM_RECEIPT_ARTIFACT_PREFIX}-{source_run}-{source_attempt}-"
    current_receipt_artifacts = [
        artifact
        for artifact in artifacts
        if isinstance(artifact, Mapping)
        and isinstance(artifact.get("name"), str)
        and str(artifact["name"]).startswith(current_prefix)
    ]
    if len(current_receipt_artifacts) != len(receipt_artifacts):
        raise TrustedEvidenceError("CI run has a missing, duplicate, or unexpected current-attempt receipt artifact")
    artifact_ids: set[int] = set()
    skew = timedelta(seconds=MAX_FUTURE_SKEW_SECONDS)
    for receipt in receipts_by_name.values():
        matches = [artifact for artifact in current_receipt_artifacts if artifact.get("name") == receipt["artifact_name"]]
        if len(matches) != 1:
            raise TrustedEvidenceError("platform receipt is not bound to exactly one source-run artifact")
        artifact = matches[0]
        artifact_id = artifact.get("id")
        artifact_run = artifact.get("workflow_run")
        if (
            type(artifact_id) is not int
            or artifact_id <= 0
            or artifact_id in artifact_ids
            or type(artifact.get("size_in_bytes")) is not int
            or artifact.get("size_in_bytes", 0) <= 0
            or artifact.get("expired") is not False
            or not isinstance(artifact_run, Mapping)
            or artifact_run.get("id") != source_run
            or artifact_run.get("head_sha") != revision
        ):
            raise TrustedEvidenceError("platform receipt artifact is empty, expired, duplicated, or cross-run")
        artifact_ids.add(artifact_id)
        generated_at = _platform_timestamp(receipt["generated_at"], "platform receipt generated_at", now=current)
        for field in ("created_at", "updated_at"):
            artifact_time = _platform_timestamp(
                artifact.get(field), f"platform receipt artifact {field}", now=current
            )
            if artifact_time < generated_at - skew or artifact_time > run_times["updated_at"] + skew:
                raise TrustedEvidenceError("platform receipt artifact timestamp falls outside its source-run window")

    platform_groups = {
        "windows_hosted_python_and_smoke": groups["python_windows_2025_vs2026_matrix"],
        "windows_11_arm_python_and_smoke": groups["windows_11_arm"],
        "ubuntu_python_tkinter_and_verifier": groups["python_ubuntu_matrix"] + groups["tkinter_gui_lifecycle"],
        "macos_14_15_26_python_and_verifier": groups["python_macos_14_15_26_matrix"],
    }
    for check_name, names in platform_groups.items():
        if any(name not in indexed for name in names):
            raise TrustedEvidenceError(f"platform evidence for {check_name} is incomplete")
    return PLATFORM_CI_CHECKS, PLATFORM_CHECKS


def build_platform_manifests(
    run_payload: Any,
    jobs_payload: Any,
    artifacts_payload: Any,
    source_receipts: Any,
    *,
    repository: str,
    source_revision: str,
    source_run_id: int,
    run_id: int,
    run_attempt: int,
    workflow_ref: str,
    generated_at: datetime | None = None,
) -> dict[str, dict[str, Any]]:
    revision = _require_revision(source_revision)
    ci_checks, platform_checks = derive_platform_checks(
        run_payload,
        jobs_payload,
        artifacts_payload,
        source_receipts,
        expected_revision=revision,
        source_run_id=source_run_id,
        now=generated_at,
    )
    source_run_attempt = run_payload.get("run_attempt")
    if type(source_run_attempt) is not int or source_run_attempt <= 0:
        raise TrustedEvidenceError("CI source run attempt must be a positive integer")
    canonical_receipts = sorted((dict(receipt) for receipt in source_receipts), key=lambda item: item["job_name"])
    source = f"https://github.com/{REPOSITORY}/actions/runs/{source_run_id}"
    output: dict[str, dict[str, Any]] = {}
    for evidence_type, checks in (("platform-ci", ci_checks), ("platform", platform_checks)):
        metadata = _evidence_metadata(
            evidence_type,
            repository=repository,
            source_revision=revision,
            run_id=run_id,
            run_attempt=run_attempt,
            workflow_ref=workflow_ref,
            generated_at=generated_at,
        )
        manifest = _base_manifest(
            evidence_type,
            source=source,
            source_revision=revision,
            checks=checks,
            metadata=metadata,
        )
        manifest["source_run_id"] = source_run_id
        manifest["source_run_attempt"] = source_run_attempt
        manifest["source_receipts"] = canonical_receipts
        if evidence_type == "platform":
            manifest["targets"] = list(_PLATFORM_TARGETS)
        output[evidence_type] = manifest
    return output


def _review_funded_collector_job(
    run_jobs: Any,
    *,
    source_revision: str,
    run_id: int,
    run_attempt: int,
) -> dict[str, Any]:
    if not isinstance(run_jobs, Mapping) or not isinstance(run_jobs.get("jobs"), list):
        raise TrustedEvidenceError("funded workflow job metadata must contain a jobs list")
    jobs = run_jobs["jobs"]
    total_count = run_jobs.get("total_count")
    if type(total_count) is not int or total_count != len(jobs) or total_count > 100:
        raise TrustedEvidenceError("funded workflow job metadata is incomplete or paginated")
    matches = [
        job
        for job in jobs
        if isinstance(job, Mapping) and job.get("name") == FUNDED_COLLECTOR_JOB
    ]
    if len(matches) != 1:
        raise TrustedEvidenceError("funded evidence requires exactly one bounded collector job")
    job = matches[0]
    labels = job.get("labels")
    normalized_labels = (
        [str(label).strip().lower() for label in labels]
        if isinstance(labels, list) and all(isinstance(label, str) for label in labels)
        else []
    )
    if len(normalized_labels) != len(set(normalized_labels)) or set(normalized_labels) != FUNDED_COLLECTOR_LABELS:
        raise TrustedEvidenceError("funded collector job does not use the exact persistent production labels")
    if (
        type(job.get("id")) is not int
        or job["id"] < 1
        or job.get("run_id") != run_id
        or job.get("run_attempt") != run_attempt
        or job.get("head_sha") != source_revision
        or job.get("status") != "completed"
        or job.get("conclusion") != "success"
    ):
        raise TrustedEvidenceError("funded collector job identity or outcome is invalid")
    started_at, started_at_text = _bound_utc_timestamp(
        job.get("started_at"), "funded collector started_at"
    )
    completed_at, completed_at_text = _bound_utc_timestamp(
        job.get("completed_at"), "funded collector completed_at"
    )
    if completed_at < started_at:
        raise TrustedEvidenceError("funded collector completion precedes its start")
    steps = job.get("steps")
    if not isinstance(steps, list):
        raise TrustedEvidenceError("funded collector job steps are missing")
    observed: list[str] = []
    for required in FUNDED_COLLECTOR_STEPS:
        matches_for_step = [
            step
            for step in steps
            if isinstance(step, Mapping) and step.get("name") == required
        ]
        if len(matches_for_step) != 1:
            raise TrustedEvidenceError(f"funded collector requires exactly one successful {required!r} step")
        step = matches_for_step[0]
        if step.get("status") != "completed" or step.get("conclusion") != "success":
            raise TrustedEvidenceError(f"funded collector step {required!r} did not succeed")
        observed.append(required)
    positions = [
        next(index for index, step in enumerate(steps) if isinstance(step, Mapping) and step.get("name") == name)
        for name in FUNDED_COLLECTOR_STEPS
    ]
    if positions != sorted(positions):
        raise TrustedEvidenceError("funded collector safety steps ran out of order")
    return {
        "job_id": job["id"],
        "run_id": run_id,
        "run_attempt": run_attempt,
        "head_sha": source_revision,
        "name": FUNDED_COLLECTOR_JOB,
        "status": "completed",
        "conclusion": "success",
        "runner_environment": "self-hosted",
        "labels": sorted(FUNDED_COLLECTOR_LABELS),
        "steps": observed,
        "started_at": started_at_text,
        "completed_at": completed_at_text,
    }


def _review_production_environment(
    environment_config: Any,
    *,
    collector_job: Mapping[str, Any],
) -> dict[str, Any]:
    error = "production environment protections are missing, malformed, or not fixed before collection"
    if not isinstance(environment_config, Mapping) or set(environment_config) != _FUNDED_ENVIRONMENT_API_FIELDS:
        raise TrustedEvidenceError(error)
    environment_id = environment_config.get("id")
    if (
        type(environment_id) is not int
        or environment_id < 1
        or environment_config.get("name") != FUNDED_ENVIRONMENT_NAME
        or environment_config.get("url") != FUNDED_ENVIRONMENT_SOURCE
        or not isinstance(environment_config.get("node_id"), str)
        or not environment_config["node_id"]
        or not isinstance(environment_config.get("html_url"), str)
        or not environment_config["html_url"].startswith("https://github.com/")
    ):
        raise TrustedEvidenceError(error)

    created_at, _created_at_text = _bound_utc_timestamp(
        environment_config.get("created_at"), "production environment created_at"
    )
    updated_at, updated_at_text = _bound_utc_timestamp(
        environment_config.get("updated_at"), "production environment updated_at"
    )
    collector_started_at, collector_started_at_text = _bound_utc_timestamp(
        collector_job.get("started_at"), "funded collector started_at"
    )
    if created_at > updated_at or updated_at > collector_started_at:
        raise TrustedEvidenceError(error)

    branch_policy = environment_config.get("deployment_branch_policy")
    if (
        not isinstance(branch_policy, Mapping)
        or set(branch_policy) != {"protected_branches", "custom_branch_policies"}
        or branch_policy.get("protected_branches") is not True
        or branch_policy.get("custom_branch_policies") is not False
    ):
        raise TrustedEvidenceError(error)

    protection_rules = environment_config.get("protection_rules")
    if not isinstance(protection_rules, list) or not 1 <= len(protection_rules) <= 6:
        raise TrustedEvidenceError(error)
    rule_ids: set[int] = set()
    rule_types: list[str] = []
    for rule in protection_rules:
        if not isinstance(rule, Mapping):
            raise TrustedEvidenceError(error)
        rule_id = rule.get("id")
        rule_type = rule.get("type")
        if (
            type(rule_id) is not int
            or rule_id < 1
            or rule_id in rule_ids
            or not isinstance(rule_type, str)
            or not rule_type
        ):
            raise TrustedEvidenceError(error)
        rule_ids.add(rule_id)
        rule_types.append(rule_type)
    if rule_types.count("required_reviewers") != 1 or rule_types.count("branch_policy") != 1:
        raise TrustedEvidenceError(error)

    required_rule = protection_rules[rule_types.index("required_reviewers")]
    if (
        set(required_rule) != _FUNDED_REQUIRED_REVIEWERS_RULE_FIELDS
        or required_rule.get("prevent_self_review") is not True
        or not isinstance(required_rule.get("node_id"), str)
        or not required_rule["node_id"]
    ):
        raise TrustedEvidenceError(error)
    raw_reviewers = required_rule.get("reviewers")
    if not isinstance(raw_reviewers, list) or not 1 <= len(raw_reviewers) <= 6:
        raise TrustedEvidenceError(error)
    reviewer_summaries: list[dict[str, Any]] = []
    reviewer_keys: set[tuple[str, int]] = set()
    reviewer_identities: set[tuple[str, str]] = set()
    for item in raw_reviewers:
        if not isinstance(item, Mapping) or set(item) != _FUNDED_REVIEWER_API_FIELDS:
            raise TrustedEvidenceError(error)
        reviewer_type = item.get("type")
        reviewer = item.get("reviewer")
        if reviewer_type not in {"User", "Team"} or not isinstance(reviewer, Mapping):
            raise TrustedEvidenceError(error)
        reviewer_id = reviewer.get("id")
        identity_field = "login" if reviewer_type == "User" else "slug"
        identity = reviewer.get(identity_field)
        if (
            type(reviewer_id) is not int
            or reviewer_id < 1
            or not isinstance(identity, str)
            or not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", identity)
        ):
            raise TrustedEvidenceError(error)
        key = (reviewer_type, reviewer_id)
        identity_key = (reviewer_type, identity.casefold())
        if key in reviewer_keys or identity_key in reviewer_identities:
            raise TrustedEvidenceError(error)
        reviewer_keys.add(key)
        reviewer_identities.add(identity_key)
        reviewer_summaries.append(
            {"type": reviewer_type, "id": reviewer_id, "identity": identity}
        )
    reviewer_summaries.sort(key=lambda item: (str(item["type"]), int(item["id"])))
    return {
        "schema_version": 1,
        "environment_id": environment_id,
        "environment_name": FUNDED_ENVIRONMENT_NAME,
        "source": FUNDED_ENVIRONMENT_SOURCE,
        "updated_at": updated_at_text,
        "collector_started_at": collector_started_at_text,
        "required_reviewers": reviewer_summaries,
        "prevent_self_review": True,
        "protected_branches": True,
    }


def _review_funded_environment_approval(
    run_approvals: Any,
    *,
    environment_config: Mapping[str, Any],
    environment_protection: Mapping[str, Any],
    run_id: int,
    run_attempt: int,
) -> dict[str, Any]:
    error = "funded evidence requires one exact run-specific approved production review"
    # The review-history endpoint is scoped to a run, not an attempt. Refuse reruns
    # so a prior attempt's approval cannot be replayed for a later funded action.
    if run_attempt != 1 or not isinstance(run_approvals, list) or len(run_approvals) != 1:
        raise TrustedEvidenceError(error)
    record = run_approvals[0]
    if not isinstance(record, Mapping) or set(record) != _FUNDED_APPROVAL_RECORD_FIELDS:
        raise TrustedEvidenceError(error)
    comment = record.get("comment")
    if (
        record.get("state") != "approved"
        or not isinstance(comment, str)
        or len(comment.encode("utf-8")) > 4096
        or "\x00" in comment
    ):
        raise TrustedEvidenceError(error)

    environments = record.get("environments")
    if not isinstance(environments, list) or len(environments) != 1:
        raise TrustedEvidenceError(error)
    approved_environment = environments[0]
    if (
        not isinstance(approved_environment, Mapping)
        or set(approved_environment) != _FUNDED_APPROVAL_ENVIRONMENT_FIELDS
    ):
        raise TrustedEvidenceError(error)
    expected_environment = {
        field: environment_config.get(field) for field in _FUNDED_APPROVAL_ENVIRONMENT_FIELDS
    }
    if dict(approved_environment) != expected_environment:
        raise TrustedEvidenceError(error)

    user = record.get("user")
    if not isinstance(user, Mapping) or set(user) != _FUNDED_APPROVAL_USER_FIELDS:
        raise TrustedEvidenceError(error)
    reviewer_id = user.get("id")
    reviewer_login = user.get("login")
    if (
        type(reviewer_id) is not int
        or reviewer_id < 1
        or not isinstance(reviewer_login, str)
        or not re.fullmatch(r"[A-Za-z0-9-]{1,39}", reviewer_login)
        or user.get("type") != "User"
        or type(user.get("site_admin")) is not bool
        or not isinstance(user.get("node_id"), str)
        or not user["node_id"]
        or len(user["node_id"]) > 256
        or not isinstance(user.get("gravatar_id"), str)
        or len(user["gravatar_id"]) > 256
    ):
        raise TrustedEvidenceError(error)
    for field in _FUNDED_APPROVAL_USER_FIELDS - {
        "login",
        "id",
        "node_id",
        "gravatar_id",
        "type",
        "site_admin",
    }:
        value = user.get(field)
        if not isinstance(value, str) or not value.startswith("https://") or len(value) > 2048:
            raise TrustedEvidenceError(error)

    environment_id = environment_protection.get("environment_id")
    if (
        type(environment_id) is not int
        or approved_environment.get("id") != environment_id
        or approved_environment.get("name") != FUNDED_ENVIRONMENT_NAME
        or approved_environment.get("url") != FUNDED_ENVIRONMENT_SOURCE
    ):
        raise TrustedEvidenceError(error)
    return {
        "schema_version": 1,
        "run_id": run_id,
        "run_attempt": run_attempt,
        "environment_id": environment_id,
        "environment_name": FUNDED_ENVIRONMENT_NAME,
        "state": "approved",
        "reviewer": {"id": reviewer_id, "login": reviewer_login},
        "source": f"https://api.github.com/repos/{REPOSITORY}/actions/runs/{run_id}/approvals",
    }


def _review_funded_token_policy(
    token_policy: Any,
    *,
    environment_protection: Mapping[str, Any],
    collector_job: Mapping[str, Any],
    funded_check: Mapping[str, Any],
) -> dict[str, Any]:
    error = "funded token policy is missing, malformed, stale, or does not allow the selected token"
    if not isinstance(token_policy, Mapping) or set(token_policy) != _FUNDED_POLICY_API_FIELDS:
        raise TrustedEvidenceError(error)
    if token_policy.get("name") != FUNDED_TOKEN_ALLOWLIST_VARIABLE:
        raise TrustedEvidenceError(error)
    created_at, _created_at_text = _bound_utc_timestamp(
        token_policy.get("created_at"), "funded token policy created_at"
    )
    updated_at, updated_at_text = _bound_utc_timestamp(
        token_policy.get("updated_at"), "funded token policy updated_at"
    )
    collector_started_at, _collector_started_at_text = _bound_utc_timestamp(
        collector_job.get("started_at"), "funded collector started_at"
    )
    if created_at > updated_at or updated_at > collector_started_at:
        raise TrustedEvidenceError(error)
    try:
        token_ids = parse_funded_token_allowlist(token_policy.get("value"))
    except ValueError as exc:
        raise TrustedEvidenceError(error) from exc
    selected_token_id = funded_check.get("token_id")
    receipt = funded_check.get("funded_token_policy_receipt")
    if (
        not isinstance(selected_token_id, str)
        or selected_token_id not in token_ids
        or not isinstance(receipt, Mapping)
        or set(receipt) != _FUNDED_POLICY_RECEIPT_FIELDS
        or receipt.get("schema_version") != 1
        or receipt.get("variable_name") != FUNDED_TOKEN_ALLOWLIST_VARIABLE
        or receipt.get("token_ids") != list(token_ids)
        or receipt.get("allowlist_sha256") != funded_token_allowlist_sha256(token_ids)
        or receipt.get("selected_token_id") != selected_token_id
    ):
        raise TrustedEvidenceError(error)
    environment_id = environment_protection.get("environment_id")
    if type(environment_id) is not int or environment_id < 1:
        raise TrustedEvidenceError(error)
    return {
        "schema_version": 1,
        "environment_id": environment_id,
        "environment_name": FUNDED_ENVIRONMENT_NAME,
        "variable_name": FUNDED_TOKEN_ALLOWLIST_VARIABLE,
        "source": FUNDED_POLICY_SOURCE,
        "updated_at": updated_at_text,
        "token_ids": list(token_ids),
        "allowlist_sha256": funded_token_allowlist_sha256(token_ids),
        "selected_token_id": selected_token_id,
    }


def _review_funded_recovery_journal(
    report: Mapping[str, Any],
    journal: Any,
    *,
    journal_sha256: str,
    source_revision: str,
    run_id: int,
    run_attempt: int,
) -> dict[str, Any]:
    error = "funded recovery journal is unresolved, ambiguous, or not bound to this workflow run"
    if not isinstance(journal, Mapping) or set(journal) != _FUNDED_RECOVERY_JOURNAL_FIELDS:
        raise TrustedEvidenceError(error)
    expected_nonce = f"{source_revision}:{run_id}:{run_attempt}"
    if (
        journal.get("schema_version") != 2
        or journal.get("market_id") != "polymarket"
        or journal.get("source_revision") != source_revision
        or journal.get("workflow_run_id") != run_id
        or journal.get("workflow_run_attempt") != run_attempt
        or journal.get("evidence_nonce") != expected_nonce
        or journal.get("stage") != "cancel_verified"
        or journal.get("resolved") is not True
        or journal.get("manual_reconciliation_required") is not False
        or journal.get("zero_fill_verified") is not True
        or journal.get("post_only") is not True
        or journal.get("tif") != "GTC"
        or journal.get("side") not in {"BUY", "SELL"}
        or type(journal.get("sequence")) is not int
        or journal["sequence"] < 3
    ):
        raise TrustedEvidenceError(error)
    try:
        if str(uuid.UUID(str(journal.get("run_id")))) != journal.get("run_id"):
            raise ValueError
    except (TypeError, ValueError, AttributeError) as exc:
        raise TrustedEvidenceError(error) from exc
    journal_times: dict[str, datetime] = {}
    for field in ("run_started_at", "updated_at"):
        value = journal.get(field)
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError as exc:
            raise TrustedEvidenceError(error) from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise TrustedEvidenceError(error)
        journal_times[field] = parsed
    if journal_times["updated_at"] < journal_times["run_started_at"]:
        raise TrustedEvidenceError(error)
    for field in ("token_id", "order_id", "account_address"):
        value = journal.get(field)
        if (
            not isinstance(value, str)
            or not value
            or value != value.strip()
            or len(value) > 256
            or not value.isprintable()
        ):
            raise TrustedEvidenceError(error)
    if not _EVM_ADDRESS_RE.fullmatch(journal["account_address"]):
        raise TrustedEvidenceError(error)
    price = journal.get("price")
    size = journal.get("size")
    if (
        type(price) not in (int, float)
        or type(size) not in (int, float)
        or not math.isfinite(price)
        or not math.isfinite(size)
        or not 0 < price < 1
        or not 0 < size <= 5.0
        or price * size > 1.0
    ):
        raise TrustedEvidenceError(error)
    funded = report.get("funded_live_order_check")
    if not isinstance(funded, Mapping):
        raise TrustedEvidenceError(error)
    audit = funded.get("audit") if isinstance(funded.get("audit"), Mapping) else {}
    receipt = funded.get("recovery_journal_receipt")
    if not isinstance(receipt, Mapping) or set(receipt) != _FUNDED_RECOVERY_RECEIPT_FIELDS:
        raise TrustedEvidenceError(error)
    if (
        receipt.get("schema_version") != 1
        or receipt.get("sha256") != journal_sha256
        or receipt.get("source_revision") != source_revision
        or receipt.get("workflow_run_id") != run_id
        or receipt.get("workflow_run_attempt") != run_attempt
        or receipt.get("evidence_nonce") != expected_nonce
        or receipt.get("order_id") != journal.get("order_id")
        or receipt.get("stage") != "cancel_verified"
        or receipt.get("resolved") is not True
        or audit.get("order_id") != journal.get("order_id")
        or funded.get("token_id") != journal.get("token_id")
        or funded.get("side") != journal.get("side")
        or funded.get("price") != price
        or funded.get("size") != size
        or funded.get("tif") != "GTC"
    ):
        raise TrustedEvidenceError(error)
    return dict(receipt)


def build_live_manifest(
    report: Any,
    *,
    tier: str,
    repository: str,
    source_revision: str,
    run_id: int,
    run_attempt: int,
    workflow_ref: str,
    recovery_journal: Any = None,
    recovery_journal_sha256: str = "",
    run_jobs: Any = None,
    environment_config: Any = None,
    run_approvals: Any = None,
    token_policy: Any = None,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    from polymarket.live_report_schema import validate_live_validation_report
    from polymarket.live_reports import live_validation_report_promotion

    if tier not in {"credentialed", "funded"}:
        raise TrustedEvidenceError("tier must be credentialed or funded")
    if not isinstance(report, Mapping):
        raise TrustedEvidenceError("live report must be an object")
    revision = _require_revision(source_revision)
    validation = validate_live_validation_report(report)
    if validation.get("ok") is not True:
        raise TrustedEvidenceError("live report failed schema validation")
    provenance = report.get("source_provenance")
    if (
        not isinstance(provenance, Mapping)
        or provenance.get("stable") is not True
        or provenance.get("source_revision") != revision
    ):
        raise TrustedEvidenceError("live report is not bound to the exact clean source revision")
    promotion = live_validation_report_promotion(report)
    field = "can_promote_credential_live_verified" if tier == "credentialed" else "can_promote_funded_live_verified"
    if promotion.get(field) is not True:
        reasons = promotion.get("blocked_reasons")
        detail = "; ".join(str(item) for item in reasons) if isinstance(reasons, list) else "promotion failed"
        raise TrustedEvidenceError(f"live report is not {tier} promotion-eligible: {detail}")
    funded_check = report.get("funded_live_order_check")
    funded_live_action = isinstance(funded_check, Mapping) and funded_check.get("live_action") is True
    if tier == "funded" and not funded_live_action:
        raise TrustedEvidenceError("funded evidence requires a real live_action=true order/cancel audit")
    recovery_receipt: dict[str, Any] | None = None
    collector_job: dict[str, Any] | None = None
    environment_protection: dict[str, Any] | None = None
    environment_approval: dict[str, Any] | None = None
    funded_token_policy: dict[str, Any] | None = None
    if tier == "funded":
        if not _HASH_RE.fullmatch(recovery_journal_sha256):
            raise TrustedEvidenceError("funded evidence requires the exact recovery journal SHA-256")
        collector_job = _review_funded_collector_job(
            run_jobs,
            source_revision=revision,
            run_id=run_id,
            run_attempt=run_attempt,
        )
        environment_protection = _review_production_environment(
            environment_config,
            collector_job=collector_job,
        )
        environment_approval = _review_funded_environment_approval(
            run_approvals,
            environment_config=environment_config,
            environment_protection=environment_protection,
            run_id=run_id,
            run_attempt=run_attempt,
        )
        funded_token_policy = _review_funded_token_policy(
            token_policy,
            environment_protection=environment_protection,
            collector_job=collector_job,
            funded_check=funded_check,
        )
        recovery_receipt = _review_funded_recovery_journal(
            report,
            recovery_journal,
            journal_sha256=recovery_journal_sha256,
            source_revision=revision,
            run_id=run_id,
            run_attempt=run_attempt,
        )

    evidence_type = f"{tier}-polymarket"
    metadata = _evidence_metadata(
        evidence_type,
        repository=repository,
        source_revision=revision,
        run_id=run_id,
        run_attempt=run_attempt,
        workflow_ref=workflow_ref,
        generated_at=generated_at,
    )
    manifest = _base_manifest(
        evidence_type,
        source=f"https://github.com/{REPOSITORY}/actions/runs/{run_id}",
        source_revision=revision,
        checks=REQUIRED_CHECKS[evidence_type],
        metadata=metadata,
    )
    canonical_report = canonical_json_bytes(dict(report))
    manifest.update(
        {
            "target_tier": f"{tier}_live_verified",
            "report_sha256": hashlib.sha256(canonical_report).hexdigest(),
            "live_action": tier == "funded",
            "live_report": dict(report),
        }
    )
    if tier == "funded":
        manifest["recovery_journal_sha256"] = recovery_journal_sha256
        manifest["recovery_receipt"] = recovery_receipt
        manifest["collector_job"] = collector_job
        manifest["environment_protection"] = environment_protection
        manifest["environment_approval"] = environment_approval
        manifest["funded_token_policy"] = funded_token_policy
    return manifest


def _expected_source(payload: Mapping[str, Any], evidence_type: str) -> str:
    if evidence_type == "repository-settings":
        return f"https://api.github.com/repos/{REPOSITORY}/branches/main/protection"
    if evidence_type == "release-environment":
        return f"https://api.github.com/repos/{REPOSITORY}/environments"
    if evidence_type in {"platform-ci", "platform"}:
        return f"https://github.com/{REPOSITORY}/actions/runs/{payload.get('source_run_id')}"
    evidence = payload.get("evidence")
    run_id = evidence.get("run_id") if isinstance(evidence, Mapping) else None
    return f"https://github.com/{REPOSITORY}/actions/runs/{run_id}"


def _funded_control_summary_errors(
    payload: Mapping[str, Any],
    *,
    report: Mapping[str, Any],
    collector: Any,
) -> list[str]:
    errors: list[str] = []
    collector_started_at: datetime | None = None
    if isinstance(collector, Mapping):
        try:
            collector_started_at, collector_started_at_text = _bound_utc_timestamp(
                collector.get("started_at"), "funded collector started_at"
            )
            completed_at, _completed_at_text = _bound_utc_timestamp(
                collector.get("completed_at"), "funded collector completed_at"
            )
            if completed_at < collector_started_at:
                raise TrustedEvidenceError("funded collector completion precedes its start")
        except TrustedEvidenceError:
            errors.append("funded collector timestamps are invalid")
            collector_started_at_text = ""
    else:
        collector_started_at_text = ""

    environment = payload.get("environment_protection")
    if not isinstance(environment, Mapping) or set(environment) != _FUNDED_ENVIRONMENT_SUMMARY_FIELDS:
        errors.append("funded production environment protection summary is invalid")
        environment = {}
    reviewers = environment.get("required_reviewers")
    reviewer_summaries: list[dict[str, Any]] = []
    if not isinstance(reviewers, list) or not 1 <= len(reviewers) <= 6:
        errors.append("funded production environment reviewer summary is invalid")
    else:
        reviewer_keys: set[tuple[str, int]] = set()
        identity_keys: set[tuple[str, str]] = set()
        for reviewer in reviewers:
            if not isinstance(reviewer, Mapping) or set(reviewer) != _FUNDED_REVIEWER_SUMMARY_FIELDS:
                errors.append("funded production environment reviewer summary is invalid")
                break
            reviewer_type = reviewer.get("type")
            reviewer_id = reviewer.get("id")
            identity = reviewer.get("identity")
            if (
                reviewer_type not in {"User", "Team"}
                or type(reviewer_id) is not int
                or reviewer_id < 1
                or not isinstance(identity, str)
                or not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", identity)
            ):
                errors.append("funded production environment reviewer summary is invalid")
                break
            key = (reviewer_type, reviewer_id)
            identity_key = (reviewer_type, identity.casefold())
            if key in reviewer_keys or identity_key in identity_keys:
                errors.append("funded production environment reviewer summary is invalid")
                break
            reviewer_keys.add(key)
            identity_keys.add(identity_key)
            reviewer_summaries.append(dict(reviewer))
        if reviewer_summaries and reviewer_summaries != sorted(
            reviewer_summaries,
            key=lambda item: (str(item["type"]), int(item["id"])),
        ):
            errors.append("funded production environment reviewer order is not canonical")

    environment_id = environment.get("environment_id")
    if (
        environment.get("schema_version") != 1
        or type(environment_id) is not int
        or environment_id < 1
        or environment.get("environment_name") != FUNDED_ENVIRONMENT_NAME
        or environment.get("source") != FUNDED_ENVIRONMENT_SOURCE
        or environment.get("collector_started_at") != collector_started_at_text
        or environment.get("prevent_self_review") is not True
        or environment.get("protected_branches") is not True
    ):
        errors.append("funded production environment protection summary is invalid")
    try:
        environment_updated_at, _environment_updated_at_text = _bound_utc_timestamp(
            environment.get("updated_at"), "production environment updated_at"
        )
        if collector_started_at is None or environment_updated_at > collector_started_at:
            raise TrustedEvidenceError("production environment changed after funded collection began")
    except TrustedEvidenceError:
        errors.append("funded production environment timing is invalid")

    approval = payload.get("environment_approval")
    if not isinstance(approval, Mapping) or set(approval) != _FUNDED_APPROVAL_SUMMARY_FIELDS:
        errors.append("funded run-specific production approval summary is invalid")
        approval = {}
    approval_reviewer = approval.get("reviewer")
    reviewer_id = approval_reviewer.get("id") if isinstance(approval_reviewer, Mapping) else None
    reviewer_login = (
        approval_reviewer.get("login") if isinstance(approval_reviewer, Mapping) else None
    )
    collector_run_id = collector.get("run_id") if isinstance(collector, Mapping) else None
    collector_run_attempt = collector.get("run_attempt") if isinstance(collector, Mapping) else None
    if (
        approval.get("schema_version") != 1
        or approval.get("run_id") != collector_run_id
        or approval.get("run_attempt") != collector_run_attempt
        or collector_run_attempt != 1
        or approval.get("environment_id") != environment_id
        or approval.get("environment_name") != FUNDED_ENVIRONMENT_NAME
        or approval.get("state") != "approved"
        or not isinstance(approval_reviewer, Mapping)
        or set(approval_reviewer) != _FUNDED_APPROVAL_REVIEWER_SUMMARY_FIELDS
        or type(reviewer_id) is not int
        or reviewer_id < 1
        or not isinstance(reviewer_login, str)
        or not re.fullmatch(r"[A-Za-z0-9-]{1,39}", reviewer_login)
        or approval.get("source")
        != f"https://api.github.com/repos/{REPOSITORY}/actions/runs/{collector_run_id}/approvals"
    ):
        errors.append("funded run-specific production approval binding is invalid")

    policy = payload.get("funded_token_policy")
    if not isinstance(policy, Mapping) or set(policy) != _FUNDED_TOKEN_POLICY_SUMMARY_FIELDS:
        errors.append("funded token policy summary is invalid")
        policy = {}
    funded = report.get("funded_live_order_check")
    selected_token_id = funded.get("token_id") if isinstance(funded, Mapping) else None
    policy_receipt = (
        funded.get("funded_token_policy_receipt") if isinstance(funded, Mapping) else None
    )
    try:
        token_ids = canonical_funded_token_allowlist(policy.get("token_ids"))
    except ValueError:
        errors.append("funded token policy token inventory is invalid")
        token_ids = ()
    if token_ids and list(token_ids) != policy.get("token_ids"):
        errors.append("funded token policy token order is not canonical")
    expected_policy_hash = funded_token_allowlist_sha256(token_ids) if token_ids else ""
    if (
        policy.get("schema_version") != 1
        or policy.get("environment_id") != environment_id
        or policy.get("environment_name") != FUNDED_ENVIRONMENT_NAME
        or policy.get("variable_name") != FUNDED_TOKEN_ALLOWLIST_VARIABLE
        or policy.get("source") != FUNDED_POLICY_SOURCE
        or policy.get("selected_token_id") != selected_token_id
        or selected_token_id not in token_ids
        or policy.get("allowlist_sha256") != expected_policy_hash
        or not isinstance(policy_receipt, Mapping)
        or set(policy_receipt) != _FUNDED_POLICY_RECEIPT_FIELDS
        or policy_receipt.get("schema_version") != 1
        or policy_receipt.get("variable_name") != FUNDED_TOKEN_ALLOWLIST_VARIABLE
        or policy_receipt.get("token_ids") != list(token_ids)
        or policy_receipt.get("allowlist_sha256") != expected_policy_hash
        or policy_receipt.get("selected_token_id") != selected_token_id
    ):
        errors.append("funded token policy binding is invalid")
    try:
        policy_updated_at, _policy_updated_at_text = _bound_utc_timestamp(
            policy.get("updated_at"), "funded token policy updated_at"
        )
        if collector_started_at is None or policy_updated_at > collector_started_at:
            raise TrustedEvidenceError("funded token policy changed after collection began")
    except TrustedEvidenceError:
        errors.append("funded token policy timing is invalid")
    return errors


def validate_manifest(
    payload: Any,
    *,
    expected_evidence_type: str,
    expected_revision: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    errors: list[str] = []
    if expected_evidence_type not in WORKFLOW_CONTRACTS:
        return {"ok": False, "errors": ["evidence type has no trusted workflow contract"]}
    revision = str(expected_revision or "").strip().lower()
    if not _COMMIT_RE.fullmatch(revision):
        return {"ok": False, "errors": ["expected revision must be a lowercase 40-character commit SHA"]}
    if not isinstance(payload, Mapping):
        return {"ok": False, "errors": ["trusted evidence must be a JSON object"]}
    expected_fields = _TYPE_FIELDS[expected_evidence_type]
    if set(payload) != expected_fields:
        errors.append("trusted evidence does not match the exact top-level field contract")
    exact = {
        "schema_version": SCHEMA_VERSION,
        "report_type": REPORT_TYPE,
        "evidence_type": expected_evidence_type,
        "verified": True,
        "scope": _EXACT_SCOPES[expected_evidence_type],
        "source_revision": revision,
    }
    if any(type(payload.get(key)) is not type(value) or payload.get(key) != value for key, value in exact.items()):
        errors.append("trusted evidence type, schema, scope, verification, or revision is invalid")
    if payload.get("source") != _expected_source(payload, expected_evidence_type):
        errors.append("trusted evidence source URL is invalid")

    checks = payload.get("checks")
    required_checks = REQUIRED_CHECKS[expected_evidence_type]
    if not isinstance(checks, list) or len(checks) != len(required_checks):
        errors.append("trusted evidence checks do not match the exact contract")
    else:
        observed_names: list[str] = []
        for check in checks:
            if not isinstance(check, Mapping) or set(check) != {"name", "status"} or check.get("status") != "pass":
                errors.append("trusted evidence contains a malformed or failed check")
                break
            observed_names.append(str(check.get("name") or ""))
        if tuple(observed_names) != required_checks:
            errors.append("trusted evidence check order or inventory is invalid")

    evidence = payload.get("evidence")
    contract = WORKFLOW_CONTRACTS[expected_evidence_type]
    if not isinstance(evidence, Mapping) or set(evidence) != _EVIDENCE_FIELDS:
        errors.append("trusted evidence metadata does not match the exact contract")
        evidence = {}
    run_id = evidence.get("run_id")
    run_attempt = evidence.get("run_attempt")
    exact_metadata = {
        "schema_version": EVIDENCE_METADATA_SCHEMA_VERSION,
        "repository": REPOSITORY,
        "source_revision": revision,
        "workflow": contract["workflow"],
        "workflow_name": contract["workflow_name"],
        "workflow_ref": f"{REPOSITORY}/{contract['workflow']}@{TRUSTED_REF}",
        "event": "workflow_dispatch",
        "runner_environment": "github-hosted",
        "job": contract["job"],
    }
    if any(type(evidence.get(key)) is not type(value) or evidence.get(key) != value for key, value in exact_metadata.items()):
        errors.append("trusted evidence repository, workflow, event, runner, job, or revision metadata is invalid")
    if type(run_id) is not int or run_id <= 0 or type(run_attempt) is not int or run_attempt <= 0:
        errors.append("trusted evidence run_id and run_attempt must be positive integers")
    elif evidence.get("artifact_name") != artifact_name(expected_evidence_type, revision, run_id, run_attempt):
        errors.append("trusted evidence artifact name is invalid")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("trusted evidence validation clock must include a timezone")
    generated_at = _parse_timestamp(evidence.get("generated_at"), now=current, errors=errors)

    source_run_id: int | None = None
    source_run_attempt: int | None = None
    source_receipts: list[dict[str, Any]] = []
    governance_digest = ""
    if expected_evidence_type in {"repository-settings", "release-environment"}:
        candidate_digest = payload.get("governance_state_sha256")
        if not isinstance(candidate_digest, str) or not _HASH_RE.fullmatch(candidate_digest):
            errors.append("governance state digest is invalid")
        else:
            governance_digest = candidate_digest
    if expected_evidence_type in {"platform-ci", "platform"}:
        candidate = payload.get("source_run_id")
        if type(candidate) is not int or candidate <= 0:
            errors.append("platform evidence source_run_id must be a positive integer")
        else:
            source_run_id = candidate
        attempt_candidate = payload.get("source_run_attempt")
        if type(attempt_candidate) is not int or attempt_candidate <= 0:
            errors.append("platform evidence source_run_attempt must be a positive integer")
        else:
            source_run_attempt = attempt_candidate
        receipt_candidates = payload.get("source_receipts")
        expected_names = set(_platform_job_contracts())
        if not isinstance(receipt_candidates, list) or len(receipt_candidates) != len(expected_names):
            errors.append("platform evidence requires one source receipt per point-bearing job")
        elif source_run_id is not None and source_run_attempt is not None:
            observed_names: set[str] = set()
            observed_identities: set[str] = set()
            observed_artifacts: set[str] = set()
            events: set[str] = set()
            for candidate_receipt in receipt_candidates:
                if not isinstance(candidate_receipt, Mapping):
                    errors.append("platform source receipt must be an object")
                    break
                job_name = candidate_receipt.get("job_name")
                if not isinstance(job_name, str) or job_name in observed_names:
                    errors.append("platform source receipts have a missing or duplicate job identity")
                    break
                try:
                    receipt_generated_at = _platform_timestamp(
                        candidate_receipt.get("generated_at"),
                        "platform receipt generated_at",
                        now=current,
                    )
                    rebuilt = build_platform_job_receipt(
                        repository=REPOSITORY,
                        source_revision=revision,
                        run_id=source_run_id,
                        run_attempt=source_run_attempt,
                        workflow_ref=f"{REPOSITORY}/{PLATFORM_SOURCE_WORKFLOW}@{TRUSTED_REF}",
                        event=str(candidate_receipt.get("event") or ""),
                        job_key=str(candidate_receipt.get("job_key") or ""),
                        matrix=candidate_receipt.get("matrix")
                        if isinstance(candidate_receipt.get("matrix"), Mapping)
                        else {},
                        runner_environment=str(candidate_receipt.get("runner_environment") or ""),
                        runner_os=str(candidate_receipt.get("runner_os") or ""),
                        runner_arch=str(candidate_receipt.get("runner_arch") or ""),
                        generated_at=receipt_generated_at,
                    )
                except TrustedEvidenceError as exc:
                    errors.append(str(exc))
                    break
                if dict(candidate_receipt) != rebuilt:
                    errors.append("platform source receipt is not the exact canonical derived receipt")
                    break
                identity = str(rebuilt["identity_sha256"])
                artifact = str(rebuilt["artifact_name"])
                if identity in observed_identities or artifact in observed_artifacts:
                    errors.append("platform source receipt was reused")
                    break
                observed_names.add(job_name)
                observed_identities.add(identity)
                observed_artifacts.add(artifact)
                events.add(str(rebuilt["event"]))
                source_receipts.append(rebuilt)
            if observed_names != expected_names:
                errors.append("platform source receipt inventory is incomplete or contains unknown jobs")
            if len(events) != 1:
                errors.append("platform source receipts do not share one exact source event")
            if source_receipts != sorted(source_receipts, key=lambda item: item["job_name"]):
                errors.append("platform source receipts are not in canonical job-name order")
    if expected_evidence_type == "platform" and payload.get("targets") != list(_PLATFORM_TARGETS):
        errors.append("platform evidence targets do not match the exact supported target contract")

    if expected_evidence_type in {"credentialed-polymarket", "funded-polymarket"}:
        tier = "credentialed" if expected_evidence_type == "credentialed-polymarket" else "funded"
        if payload.get("target_tier") != f"{tier}_live_verified":
            errors.append("live evidence target tier is invalid")
        if type(payload.get("live_action")) is not bool or payload.get("live_action") is not (tier == "funded"):
            errors.append("live evidence live_action does not match its tier")
        report = payload.get("live_report")
        if not isinstance(report, Mapping):
            errors.append("live evidence report must be an object")
        else:
            report_hash = hashlib.sha256(canonical_json_bytes(dict(report))).hexdigest()
            if not _HASH_RE.fullmatch(str(payload.get("report_sha256") or "")) or payload.get("report_sha256") != report_hash:
                errors.append("live evidence report hash is invalid")
            if tier == "funded":
                receipt = payload.get("recovery_receipt")
                collector = payload.get("collector_job")
                funded = report.get("funded_live_order_check")
                report_receipt = funded.get("recovery_journal_receipt") if isinstance(funded, Mapping) else None
                if (
                    not isinstance(receipt, Mapping)
                    or set(receipt) != _FUNDED_RECOVERY_RECEIPT_FIELDS
                    or receipt != report_receipt
                    or receipt.get("sha256") != payload.get("recovery_journal_sha256")
                    or not _HASH_RE.fullmatch(str(payload.get("recovery_journal_sha256") or ""))
                    or receipt.get("source_revision") != revision
                    or receipt.get("workflow_run_id") != run_id
                    or receipt.get("workflow_run_attempt") != run_attempt
                    or receipt.get("evidence_nonce") != f"{revision}:{run_id}:{run_attempt}"
                    or receipt.get("stage") != "cancel_verified"
                    or receipt.get("resolved") is not True
                ):
                    errors.append("funded recovery receipt is invalid")
                if (
                    not isinstance(collector, Mapping)
                    or set(collector) != _FUNDED_COLLECTOR_SUMMARY_FIELDS
                    or type(collector.get("job_id")) is not int
                    or collector.get("job_id", 0) < 1
                    or collector.get("run_id") != run_id
                    or collector.get("run_attempt") != run_attempt
                    or collector.get("head_sha") != revision
                    or collector.get("name") != FUNDED_COLLECTOR_JOB
                    or collector.get("status") != "completed"
                    or collector.get("conclusion") != "success"
                    or collector.get("runner_environment") != "self-hosted"
                    or collector.get("labels") != sorted(FUNDED_COLLECTOR_LABELS)
                    or collector.get("steps") != list(FUNDED_COLLECTOR_STEPS)
                ):
                    errors.append("funded collector job binding is invalid")
                errors.extend(
                    _funded_control_summary_errors(
                        payload,
                        report=report,
                        collector=collector,
                    )
                )
                try:
                    from polymarket.live_report_schema import validate_live_validation_report
                    from polymarket.live_reports import live_validation_report_promotion

                    live_validation = validate_live_validation_report(report)
                    promotion = live_validation_report_promotion(report)
                    provenance = report.get("source_provenance")
                    if (
                        live_validation.get("ok") is not True
                        or not isinstance(provenance, Mapping)
                        or provenance.get("stable") is not True
                        or provenance.get("source_revision") != revision
                        or promotion.get("can_promote_funded_live_verified") is not True
                        or not isinstance(funded, Mapping)
                        or funded.get("live_action") is not True
                    ):
                        errors.append("funded live report is no longer promotion-eligible")
                except (ImportError, RuntimeError, ValueError) as exc:
                    errors.append(f"funded live report validation failed: {type(exc).__name__}")
            else:
                try:
                    rebuilt = build_live_manifest(
                        report,
                        tier=tier,
                        repository=REPOSITORY,
                        source_revision=revision,
                        run_id=run_id if type(run_id) is int else 0,
                        run_attempt=run_attempt if type(run_attempt) is int else 0,
                        workflow_ref=f"{REPOSITORY}/{contract['workflow']}@{TRUSTED_REF}",
                        generated_at=generated_at,
                    )
                except TrustedEvidenceError as exc:
                    errors.append(str(exc))
                else:
                    for field in ("checks", "target_tier", "report_sha256", "live_action"):
                        if payload.get(field) != rebuilt.get(field):
                            errors.append(f"live evidence derived field {field} is invalid")

    return {
        "ok": not errors,
        "errors": errors,
        "evidence_type": expected_evidence_type,
        "source_revision": revision,
        "run_id": run_id if type(run_id) is int else 0,
        "run_attempt": run_attempt if type(run_attempt) is int else 0,
        "source_run_id": source_run_id or 0,
        "source_run_attempt": source_run_attempt or 0,
        "source_receipts": source_receipts,
        "governance_state_sha256": governance_digest,
        "generated_at": generated_at,
        "artifact_name": evidence.get("artifact_name") if isinstance(evidence, Mapping) else "",
        "subject_name": contract["subject_name"],
        "workflow": contract["workflow"],
        "workflow_name": contract["workflow_name"],
        "workflow_ref": exact_metadata["workflow_ref"],
        "job": contract["job"],
        "required_steps": contract["required_steps"],
    }


def write_manifest(path: Path, payload: Mapping[str, Any]) -> None:
    if not path.parent.is_dir() or path.is_symlink():
        raise ValueError("evidence output parent must exist and output must not be a symbolic link")
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            if os.name == "posix":
                os.fchmod(handle.fileno(), 0o600)
            handle.write(canonical_json_bytes(payload))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise


def _common_builder_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repository", required=True)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--run-id", required=True, type=int)
    parser.add_argument("--run-attempt", required=True, type=int)
    parser.add_argument("--workflow-ref", required=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate or validate exact trusted readiness evidence candidates.")
    commands = parser.add_subparsers(dest="command", required=True)

    governance = commands.add_parser("governance")
    _common_builder_arguments(governance)
    governance.add_argument("--input", required=True, type=Path)
    governance.add_argument("--output-directory", required=True, type=Path)

    platform = commands.add_parser("platform")
    _common_builder_arguments(platform)
    platform.add_argument("--run-input", required=True, type=Path)
    platform.add_argument("--jobs-input", required=True, type=Path)
    platform.add_argument("--artifacts-input", required=True, type=Path)
    platform.add_argument("--receipts-input", required=True, type=Path)
    platform.add_argument("--source-run-id", required=True, type=int)
    platform.add_argument("--output-directory", required=True, type=Path)

    live = commands.add_parser("live")
    _common_builder_arguments(live)
    live.add_argument("--input", required=True, type=Path)
    live.add_argument("--recovery-journal", type=Path)
    live.add_argument("--run-jobs", type=Path)
    live.add_argument("--environment-config", type=Path)
    live.add_argument("--run-approvals", type=Path)
    live.add_argument("--token-policy", type=Path)
    live.add_argument("--tier", required=True, choices=("credentialed", "funded"))
    live.add_argument("--output", required=True, type=Path)

    validate = commands.add_parser("validate")
    validate.add_argument("--input", required=True, type=Path)
    validate.add_argument("--evidence-type", required=True, choices=tuple(WORKFLOW_CONTRACTS))
    validate.add_argument("--expected-revision", required=True)

    compare_governance = commands.add_parser("compare-governance")
    compare_governance.add_argument("--input", required=True, type=Path)
    compare_governance.add_argument("--manifest", required=True, action="append", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "governance":
            manifests = build_governance_manifests(
                load_strict_json(args.input),
                repository=args.repository,
                source_revision=args.source_revision,
                run_id=args.run_id,
                run_attempt=args.run_attempt,
                workflow_ref=args.workflow_ref,
            )
            for evidence_type, payload in manifests.items():
                write_manifest(
                    args.output_directory / WORKFLOW_CONTRACTS[evidence_type]["subject_name"],
                    payload,
                )
            result = {"ok": True, "evidence_types": sorted(manifests)}
        elif args.command == "platform":
            manifests = build_platform_manifests(
                load_strict_json(args.run_input),
                load_strict_json(args.jobs_input),
                load_strict_json(args.artifacts_input),
                load_strict_json(args.receipts_input),
                repository=args.repository,
                source_revision=args.source_revision,
                source_run_id=args.source_run_id,
                run_id=args.run_id,
                run_attempt=args.run_attempt,
                workflow_ref=args.workflow_ref,
            )
            for evidence_type, payload in manifests.items():
                write_manifest(
                    args.output_directory / WORKFLOW_CONTRACTS[evidence_type]["subject_name"],
                    payload,
                )
            result = {"ok": True, "evidence_types": sorted(manifests)}
        elif args.command == "live":
            evidence_type = f"{args.tier}-polymarket"
            recovery_journal = None
            recovery_journal_sha256 = ""
            if args.tier == "funded":
                if args.recovery_journal is None:
                    raise TrustedEvidenceError("funded evidence requires --recovery-journal")
                if args.run_jobs is None:
                    raise TrustedEvidenceError("funded evidence requires --run-jobs")
                if args.environment_config is None:
                    raise TrustedEvidenceError("funded evidence requires --environment-config")
                if args.run_approvals is None:
                    raise TrustedEvidenceError("funded evidence requires --run-approvals")
                if args.token_policy is None:
                    raise TrustedEvidenceError("funded evidence requires --token-policy")
                recovery_raw = args.recovery_journal.read_bytes()
                if len(recovery_raw) > 64 * 1024:
                    raise TrustedEvidenceError("funded recovery journal exceeds its byte limit")
                recovery_journal = load_strict_json(args.recovery_journal)
                recovery_journal_sha256 = hashlib.sha256(recovery_raw).hexdigest()
            elif args.recovery_journal is not None:
                raise TrustedEvidenceError("--recovery-journal is valid only for funded evidence")
            elif args.run_jobs is not None:
                raise TrustedEvidenceError("--run-jobs is valid only for funded evidence")
            elif args.environment_config is not None:
                raise TrustedEvidenceError("--environment-config is valid only for funded evidence")
            elif args.run_approvals is not None:
                raise TrustedEvidenceError("--run-approvals is valid only for funded evidence")
            elif args.token_policy is not None:
                raise TrustedEvidenceError("--token-policy is valid only for funded evidence")
            payload = build_live_manifest(
                load_strict_json(args.input),
                tier=args.tier,
                repository=args.repository,
                source_revision=args.source_revision,
                run_id=args.run_id,
                run_attempt=args.run_attempt,
                workflow_ref=args.workflow_ref,
                recovery_journal=recovery_journal,
                recovery_journal_sha256=recovery_journal_sha256,
                run_jobs=load_strict_json(args.run_jobs) if args.run_jobs is not None else None,
                environment_config=(
                    load_strict_json(
                        args.environment_config,
                        maximum_bytes=MAX_FUNDED_ENVIRONMENT_BYTES,
                    )
                    if args.environment_config is not None
                    else None
                ),
                run_approvals=(
                    load_strict_json(
                        args.run_approvals,
                        maximum_bytes=MAX_FUNDED_APPROVALS_BYTES,
                    )
                    if args.run_approvals is not None
                    else None
                ),
                token_policy=(
                    load_strict_json(args.token_policy, maximum_bytes=MAX_FUNDED_POLICY_BYTES)
                    if args.token_policy is not None
                    else None
                ),
            )
            write_manifest(args.output, payload)
            result = {"ok": True, "evidence_types": [evidence_type]}
        elif args.command == "validate":
            payload = load_strict_json(args.input)
            validation = validate_manifest(
                payload,
                expected_evidence_type=args.evidence_type,
                expected_revision=args.expected_revision,
            )
            if not validation["ok"]:
                raise TrustedEvidenceError("; ".join(validation["errors"]))
            if args.input.read_bytes() != canonical_json_bytes(payload):
                raise TrustedEvidenceError("trusted evidence file is not canonical JSON")
            result = {"ok": True, "evidence_types": [args.evidence_type]}
        else:
            _checks, current_digest = _validated_raw_governance(load_strict_json(args.input))
            observed_types: set[str] = set()
            for path in args.manifest:
                payload = load_strict_json(path)
                evidence_type = payload.get("evidence_type") if isinstance(payload, Mapping) else None
                if evidence_type not in {"repository-settings", "release-environment"}:
                    raise TrustedEvidenceError("governance comparison received an unexpected manifest type")
                if evidence_type in observed_types:
                    raise TrustedEvidenceError("governance comparison received a duplicate manifest type")
                if payload.get("governance_state_sha256") != current_digest:
                    raise TrustedEvidenceError("live governance state changed after evidence generation")
                observed_types.add(evidence_type)
            if observed_types != {"repository-settings", "release-environment"}:
                raise TrustedEvidenceError("governance comparison requires both exact manifest types")
            result = {"ok": True, "evidence_types": sorted(observed_types)}
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError, _DuplicateJsonKey, TrustedEvidenceError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
