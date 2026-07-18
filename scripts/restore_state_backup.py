from __future__ import annotations

import argparse
import json
import os
import shutil
import tarfile
from pathlib import Path, PurePosixPath
from typing import Any

from scripts.backup_state import SCHEMA_VERSION, _sha256


def _mkdir_private(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o700)


def _safe_member_path(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise RuntimeError(f"backup archive contains unsafe member path: {name}")
    return path


def _load_manifest(archive_path: Path) -> dict[str, Any]:
    manifest_path = archive_path.with_name(archive_path.name + ".json")
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"unable to read backup manifest: {manifest_path}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
        raise RuntimeError("backup manifest has an unsupported schema version")
    if payload.get("archive") != archive_path.name:
        raise RuntimeError("backup manifest does not match the selected archive")
    expected_sha256 = payload.get("sha256")
    if not isinstance(expected_sha256, str) or len(expected_sha256) != 64:
        raise RuntimeError("backup manifest has no usable SHA-256 checksum")
    if _sha256(archive_path) != expected_sha256:
        raise RuntimeError("backup archive checksum does not match its manifest")
    return payload


def verify_backup(archive_path: Path, max_members: int = 10_000, max_bytes: int = 1_073_741_824) -> dict[str, Any]:
    archive_path = archive_path.resolve()
    if not archive_path.is_file():
        raise ValueError(f"backup archive does not exist: {archive_path}")
    if max_members < 1 or max_bytes < 1:
        raise ValueError("max-members and max-bytes must be positive")
    manifest = _load_manifest(archive_path)
    member_count = 0
    total_size = 0
    try:
        with tarfile.open(archive_path, "r:gz") as archive:
            for member in archive:
                _safe_member_path(member.name)
                if not (member.isfile() or member.isdir()):
                    raise RuntimeError(f"backup archive contains unsupported member type: {member.name}")
                member_count += 1
                total_size += member.size
                if member_count > max_members or total_size > max_bytes:
                    raise RuntimeError("backup archive exceeds restore safety limits")
    except tarfile.TarError as exc:
        raise RuntimeError(f"backup archive cannot be read: {archive_path}") from exc
    if manifest.get("file_count") != member_count:
        raise RuntimeError("backup manifest file count does not match archive contents")
    return {**manifest, "verified_bytes": total_size}


def restore_backup(archive_path: Path, destination: Path, max_members: int = 10_000, max_bytes: int = 1_073_741_824) -> dict[str, Any]:
    manifest = verify_backup(archive_path, max_members=max_members, max_bytes=max_bytes)
    destination = destination.resolve()
    if destination.exists() and any(destination.iterdir()):
        raise ValueError(f"restore destination must be empty: {destination}")
    _mkdir_private(destination)

    with tarfile.open(archive_path, "r:gz") as archive:
        for member in archive:
            relative_path = _safe_member_path(member.name)
            target = destination.joinpath(*relative_path.parts)
            if member.isdir():
                _mkdir_private(target)
                continue
            _mkdir_private(target.parent)
            source = archive.extractfile(member)
            if source is None:
                raise RuntimeError(f"backup archive member has no file content: {member.name}")
            with source, target.open("xb") as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)
                output.flush()
                os.fsync(output.fileno())
            os.chmod(target, member.mode & 0o600 or 0o600)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify or restore a MarketSentinel state backup into an empty directory.")
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--destination", type=Path)
    parser.add_argument("--max-members", type=int, default=10_000)
    parser.add_argument("--max-bytes", type=int, default=1_073_741_824)
    args = parser.parse_args()
    try:
        if args.destination is None:
            result = verify_backup(args.archive, args.max_members, args.max_bytes)
        else:
            result = restore_backup(args.archive, args.destination, args.max_members, args.max_bytes)
    except (OSError, RuntimeError, ValueError, tarfile.TarError) as exc:
        raise SystemExit(f"State backup verification or restore failed: {exc}") from exc
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
