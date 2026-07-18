from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from stat import S_IFDIR, S_IFREG, S_IMODE, S_ISDIR, S_ISREG
from typing import Any, Callable
from urllib.error import HTTPError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

if __package__:
    from scripts.verify_service_health import check_health
else:  # Supports the documented `python /path/to/scripts/verify_production_deployment.py` invocation.
    from verify_service_health import check_health


CommandRunner = Callable[[list[str]], subprocess.CompletedProcess[str]]
REQUIRED_UNITS = (
    "market-sentinel-web.service",
    "market-sentinel-health.timer",
    "market-sentinel-backup.timer",
)
REQUIRED_PROXY_HEADERS = (
    "strict-transport-security",
    "content-security-policy",
    "x-content-type-options",
    "x-frame-options",
    "referrer-policy",
    "permissions-policy",
    "cross-origin-opener-policy",
    "cross-origin-resource-policy",
)
BACKUP_MAX_AGE_SECONDS = 26 * 60 * 60
BACKUP_MAX_FUTURE_SKEW_SECONDS = 5 * 60
REQUIRED_PRIVATE_PATHS = (
    (Path("/etc/market-sentinel/market-sentinel.env"), S_IFREG, True),
    (Path("/var/lib/market-sentinel"), S_IFDIR, False),
    (Path("/var/lib/market-sentinel-backups"), S_IFDIR, False),
)


def _run_command(args: list[str]) -> subprocess.CompletedProcess[str]:
    environment = {**os.environ, "LC_ALL": "C", "TZ": "UTC"}
    return subprocess.run(args, capture_output=True, text=True, check=False, timeout=15, env=environment)


def _systemd_timestamp_seconds(value: str) -> float:
    normalized = value.strip()
    for pattern in ("%a %Y-%m-%d %H:%M:%S UTC", "%a %Y-%m-%d %H:%M:%S.%f UTC"):
        try:
            return datetime.strptime(normalized, pattern).replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            continue
    raise ValueError(f"invalid systemd UTC timestamp: {normalized or 'missing'}")


def check_filesystem_permissions(
    stat_reader: Callable[[Path], object] = lambda path: path.stat(),
) -> list[dict[str, Any]]:
    return [
        _check_private_path(path, expected_type, require_root_owner, stat_reader)
        for path, expected_type, require_root_owner in REQUIRED_PRIVATE_PATHS
    ]


def _check_private_path(
    path: Path,
    expected_type: int,
    require_root_owner: bool,
    stat_reader: Callable[[Path], object],
) -> dict[str, Any]:
    try:
        metadata = stat_reader(path)
        mode = int(getattr(metadata, "st_mode"))
        owner = int(getattr(metadata, "st_uid"))
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
    return _check_private_path(output_path.parent, S_IFDIR, True, stat_reader)


