from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import math
import os
import re
import socket
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

try:
    from scripts.collect_prometheus_delivery_evidence import (
        DEFAULT_ALERTMANAGER_ORIGIN,
        DEFAULT_ONCALL_CREDENTIALS_FILE,
        DEFAULT_ONCALL_RECEIVER_NAME,
        DEFAULT_ONCALL_URL_FILE,
        DEFAULT_PROMETHEUS_ORIGIN,
        DEFAULT_RECEIVER_NAME,
        DEFAULT_RULE_DIRECTORY,
        MAX_EVIDENCE_BYTES,
        MAX_ONCALL_CLOCK_SKEW_SECONDS,
        MAX_ONCALL_RECEIPT_BYTES,
        ONCALL_RECEIPT_FIELDS,
        ONCALL_RECEIPT_TYPE,
        REPORT_TYPE,
        SAFE_DELIVERY_ID,
        SAFE_PROVIDER_NAME,
        SAFE_RECEIVER_NAME,
        SCHEMA_VERSION,
        alert_name,
        alertmanager_alerts_url,
        binding_sha256,
        canonical_origin,
        canonical_public_https_origin,
        evidence_transcript_sha256,
        expected_labels,
        group_name,
        prometheus_alerts_url,
        alertmanager_status_url,
        oncall_webhook_event_sha256,
        receiver_url,
        receipt_origin_sha256,
        render_rule,
        rule_filename,
        rules_url,
        strict_json,
    )
except ModuleNotFoundError:  # Direct execution adds scripts/ rather than the repository root to sys.path.
    from collect_prometheus_delivery_evidence import (  # type: ignore[no-redef]
        DEFAULT_ALERTMANAGER_ORIGIN,
        DEFAULT_ONCALL_CREDENTIALS_FILE,
        DEFAULT_ONCALL_RECEIVER_NAME,
        DEFAULT_ONCALL_URL_FILE,
        DEFAULT_PROMETHEUS_ORIGIN,
        DEFAULT_RECEIVER_NAME,
        DEFAULT_RULE_DIRECTORY,
        MAX_EVIDENCE_BYTES,
        MAX_ONCALL_CLOCK_SKEW_SECONDS,
        MAX_ONCALL_RECEIPT_BYTES,
        ONCALL_RECEIPT_FIELDS,
        ONCALL_RECEIPT_TYPE,
        REPORT_TYPE,
        SAFE_DELIVERY_ID,
        SAFE_PROVIDER_NAME,
        SAFE_RECEIVER_NAME,
        SCHEMA_VERSION,
        alert_name,
        alertmanager_alerts_url,
        binding_sha256,
        canonical_origin,
        canonical_public_https_origin,
        evidence_transcript_sha256,
        expected_labels,
        group_name,
        prometheus_alerts_url,
        alertmanager_status_url,
        oncall_webhook_event_sha256,
        receiver_url,
        receipt_origin_sha256,
        render_rule,
        rule_filename,
        rules_url,
        strict_json,
    )


REVIEWED_EVIDENCE_TYPE = "reviewed-prometheus-alert-delivery"
DEFAULT_MAX_AGE_SECONDS = 15 * 60
DEFAULT_MAX_DURATION_SECONDS = 5 * 60
DEFAULT_MAX_FUTURE_SKEW_SECONDS = MAX_ONCALL_CLOCK_SKEW_SECONDS
COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")
SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
ALERT_FINGERPRINT = re.compile(r"^[0-9a-f]{16,64}$")


class DeliveryEvidenceReviewError(ValueError):
    """Raised when raw monitoring evidence does not prove the reviewed path."""


