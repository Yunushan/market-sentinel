from __future__ import annotations

import argparse
import hashlib
import http.client
import ipaddress
import json
import math
import os
import queue
import re
import socket
import ssl
import stat
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from socketserver import BaseServer
from typing import Any, Callable


SCHEMA_VERSION = 1
REPORT_TYPE = "market-sentinel-prometheus-alert-delivery"
DEFAULT_RULE_DIRECTORY = Path("/var/lib/prometheus/market-sentinel-attestation")
DEFAULT_PROMETHEUS_ORIGIN = "http://127.0.0.1:9090"
DEFAULT_ALERTMANAGER_ORIGIN = "http://127.0.0.1:9093"
DEFAULT_RECEIVER_NAME = "market-sentinel-attestation"
DEFAULT_ONCALL_RECEIVER_NAME = "market-sentinel-oncall-attestation"
DEFAULT_RECEIVER_PATH = "/market-sentinel-alertmanager"
DEFAULT_ONCALL_RECEIPT_PATH_PREFIX = "/v1/market-sentinel/alert-receipts"
DEFAULT_ONCALL_WEBHOOK_PATH = "/v1/market-sentinel/alertmanager"
DEFAULT_ONCALL_URL_FILE = Path("/etc/market-sentinel/alertmanager-oncall-webhook-url")
DEFAULT_ONCALL_CREDENTIALS_FILE = Path("/etc/market-sentinel/alertmanager-oncall-bearer-token")
ONCALL_TOKEN_ENVIRONMENT_KEY = "MARKET_SENTINEL_ONCALL_RECEIPT_TOKEN"
MAX_HTTP_BODY_BYTES = 1024 * 1024
MAX_WEBHOOK_BODY_BYTES = 256 * 1024
MAX_ONCALL_RECEIPT_BYTES = 32 * 1024
MAX_EVIDENCE_BYTES = 4 * 1024 * 1024
MAX_ONCALL_CLOCK_SKEW_SECONDS = 60
COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")
SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
ALERT_FINGERPRINT = re.compile(r"^[0-9a-f]{16,64}$")
SAFE_RECEIVER_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,62}$")
SAFE_PROVIDER_NAME = re.compile(r"^[a-z][a-z0-9_-]{1,62}$")
SAFE_DELIVERY_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
BEARER_TOKEN = re.compile(r"^[A-Za-z0-9\-._~+/]{32,4096}=*$")
SAFE_DNS_NAME = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?$"
)
RESERVED_HOST_SUFFIXES = (
    ".example",
    ".example.com",
    ".example.net",
    ".example.org",
    ".invalid",
    ".localhost",
    ".local",
    ".test",
    ".internal",
)
RESERVED_HOSTS = {"example.com", "example.net", "example.org", "localhost"}
_UTC_CLOCK_LOCK = threading.Lock()
_LAST_UTC_TIMESTAMP_NS = 0


class DeliveryEvidenceError(RuntimeError):
    """Raised when the live alert-delivery path cannot be proven."""


@dataclass(frozen=True)
class CollectorConfig:
    source_revision: str
    deployment_identity_sha256: str
    run_id: int
    run_attempt: int
    nonce: str
    rule_directory: Path
    prometheus_origin: str
    alertmanager_origin: str
    oncall_receipt_origin: str
    oncall_receipt_token: str
    oncall_url_file: Path
    oncall_credentials_file: Path
    expected_alertmanager_gid: int
    receiver_name: str
    receiver_port: int
    output: Path
    timeout_seconds: float = 120.0
    poll_interval_seconds: float = 1.0
    request_timeout_seconds: float = 5.0
    require_root_owned_oncall_files: bool = True


@dataclass(frozen=True)
class _ResolvedPublicHTTPSOrigin:
    origin: str
    hostname: str
    port: int
    pinned_addresses: tuple[str, ...]


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_nonfinite(value: str) -> None:
    raise ValueError(f"non-finite JSON number: {value}")


def strict_json(value: str) -> Any:
    return json.loads(
        value,
        object_pairs_hook=_reject_duplicate_keys,
        parse_constant=_reject_nonfinite,
    )


def utc_now() -> str:
    """Return a UTC timestamp that is strictly ordered within this process."""

    global _LAST_UTC_TIMESTAMP_NS
    candidate_ns = time.time_ns()
    with _UTC_CLOCK_LOCK:
        minimum_ns = _LAST_UTC_TIMESTAMP_NS + 1_000
        candidate_ns = max(candidate_ns, minimum_ns)
        _LAST_UTC_TIMESTAMP_NS = candidate_ns
    seconds, nanoseconds = divmod(candidate_ns, 1_000_000_000)
    timestamp = datetime.fromtimestamp(seconds, timezone.utc).replace(microsecond=nanoseconds // 1_000)
    return timestamp.isoformat(timespec="microseconds").replace("+00:00", "Z")


def canonical_origin(value: str, label: str) -> str:
    candidate = value.strip()
    parsed = urllib.parse.urlsplit(candidate)
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.hostname is None
        or parsed.port is None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(f"{label} must be an HTTP(S) loopback origin with an explicit port")
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError as exc:
        raise ValueError(f"{label} must use a loopback IP literal, not a hostname") from exc
    if not address.is_loopback:
        raise ValueError(f"{label} must use a loopback IP address")
    host = f"[{address.compressed}]" if address.version == 6 else address.compressed
    return f"{parsed.scheme}://{host}:{parsed.port}"


def canonical_public_https_origin(
    value: str,
    label: str,
    *,
    resolver: Callable[..., Any] = socket.getaddrinfo,
) -> str:
    """Return a canonical, DNS-checked public HTTPS origin.

    The receipt API is deliberately outside the monitored host.  Rejecting
    non-global address records here prevents a protected configuration typo
    from turning the root collector into a private-network bearer-token probe.
    """

    return _resolve_public_https_origin(value, label, resolver=resolver).origin


def _resolve_public_https_origin(
    value: str,
    label: str,
    *,
    resolver: Callable[..., Any] = socket.getaddrinfo,
) -> _ResolvedPublicHTTPSOrigin:
    """Resolve and retain every public address used by the receipt transport."""

    candidate = value.strip()
    try:
        parsed = urllib.parse.urlsplit(candidate)
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"{label} has an invalid port") from exc
    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.hostname is None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            f"{label} must be a credential-free public HTTPS origin without a path, query, or fragment"
        )
    if port is not None and not 1 <= port <= 65535:
        raise ValueError(f"{label} has an invalid port")
    hostname = parsed.hostname.rstrip(".").lower()
    if hostname != parsed.hostname.lower():
        raise ValueError(f"{label} must not use a trailing-dot hostname")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        if hostname in RESERVED_HOSTS or hostname.endswith(RESERVED_HOST_SUFFIXES):
            raise ValueError(f"{label} uses a reserved or placeholder hostname") from None
        if SAFE_DNS_NAME.fullmatch(hostname) is None:
            raise ValueError(f"{label} must use a fully qualified public DNS name") from None
        try:
            answers = resolver(hostname, port or 443, type=socket.SOCK_STREAM)
        except OSError as exc:
            raise ValueError(f"{label} hostname did not resolve") from exc
        resolved: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
        for answer in answers:
            try:
                family = answer[0]
                socktype = answer[1]
                item = ipaddress.ip_address(answer[4][0])
            except (IndexError, TypeError, ValueError) as exc:
                raise ValueError(f"{label} resolver returned an invalid address") from exc
            if family not in {socket.AF_INET, socket.AF_INET6} or socktype not in {
                0,
                socket.SOCK_STREAM,
            }:
                raise ValueError(f"{label} resolver returned an invalid address family") from None
            if (family == socket.AF_INET) != (item.version == 4):
                raise ValueError(f"{label} resolver returned a mismatched address family") from None
            if item not in resolved:
                resolved.append(item)
        if not resolved or any(not item.is_global for item in resolved):
            raise ValueError(f"{label} must resolve exclusively to public global addresses") from None
        host = hostname
    else:
        if not address.is_global:
            raise ValueError(f"{label} must use a public global address")
        host = f"[{address.compressed}]" if address.version == 6 else address.compressed
        resolved = [address]
    suffix = "" if port in {None, 443} else f":{port}"
    return _ResolvedPublicHTTPSOrigin(
        origin=f"https://{host}{suffix}",
        hostname=hostname,
        port=port or 443,
        pinned_addresses=tuple(item.compressed for item in resolved),
    )


