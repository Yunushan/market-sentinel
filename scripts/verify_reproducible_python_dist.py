#!/usr/bin/env python3
"""Compare two independently built Python distribution directories byte-for-byte."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path


def _artifacts(directory: Path) -> dict[str, Path]:
    directory = directory.resolve()
    if not directory.is_dir():
        raise ValueError(f"distribution directory does not exist: {directory}")
    artifacts = {
        path.name: path
        for path in directory.iterdir()
        if path.is_file() and (path.name.endswith(".whl") or path.name.endswith(".tar.gz"))
    }
    wheels = [name for name in artifacts if name.endswith(".whl")]
    sdists = [name for name in artifacts if name.endswith(".tar.gz")]
    if len(wheels) != 1 or len(sdists) != 1 or len(artifacts) != 2:
        names = ", ".join(sorted(artifacts)) or "none"
        raise ValueError(
            "expected exactly one wheel and one .tar.gz source distribution in "
            f"{directory}; found {names}"
        )
    return artifacts


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_reproducible_distributions(first_dir: Path, second_dir: Path) -> None:
    first = _artifacts(first_dir)
    second = _artifacts(second_dir)
    if set(first) != set(second):
        raise ValueError(
            "independent Python builds produced different artifact names: "
            f"{sorted(first)} != {sorted(second)}"
        )
    mismatches = [
        name
        for name in sorted(first)
        if _sha256(first[name]) != _sha256(second[name])
    ]
    if mismatches:
        raise ValueError(
            "independent Python builds are not byte-reproducible: "
            + ", ".join(mismatches)
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--first-dir", type=Path, required=True)
    parser.add_argument("--second-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        verify_reproducible_distributions(args.first_dir, args.second_dir)
    except (OSError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    print("[ok] Python distributions are byte-reproducible")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
