"""Journal an operator-controlled rollback and reactivation on the production host.

This program never switches releases.  The operator performs both changes in a
separate session; success is written only after the running service and deployed
files independently show the expected current -> rollback -> current sequence.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.deployment_identity import (  # noqa: E402
    COMMIT_SHA,
    SHA256_HEX,
    canonical_https_origin,
    frontend_tree_sha256,
)

if __package__:
    from scripts.verify_production_deployment import (  # noqa: E402
        PROVIDER_SLUG,
        ROLLBACK_DRILL_REPORT_TYPE,
        check_deployment_host_identity,
        check_evidence_output_directory,
        check_rollback_drill,
        _fsync_parent_directory,
        source_identity,
        write_evidence,
    )
    from scripts.verify_service_health import check_health  # noqa: E402
else:
    from verify_production_deployment import (  # noqa: E402
        PROVIDER_SLUG,
        ROLLBACK_DRILL_REPORT_TYPE,
        check_deployment_host_identity,
        check_evidence_output_directory,
        check_rollback_drill,
        _fsync_parent_directory,
        source_identity,
        write_evidence,
    )
    from verify_service_health import check_health  # noqa: E402


DEFAULT_REPORT = Path("/var/lib/market-sentinel-rollback-drills/latest.json")
DEFAULT_DEPLOYMENT_ROOT = Path("/opt/market-sentinel")
DEFAULT_HEALTH_URL = "http://127.0.0.1:8765/api/health"
SERVICE = "market-sentinel-web.service"
INVOCATION_ID = re.compile(r"^[0-9a-f]{32}$")
STABLE_VERSION = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
CONFIRMATION = "I_UNDERSTAND_THIS_RESTARTS_PRODUCTION"


@dataclass(frozen=True)
class Release:
    version: str
    revision: str
    frontend_sha256: str


@dataclass(frozen=True)
class ServiceInvocation:
    invocation_id: str
    pid: int


def _utc_now(clock: Callable[[], float]) -> str:
    return datetime.fromtimestamp(clock(), timezone.utc).isoformat().replace("+00:00", "Z")


def observe_web_service() -> ServiceInvocation:
    """Identify the active systemd invocation, not merely a reused process ID."""

    result = subprocess.run(
        ["systemctl", "show", SERVICE, "--property=ActiveState", "--property=MainPID",
         "--property=InvocationID"],
        capture_output=True, text=True, check=False, timeout=10,
    )
    pairs = [line.partition("=") for line in result.stdout.splitlines()]
    fields = {key: value for key, separator, value in pairs if separator}
    if (
        result.returncode
        or len(pairs) != 3
        or set(fields) != {"ActiveState", "MainPID", "InvocationID"}
        or fields["ActiveState"] != "active"
    ):
        raise RuntimeError("production web service is not active")
    try:
        pid = int(fields["MainPID"])
    except ValueError as exc:
        raise RuntimeError("production web service has no usable MainPID") from exc
    invocation_id = fields["InvocationID"].strip().lower()
    if pid <= 0 or not INVOCATION_ID.fullmatch(invocation_id):
        raise RuntimeError("production web service has no usable invocation identity")
    return ServiceInvocation(invocation_id, pid)


def _validate_release(release: Release, label: str) -> None:
    if not STABLE_VERSION.fullmatch(release.version) or not COMMIT_SHA.fullmatch(release.revision):
        raise ValueError(f"{label} stable version and 40-character lowercase revision are required")
    if not SHA256_HEX.fullmatch(release.frontend_sha256):
        raise ValueError(f"{label} frontend SHA-256 must be 64 lowercase hexadecimal characters")


def _validate_report_target(path: Path) -> None:
    if path != DEFAULT_REPORT:
        raise ValueError(f"rollback report must use the dedicated path {DEFAULT_REPORT}")
    if check_evidence_output_directory(path)["status"] != "pass":
        raise ValueError("rollback report parent must be an existing private root-owned directory without symlinks")
    if path.is_symlink():
        raise ValueError("rollback report must not be a symbolic link")
    if path.exists():
        metadata = path.lstat()
        if not path.is_file() or metadata.st_mode & 0o077 or (os.name == "posix" and metadata.st_uid != 0):
            raise ValueError("existing rollback report must be a private root-owned regular file")


def _validate_health_url(value: str) -> None:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("health URL must be an HTTP loopback /api/health endpoint") from exc
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or not port
        or parsed.path != "/api/health"
        or parsed.query
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError("health URL must be an HTTP loopback /api/health endpoint")


def _archive_previous_report(path: Path) -> Path | None:
    """Preserve the exact previous journal bytes before invalidating latest.json."""

    if not path.exists():
        return None
    raw = path.read_bytes()
    if not raw or len(raw) > 64 * 1024:
        raise ValueError("existing rollback report is empty or oversized; preserve it manually")
    archive = path.with_name(f"{path.stem}.previous-{uuid.uuid4()}{path.suffix}")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(archive, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            if os.name == "posix":
                os.fchmod(handle.fileno(), 0o600)
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        _fsync_parent_directory(archive)
    except OSError:
        archive.unlink(missing_ok=True)
        raise
    return archive


def _observe_healthy_release(
    release: Release,
    *,
    prior_invocation: ServiceInvocation | None,
    deployment_root: Path,
    frontend_dir: Path,
    health_url: str,
    token: str,
    timeout: float,
    service_observer: Callable[[], ServiceInvocation],
    health_probe: Callable[[str, str, float], dict],
    source_reader: Callable[[Path], dict[str, str]],
    frontend_digest: Callable[[Path], str],
) -> tuple[ServiceInvocation, dict[str, str]]:
    before = service_observer()
    if prior_invocation is not None and before.invocation_id == prior_invocation.invocation_id:
        raise RuntimeError("web service invocation did not change after the operator's release switch")
    payload = health_probe(health_url, token, timeout)
    after = service_observer()
    if before != after:
        raise RuntimeError("web service restarted during the health observation")
    if (
        payload.get("status") != "ok"
        or ("readiness" in payload and (
            not isinstance(payload["readiness"], dict)
            or payload["readiness"].get("ready") is not True
        ))
        or
        payload.get("api_version") != release.version
        or payload.get("runtime_source_revision") != release.revision
        or payload.get("runtime_frontend_sha256") != release.frontend_sha256
    ):
        raise RuntimeError("running web service identity differs from the expected release")
    source = source_reader(deployment_root)
    if (
        source.get("git_revision") != release.revision
        or source.get("git_revision_status") != "ok"
        or source.get("git_worktree_status") != "clean"
        or source.get("project_version") != release.version
    ):
        raise RuntimeError("active clean deployment checkout differs from the running release")
    if frontend_digest(frontend_dir) != release.frontend_sha256:
        raise RuntimeError("active frontend files differ from the running release")
    return before, {
        "api_version": release.version,
        "runtime_source_revision": release.revision,
        "runtime_frontend_sha256": release.frontend_sha256,
    }


def run_rollback_drill(
    *,
    current: Release,
    rollback: Release,
    deployment_provider: str,
    host_identity_sha256: str,
    public_origin: str,
    report_path: Path,
    deployment_root: Path,
    frontend_dir: Path,
    health_url: str,
    token: str,
    timeout: float = 5.0,
    prompt: Callable[[str], str] = input,
    service_observer: Callable[[], ServiceInvocation] = observe_web_service,
    health_probe: Callable[[str, str, float], dict] = check_health,
    source_reader: Callable[[Path], dict[str, str]] = source_identity,
    frontend_digest: Callable[[Path], str] = frontend_tree_sha256,
    report_writer: Callable[[Path, dict], None] = write_evidence,
    clock: Callable[[], float] = time.time,
) -> dict:
    """Record exactly five steps; partial/failed attempts cannot be score evidence."""

    _validate_release(current, "current")
    _validate_release(rollback, "rollback")
    if (
        rollback.revision == current.revision
        or tuple(map(int, rollback.version.split(".")))
        >= tuple(map(int, current.version.split(".")))
    ):
        raise ValueError("rollback must be a different, older stable release")
    if not PROVIDER_SLUG.fullmatch(deployment_provider):
        raise ValueError("deployment provider must be a lowercase provider slug")
    if not SHA256_HEX.fullmatch(host_identity_sha256):
        raise ValueError("host identity must be a lowercase SHA-256 digest")
    if public_origin != canonical_https_origin(public_origin):
        raise ValueError("public origin must be a canonical HTTPS origin")
    if not token.strip() or any(character in token for character in "\r\n"):
        raise ValueError("an observability token is required")
    _validate_health_url(health_url)
    if not 0 < timeout <= 30:
        raise ValueError("health timeout must be between 0 and 30 seconds")
    _validate_report_target(report_path)

    started_at = _utc_now(clock)
    report: dict = {
        "schema_version": 1,
        "report_type": ROLLBACK_DRILL_REPORT_TYPE,
        "drill_id": str(uuid.uuid4()),
        "started_at": started_at,
        "completed_at": started_at,
        "deployment_provider": deployment_provider,
        "host_identity_sha256": host_identity_sha256,
        "public_origin": public_origin,
        "current_revision": current.revision,
        "rollback_revision": rollback.revision,
        "final_revision": "",
        "status": "in_progress",
        "steps": [],
    }
    # Replaces a previous successful latest.json before any operator action.
    _archive_previous_report(report_path)
    report_writer(report_path, report)
    operator_may_have_switched = False

    def step(name: str, release: Release, observation: dict[str, str] | None, observed_at: str) -> dict:
        return {
            "name": name,
            "status": "pass",
            "revision": release.revision,
            "observed_at": observed_at,
            "api_version": observation["api_version"] if observation else "",
            "runtime_source_revision": observation["runtime_source_revision"] if observation else "",
            "runtime_frontend_sha256": observation["runtime_frontend_sha256"] if observation else "",
        }

    try:
        first_invocation, first_health = _observe_healthy_release(
            current, prior_invocation=None, deployment_root=deployment_root,
            frontend_dir=frontend_dir, health_url=health_url, token=token,
            timeout=timeout, service_observer=service_observer,
            health_probe=health_probe, source_reader=source_reader,
            frontend_digest=frontend_digest,
        )
        report["steps"].append(step("current_release_healthy", current, first_health, _utc_now(clock)))
        report_writer(report_path, report)

        operator_may_have_switched = True
        if prompt(
            f"Activate reviewed rollback {rollback.revision}, restart {SERVICE}, then type "
            f"ACTIVATED {rollback.revision}: "
        ).strip() != f"ACTIVATED {rollback.revision}":
            raise RuntimeError("rollback activation was not explicitly confirmed")
        rollback_invocation = service_observer()
        if rollback_invocation.invocation_id == first_invocation.invocation_id:
            raise RuntimeError("web service invocation did not change for rollback")
        rollback_activated_at = _utc_now(clock)
        observed_invocation, rollback_health = _observe_healthy_release(
            rollback, prior_invocation=first_invocation, deployment_root=deployment_root,
            frontend_dir=frontend_dir, health_url=health_url, token=token,
            timeout=timeout, service_observer=service_observer,
            health_probe=health_probe, source_reader=source_reader,
            frontend_digest=frontend_digest,
        )
        if observed_invocation != rollback_invocation:
            raise RuntimeError("web service invocation changed during rollback verification")
        report["steps"].extend((
            step("rollback_release_activated", rollback, None, rollback_activated_at),
            step("rollback_release_healthy", rollback, rollback_health, _utc_now(clock)),
        ))
        report_writer(report_path, report)

        if prompt(
            f"Reactivate reviewed current release {current.revision}, restart {SERVICE}, "
            f"then type REACTIVATED {current.revision}: "
        ).strip() != f"REACTIVATED {current.revision}":
            raise RuntimeError("current release reactivation was not explicitly confirmed")
        current_invocation = service_observer()
        if current_invocation.invocation_id == rollback_invocation.invocation_id:
            raise RuntimeError("web service invocation did not change for reactivation")
        current_activated_at = _utc_now(clock)
        observed_invocation, current_health = _observe_healthy_release(
            current, prior_invocation=rollback_invocation, deployment_root=deployment_root,
            frontend_dir=frontend_dir, health_url=health_url, token=token,
            timeout=timeout, service_observer=service_observer,
            health_probe=health_probe, source_reader=source_reader,
            frontend_digest=frontend_digest,
        )
        if observed_invocation != current_invocation:
            raise RuntimeError("web service invocation changed during reactivation verification")
        report["steps"].extend((
            step("current_release_reactivated", current, None, current_activated_at),
            step("current_release_healthy_after_reactivation", current, current_health, _utc_now(clock)),
        ))
        report["completed_at"] = _utc_now(clock)
        if (
            datetime.fromisoformat(report["completed_at"].replace("Z", "+00:00"))
            - datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        ).total_seconds() > 3600:
            raise RuntimeError("rollback drill exceeded the verifier's one-hour limit")
        report["final_revision"] = current.revision
        report["status"] = "ok"
        report_writer(report_path, report)
        return report
    except (Exception, KeyboardInterrupt) as error:
        report["status"] = "failed"
        report["completed_at"] = _utc_now(clock)
        journal_error: Exception | None = None
        try:
            report_writer(report_path, report)
        except Exception as exc:
            journal_error = exc
        recovery_error: BaseException | None = None
        if operator_may_have_switched:
            try:
                if prompt(
                    f"Drill failed. Restore reviewed current release {current.revision} now, "
                    f"restart {SERVICE}, then type RECOVERED {current.revision}: "
                ).strip() != f"RECOVERED {current.revision}":
                    raise RuntimeError("operator did not confirm current release recovery")
                _observe_healthy_release(
                    current, prior_invocation=None, deployment_root=deployment_root,
                    frontend_dir=frontend_dir, health_url=health_url, token=token,
                    timeout=timeout, service_observer=service_observer,
                    health_probe=health_probe, source_reader=source_reader,
                    frontend_digest=frontend_digest,
                )
                report["final_revision"] = current.revision
                try:
                    report_writer(report_path, report)
                except Exception as exc:
                    journal_error = exc
            except (Exception, KeyboardInterrupt) as exc:
                recovery_error = exc
        if recovery_error is not None or journal_error is not None:
            details = [str(error)]
            if recovery_error is not None:
                details.append(f"current release recovery could not be verified: {recovery_error}")
            if journal_error is not None:
                details.append(f"failed journal could not be durably saved: {journal_error}")
            raise RuntimeError("; ".join(details)) from error
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--current-version", required=True)
    parser.add_argument("--current-revision", required=True)
    parser.add_argument("--current-frontend-sha256", required=True)
    parser.add_argument("--rollback-version", required=True)
    parser.add_argument("--rollback-revision", required=True)
    parser.add_argument("--rollback-frontend-sha256", required=True)
    parser.add_argument("--deployment-provider", required=True)
    parser.add_argument("--expected-host-id-sha256", required=True)
    parser.add_argument("--public-origin", required=True)
    parser.add_argument("--host-identity-file", type=Path, default=Path("/etc/machine-id"))
    parser.add_argument("--deployment-root", type=Path, default=DEFAULT_DEPLOYMENT_ROOT)
    parser.add_argument("--frontend-dir", type=Path, default=DEFAULT_DEPLOYMENT_ROOT / "frontend" / "dist")
    parser.add_argument("--health-url", default=DEFAULT_HEALTH_URL)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--confirm-production-drill", required=True)
    args = parser.parse_args()

    if args.confirm_production_drill != CONFIRMATION:
        parser.error(f"--confirm-production-drill must equal {CONFIRMATION}")
    if os.name != "posix" or os.geteuid() != 0 or not sys.stdin.isatty():
        parser.error("production rollback drill requires root on the Linux host and an interactive terminal")
    if os.environ.get("MARKET_SENTINEL_API_TOKEN"):
        parser.error("admin API token must not be present in the rollback drill environment")
    token = os.environ.get("MARKET_SENTINEL_OBSERVABILITY_TOKEN", "")
    host_check = check_deployment_host_identity(
        args.deployment_provider, args.expected_host_id_sha256, args.host_identity_file,
    )
    if host_check["status"] != "pass":
        parser.error(f"host identity check failed: {host_check['detail']}")
    current = Release(args.current_version, args.current_revision, args.current_frontend_sha256)
    rollback = Release(args.rollback_version, args.rollback_revision, args.rollback_frontend_sha256)
    try:
        report = run_rollback_drill(
            current=current, rollback=rollback,
            deployment_provider=args.deployment_provider,
            host_identity_sha256=args.expected_host_id_sha256,
            public_origin=args.public_origin, report_path=DEFAULT_REPORT,
            deployment_root=args.deployment_root, frontend_dir=args.frontend_dir,
            health_url=args.health_url, token=token, timeout=args.timeout,
        )
        verdict = check_rollback_drill(
            DEFAULT_REPORT, expected_version=current.version,
            expected_current_revision=current.revision,
            expected_frontend_sha256=current.frontend_sha256,
            deployment_provider=args.deployment_provider,
            host_identity_sha256=args.expected_host_id_sha256,
            public_origin=args.public_origin,
        )
        if verdict["status"] != "pass":
            raise RuntimeError(f"saved rollback report failed verifier: {verdict['detail']}")
    except (OSError, RuntimeError, ValueError, KeyboardInterrupt) as exc:
        raise SystemExit(
            f"Rollback drill failed: {exc}. Restore the current release immediately "
            "and verify its health; the report is not score evidence."
        ) from exc
    print(f"Rollback drill verified: {report['drill_id']} ({DEFAULT_REPORT})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
