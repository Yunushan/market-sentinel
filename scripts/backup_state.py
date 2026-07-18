from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterator


BACKUP_PREFIX = "market-sentinel-state-"
MANIFEST_SUFFIX = ".json"
SCHEMA_VERSION = 1


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_relative(path: Path, source: Path) -> PurePosixPath:
    relative = path.relative_to(source)
    value = PurePosixPath(relative.as_posix())
    if value.is_absolute() or ".." in value.parts or not value.parts:
        raise RuntimeError(f"unsafe backup path: {relative}")
    return value


def _iter_regular_files(source: Path) -> Iterator[tuple[Path, PurePosixPath]]:
    for root, directory_names, file_names in os.walk(source, followlinks=False):
        root_path = Path(root)
        for directory_name in directory_names:
            candidate = root_path / directory_name
            if candidate.is_symlink():
                raise RuntimeError(f"refusing to back up symbolic link: {candidate}")
        directory_names.sort()
        for file_name in sorted(file_names):
            candidate = root_path / file_name
            if candidate.is_symlink() or not candidate.is_file():
                raise RuntimeError(f"refusing to back up non-regular file: {candidate}")
            yield candidate, _safe_relative(candidate, source)


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=".market-sentinel-", suffix=".json", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, 0o600)
        os.replace(temporary_path, path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def _validate_locations(source: Path, destination: Path) -> tuple[Path, Path]:
    source = source.resolve()
    destination = destination.resolve()
    if not source.is_dir():
        raise ValueError(f"backup source is not a directory: {source}")
    if destination.is_relative_to(source):
        raise ValueError("backup destination must not be inside the source directory")
    destination.mkdir(parents=True, exist_ok=True)
    if not destination.is_dir():
        raise ValueError(f"backup destination is not a directory: {destination}")
    os.chmod(destination, 0o700)
    return source, destination


def _prune_backups(destination: Path, retain: int) -> list[str]:
    backups = sorted(destination.glob(f"{BACKUP_PREFIX}*.tar.gz"), key=lambda path: path.stat().st_mtime, reverse=True)
    removed: list[str] = []
    for path in backups[retain:]:
        path.unlink()
        path.with_name(path.name + MANIFEST_SUFFIX).unlink(missing_ok=True)
        removed.append(path.name)
    return removed


def create_backup(source: Path, destination: Path, retain: int = 14) -> dict[str, Any]:
    if retain < 1:
        raise ValueError("retain must be at least one")
    source, destination = _validate_locations(source, destination)
    now = _utc_now()
    archive_name = f"{BACKUP_PREFIX}{now.strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(4)}.tar.gz"
    archive_path = destination / archive_name
    descriptor, temporary_name = tempfile.mkstemp(prefix=".market-sentinel-", suffix=".tar.gz", dir=destination)
    temporary_path = Path(temporary_name)
    file_count = 0
    uncompressed_bytes = 0
    try:
        with os.fdopen(descriptor, "wb") as raw_handle:
            with tarfile.open(fileobj=raw_handle, mode="w:gz", format=tarfile.PAX_FORMAT) as archive:
                for file_path, relative_path in _iter_regular_files(source):
                    file_count += 1
                    uncompressed_bytes += file_path.stat().st_size
                    with file_path.open("rb") as input_handle:
                        member = archive.gettarinfo(str(file_path), arcname=str(relative_path))
                        archive.addfile(member, input_handle)
            raw_handle.flush()
            os.fsync(raw_handle.fileno())
        os.chmod(temporary_path, 0o600)
        os.replace(temporary_path, archive_path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise

    manifest = {
        "archive": archive_name,
        "created_at": now.isoformat().replace("+00:00", "Z"),
        "file_count": file_count,
        "schema_version": SCHEMA_VERSION,
        "sha256": _sha256(archive_path),
        "source_name": source.name,
        "uncompressed_bytes": uncompressed_bytes,
    }
    _write_json_atomic(archive_path.with_name(archive_path.name + MANIFEST_SUFFIX), manifest)
    manifest["pruned"] = _prune_backups(destination, retain)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Create an integrity-manifested MarketSentinel state backup with atomic publication.")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--retain", type=int, default=14)
    args = parser.parse_args()
    try:
        result = create_backup(args.source, args.destination, args.retain)
    except (OSError, RuntimeError, ValueError, tarfile.TarError) as exc:
        raise SystemExit(f"State backup failed: {exc}") from exc
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
