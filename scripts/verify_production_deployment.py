from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import time
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


def _run_command(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, check=False, timeout=15)


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
            "--property=ExecMainExitTimestampUSec",
            "--value",
        ]
    )
    values = [value.strip() for value in completion.stdout.splitlines()]
    result, exit_status, completed_at_us = (values + ["", "", ""])[:3]
    try:
        backup_age_seconds = clock() - (int(completed_at_us) / 1_000_000)
    except ValueError:
        backup_age_seconds = float("inf")
    completed = (
        completion.returncode == 0
        and result == "success"
        and exit_status == "0"
        and completed_at_us not in {"", "0"}
        and backup_age_seconds >= -BACKUP_MAX_FUTURE_SKEW_SECONDS
        and backup_age_seconds <= BACKUP_MAX_AGE_SECONDS
    )
    checks.append(
        {
            "name": "systemd_recent_success_market-sentinel-backup.service",
            "status": "pass" if completed else "fail",
            "detail": (
                f"result={result or 'unknown'}; exit_status={exit_status or 'unknown'}; "
                f"completed_at_us={completed_at_us or 'unknown'}; backup_age_seconds={backup_age_seconds:.0f}; "
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
        if exc.code != 401:
            raise RuntimeError(f"unauthenticated public proxy request returned HTTP {exc.code}, expected 401") from exc

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


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect read-only MarketSentinel production deployment evidence.")
    parser.add_argument("--loopback-url", default="http://127.0.0.1:8765/api/health")
    parser.add_argument("--token", default=os.environ.get("MARKET_SENTINEL_API_TOKEN", ""))
    parser.add_argument("--expected-version", default="")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--skip-systemd", action="store_true")
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
    print(json.dumps({"status": "ok" if passed else "failed", "checks": checks}, sort_keys=True))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