def receipt_origin_sha256(origin: str) -> str:
    return hashlib.sha256(origin.encode("utf-8")).hexdigest()


def oncall_receipt_url(origin: str, binding: str) -> str:
    if SHA256_HEX.fullmatch(binding) is None:
        raise ValueError("on-call receipt binding must be a SHA-256 digest")
    return f"{origin}{DEFAULT_ONCALL_RECEIPT_PATH_PREFIX}/{binding}"


def _validate_bearer_token(value: str) -> str:
    if (
        not isinstance(value, str)
        or not 32 <= len(value) <= 4096
        or value != value.strip()
        or BEARER_TOKEN.fullmatch(value) is None
    ):
        raise ValueError("on-call receipt bearer token must be a non-whitespace RFC 6750 token of at least 32 characters")
    return value


def _validate_private_config_metadata(
    metadata: os.stat_result,
    label: str,
    *,
    require_root_owned: bool,
    expected_group_id: int,
) -> None:
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"{label} must be a regular file")
    permissions = stat.S_IMODE(metadata.st_mode)
    if require_root_owned:
        if os.name != "posix" or metadata.st_uid != 0:
            raise ValueError(f"{label} must be owned by root on the production host")
        if permissions not in {0o600, 0o640}:
            raise ValueError(f"{label} permissions must be exactly 0600 or 0640")
        if permissions == 0o640 and metadata.st_gid != expected_group_id:
            raise ValueError(
                f"{label} mode 0640 group must match the expected Alertmanager GID"
            )


def _read_private_config_file(
    path: Path,
    label: str,
    *,
    require_root_owned: bool,
    expected_group_id: int,
) -> bytes:
    candidate = path.absolute()
    if not path.is_absolute() or candidate.is_symlink() or not candidate.is_file():
        raise ValueError(f"{label} must be an absolute regular non-symbolic-link file")
    if any(parent.is_symlink() for parent in candidate.parents):
        raise ValueError(f"{label} path must not contain symbolic-link components")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(candidate, flags)
    try:
        metadata = os.fstat(descriptor)
        _validate_private_config_metadata(
            metadata,
            label,
            require_root_owned=require_root_owned,
            expected_group_id=expected_group_id,
        )
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            raw = handle.read(4097)
    finally:
        os.close(descriptor)
    if not 0 < len(raw) <= 4096:
        raise ValueError(f"{label} must be nonempty and at most 4096 bytes")
    return raw


def validate_oncall_config_files(
    *,
    origin: str,
    bearer_token: str,
    url_file: Path,
    credentials_file: Path,
    require_root_owned: bool,
    expected_group_id: int,
) -> None:
    url_raw = _read_private_config_file(
        url_file,
        "Alertmanager on-call URL file",
        require_root_owned=require_root_owned,
        expected_group_id=expected_group_id,
    )
    credentials_raw = _read_private_config_file(
        credentials_file,
        "Alertmanager on-call credentials file",
        require_root_owned=require_root_owned,
        expected_group_id=expected_group_id,
    )
    try:
        configured_url = url_raw.decode("utf-8")
        configured_token = credentials_raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("Alertmanager on-call configuration files must be UTF-8") from exc
    if configured_url != f"{origin}{DEFAULT_ONCALL_WEBHOOK_PATH}":
        raise ValueError("Alertmanager on-call URL file does not contain the exact protected bridge endpoint")
    if configured_token != bearer_token:
        raise ValueError("Alertmanager on-call credentials do not match the protected receipt credential")


def receiver_url(port: int) -> str:
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("receiver port must be between 1 and 65535")
    return f"http://127.0.0.1:{port}{DEFAULT_RECEIVER_PATH}"


