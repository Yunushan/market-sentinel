from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.collect_platform_evidence import collect_evidence, write_evidence


class PlatformEvidenceTests(unittest.TestCase):
    def test_collects_redacted_successful_host_evidence(self) -> None:
        calls: list[tuple[list[str], Path, float]] = []

        def runner(args: list[str], cwd: Path, timeout: float) -> subprocess.CompletedProcess[object]:
            calls.append((args, cwd, timeout))
            return subprocess.CompletedProcess(args, 0)

        evidence = collect_evidence("FreeBSD 14.2", "python3", 30.0, True, runner)

        self.assertEqual("ok", evidence["status"])
        self.assertEqual("FreeBSD 14.2", evidence["platform_label"])
        self.assertRegex(str(evidence["source"]["project_version"]), r"^\d+\.\d+\.\d+$")
        self.assertEqual(
            ["python_version", "python_dependency_check", "tkinter_smoke", "project_verification", "frontend_build"],
            [check["name"] for check in evidence["checks"]],
        )
        self.assertEqual(["npm", "run", "build"], calls[-1][0])
        self.assertNotIn("stdout", json.dumps(evidence))
        self.assertNotIn("stderr", json.dumps(evidence))

    def test_collects_failed_command_without_capturing_output(self) -> None:
        def runner(args: list[str], cwd: Path, timeout: float) -> subprocess.CompletedProcess[object]:
            return subprocess.CompletedProcess(args, 7)

        evidence = collect_evidence("Solaris 11", "python3", 30.0, False, runner)

        self.assertEqual("failed", evidence["status"])
        self.assertTrue(all(check["returncode"] == 7 for check in evidence["checks"]))

    def test_writes_atomic_json_to_existing_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "platform-evidence.json"
            with patch("scripts.collect_platform_evidence._fsync_parent_directory") as sync_parent:
                write_evidence(output, {"status": "ok", "checks": []})

            self.assertEqual({"status": "ok", "checks": []}, json.loads(output.read_text(encoding="utf-8")))
            self.assertFalse(list(output.parent.glob("*.tmp")))
            sync_parent.assert_called_once_with(output)

    def test_rejects_a_missing_output_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "does not exist"):
                write_evidence(Path(directory) / "missing" / "platform-evidence.json", {"status": "ok"})

    @unittest.skipUnless(os.name == "posix", "symbolic-link safety is verified on POSIX hosts")
    def test_rejects_a_symlinked_output_parent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target"
            target.mkdir()
            linked = root / "linked"
            linked.symlink_to(target, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "symbolic-link"):
                write_evidence(linked / "platform-evidence.json", {"status": "ok"})

    @unittest.skipUnless(os.name == "posix", "symbolic-link safety is verified on POSIX hosts")
    def test_allows_a_platform_temp_directory_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target"
            target.mkdir()
            temp_alias = root / "platform-temp"
            temp_alias.symlink_to(target, target_is_directory=True)
            output = temp_alias / "platform-evidence.json"

            with patch("scripts.collect_platform_evidence.tempfile.gettempdir", return_value=str(temp_alias)):
                write_evidence(output, {"status": "ok"})

            self.assertEqual({"status": "ok"}, json.loads(output.read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main()
