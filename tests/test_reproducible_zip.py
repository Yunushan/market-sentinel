from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from scripts.create_reproducible_zip import (
    ZIP_MIN_EPOCH,
    create_reproducible_zip,
    normalized_zip_datetime,
)


class ReproducibleZipTests(unittest.TestCase):
    def test_same_inputs_ignore_filesystem_times_and_produce_identical_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            (source / "index.html").write_text("<h1>release</h1>\n", encoding="utf-8")
            assets = source / "assets"
            assets.mkdir()
            (assets / "app.js").write_text("console.log('release');\n", encoding="utf-8")
            first = root / "first.zip"
            second = root / "second.zip"

            with patch.dict(os.environ, {"SOURCE_DATE_EPOCH": "1700000001"}, clear=False):
                create_reproducible_zip(source, first, prefix="bundle")
                os.utime(source / "index.html", (1_800_000_000, 1_800_000_000))
                os.utime(assets / "app.js", (1_900_000_000, 1_900_000_000))
                create_reproducible_zip(source, second, prefix="bundle")

            self.assertEqual(first.read_bytes(), second.read_bytes())
            with ZipFile(first) as archive:
                self.assertEqual(
                    archive.namelist(),
                    ["bundle/assets/app.js", "bundle/index.html"],
                )
                self.assertEqual(
                    {entry.date_time for entry in archive.infolist()},
                    {(2023, 11, 14, 22, 13, 20)},
                )

    def test_missing_epoch_uses_deterministic_zip_minimum(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(normalized_zip_datetime(), (1980, 1, 1, 0, 0, 0))
        self.assertEqual(normalized_zip_datetime(0), normalized_zip_datetime(ZIP_MIN_EPOCH))

    def test_invalid_epoch_and_unsafe_prefix_fail_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "must be an integer"):
            normalized_zip_datetime("today")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            (source / "file.txt").write_text("content", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "safe relative path"):
                create_reproducible_zip(source, root / "archive.zip", prefix="../escape")


if __name__ == "__main__":
    unittest.main()