def canonical_binding_input(
    *,
    source_revision: str,
    deployment_identity_sha256: str,
    run_id: int,
    run_attempt: int,
    nonce: str,
    started_at: str,
    rule_directory: str,
    prometheus_origin: str,
    alertmanager_origin: str,
    oncall_receipt_origin_sha256: str,
    callback_url: str,
    receiver_name: str,
) -> bytes:
    payload = {
        "alertmanager_origin": alertmanager_origin,
        "callback_url": callback_url,
        "deployment_identity_sha256": deployment_identity_sha256,
        "nonce": nonce,
        "oncall_receipt_origin_sha256": oncall_receipt_origin_sha256,
        "prometheus_origin": prometheus_origin,
        "receiver_name": receiver_name,
        "report_type": REPORT_TYPE,
        "rule_directory": rule_directory,
        "run_attempt": run_attempt,
        "run_id": run_id,
        "schema_version": SCHEMA_VERSION,
        "source_revision": source_revision,
        "started_at": started_at,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def binding_sha256(**values: Any) -> str:
    return hashlib.sha256(canonical_binding_input(**values)).hexdigest()


def evidence_transcript_sha256(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def alert_name(binding: str) -> str:
    return f"MarketSentinelDeliveryAttestation_{binding[:24]}"


def group_name(binding: str) -> str:
    return f"market-sentinel-delivery-attestation-{binding[:24]}"


def rule_filename(binding: str) -> str:
    return f"market-sentinel-attestation-{binding[:24]}.yml"


def expected_labels(
    *,
    binding: str,
    source_revision: str,
    deployment_identity_sha256: str,
    run_id: int,
    run_attempt: int,
) -> dict[str, str]:
    return {
        "alertname": alert_name(binding),
        "market_sentinel_attestation": "true",
        "market_sentinel_binding": binding,
        "market_sentinel_deployment": deployment_identity_sha256,
        "market_sentinel_revision": source_revision,
        "market_sentinel_run": f"{run_id}.{run_attempt}",
        "severity": "none",
    }


def oncall_webhook_event_sha256(
    *,
    labels: dict[str, str],
    alert_fingerprint: str,
    starts_at: str,
) -> str:
    payload = {
        "alert": {
            "annotations": {"attestation_binding": labels["market_sentinel_binding"]},
            "fingerprint": alert_fingerprint,
            "labels": labels,
            "starts_at": starts_at,
            "status": "firing",
        },
        "receiver": DEFAULT_ONCALL_RECEIVER_NAME,
        "status": "firing",
        "version": "4",
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def render_rule(
    *,
    binding: str,
    source_revision: str,
    deployment_identity_sha256: str,
    run_id: int,
    run_attempt: int,
) -> str:
    labels = expected_labels(
        binding=binding,
        source_revision=source_revision,
        deployment_identity_sha256=deployment_identity_sha256,
        run_id=run_id,
        run_attempt=run_attempt,
    )
    return (
        "groups:\n"
        f"  - name: {group_name(binding)}\n"
        "    interval: 1s\n"
        "    rules:\n"
        f"      - alert: {labels['alertname']}\n"
        "        expr: vector(1)\n"
        "        labels:\n"
        "          market_sentinel_attestation: \"true\"\n"
        f"          market_sentinel_binding: \"{binding}\"\n"
        f"          market_sentinel_deployment: \"{deployment_identity_sha256}\"\n"
        f"          market_sentinel_revision: \"{source_revision}\"\n"
        f"          market_sentinel_run: \"{run_id}.{run_attempt}\"\n"
        "          severity: none\n"
        "        annotations:\n"
        f"          attestation_binding: \"{binding}\"\n"
        "          summary: MarketSentinel synthetic alert-delivery attestation\n"
    )


def rules_url(origin: str, name: str) -> str:
    query = urllib.parse.urlencode([("type", "alert"), ("rule_name[]", name)])
    return f"{origin}/api/v1/rules?{query}"


def prometheus_alerts_url(origin: str) -> str:
    return f"{origin}/api/v1/alerts"


def alertmanager_alerts_url(origin: str) -> str:
    query = urllib.parse.urlencode(
        [("active", "true"), ("silenced", "false"), ("inhibited", "false")]
    )
    return f"{origin}/api/v2/alerts?{query}"


def alertmanager_status_url(origin: str) -> str:
    return f"{origin}/api/v2/status"


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        raise urllib.error.HTTPError(req.full_url, code, "redirects are not permitted", headers, fp)


_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _RejectRedirects())


def _read_bounded(response: Any, maximum_bytes: int) -> bytes:
    declared = response.headers.get("Content-Length")
    if declared is not None:
        try:
            declared_size = int(declared)
        except ValueError as exc:
            raise DeliveryEvidenceError("monitoring API returned an invalid Content-Length") from exc
        if declared_size < 0 or declared_size > maximum_bytes:
            raise DeliveryEvidenceError("monitoring API response exceeds the evidence byte limit")
    body = response.read(maximum_bytes + 1)
    if len(body) > maximum_bytes:
        raise DeliveryEvidenceError("monitoring API response exceeds the evidence byte limit")
    return body


def http_observation(url: str, *, method: str, timeout: float, maximum_bytes: int = MAX_HTTP_BODY_BYTES) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        method=method,
        headers={"Accept": "application/json", "User-Agent": "MarketSentinel-attestation/1"},
    )
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            status = int(response.status)
            body = _read_bounded(response, maximum_bytes)
            content_type = str(response.headers.get("Content-Type") or "")
    except (OSError, urllib.error.URLError, urllib.error.HTTPError) as exc:
        raise DeliveryEvidenceError(f"monitoring API request failed for {url}: {exc}") from exc
    if status != 200:
        raise DeliveryEvidenceError(f"monitoring API returned HTTP {status} for {url}")
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DeliveryEvidenceError(f"monitoring API returned non-UTF-8 content for {url}") from exc
    return {
        "body": text,
        "body_sha256": hashlib.sha256(body).hexdigest(),
        "content_type": content_type,
        "observed_at": utc_now(),
        "status": status,
        "url": url,
    }


def observation_json(observation: dict[str, Any]) -> Any:
    media_type = str(observation.get("content_type") or "").split(";", 1)[0].strip().lower()
    if media_type not in {"application/json", "application/problem+json"}:
        raise DeliveryEvidenceError("monitoring API did not return JSON content")
    try:
        return strict_json(str(observation["body"]))
    except (json.JSONDecodeError, ValueError) as exc:
        raise DeliveryEvidenceError("monitoring API returned malformed JSON") from exc


def _yaml_list_block(source: str, field: str, value: str) -> tuple[int, int, str]:
    lines = source.splitlines()
    pattern = re.compile(rf"^( *)- {re.escape(field)}: {re.escape(value)}$")
    matches: list[tuple[int, int]] = []
    for index, line in enumerate(lines):
        match = pattern.fullmatch(line)
        if match is not None:
            matches.append((index, len(match.group(1))))
    if len(matches) != 1:
        raise DeliveryEvidenceError(f"loaded Alertmanager config must contain one exact {field}={value} block")
    start, indentation = matches[0]
    end = len(lines)
    for index in range(start + 1, len(lines)):
        line = lines[index]
        if not line.strip():
            continue
        next_indentation = len(line) - len(line.lstrip(" "))
        if next_indentation <= indentation:
            end = index
            break
    return start, indentation, "\n".join(lines[start:end]) + "\n"


def _require_exact_yaml_list_block(
    source: str,
    *,
    field: str,
    value: str,
    relative_lines: tuple[str, ...],
    label: str,
) -> tuple[int, int, str]:
    start, indentation, block = _yaml_list_block(source, field, value)
    prefix = " " * indentation
    expected = "".join(f"{prefix}{line}\n" for line in relative_lines)
    if block != expected:
        raise DeliveryEvidenceError(
            f"loaded Alertmanager {label} block has unknown, missing, reordered, or misindented fields"
        )
    return start, indentation, block


def _route_block_lines(receiver_name: str, *, continuing: bool) -> tuple[str, ...]:
    return (
        f"- receiver: {receiver_name}",
        "  matchers:",
        '    - market_sentinel_attestation="true"',
        "  group_by:",
        "    - alertname",
        "    - market_sentinel_binding",
        "  group_wait: 0s",
        "  group_interval: 1m",
        "  repeat_interval: 24h",
        f"  continue: {str(continuing).lower()}",
    )


def _external_receiver_block_lines() -> tuple[str, ...]:
    return (
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
    )


def loaded_alertmanager_config_contract(
    payload: Any,
    *,
    bearer_token: str,
    oncall_origin: str,
) -> dict[str, Any]:
    if not isinstance(payload, dict) or not isinstance(payload.get("config"), dict):
        raise DeliveryEvidenceError("Alertmanager status did not expose its loaded configuration")
    original = payload["config"].get("original")
    if not isinstance(original, str) or not original or len(original.encode("utf-8")) > MAX_HTTP_BODY_BYTES:
        raise DeliveryEvidenceError("Alertmanager loaded configuration is empty or oversized")
    if bearer_token in original or oncall_origin in original or f"{oncall_origin}{DEFAULT_ONCALL_WEBHOOK_PATH}" in original:
        raise DeliveryEvidenceError("Alertmanager status exposed an on-call secret or raw bridge URL")
    external_start, external_indent, external_route = _require_exact_yaml_list_block(
        original,
        field="receiver",
        value=DEFAULT_ONCALL_RECEIVER_NAME,
        relative_lines=_route_block_lines(DEFAULT_ONCALL_RECEIVER_NAME, continuing=True),
        label="external on-call route",
    )
    local_start, local_indent, local_route = _require_exact_yaml_list_block(
        original,
        field="receiver",
        value=DEFAULT_RECEIVER_NAME,
        relative_lines=_route_block_lines(DEFAULT_RECEIVER_NAME, continuing=False),
        label="controlled loopback route",
    )
    if external_indent != 4 or local_indent != 4 or external_start >= local_start:
        raise DeliveryEvidenceError("loaded Alertmanager routes do not continue from external on-call to loopback")
    _, receiver_indent, external_receiver = _require_exact_yaml_list_block(
        original,
        field="name",
        value=DEFAULT_ONCALL_RECEIVER_NAME,
        relative_lines=_external_receiver_block_lines(),
        label="external on-call receiver",
    )
    if receiver_indent != 2:
        raise DeliveryEvidenceError("loaded Alertmanager external receiver is outside the top-level receivers list")
    matcher = 'market_sentinel_attestation="true"'
    contract = {
        "authorization_type": "Bearer",
        "credentials_file": DEFAULT_ONCALL_CREDENTIALS_FILE.as_posix(),
        "external_continue": True,
        "external_receiver": DEFAULT_ONCALL_RECEIVER_NAME,
        "follow_redirects": False,
        "proxy_from_environment": False,
        "local_continue": False,
        "local_receiver": DEFAULT_RECEIVER_NAME,
        "matcher": matcher,
        "max_alerts": 1,
        "send_resolved": False,
        "tls_insecure_skip_verify": False,
        "url_file": DEFAULT_ONCALL_URL_FILE.as_posix(),
    }
    return {
        "config_sha256": hashlib.sha256(original.encode("utf-8")).hexdigest(),
        "contract": contract,
        "external_receiver_excerpt": external_receiver,
        "external_route_excerpt": external_route,
        "local_route_excerpt": local_route,
    }


def sanitized_alertmanager_config_observation(
    observation: dict[str, Any],
    *,
    bearer_token: str,
    oncall_origin: str,
) -> dict[str, Any]:
    proof = loaded_alertmanager_config_contract(
        observation_json(observation),
        bearer_token=bearer_token,
        oncall_origin=oncall_origin,
    )
    return {
        **proof,
        "observed_at": observation["observed_at"],
        "status": observation["status"],
        "status_body_sha256": observation["body_sha256"],
        "url": observation["url"],
    }


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS connection that never resolves or delegates through an ambient proxy."""

    def __init__(
        self,
        hostname: str,
        *,
        port: int,
        pinned_address: str,
        timeout: float,
    ) -> None:
        self._pinned_address = pinned_address
        super().__init__(hostname, port=port, timeout=timeout)

    def connect(self) -> None:
        if self._tunnel_host is not None:
            raise OSError("proxy tunnels are forbidden for the on-call receipt transport")
        address = ipaddress.ip_address(self._pinned_address)
        if not address.is_global:
            raise OSError("on-call receipt transport refused a non-public pinned address")
        family = socket.AF_INET if address.version == 4 else socket.AF_INET6
        endpoint: tuple[Any, ...]
        if address.version == 4:
            endpoint = (address.compressed, self.port)
        else:
            endpoint = (address.compressed, self.port, 0, 0)
        raw_socket = socket.socket(family, socket.SOCK_STREAM)
        try:
            raw_socket.settimeout(self.timeout)
            if self.source_address:
                raw_socket.bind(self.source_address)
            raw_socket.connect(endpoint)
            self.sock = self._context.wrap_socket(raw_socket, server_hostname=self.host)
        except BaseException:
            raw_socket.close()
            raise


def oncall_receipt_observation(
    *,
    url: str,
    bearer_token: str,
    timeout: float,
    pinned_addresses: tuple[str, ...],
    connection_factory: Callable[..., http.client.HTTPSConnection] = _PinnedHTTPSConnection,
    monotonic: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Fetch a receipt directly from the DNS answers approved during validation.

    The HTTP request uses an origin-form target and a direct TLS socket, so
    environment proxy variables and a second DNS lookup cannot receive the
    bearer credential. TLS SNI and certificate hostname checks still use the
    configured receipt hostname.
    """

    try:
        parsed = urllib.parse.urlsplit(url)
        port = parsed.port or 443
    except ValueError as exc:
        raise DeliveryEvidenceError("on-call receipt URL is invalid") from exc
    if (
        parsed.scheme != "https"
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or re.fullmatch(
            rf"{re.escape(DEFAULT_ONCALL_RECEIPT_PATH_PREFIX)}/[0-9a-f]{{64}}",
            parsed.path,
        )
        is None
        or parsed.query
        or parsed.fragment
    ):
        raise DeliveryEvidenceError("on-call receipt URL is not the fixed credential-free HTTPS endpoint")
    hostname = parsed.hostname.lower()
    try:
        normalized_addresses = tuple(ipaddress.ip_address(item).compressed for item in pinned_addresses)
    except (TypeError, ValueError) as exc:
        raise DeliveryEvidenceError("on-call receipt transport received an invalid pinned address") from exc
    if not normalized_addresses or any(
        not ipaddress.ip_address(item).is_global for item in normalized_addresses
    ):
        raise DeliveryEvidenceError("on-call receipt transport requires public pinned addresses")
    request_target = parsed.path
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {bearer_token}",
        "Cache-Control": "no-store",
        "User-Agent": "MarketSentinel-oncall-attestation/1",
    }
    deadline = monotonic() + timeout
    failures: list[str] = []
    status: int | None = None
    body: bytes | None = None
    content_type = ""
    for pinned_address in normalized_addresses:
        remaining = deadline - monotonic()
        if remaining <= 0:
            break
        connection = connection_factory(
            hostname,
            port=port,
            pinned_address=pinned_address,
            timeout=remaining,
        )
        try:
            connection.request("GET", request_target, headers=headers)
            response = connection.getresponse()
            status = int(response.status)
            body = _read_bounded(response, MAX_ONCALL_RECEIPT_BYTES)
            content_type = str(response.headers.get("Content-Type") or "")
            break
        except (OSError, ssl.SSLError, http.client.HTTPException) as exc:
            failures.append(f"{pinned_address}: {exc}")
        finally:
            connection.close()
    if status is None or body is None:
        detail = "; ".join(failures) if failures else "request deadline expired"
        raise DeliveryEvidenceError(f"on-call receipt request failed: {detail}")
    if status != 200:
        raise DeliveryEvidenceError(f"on-call receipt service returned HTTP {status}")
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DeliveryEvidenceError("on-call receipt service returned non-UTF-8 content") from exc
    return {
        "body": text,
        "body_sha256": hashlib.sha256(body).hexdigest(),
        "content_type": content_type,
        "observed_at": utc_now(),
        "status": status,
    }


