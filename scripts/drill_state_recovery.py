"""Verify a separately copied backup and measure an isolated application restore.

Run this on a recovery host after transferring an archive and its manifest by
an operator-managed encrypted channel. This command does not transfer backups,
prove host separation, or measure incident-wide RTO.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

if __package__:
    from scripts.restore_state_backup import _manifest_created_at, restore_backup, verify_backup
    from scripts.verify_restored_state import application_check_valid, isolated_environment
else:  # Supports direct invocation from the documented deployment checkout.
    from restore_state_backup import _manifest_created_at, restore_backup, verify_backup
    from verify_restored_state import application_check_valid, isolated_environment


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SHA256_HEX = re.compile(r"[0-9a-f]{64}\Z")
MAX_FUTURE_SKEW_SECONDS = 60.0
APPLICATION_TIMEOUT_SECONDS = 60.0


def _positive_limit(value: float, name: str) -> float:
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be finite and positive")
    return value


def _restored_inventory(state: Path) -> tuple[int, int]:
    files = 0
    total_bytes = 0
    for path in state.rglob("*"):
        if path.is_symlink():
            raise RuntimeError("restored state contains a symbolic link")
        if path.is_file():
            files += 1
            total_bytes += path.stat().st_size
    return files, total_bytes


def run_recovery_drill(
    archive: Path,
    destination: Path,
    *,
    expected_sha256: str,
    expected_created_at: str,
    frontend_dir: Path,
    expected_version: str,
    expected_source_revision: str,
    expected_frontend_sha256: str,
    max_backup_age_seconds: float,
    max_restore_validation_seconds: float,
    clock: Callable[[], float] = time.time,
    monotonic: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Return bounded drill measurements only after a verified application boot."""
    if not isinstance(expected_sha256, str) or SHA256_HEX.fullmatch(expected_sha256) is None:
        raise ValueError("expected SHA-256 must be 64 lowercase hexadecimal characters")
    if not isinstance(expected_created_at, str) or not expected_created_at:
        raise ValueError("expected backup creation timestamp is required")
    if not isinstance(expected_frontend_sha256, str) or SHA256_HEX.fullmatch(expected_frontend_sha256) is None:
        raise ValueError("expected frontend SHA-256 must be 64 lowercase hexadecimal characters")
    if not expected_version or not expected_source_revision:
        raise ValueError("expected version and source revision are required")
    max_backup_age_seconds = _positive_limit(max_backup_age_seconds, "max backup age")
    max_restore_validation_seconds = _positive_limit(
        max_restore_validation_seconds, "max restore-validation time",
    )
    archive = Path(archive)
    destination = Path(destination)
    if archive.is_symlink():
        raise ValueError("backup archive must not be a symbolic link")
    if os.path.lexists(destination):
        raise ValueError("restore destination must not already exist")

    start_wall = clock()
    started_at = datetime.fromtimestamp(start_wall, timezone.utc).isoformat().replace("+00:00", "Z")
    start = monotonic()
    manifest = verify_backup(archive)
    if manifest["sha256"] != expected_sha256:
        raise ValueError("copied backup does not match the independently supplied SHA-256")
    if manifest.get("created_at") != expected_created_at:
        raise ValueError("copied backup timestamp does not match the independently supplied source inventory")
    backup_created_at = _manifest_created_at(manifest)
    backup_age_seconds = start_wall - backup_created_at.timestamp()
    if not -MAX_FUTURE_SKEW_SECONDS <= backup_age_seconds <= max_backup_age_seconds:
        raise ValueError("copied backup is outside the configured age limit")

    restored_manifest = restore_backup(archive, destination)
    if restored_manifest != manifest:
        raise RuntimeError("backup changed between verification and restore")
    restored_files, restored_bytes = _restored_inventory(destination)
    if restored_files != manifest["file_count"] or restored_bytes != manifest["verified_bytes"]:
        raise RuntimeError("restored file inventory does not match the verified backup")

    remaining = max_restore_validation_seconds - (monotonic() - start)
    if remaining <= 0:
        raise RuntimeError("restore and validation exceeded the configured time limit")
    try:
        result = subprocess.run(
            [
                sys.executable, "-I", "-B", str(PROJECT_ROOT / "scripts" / "verify_restored_state.py"),
                "--state", str(destination.resolve()), "--frontend-dir", str(Path(frontend_dir).resolve()),
            ],
            cwd=destination.parent,
            env=isolated_environment(destination.resolve()),
            capture_output=True,
            text=True,
            timeout=min(APPLICATION_TIMEOUT_SECONDS, remaining),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("restored application exceeded its validation time limit") from exc
    if result.returncode != 0 or len(result.stdout) > 4096:
        raise RuntimeError("restored application validation failed")
    try:
        application = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("restored application returned invalid validation data") from exc
    if not application_check_valid(
        application,
        version=expected_version,
        revision=expected_source_revision,
        frontend_sha256=expected_frontend_sha256,
    ):
        raise RuntimeError("restored application validation or runtime identity is invalid")
    restore_validation_seconds = monotonic() - start
    if restore_validation_seconds > max_restore_validation_seconds:
        raise RuntimeError("restore and validation exceeded the configured time limit")

    return {
        "schema_version": 1,
        "scope": "copied_backup_restore_and_isolated_application_probe",
        "started_at": started_at,
        "completed_at": datetime.fromtimestamp(clock(), timezone.utc).isoformat().replace("+00:00", "Z"),
        "archive": archive.name,
        "backup_sha256": expected_sha256,
        "backup_created_at": backup_created_at.isoformat().replace("+00:00", "Z"),
        "backup_age_seconds": round(backup_age_seconds, 3),
        "max_backup_age_seconds": max_backup_age_seconds,
        "restore_validation_seconds": round(restore_validation_seconds, 3),
        "max_restore_validation_seconds": max_restore_validation_seconds,
        "restored_file_count": restored_files,
        "restored_bytes": restored_bytes,
        "application": application,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--expected-created-at", required=True)
    parser.add_argument("--frontend-dir", type=Path, required=True)
    parser.add_argument("--expected-version", required=True)
    parser.add_argument("--expected-source-revision", required=True)
    parser.add_argument("--expected-frontend-sha256", required=True)
    parser.add_argument("--max-backup-age-seconds", type=float, required=True)
    parser.add_argument("--max-restore-validation-seconds", type=float, required=True)
    args = parser.parse_args()
    try:
        report = run_recovery_drill(
            args.archive,
            args.destination,
            expected_sha256=args.expected_sha256,
            expected_created_at=args.expected_created_at,
            frontend_dir=args.frontend_dir,
            expected_version=args.expected_version,
            expected_source_revision=args.expected_source_revision,
            expected_frontend_sha256=args.expected_frontend_sha256,
            max_backup_age_seconds=args.max_backup_age_seconds,
            max_restore_validation_seconds=args.max_restore_validation_seconds,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise SystemExit(f"Recovery drill failed: {exc}") from exc
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
