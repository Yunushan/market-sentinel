from __future__ import annotations

import io
import hashlib
import json
import tarfile
import tempfile
import unittest
from pathlib import Path

from scripts.backup_state import create_backup
from scripts.restore_state_backup import restore_backup, verify_backup


class StateBackupTests(unittest.TestCase):
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
