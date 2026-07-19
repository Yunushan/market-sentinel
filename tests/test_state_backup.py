from __future__ import annotations

import io
import hashlib
import json
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.backup_state import _fsync_directory, create_backup
from scripts.restore_state_backup import restore_backup, verify_backup


class StateBackupTests(unittest.TestCase):
    def test_backup_directory_is_synced_on_posix(self) -> None:
        directory = Path("backups")
        with (
            patch("scripts.backup_state.os.name", "posix"),
            patch("scripts.backup_state.os.open", return_value=42) as open_directory,
            patch("scripts.backup_state.os.fsync") as sync,
            patch("scripts.backup_state.os.close") as close,
        ):
            _fsync_directory(directory)

        open_directory.assert_called_once()
        sync.assert_called_once_with(42)
        close.assert_called_once_with(42)

    def test_restore_utility_runs_when_invoked_as_a_script_path(self) -> None:
        script = Path(__file__).resolve().parent.parent / "scripts" / "restore_state_backup.py"
        result = subprocess.run([sys.executable, str(script), "--help"], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Verify or restore", result.stdout)

    def test_backup_verification_and_restore_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "state"
            destination = root / "backups"
            restored = root / "restored"
            (source / "nested").mkdir(parents=True)
            (source / "config.json").write_text('{"theme": "dark"}', encoding="utf-8")
            (source / "nested" / "paper.jsonl").write_text('{"id": 1}\n', encoding="utf-8")

            manifest = create_backup(source, destination, retain=2)
            archive = destination / str(manifest["archive"])
            self.assertTrue(archive.is_file())
            self.assertTrue(archive.with_name(archive.name + ".json").is_file())
            self.assertEqual(verify_backup(archive)["file_count"], 2)
            restore_backup(archive, restored)

            self.assertEqual((restored / "config.json").read_text(encoding="utf-8"), '{"theme": "dark"}')
            self.assertEqual((restored / "nested" / "paper.jsonl").read_text(encoding="utf-8"), '{"id": 1}\n')

    def test_backup_retention_prunes_old_archive_and_checksum_mismatch_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "state"
            destination = root / "backups"
            source.mkdir()
            source.joinpath("config.json").write_text("{}", encoding="utf-8")
            first_archive = destination / str(create_backup(source, destination, retain=1)["archive"])
            source.joinpath("config.json").write_text('{"updated": true}', encoding="utf-8")
            second_archive = destination / str(create_backup(source, destination, retain=1)["archive"])
            self.assertFalse(first_archive.exists())
            self.assertTrue(second_archive.exists())
            with second_archive.open("ab") as handle:
                handle.write(b"tampered")
            with self.assertRaisesRegex(RuntimeError, "checksum"):
                verify_backup(second_archive)

    def test_backup_uses_a_consistent_sqlite_snapshot_without_wal_sidecars(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "state"
            destination = root / "backups"
            restored = root / "restored"
            source.mkdir()
            database = source / "leaderboard.sqlite3"
            connection = sqlite3.connect(database)
            try:
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("CREATE TABLE rows (value TEXT NOT NULL)")
                connection.execute("INSERT INTO rows(value) VALUES ('durable')")
                connection.commit()
                archive = destination / str(create_backup(source, destination)["archive"])
            finally:
                connection.close()

            with tarfile.open(archive, "r:gz") as handle:
                self.assertEqual(handle.getnames(), ["leaderboard.sqlite3"])
            restore_backup(archive, restored)
            restored_connection = sqlite3.connect(restored / "leaderboard.sqlite3")
            try:
                self.assertEqual(restored_connection.execute("SELECT value FROM rows").fetchone()[0], "durable")
            finally:
                restored_connection.close()

    def test_restore_rejects_unsafe_archive_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            archive = root / "unsafe.tar.gz"
            with tarfile.open(archive, "w:gz") as handle:
                member = tarfile.TarInfo("../outside.txt")
                payload = b"unsafe"
                member.size = len(payload)
                handle.addfile(member, io.BytesIO(payload))
            archive.with_name(archive.name + ".json").write_text(
                json.dumps(
                    {
                        "archive": archive.name,
                        "created_at": "2026-01-01T00:00:00Z",
                        "file_count": 1,
                        "schema_version": 1,
                        "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                        "source_name": "state",
                        "uncompressed_bytes": 6,
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "unsafe member path"):
                verify_backup(archive)

    def test_restore_requires_an_empty_destination(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "state"
            destination = root / "backups"
            restore_target = root / "restore-target"
            source.mkdir()
            source.joinpath("config.json").write_text("{}", encoding="utf-8")
            restore_target.mkdir()
            restore_target.joinpath("existing.txt").write_text("keep", encoding="utf-8")
            archive = destination / str(create_backup(source, destination)["archive"])
            with self.assertRaisesRegex(ValueError, "must be empty"):
                restore_backup(archive, restore_target)


if __name__ == "__main__":
    unittest.main()
