from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from core.deployment_identity import frontend_tree_sha256
from core.models import AppConfig
from scripts.backup_state import create_backup
from scripts.drill_state_recovery import run_recovery_drill
from scripts.verify_production_deployment import source_identity


ROOT = Path(__file__).resolve().parents[1]


class RecoveryDrillTests(unittest.TestCase):
    def setUp(self) -> None:
        cache = ROOT / ".cache"
        cache.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="recovery-drill-test-", dir=cache)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        state = self.root / "state"
        state.mkdir()
        (state / "config.json").write_text(json.dumps(AppConfig().to_dict()), encoding="utf-8")
        (state / "note.txt").write_text("copied backup content", encoding="utf-8")
        manifest = create_backup(state, self.root / "source-backups")
        self.archive = self.root / "recovered-copy" / manifest["archive"]
        self.archive.parent.mkdir()
        shutil.copyfile(self.root / "source-backups" / manifest["archive"], self.archive)
        shutil.copyfile(
            self.root / "source-backups" / (manifest["archive"] + ".json"),
            self.archive.with_name(self.archive.name + ".json"),
        )
        self.digest = manifest["sha256"]
        self.created_at_text = manifest["created_at"]
        self.created_at = datetime.fromisoformat(manifest["created_at"].replace("Z", "+00:00")).timestamp()
        self.frontend = self.root / "frontend"
        self.frontend.mkdir()
        (self.frontend / "index.html").write_text("<!doctype html><title>Recovery drill</title>", encoding="utf-8")
        identity = source_identity()
        self.expected = {
            "expected_sha256": self.digest,
            "expected_created_at": self.created_at_text,
            "frontend_dir": self.frontend,
            "expected_version": identity["project_version"],
            "expected_source_revision": identity["git_revision"],
            "expected_frontend_sha256": frontend_tree_sha256(self.frontend),
            "max_backup_age_seconds": 3600,
            "max_restore_validation_seconds": 60,
        }

    def test_copied_pair_restores_and_application_probe_is_measured(self) -> None:
        destination = self.root / "restored"
        report = run_recovery_drill(self.archive, destination, **self.expected)
        self.assertEqual(report["scope"], "copied_backup_restore_and_isolated_application_probe")
        self.assertEqual(report["backup_sha256"], self.digest)
        self.assertEqual(report["restored_file_count"], 2)
        self.assertGreater(report["restored_bytes"], 0)
        self.assertGreaterEqual(report["restore_validation_seconds"], 0)
        self.assertTrue(report["application"]["health_ready"])
        self.assertTrue(report["application"]["mutations_blocked"])
        self.assertEqual((destination / "note.txt").read_text(encoding="utf-8"), "copied backup content")

    def test_copied_pair_requires_independently_supplied_digest(self) -> None:
        with self.assertRaisesRegex(ValueError, "independently supplied"):
            run_recovery_drill(
                self.archive, self.root / "wrong-digest",
                **{**self.expected, "expected_sha256": "a" * 64},
            )
        self.assertFalse((self.root / "wrong-digest").exists())

    def test_copied_pair_timestamp_must_match_source_inventory(self) -> None:
        manifest_path = self.archive.with_name(self.archive.name + ".json")
        copied_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        copied_manifest["created_at"] = "2099-01-01T00:00:00Z"
        manifest_path.write_text(json.dumps(copied_manifest), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "source inventory"):
            run_recovery_drill(self.archive, self.root / "forged-timestamp", **self.expected)
        self.assertFalse((self.root / "forged-timestamp").exists())

    def test_backup_age_limit_fails_before_extraction(self) -> None:
        with self.assertRaisesRegex(ValueError, "age limit"):
            run_recovery_drill(
                self.archive, self.root / "stale",
                clock=lambda: self.created_at + 3601,
                **self.expected,
            )
        self.assertFalse((self.root / "stale").exists())

    def test_restore_validation_limit_fails_closed(self) -> None:
        ticks = iter((0.0, 100.0))
        with self.assertRaisesRegex(RuntimeError, "time limit"), patch(
            "scripts.drill_state_recovery.subprocess.run",
        ) as application:
            run_recovery_drill(
                self.archive, self.root / "slow-restore",
                monotonic=lambda: next(ticks),
                **{**self.expected, "max_restore_validation_seconds": 1.0},
            )
        application.assert_not_called()

    def test_existing_destination_and_mismatched_runtime_identity_fail(self) -> None:
        existing = self.root / "existing"
        existing.mkdir()
        with self.assertRaisesRegex(ValueError, "must not already exist"):
            run_recovery_drill(self.archive, existing, **self.expected)
        with self.assertRaisesRegex(RuntimeError, "runtime identity"):
            run_recovery_drill(
                self.archive, self.root / "wrong-identity",
                **{**self.expected, "expected_source_revision": "0" * 40},
            )


if __name__ == "__main__":
    unittest.main()