ONCALL_RECEIPT_FIELDS = {
    "acknowledged_at",
    "acknowledgement_kind",
    "acknowledger_sha256",
    "alert_fingerprint",
    "binding_sha256",
    "delivery_id",
    "deployment_identity_sha256",
    "dispatched_at",
    "oncall_channel_sha256",
    "oncall_provider",
    "receipt_type",
    "received_at",
    "run_attempt",
    "run_id",
    "schema_version",
    "source_revision",
    "status",
    "webhook_body_sha256",
    "webhook_event_sha256",
    "webhook_receiver",
}
ONCALL_RECEIPT_TYPE = "market-sentinel-oncall-delivery-receipt"


def _utc_timestamp(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise DeliveryEvidenceError(f"{label} must be a UTC timestamp ending in Z")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise DeliveryEvidenceError(f"{label} is not a valid ISO-8601 timestamp") from exc
    return parsed.astimezone(timezone.utc)


def parse_oncall_observation(observation: Any) -> tuple[dict[str, Any], datetime]:
    if not isinstance(observation, dict) or set(observation) != {
        "body",
        "body_sha256",
        "content_type",
        "observed_at",
        "status",
    }:
        raise DeliveryEvidenceError("on-call receipt observation fields are not exact")
    if observation["status"] != 200 or not isinstance(observation["body"], str):
        raise DeliveryEvidenceError("on-call receipt observation was not a successful UTF-8 response")
    if not isinstance(observation["content_type"], str):
        raise DeliveryEvidenceError("on-call receipt Content-Type must be a string")
    media_type = observation["content_type"].split(";", 1)[0].strip().lower()
    if media_type not in {"application/json", "application/problem+json"}:
        raise DeliveryEvidenceError("on-call receipt service did not return JSON content")
    raw = observation["body"].encode("utf-8")
    if len(raw) > MAX_ONCALL_RECEIPT_BYTES or observation["body_sha256"] != hashlib.sha256(raw).hexdigest():
        raise DeliveryEvidenceError("on-call receipt body digest does not match its bounded raw bytes")
    try:
        payload = strict_json(observation["body"])
    except (json.JSONDecodeError, ValueError) as exc:
        raise DeliveryEvidenceError("on-call receipt body is not strict JSON") from exc
    if not isinstance(payload, dict) or set(payload) != ONCALL_RECEIPT_FIELDS:
        raise DeliveryEvidenceError("on-call receipt fields are not exact")
    return payload, _utc_timestamp(observation["observed_at"], "on-call receipt observed_at")


def validate_oncall_receipt(
    payload: dict[str, Any],
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
) -> dict[str, datetime]:
    expected_identity = {
        "binding_sha256": binding,
        "deployment_identity_sha256": deployment_identity_sha256,
        "run_attempt": run_attempt,
        "run_id": run_id,
        "source_revision": source_revision,
    }
    if (
        type(payload.get("run_id")) is not int
        or type(payload.get("run_attempt")) is not int
        or type(payload.get("schema_version")) is not int
    ):
        raise DeliveryEvidenceError("on-call receipt numeric identity fields must be integers")
    if any(payload.get(key) != value for key, value in expected_identity.items()):
        raise DeliveryEvidenceError("on-call receipt is not bound to the exact deployment challenge")
    if (
        payload.get("schema_version") != SCHEMA_VERSION
        or payload.get("receipt_type") != ONCALL_RECEIPT_TYPE
        or payload.get("status") != "acknowledged"
        or payload.get("acknowledgement_kind") != "human"
    ):
        raise DeliveryEvidenceError("on-call receipt status does not prove a human acknowledgement")
    if payload.get("alert_fingerprint") != alert_fingerprint or ALERT_FINGERPRINT.fullmatch(alert_fingerprint) is None:
        raise DeliveryEvidenceError("on-call receipt fingerprint does not match Alertmanager")
    expected_event_sha256 = oncall_webhook_event_sha256(
        labels=labels,
        alert_fingerprint=alert_fingerprint,
        starts_at=alert_starts_at,
    )
    if (
        payload.get("webhook_receiver") != DEFAULT_ONCALL_RECEIVER_NAME
        or payload.get("webhook_event_sha256") != expected_event_sha256
        or not isinstance(payload.get("webhook_body_sha256"), str)
        or SHA256_HEX.fullmatch(payload["webhook_body_sha256"]) is None
    ):
        raise DeliveryEvidenceError("on-call receipt is not bound to the exact external Alertmanager webhook")
    provider = payload.get("oncall_provider")
    if (
        not isinstance(provider, str)
        or SAFE_PROVIDER_NAME.fullmatch(provider) is None
        or provider in {"local", "loopback", "mock", "none", "test"}
    ):
        raise DeliveryEvidenceError("on-call receipt provider identity is invalid")
    if not isinstance(payload.get("delivery_id"), str) or SAFE_DELIVERY_ID.fullmatch(payload["delivery_id"]) is None:
        raise DeliveryEvidenceError("on-call receipt delivery ID is invalid")
    for field in ("oncall_channel_sha256", "acknowledger_sha256"):
        if not isinstance(payload.get(field), str) or SHA256_HEX.fullmatch(payload[field]) is None:
            raise DeliveryEvidenceError(f"on-call receipt {field} is not a SHA-256 identity")
    received = _utc_timestamp(payload.get("received_at"), "on-call received_at")
    dispatched = _utc_timestamp(payload.get("dispatched_at"), "on-call dispatched_at")
    acknowledged = _utc_timestamp(payload.get("acknowledged_at"), "on-call acknowledged_at")
    alert_active = _utc_timestamp(alert_starts_at, "Alertmanager alert startsAt")
    skew = timedelta(seconds=MAX_ONCALL_CLOCK_SKEW_SECONDS)
    if received < alert_active - skew:
        raise DeliveryEvidenceError(
            "on-call receipt received_at precedes the Alertmanager alert activeAt beyond allowed clock skew"
        )
    if not (
        received <= dispatched < acknowledged
        and received >= started_at - skew
        and acknowledged <= observed_at + skew
    ):
        raise DeliveryEvidenceError("on-call receipt timestamps are out of order or outside the active challenge window")
    return {
        "acknowledged_at": acknowledged,
        "dispatched_at": dispatched,
        "received_at": received,
    }


def _labels_match(candidate: Any, expected: dict[str, str]) -> bool:
    return isinstance(candidate, dict) and all(candidate.get(key) == value for key, value in expected.items())


def loaded_rule_present(payload: Any, *, expected_file: str, binding: str, labels: dict[str, str]) -> bool:
    if not isinstance(payload, dict) or payload.get("status") != "success":
        return False
    data = payload.get("data")
    groups = data.get("groups") if isinstance(data, dict) else None
    if not isinstance(groups, list):
        return False
    rule_labels = {key: value for key, value in labels.items() if key != "alertname"}
    matches = []
    for group in groups:
        if not isinstance(group, dict) or group.get("name") != group_name(binding) or group.get("file") != expected_file:
            continue
        rules = group.get("rules")
        if not isinstance(rules, list):
            continue
        matches.extend(
            rule
            for rule in rules
            if isinstance(rule, dict)
            and rule.get("name") == labels["alertname"]
            and rule.get("query") == "vector(1)"
            and rule.get("health") == "ok"
            and _labels_match(rule.get("labels"), rule_labels)
        )
    return len(matches) == 1


def loaded_rule_absent(payload: Any, *, expected_file: str, binding: str, labels: dict[str, str]) -> bool:
    if not isinstance(payload, dict) or payload.get("status") != "success":
        return False
    data = payload.get("data")
    groups = data.get("groups") if isinstance(data, dict) else None
    if not isinstance(groups, list):
        return False
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
                (group.get("file") == expected_file and group.get("name") == group_name(binding))
                or rule.get("name") == labels["alertname"]
                or (isinstance(rule_labels, dict) and rule_labels.get("market_sentinel_binding") == binding)
            ):
                return False
    return True