def _require_exact_keys(value: Any, expected: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DeliveryEvidenceReviewError(f"{label} must be an object")
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        detail = []
        if missing:
            detail.append("missing=" + ",".join(missing))
        if unknown:
            detail.append("unknown=" + ",".join(unknown))
        raise DeliveryEvidenceReviewError(f"{label} fields are not exact ({'; '.join(detail)})")
    return value


def _timestamp(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise DeliveryEvidenceReviewError(f"{label} must be a UTC timestamp ending in Z")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise DeliveryEvidenceReviewError(f"{label} is not a valid ISO-8601 timestamp") from exc
    return parsed.astimezone(timezone.utc)


def _positive_int(value: Any, label: str) -> int:
    if type(value) is not int or value <= 0:
        raise DeliveryEvidenceReviewError(f"{label} must be a positive integer")
    return value


def _load_report(path: Path) -> tuple[dict[str, Any], str]:
    if path.is_symlink() or not path.is_file():
        raise DeliveryEvidenceReviewError("raw evidence must be a regular non-symbolic-link file")
    size = path.stat().st_size
    if not 0 < size <= MAX_EVIDENCE_BYTES:
        raise DeliveryEvidenceReviewError("raw evidence is empty or oversized")
    raw = path.read_bytes()
    try:
        payload = strict_json(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise DeliveryEvidenceReviewError("raw evidence must be strict UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise DeliveryEvidenceReviewError("raw evidence must be a JSON object")
    return payload, hashlib.sha256(raw).hexdigest()


def _parse_json_body(observation: dict[str, Any], label: str) -> Any:
    body = observation["body"]
    media_type = observation["content_type"].split(";", 1)[0].strip().lower()
    if media_type not in {"application/json", "application/problem+json"}:
        raise DeliveryEvidenceReviewError(f"{label} did not record a JSON Content-Type")
    try:
        return strict_json(body)
    except (json.JSONDecodeError, ValueError) as exc:
        raise DeliveryEvidenceReviewError(f"{label} body is not strict JSON") from exc


def _http_observation(value: Any, *, label: str, expected_url: str) -> tuple[dict[str, Any], datetime]:
    observation = _require_exact_keys(
        value,
        {"body", "body_sha256", "content_type", "observed_at", "status", "url"},
        label,
    )
    if observation["url"] != expected_url or observation["status"] != 200:
        raise DeliveryEvidenceReviewError(f"{label} did not record the exact successful endpoint")
    if not isinstance(observation["body"], str) or not isinstance(observation["content_type"], str):
        raise DeliveryEvidenceReviewError(f"{label} body and Content-Type must be strings")
    body_digest = hashlib.sha256(observation["body"].encode("utf-8")).hexdigest()
    if observation["body_sha256"] != body_digest:
        raise DeliveryEvidenceReviewError(f"{label} body digest does not match its raw bytes")
    return observation, _timestamp(observation["observed_at"], f"{label}.observed_at")


def _receiver_observation(value: Any) -> tuple[dict[str, Any], datetime]:
    observation = _require_exact_keys(
        value,
        {"body", "body_sha256", "content_type", "observed_at", "remote_ip"},
        "receiver webhook",
    )
    if not isinstance(observation["body"], str) or not isinstance(observation["content_type"], str):
        raise DeliveryEvidenceReviewError("receiver webhook body and Content-Type must be strings")
    try:
        remote = ipaddress.ip_address(observation["remote_ip"])
    except (TypeError, ValueError) as exc:
        raise DeliveryEvidenceReviewError("receiver webhook remote IP is invalid") from exc
    if not remote.is_loopback:
        raise DeliveryEvidenceReviewError("receiver webhook was not delivered from loopback")
    body_digest = hashlib.sha256(observation["body"].encode("utf-8")).hexdigest()
    if observation["body_sha256"] != body_digest:
        raise DeliveryEvidenceReviewError("receiver webhook body digest does not match its raw bytes")
    return observation, _timestamp(observation["observed_at"], "receiver webhook observed_at")


def _alertmanager_config_observation(
    value: Any,
    *,
    expected_url: str,
    oncall_origin: str,
) -> tuple[dict[str, Any], datetime]:
    observation = _require_exact_keys(
        value,
        {
            "config_sha256",
            "contract",
            "external_receiver_excerpt",
            "external_route_excerpt",
            "local_route_excerpt",
            "observed_at",
            "status",
            "status_body_sha256",
            "url",
        },
        "Alertmanager loaded config",
    )
    if observation["url"] != expected_url or observation["status"] != 200:
        raise DeliveryEvidenceReviewError("Alertmanager loaded config did not come from the exact status endpoint")
    for field in ("config_sha256", "status_body_sha256"):
        if not isinstance(observation[field], str) or SHA256_HEX.fullmatch(observation[field]) is None:
            raise DeliveryEvidenceReviewError(f"Alertmanager loaded config {field} is invalid")
    expected_contract = {
        "authorization_type": "Bearer",
        "credentials_file": DEFAULT_ONCALL_CREDENTIALS_FILE.as_posix(),
        "external_continue": True,
        "external_receiver": DEFAULT_ONCALL_RECEIVER_NAME,
        "follow_redirects": False,
        "proxy_from_environment": False,
        "local_continue": False,
        "local_receiver": DEFAULT_RECEIVER_NAME,
        "matcher": 'market_sentinel_attestation="true"',
        "max_alerts": 1,
        "send_resolved": False,
        "tls_insecure_skip_verify": False,
        "url_file": DEFAULT_ONCALL_URL_FILE.as_posix(),
    }
    if observation["contract"] != expected_contract:
        raise DeliveryEvidenceReviewError("Alertmanager loaded config contract is not exact")
    excerpts = {
        "external route": observation["external_route_excerpt"],
        "local route": observation["local_route_excerpt"],
        "external receiver": observation["external_receiver_excerpt"],
    }
    matcher = 'market_sentinel_attestation="true"'
    route_tail = (
        "  matchers:",
        f"    - {matcher}",
        "  group_by:",
        "    - alertname",
        "    - market_sentinel_binding",
        "  group_wait: 0s",
        "  group_interval: 1m",
        "  repeat_interval: 24h",
    )
    expected_excerpts = {
        "external route": (
            f"- receiver: {DEFAULT_ONCALL_RECEIVER_NAME}",
            *route_tail,
            "  continue: true",
        ),
        "local route": (
            f"- receiver: {DEFAULT_RECEIVER_NAME}",
            *route_tail,
            "  continue: false",
        ),
        "external receiver": (
            f"- name: {DEFAULT_ONCALL_RECEIVER_NAME}",
            "  webhook_configs:",
            f"    - url_file: {DEFAULT_ONCALL_URL_FILE.as_posix()}",
            "      send_resolved: false",
            "      max_alerts: 1",
            "      http_config:",
            "        follow_redirects: false",
            "        proxy_from_environment: false",
            "        tls_config:",
            "          insecure_skip_verify: false",
            "        authorization:",
            "          type: Bearer",
            f"          credentials_file: {DEFAULT_ONCALL_CREDENTIALS_FILE.as_posix()}",
        ),
    }
    expected_indentation = {"external route": 4, "local route": 4, "external receiver": 2}
    for label, expected_lines in expected_excerpts.items():
        excerpt = excerpts[label]
        if not isinstance(excerpt, str) or not excerpt.endswith("\n"):
            raise DeliveryEvidenceReviewError("Alertmanager loaded config excerpts are not exact text")
        lines = excerpt.splitlines()
        if not lines or "\t" in excerpt:
            raise DeliveryEvidenceReviewError(f"Alertmanager loaded {label} excerpt is misindented")
        indentation = len(lines[0]) - len(lines[0].lstrip(" "))
        if indentation != expected_indentation[label]:
            raise DeliveryEvidenceReviewError(f"Alertmanager loaded {label} excerpt is outside its exact list")
        expected = [f"{' ' * indentation}{line}" for line in expected_lines]
        if lines != expected:
            raise DeliveryEvidenceReviewError(
                f"Alertmanager loaded {label} excerpt has unknown, missing, reordered, or misindented fields"
            )
    serialized_excerpts = "".join(excerpts.values())
    if oncall_origin in serialized_excerpts or "Authorization:" in serialized_excerpts:
        raise DeliveryEvidenceReviewError("Alertmanager loaded config proof contains a raw secret URL or header")
    return observation, _timestamp(observation["observed_at"], "Alertmanager loaded config observed_at")


def _oncall_observation(value: Any) -> tuple[dict[str, Any], dict[str, Any], datetime]:
    observation = _require_exact_keys(
        value,
        {"body", "body_sha256", "content_type", "observed_at", "status"},
        "on-call receipt",
    )
    if observation["status"] != 200:
        raise DeliveryEvidenceReviewError("on-call receipt was not a successful response")
    if not isinstance(observation["body"], str) or not isinstance(observation["content_type"], str):
        raise DeliveryEvidenceReviewError("on-call receipt body and Content-Type must be strings")
    media_type = observation["content_type"].split(";", 1)[0].strip().lower()
    if media_type not in {"application/json", "application/problem+json"}:
        raise DeliveryEvidenceReviewError("on-call receipt did not record a JSON Content-Type")
    raw = observation["body"].encode("utf-8")
    if len(raw) > MAX_ONCALL_RECEIPT_BYTES or observation["body_sha256"] != hashlib.sha256(raw).hexdigest():
        raise DeliveryEvidenceReviewError("on-call receipt digest does not match its bounded raw bytes")
    try:
        payload = strict_json(observation["body"])
    except (json.JSONDecodeError, ValueError) as exc:
        raise DeliveryEvidenceReviewError("on-call receipt body is not strict JSON") from exc
    receipt = _require_exact_keys(payload, ONCALL_RECEIPT_FIELDS, "on-call receipt body")
    observed_at = _timestamp(observation["observed_at"], "on-call receipt observed_at")
    return observation, receipt, observed_at


def _review_oncall_receipt(
    receipt: dict[str, Any],
    *,
    source_revision: str,
    deployment_identity_sha256: str,
    run_id: int,
    run_attempt: int,
    binding: str,
    alert_fingerprint: str,
    labels: dict[str, str],
    alert_starts_at: str,
    started_at: datetime,
    observed_at: datetime,
) -> tuple[datetime, datetime, datetime]:
    exact_identity = {
        "binding_sha256": binding,
        "deployment_identity_sha256": deployment_identity_sha256,
        "run_attempt": run_attempt,
        "run_id": run_id,
        "source_revision": source_revision,
    }
    if (
        type(receipt.get("run_id")) is not int
        or type(receipt.get("run_attempt")) is not int
        or type(receipt.get("schema_version")) is not int
    ):
        raise DeliveryEvidenceReviewError("on-call receipt numeric identity fields must be integers")
    if any(receipt.get(key) != value for key, value in exact_identity.items()):
        raise DeliveryEvidenceReviewError("on-call receipt is not bound to the exact deployment challenge")
    if (
        receipt.get("schema_version") != SCHEMA_VERSION
        or receipt.get("receipt_type") != ONCALL_RECEIPT_TYPE
        or receipt.get("status") != "acknowledged"
        or receipt.get("acknowledgement_kind") != "human"
    ):
        raise DeliveryEvidenceReviewError("on-call receipt does not prove a human acknowledgement")
    if receipt.get("alert_fingerprint") != alert_fingerprint:
        raise DeliveryEvidenceReviewError("on-call receipt fingerprint does not match Alertmanager")
    expected_event_sha256 = oncall_webhook_event_sha256(
        labels=labels,
        alert_fingerprint=alert_fingerprint,
        starts_at=alert_starts_at,
    )
    if (
        receipt.get("webhook_receiver") != DEFAULT_ONCALL_RECEIVER_NAME
        or receipt.get("webhook_event_sha256") != expected_event_sha256
        or not isinstance(receipt.get("webhook_body_sha256"), str)
        or SHA256_HEX.fullmatch(receipt["webhook_body_sha256"]) is None
    ):
        raise DeliveryEvidenceReviewError(
            "on-call receipt is not bound to the exact external Alertmanager webhook"
        )
    provider = receipt.get("oncall_provider")
    if (
        not isinstance(provider, str)
        or SAFE_PROVIDER_NAME.fullmatch(provider) is None
        or provider in {"local", "loopback", "mock", "none", "test"}
    ):
        raise DeliveryEvidenceReviewError("on-call provider identity is invalid")
    delivery_id = receipt.get("delivery_id")
    if not isinstance(delivery_id, str) or SAFE_DELIVERY_ID.fullmatch(delivery_id) is None:
        raise DeliveryEvidenceReviewError("on-call delivery ID is invalid")
    for field in ("oncall_channel_sha256", "acknowledger_sha256"):
        if not isinstance(receipt.get(field), str) or SHA256_HEX.fullmatch(receipt[field]) is None:
            raise DeliveryEvidenceReviewError(f"on-call {field} is not a SHA-256 identity")
    received_at = _timestamp(receipt.get("received_at"), "on-call received_at")
    dispatched_at = _timestamp(receipt.get("dispatched_at"), "on-call dispatched_at")
    acknowledged_at = _timestamp(receipt.get("acknowledged_at"), "on-call acknowledged_at")
    alert_active_at = _timestamp(alert_starts_at, "Alertmanager alert startsAt")
    skew = timedelta(seconds=MAX_ONCALL_CLOCK_SKEW_SECONDS)
    if received_at < alert_active_at - skew:
        raise DeliveryEvidenceReviewError(
            "on-call receipt received_at precedes the Alertmanager alert activeAt beyond allowed clock skew"
        )
    if not (
        received_at <= dispatched_at < acknowledged_at
        and received_at >= started_at - skew
        and acknowledged_at <= observed_at + skew
    ):
        raise DeliveryEvidenceReviewError(
            "on-call receipt timestamps are out of order or outside the active challenge window"
        )
    return received_at, dispatched_at, acknowledged_at


def _labels_match(candidate: Any, expected: dict[str, str]) -> bool:
    return isinstance(candidate, dict) and all(candidate.get(key) == value for key, value in expected.items())


def _one_match(values: Any, predicate: Any, label: str) -> dict[str, Any]:
    if not isinstance(values, list):
        raise DeliveryEvidenceReviewError(f"{label} collection must be an array")
    matches = [item for item in values if isinstance(item, dict) and predicate(item)]
    if len(matches) != 1:
        raise DeliveryEvidenceReviewError(f"{label} must contain exactly one bound attestation object")
    return matches[0]


def _loaded_rule(payload: Any, *, path: str, binding: str, labels: dict[str, str]) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("status") != "success" or not isinstance(payload.get("data"), dict):
        raise DeliveryEvidenceReviewError("Prometheus rules response is not successful")
    groups = payload["data"].get("groups")
    if not isinstance(groups, list):
        raise DeliveryEvidenceReviewError("Prometheus rules response has no groups")
    candidate_rules: list[dict[str, Any]] = []
    for group in groups:
        if not isinstance(group, dict) or group.get("file") != path or group.get("name") != group_name(binding):
            continue
        rules = group.get("rules")
        if isinstance(rules, list):
            candidate_rules.extend(item for item in rules if isinstance(item, dict))
    rule_labels = {key: value for key, value in labels.items() if key != "alertname"}
    return _one_match(
        candidate_rules,
        lambda rule: rule.get("type") == "alerting"
        and rule.get("name") == labels["alertname"]
        and rule.get("query") == "vector(1)"
        and rule.get("health") == "ok"
        and _labels_match(rule.get("labels"), rule_labels),
        "Prometheus loaded rules",
    )


def _require_rule_absent(payload: Any, *, path: str, binding: str, labels: dict[str, str]) -> None:
    if not isinstance(payload, dict) or payload.get("status") != "success" or not isinstance(payload.get("data"), dict):
        raise DeliveryEvidenceReviewError("post-cleanup Prometheus rules response is not successful")
    groups = payload["data"].get("groups")
    if not isinstance(groups, list):
        raise DeliveryEvidenceReviewError("post-cleanup Prometheus rules response has no groups")
    for group in groups:
        if not isinstance(group, dict):
            continue
        rules = group.get("rules")
        if not isinstance(rules, list):
            continue
        for rule in rules:
            if not isinstance(rule, dict):
                continue
            rule_labels = rule.get("labels")
            if (
                (group.get("file") == path and group.get("name") == group_name(binding))
                or rule.get("name") == labels["alertname"]
                or (isinstance(rule_labels, dict) and rule_labels.get("market_sentinel_binding") == binding)
            ):
                raise DeliveryEvidenceReviewError("Prometheus still reports the synthetic rule after cleanup")


def _prometheus_alert(payload: Any, labels: dict[str, str]) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("status") != "success" or not isinstance(payload.get("data"), dict):
        raise DeliveryEvidenceReviewError("Prometheus alerts response is not successful")
    alert = _one_match(
        payload["data"].get("alerts"),
        lambda item: item.get("state") == "firing" and _labels_match(item.get("labels"), labels),
        "Prometheus active alerts",
    )
    if not isinstance(alert.get("annotations"), dict) or alert["annotations"].get("attestation_binding") != labels["market_sentinel_binding"]:
        raise DeliveryEvidenceReviewError("Prometheus alert annotation is not bound to the challenge")
    _timestamp(alert.get("activeAt"), "Prometheus alert activeAt")
    return alert


def _alertmanager_alert(payload: Any, labels: dict[str, str], receiver_name: str) -> dict[str, Any]:
    alert = _one_match(
        payload,
        lambda item: isinstance(item.get("status"), dict)
        and item["status"].get("state") == "active"
        and _labels_match(item.get("labels"), labels),
        "Alertmanager active alerts",
    )
    if not isinstance(alert.get("annotations"), dict) or alert["annotations"].get("attestation_binding") != labels["market_sentinel_binding"]:
        raise DeliveryEvidenceReviewError("Alertmanager alert annotation is not bound to the challenge")
    receivers = alert.get("receivers")
    if not isinstance(receivers, list):
        raise DeliveryEvidenceReviewError("Alertmanager receiver references are not an array")
    receiver_names = {receiver.get("name") for receiver in receivers if isinstance(receiver, dict)}
    if receiver_names != {receiver_name, DEFAULT_ONCALL_RECEIVER_NAME}:
        raise DeliveryEvidenceReviewError(
            "Alertmanager did not report both the external on-call and controlled loopback receivers"
        )
    fingerprint = alert.get("fingerprint")
    if not isinstance(fingerprint, str) or not ALERT_FINGERPRINT.fullmatch(fingerprint):
        raise DeliveryEvidenceReviewError("Alertmanager alert fingerprint is invalid")
    _timestamp(alert.get("startsAt"), "Alertmanager alert startsAt")
    return alert


def _webhook_alert(payload: Any, labels: dict[str, str], receiver_name: str, fingerprint: str) -> dict[str, Any]:
    if (
        not isinstance(payload, dict)
        or payload.get("version") != "4"
        or payload.get("receiver") != receiver_name
        or payload.get("status") != "firing"
    ):
        raise DeliveryEvidenceReviewError("controlled receiver payload is not an Alertmanager v4 firing notification")
    alert = _one_match(
        payload.get("alerts"),
        lambda item: item.get("status") == "firing" and _labels_match(item.get("labels"), labels),
        "controlled receiver alerts",
    )
    if alert.get("fingerprint") != fingerprint:
        raise DeliveryEvidenceReviewError("controlled receiver fingerprint does not match Alertmanager")
    if not isinstance(alert.get("annotations"), dict) or alert["annotations"].get("attestation_binding") != labels["market_sentinel_binding"]:
        raise DeliveryEvidenceReviewError("controlled receiver annotation is not bound to the challenge")
    _timestamp(alert.get("startsAt"), "controlled receiver alert startsAt")
    return alert


def review_payload(
    payload: dict[str, Any],
    *,
    raw_report_sha256: str,
    expected_source_revision: str,
    expected_deployment_identity_sha256: str,
    expected_run_id: int,
    expected_run_attempt: int,
    expected_nonce: str,
    expected_rule_directory: Path,
    expected_oncall_receipt_origin: str,
    expected_prometheus_origin: str = DEFAULT_PROMETHEUS_ORIGIN,
    expected_alertmanager_origin: str = DEFAULT_ALERTMANAGER_ORIGIN,
    expected_receiver_name: str = DEFAULT_RECEIVER_NAME,
    expected_receiver_port: int = 19094,
    max_age_seconds: float = DEFAULT_MAX_AGE_SECONDS,
    max_duration_seconds: float = DEFAULT_MAX_DURATION_SECONDS,
    now: datetime | None = None,
    origin_resolver: Callable[..., Any] = socket.getaddrinfo,
) -> dict[str, Any]:
    report = _require_exact_keys(
        payload,
        {
            "challenge",
            "completed_at",
            "endpoints",
            "identity",
            "observations",
            "report_type",
            "rule",
            "schema_version",
            "started_at",
            "status",
            "transcript_sha256",
        },
        "raw report",
    )
    if report["schema_version"] != SCHEMA_VERSION or report["report_type"] != REPORT_TYPE or report["status"] != "ok":
        raise DeliveryEvidenceReviewError("raw report identity or status is invalid")
    if not SHA256_HEX.fullmatch(str(raw_report_sha256)):
        raise DeliveryEvidenceReviewError("raw report digest is invalid")
    transcript = dict(report)
    declared_transcript = transcript.pop("transcript_sha256")
    if declared_transcript != evidence_transcript_sha256(transcript):
        raise DeliveryEvidenceReviewError("raw report transcript digest does not match its complete evidence content")

    revision = expected_source_revision.strip().lower()
    deployment = expected_deployment_identity_sha256.strip().lower()
    nonce = expected_nonce.strip().lower()
    if not COMMIT_SHA.fullmatch(revision) or not SHA256_HEX.fullmatch(deployment) or not SHA256_HEX.fullmatch(nonce):
        raise DeliveryEvidenceReviewError("expected revision, deployment identity, or nonce is malformed")
    run_id = _positive_int(expected_run_id, "expected run ID")
    run_attempt = _positive_int(expected_run_attempt, "expected run attempt")
    identity = _require_exact_keys(
        report["identity"],
        {"deployment_identity_sha256", "run_attempt", "run_id", "source_revision"},
        "identity",
    )
    if identity != {
        "deployment_identity_sha256": deployment,
        "run_attempt": run_attempt,
        "run_id": run_id,
        "source_revision": revision,
    }:
        raise DeliveryEvidenceReviewError("raw report is not bound to the expected revision and deployment run")

    started_at = _timestamp(report["started_at"], "started_at")
    completed_at = _timestamp(report["completed_at"], "completed_at")
    current_time = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if not math.isfinite(max_age_seconds) or max_age_seconds <= 0 or not math.isfinite(max_duration_seconds) or max_duration_seconds <= 0:
        raise DeliveryEvidenceReviewError("freshness limits must be positive finite numbers")
    if completed_at < started_at or (completed_at - started_at).total_seconds() > max_duration_seconds:
        raise DeliveryEvidenceReviewError("raw report duration is invalid or exceeds the reviewed limit")
    if completed_at > current_time + timedelta(seconds=DEFAULT_MAX_FUTURE_SKEW_SECONDS):
        raise DeliveryEvidenceReviewError("raw report completion is unacceptably far in the future")
    if current_time - completed_at > timedelta(seconds=max_age_seconds):
        raise DeliveryEvidenceReviewError("raw report is stale")

    prometheus_origin = canonical_origin(expected_prometheus_origin, "expected Prometheus origin")
    alertmanager_origin = canonical_origin(expected_alertmanager_origin, "expected Alertmanager origin")
    try:
        oncall_origin = canonical_public_https_origin(
            expected_oncall_receipt_origin,
            "expected on-call receipt origin",
            resolver=origin_resolver,
        )
    except ValueError as exc:
        raise DeliveryEvidenceReviewError(str(exc)) from exc
    oncall_origin_digest = receipt_origin_sha256(oncall_origin)
    if not SAFE_RECEIVER_NAME.fullmatch(expected_receiver_name):
        raise DeliveryEvidenceReviewError("expected receiver name is malformed")
    callback_url = receiver_url(expected_receiver_port)
    if not expected_rule_directory.is_absolute():
        raise DeliveryEvidenceReviewError("expected rule directory must be absolute")
    rule_directory = expected_rule_directory.absolute()
    endpoints = _require_exact_keys(
        report["endpoints"],
        {
            "alertmanager_origin",
            "oncall_receipt_origin_sha256",
            "prometheus_origin",
            "receiver_name",
            "receiver_url",
        },
        "endpoints",
    )
    if endpoints != {
        "alertmanager_origin": alertmanager_origin,
        "oncall_receipt_origin_sha256": oncall_origin_digest,
        "prometheus_origin": prometheus_origin,
        "receiver_name": expected_receiver_name,
        "receiver_url": callback_url,
    }:
        raise DeliveryEvidenceReviewError("raw report endpoints do not match the reviewed local monitoring path")

    binding = binding_sha256(
        source_revision=revision,
        deployment_identity_sha256=deployment,
        run_id=run_id,
        run_attempt=run_attempt,
        nonce=nonce,
        started_at=report["started_at"],
        rule_directory=str(rule_directory),
        prometheus_origin=prometheus_origin,
        alertmanager_origin=alertmanager_origin,
        oncall_receipt_origin_sha256=oncall_origin_digest,
        callback_url=callback_url,
        receiver_name=expected_receiver_name,
    )
    challenge = _require_exact_keys(report["challenge"], {"binding_sha256", "nonce"}, "challenge")
    if challenge != {"binding_sha256": binding, "nonce": nonce}:
        raise DeliveryEvidenceReviewError("raw report challenge does not match the externally expected nonce")
    labels = expected_labels(
        binding=binding,
        source_revision=revision,
        deployment_identity_sha256=deployment,
        run_id=run_id,
        run_attempt=run_attempt,
    )
    expected_rule_content = render_rule(
        binding=binding,
        source_revision=revision,
        deployment_identity_sha256=deployment,
        run_id=run_id,
        run_attempt=run_attempt,
    )
    expected_rule_path = str(rule_directory / rule_filename(binding))
    rule = _require_exact_keys(
        report["rule"],
        {"alert_name", "content", "group_name", "path", "sha256"},
        "rule",
    )
    if rule != {
        "alert_name": alert_name(binding),
        "content": expected_rule_content,
        "group_name": group_name(binding),
        "path": expected_rule_path,
        "sha256": hashlib.sha256(expected_rule_content.encode("utf-8")).hexdigest(),
    }:
        raise DeliveryEvidenceReviewError("synthetic rule is not the exact deterministic reviewed rule")

    observations = _require_exact_keys(
        report["observations"],
        {
            "alertmanager_alerts",
            "alertmanager_config",
            "cleanup_reload",
            "cleanup_rules",
            "oncall_receipt",
            "prometheus_alerts",
            "prometheus_reload",
            "prometheus_rules",
            "receiver_webhook",
        },
        "observations",
    )
    reload_observation, reload_at = _http_observation(
        observations["prometheus_reload"],
        label="Prometheus reload",
        expected_url=f"{prometheus_origin}/-/reload",
    )
    rules_observation, rules_at = _http_observation(
        observations["prometheus_rules"],
        label="Prometheus rules",
        expected_url=rules_url(prometheus_origin, labels["alertname"]),
    )
    prometheus_observation, prometheus_at = _http_observation(
        observations["prometheus_alerts"],
        label="Prometheus alerts",
        expected_url=prometheus_alerts_url(prometheus_origin),
    )
    alertmanager_observation, alertmanager_at = _http_observation(
        observations["alertmanager_alerts"],
        label="Alertmanager alerts",
        expected_url=alertmanager_alerts_url(alertmanager_origin),
    )
    alertmanager_config_observation, alertmanager_config_at = _alertmanager_config_observation(
        observations["alertmanager_config"],
        expected_url=alertmanager_status_url(alertmanager_origin),
        oncall_origin=oncall_origin,
    )
    receiver_observation, receiver_at = _receiver_observation(observations["receiver_webhook"])
    oncall_observation, oncall_receipt, oncall_observed_at = _oncall_observation(
        observations["oncall_receipt"]
    )
    cleanup_observation, cleanup_at = _http_observation(
        observations["cleanup_reload"],
        label="Prometheus cleanup reload",
        expected_url=f"{prometheus_origin}/-/reload",
    )
    cleanup_rules_observation, cleanup_rules_at = _http_observation(
        observations["cleanup_rules"],
        label="Prometheus post-cleanup rules",
        expected_url=rules_url(prometheus_origin, labels["alertname"]),
    )
    del reload_observation, cleanup_observation
    if not (
        started_at
        <= reload_at
        <= rules_at
        <= prometheus_at
        <= alertmanager_at
        <= cleanup_at
        <= cleanup_rules_at
        <= completed_at
    ):
        raise DeliveryEvidenceReviewError("monitoring API observation timestamps are out of order")
    if not (
        alertmanager_at <= alertmanager_config_at <= oncall_observed_at <= cleanup_at
        and started_at <= receiver_at <= oncall_observed_at
    ):
        raise DeliveryEvidenceReviewError(
            "receiver or external on-call delivery timestamp falls outside the active challenge window"
        )

    _loaded_rule(
        _parse_json_body(rules_observation, "Prometheus rules"),
        path=expected_rule_path,
        binding=binding,
        labels=labels,
    )
    _require_rule_absent(
        _parse_json_body(cleanup_rules_observation, "Prometheus post-cleanup rules"),
        path=expected_rule_path,
        binding=binding,
        labels=labels,
    )
    prometheus_alert = _prometheus_alert(
        _parse_json_body(prometheus_observation, "Prometheus alerts"),
        labels,
    )
    alertmanager_alert = _alertmanager_alert(
        _parse_json_body(alertmanager_observation, "Alertmanager alerts"),
        labels,
        expected_receiver_name,
    )
    webhook_alert = _webhook_alert(
        _parse_json_body(receiver_observation, "receiver webhook"),
        labels,
        expected_receiver_name,
        alertmanager_alert["fingerprint"],
    )
    oncall_received_at, oncall_dispatched_at, oncall_acknowledged_at = _review_oncall_receipt(
        oncall_receipt,
        source_revision=revision,
        deployment_identity_sha256=deployment,
        run_id=run_id,
        run_attempt=run_attempt,
        binding=binding,
        alert_fingerprint=alertmanager_alert["fingerprint"],
        labels=labels,
        alert_starts_at=alertmanager_alert["startsAt"],
        started_at=started_at,
        observed_at=oncall_observed_at,
    )
    prometheus_active = _timestamp(prometheus_alert["activeAt"], "Prometheus alert activeAt")
    alertmanager_start = _timestamp(alertmanager_alert["startsAt"], "Alertmanager alert startsAt")
    webhook_start = _timestamp(webhook_alert["startsAt"], "controlled receiver alert startsAt")
    if not started_at <= prometheus_active <= completed_at:
        raise DeliveryEvidenceReviewError("Prometheus alert activation is outside the challenge window")
    if alertmanager_start != prometheus_active or webhook_start != prometheus_active:
        raise DeliveryEvidenceReviewError("alert start timestamps do not match across all delivery hops")

    return {
        "acknowledged_at": oncall_receipt["acknowledged_at"],
        "acknowledger_sha256": oncall_receipt["acknowledger_sha256"],
        "alert_fingerprint": alertmanager_alert["fingerprint"],
        "alertmanager_config_sha256": alertmanager_config_observation["config_sha256"],
        "alertmanager_status_sha256": alertmanager_config_observation["status_body_sha256"],
        "alert_name": labels["alertname"],
        "binding_sha256": binding,
        "deployment_identity_sha256": deployment,
        "delivery_id_sha256": hashlib.sha256(oncall_receipt["delivery_id"].encode("utf-8")).hexdigest(),
        "evidence_type": REVIEWED_EVIDENCE_TYPE,
        "nonce": nonce,
        "oncall_channel_sha256": oncall_receipt["oncall_channel_sha256"],
        "oncall_provider": oncall_receipt["oncall_provider"],
        "raw_report_sha256": raw_report_sha256,
        "receipt_origin_sha256": oncall_origin_digest,
        "receipt_sha256": oncall_observation["body_sha256"],
        "reviewed_at": current_time.isoformat().replace("+00:00", "Z"),
        "run_attempt": run_attempt,
        "run_id": run_id,
        "schema_version": SCHEMA_VERSION,
        "source_revision": revision,
        "status": "ok",
        "timeline": {
            "alertmanager_observed_at": alertmanager_observation["observed_at"],
            "alertmanager_config_observed_at": alertmanager_config_observation["observed_at"],
            "cleanup_rule_absent_at": cleanup_rules_observation["observed_at"],
            "completed_at": report["completed_at"],
            "oncall_acknowledged_at": oncall_acknowledged_at.isoformat().replace("+00:00", "Z"),
            "oncall_dispatched_at": oncall_dispatched_at.isoformat().replace("+00:00", "Z"),
            "oncall_receipt_observed_at": oncall_observation["observed_at"],
            "oncall_received_at": oncall_received_at.isoformat().replace("+00:00", "Z"),
            "prometheus_alert_observed_at": prometheus_observation["observed_at"],
            "prometheus_rule_observed_at": rules_observation["observed_at"],
            "receiver_observed_at": receiver_observation["observed_at"],
            "started_at": report["started_at"],
        },
        "transcript_sha256": report["transcript_sha256"],
        "webhook_body_sha256": oncall_receipt["webhook_body_sha256"],
        "webhook_event_sha256": oncall_receipt["webhook_event_sha256"],
    }


def review_report(path: Path, **kwargs: Any) -> dict[str, Any]:
    payload, raw_digest = _load_report(path)
    return review_payload(payload, raw_report_sha256=raw_digest, **kwargs)


def _write_review(path: Path, payload: dict[str, Any]) -> None:
    if not path.parent.is_dir() or path.is_symlink():
        raise DeliveryEvidenceReviewError("review output parent must exist and output must not be a symlink")
    raw = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            if os.name == "posix":
                os.fchmod(handle.fileno(), 0o600)
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Independently review raw Prometheus alert-delivery evidence.")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-source-revision", required=True)
    parser.add_argument("--expected-deployment-identity-sha256", required=True)
    parser.add_argument("--expected-run-id", type=int, required=True)
    parser.add_argument("--expected-run-attempt", type=int, required=True)
    parser.add_argument("--expected-nonce", required=True)
    parser.add_argument("--expected-rule-directory", type=Path, default=DEFAULT_RULE_DIRECTORY)
    parser.add_argument("--expected-oncall-receipt-origin", required=True)
    parser.add_argument("--expected-prometheus-origin", default=DEFAULT_PROMETHEUS_ORIGIN)
    parser.add_argument("--expected-alertmanager-origin", default=DEFAULT_ALERTMANAGER_ORIGIN)
    parser.add_argument("--expected-receiver-name", default=DEFAULT_RECEIVER_NAME)
    parser.add_argument("--expected-receiver-port", type=int, default=19094)
    parser.add_argument("--max-age-seconds", type=float, default=DEFAULT_MAX_AGE_SECONDS)
    parser.add_argument("--max-duration-seconds", type=float, default=DEFAULT_MAX_DURATION_SECONDS)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        reviewed = review_report(
            args.input,
            expected_source_revision=args.expected_source_revision,
            expected_deployment_identity_sha256=args.expected_deployment_identity_sha256,
            expected_run_id=args.expected_run_id,
            expected_run_attempt=args.expected_run_attempt,
            expected_nonce=args.expected_nonce,
            expected_rule_directory=args.expected_rule_directory,
            expected_oncall_receipt_origin=args.expected_oncall_receipt_origin,
            expected_prometheus_origin=args.expected_prometheus_origin,
            expected_alertmanager_origin=args.expected_alertmanager_origin,
            expected_receiver_name=args.expected_receiver_name,
            expected_receiver_port=args.expected_receiver_port,
            max_age_seconds=args.max_age_seconds,
            max_duration_seconds=args.max_duration_seconds,
        )
        _write_review(args.output, reviewed)
    except (DeliveryEvidenceReviewError, OSError, ValueError) as exc:
        print(f"Prometheus delivery evidence review failed: {exc}", file=os.sys.stderr)
        return 1
    print(json.dumps({"output": str(args.output), "status": "ok"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
