from __future__ import annotations

import argparse
import base64
import hashlib
import ipaddress
import json
import os
import re
import shlex
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from stat import S_IFDIR, S_IFREG, S_IMODE, S_ISDIR, S_ISREG
from typing import Any, Callable
from urllib.error import HTTPError
from urllib.parse import urljoin, urlparse
from urllib.request import Request

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.deployment_identity import (
    SHA256_HEX,
    canonical_https_origin,
    frontend_tree_sha256,
    git_top_level_matches,
    safe_git_command,
    safe_git_environment,
)
from core.probe_transport import is_public_probe_address
from core.request_control import request_scope, resolve_with_deadline

if __package__:
    from scripts.restore_state_backup import (
        DEFAULT_MAX_ARCHIVE_BYTES,
        DEFAULT_MAX_MEMBERS,
        DEFAULT_MAX_UNCOMPRESSED_BYTES,
        catalog_verified_backups,
        restore_backup,
    )
    from scripts.verify_service_health import check_health, open_probe as urlopen, read_health_payload, read_probe_body
    from scripts.verify_restored_state import application_check_valid, isolated_environment
else:  # Supports the documented `python /path/to/scripts/verify_production_deployment.py` invocation.
    from restore_state_backup import (
        DEFAULT_MAX_ARCHIVE_BYTES,
        DEFAULT_MAX_MEMBERS,
        DEFAULT_MAX_UNCOMPRESSED_BYTES,
        catalog_verified_backups,
        restore_backup,
    )
    from verify_service_health import check_health, open_probe as urlopen, read_health_payload, read_probe_body
    from verify_restored_state import application_check_valid, isolated_environment

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 uses the locked tomli dependency.
    import tomli as tomllib