def prometheus_alert_present(payload: Any, labels: dict[str, str]) -> bool:
    if not isinstance(payload, dict) or payload.get("status") != "success":
        return False
    data = payload.get("data")
    alerts = data.get("alerts") if isinstance(data, dict) else None
    if not isinstance(alerts, list):
        return False
    return sum(
        1
        for alert in alerts
        if isinstance(alert, dict)
        and alert.get("state") == "firing"
        and _labels_match(alert.get("labels"), labels)
    ) == 1


def alertmanager_alert_present(payload: Any, labels: dict[str, str]) -> bool:
    if not isinstance(payload, list):
        return False
    return sum(
        1
        for alert in payload
        if isinstance(alert, dict)
        and isinstance(alert.get("status"), dict)
        and alert["status"].get("state") == "active"
        and _labels_match(alert.get("labels"), labels)
        and {
            receiver.get("name")
            for receiver in alert.get("receivers", [])
            if isinstance(receiver, dict)
        }
        == {DEFAULT_RECEIVER_NAME, DEFAULT_ONCALL_RECEIVER_NAME}
    ) == 1


def alertmanager_alert_fingerprint(payload: Any, labels: dict[str, str]) -> str:
    if not isinstance(payload, list):
        raise DeliveryEvidenceError("Alertmanager response is not an array")
    matches = [
        alert
        for alert in payload
        if isinstance(alert, dict)
        and isinstance(alert.get("status"), dict)
        and alert["status"].get("state") == "active"
        and _labels_match(alert.get("labels"), labels)
        and {
            receiver.get("name")
            for receiver in alert.get("receivers", [])
            if isinstance(receiver, dict)
        }
        == {DEFAULT_RECEIVER_NAME, DEFAULT_ONCALL_RECEIVER_NAME}
    ]
    if len(matches) != 1:
        raise DeliveryEvidenceError("Alertmanager did not expose exactly one bound synthetic alert")
    fingerprint = matches[0].get("fingerprint")
    if not isinstance(fingerprint, str) or ALERT_FINGERPRINT.fullmatch(fingerprint) is None:
        raise DeliveryEvidenceError("Alertmanager synthetic alert fingerprint is invalid")
    return fingerprint


