from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


class ProductReadinessTests(unittest.TestCase):
    def test_readiness_scorer_reports_conservative_static_score(self) -> None:
        result = subprocess.run(
            [sys.executable, "scripts/check_product_readiness.py", "--no-run-local", "--json"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["out_of"], 100)
        self.assertLess(report["score"], 100)
        self.assertEqual(report["status"], "not_ready")
        self.assertEqual({item["name"] for item in report["categories"]}, {
            "architecture_scope",
            "tests_correctness",
            "security_safety",
            "ci_cd_release",
            "operations_recovery",
            "platform_evidence",
            "live_acceptance",
        })

    def test_readiness_document_defines_external_evidence_boundary(self) -> None:
        text = (ROOT / "docs" / "PRODUCTION_READINESS.md").read_text(encoding="utf-8")
        for fragment in (
            "repository's conservative",
            "External Evidence Manifests",
            "credentialed",
            "funded",
            "Do not put venue credentials",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, text)

    def test_unreviewed_external_evidence_does_not_award_points(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "deployment.json"
            path.write_text(
                json.dumps(
                    {
                        "verified": True,
                        "schema_version": 1,
                        "evidence_type": "deployment",
                        "source": "test",
                        "checks": [{"name": "health", "status": "pass"}],
                    }
                ),
                encoding="utf-8",
            )
            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/check_product_readiness.py",
                    "--no-run-local",
                    "--deployment-evidence",
                    str(path),
                    "--json",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = json.loads(result.stdout)
        operations = next(item for item in report["categories"] if item["name"] == "operations_recovery")
        self.assertEqual(operations["earned"], 12)
        self.assertTrue(any("reviewed_by" in item for item in operations["missing"]))

    def test_reviewed_partial_external_evidence_awards_only_its_scoped_points(self) -> None:
        manifest = {
            "verified": True,
            "schema_version": 1,
            "reviewed_by": "test-reviewer",
            "reviewed_at": "2026-08-03T00:00:00Z",
            "checks": [{"name": "check", "status": "pass"}],
        }
        with tempfile.TemporaryDirectory() as temporary:
            platform_path = Path(temporary) / "platform-ci.json"
            release_environment_path = Path(temporary) / "release-environment.json"
            platform_path.write_text(
                json.dumps(
                    {
                        **manifest,
                        "evidence_type": "platform-ci",
                        "source": "test",
                        "scope": "hosted-ci",
                        "run_id": 1,
                        "source_revision": "a" * 40,
                    }
                ),
                encoding="utf-8",
            )
            release_environment_path.write_text(
                json.dumps(
                    {
                        **manifest,
                        "evidence_type": "release-environment",
                        "source": "test",
                    }
                ),
                encoding="utf-8",
            )
            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/check_product_readiness.py",
                    "--no-run-local",
                    "--platform-ci-evidence",
                    str(platform_path),
                    "--release-environment-evidence",
                    str(release_environment_path),
                    "--json",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = json.loads(result.stdout)
        platform = next(item for item in report["categories"] if item["name"] == "platform_evidence")
        ci_cd = next(item for item in report["categories"] if item["name"] == "ci_cd_release")
        self.assertEqual(platform["earned"], 8)
        self.assertEqual(ci_cd["earned"], 15)

    def test_mislabeled_evidence_cannot_award_a_different_tier(self) -> None:
        manifest = {
            "verified": True,
            "schema_version": 1,
            "evidence_type": "release-environment",
            "reviewed_by": "test-reviewer",
            "reviewed_at": "2026-08-03T00:00:00Z",
            "source": "test",
            "checks": [{"name": "check", "status": "pass"}],
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "mislabeled.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/check_product_readiness.py",
                    "--no-run-local",
                    "--platform-ci-evidence",
                    str(path),
                    "--json",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = json.loads(result.stdout)
        platform = next(item for item in report["categories"] if item["name"] == "platform_evidence")
        self.assertEqual(platform["earned"], 5)
        self.assertTrue(any("evidence_type=\'platform-ci\'" in item for item in platform["missing"]))


if __name__ == "__main__":
    unittest.main()
