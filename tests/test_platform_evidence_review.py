from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.collect_platform_evidence import SCHEMA_VERSION, source_identity
from scripts.review_platform_evidence import REQUIRED_CHECKS, review_evidence


ROOT = Path(__file__).resolve().parent.parent


def _valid_payload() -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "collected_at": "2026-07-19T12:00:00Z",
        "platform_label": "FreeBSD 14.2",
        "source": source_identity(),
        "host": {
            "system": "FreeBSD",
            "release": "14.2",
            "machine": "amd64",
            "python_version": "3.12.0",
        },
        "checks": [
            {"name": name, "status": "pass", "returncode": 0, "duration_seconds": 1.0}
            for name in sorted(REQUIRED_CHECKS)
        ],
        "status": "ok",
    }


class PlatformEvidenceReviewTests(unittest.TestCase):
    def test_accepts_current_checkout_evidence_without_promoting_a_claim(self) -> None:
        identity = source_identity()
        result = review_evidence(_valid_payload(), identity["project_version"], identity["git_commit"])

        self.assertTrue(result["ok"])
        self.assertFalse(result["promotion_permitted"])
        self.assertRegex(str(result["payload_sha256"]), r"^[0-9a-f]{64}$")

    def test_rejects_captured_command_output_and_incomplete_checks(self) -> None:
        payload = copy.deepcopy(_valid_payload())
        checks = payload["checks"]
        self.assertIsInstance(checks, list)
        checks[0]["stdout"] = "credential-bearing command output"
        checks.pop()

        result = review_evidence(payload)

        self.assertFalse(result["ok"])
        self.assertTrue(any("unexpected fields" in error for error in result["errors"]))
        self.assertTrue(any("checks are missing" in error for error in result["errors"]))

    def test_rejects_source_revision_mismatch(self) -> None:
        payload = _valid_payload()
        result = review_evidence(payload, expected_version="9.9.9", expected_commit="0" * 40)

        self.assertFalse(result["ok"])
        self.assertTrue(any("project_version" in error for error in result["errors"]))
        self.assertTrue(any("git_commit" in error for error in result["errors"]))

    def test_rejects_a_timestamp_without_explicit_utc(self) -> None:
        payload = _valid_payload()
        payload["collected_at"] = "2026-07-19T12:00:00"

        result = review_evidence(payload)

        self.assertFalse(result["ok"])
        self.assertIn("collected_at must be a non-empty UTC timestamp", result["errors"])

    def test_cli_reads_json_and_reports_non_promoting_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            evidence_path = Path(directory) / "platform-evidence.json"
            evidence_path.write_text(json.dumps(_valid_payload()), encoding="utf-8")

            result = subprocess.run(
                [sys.executable, "scripts/review_platform_evidence.py", "--json", str(evidence_path)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        review = json.loads(result.stdout)
        self.assertTrue(review["ok"])
        self.assertFalse(review["promotion_permitted"])

    @unittest.skipUnless(os.name == "posix", "symbolic-link safety is verified on POSIX hosts")
    def test_cli_rejects_a_symbolic_link_evidence_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence_path = root / "platform-evidence.json"
            evidence_path.write_text(json.dumps(_valid_payload()), encoding="utf-8")
            linked = root / "linked-evidence.json"
            linked.symlink_to(evidence_path)

            result = subprocess.run(
                [sys.executable, "scripts/review_platform_evidence.py", str(linked)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("symbolic-link", result.stdout)


if __name__ == "__main__":
    unittest.main()
