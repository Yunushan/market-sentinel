from __future__ import annotations

import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from core.storage import ConfigLoadError, load_config
from scripts import initialize_production_config as initializer


class ProductionConfigInitializationTests(unittest.TestCase):
    def test_private_entry_rejects_wrong_owner_mode_and_type(self) -> None:
        directory = SimpleNamespace(st_mode=stat.S_IFDIR | 0o700, st_uid=42, st_gid=42)
        initializer._require_private_entry(directory, kind="state directory", uid=42, gid=42)
        with self.assertRaisesRegex(ValueError, "owned"):
            initializer._require_private_entry(directory, kind="state directory", uid=7, gid=42)
        with self.assertRaisesRegex(ValueError, "mode 0700"):
            initializer._require_private_entry(
                SimpleNamespace(st_mode=stat.S_IFDIR | 0o750, st_uid=42, st_gid=42),
                kind="state directory", uid=42, gid=42,
            )
        with self.assertRaisesRegex(ValueError, "regular file"):
            initializer._require_private_entry(
                SimpleNamespace(st_mode=stat.S_IFLNK | 0o600, st_uid=42, st_gid=42),
                kind="configuration", uid=42, gid=42,
            )

    def test_initializer_creates_once_and_preserves_existing_config(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            # Windows reports synthesized POSIX mode bits. Permission policy is
            # exercised separately above; here use the real atomic store.
            with patch.object(initializer, "_require_private_entry"):
                self.assertEqual(initializer.initialize_config(path, uid=42, gid=42), "created")
                original = path.read_bytes()
                self.assertTrue(original)
                self.assertEqual(initializer.initialize_config(path, uid=42, gid=42), "existing")
                self.assertEqual(path.read_bytes(), original)
                self.assertFalse(load_config(path).copytrading.live)

    def test_initializer_rejects_malformed_existing_config_without_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_bytes(b'{"markets":')
            with patch.object(initializer, "_require_private_entry"):
                with self.assertRaises(ConfigLoadError):
                    initializer.initialize_config(path, uid=42, gid=42)
            self.assertEqual(path.read_bytes(), b'{"markets":')

    def test_runbook_initializes_before_enabling_worker_timers(self) -> None:
        runbook = (Path(__file__).resolve().parents[1] / "docs" / "PRODUCTION_OPERATIONS.md").read_text(
            encoding="utf-8"
        )
        self.assertLess(
            runbook.index("scripts/initialize_production_config.py"),
            runbook.index("sudo systemctl enable --now market-sentinel-alerts-refresh.timer"),
        )


if __name__ == "__main__":
    unittest.main()
