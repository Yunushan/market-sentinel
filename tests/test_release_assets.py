from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts.verify_release_assets import verify_release_assets


VERSION = "1.2.3"
TAG = "v1.2.3"


class ReleaseAssetVerificationTests(unittest.TestCase):
    def _write_assets(self, root: Path) -> None:
        names = (
            f"market_sentinel-{VERSION}-py3-none-any.whl",
            f"market_sentinel-{VERSION}.tar.gz",
            f"market-sentinel-{TAG}-frontend-dist.zip",
            f"market-sentinel-{TAG}-win-x64.zip",
            f"market-sentinel-{TAG}-win-x64.msi",
        )
        for name in names:
            (root / name).write_bytes(name.encode("utf-8"))
        sbom_name = f"market-sentinel-{VERSION}-sbom.spdx.json"
        (root / sbom_name).write_text(
            json.dumps(
                {
                    "spdxVersion": "SPDX-2.3",
                    "name": f"market-sentinel-{VERSION}-sbom",
                    "packages": [{"name": "market-sentinel", "versionInfo": VERSION}],
                }
            ),
            encoding="utf-8",
        )
        lines = []
        for path in sorted(root.iterdir()):
            if path.name == "SHA256SUMS.txt":
                continue
            lines.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}")
        (root / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
        (root / "RELEASE_NOTES.md").write_text("## MarketSentinel\n", encoding="utf-8")

    def test_accepts_complete_release_assets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_assets(root)
            verify_release_assets(root, VERSION, TAG)

    def test_rejects_checksum_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_assets(root)
            (root / f"market-sentinel-{TAG}-win-x64.msi").write_text("changed", encoding="utf-8")
            with self.assertRaisesRegex(SystemExit, "digest mismatch"):
                verify_release_assets(root, VERSION, TAG)

    def test_rejects_incomplete_checksum_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_assets(root)
            checksum_path = root / "SHA256SUMS.txt"
            lines = checksum_path.read_text(encoding="utf-8").splitlines()
            checksum_path.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(SystemExit, "does not exactly cover"):
                verify_release_assets(root, VERSION, TAG)

    def test_rejects_unexpected_publishable_asset(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_assets(root)
            (root / "unreviewed.bin").write_bytes(b"not a release asset")
            with self.assertRaisesRegex(SystemExit, "unexpected files"):
                verify_release_assets(root, VERSION, TAG)
