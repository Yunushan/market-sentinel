#!/usr/bin/env python3
"""Normalize a locally built Python sdist to reproducible tar/gzip metadata."""

from __future__ import annotations

import argparse
import gzip
import os
import tarfile
from pathlib import Path, PurePosixPath


def source_date_epoch(value: int | str | None = None) -> int:
    raw = os.environ.get("SOURCE_DATE_EPOCH") if value is None else str(value)
    if raw is None:
        raise ValueError("SOURCE_DATE_EPOCH is required when normalizing a Python sdist")
    try:
        epoch = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"SOURCE_DATE_EPOCH must be an integer; got {raw!r}") from exc
    if not 0 <= epoch <= 0xFFFFFFFF:
        raise ValueError("SOURCE_DATE_EPOCH must fit the gzip timestamp field")
    return epoch


def _safe_member_name(name: str) -> str:
    normalized = name.replace("\\", "/")
    path = PurePosixPath(normalized)
    if not normalized or normalized != name or path.is_absolute() or ".." in path.parts:
        raise ValueError(f"sdist contains an unsafe member name: {name!r}")
    return path.as_posix()


def normalize_sdist(path: Path, *, epoch: int | str | None = None) -> Path:
    path = path.resolve()
    if not path.is_file():
        raise ValueError(f"Python sdist does not exist: {path}")
    timestamp = source_date_epoch(epoch)
    temporary = path.with_name(f".{path.name}.tmp")
    if temporary.exists():
        temporary.unlink()

    try:
        with tarfile.open(path, "r:gz") as source:
            members = source.getmembers()
            names = [_safe_member_name(member.name) for member in members]
            if len(names) != len(set(names)):
                raise ValueError(f"sdist contains duplicate member names: {path.name}")
            by_name = dict(zip(names, members, strict=True))
            with temporary.open("wb") as raw_output:
                with gzip.GzipFile(
                    filename="",
                    mode="wb",
                    compresslevel=9,
                    fileobj=raw_output,
                    mtime=timestamp,
                ) as compressed_output:
                    with tarfile.open(
                        fileobj=compressed_output,
                        mode="w",
                        format=tarfile.PAX_FORMAT,
                    ) as destination:
                        for name in sorted(by_name):
                            member = by_name[name]
                            if not (member.isfile() or member.isdir()):
                                raise ValueError(
                                    f"sdist contains unsupported non-file member: {member.name}"
                                )
                            member.name = name
                            member.mtime = timestamp
                            member.uid = 0
                            member.gid = 0
                            member.uname = ""
                            member.gname = ""
                            member.mode = (
                                0o755
                                if member.isdir() or member.mode & 0o111
                                else 0o644
                            )
                            member.pax_headers = {}
                            if member.isfile():
                                payload = source.extractfile(member)
                                if payload is None:
                                    raise ValueError(f"could not read sdist member: {member.name}")
                                with payload:
                                    destination.addfile(member, payload)
                            else:
                                destination.addfile(member)
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", type=Path, required=True)
    parser.add_argument("--epoch", help="Unix timestamp; defaults to SOURCE_DATE_EPOCH.")
    args = parser.parse_args()
    try:
        output = normalize_sdist(args.path, epoch=args.epoch)
    except (OSError, tarfile.TarError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    print(f"[ok] normalized Python sdist ({output})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