def check_systemd(
    runner: CommandRunner = _run_command,
    clock: Callable[[], float] = time.time,
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for unit in REQUIRED_UNITS:
        for command in ("is-active", "is-enabled"):
            result = runner(["systemctl", command, unit])
            checks.append(
                {
                    "name": f"systemd_{command}_{unit}",
                    "status": "pass" if result.returncode == 0 else "fail",
                    "detail": (result.stdout or result.stderr).strip(),
                }
            )
    completion = runner(
        [
            "systemctl",
            "show",
            "market-sentinel-backup.service",
            "--property=Result",
            "--property=ExecMainStatus",
            "--property=ExecMainExitTimestamp",
            "--value",
        ]
    )
    values = [value.strip() for value in completion.stdout.splitlines()]
    result, exit_status, completed_at = (values + ["", "", ""])[:3]
    try:
        backup_age_seconds = clock() - _systemd_timestamp_seconds(completed_at)
    except ValueError:
        backup_age_seconds = float("inf")
    completed = (
        completion.returncode == 0
        and result == "success"
        and exit_status == "0"
        and completed_at not in {"", "n/a"}
        and backup_age_seconds >= -BACKUP_MAX_FUTURE_SKEW_SECONDS
        and backup_age_seconds <= BACKUP_MAX_AGE_SECONDS
    )
    checks.append(
        {
            "name": "systemd_recent_success_market-sentinel-backup.service",
            "status": "pass" if completed else "fail",
            "detail": (
                f"result={result or 'unknown'}; exit_status={exit_status or 'unknown'}; "
                f"completed_at={completed_at or 'unknown'}; backup_age_seconds={backup_age_seconds:.0f}; "
                f"max_age_seconds={BACKUP_MAX_AGE_SECONDS}; max_future_skew_seconds={BACKUP_MAX_FUTURE_SKEW_SECONDS}"
            ),
        }
    )
    return checks


def check_loopback(url: str, token: str, timeout: float, expected_version: str = "") -> dict[str, Any]:
    payload = check_health(url, token, timeout)
    version = str(payload["api_version"])
    if expected_version and version != expected_version:
        raise RuntimeError(f"health endpoint reported version {version}, expected {expected_version}")
    return {"name": "loopback_health", "status": "pass", "api_version": version}


def check_public_proxy(
    url: str,
    username: str,
    password: str,
    timeout: float,
    expected_version: str = "",
) -> dict[str, Any]:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("public URL must be an absolute https URL")
    if not username or not password:
        raise ValueError("public proxy verification requires non-empty Basic Auth credentials")
    health_url = urljoin(url.rstrip("/") + "/", "api/health")
    try:
        with urlopen(Request(health_url, headers={"Accept": "application/json"}, method="GET"), timeout=timeout):
            raise RuntimeError("unauthenticated public proxy request was accepted")
    except HTTPError as exc:
        try:
            if exc.code != 401:
                raise RuntimeError(f"unauthenticated public proxy request returned HTTP {exc.code}, expected 401") from exc
        finally:
            exc.close()

    headers = {"Accept": "application/json"}
    encoded = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    headers["Authorization"] = f"Basic {encoded}"
    with urlopen(Request(health_url, headers=headers, method="GET"), timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
        header_names = {name.lower() for name in response.headers}
        missing = [name for name in REQUIRED_PROXY_HEADERS if name not in header_names]
        if response.status != 200 or payload.get("status") != "ok":
            raise RuntimeError("public proxy health endpoint did not report status=ok")
        if expected_version and str(payload.get("api_version", "")) != expected_version:
            raise RuntimeError(
                f"public proxy reported version {payload.get('api_version')}, expected {expected_version}"
            )
        if response.headers.get("Cache-Control") != "no-store":
            raise RuntimeError("public proxy health endpoint is missing Cache-Control: no-store")
        if missing:
            raise RuntimeError("public proxy is missing security headers: " + ", ".join(missing))
    return {"name": "public_https_proxy", "status": "pass", "api_version": payload.get("api_version")}


def write_evidence(path: Path, payload: dict[str, Any]) -> None:
    """Atomically persist redacted deployment evidence with private permissions."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
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
    except OSError:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect read-only MarketSentinel production deployment evidence.")
    parser.add_argument("--loopback-url", default="http://127.0.0.1:8765/api/health")
    parser.add_argument("--token", default=os.environ.get("MARKET_SENTINEL_API_TOKEN", ""))
    parser.add_argument("--expected-version", default="")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--skip-systemd", action="store_true")
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional path for an atomically written, mode-0600 JSON evidence record.",
    )
    parser.add_argument("--public-url", default="")
    parser.add_argument("--public-basic-user", default=os.environ.get("MARKET_SENTINEL_PUBLIC_BASIC_USER", ""))
    parser.add_argument(
        "--public-basic-password-env",
        default="MARKET_SENTINEL_PUBLIC_BASIC_PASSWORD",
        help="Environment variable containing the required public Basic Auth password when --public-url is set.",
    )
    args = parser.parse_args()

    checks: list[dict[str, Any]] = []
    try:
        if not args.skip_systemd:
            checks.extend(check_systemd())
            checks.extend(check_filesystem_permissions())
        checks.append(check_loopback(args.loopback_url, args.token, args.timeout, args.expected_version))
        if args.public_url:
            password = os.environ.get(args.public_basic_password_env, "")
            checks.append(
                check_public_proxy(
                    args.public_url,
                    args.public_basic_user,
                    password,
                    args.timeout,
                    args.expected_version,
                )
            )
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError, subprocess.TimeoutExpired) as exc:
        checks.append({"name": "deployment_verifier", "status": "fail", "detail": str(exc)})

    passed = all(check["status"] == "pass" for check in checks)
    evidence = {"status": "ok" if passed else "failed", "checks": checks}
    if args.output:
        output_directory = check_evidence_output_directory(args.output)
        checks.append(output_directory)
        evidence = {"status": "ok" if all(check["status"] == "pass" for check in checks) else "failed", "checks": checks}
        if output_directory["status"] == "pass":
            try:
                write_evidence(args.output, evidence)
            except OSError as exc:
                checks.append({"name": "evidence_output", "status": "fail", "detail": str(exc)})
                evidence = {"status": "failed", "checks": checks}
    print(json.dumps(evidence, sort_keys=True))
    return 0 if evidence["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
