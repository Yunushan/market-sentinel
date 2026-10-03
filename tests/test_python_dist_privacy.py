from __future__ import annotations

import io
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from scripts.verify_python_dist_artifacts import REQUIRED_SDIST_MEMBERS, _verify_artifact_privacy, verify_sdist, verify_wheel


ROOT = Path(__file__).resolve().parents[1]


class PythonDistributionPrivacyTests(unittest.TestCase):
    def test_sdist_from_checkout_with_private_state_excludes_it(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shutil.copyfile(ROOT / "MANIFEST.in", root / "MANIFEST.in")
            (root / "setup.py").write_text(
                "from setuptools import setup\nsetup(name='privacy_audit', version='1.0.0', py_modules=[])\n",
                encoding="utf-8",
            )
            (root / "data" / "nested").mkdir(parents=True)
            (root / "data" / "config.example.json").write_text("{}", encoding="utf-8")
            (root / "data" / "config.json").write_text('{"private":"CONFIG_MARKER"}', encoding="utf-8")
            (root / "data" / "nested" / "wallets.json").write_text("WALLET_MARKER", encoding="utf-8")
            (root / "deploy").mkdir()
            (root / "deploy" / "service.env").write_text("TOKEN=ENV_MARKER", encoding="utf-8")
            (root / "deploy" / ".env.production").write_text("TOKEN=ENV_MARKER", encoding="utf-8")
            (root / "deploy" / "service.pem").write_text("KEY_MARKER", encoding="utf-8")
            (root / "deploy" / "private.PEM").write_text("KEY_MARKER", encoding="utf-8")
            (root / "deploy" / "private.ENV").write_text("TOKEN=ENV_MARKER", encoding="utf-8")
            (root / "deploy" / ".ENV.production").write_text("TOKEN=ENV_MARKER", encoding="utf-8")
            (root / "DATA").mkdir(exist_ok=True)
            (root / "DATA" / "private.json").write_text("CONFIG_MARKER", encoding="utf-8")
            # A previous build's SOURCES inventory must not reintroduce runtime data.
            (root / "privacy_audit.egg-info").mkdir()
            (root / "privacy_audit.egg-info" / "SOURCES.txt").write_text(
                "data/config.json\ndata/nested/wallets.json\nDATA/private.json\ndeploy/service.env\ndeploy/service.pem\n",
                encoding="utf-8",
            )
            completed = subprocess.run(
                [sys.executable, "setup.py", "sdist"], cwd=root,
                text=True, capture_output=True, timeout=60,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            archive_path = next((root / "dist").glob("*.tar.gz"))
            with tarfile.open(archive_path, "r:gz") as archive:
                names = archive.getnames()
                self.assertTrue(any(name.endswith("data/config.example.json") for name in names))
                for private_name in (
                    "data/config.json", "data/nested/wallets.json", "deploy/service.env", "deploy/service.pem",
                    "deploy/.env.production",
                    "deploy/private.PEM", "deploy/private.ENV", "deploy/.ENV.production", "DATA/private.json",
                ):
                    self.assertFalse(any(name.endswith(private_name) for name in names), private_name)
                for member in archive.getmembers():
                    if member.isfile():
                        stream = archive.extractfile(member)
                        assert stream is not None
                        content = stream.read()
                        for marker in (b"CONFIG_MARKER", b"WALLET_MARKER", b"ENV_MARKER", b"KEY_MARKER"):
                            self.assertNotIn(marker, content)

    def test_verifier_rejects_private_state_even_when_required_files_exist(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for private_name in (
                "data/config.json", "data/nested/wallets.json", "deploy/service.env", "deploy/key.pem",
                "deploy/.env.production", "deploy/service.env.local",
                "DATA/config.json", "Data/nested/wallets.json", "DATA/config.example.json",
                "FRONTEND/DIST/index.html", "frontend/NODE_MODULES/private.json",
                ".CACHE/credentials.json", "core/__PYCACHE__/runtime.pyc",
                "deploy/key.PEM", "deploy/service.ENV", "deploy/.ENV.production",
                "deploy/config.json:private", "deploy./secret.json", "data /config.json",
                "C:/private.json", "//server/share/private.json",
            ):
                archive_path = root / "market_sentinel-1.0.0.tar.gz"
                with tarfile.open(archive_path, "w:gz") as archive:
                    for name in REQUIRED_SDIST_MEMBERS | {private_name}:
                        content = (ROOT / "LICENSE").read_bytes() if name == "LICENSE" else b"fixture"
                        info = tarfile.TarInfo(f"market_sentinel-1.0.0/{name}")
                        info.size = len(content)
                        archive.addfile(info, io.BytesIO(content))
                with self.subTest(private_name=private_name):
                    with self.assertRaisesRegex(SystemExit, "generated/private artifacts"):
                        verify_sdist(archive_path, "1.0.0")

    def test_wheel_rejects_case_variants_and_ambiguous_windows_paths_before_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            archive_path = Path(temporary) / "fixture.whl"
            for private_name in (
                "DATA/config.json", "FRONTEND/DIST/index.html", "core/__PYCACHE__/module.pyc",
                ".CACHE/private.json", "C:/private.json", "core/module.py:private",
                "core\\..\\data\\config.json", "core./module.py", "data /config.json",
            ):
                with self.subTest(private_name=private_name):
                    with ZipFile(archive_path, "w") as archive:
                        archive.writestr(private_name, "private marker")
                    with self.assertRaisesRegex(SystemExit, "generated/private artifacts"):
                        verify_wheel(archive_path, "1.0.0")

    def test_privacy_verifier_keeps_exact_reviewed_examples_and_directory_entries(self) -> None:
        _verify_artifact_privacy({"data/", "data/config.example.json", ".env.example",
                                  "deploy/systemd/market-sentinel.env.example"}, "fixture")

    def test_sdist_rejects_backslash_archive_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            archive_path = Path(temporary) / "fixture.tar.gz"
            with tarfile.open(archive_path, "w:gz") as archive:
                content = b"private marker"
                member = tarfile.TarInfo("market_sentinel-1.0.0\\data\\config.json")
                member.size = len(content)
                archive.addfile(member, io.BytesIO(content))
            with self.assertRaisesRegex(SystemExit, "unexpected archive roots"):
                verify_sdist(archive_path, "1.0.0")


if __name__ == "__main__":
    unittest.main()