def alertmanager_alert_starts_at(payload: Any, labels: dict[str, str]) -> str:
    if not isinstance(payload, list):
        raise DeliveryEvidenceError("Alertmanager response is not an array")
    matches = [
        alert
        for alert in payload
        if isinstance(alert, dict)
        and isinstance(alert.get("status"), dict)
        and alert["status"].get("state") == "active"
        and _labels_match(alert.get("labels"), labels)
    ]
    if len(matches) != 1 or not isinstance(matches[0].get("startsAt"), str):
        raise DeliveryEvidenceError("Alertmanager synthetic alert start timestamp is unavailable")
    _utc_timestamp(matches[0]["startsAt"], "Alertmanager alert startsAt")
    return matches[0]["startsAt"]


def webhook_payload_present(payload: Any, labels: dict[str, str], receiver_name: str) -> bool:
    if not isinstance(payload, dict) or payload.get("receiver") != receiver_name or payload.get("status") != "firing":
        return False
    alerts = payload.get("alerts")
    if not isinstance(alerts, list):
        return False
    return sum(
        1
        for alert in alerts
        if isinstance(alert, dict)
        and alert.get("status") == "firing"
        and _labels_match(alert.get("labels"), labels)
    ) == 1


def _receiver_handler(
    receipts: "queue.Queue[dict[str, Any]]",
    labels: dict[str, str],
    receiver_name: str,
) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "MarketSentinelAttestation/1"
        sys_version = ""

        def log_message(self, format: str, *args: Any) -> None:
            return

        def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API.
            if self.path != DEFAULT_RECEIVER_PATH or not ipaddress.ip_address(self.client_address[0]).is_loopback:
                self.send_error(404)
                return
            if self.headers.get("Transfer-Encoding"):
                self.send_error(400, "transfer encoding is not accepted")
                return
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                self.send_error(411)
                return
            content_type = str(self.headers.get("Content-Type") or "")
            if not 0 < length <= MAX_WEBHOOK_BODY_BYTES or content_type.split(";", 1)[0].strip().lower() != "application/json":
                self.send_error(413)
                return
            body = self.rfile.read(length)
            if len(body) != length:
                self.send_error(400)
                return
            try:
                text = body.decode("utf-8")
                payload = strict_json(text)
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
                self.send_error(400)
                return
            if not webhook_payload_present(payload, labels, receiver_name):
                self.send_error(422)
                return
            receipts.put(
                {
                    "body": text,
                    "body_sha256": hashlib.sha256(body).hexdigest(),
                    "content_type": content_type,
                    "observed_at": utc_now(),
                    "remote_ip": self.client_address[0],
                }
            )
            self.send_response(204)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()

    return Handler


class _PreboundThreadingHTTPServer(ThreadingHTTPServer):
    """Serve from a validated socket that was bound before the collector started."""

    def __init__(self, receiver_socket: socket.socket, handler: type[BaseHTTPRequestHandler]) -> None:
        receiver_address = receiver_socket.getsockname()
        BaseServer.__init__(self, receiver_address, handler)
        self.socket = receiver_socket
        self.server_name = socket.getfqdn(receiver_address[0])
        self.server_port = receiver_address[1]


def _build_receiver_server(
    receiver_port: int,
    handler: type[BaseHTTPRequestHandler],
    receiver_socket: socket.socket | None,
) -> ThreadingHTTPServer:
    if receiver_socket is None:
        return ThreadingHTTPServer(("127.0.0.1", receiver_port), handler)
    try:
        if receiver_socket.family != socket.AF_INET or receiver_socket.type & socket.SOCK_STREAM != socket.SOCK_STREAM:
            raise DeliveryEvidenceError("controlled receiver socket must be an IPv4 stream socket")
        address = receiver_socket.getsockname()
        if address != ("127.0.0.1", receiver_port):
            raise DeliveryEvidenceError("controlled receiver socket is not bound to the configured loopback port")
        if receiver_socket.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN) != 1:
            raise DeliveryEvidenceError("controlled receiver socket must already be listening")
        return _PreboundThreadingHTTPServer(receiver_socket, handler)
    except BaseException:
        receiver_socket.close()
        raise


def _validate_config(
    config: CollectorConfig,
    *,
    origin_resolver: Callable[..., Any],
) -> tuple[str, str, str, Path, _ResolvedPublicHTTPSOrigin, str]:
    revision = config.source_revision.strip().lower()
    deployment = config.deployment_identity_sha256.strip().lower()
    nonce = config.nonce.strip().lower()
    if not COMMIT_SHA.fullmatch(revision):
        raise ValueError("source revision must be a lowercase 40-character Git commit")
    if not SHA256_HEX.fullmatch(deployment):
        raise ValueError("deployment identity must be a lowercase SHA-256 digest")
    if not SHA256_HEX.fullmatch(nonce):
        raise ValueError("nonce must be a lowercase 256-bit hexadecimal challenge")
    if type(config.run_id) is not int or config.run_id <= 0 or type(config.run_attempt) is not int or config.run_attempt <= 0:
        raise ValueError("run ID and attempt must be positive integers")
    if not SAFE_RECEIVER_NAME.fullmatch(config.receiver_name) or config.receiver_name != DEFAULT_RECEIVER_NAME:
        raise ValueError("receiver name must be the fixed controlled attestation receiver")
    if not math.isfinite(config.timeout_seconds) or config.timeout_seconds <= 0:
        raise ValueError("timeout must be a positive finite number")
    if not math.isfinite(config.poll_interval_seconds) or not 0 < config.poll_interval_seconds <= 10:
        raise ValueError("poll interval must be in the range (0, 10]")
    if not math.isfinite(config.request_timeout_seconds) or config.request_timeout_seconds <= 0:
        raise ValueError("request timeout must be a positive finite number")
    if type(config.require_root_owned_oncall_files) is not bool:
        raise ValueError("root-owned on-call file enforcement must be a boolean")
    if type(config.expected_alertmanager_gid) is not int or config.expected_alertmanager_gid <= 0:
        raise ValueError("expected Alertmanager GID must be a positive integer")
    rule_directory = config.rule_directory.absolute()
    if not rule_directory.is_dir():
        raise ValueError("rule directory must be an existing absolute directory")
    if any(candidate.is_symlink() for candidate in (rule_directory, *rule_directory.parents)):
        raise ValueError("rule directory must not contain symbolic-link components")
    output_parent = config.output.parent.absolute()
    if not output_parent.is_dir() or config.output.is_symlink():
        raise ValueError("evidence output parent must exist and output must not be a symbolic link")
    if any(candidate.is_symlink() for candidate in (output_parent, *output_parent.parents)):
        raise ValueError("evidence output path must not contain symbolic-link components")
    oncall_target = _resolve_public_https_origin(
        config.oncall_receipt_origin,
        "on-call receipt origin",
        resolver=origin_resolver,
    )
    oncall_token = _validate_bearer_token(config.oncall_receipt_token)
    validate_oncall_config_files(
        origin=oncall_target.origin,
        bearer_token=oncall_token,
        url_file=config.oncall_url_file,
        credentials_file=config.oncall_credentials_file,
        require_root_owned=config.require_root_owned_oncall_files,
        expected_group_id=config.expected_alertmanager_gid,
    )
    return revision, deployment, nonce, rule_directory, oncall_target, oncall_token


