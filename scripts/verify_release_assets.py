from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path


CHECKSUM_LINE = re.compile(r"^([0-9a-f]{64})  ([^/\\]+)$")


def expected_assets(version: str, tag: str) -> set[str]:
    return {
        f"market_sentinel-{version}-py3-none-any.whl",
        f"market_sentinel-{version}.tar.gz",
        f"market-sentinel-{tag}-frontend-dist.zip",
        f"market-sentinel-{tag}-win-x64.zip",
        f"market-sentinel-{tag}-win-x64.msi",
        f"market-sentinel-{version}-sbom.spdx.json",
    }


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_checksums(path: Path) -> dict[str, str]:
    if not path.is_file():
        raise SystemExit(f"Missing checksum manifest: {path.name}")
    checksums: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = CHECKSUM_LINE.fullmatch(line)
        if not match:
            raise SystemExit(f"Malformed SHA256SUMS entry: {line!r}")
        digest, name = match.groups()
        if name in checksums:
            raise SystemExit(f"Duplicate SHA256SUMS entry: {name}")
        checksums[name] = digest
    return checksums


def verify_sbom(path: Path, version: str) -> None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SystemExit(f"Invalid SPDX SBOM {path.name}: {error}") from error
    if payload.get("spdxVersion") != "SPDX-2.3":
        raise SystemExit(f"SBOM {path.name} does not declare SPDX-2.3.")
    if payload.get("name") != f"market-sentinel-{version}-sbom":
        raise SystemExit(f"SBOM {path.name} does not match release version {version}.")
    packages = payload.get("packages")
    if not isinstance(packages, list) or not any(
        package.get("name") == "market-sentinel" and package.get("versionInfo") == version
        for package in packages
        if isinstance(package, dict)
    ):
        raise SystemExit(f"SBOM {path.name} is missing the market-sentinel {version} package entry.")


def verify_release_assets(asset_dir: Path, version: str, tag: str) -> None:
    expected = expected_assets(version, tag)
    actual = {path.name for path in asset_dir.iterdir() if path.is_file()}
    allowed = expected | {"SHA256SUMS.txt", "RELEASE_NOTES.md"}
    missing = sorted(expected - actual)
    if missing:
        raise SystemExit(f"Release assets are missing: {', '.join(missing)}")
    unexpected = sorted(actual - allowed)
    if unexpected:
        raise SystemExit(f"Release assets contain unexpected files: {', '.join(unexpected)}")
    release_notes = asset_dir / "RELEASE_NOTES.md"
    if not release_notes.is_file() or not release_notes.read_text(encoding="utf-8").strip():
        raise SystemExit("Release notes are missing or empty.")

    checksums = read_checksums(asset_dir / "SHA256SUMS.txt")
    checksum_names = set(checksums)
    if checksum_names != expected:
        missing_checksums = sorted(expected - checksum_names)
        unexpected_checksums = sorted(checksum_names - expected)
        details = []
        if missing_checksums:
            details.append(f"missing: {', '.join(missing_checksums)}")
        if unexpected_checksums:
            details.append(f"unexpected: {', '.join(unexpected_checksums)}")
        raise SystemExit("SHA256SUMS does not exactly cover release assets (" + "; ".join(details) + ").")

    for name in sorted(expected):
        path = asset_dir / name
        if path.stat().st_size == 0:
            raise SystemExit(f"Release asset is empty: {name}")
        if checksums[name] != sha256(path):
            raise SystemExit(f"SHA256SUMS digest mismatch: {name}")

    verify_sbom(asset_dir / f"market-sentinel-{version}-sbom.spdx.json", version)


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify final MarketSentinel release assets and checksums.")
    parser.add_argument("--asset-dir", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--tag", required=True)
    args = parser.parse_args()
    asset_dir = args.asset_dir.resolve()
    if not asset_dir.is_dir():
        raise SystemExit(f"Release asset directory does not exist: {asset_dir}")
    verify_release_assets(asset_dir, str(args.version), str(args.tag))
    print(f"[ok] Release assets ({asset_dir})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
