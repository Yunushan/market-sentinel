from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.atomic_files import atomic_write_text, fsync_parent_directory


class AtomicFileTests(unittest.TestCase):
    def test_atomic_write_uses_an_exclusive_temporary_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "evidence.json"
            predictable_temporary = target.with_name(f"{target.name}.tmp")
            predictable_temporary.write_text("keep", encoding="utf-8")

            self.assertEqual(atomic_write_text(target, "{\"status\": \"ok\"}\n"), target)

            self.assertEqual(target.read_text(encoding="utf-8"), "{\"status\": \"ok\"}\n")
            self.assertEqual(predictable_temporary.read_text(encoding="utf-8"), "keep")
            self.assertFalse(list(target.parent.glob(f".{target.name}.*.tmp")))

    def test_parent_directory_is_synced_on_posix(self) -> None:
        path = Path("output") / "report.json"
        with (
            patch("core.atomic_files.os.name", "posix"),
            patch("core.atomic_files.os.open", return_value=42) as open_directory,
            patch("core.atomic_files.os.fsync") as sync,
            patch("core.atomic_files.os.close") as close,
        ):
            fsync_parent_directory(path)

        open_directory.assert_called_once_with(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        sync.assert_called_once_with(42)
        close.assert_called_once_with(42)