def _create_rule(path: Path, content: str) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o644)
    try:
        raw = content.encode("utf-8")
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        if os.name == "posix":
            os.chmod(path, 0o644, follow_symlinks=False)
    finally:
        os.close(descriptor)


def _write_evidence(path: Path, payload: dict[str, Any]) -> None:
    encoded = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
    if len(encoded) > MAX_EVIDENCE_BYTES:
        raise DeliveryEvidenceError("evidence report exceeds the maximum byte limit")
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            if os.name == "posix":
                os.fchmod(handle.fileno(), 0o600)
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise


def _poll_json(
    url: str,
    predicate: Callable[[Any], bool],
    *,
    deadline: float,
    poll_interval: float,
    request_timeout: float,
    monotonic: Callable[[], float],
    sleeper: Callable[[float], None],
) -> dict[str, Any]:
    last_error = "no response"
    while monotonic() < deadline:
        try:
            remaining = max(0.01, deadline - monotonic())
            observation = http_observation(url, method="GET", timeout=min(request_timeout, remaining))
            payload = observation_json(observation)
            if predicate(payload):
                return observation
            last_error = "response did not contain the exact attestation object"
        except DeliveryEvidenceError as exc:
            last_error = str(exc)
        remaining = deadline - monotonic()
        if remaining > 0:
            sleeper(min(poll_interval, remaining))
    raise DeliveryEvidenceError(f"timed out waiting for {url}: {last_error}")


def _poll_oncall_receipt(
    *,
    url: str,
    bearer_token: str,
    pinned_addresses: tuple[str, ...],
    fetcher: Callable[..., dict[str, Any]],
    source_revision: str,
    deployment_identity_sha256: str,
    run_id: int,
    run_attempt: int,
    binding: str,
    alert_fingerprint: str,
    labels: dict[str, str],
    alert_starts_at: str,
    started_at: datetime,
    deadline: float,
    poll_interval: float,
    request_timeout: float,
    monotonic: Callable[[], float],
    sleeper: Callable[[float], None],
) -> dict[str, Any]:
    last_error = "no receipt"
    while monotonic() < deadline:
        try:
            remaining = max(0.01, deadline - monotonic())
            observation = fetcher(
                url=url,
                bearer_token=bearer_token,
                timeout=min(request_timeout, remaining),
                pinned_addresses=pinned_addresses,
            )
            payload, observed_at = parse_oncall_observation(observation)
            validate_oncall_receipt(
                payload,
                source_revision=source_revision,
                deployment_identity_sha256=deployment_identity_sha256,
                run_id=run_id,
                run_attempt=run_attempt,
                binding=binding,
                alert_fingerprint=alert_fingerprint,
                labels=labels,
                alert_starts_at=alert_starts_at,
                started_at=started_at,
                observed_at=observed_at,
            )
            return observation
        except DeliveryEvidenceError as exc:
            last_error = str(exc)
        remaining = deadline - monotonic()
        if remaining > 0:
            sleeper(min(poll_interval, remaining))
    raise DeliveryEvidenceError(f"timed out waiting for acknowledged external on-call receipt: {last_error}")


