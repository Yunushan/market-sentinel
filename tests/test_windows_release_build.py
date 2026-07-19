from __future__ import annotations

import stat
import tempfile
import unittest
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from scripts.build_windows_release import extract_frontend_archive


class WindowsReleaseBuildTests(unittest.TestCase):
    def test_extract_frontend_archive_extracts_normal_release_assets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive_path = root / "frontend.zip"
            destination = root / "dist"
            destination.mkdir()
            with ZipFile(archive_path, "w", ZIP_DEFLATED) as archive:
                archive.writestr("index.html", "<html></html>")
                archive.writestr("assets/app.js", "console.log('ok')")

            extract_frontend_archive(archive_path, destination)

            self.assertEqual((destination / "index.html").read_text(encoding="utf-8"), "<html></html>")
            self.assertEqual((destination / "assets" / "app.js").read_text(encoding="utf-8"), "console.log('ok')")

    def test_extract_frontend_archive_rejects_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive_path = root / "unsafe.zip"
            destination = root / "dist"
            destination.mkdir()
            with ZipFile(archive_path, "w", ZIP_DEFLATED) as archive:
                archive.writestr("../outside.txt", "unsafe")

            with self.assertRaisesRegex(ValueError, "unsafe member path"):
                extract_frontend_archive(archive_path, destination)

            self.assertFalse((root / "outside.txt").exists())

    def test_extract_frontend_archive_rejects_symbolic_links(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive_path = root / "symlink.zip"
            destination = root / "dist"
            destination.mkdir()
            link = ZipInfo("assets/link")
            link.external_attr = (stat.S_IFLNK | 0o777) << 16
            with ZipFile(archive_path, "w", ZIP_DEFLATED) as archive:
                archive.writestr(link, "../../outside.txt")

            with self.assertRaisesRegex(ValueError, "symbolic-link"):
                extract_frontend_archive(archive_path, destination)
