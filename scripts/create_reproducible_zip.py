#!/usr/bin/env python3
"""Create a sorted ZIP with normalized metadata and SOURCE_DATE_EPOCH time."""

from __future__ import annotations

import argparse
import os
import shutil
import stat
import time
from pathlib import Path, PurePosixPath
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo


ZIP_MIN_EPOCH = 315_532_800  # 1980-01-01T00:00:00Z, the earliest ZIP timestamp.
ZIP_MAX_EPOCH = 4_354_819_198  # 2107-12-31T23:59:58Z, the latest ZIP timestamp.


def normalized_zip_datetime(epoch: int | str | None = None) -> tuple[int, int, int, int, int, int]:
    raw = os.environ.get("SOURCE_DATE_EPOCH", str(ZIP_MIN_EPOCH)) if epoch is None else str(epoch)
    try:
        parsed = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"SOURCE_DATE_EPOCH must be an integer; got {raw!r}") from exc
    clamped = min(max(parsed, ZIP_MIN_EPOCH), ZIP_MAX_EPOCH)
    value = time.gmtime(clamped)
    # ZIP stores seconds at two-second precision. Normalize explicitly rather
    # than relying on an implementation-specific truncation during writing.
    return (value.tm_year, value.tm_mon, value.tm_mday, value.tm_hour, value.tm_min, value.tm_sec & ~1)


def _archive_prefix(value: str | None) -> PurePosixPath | None:
    if value is None or not value.strip():
        return None
    prefix = PurePosixPath(value.strip().replace("\\", "/"))
    if prefix.is_absolute() or ".." in prefix.parts or str(prefix) in {"", "."}:
        raise ValueError(f"archive prefix must be a safe relative path; got {value!r}")
    return prefix


def create_reproducible_zip(
    source_dir: Path,
    output_path: Path,
    *,
    prefix: str | None = None,
    epoch: int | str | None = None,
) -> Path:
    source_dir = source_dir.resolve()
    output_path = output_path.resolve()
    if not source_dir.is_dir():
        raise ValueError(f"ZIP source directory does not exist: {source_dir}")
    archive_prefix = _archive_prefix(prefix)
    timestamp = normalized_zip_datetime(epoch)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_name(f".{output_path.name}.tmp")
    if temporary_path.exists():
        temporary_path.unlink()

    try:
        with ZipFile(temporary_path, "w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
            for path in sorted(source_dir.rglob("*"), key=lambda item: item.relative_to(source_dir).as_posix()):
                if path.is_symlink():
                    raise ValueError(f"ZIP source contains unsupported symbolic link: {path}")
                if not path.is_file():
                    continue
                relative = PurePosixPath(path.relative_to(source_dir).as_posix())
                archive_name = archive_prefix / relative if archive_prefix is not None else relative
                info = ZipInfo(archive_name.as_posix(), date_time=timestamp)
                info.create_system = 3
                info.compress_type = ZIP_DEFLATED
                info.external_attr = ((stat.S_IFREG | 0o644) << 16) | 0x20
                info.extra = b""
                with path.open("rb") as source, archive.open(info, "w") as destination:
                    shutil.copyfileobj(source, destination)
        temporary_path.replace(output_path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()
    return output_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--prefix")
    parser.add_argument("--epoch", help="Unix timestamp; defaults to SOURCE_DATE_EPOCH.")
    args = parser.parse_args()
    try:
        output = create_reproducible_zip(
            args.source_dir,
            args.output,
            prefix=args.prefix,
            epoch=args.epoch,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print(f"[ok] reproducible ZIP ({output})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