CommandRunner = Callable[[list[str]], subprocess.CompletedProcess[str]]
REQUIRED_UNITS = (
    "market-sentinel-web.service",
    "market-sentinel-health.timer",
    "market-sentinel-backup.timer",
    "market-sentinel-alerts-refresh.timer",
    "market-sentinel-wallets-poll.timer",
)
REQUIRED_PROXY_HEADER_VALUES = {
    "strict-transport-security": ("max-age=31536000", "includesubdomains"),
    "content-security-policy": (
        "default-src 'self'",
        "base-uri 'self'",
        "object-src 'none'",
        "frame-ancestors 'none'",
        "form-action 'self'",
        "connect-src 'self'",
        "img-src 'self' data:",
        "script-src 'self'",
        "style-src 'self'",
    ),
    "x-content-type-options": ("nosniff",),
    "x-frame-options": ("deny",),
    "referrer-policy": ("no-referrer",),
    "permissions-policy": ("camera=()", "geolocation=()", "microphone=()", "payment=()", "usb=()"),
    "cross-origin-opener-policy": ("same-origin",),
    "cross-origin-resource-policy": ("same-origin",),
}
BACKUP_MAX_AGE_SECONDS = 26 * 60 * 60
BACKUP_MAX_FUTURE_SKEW_SECONDS = 5 * 60
HEALTH_CHECK_MAX_AGE_SECONDS = 5 * 60
UNATTENDED_WORKER_MAX_FUTURE_SKEW_SECONDS = 5
UNATTENDED_WORKER_COMPLETION_SKEW_SECONDS = 60
UNATTENDED_WORKER_STATE_MAX_BYTES = 64 * 1024
UNATTENDED_WORKER_ENVIRONMENT_MAX_BYTES = 32 * 1024
UNATTENDED_WORKER_TASK_MAX_AGE_SECONDS = {
    "alerts-refresh": 5 * 60,
    "wallets-poll": 10 * 60,
}
ROLLBACK_DRILL_MAX_AGE_SECONDS = 24 * 60 * 60
ROLLBACK_DRILL_MAX_FUTURE_SKEW_SECONDS = 5 * 60
DEFAULT_BACKUP_DIRECTORY = Path("/var/lib/market-sentinel-backups")
DEFAULT_STATE_DIRECTORY = Path("/var/lib/market-sentinel")
DEFAULT_SERVICE_ENVIRONMENT_PATH = Path("/etc/market-sentinel/market-sentinel.env")
DEFAULT_HEALTH_ENVIRONMENT_PATH = Path("/etc/market-sentinel/market-sentinel-health.env")
DEFAULT_WORKER_ENVIRONMENT_PATH = Path("/etc/market-sentinel/market-sentinel-worker.env")
DEFAULT_WORKER_STATE_PATH = DEFAULT_STATE_DIRECTORY / "unattended-worker-state.json"
DEFAULT_WORKER_LOCK_PATH = DEFAULT_STATE_DIRECTORY / ".unattended-worker.lock"
DURABLE_STATE_PATHS = {
    "POLYMARKET_ANALYTICS_CACHE_PATH": DEFAULT_STATE_DIRECTORY / "polymarket_analytics_cache.json",
    "POLYMARKET_LIVE_VALIDATION_REPORTS_PATH": (
        DEFAULT_STATE_DIRECTORY / "polymarket_live_validation_reports.json"
    ),
    "POLYMARKET_LIVE_VALIDATION_DECISIONS_PATH": (
        DEFAULT_STATE_DIRECTORY / "polymarket_live_validation_decisions.json"
    ),
    "POLYMARKET_LIVE_VALIDATION_PROMOTION_PROPOSAL_SNAPSHOTS_PATH": (
        DEFAULT_STATE_DIRECTORY / "polymarket_live_validation_promotion_proposal_snapshots.json"
    ),
}
REQUIRED_WEB_SERVICE_PROPERTIES = {
    "User": "market-sentinel",
    "Group": "market-sentinel",
    "WorkingDirectory": "/opt/market-sentinel",
    "Restart": "on-failure",
    "PermissionsStartOnly": "no",
    "RootDirectoryStartOnly": "no",
    "UMask": "0077",
    "NoNewPrivileges": "yes",
    "PrivateTmp": "yes",
    "PrivateDevices": "yes",
    "ProtectClock": "yes",
    "ProtectHostname": "yes",
    "ProtectSystem": "strict",
    "ProtectHome": "yes",
    "ProtectKernelTunables": "yes",
    "ProtectKernelModules": "yes",
    "ProtectKernelLogs": "yes",
    "ProtectControlGroups": "yes",
    "ProtectProc": "invisible",
    "ProcSubset": "pid",
    "RestrictSUIDSGID": "yes",
    "RestrictRealtime": "yes",
    "RestrictNamespaces": "yes",
    "SystemCallArchitectures": "native",
    "LockPersonality": "yes",
    "MemoryDenyWriteExecute": "yes",
    "MemoryMax": str(1024 * 1024 * 1024),
    "TasksMax": "128",
    "LimitNOFILE": "4096",
}
REQUIRED_WEB_ADDRESS_FAMILIES = frozenset({"AF_UNIX", "AF_INET", "AF_INET6"})
REQUIRED_WEB_EXEC_START_PRE_COMMANDS = (
    (
        "/usr/bin/test",
        "${POLYMARKET_ANALYTICS_CACHE_PATH}",
        "=",
        "/var/lib/market-sentinel/polymarket_analytics_cache.json",
    ),
    (
        "/usr/bin/test",
        "${POLYMARKET_LIVE_VALIDATION_REPORTS_PATH}",
        "=",
        "/var/lib/market-sentinel/polymarket_live_validation_reports.json",
    ),
    (
        "/usr/bin/test",
        "${POLYMARKET_LIVE_VALIDATION_DECISIONS_PATH}",
        "=",
        "/var/lib/market-sentinel/polymarket_live_validation_decisions.json",
    ),
    (
        "/usr/bin/test",
        "${POLYMARKET_LIVE_VALIDATION_PROMOTION_PROPOSAL_SNAPSHOTS_PATH}",
        "=",
        "/var/lib/market-sentinel/polymarket_live_validation_promotion_proposal_snapshots.json",
    ),
    (
        "/opt/market-sentinel/.venv/bin/python",
        "-m",
        "market_sentinel_cli",
        "doctor",
        "--strict",
        "--compact",
        "--config",
        "/var/lib/market-sentinel/config.json",
        "--frontend-dir",
        "/opt/market-sentinel/frontend/dist",
    ),
)
REQUIRED_SYSTEMD_TIMER_CONTRACTS = {
    "market-sentinel-health.timer": {
        "unit": "market-sentinel-health.service",
        "persistent": True,
        "monotonic_schedules": [
            {"base": "OnBootUSec", "offset_usec": 120_000_000},
            {"base": "OnUnitActiveUSec", "offset_usec": 60_000_000},
        ],
        "calendar_schedules": [],
        "accuracy_usec": 10_000_000,
    },
    "market-sentinel-backup.timer": {
        "unit": "market-sentinel-backup.service",
        "persistent": True,
        "monotonic_schedules": [],
        "calendar_schedules": ["daily"],
        "randomized_delay_usec": 900_000_000,
    },
    "market-sentinel-alerts-refresh.timer": {
        "unit": "market-sentinel-alerts-refresh.service",
        "persistent": True,
        "monotonic_schedules": [],
        "calendar_schedules": ["*-*-* *:0/2:15"],
        "accuracy_usec": 10_000_000,
        "randomized_delay_usec": 10_000_000,
    },
    "market-sentinel-wallets-poll.timer": {
        "unit": "market-sentinel-wallets-poll.service",
        "persistent": True,
        "monotonic_schedules": [],
        "calendar_schedules": ["*-*-* *:0/5:45"],
        "accuracy_usec": 10_000_000,
        "randomized_delay_usec": 10_000_000,
    },
}
REQUIRED_HEALTH_SERVICE_PROPERTIES = {
    "Type": "oneshot",
    "User": "market-sentinel-health",
    "Group": "market-sentinel-health",
    "WorkingDirectory": "/opt/market-sentinel",
    "UMask": "0077",
    "NoNewPrivileges": "yes",
    "PrivateTmp": "yes",
    "PrivateDevices": "yes",
    "ProtectClock": "yes",
    "ProtectHostname": "yes",
    "ProtectSystem": "strict",
    "ProtectHome": "yes",
    "ProtectKernelTunables": "yes",
    "ProtectKernelModules": "yes",
    "ProtectKernelLogs": "yes",
    "ProtectControlGroups": "yes",
    "ProtectProc": "invisible",
    "ProcSubset": "pid",
    "RestrictSUIDSGID": "yes",
    "RestrictRealtime": "yes",
    "RestrictNamespaces": "yes",
    "PrivateUsers": "yes",
    "SystemCallArchitectures": "native",
    "LockPersonality": "yes",
    "MemoryDenyWriteExecute": "yes",
    "MemoryMax": str(128 * 1024 * 1024),
    "TasksMax": "32",
    "LimitNOFILE": "256",
}
REQUIRED_HEALTH_ADDRESS_FAMILIES = frozenset({"AF_UNIX", "AF_INET", "AF_INET6"})
REQUIRED_WORKER_ADDRESS_FAMILIES = frozenset({"AF_UNIX", "AF_INET", "AF_INET6"})
REQUIRED_WORKER_SERVICE_PROPERTIES = {
    "Type": "oneshot",
    "User": "market-sentinel",
    "Group": "market-sentinel",
    "WorkingDirectory": "/opt/market-sentinel",
    "StateDirectory": "market-sentinel",
    "StateDirectoryMode": "0700",
    "KillMode": "mixed",
    "UMask": "0077",
    "NoNewPrivileges": "yes",
    "PrivateTmp": "yes",
    "PrivateDevices": "yes",
    "ProtectClock": "yes",
    "ProtectHostname": "yes",
    "ProtectSystem": "strict",
    "ProtectHome": "yes",
    "ProtectKernelTunables": "yes",
    "ProtectKernelModules": "yes",
    "ProtectKernelLogs": "yes",
    "ProtectControlGroups": "yes",
    "ProtectProc": "invisible",
    "ProcSubset": "pid",
    "RestrictSUIDSGID": "yes",
    "RestrictRealtime": "yes",
    "RestrictNamespaces": "yes",
    "SystemCallArchitectures": "native",
    "LockPersonality": "yes",
    "MemoryDenyWriteExecute": "yes",
    "MemoryMax": str(512 * 1024 * 1024),
    "TasksMax": "64",
    "LimitNOFILE": "1024",
}
REQUIRED_WORKER_UNSET_ENVIRONMENT = frozenset(
    {
        "MARKET_SENTINEL_API_TOKEN",
        "MARKET_SENTINEL_OBSERVABILITY_TOKEN",
        "PRIVATE_KEY",
        "POLYMARKET_PRIVATE_KEY",
        "POLY_API_SECRET",
        "POLY_SECRET",
        "POLY_PASSPHRASE",
        "POLY_SIGNATURE",
        "POLY_BUILDER_API_KEY",
        "POLY_BUILDER_SECRET",
        "POLY_BUILDER_PASSPHRASE",
        "POLY_BUILDER_SIGNATURE",
        "RELAYER_API_KEY",
        "RELAYER_API_KEY_ADDRESS",
        "KALSHI_PRIVATE_KEY_PATH",
        "KALSHI_PRIVATE_KEY_PEM",
        "KALSHI_PRIVATE_KEY_PASSWORD",
        "OPINION_PRIVATE_KEY",
        "SX_BET_PRIVATE_KEY",
        "GEMINI_API_SECRET",
        "XO_API_SECRET",
        "PROB_API_SECRET",
        "PROBABLE_API_SECRET",
        "PROB_PASSPHRASE",
        "PROBABLE_API_PASSPHRASE",
        "LIMITLESS_TOKEN_SECRET",
        "PROPHET_EXCHANGE_SECRET_KEY",
        "SSLKEYLOGFILE",
        "PYTHONHOME",
        "PYTHONINSPECT",
        "PYTHONSTARTUP",
        "PYTHONBREAKPOINT",
    }
)
ALLOWED_WORKER_ENVIRONMENT_KEYS = frozenset(
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
REQUIRED_WORKER_EXEC_START_PRE_COMMANDS = (
    ("/usr/bin/test", "-f", "/var/lib/market-sentinel/config.json"),
    ("/usr/bin/test", "!", "-L", "/var/lib/market-sentinel/config.json"),
)
_WORKER_COMMAND_PREFIX = (
    "/opt/market-sentinel/.venv/bin/python",
    "-m",
    "core.unattended_worker",
    "run",
)
_WORKER_COMMAND_SUFFIX = (
    "--config",
    "/var/lib/market-sentinel/config.json",
    "--state-file",
    DEFAULT_WORKER_STATE_PATH.as_posix(),
    "--lock-file",
    DEFAULT_WORKER_LOCK_PATH.as_posix(),
    "--deadline-seconds",
    "90",
    "--attempt-timeout-seconds",
    "40",
    "--lock-timeout-seconds",
    "5",
    "--max-attempts",
    "3",
    "--initial-backoff-seconds",
    "2",
    "--max-backoff-seconds",
    "8",
)
WORKER_SOURCE_REVISION_ENVIRONMENT_KEY = "MARKET_SENTINEL_SOURCE_REVISION"
WORKER_SOURCE_REVISION_TOKEN = "${MARKET_SENTINEL_SOURCE_REVISION}"
WORKER_UNIT_CONTRACT_BINDING_PLACEHOLDER = "0" * 64
_NORMALIZED_WORKER_EXEC_START_COMMANDS = {
    "market-sentinel-alerts-refresh.service": (
        *_WORKER_COMMAND_PREFIX,
        "--task",
        "alerts-refresh",
        "--source-revision",
        WORKER_SOURCE_REVISION_TOKEN,
        "--service-unit",
        "market-sentinel-alerts-refresh.service",
        "--unit-contract-sha256",
        WORKER_UNIT_CONTRACT_BINDING_PLACEHOLDER,
        *_WORKER_COMMAND_SUFFIX,
    ),
    "market-sentinel-wallets-poll.service": (
        *_WORKER_COMMAND_PREFIX,
        "--task",
        "wallets-poll",
        "--source-revision",
        WORKER_SOURCE_REVISION_TOKEN,
        "--service-unit",
        "market-sentinel-wallets-poll.service",
        "--unit-contract-sha256",
        WORKER_UNIT_CONTRACT_BINDING_PLACEHOLDER,
        *_WORKER_COMMAND_SUFFIX,
        "--wallet-limit",
        "25",
    ),
}
UNATTENDED_WORKER_SERVICES = {
    "market-sentinel-alerts-refresh.service": {
        "task": "alerts-refresh",
        "timer": "market-sentinel-alerts-refresh.timer",
    },
    "market-sentinel-wallets-poll.service": {
        "task": "wallets-poll",
        "timer": "market-sentinel-wallets-poll.timer",
    },
}
def _unattended_service_contract(
    service: str,
    exec_start: tuple[str, ...],
) -> dict[str, Any]:
    identity = UNATTENDED_WORKER_SERVICES[service]
    return {
        **identity,
        "exec_start": list(exec_start),
        "exec_start_pre": [list(command) for command in REQUIRED_WORKER_EXEC_START_PRE_COMMANDS],
        "properties": dict(REQUIRED_WORKER_SERVICE_PROPERTIES),
        "address_families": sorted(REQUIRED_WORKER_ADDRESS_FAMILIES),
        "environment_file": DEFAULT_WORKER_ENVIRONMENT_PATH.as_posix(),
        "environment": ["PYTHONUNBUFFERED=1"],
        "unset_environment": sorted(REQUIRED_WORKER_UNSET_ENVIRONMENT),
        "pass_environment": [],
        "read_only_paths": ["/etc/market-sentinel"],
        "read_write_paths": [DEFAULT_STATE_DIRECTORY.as_posix()],
        "capability_bounding_set": [],
        "ambient_capabilities": [],
        "timeout_start_usec": 105_000_000,
        "timeout_stop_usec": 10_000_000,
    }


def _worker_unit_contract_sha256(contract: dict[str, Any]) -> str:
    canonical = json.dumps(contract, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(b"market-sentinel-worker-unit-contract-v1\0" + canonical.encode("ascii")).hexdigest()


def worker_invocation_sha256(
    *,
    task: str,
    service_unit: str,
    source_revision: str,
    unit_contract_sha256: str,
) -> str:
    """Independently derive the worker's revision-and-unit invocation binding."""

    payload = {
        "schema_version": 1,
        "service_unit": service_unit,
        "source_revision": source_revision,
        "task": task,
        "unit_contract_sha256": unit_contract_sha256,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(b"market-sentinel-worker-invocation-v1\0" + canonical.encode("ascii")).hexdigest()


_NORMALIZED_UNATTENDED_SERVICE_CONTRACTS = {
    service: _unattended_service_contract(service, _NORMALIZED_WORKER_EXEC_START_COMMANDS[service])
    for service in UNATTENDED_WORKER_SERVICES
}
REQUIRED_WORKER_UNIT_CONTRACT_SHA256 = {
    service: _worker_unit_contract_sha256(contract)
    for service, contract in _NORMALIZED_UNATTENDED_SERVICE_CONTRACTS.items()
}
REQUIRED_WORKER_EXEC_START_COMMANDS = {
    service: tuple(
        REQUIRED_WORKER_UNIT_CONTRACT_SHA256[service]
        if token == WORKER_UNIT_CONTRACT_BINDING_PLACEHOLDER
        else token
        for token in command
    )
    for service, command in _NORMALIZED_WORKER_EXEC_START_COMMANDS.items()
}
REQUIRED_UNATTENDED_SERVICE_CONTRACTS = {
    service: _unattended_service_contract(service, REQUIRED_WORKER_EXEC_START_COMMANDS[service])
    for service in UNATTENDED_WORKER_SERVICES
}
EVIDENCE_SCHEMA_VERSION = 1
COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")
PROVIDER_SLUG = re.compile(r"^[a-z0-9][a-z0-9._-]{1,63}$")
EXTERNAL_PROBE_REPORT_TYPE = "market-sentinel-external-deployment-probe"
ROLLBACK_DRILL_REPORT_TYPE = "market-sentinel-production-rollback-drill"
ROLLBACK_DRILL_STEPS = (
    "current_release_healthy",
    "rollback_release_activated",
    "rollback_release_healthy",
    "current_release_reactivated",
    "current_release_healthy_after_reactivation",
)
REQUIRED_PRIVATE_PATHS = (
    (DEFAULT_SERVICE_ENVIRONMENT_PATH, S_IFREG, True),
    (DEFAULT_HEALTH_ENVIRONMENT_PATH, S_IFREG, True),
    (DEFAULT_WORKER_ENVIRONMENT_PATH, S_IFREG, True),
    (DEFAULT_STATE_DIRECTORY, S_IFDIR, False),
)
PUBLIC_PROXY_AUTH_PROBES = (
    ("GET", ""),
    ("GET", "api/health"),
    ("GET", "api/state"),
    ("GET", "metrics"),
    ("PATCH", "api/config"),
)


def _validated_public_origin(value: str, timeout: float = 10.0) -> str:
    with request_scope(timeout):
        origin = canonical_https_origin(value)
        parsed = urlparse(origin)
        try:
            address = ipaddress.ip_address(parsed.hostname)
        except ValueError:
            address = None
        if address is not None and not is_public_probe_address(str(address)):
            raise ValueError("public URL must use a public unicast address")
        if address is None:
            try:
                resolved = {
                    item[4][0]
                    for item in resolve_with_deadline(
                        socket.getaddrinfo, parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM
                    )
                }
            except OSError as exc:
                raise ValueError("public URL hostname could not be resolved safely") from exc
            if not resolved or any(not is_public_probe_address(item) for item in resolved):
                raise ValueError("public URL must resolve exclusively to public unicast addresses")
    return origin


def _run_command(args: list[str]) -> subprocess.CompletedProcess[str]:
    environment = {**os.environ, "LC_ALL": "C", "TZ": "UTC"}
    return subprocess.run(args, capture_output=True, text=True, check=False, timeout=15, env=environment)


def source_identity(root: Path = PROJECT_ROOT) -> dict[str, str]:
    """Return minimal source provenance without retaining command output."""
    project_version = "unknown"
    try:
        data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        candidate = data.get("project", {}).get("version", "")
        if isinstance(candidate, str) and candidate.strip():
            project_version = candidate.strip()
    except (OSError, TypeError, ValueError):
        pass

    revision = ""
    revision_status = "unavailable"
    worktree_status = "unavailable"
    try:
        trusted_root = root.resolve(strict=True)
        environment = safe_git_environment()
        top_level_result = subprocess.run(
            safe_git_command(trusted_root, "rev-parse", "--show-toplevel"),
            cwd=trusted_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
            env=environment,
        )
        if top_level_result.returncode == 0 and git_top_level_matches(trusted_root, top_level_result.stdout):
            before_result = subprocess.run(
                safe_git_command(trusted_root, "rev-parse", "--verify", "HEAD^{commit}"),
                cwd=trusted_root,
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
                env=environment,
            )
            before = before_result.stdout.strip().lower()
            before_valid = before_result.returncode == 0 and COMMIT_SHA.fullmatch(before)
        else:
            revision_status = "invalid"
            before = ""
            before_valid = False
        if before_valid:
            status_result = subprocess.run(
                safe_git_command(trusted_root, "status", "--porcelain=v1", "--untracked-files=all"),
                cwd=trusted_root,
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
                env=environment,
            )
            if status_result.returncode == 0:
                after_result = subprocess.run(
                    safe_git_command(trusted_root, "rev-parse", "--verify", "HEAD^{commit}"),
                    cwd=trusted_root,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=10,
                    env=environment,
                )
                after = after_result.stdout.strip().lower()
                if after_result.returncode == 0 and COMMIT_SHA.fullmatch(after) and before == after:
                    revision = before
                    revision_status = "ok"
                    worktree_status = "clean" if not status_result.stdout.strip() else "dirty"
                else:
                    revision_status = "invalid"
    except (OSError, RuntimeError, subprocess.TimeoutExpired):
        pass

    return {
        "project_version": project_version,
        "git_revision": revision,
        "git_revision_status": revision_status,
        "git_worktree_status": worktree_status,
    }


def check_source_revision(
    expected_revision: str,
    source: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Require deployment evidence to match the intended release commit."""
    expected = expected_revision.strip().lower()
    if not COMMIT_SHA.fullmatch(expected):
        return {
            "name": "source_revision",
            "status": "fail",
            "detail": "--expected-source-revision must be a lowercase 40-character Git commit",
        }
    identity = source if source is not None else source_identity()
    revision_status = identity.get("git_revision_status", "unavailable").strip().lower()
    if revision_status != "ok":
        return {
            "name": "source_revision",
            "status": "fail",
            "detail": f"deployed Git revision identity is {revision_status or 'unavailable'}",
        }
    actual = identity.get("git_revision", "").strip().lower()
    if actual != expected:
        return {
            "name": "source_revision",
            "status": "fail",
            "detail": f"deployed Git revision is {actual or 'unavailable'}, expected {expected}",
        }
    worktree_status = identity.get("git_worktree_status", "unavailable").strip().lower()
    if worktree_status != "clean":
        return {
            "name": "source_revision",
            "status": "fail",
            "detail": (
                "deployed source checkout is not clean "
                f"(git_worktree_status={worktree_status or 'unavailable'}); "
                "tracked, staged, and untracked source changes are not release evidence"
            ),
        }
    return {
        "name": "source_revision",
        "status": "pass",
        "detail": f"deployed Git revision matches {expected} and the checkout is clean",
    }


def _systemd_timestamp_seconds(value: str) -> float:
    normalized = value.strip()
    for pattern in ("%a %Y-%m-%d %H:%M:%S UTC", "%a %Y-%m-%d %H:%M:%S.%f UTC"):
        try:
            return datetime.strptime(normalized, pattern).replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            continue
    raise ValueError(f"invalid systemd UTC timestamp: {normalized or 'missing'}")


def check_filesystem_permissions(
    stat_reader: Callable[[Path], object] = lambda path: path.lstat(),
    *,
    backup_directory: Path = DEFAULT_BACKUP_DIRECTORY,
) -> list[dict[str, Any]]:
    required_paths = (
        *REQUIRED_PRIVATE_PATHS,
        (Path(backup_directory), S_IFDIR, False),
    )
    return [
        _check_private_path(path, expected_type, require_root_owner, stat_reader)
        for path, expected_type, require_root_owner in required_paths
    ]


def _systemd_property(
    runner: CommandRunner,
    unit: str,
    property_name: str,
    *,
    allow_empty: bool = False,
) -> str:
    result = runner(["systemctl", "show", unit, f"--property={property_name}", "--value"])
    if result.returncode != 0:
        raise RuntimeError(f"systemd could not read {property_name} for {unit}")
    value = result.stdout.strip()
    if not value and not allow_empty:
        raise RuntimeError(f"systemd returned an empty {property_name} for {unit}")
    return value


_SYSTEMD_EXEC_EX_SIGNATURE = "a(sasasttttuii)"
_SYSTEMD_WEB_SERVICE_OBJECT = (
    "/org/freedesktop/systemd1/unit/market_2dsentinel_2dweb_2eservice"
)
_SYSTEMD_WORKER_SERVICE_OBJECTS = {
    "market-sentinel-alerts-refresh.service": (
        "/org/freedesktop/systemd1/unit/market_2dsentinel_2dalerts_2drefresh_2eservice"
    ),
    "market-sentinel-wallets-poll.service": (
        "/org/freedesktop/systemd1/unit/market_2dsentinel_2dwallets_2dpoll_2eservice"
    ),
}
_SYSTEMD_DURATION_TOKEN = re.compile(r"(?P<value>[0-9]+)(?P<unit>min|ms|us|s|h|d|w)")
_SYSTEMD_DURATION_MULTIPLIERS = {
    "us": 1,
    "ms": 1_000,
    "s": 1_000_000,
    "min": 60_000_000,
    "h": 3_600_000_000,
    "d": 86_400_000_000,
    "w": 604_800_000_000,
}


def _parse_systemd_exec_commands(value: str) -> tuple[tuple[str, ...], ...]:
    """Parse busctl's typed ExecStartPreEx value without losing argv boundaries."""
    if not value or len(value) > 64 * 1024:
        raise ValueError("invalid systemd command-array size")
    try:
        tokens = shlex.split(value, posix=True)
    except ValueError as exc:
        raise ValueError("invalid quoting in systemd command array") from exc
    position = 0

    def take_token(label: str) -> str:
        nonlocal position
        if position >= len(tokens):
            raise ValueError(f"systemd command array is missing {label}")
        token = tokens[position]
        position += 1
        return token

    def take_count(label: str, *, maximum: int) -> int:
        token = take_token(label)
        if re.fullmatch(r"[0-9]+", token) is None:
            raise ValueError(f"systemd command array has invalid {label}")
        count = int(token)
        if count > maximum:
            raise ValueError(f"systemd command array exceeds the {label} limit")
        return count

    if take_token("D-Bus signature") != _SYSTEMD_EXEC_EX_SIGNATURE:
        raise ValueError("systemd returned an unexpected ExecStartPreEx signature")
    command_count = take_count("command count", maximum=32)
    if command_count == 0:
        raise ValueError("systemd returned an empty command array")
    commands: list[tuple[str, ...]] = []
    for _ in range(command_count):
        path = take_token("command path")
        argument_count = take_count("argument count", maximum=128)
        argv = tuple(take_token("command argument") for _ in range(argument_count))
        flag_count = take_count("execution flag count", maximum=16)
        flags = tuple(take_token("execution flag") for _ in range(flag_count))
        runtime_values = tuple(
            take_count("runtime value", maximum=(1 << 64) - 1)
            for _ in range(7)
        )
        start_realtime, start_monotonic, stop_realtime, stop_monotonic, _pid, code, status = (
            runtime_values
        )
        if not argv or argv[0] != path or flags:
            raise ValueError("systemd command path or execution flags are not exact")
        if (
            code != 1
            or status != 0
            or min(start_realtime, start_monotonic, stop_realtime, stop_monotonic) <= 0
        ):
            raise ValueError("systemd command does not attest a completed successful execution")
        commands.append(argv)
    if position != len(tokens):
        raise ValueError("systemd command array has unexpected trailing data")
    return tuple(commands)


def check_web_startup_preflight(runner: CommandRunner = _run_command) -> dict[str, Any]:
    """Prove the installed web unit retains every ordered fail-closed startup guard."""
    base = {
        "name": "web_startup_preflight",
        "expected_command_count": len(REQUIRED_WEB_EXEC_START_PRE_COMMANDS),
    }
    try:
        result = runner(
            [
                "busctl",
                "--system",
                "get-property",
                "org.freedesktop.systemd1",
                _SYSTEMD_WEB_SERVICE_OBJECT,
                "org.freedesktop.systemd1.Service",
                "ExecStartPreEx",
            ]
        )
        if result.returncode != 0:
            raise RuntimeError("systemd could not read structured ExecStartPreEx for the web service")
        commands = _parse_systemd_exec_commands(result.stdout.strip())
        if commands != REQUIRED_WEB_EXEC_START_PRE_COMMANDS:
            raise RuntimeError("effective web ExecStartPre commands do not match the reviewed ordered contract")
    except (RuntimeError, ValueError) as exc:
        return {**base, "status": "fail", "detail": str(exc)}
    return {
        **base,
        "status": "pass",
        "detail": "all ordered durable-path guards and the strict doctor command are fail closed",
        "command_count": len(commands),
        "commands": [list(command) for command in commands],
        "commands_succeeded": True,
    }


def _systemd_duration_usec(value: str) -> int:
    normalized = value.strip()
    if normalized == "0":
        return 0
    total = 0
    cursor = 0
    matched = False
    for match in _SYSTEMD_DURATION_TOKEN.finditer(normalized):
        if normalized[cursor : match.start()].strip():
            raise ValueError("invalid systemd duration")
        total += int(match.group("value")) * _SYSTEMD_DURATION_MULTIPLIERS[match.group("unit")]
        cursor = match.end()
        matched = True
    if not matched or normalized[cursor:].strip():
        raise ValueError("invalid systemd duration")
    return total


def _parse_systemd_timer_schedules(
    value: str,
    *,
    calendar: bool,
) -> list[dict[str, Any]] | list[str]:
    if not value.strip():
        return []
    schedules: list[Any] = []
    cursor = 0
    for match in re.finditer(r"\{\s*(?P<body>[^{}]*)\}", value):
        if value[cursor : match.start()].strip():
            raise ValueError("unexpected data in systemd timer schedule array")
        cursor = match.end()
        fields: dict[str, str] = {}
        for raw_field in match.group("body").split(";"):
            field = raw_field.strip()
            if not field:
                continue
            name, separator, field_value = field.partition("=")
            if not separator or name.strip() in fields:
                raise ValueError("invalid systemd timer schedule field")
            fields[name.strip()] = field_value.strip()
        if calendar:
            if "OnCalendar" not in fields:
                raise ValueError("systemd calendar schedule is missing OnCalendar")
            spec = " ".join(fields["OnCalendar"].split())
            if spec in {"daily", "*-*-* 00:00:00"}:
                spec = "daily"
            # systemd versions disagree on whether the first minute in a
            # stepped calendar expression is rendered as 0 or 00.
            spec = re.sub(r"(?<=:)00/([1-9][0-9]*)(?=:)", r"0/\1", spec)
            schedules.append(spec)
            continue
        bases = [name for name in fields if name.startswith("On") and name.endswith(("Sec", "USec"))]
        if len(bases) != 1:
            raise ValueError("systemd monotonic schedule does not contain exactly one trigger")
        base = bases[0]
        if base.endswith("Sec") and not base.endswith("USec"):
            base = base[:-3] + "USec"
        schedules.append({"base": base, "offset_usec": _systemd_duration_usec(fields[bases[0]])})
    if value[cursor:].strip():
        raise ValueError("unexpected trailing data in systemd timer schedule array")
    if calendar:
        return sorted(schedules)
    return sorted(schedules, key=lambda schedule: (schedule["base"], schedule["offset_usec"]))


def check_systemd_timer_contracts(runner: CommandRunner = _run_command) -> dict[str, Any]:
    """Attest the effective service target and schedule for each production timer."""
    base = {
        "name": "systemd_timer_contracts",
        "expected_timer_count": len(REQUIRED_SYSTEMD_TIMER_CONTRACTS),
    }
    observed: dict[str, dict[str, Any]] = {}
    try:
        for timer, expected in REQUIRED_SYSTEMD_TIMER_CONTRACTS.items():
            persistent = _systemd_property(runner, timer, "Persistent")
            if persistent not in {"yes", "no"}:
                raise RuntimeError(f"systemd returned an invalid Persistent value for {timer}")
            contract: dict[str, Any] = {
                "unit": _systemd_property(runner, timer, "Unit"),
                "persistent": persistent == "yes",
                "monotonic_schedules": _parse_systemd_timer_schedules(
                    _systemd_property(runner, timer, "TimersMonotonic", allow_empty=True),
                    calendar=False,
                ),
                "calendar_schedules": _parse_systemd_timer_schedules(
                    _systemd_property(runner, timer, "TimersCalendar", allow_empty=True),
                    calendar=True,
                ),
            }
            if "accuracy_usec" in expected:
                contract["accuracy_usec"] = _systemd_duration_usec(
                    _systemd_property(runner, timer, "AccuracyUSec")
                )
            if "randomized_delay_usec" in expected:
                contract["randomized_delay_usec"] = _systemd_duration_usec(
                    _systemd_property(runner, timer, "RandomizedDelayUSec")
                )
            if contract != expected:
                raise RuntimeError(f"effective timer contract does not match the reviewed schedule for {timer}")
            observed[timer] = contract
    except (RuntimeError, ValueError) as exc:
        return {**base, "status": "fail", "detail": str(exc)}
    return {
        **base,
        "status": "pass",
        "detail": "all production timers have the exact reviewed effective targets and schedules",
        "timer_count": len(observed),
        "timers": observed,
    }


def _contains_path_token(value: str, path: Path) -> bool:
    path_text = re.escape(path.as_posix())
    return re.search(rf"(?<![A-Za-z0-9_./-]){path_text}(?![A-Za-z0-9_./-])", value) is not None


def _contains_command_option(value: str, option: str, expected: str) -> bool:
    return re.search(
        rf"(?:^|[\s;]){re.escape(option)}(?:=|\s+)[\"']?{re.escape(expected)}"
        r"[\"']?(?=$|[\s;}])",
        value,
    ) is not None


def _strong_observability_token(token: str) -> bool:
    try:
        encoded = token.encode("ascii")
    except UnicodeEncodeError:
        return False
    if not 32 <= len(encoded) <= 512 or re.fullmatch(r"[A-Za-z0-9._~+/-]+=*", token) is None:
        return False
    token_body = token.rstrip("=")
    collapsed = re.sub(r"[^a-z0-9]", "", token_body.lower())
    weak_markers = ("apitoken", "changeme", "exampletoken", "password", "replaceme", "testtoken")
    repeated = any(
        len(token_body) % width == 0 and token_body == token_body[:width] * (len(token_body) // width)
        for width in range(1, min(16, len(token_body) // 2) + 1)
    )
    return len(set(token_body)) >= 10 and not repeated and not any(marker in collapsed for marker in weak_markers)


def check_health_credential_isolation(
    runner: CommandRunner = _run_command,
    environment_reader: Callable[[Path], bytes] = lambda path: path.read_bytes(),
) -> dict[str, Any]:
    """Prove the periodic health process receives only its scoped credential."""
    base = {
        "name": "health_credential_isolation",
        "environment_path": DEFAULT_HEALTH_ENVIRONMENT_PATH.as_posix(),
    }
    try:
        for property_name, expected_value in REQUIRED_HEALTH_SERVICE_PROPERTIES.items():
            actual_value = _systemd_property(
                runner,
                "market-sentinel-health.service",
                property_name,
            )
            if actual_value != expected_value:
                raise RuntimeError(
                    f"the health service has unsafe effective {property_name}={actual_value!r}; "
                    f"expected {expected_value!r}"
                )
        address_families = frozenset(
            _systemd_property(
                runner,
                "market-sentinel-health.service",
                "RestrictAddressFamilies",
            ).split()
        )
        if address_families != REQUIRED_HEALTH_ADDRESS_FAMILIES:
            raise RuntimeError("the health service effective RestrictAddressFamilies is not reviewed")

        environment_files = _systemd_property(runner, "market-sentinel-health.service", "EnvironmentFiles")
        required_environment = re.compile(
            rf"^{re.escape(DEFAULT_HEALTH_ENVIRONMENT_PATH.as_posix())}\s+\(ignore_errors=no\)$"
        )
        if required_environment.fullmatch(environment_files) is None:
            raise RuntimeError("the health service does not exclusively load its required credential file")

        unset_environment = set(
            _systemd_property(runner, "market-sentinel-health.service", "UnsetEnvironment").split()
        )
        if "MARKET_SENTINEL_API_TOKEN" not in unset_environment:
            raise RuntimeError("the health service does not remove the admin API token from its environment")
        if "MARKET_SENTINEL_OBSERVABILITY_TOKEN" in unset_environment:
            raise RuntimeError("the health service removes its required observability token")

        for property_name in ("Environment", "PassEnvironment"):
            result = runner(
                ["systemctl", "show", "market-sentinel-health.service", f"--property={property_name}", "--value"]
            )
            if result.returncode != 0:
                raise RuntimeError(f"systemd could not read {property_name} for market-sentinel-health.service")
            if result.stdout.strip():
                raise RuntimeError(
                    f"the health service has unexpected effective {property_name} credential sources"
                )

        preflight = runner(
            ["systemctl", "show", "market-sentinel-health.service", "--property=ExecStartPre", "--value"]
        )
        if preflight.returncode != 0:
            raise RuntimeError("systemd could not read ExecStartPre for market-sentinel-health.service")
        if preflight.stdout.strip():
            raise RuntimeError("the health service exposes its observer token through a pre-start command")

        command = _systemd_property(runner, "market-sentinel-health.service", "ExecStart")
        if not _contains_path_token(command, Path("/opt/market-sentinel/.venv/bin/python")):
            raise RuntimeError("the health service does not use the reviewed runtime interpreter")
        if not _contains_path_token(
            command,
            Path("/opt/market-sentinel/scripts/verify_service_health.py"),
        ):
            raise RuntimeError("the health service does not execute the reviewed health probe")
        if any(_contains_path_token(command, path) for path in (Path("/bin/sh"), Path("/bin/bash"), Path("/usr/bin/env"))):
            raise RuntimeError("the health service wraps its probe in an unreviewed command interpreter")
        if re.search(r"(?:^|[\s;])--require-observability-token(?=$|[\s;}])", command) is None:
            raise RuntimeError("the health probe is not in fail-closed observability-only mode")
        if re.search(r"(?:^|[\s;])--token(?:=|\s+)", command) is not None:
            raise RuntimeError("the health service exposes a bearer token in its command line")

        web_post_start = runner(
            ["systemctl", "show", "market-sentinel-web.service", "--property=ExecStartPost", "--value"]
        )
        if web_post_start.returncode != 0:
            raise RuntimeError("systemd could not read ExecStartPost for market-sentinel-web.service")
        if web_post_start.stdout.strip():
            raise RuntimeError("the web service still launches a health probe with its privileged environment")

        raw_environment = environment_reader(DEFAULT_HEALTH_ENVIRONMENT_PATH)
        if len(raw_environment) > 8 * 1024:
            raise RuntimeError("the health credential file exceeds the verifier safety limit")
        try:
            environment_text = raw_environment.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise RuntimeError("the health credential file is not UTF-8") from exc
        assignments: dict[str, str] = {}
        for raw_line in environment_text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            name, separator, value = line.partition("=")
            if not separator or re.fullmatch(r"[A-Z][A-Z0-9_]*", name) is None:
                raise RuntimeError("the health credential file contains an invalid assignment")
            if name in assignments:
                raise RuntimeError("the health credential file contains a duplicate assignment")
            assignments[name] = value
        if set(assignments) != {"MARKET_SENTINEL_OBSERVABILITY_TOKEN"}:
            raise RuntimeError("the health credential file must contain only the observability token")
        if not _strong_observability_token(assignments["MARKET_SENTINEL_OBSERVABILITY_TOKEN"]):
            raise RuntimeError("the health credential file does not contain a strong observability token")
    except (OSError, RuntimeError, ValueError) as exc:
        return {**base, "status": "fail", "detail": str(exc)}

    return {
        **base,
        "status": "pass",
        "detail": "health probe has a private observability-only environment and fail-closed command",
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


def _read_process_environment(pid: int) -> bytes:
    return Path(f"/proc/{pid}/environ").read_bytes()


def check_durable_state_wiring(
    runner: CommandRunner = _run_command,
    process_environment_reader: Callable[[int], bytes] = _read_process_environment,
) -> dict[str, Any]:
    """Prove the running service stores every durable artifact inside the backed-up state root."""
    base = {
        "name": "durable_state_wiring",
        "state_directory": DEFAULT_STATE_DIRECTORY.as_posix(),
        "backup_source": DEFAULT_STATE_DIRECTORY.as_posix(),
        "durable_store_count": len(DURABLE_STATE_PATHS),
        "verified_service_property_count": len(REQUIRED_WEB_SERVICE_PROPERTIES) + 2,
    }
    try:
        environment_files = _systemd_property(runner, "market-sentinel-web.service", "EnvironmentFiles")
        if not _contains_path_token(environment_files, DEFAULT_SERVICE_ENVIRONMENT_PATH):
            raise RuntimeError("the web service does not load the protected service environment file")
        required_environment_pattern = re.compile(
            rf"(?<![A-Za-z0-9_./-]){re.escape(DEFAULT_SERVICE_ENVIRONMENT_PATH.as_posix())}"
            r"\s+\(ignore_errors=no\)(?!\S)"
        )
        if required_environment_pattern.search(environment_files) is None:
            raise RuntimeError("the web service environment file is optional instead of fail-fast")

        for property_name, expected_value in REQUIRED_WEB_SERVICE_PROPERTIES.items():
            actual_value = _systemd_property(
                runner,
                "market-sentinel-web.service",
                property_name,
            )
            if actual_value != expected_value:
                raise RuntimeError(
                    f"the web service has unsafe effective {property_name}={actual_value!r}; "
                    f"expected {expected_value!r}"
                )

        address_families = frozenset(
            _systemd_property(
                runner,
                "market-sentinel-web.service",
                "RestrictAddressFamilies",
            ).split()
        )
        if address_families != REQUIRED_WEB_ADDRESS_FAMILIES:
            raise RuntimeError(
                "the web service effective RestrictAddressFamilies is not the reviewed minimal set"
            )

        web_command = _systemd_property(runner, "market-sentinel-web.service", "ExecStart")
        required_web_options = {
            "--host": "127.0.0.1",
            "--port": "8765",
            "--config": "/var/lib/market-sentinel/config.json",
            "--frontend-dir": "/opt/market-sentinel/frontend/dist",
        }
        if re.search(r"(?:^|[\s;])-m\s+web_api(?=$|[\s;}])", web_command) is None:
            raise RuntimeError("the web service does not execute the reviewed web_api module")
        for option, expected_value in required_web_options.items():
            if not _contains_command_option(web_command, option, expected_value):
                raise RuntimeError(
                    f"the web service effective ExecStart does not contain reviewed {option} {expected_value}"
                )

        writable_paths = _systemd_property(runner, "market-sentinel-web.service", "ReadWritePaths")
        if not _contains_path_token(writable_paths, DEFAULT_STATE_DIRECTORY):
            raise RuntimeError("the durable state directory is not writable in the web service sandbox")

        backup_command = _systemd_property(runner, "market-sentinel-backup.service", "ExecStart")
        backup_source_pattern = re.compile(
            rf"(?:^|[\s;])--source(?:=|\s+)[\"']?{re.escape(DEFAULT_STATE_DIRECTORY.as_posix())}"
            rf"[\"']?(?=$|[\s;}}])"
        )
        if backup_source_pattern.search(backup_command) is None:
            raise RuntimeError("the backup service does not capture the durable state directory")

        raw_pid = _systemd_property(runner, "market-sentinel-web.service", "MainPID")
        try:
            pid = int(raw_pid)
        except ValueError as exc:
            raise RuntimeError("the web service MainPID is invalid") from exc
        if pid <= 0:
            raise RuntimeError("the web service is not running")

        raw_environment = process_environment_reader(pid)
        if len(raw_environment) > 1024 * 1024:
            raise RuntimeError("the web service environment exceeds the verifier safety limit")
        observed: dict[str, str] = {}
        required_names = {name.encode("ascii"): name for name in DURABLE_STATE_PATHS}
        for entry in raw_environment.split(b"\0"):
            key, separator, value = entry.partition(b"=")
            name = required_names.get(key)
            if not separator or name is None:
                continue
            if name in observed:
                raise RuntimeError(f"the running service has duplicate {name} entries")
            try:
                observed[name] = value.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise RuntimeError(f"the running service has a non-UTF-8 {name} value") from exc

        for name, expected_path in DURABLE_STATE_PATHS.items():
            actual = observed.get(name)
            if actual is None:
                raise RuntimeError(f"the running service is missing {name}")
            if actual != expected_path.as_posix():
                raise RuntimeError(f"the running service has an unsafe {name} value")
            if not expected_path.is_relative_to(DEFAULT_STATE_DIRECTORY):
                raise RuntimeError(f"the expected {name} path is outside the durable state directory")
    except (OSError, RuntimeError, ValueError) as exc:
        return {**base, "status": "fail", "detail": str(exc)}

    return {
        **base,
        "status": "pass",
        "detail": (
            "running service identity, command, sandbox, resource limits, and durable paths are exact "
            "and beneath the effective backup source"
        ),
    }


def _check_private_path(
    path: Path,
    expected_type: int,
    require_root_owner: bool,
    stat_reader: Callable[[Path], object],
) -> dict[str, Any]:
    try:
        metadata = stat_reader(path)
        mode = int(metadata.st_mode)
        owner = int(metadata.st_uid)
        valid_type = S_ISREG(mode) if expected_type == S_IFREG else S_ISDIR(mode)
        private = S_IMODE(mode) & 0o077 == 0
        owner_valid = not require_root_owner or owner == 0
        passed = valid_type and private and owner_valid
        detail = f"mode={S_IMODE(mode):04o}; uid={owner}; expected={'file' if expected_type == S_IFREG else 'directory'}"
    except OSError as exc:
        passed = False
        detail = str(exc)
    return {
        "name": f"filesystem_private_{path.name}",
        "status": "pass" if passed else "fail",
        "detail": detail,
    }


def check_evidence_output_directory(
    output_path: Path,
    stat_reader: Callable[[Path], object] = lambda path: path.stat(),
) -> dict[str, Any]:
    """Require output evidence to live in a private, root-owned existing directory."""
    parent = output_path.parent
    symlinked_component = next((path for path in (parent, *parent.parents) if path.is_symlink()), None)
    if symlinked_component is not None:
        return {
            "name": f"filesystem_private_{parent.name}",
            "status": "fail",
            "detail": f"refusing symbolic-link evidence path component: {symlinked_component}",
        }
    result = _check_private_path(parent, S_IFDIR, True, stat_reader)
    # Keep the evidence contract independent of the operator-selected output
    # directory name.  A stable check identifier lets the offline reviewer
    # require an exact, duplicate-free check inventory.
    result["name"] = "evidence_output_directory"
    return result


def _recent_systemd_success(
    unit: str,
    *,
    maximum_age_seconds: float,
    runner: CommandRunner,
    clock: Callable[[], float],
) -> dict[str, Any]:
    completion = runner(
        [
            "systemctl",
            "show",
            unit,
            "--property=Result",
            "--property=ExecMainStatus",
            "--property=ExecMainExitTimestamp",
            "--value",
        ]
    )
    values = [value.strip() for value in completion.stdout.splitlines()]
    result, exit_status, completed_at = (values + ["", "", ""])[:3]
    completed_at_unix_seconds: float | None = None
    try:
        completed_at_unix_seconds = _systemd_timestamp_seconds(completed_at)
        age_seconds = clock() - completed_at_unix_seconds
    except ValueError:
        age_seconds = float("inf")
    completed = (
        completion.returncode == 0
        and result == "success"
        and exit_status == "0"
        and completed_at not in {"", "n/a"}
        and age_seconds >= -BACKUP_MAX_FUTURE_SKEW_SECONDS
        and age_seconds <= maximum_age_seconds
    )
    return {
        "name": f"systemd_recent_success_{unit}",
        "status": "pass" if completed else "fail",
        "detail": (
            f"result={result or 'unknown'}; exit_status={exit_status or 'unknown'}; "
            f"completed_at={completed_at or 'unknown'}; age_seconds={age_seconds:.0f}; "
            f"max_age_seconds={maximum_age_seconds}; "
            f"max_future_skew_seconds={BACKUP_MAX_FUTURE_SKEW_SECONDS}"
        ),
        "unit": unit,
        "completed_at": completed_at,
        "completed_at_unix_seconds": completed_at_unix_seconds,
        "age_seconds": age_seconds if completed_at_unix_seconds is not None else None,
        "max_age_seconds": maximum_age_seconds,
    }


def check_systemd(
    runner: CommandRunner = _run_command,
    clock: Callable[[], float] = time.time,
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for unit in REQUIRED_UNITS:
        for command in ("is-active", "is-enabled"):
            result = runner(["systemctl", command, unit])
            expected_state = "active" if command == "is-active" else "enabled"
            reported_state = result.stdout.strip()
            checks.append(
                {
                    "name": f"systemd_{command}_{unit}",
                    "status": (
                        "pass"
                        if result.returncode == 0 and reported_state == expected_state
                        else "fail"
                    ),
                    "detail": (result.stdout or result.stderr).strip(),
                }
            )
    checks.append(check_systemd_timer_contracts(runner))
    checks.append(
        _recent_systemd_success(
            "market-sentinel-health.service",
            maximum_age_seconds=HEALTH_CHECK_MAX_AGE_SECONDS,
            runner=runner,
            clock=clock,
        )
    )
    checks.append(
        _recent_systemd_success(
            "market-sentinel-backup.service",
            maximum_age_seconds=BACKUP_MAX_AGE_SECONDS,
            runner=runner,
            clock=clock,
        )
    )
    for service, identity in UNATTENDED_WORKER_SERVICES.items():
        checks.append(
            _recent_systemd_success(
                service,
                maximum_age_seconds=UNATTENDED_WORKER_TASK_MAX_AGE_SECONDS[identity["task"]],
                runner=runner,
                clock=clock,
            )
        )
    return checks


def _read_private_regular_file(
    path: Path,
    *,
    maximum_bytes: int,
    require_root_owner: bool,
    allow_empty: bool = False,
) -> tuple[bytes, object]:
    """Read one bounded file while rejecting links and replacement races."""
    before = path.lstat()
    if not S_ISREG(before.st_mode):
        raise ValueError(f"{path.name} must be a regular, non-symbolic-link file")
    if os.name == "posix" and S_IMODE(before.st_mode) & 0o077:
        raise ValueError(f"{path.name} must not grant group or other permissions")
    if os.name == "posix" and require_root_owner and getattr(before, "st_uid", -1) != 0:
        raise ValueError(f"{path.name} must be root-owned")
    descriptor = -1
    try:
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = -1
            opened = os.fstat(handle.fileno())
            if not S_ISREG(opened.st_mode):
                raise ValueError(f"{path.name} must be a regular file")
            if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
                raise ValueError(f"{path.name} changed identity while opening")
            raw = handle.read(maximum_bytes + 1)
            after = os.fstat(handle.fileno())
    except OSError as exc:
        raise ValueError(f"{path.name} could not be read safely") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if (not raw and not allow_empty) or len(raw) > maximum_bytes:
        raise ValueError(f"{path.name} is empty or exceeds its safety limit")
    final = path.lstat()
    if (
        (opened.st_dev, opened.st_ino) != (after.st_dev, after.st_ino)
        or (opened.st_dev, opened.st_ino) != (final.st_dev, final.st_ino)
        or not S_ISREG(final.st_mode)
        or opened.st_size != after.st_size
        or opened.st_mtime_ns != after.st_mtime_ns
    ):
        raise ValueError(f"{path.name} changed while it was being read")
    if os.name == "posix" and (S_IMODE(after.st_mode) & 0o077 or S_IMODE(final.st_mode) & 0o077):
        raise ValueError(f"{path.name} must not grant group or other permissions")
    if os.name == "posix" and require_root_owner and (
        getattr(after, "st_uid", -1) != 0 or getattr(final, "st_uid", -1) != 0
    ):
        raise ValueError(f"{path.name} must be root-owned")
    return raw, before


def _strict_json_bytes(raw: bytes, label: str) -> dict[str, Any]:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"{label} contains a duplicate JSON key")
            result[key] = value
        return result

    def reject_constant(_value: str) -> None:
        raise ValueError(f"{label} contains a non-finite JSON number")

    try:
        decoded = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=reject_duplicates,
            parse_constant=reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} must be strict UTF-8 JSON") from exc
    if not isinstance(decoded, dict):
        raise ValueError(f"{label} root must be a JSON object")
    return decoded


def _worker_environment_keys(raw: bytes, *, expected_revision: str) -> list[str]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("worker environment file must be UTF-8") from exc
    if "\x00" in text:
        raise ValueError("worker environment file contains a NUL byte")
    assignments: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if len(raw_line) > 4096 or raw_line.rstrip().endswith("\\"):
            raise ValueError("worker environment file contains an unsafe assignment")
        key, separator, _value = line.partition("=")
        if (
            not separator
            or re.fullmatch(r"[A-Z][A-Z0-9_]*", key) is None
            or key not in ALLOWED_WORKER_ENVIRONMENT_KEYS
            or key in assignments
        ):
            raise ValueError("worker environment file contains a duplicate or disallowed key")
        assignments[key] = _value
    if assignments.get(WORKER_SOURCE_REVISION_ENVIRONMENT_KEY) != expected_revision:
        raise ValueError("worker environment source revision does not match the deployed release")
    return sorted(assignments)


def _worker_int(value: Any, label: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{label} must be an integer greater than or equal to {minimum}")
    return value


def _worker_number(value: Any, label: str, *, minimum: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite number")
    number = float(value)
    if not (number >= minimum and number < float("inf")):
        raise ValueError(f"{label} must be a finite number greater than or equal to {minimum}")
    return number


def _worker_timestamp_pair(entry: dict[str, Any], stem: str, label: str) -> float:
    text_key = stem
    unix_key = f"{stem}_unix"
    timestamp = _utc_datetime(entry.get(text_key), f"{label} {text_key}").timestamp()
    unix_timestamp = _worker_number(entry.get(unix_key), f"{label} {unix_key}")
    if abs(timestamp - unix_timestamp) > 1:
        raise ValueError(f"{label} {stem} timestamp representations disagree")
    return unix_timestamp


def _worker_service_commands(
    runner: CommandRunner,
    service: str,
    property_name: str,
) -> tuple[tuple[str, ...], ...]:
    result = runner(
        [
            "busctl",
            "--system",
            "get-property",
            "org.freedesktop.systemd1",
            _SYSTEMD_WORKER_SERVICE_OBJECTS[service],
            "org.freedesktop.systemd1.Service",
            property_name,
        ]
    )
    if result.returncode != 0:
        raise RuntimeError(f"systemd could not read structured {property_name} for {service}")
    return _parse_systemd_exec_commands(result.stdout.strip())


def _worker_service_contract(runner: CommandRunner, service: str) -> dict[str, Any]:
    identity = UNATTENDED_WORKER_SERVICES[service]
    properties: dict[str, str] = {}
    for property_name, expected in REQUIRED_WORKER_SERVICE_PROPERTIES.items():
        actual = _systemd_property(runner, service, property_name)
        if actual != expected:
            raise RuntimeError(f"{service} effective {property_name} is not the reviewed value")
        properties[property_name] = actual

    address_families = sorted(_systemd_property(runner, service, "RestrictAddressFamilies").split())
    if address_families != sorted(REQUIRED_WORKER_ADDRESS_FAMILIES):
        raise RuntimeError(f"{service} has an unsafe address-family sandbox")
    environment_files = _systemd_property(runner, service, "EnvironmentFiles")
    expected_environment_file = (
        f"{DEFAULT_WORKER_ENVIRONMENT_PATH.as_posix()} (ignore_errors=no)"
    )
    if environment_files != expected_environment_file:
        raise RuntimeError(f"{service} does not exclusively load the dedicated worker environment")

    try:
        environment = shlex.split(_systemd_property(runner, service, "Environment"), posix=True)
        pass_environment = shlex.split(
            _systemd_property(runner, service, "PassEnvironment", allow_empty=True),
            posix=True,
        )
        unset_environment = sorted(
            shlex.split(_systemd_property(runner, service, "UnsetEnvironment"), posix=True)
        )
        read_only_paths = shlex.split(_systemd_property(runner, service, "ReadOnlyPaths"), posix=True)
        read_write_paths = shlex.split(_systemd_property(runner, service, "ReadWritePaths"), posix=True)
        capability_bounding_set = shlex.split(
            _systemd_property(runner, service, "CapabilityBoundingSet", allow_empty=True),
            posix=True,
        )
        ambient_capabilities = shlex.split(
            _systemd_property(runner, service, "AmbientCapabilities", allow_empty=True),
            posix=True,
        )
    except ValueError as exc:
        raise RuntimeError(f"{service} returned malformed sandbox properties") from exc

    timeout_start_usec = _systemd_duration_usec(_systemd_property(runner, service, "TimeoutStartUSec"))
    timeout_stop_usec = _systemd_duration_usec(_systemd_property(runner, service, "TimeoutStopUSec"))
    exec_start_pre = _worker_service_commands(runner, service, "ExecStartPreEx")
    exec_start = _worker_service_commands(runner, service, "ExecStartEx")
    contract = {
        **identity,
        "exec_start": list(exec_start[0]) if len(exec_start) == 1 else [],
        "exec_start_pre": [list(command) for command in exec_start_pre],
        "properties": properties,
        "address_families": address_families,
        "environment_file": DEFAULT_WORKER_ENVIRONMENT_PATH.as_posix(),
        "environment": environment,
        "unset_environment": unset_environment,
        "pass_environment": pass_environment,
        "read_only_paths": read_only_paths,
        "read_write_paths": read_write_paths,
        "capability_bounding_set": capability_bounding_set,
        "ambient_capabilities": ambient_capabilities,
        "timeout_start_usec": timeout_start_usec,
        "timeout_stop_usec": timeout_stop_usec,
    }
    if contract != REQUIRED_UNATTENDED_SERVICE_CONTRACTS[service]:
        raise RuntimeError(f"{service} effective command, credential isolation, or sandbox is unsafe")
    return contract


def check_unattended_workers(
    runner: CommandRunner = _run_command,
    *,
    state_path: Path = DEFAULT_WORKER_STATE_PATH,
    lock_path: Path = DEFAULT_WORKER_LOCK_PATH,
    environment_path: Path = DEFAULT_WORKER_ENVIRONMENT_PATH,
    expected_revision: str = "",
    clock: Callable[[], float] = time.time,
    recent_service_checks: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Attest safe worker units and their independently parsed durable freshness state."""
    base = {
        "name": "unattended_workers",
        "state_file": Path(state_path).as_posix(),
        "lock_file": Path(lock_path).as_posix(),
        "environment_file": Path(environment_path).as_posix(),
        "expected_service_count": len(UNATTENDED_WORKER_SERVICES),
    }
    try:
        expected_revision = expected_revision.strip().lower()
        if COMMIT_SHA.fullmatch(expected_revision) is None:
            raise ValueError("worker evidence requires an exact lowercase source revision")
        state_path = Path(state_path)
        lock_path = Path(lock_path)
        environment_path = Path(environment_path)
        if any(not path.is_absolute() for path in (state_path, lock_path, environment_path)):
            raise ValueError("worker state, lock, and environment files must use absolute paths")
        if state_path == lock_path or state_path.parent != lock_path.parent:
            raise ValueError("worker state and lock files must be distinct siblings")
        for path in (state_path, lock_path, environment_path):
            if any(component.is_symlink() for component in (path, *path.parents)):
                raise ValueError(f"{path.name} path must not contain symbolic links")

        state_raw, state_metadata = _read_private_regular_file(
            state_path,
            maximum_bytes=UNATTENDED_WORKER_STATE_MAX_BYTES,
            require_root_owner=False,
        )
        environment_raw, _environment_metadata = _read_private_regular_file(
            environment_path,
            maximum_bytes=UNATTENDED_WORKER_ENVIRONMENT_MAX_BYTES,
            require_root_owner=True,
            allow_empty=True,
        )
        _lock_raw, lock_metadata = _read_private_regular_file(
            lock_path,
            maximum_bytes=4096,
            require_root_owner=False,
            allow_empty=True,
        )
        if getattr(lock_metadata, "st_uid", None) != getattr(state_metadata, "st_uid", None):
            raise ValueError("worker state and lock files must have the same owner")

        environment_keys = _worker_environment_keys(
            environment_raw,
            expected_revision=expected_revision,
        )
        state = _strict_json_bytes(state_raw, "worker state")
        if set(state) != {"schema_version", "updated_at", "updated_at_unix", "tasks"}:
            raise ValueError("worker state top-level fields are not exact")
        if isinstance(state["schema_version"], bool) or state["schema_version"] != 1:
            raise ValueError("worker state schema_version must equal 1")
        tasks = state["tasks"]
        if not isinstance(tasks, dict) or set(tasks) != set(UNATTENDED_WORKER_TASK_MAX_AGE_SECONDS):
            raise ValueError("worker state must contain exactly both reviewed task entries")

        observed_at = clock()
        updated_at = _worker_timestamp_pair(state, "updated_at", "worker state")
        if updated_at > observed_at + UNATTENDED_WORKER_MAX_FUTURE_SKEW_SECONDS:
            raise ValueError("worker state updated_at is implausibly future-dated")

        service_contracts = {
            service: _worker_service_contract(runner, service)
            for service in UNATTENDED_WORKER_SERVICES
        }
        supplied_recent = recent_service_checks or {}
        services: dict[str, dict[str, Any]] = {}
        normalized_tasks: dict[str, dict[str, Any]] = {}
        finished_timestamps: list[float] = []
        required_task_fields = {
            "state",
            "run_id",
            "pid",
            "last_started_at",
            "last_started_at_unix",
            "deadline_seconds",
            "max_attempts",
            "source_revision",
            "service_unit",
            "unit_contract_sha256",
            "invocation_sha256",
            "attempts_completed",
            "consecutive_failures",
            "abandoned_runs",
            "last_attempt_at",
            "last_attempt_at_unix",
            "last_attempt_outcome",
            "last_attempt_processed",
            "last_attempt_problems",
            "last_attempt_emitted",
            "last_finished_at",
            "last_finished_at_unix",
            "last_duration_seconds",
            "last_outcome",
            "last_processed",
            "last_problems",
            "last_emitted",
            "total_runs",
            "total_successes",
            "total_failures",
            "last_success_at",
            "last_success_at_unix",
        }
        optional_task_fields = {
            "last_failure_at",
            "last_failure_at_unix",
            "previous_run_outcome",
        }
        for service, identity in UNATTENDED_WORKER_SERVICES.items():
            task = identity["task"]
            entry = tasks[task]
            if not isinstance(entry, dict):
                raise ValueError(f"worker state {task} entry must be an object")
            if not required_task_fields.issubset(entry) or set(entry) - (
                required_task_fields | optional_task_fields
            ):
                raise ValueError(f"worker state {task} fields are incomplete or unknown")
            if (
                entry.get("state") != "succeeded"
                or entry.get("last_outcome") != "succeeded"
                or entry.get("last_attempt_outcome") != "succeeded"
            ):
                raise ValueError(f"worker state {task} does not record a successful terminal run")
            expected_contract_sha256 = REQUIRED_WORKER_UNIT_CONTRACT_SHA256[service]
            expected_invocation_sha256 = worker_invocation_sha256(
                task=task,
                service_unit=service,
                source_revision=expected_revision,
                unit_contract_sha256=expected_contract_sha256,
            )
            if (
                entry.get("source_revision") != expected_revision
                or entry.get("service_unit") != service
                or entry.get("unit_contract_sha256") != expected_contract_sha256
                or entry.get("invocation_sha256") != expected_invocation_sha256
            ):
                raise ValueError(
                    f"worker state {task} is not bound to the reviewed revision and unit invocation"
                )
            run_id = entry.get("run_id")
            if not isinstance(run_id, str):
                raise ValueError(f"worker state {task} run_id must be a canonical UUIDv4")
            try:
                parsed_run_id = uuid.UUID(run_id)
            except ValueError as exc:
                raise ValueError(f"worker state {task} run_id must be a canonical UUIDv4") from exc
            if str(parsed_run_id) != run_id or parsed_run_id.version != 4:
                raise ValueError(f"worker state {task} run_id must be a canonical UUIDv4")

            _worker_int(entry.get("pid"), f"worker state {task} pid", minimum=1)
            deadline_seconds = _worker_number(
                entry.get("deadline_seconds"), f"worker state {task} deadline_seconds"
            )
            max_attempts = _worker_int(
                entry.get("max_attempts"), f"worker state {task} max_attempts", minimum=1
            )
            attempts_completed = _worker_int(
                entry.get("attempts_completed"),
                f"worker state {task} attempts_completed",
                minimum=1,
            )
            if deadline_seconds != 90 or max_attempts != 3 or attempts_completed > max_attempts:
                raise ValueError(f"worker state {task} retry/deadline telemetry is not reviewed")

            counters = {
                key: _worker_int(entry.get(key), f"worker state {task} {key}")
                for key in (
                    "last_attempt_processed",
                    "last_attempt_problems",
                    "last_attempt_emitted",
                    "last_processed",
                    "last_problems",
                    "last_emitted",
                    "consecutive_failures",
                    "abandoned_runs",
                    "total_runs",
                    "total_successes",
                    "total_failures",
                )
            }
            if (
                counters["last_attempt_problems"] != 0
                or counters["last_problems"] != 0
                or counters["consecutive_failures"] != 0
                or counters["total_runs"] < 1
                or counters["total_successes"] < 1
                or counters["total_successes"] + counters["total_failures"] != counters["total_runs"]
                or counters["last_attempt_processed"] != counters["last_processed"]
                or counters["last_processed"] < 1
                or counters["last_attempt_emitted"] != counters["last_emitted"]
            ):
                raise ValueError(f"worker state {task} success and counter invariants do not hold")

            started_at = _worker_timestamp_pair(entry, "last_started_at", f"worker state {task}")
            attempted_at = _worker_timestamp_pair(entry, "last_attempt_at", f"worker state {task}")
            finished_at = _worker_timestamp_pair(entry, "last_finished_at", f"worker state {task}")
            success_at = _worker_timestamp_pair(entry, "last_success_at", f"worker state {task}")
            duration_seconds = _worker_number(
                entry.get("last_duration_seconds"),
                f"worker state {task} last_duration_seconds",
            )
            if (
                started_at > attempted_at + 1
                or attempted_at > finished_at + 1
                or abs(finished_at - success_at) > 1
                or abs((finished_at - started_at) - duration_seconds) > 2
            ):
                raise ValueError(f"worker state {task} timestamps are inconsistent")
            if "last_failure_at" in entry or "last_failure_at_unix" in entry:
                if not {"last_failure_at", "last_failure_at_unix"}.issubset(entry):
                    raise ValueError(f"worker state {task} failure timestamp pair is incomplete")
                failure_at = _worker_timestamp_pair(entry, "last_failure_at", f"worker state {task}")
                if failure_at > started_at + 1:
                    raise ValueError(f"worker state {task} retained failure timestamp is inconsistent")
            if "previous_run_outcome" in entry and entry["previous_run_outcome"] != (
                "abandoned_without_terminal_telemetry"
            ):
                raise ValueError(f"worker state {task} previous run outcome is unknown")

            max_age_seconds = UNATTENDED_WORKER_TASK_MAX_AGE_SECONDS[task]
            freshness_age_seconds = observed_at - success_at
            if (
                freshness_age_seconds < -UNATTENDED_WORKER_MAX_FUTURE_SKEW_SECONDS
                or freshness_age_seconds > max_age_seconds
            ):
                raise ValueError(f"worker state {task} success is stale or implausibly future-dated")

            recent_name = f"systemd_recent_success_{service}"
            recent = supplied_recent.get(recent_name)
            if recent is None:
                recent = _recent_systemd_success(
                    service,
                    maximum_age_seconds=max_age_seconds,
                    runner=runner,
                    clock=lambda: observed_at,
                )
            if not isinstance(recent, dict) or recent.get("status") != "pass":
                raise ValueError(f"{service} does not have a recent successful systemd run")
            completed_at_unix = _worker_number(
                recent.get("completed_at_unix_seconds"),
                f"{service} completed_at_unix_seconds",
            )
            service_age_seconds = observed_at - completed_at_unix
            reported_service_age = _worker_number(
                recent.get("age_seconds"),
                f"{service} age_seconds",
                minimum=-UNATTENDED_WORKER_MAX_FUTURE_SKEW_SECONDS,
            )
            if (
                recent.get("unit") != service
                or recent.get("max_age_seconds") != max_age_seconds
                or not isinstance(recent.get("completed_at"), str)
                or not recent["completed_at"].strip()
                or service_age_seconds < -UNATTENDED_WORKER_MAX_FUTURE_SKEW_SECONDS
                or service_age_seconds > max_age_seconds
                or abs(reported_service_age - service_age_seconds) > 5
                or completed_at_unix - finished_at < -2
                or completed_at_unix - finished_at > UNATTENDED_WORKER_COMPLETION_SKEW_SECONDS
            ):
                raise ValueError(f"{service} completion is inconsistent with durable worker telemetry")

            services[service] = {
                **identity,
                "completed_at": recent["completed_at"],
                "completed_at_unix_seconds": completed_at_unix,
                "age_seconds": reported_service_age,
                "max_age_seconds": max_age_seconds,
                "source_revision": expected_revision,
                "unit_contract_sha256": expected_contract_sha256,
            }
            normalized_tasks[task] = {
                "service": service,
                "timer": identity["timer"],
                "state": entry["state"],
                "run_id": run_id,
                "source_revision": expected_revision,
                "service_unit": service,
                "unit_contract_sha256": expected_contract_sha256,
                "invocation_sha256": expected_invocation_sha256,
                "last_started_at": entry["last_started_at"],
                "last_started_at_unix_seconds": started_at,
                "last_attempt_at": entry["last_attempt_at"],
                "last_attempt_at_unix_seconds": attempted_at,
                "last_finished_at": entry["last_finished_at"],
                "last_finished_at_unix_seconds": finished_at,
                "last_success_at": entry["last_success_at"],
                "last_success_at_unix_seconds": success_at,
                "last_duration_seconds": duration_seconds,
                "deadline_seconds": deadline_seconds,
                "max_attempts": max_attempts,
                "attempts_completed": attempts_completed,
                "last_attempt_outcome": entry["last_attempt_outcome"],
                "last_outcome": entry["last_outcome"],
                "last_attempt_processed": counters["last_attempt_processed"],
                "last_attempt_problems": counters["last_attempt_problems"],
                "last_attempt_emitted": counters["last_attempt_emitted"],
                "processed": counters["last_processed"],
                "problems": counters["last_problems"],
                "emitted": counters["last_emitted"],
                "consecutive_failures": counters["consecutive_failures"],
                "abandoned_runs": counters["abandoned_runs"],
                "total_runs": counters["total_runs"],
                "total_successes": counters["total_successes"],
                "total_failures": counters["total_failures"],
                "freshness_age_seconds": round(freshness_age_seconds, 6),
                "max_age_seconds": max_age_seconds,
            }
            finished_timestamps.append(finished_at)

        if abs(updated_at - max(finished_timestamps)) > 1:
            raise ValueError("worker state updated_at does not identify the latest terminal task update")
    except (OSError, RuntimeError, ValueError) as exc:
        return {**base, "status": "fail", "detail": str(exc)}

    return {
        **base,
        "status": "pass",
        "detail": (
            "both serialized unattended tasks have exact hardened units, scoped environments, "
            "recent successful runs, and fresh durable terminal telemetry"
        ),
        "state_schema_version": state["schema_version"],
        "state_updated_at": state["updated_at"],
        "state_updated_at_unix_seconds": updated_at,
        "state_sha256": hashlib.sha256(state_raw).hexdigest(),
        "environment_keys": environment_keys,
        "service_count": len(services),
        "service_contracts": service_contracts,
        "services": services,
        "tasks": normalized_tasks,
    }


def check_backup_evidence(
    backup_directory: Path,
    *,
    clock: Callable[[], float] = time.time,
    stat_reader: Callable[[Path], object] = lambda path: path.lstat(),
) -> dict[str, Any]:
    """Require a recent, bounded, cryptographically verified state backup."""
    backup_directory = Path(backup_directory)
    base = {
        "name": "verified_recent_state_backup",
        "directory": str(backup_directory),
    }
    if not backup_directory.is_absolute():
        return {
            **base,
            "status": "fail",
            "detail": "trusted backup directory must be an absolute path",
        }
    symlinked_component = next(
        (path for path in (backup_directory, *backup_directory.parents) if path.is_symlink()),
        None,
    )
    if symlinked_component is not None:
        return {
            **base,
            "status": "fail",
            "detail": f"refusing symbolic-link backup path component: {symlinked_component}",
        }
    directory_check = _check_private_path(backup_directory, S_IFDIR, False, stat_reader)
    if directory_check["status"] != "pass":
        return {
            **base,
            "status": "fail",
            "detail": f"backup directory is not a trusted private directory: {directory_check['detail']}",
        }

    try:
        catalog = catalog_verified_backups(
            backup_directory,
            max_members=DEFAULT_MAX_MEMBERS,
            max_bytes=DEFAULT_MAX_UNCOMPRESSED_BYTES,
            max_archive_bytes=DEFAULT_MAX_ARCHIVE_BYTES,
        )
    except (OSError, RuntimeError, ValueError, tarfile.TarError) as exc:
        return {**base, "status": "fail", "detail": str(exc)}

    observed_at = clock()
    for backup in catalog.verified:
        age_seconds = observed_at - backup.created_at.timestamp()
        if -BACKUP_MAX_FUTURE_SKEW_SECONDS <= age_seconds <= BACKUP_MAX_AGE_SECONDS:
            manifest = backup.manifest
            return {
                **base,
                "status": "pass",
                "archive": backup.archive_path.name,
                "created_at": backup.created_at.isoformat().replace("+00:00", "Z"),
                "backup_age_seconds": round(age_seconds),
                "sha256": manifest["sha256"],
                "file_count": manifest["file_count"],
                "verified_archive_bytes": manifest["verified_archive_bytes"],
                "verified_tar_bytes": manifest["verified_tar_bytes"],
                "verified_bytes": manifest["verified_bytes"],
                "verified_pairs": len(catalog.verified),
                "invalid_pairs": len(catalog.invalid_pairs),
                "orphan_archives": len(catalog.orphan_archives),
                "orphan_manifests": len(catalog.orphan_manifests),
                "detail": "archive checksum, manifest, member paths, types, counts, and size bounds verified",
            }

    return {
        **base,
        "status": "fail",
        "verified_pairs": len(catalog.verified),
        "invalid_pairs": len(catalog.invalid_pairs),
        "orphan_archives": len(catalog.orphan_archives),
        "orphan_manifests": len(catalog.orphan_manifests),
        "detail": (
            "no cryptographically verified backup pair has a creation timestamp within "
            f"{BACKUP_MAX_AGE_SECONDS} seconds and no more than "
            f"{BACKUP_MAX_FUTURE_SKEW_SECONDS} seconds in the future"
        ),
    }


def _utc_datetime(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty UTC timestamp")
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"{label} is not a valid ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{label} must include a UTC offset")
    return parsed.astimezone(timezone.utc)


def check_deployment_host_identity(
    provider: str,
    expected_host_identity_sha256: str,
    identity_file: Path = Path("/etc/machine-id"),
) -> dict[str, Any]:
    """Bind a self-hosted report to a protected provider label and machine identity."""

    normalized_provider = provider.strip().lower()
    expected_digest = expected_host_identity_sha256.strip().lower()
    base = {
        "name": "deployment_host_identity",
        "deployment_provider": normalized_provider,
        "host_identity_sha256": expected_digest,
    }
    try:
        identity_file = Path(identity_file)
        if not PROVIDER_SLUG.fullmatch(normalized_provider):
            raise ValueError("deployment provider must be a lowercase provider slug")
        if not SHA256_HEX.fullmatch(expected_digest):
            raise ValueError("expected host identity must be a lowercase SHA-256 digest")
        if not identity_file.is_absolute():
            raise ValueError("host identity file must use an absolute path")
        if any(path.is_symlink() for path in (identity_file, *identity_file.parents)):
            raise ValueError("host identity path must not contain symbolic links")
        metadata = identity_file.lstat()
        if not S_ISREG(metadata.st_mode) or S_IMODE(metadata.st_mode) & 0o022:
            raise ValueError("host identity file must be a non-writable regular file")
        if os.name == "posix" and getattr(metadata, "st_uid", -1) != 0:
            raise ValueError("host identity file must be root-owned")
        raw_identity = identity_file.read_bytes()
        if not raw_identity or len(raw_identity) > 4096:
            raise ValueError("host identity file is empty or oversized")
        actual_digest = hashlib.sha256(raw_identity.strip()).hexdigest()
        if actual_digest != expected_digest:
            raise ValueError("production host identity does not match the protected expected digest")
    except (OSError, RuntimeError, ValueError) as exc:
        return {**base, "status": "fail", "detail": str(exc)}
    return {
        **base,
        "status": "pass",
        "detail": "provider and root-owned machine identity match protected deployment configuration",
    }


def check_restore_drill(
    backup_directory: Path,
    *,
    clock: Callable[[], float] = time.time,
    frontend_dir: Path = PROJECT_ROOT / "frontend" / "dist",
    expected_version: str = "",
    expected_source_revision: str = "",
    expected_frontend_sha256: str = "",
) -> dict[str, Any]:
    """Restore and boot a read-only application against the isolated backup."""

    base = {"name": "verified_restore_drill", "mode": "isolated_full_restore"}
    try:
        catalog = catalog_verified_backups(
            Path(backup_directory),
            max_members=DEFAULT_MAX_MEMBERS,
            max_bytes=DEFAULT_MAX_UNCOMPRESSED_BYTES,
            max_archive_bytes=DEFAULT_MAX_ARCHIVE_BYTES,
        )
        observed_at = clock()
        backup = next(
            (
                item
                for item in catalog.verified
                if -BACKUP_MAX_FUTURE_SKEW_SECONDS
                <= observed_at - item.created_at.timestamp()
                <= BACKUP_MAX_AGE_SECONDS
            ),
            None,
        )
        if backup is None:
            raise RuntimeError("no recent verified backup is available for a restore drill")
        with tempfile.TemporaryDirectory(prefix="market-sentinel-restore-drill-") as temporary:
            restored = Path(temporary) / "restored-state"
            manifest = restore_backup(
                backup.archive_path,
                restored,
                max_members=DEFAULT_MAX_MEMBERS,
                max_bytes=DEFAULT_MAX_UNCOMPRESSED_BYTES,
                max_archive_bytes=DEFAULT_MAX_ARCHIVE_BYTES,
            )
            restored_files = 0
            restored_bytes = 0
            for candidate in restored.rglob("*"):
                if candidate.is_symlink():
                    raise RuntimeError("restore drill produced a symbolic link")
                if candidate.is_file():
                    restored_files += 1
                    restored_bytes += candidate.stat().st_size
            if (
                restored_files != manifest.get("file_count")
                or restored_bytes != manifest.get("verified_bytes")
            ):
                raise RuntimeError("restored file inventory does not match the verified backup manifest")
            result = subprocess.run(
                [
                    sys.executable, "-I", "-B", str(PROJECT_ROOT / "scripts" / "verify_restored_state.py"),
                    "--state", str(restored), "--frontend-dir", str(frontend_dir.resolve()),
                ],
                cwd=temporary, env=isolated_environment(restored),
                capture_output=True, text=True, timeout=60, check=False,
            )
            if result.returncode != 0 or len(result.stdout) > 4096:
                raise RuntimeError("restored application validation failed; backup contents were not promoted")
            application = json.loads(result.stdout)
            identity = source_identity()
            if not application_check_valid(
                application,
                version=expected_version or identity["project_version"],
                revision=expected_source_revision or identity["git_revision"],
                frontend_sha256=expected_frontend_sha256 or frontend_tree_sha256(frontend_dir),
            ):
                raise RuntimeError("restored application validation or runtime identity is invalid")
        completed_at = datetime.fromtimestamp(clock(), timezone.utc).isoformat().replace("+00:00", "Z")
    except subprocess.TimeoutExpired:
        return {**base, "status": "fail", "detail": "restored application exceeded the 60-second validation budget"}
    except (OSError, RuntimeError, ValueError, tarfile.TarError) as exc:
        return {**base, "status": "fail", "detail": str(exc)}
    return {
        **base,
        "status": "pass",
        "archive": backup.archive_path.name,
        "backup_created_at": backup.created_at.isoformat().replace("+00:00", "Z"),
        "backup_sha256": manifest["sha256"],
        "restored_file_count": restored_files,
        "restored_bytes": restored_bytes,
        "completed_at": completed_at,
        "application": application,
        "detail": "the complete backup was restored, booted read-only without venue access, and left unchanged",
    }


def _read_strict_json_object(path: Path, *, maximum_bytes: int) -> tuple[dict[str, Any], str]:
    raw = path.read_bytes()
    if not raw or len(raw) > maximum_bytes:
        raise ValueError("JSON report is empty or oversized")

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        raise ValueError(f"non-finite JSON number: {value}")

    try:
        payload = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=reject_duplicates,
            parse_constant=reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("JSON report is malformed") from exc
    if not isinstance(payload, dict):
        raise ValueError("JSON report must be an object")
    return payload, hashlib.sha256(raw).hexdigest()


def check_rollback_drill(
    report_path: Path,
    *,
    expected_version: str,
    expected_current_revision: str,
    expected_frontend_sha256: str,
    deployment_provider: str,
    host_identity_sha256: str,
    public_origin: str,
    clock: Callable[[], float] = time.time,
) -> dict[str, Any]:
    """Validate a recent root-owned journal from a completed production rollback drill."""

    base = {"name": "verified_production_rollback_drill"}
    try:
        report_path = Path(report_path)
        if not report_path.is_absolute():
            raise ValueError("rollback drill report must use an absolute path")
        if any(path.is_symlink() for path in (report_path, *report_path.parents)):
            raise ValueError("rollback drill report path must not contain symbolic links")
        metadata = report_path.lstat()
        if not S_ISREG(metadata.st_mode) or S_IMODE(metadata.st_mode) & 0o077:
            raise ValueError("rollback drill report must be a private regular file")
        if os.name == "posix" and getattr(metadata, "st_uid", -1) != 0:
            raise ValueError("rollback drill report must be root-owned")
        report, report_sha256 = _read_strict_json_object(report_path, maximum_bytes=64 * 1024)
        expected_fields = {
            "schema_version",
            "report_type",
            "drill_id",
            "started_at",
            "completed_at",
            "deployment_provider",
            "host_identity_sha256",
            "public_origin",
            "current_revision",
            "rollback_revision",
            "final_revision",
            "status",
            "steps",
        }
        if set(report) != expected_fields:
            raise ValueError("rollback drill report fields are not exact")
        drill_id = str(report.get("drill_id") or "")
        if str(uuid.UUID(drill_id)) != drill_id:
            raise ValueError("rollback drill id must be a canonical UUID")
        current_revision = expected_current_revision.strip().lower()
        rollback_revision = str(report.get("rollback_revision") or "").strip().lower()
        if (
            report.get("schema_version") != 1
            or report.get("report_type") != ROLLBACK_DRILL_REPORT_TYPE
            or report.get("status") != "ok"
            or report.get("deployment_provider") != deployment_provider.strip().lower()
            or report.get("host_identity_sha256") != host_identity_sha256.strip().lower()
            or report.get("public_origin") != _validated_public_origin(public_origin)
            or report.get("current_revision") != current_revision
            or report.get("final_revision") != current_revision
            or not COMMIT_SHA.fullmatch(rollback_revision)
            or rollback_revision == current_revision
        ):
            raise ValueError("rollback drill identity does not match this deployment")
        started_at = _utc_datetime(report.get("started_at"), "rollback started_at")
        completed_at = _utc_datetime(report.get("completed_at"), "rollback completed_at")
        observed_at = datetime.fromtimestamp(clock(), timezone.utc)
        age_seconds = (observed_at - completed_at).total_seconds()
        if (
            completed_at < started_at
            or (completed_at - started_at).total_seconds() > 60 * 60
            or age_seconds < -ROLLBACK_DRILL_MAX_FUTURE_SKEW_SECONDS
            or age_seconds > ROLLBACK_DRILL_MAX_AGE_SECONDS
        ):
            raise ValueError("rollback drill timing is stale, future-dated, reversed, or unbounded")
        steps = report.get("steps")
        if not isinstance(steps, list) or len(steps) != len(ROLLBACK_DRILL_STEPS):
            raise ValueError("rollback drill must contain the exact ordered step inventory")
        expected_revisions = (
            current_revision,
            rollback_revision,
            rollback_revision,
            current_revision,
            current_revision,
        )
        previous_time = started_at
        for position, (step, expected_name, expected_revision) in enumerate(
            zip(steps, ROLLBACK_DRILL_STEPS, expected_revisions, strict=True)
        ):
            if not isinstance(step, dict) or set(step) != {
                "name",
                "status",
                "revision",
                "observed_at",
                "api_version",
                "runtime_source_revision",
                "runtime_frontend_sha256",
            }:
                raise ValueError(f"rollback drill step {position} fields are not exact")
            step_time = _utc_datetime(step.get("observed_at"), f"rollback step {position} observed_at")
            if (
                step.get("name") != expected_name
                or step.get("status") != "pass"
                or step.get("revision") != expected_revision
                or step_time < previous_time
                or step_time > completed_at
            ):
                raise ValueError(f"rollback drill step {position} is invalid")
            is_current_health = expected_name in {
                "current_release_healthy",
                "current_release_healthy_after_reactivation",
            }
            is_rollback_health = expected_name == "rollback_release_healthy"
            if is_current_health:
                if (
                    step.get("api_version") != expected_version
                    or step.get("runtime_source_revision") != current_revision
                    or step.get("runtime_frontend_sha256") != expected_frontend_sha256
                ):
                    raise ValueError("current-release health proof in rollback drill is invalid")
            elif is_rollback_health:
                if (
                    not isinstance(step.get("api_version"), str)
                    or not step["api_version"].strip()
                    or step.get("runtime_source_revision") != rollback_revision
                    or not isinstance(step.get("runtime_frontend_sha256"), str)
                    or not SHA256_HEX.fullmatch(step["runtime_frontend_sha256"])
                ):
                    raise ValueError("rollback-release health proof in rollback drill is invalid")
            elif any(step.get(field) != "" for field in (
                "api_version",
                "runtime_source_revision",
                "runtime_frontend_sha256",
            )):
                raise ValueError("activation-only rollback steps must not claim health fingerprints")
            previous_time = step_time
    except (OSError, RuntimeError, ValueError) as exc:
        return {**base, "status": "fail", "detail": str(exc)}
    return {
        **base,
        "status": "pass",
        "drill_id": drill_id,
        "report_sha256": report_sha256,
        "completed_at": completed_at.isoformat().replace("+00:00", "Z"),
        "rollback_revision": rollback_revision,
        "final_revision": current_revision,
        "step_count": len(steps),
        "detail": "rollback release activation, health, and reactivation of the current release were journaled",
    }


def _require_runtime_fingerprints(
    payload: dict[str, Any],
    expected_source_revision: str,
    expected_frontend_sha256: str,
) -> tuple[str, str]:
    runtime_revision = str(payload.get("runtime_source_revision") or "").strip().lower()
    runtime_frontend_sha256 = str(payload.get("runtime_frontend_sha256") or "").strip().lower()
    if expected_source_revision and runtime_revision != expected_source_revision:
        raise RuntimeError(
            "health endpoint reported process-start source revision "
            f"{runtime_revision or 'unavailable'}, expected {expected_source_revision}; restart the service"
        )
    if expected_frontend_sha256 and runtime_frontend_sha256 != expected_frontend_sha256:
        raise RuntimeError(
            "health endpoint reported process-start frontend digest "
            f"{runtime_frontend_sha256 or 'unavailable'}, expected {expected_frontend_sha256}; restart the service"
        )
    return runtime_revision, runtime_frontend_sha256


def check_loopback(
    url: str,
    token: str,
    timeout: float,
    expected_version: str = "",
    expected_source_revision: str = "",
    expected_frontend_sha256: str = "",
    frontend_dir: Path = PROJECT_ROOT / "frontend" / "dist",
) -> dict[str, Any]:
    payload = check_health(url, token, timeout)
    version = str(payload["api_version"])
    if expected_version and version != expected_version:
        raise RuntimeError(f"health endpoint reported version {version}, expected {expected_version}")
    runtime_revision, runtime_frontend = _require_runtime_fingerprints(
        payload,
        expected_source_revision,
        expected_frontend_sha256,
    )
    disk_frontend = ""
    if expected_frontend_sha256:
        disk_frontend = frontend_tree_sha256(frontend_dir)
        if disk_frontend != expected_frontend_sha256:
            raise RuntimeError(
                f"served frontend tree digest is {disk_frontend}, expected {expected_frontend_sha256}"
            )
    return {
        "name": "loopback_health",
        "status": "pass",
        "api_version": version,
        "runtime_source_revision": runtime_revision,
        "runtime_frontend_sha256": runtime_frontend,
        "disk_frontend_sha256": disk_frontend,
    }


def check_loopback_metrics(url: str, token: str, timeout: float) -> dict[str, Any]:
    """Verify the authenticated Prometheus endpoint without persisting metric values."""
    headers = {"Accept": "text/plain; version=0.0.4"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    with urlopen(Request(url, headers=headers, method="GET"), timeout=timeout) as response:
        content_type = str(response.headers.get("Content-Type") or "").lower()
        body = read_probe_body(response).decode("utf-8")
    if response.status != 200:
        raise RuntimeError(f"loopback metrics endpoint returned HTTP {response.status}")
    if not content_type.startswith("text/plain; version=0.0.4"):
        raise RuntimeError("loopback metrics endpoint did not return Prometheus text format")
    required = (
        "market_sentinel_http_requests_total",
        "market_sentinel_http_request_duration_seconds_total",
        "market_sentinel_http_requests_completed_total",
    )
    missing = [name for name in required if name not in body]
    if missing:
        raise RuntimeError("loopback metrics endpoint is missing required metrics: " + ", ".join(missing))
    return {"name": "loopback_metrics", "status": "pass", "format": "prometheus"}


def _require_unauthorized(request: Request, timeout: float, label: str, *, opener=None) -> None:
    opener = opener or urlopen
    try:
        with opener(request, timeout=timeout) as response:
            status = response.status
    except HTTPError as exc:
        try:
            if exc.code != 401:
                raise RuntimeError(f"unauthenticated {label} returned HTTP {exc.code}, expected 401") from exc
        finally:
            exc.close()
        return
    raise RuntimeError(f"unauthenticated {label} was accepted with HTTP {status}")


def check_loopback_token_auth(url: str, timeout: float) -> dict[str, Any]:
    """Prove that the upstream API rejects a tokenless loopback request."""
    _require_unauthorized(
        Request(url, headers={"Accept": "application/json"}, method="GET"),
        timeout,
        "loopback API request",
    )
    return {"name": "loopback_token_auth", "status": "pass"}


def check_public_proxy(
    url: str,
    username: str,
    password: str,
    timeout: float,
    expected_version: str = "",
    upstream_token: str = "",
    expected_source_revision: str = "",
    expected_frontend_sha256: str = "",
    *,
    require_upstream_token: bool = True,
) -> dict[str, Any]:
    origin = _validated_public_origin(url, timeout)
    if not username or not password:
        raise ValueError("public proxy verification requires non-empty Basic Auth credentials")
    if require_upstream_token and not upstream_token.strip():
        raise ValueError("public proxy verification requires a non-empty upstream API token")

    base_url = origin + "/"
    for method, relative_url in PUBLIC_PROXY_AUTH_PROBES:
        probe_url = urljoin(base_url, relative_url)
        headers = {"Accept": "application/json"}
        body = None
        if method == "PATCH":
            headers["Content-Type"] = "application/json"
            body = b"{"
        _require_unauthorized(
            Request(probe_url, data=body, headers=headers, method=method),
            timeout,
            f"public proxy {method} {urlparse(probe_url).path or '/'}",
            opener=partial(urlopen, public_only=True),
        )

    health_url = urljoin(base_url, "api/health")

    headers = {"Accept": "application/json"}
    encoded = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    headers["Authorization"] = f"Basic {encoded}"
    with urlopen(Request(health_url, headers=headers, method="GET"), timeout=timeout, public_only=True) as response:
        payload = read_health_payload(response)
        response_headers = {str(name).lower(): str(value) for name, value in response.headers.items()}
        missing = [name for name in REQUIRED_PROXY_HEADER_VALUES if name not in response_headers]
        if expected_version and str(payload.get("api_version", "")) != expected_version:
            raise RuntimeError(
                f"public proxy reported version {payload.get('api_version')}, expected {expected_version}"
            )
        runtime_revision, runtime_frontend_sha256 = _require_runtime_fingerprints(
            payload,
            expected_source_revision,
            expected_frontend_sha256,
        )
        if response_headers.get("cache-control") != "no-store":
            raise RuntimeError("public proxy health endpoint is missing Cache-Control: no-store")
        if missing:
            raise RuntimeError("public proxy is missing security headers: " + ", ".join(missing))
        weak_headers = [
            name
            for name, expected_values in REQUIRED_PROXY_HEADER_VALUES.items()
            if any(value not in response_headers[name].lower() for value in expected_values)
        ]
        if weak_headers:
            raise RuntimeError("public proxy has incomplete security-header policy: " + ", ".join(weak_headers))
        if response_headers.get("server"):
            raise RuntimeError("public proxy exposes a Server header")
    return {
        "name": "public_https_proxy",
        "status": "pass",
        "api_version": payload.get("api_version"),
        "runtime_source_revision": runtime_revision,
        "runtime_frontend_sha256": runtime_frontend_sha256,
        "unauthenticated_probes": len(PUBLIC_PROXY_AUTH_PROBES),
    }


def write_evidence(path: Path, payload: dict[str, Any]) -> None:
    """Atomically persist redacted deployment evidence in a pre-validated directory."""
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            if os.name == "posix":
                os.fchmod(handle.fileno(), 0o600)
            json.dump(payload, handle, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_parent_directory(path)
    except OSError:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _fsync_parent_directory(path: Path) -> None:
    """Persist the directory entry created by the atomic replacement on POSIX hosts."""
    if os.name != "posix":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path.parent, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def build_evidence(
    checks: list[dict[str, Any]],
    *,
    collected_at: datetime | None = None,
    source: dict[str, str] | None = None,
    collection: dict[str, Any] | None = None,
) -> dict[str, Any]:
    timestamp = collected_at or datetime.now(timezone.utc)
    payload = {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "collected_at": timestamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source": source if source is not None else source_identity(),
        "status": "ok" if all(check["status"] == "pass" for check in checks) else "failed",
        "checks": checks,
    }
    if collection is not None:
        payload["collection"] = collection
    return payload


def build_external_public_probe_evidence(
    *,
    public_origin: str,
    username: str,
    password: str,
    expected_version: str,
    expected_source_revision: str,
    expected_frontend_sha256: str,
    run_id: int,
    run_attempt: int,
    nonce: str,
    timeout: float,
    probed_at: datetime | None = None,
) -> dict[str, Any]:
    """Probe the public deployment from a separately attested GitHub-hosted job."""

    origin = _validated_public_origin(public_origin, timeout)
    revision = expected_source_revision.strip().lower()
    frontend_sha256 = expected_frontend_sha256.strip().lower()
    if not expected_version.strip() or not COMMIT_SHA.fullmatch(revision):
        raise ValueError("external probe requires an exact release version and source revision")
    if not SHA256_HEX.fullmatch(frontend_sha256):
        raise ValueError("external probe requires an exact frontend SHA-256")
    if run_id <= 0 or run_attempt <= 0 or nonce != f"{revision}:{run_id}:{run_attempt}":
        raise ValueError("external probe workflow identity is invalid")
    check = check_public_proxy(
        origin,
        username,
        password,
        timeout,
        expected_version.strip(),
        "",
        revision,
        frontend_sha256,
        require_upstream_token=False,
    )
    timestamp = (probed_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return {
        "schema_version": 1,
        "report_type": EXTERNAL_PROBE_REPORT_TYPE,
        "probed_at": timestamp.isoformat().replace("+00:00", "Z"),
        "status": "ok",
        "source_revision": revision,
        "collection": {
            "mode": "github_hosted_external_public_probe",
            "public_origin": origin,
            "expected_version": expected_version.strip(),
            "expected_source_revision": revision,
            "expected_frontend_sha256": frontend_sha256,
            "run_id": run_id,
            "run_attempt": run_attempt,
            "nonce": nonce,
            "runner_environment": "github-hosted",
        },
        "checks": [check],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect read-only MarketSentinel production deployment evidence.")
    parser.add_argument("--loopback-url", default="http://127.0.0.1:8765/api/health")
    parser.add_argument("--loopback-metrics-url", default="http://127.0.0.1:8765/metrics")
    parser.add_argument("--token", default=os.environ.get("MARKET_SENTINEL_API_TOKEN", ""))
    parser.add_argument("--expected-version", default="")
    parser.add_argument(
        "--expected-source-revision",
        default="",
        help="Required lowercase 40-character Git commit for the deployed release source.",
    )
    parser.add_argument(
        "--expected-frontend-sha256",
        default="",
        help="Required trusted SHA-256 fingerprint of the reviewed frontend tree.",
    )
    parser.add_argument(
        "--frontend-dir",
        type=Path,
        default=PROJECT_ROOT / "frontend" / "dist",
        help="Served frontend directory to hash independently of the running process.",
    )
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument(
        "--skip-systemd",
        action="store_true",
        help=(
            "Skip Linux systemd, filesystem-ownership, and backup-archive checks for an isolated loopback smoke test."
        ),
    )
    parser.add_argument(
        "--backup-directory",
        type=Path,
        default=DEFAULT_BACKUP_DIRECTORY,
        help="Trusted private directory containing state backup archive/manifest pairs.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional path for an atomically written, mode-0600 JSON evidence record.",
    )
    parser.add_argument("--public-url", default="")
    parser.add_argument("--deployment-root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--evidence-run-id", type=int, default=0)
    parser.add_argument("--evidence-run-attempt", type=int, default=0)
    parser.add_argument("--evidence-nonce", default="")
    parser.add_argument(
        "--external-public-probe-only",
        action="store_true",
        help="Run only the public HTTPS probe for a separately attested GitHub-hosted job.",
    )
    parser.add_argument("--deployment-provider", default="")
    parser.add_argument("--expected-host-id-sha256", default="")
    parser.add_argument("--host-identity-file", type=Path, default=Path("/etc/machine-id"))
    parser.add_argument(
        "--rollback-drill-report",
        type=Path,
        default=Path("/var/lib/market-sentinel-rollback-drills/latest.json"),
    )
    parser.add_argument("--public-basic-user", default=os.environ.get("MARKET_SENTINEL_PUBLIC_BASIC_USER", ""))
    parser.add_argument(
        "--public-basic-password-env",
        default="MARKET_SENTINEL_PUBLIC_BASIC_PASSWORD",
        help="Environment variable containing the required public Basic Auth password when --public-url is set.",
    )
    args = parser.parse_args()

    if args.external_public_probe_only:
        try:
            if args.output is None:
                raise ValueError("external public probe requires --output")
            password = os.environ.get(args.public_basic_password_env, "")
            evidence = build_external_public_probe_evidence(
                public_origin=args.public_url,
                username=args.public_basic_user,
                password=password,
                expected_version=args.expected_version,
                expected_source_revision=args.expected_source_revision,
                expected_frontend_sha256=args.expected_frontend_sha256,
                run_id=args.evidence_run_id,
                run_attempt=args.evidence_run_attempt,
                nonce=args.evidence_nonce,
                timeout=args.timeout,
            )
            args.output.parent.mkdir(parents=True, exist_ok=True)
            write_evidence(args.output, evidence)
        except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
            print(json.dumps({"status": "failed", "detail": str(exc)}, sort_keys=True))
            return 1
        print(json.dumps(evidence, sort_keys=True))
        return 0

    checks: list[dict[str, Any]] = []
    evidence_source = source_identity(args.deployment_root)
    expected_version = args.expected_version.strip()
    expected_source_revision = args.expected_source_revision.strip().lower()
    expected_frontend_sha256 = args.expected_frontend_sha256.strip().lower()
    try:
        public_origin = _validated_public_origin(args.public_url, args.timeout) if args.public_url else ""
    except (OSError, ValueError):
        public_origin = ""
    collection = {
        "mode": "production" if not args.skip_systemd and bool(args.public_url) else "local_smoke",
        "systemd_requested": not args.skip_systemd,
        "public_proxy_requested": bool(args.public_url),
        "public_origin": public_origin,
        "expected_version": expected_version,
        "expected_source_revision": expected_source_revision,
        "expected_frontend_sha256": expected_frontend_sha256,
        "deployment_provider": args.deployment_provider.strip().lower(),
        "host_identity_sha256": args.expected_host_id_sha256.strip().lower(),
        "restore_drill_requested": not args.skip_systemd and bool(public_origin),
        "rollback_drill_requested": not args.skip_systemd and bool(public_origin),
        "run_id": args.evidence_run_id,
        "run_attempt": args.evidence_run_attempt,
        "nonce": args.evidence_nonce,
    }
    missing_identity = False
    if args.public_url and not public_origin:
        missing_identity = True
        checks.append({"name": "public_origin", "status": "fail", "detail": "--public-url must be a canonical public HTTPS origin"})
    if not args.skip_systemd and (args.evidence_run_id <= 0 or args.evidence_run_attempt <= 0 or not args.evidence_nonce.strip()):
        missing_identity = True
        checks.append({"name": "workflow_nonce", "status": "fail", "detail": "production collection requires run id, attempt, and nonce"})
    if not expected_version:
        missing_identity = True
        checks.append(
            {
                "name": "expected_version",
                "status": "fail",
                "detail": "--expected-version is required to prove the deployed release identity",
            }
        )
    if not expected_source_revision:
        missing_identity = True
        checks.append(
            {
                "name": "expected_source_revision",
                "status": "fail",
                "detail": "--expected-source-revision is required to prove the deployed source identity",
            }
        )
    if not expected_frontend_sha256:
        missing_identity = True
        checks.append(
            {
                "name": "expected_frontend_sha256",
                "status": "fail",
                "detail": "--expected-frontend-sha256 is required to prove the served frontend identity",
            }
        )
    elif not SHA256_HEX.fullmatch(expected_frontend_sha256):
        missing_identity = True
        checks.append(
            {
                "name": "expected_frontend_sha256",
                "status": "fail",
                "detail": "--expected-frontend-sha256 must be a lowercase 64-character SHA-256 digest",
            }
        )
    if not missing_identity:
        try:
            checks.append(check_source_revision(expected_source_revision, evidence_source))
            if not args.skip_systemd:
                systemd_checks = check_systemd()
                checks.extend(systemd_checks)
                checks.extend(check_filesystem_permissions(backup_directory=args.backup_directory))
                checks.append(check_health_credential_isolation())
                checks.append(check_web_startup_preflight())
                checks.append(check_durable_state_wiring())
                checks.append(
                    check_unattended_workers(
                        expected_revision=expected_source_revision,
                        recent_service_checks={
                            check["name"]: check
                            for check in systemd_checks
                            if str(check.get("name", "")).startswith(
                                "systemd_recent_success_market-sentinel-"
                            )
                        }
                    )
                )
                checks.append(check_backup_evidence(args.backup_directory))
                if public_origin:
                    checks.append(
                        check_deployment_host_identity(
                            args.deployment_provider,
                            args.expected_host_id_sha256,
                            args.host_identity_file,
                        )
                    )
                    checks.append(check_restore_drill(
                        args.backup_directory, frontend_dir=args.frontend_dir,
                        expected_version=expected_version,
                        expected_source_revision=expected_source_revision,
                        expected_frontend_sha256=expected_frontend_sha256,
                    ))
                    checks.append(
                        check_rollback_drill(
                            args.rollback_drill_report,
                            expected_version=expected_version,
                            expected_current_revision=expected_source_revision,
                            expected_frontend_sha256=expected_frontend_sha256,
                            deployment_provider=args.deployment_provider,
                            host_identity_sha256=args.expected_host_id_sha256,
                            public_origin=public_origin,
                        )
                    )
            if args.public_url and not args.token.strip():
                raise ValueError("public proxy verification requires a non-empty upstream API token")
            checks.append(
                check_loopback(
                    args.loopback_url,
                    args.token,
                    args.timeout,
                    expected_version,
                    expected_source_revision,
                    expected_frontend_sha256,
                    args.frontend_dir,
                )
            )
            checks.append(check_loopback_metrics(args.loopback_metrics_url, args.token, args.timeout))
            if public_origin:
                password = os.environ.get(args.public_basic_password_env, "")
                checks.append(check_loopback_token_auth(args.loopback_url, args.timeout))
                checks.append(
                    check_public_proxy(
                        public_origin,
                        args.public_basic_user,
                        password,
                        args.timeout,
                        expected_version,
                        args.token,
                        expected_source_revision,
                        expected_frontend_sha256,
                    )
                )
        except (OSError, RuntimeError, ValueError, json.JSONDecodeError, subprocess.TimeoutExpired) as exc:
            checks.append({"name": "deployment_verifier", "status": "fail", "detail": str(exc)})

    # Re-read source provenance after all host/network probes. Evidence must
    # never report success for a revision different from the one actually
    # recorded in the artifact.
    evidence_source = source_identity(args.deployment_root)
    if not missing_identity:
        final_source_check = check_source_revision(expected_source_revision, evidence_source)
        final_source_check["name"] = "source_revision_final"
        checks.append(final_source_check)
    evidence = build_evidence(checks, source=evidence_source, collection=collection)
    if args.output:
        output_directory = check_evidence_output_directory(args.output)
        checks.append(output_directory)
        evidence_source = source_identity(args.deployment_root)
        if not missing_identity:
            pre_write_source_check = check_source_revision(expected_source_revision, evidence_source)
            pre_write_source_check["name"] = "source_revision_pre_write"
            checks.append(pre_write_source_check)
        evidence = build_evidence(checks, source=evidence_source, collection=collection)
        if output_directory["status"] == "pass":
            try:
                write_evidence(args.output, evidence)
            except OSError as exc:
                checks.append({"name": "evidence_output", "status": "fail", "detail": str(exc)})
                evidence = build_evidence(checks, source=evidence_source, collection=collection)
    print(json.dumps(evidence, sort_keys=True))
    return 0 if evidence["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