def collect_evidence(
    config: CollectorConfig,
    *,
    monotonic: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
    origin_resolver: Callable[..., Any] = socket.getaddrinfo,
    receipt_fetcher: Callable[..., dict[str, Any]] = oncall_receipt_observation,
    receiver_socket: socket.socket | None = None,
) -> dict[str, Any]:
    revision, deployment, nonce, rule_directory, oncall_target, oncall_token = _validate_config(
        config,
        origin_resolver=origin_resolver,
    )
    oncall_origin = oncall_target.origin
    prometheus_origin = canonical_origin(config.prometheus_origin, "Prometheus origin")
    alertmanager_origin = canonical_origin(config.alertmanager_origin, "Alertmanager origin")
    callback_url = receiver_url(config.receiver_port)
    oncall_origin_digest = receipt_origin_sha256(oncall_origin)
    started_at = utc_now()
    started = _utc_timestamp(started_at, "started_at")
    binding = binding_sha256(
        source_revision=revision,
        deployment_identity_sha256=deployment,
        run_id=config.run_id,
        run_attempt=config.run_attempt,
        nonce=nonce,
        started_at=started_at,
        rule_directory=str(rule_directory),
        prometheus_origin=prometheus_origin,
        alertmanager_origin=alertmanager_origin,
        oncall_receipt_origin_sha256=oncall_origin_digest,
        callback_url=callback_url,
        receiver_name=config.receiver_name,
    )
    labels = expected_labels(
        binding=binding,
        source_revision=revision,
        deployment_identity_sha256=deployment,
        run_id=config.run_id,
        run_attempt=config.run_attempt,
    )
    rule_content = render_rule(
        binding=binding,
        source_revision=revision,
        deployment_identity_sha256=deployment,
        run_id=config.run_id,
        run_attempt=config.run_attempt,
    )
    rule_path = rule_directory / rule_filename(binding)
    receipts: "queue.Queue[dict[str, Any]]" = queue.Queue(maxsize=4)
    server = _build_receiver_server(
        config.receiver_port,
        _receiver_handler(receipts, labels, config.receiver_name),
        receiver_socket,
    )
    server.daemon_threads = True
    receiver_thread = threading.Thread(target=server.serve_forever, name="alertmanager-attestation-receiver", daemon=True)
    receiver_thread.start()
    deadline = monotonic() + config.timeout_seconds
    created = False
    cleanup_observation: dict[str, Any] | None = None
    cleanup_rules_observation: dict[str, Any] | None = None
    primary_error: BaseException | None = None
    observations: dict[str, Any] = {}
    try:
        _create_rule(rule_path, rule_content)
        created = True
        remaining = max(0.01, deadline - monotonic())
        observations["prometheus_reload"] = http_observation(
            f"{prometheus_origin}/-/reload",
            method="POST",
            timeout=min(config.request_timeout_seconds, remaining),
            maximum_bytes=64 * 1024,
        )
        observations["prometheus_rules"] = _poll_json(
            rules_url(prometheus_origin, labels["alertname"]),
            lambda payload: loaded_rule_present(
                payload,
                expected_file=str(rule_path),
                binding=binding,
                labels=labels,
            ),
            deadline=deadline,
            poll_interval=config.poll_interval_seconds,
            request_timeout=config.request_timeout_seconds,
            monotonic=monotonic,
            sleeper=sleeper,
        )
        observations["prometheus_alerts"] = _poll_json(
            prometheus_alerts_url(prometheus_origin),
            lambda payload: prometheus_alert_present(payload, labels),
            deadline=deadline,
            poll_interval=config.poll_interval_seconds,
            request_timeout=config.request_timeout_seconds,
            monotonic=monotonic,
            sleeper=sleeper,
        )
        observations["alertmanager_alerts"] = _poll_json(
            alertmanager_alerts_url(alertmanager_origin),
            lambda payload: alertmanager_alert_present(payload, labels),
            deadline=deadline,
            poll_interval=config.poll_interval_seconds,
            request_timeout=config.request_timeout_seconds,
            monotonic=monotonic,
            sleeper=sleeper,
        )
        alertmanager_payload = observation_json(observations["alertmanager_alerts"])
        fingerprint = alertmanager_alert_fingerprint(alertmanager_payload, labels)
        alert_starts_at = alertmanager_alert_starts_at(alertmanager_payload, labels)
        remaining = max(0.01, deadline - monotonic())
        raw_config_observation = http_observation(
            alertmanager_status_url(alertmanager_origin),
            method="GET",
            timeout=min(config.request_timeout_seconds, remaining),
        )
        observations["alertmanager_config"] = sanitized_alertmanager_config_observation(
            raw_config_observation,
            bearer_token=oncall_token,
            oncall_origin=oncall_origin,
        )
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise DeliveryEvidenceError("timed out waiting for the controlled Alertmanager receiver")
        try:
            observations["receiver_webhook"] = receipts.get(timeout=remaining)
        except queue.Empty as exc:
            raise DeliveryEvidenceError("timed out waiting for the controlled Alertmanager receiver") from exc
        observations["oncall_receipt"] = _poll_oncall_receipt(
            url=oncall_receipt_url(oncall_origin, binding),
            bearer_token=oncall_token,
            pinned_addresses=oncall_target.pinned_addresses,
            fetcher=receipt_fetcher,
            source_revision=revision,
            deployment_identity_sha256=deployment,
            run_id=config.run_id,
            run_attempt=config.run_attempt,
            binding=binding,
            alert_fingerprint=fingerprint,
            labels=labels,
            alert_starts_at=alert_starts_at,
            started_at=started,
            deadline=deadline,
            poll_interval=config.poll_interval_seconds,
            request_timeout=config.request_timeout_seconds,
            monotonic=monotonic,
            sleeper=sleeper,
        )
    except BaseException as exc:
        primary_error = exc
    finally:
        cleanup_error: BaseException | None = None
        if created:
            try:
                rule_path.unlink()
                cleanup_observation = http_observation(
                    f"{prometheus_origin}/-/reload",
                    method="POST",
                    timeout=config.request_timeout_seconds,
                    maximum_bytes=64 * 1024,
                )
                cleanup_deadline = monotonic() + min(30.0, max(5.0, config.request_timeout_seconds * 2))
                cleanup_rules_observation = _poll_json(
                    rules_url(prometheus_origin, labels["alertname"]),
                    lambda payload: loaded_rule_absent(
                        payload,
                        expected_file=str(rule_path),
                        binding=binding,
                        labels=labels,
                    ),
                    deadline=cleanup_deadline,
                    poll_interval=config.poll_interval_seconds,
                    request_timeout=config.request_timeout_seconds,
                    monotonic=monotonic,
                    sleeper=sleeper,
                )
            except BaseException as exc:
                cleanup_error = exc
        server.shutdown()
        server.server_close()
        receiver_thread.join(timeout=5)
        if cleanup_error is not None:
            if primary_error is None:
                primary_error = DeliveryEvidenceError(f"failed to remove the synthetic rule cleanly: {cleanup_error}")
            else:
                primary_error = DeliveryEvidenceError(
                    f"{primary_error}; synthetic-rule cleanup also failed: {cleanup_error}"
                )
    if primary_error is not None:
        raise primary_error
    if cleanup_observation is None or cleanup_rules_observation is None:
        raise DeliveryEvidenceError("synthetic rule cleanup was not observed")
    observations["cleanup_reload"] = cleanup_observation
    observations["cleanup_rules"] = cleanup_rules_observation
    completed_at = utc_now()
    evidence = {
        "challenge": {"binding_sha256": binding, "nonce": nonce},
        "completed_at": completed_at,
        "endpoints": {
            "alertmanager_origin": alertmanager_origin,
            "oncall_receipt_origin_sha256": oncall_origin_digest,
            "prometheus_origin": prometheus_origin,
            "receiver_name": config.receiver_name,
            "receiver_url": callback_url,
        },
        "identity": {
            "deployment_identity_sha256": deployment,
            "run_attempt": config.run_attempt,
            "run_id": config.run_id,
            "source_revision": revision,
        },
        "observations": observations,
        "report_type": REPORT_TYPE,
        "rule": {
            "alert_name": labels["alertname"],
            "content": rule_content,
            "group_name": group_name(binding),
            "path": str(rule_path),
            "sha256": hashlib.sha256(rule_content.encode("utf-8")).hexdigest(),
        },
        "schema_version": SCHEMA_VERSION,
        "started_at": started_at,
        "status": "ok",
    }
    evidence["transcript_sha256"] = evidence_transcript_sha256(evidence)
    serialized_evidence = json.dumps(evidence, sort_keys=True, separators=(",", ":"))
    if oncall_token in serialized_evidence or oncall_origin in serialized_evidence:
        raise DeliveryEvidenceError("on-call secret or raw receipt origin leaked into evidence")
    _write_evidence(config.output, evidence)
    return evidence


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Prove a run-unique Prometheus alert traverses Alertmanager to both a controlled "
            "loopback receiver and an acknowledged external on-call channel."
        )
    )
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--deployment-identity-sha256", required=True)
    parser.add_argument("--run-id", required=True, type=int)
    parser.add_argument("--run-attempt", required=True, type=int)
    parser.add_argument("--nonce", required=True, help="Externally generated 64-character lowercase hexadecimal challenge.")
    parser.add_argument("--rule-directory", type=Path, default=DEFAULT_RULE_DIRECTORY)
    parser.add_argument("--prometheus-origin", default=DEFAULT_PROMETHEUS_ORIGIN)
    parser.add_argument("--alertmanager-origin", default=DEFAULT_ALERTMANAGER_ORIGIN)
    parser.add_argument("--oncall-receipt-origin", required=True)
    parser.add_argument("--oncall-url-file", type=Path, default=DEFAULT_ONCALL_URL_FILE)
    parser.add_argument("--oncall-credentials-file", type=Path, default=DEFAULT_ONCALL_CREDENTIALS_FILE)
    parser.add_argument("--expected-alertmanager-gid", type=int, required=True)
    parser.add_argument("--receiver-name", default=DEFAULT_RECEIVER_NAME)
    parser.add_argument("--receiver-port", type=int, default=19094)
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    parser.add_argument("--poll-interval-seconds", type=float, default=1.0)
    parser.add_argument("--request-timeout-seconds", type=float, default=5.0)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    oncall_token = os.environ.get(ONCALL_TOKEN_ENVIRONMENT_KEY, "")
    config = CollectorConfig(
        source_revision=args.source_revision,
        deployment_identity_sha256=args.deployment_identity_sha256,
        run_id=args.run_id,
        run_attempt=args.run_attempt,
        nonce=args.nonce,
        rule_directory=args.rule_directory,
        prometheus_origin=args.prometheus_origin,
        alertmanager_origin=args.alertmanager_origin,
        oncall_receipt_origin=args.oncall_receipt_origin,
        oncall_receipt_token=oncall_token,
        oncall_url_file=args.oncall_url_file,
        oncall_credentials_file=args.oncall_credentials_file,
        expected_alertmanager_gid=args.expected_alertmanager_gid,
        receiver_name=args.receiver_name,
        receiver_port=args.receiver_port,
        output=args.output,
        timeout_seconds=args.timeout_seconds,
        poll_interval_seconds=args.poll_interval_seconds,
        request_timeout_seconds=args.request_timeout_seconds,
    )
    try:
        evidence = collect_evidence(config)
    except (DeliveryEvidenceError, OSError, ValueError) as exc:
        print(f"Prometheus delivery attestation failed: {exc}", file=os.sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "binding_sha256": evidence["challenge"]["binding_sha256"],
                "output": str(args.output),
                "status": "ok",
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
