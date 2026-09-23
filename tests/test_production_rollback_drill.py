from __future__ import annotations

import copy
import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.drill_production_rollback import (
    Release,
    ServiceInvocation,
    _archive_previous_report,
    _validate_report_target,
    observe_web_service,
    run_rollback_drill,
)
from scripts.verify_production_deployment import ROLLBACK_DRILL_STEPS, check_rollback_drill


CURRENT = Release("1.0.12", "a" * 40, "b" * 64)
ROLLBACK = Release("1.0.11", "c" * 40, "d" * 64)
HOST_SHA256 = "e" * 64


def health(release: Release) -> dict:
    return {
        "status": "ok",
        "readiness": {"ready": True},
        "api_version": release.version,
        "runtime_source_revision": release.revision,
        "runtime_frontend_sha256": release.frontend_sha256,
    }


class ProductionRollbackDrillTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.writes: list[dict] = []
        self.prompts: list[str] = []
        self.clock_ticks = 0

    def clock(self) -> float:
        self.clock_ticks += 1
        return 1_800_000_000 + self.clock_ticks

    def conduct(self, *, invocations=None, payloads=None, prompts=None, revisions=None, source_statuses=None, digests=None, health_url="http://127.0.0.1:8765/api/health", writer=None) -> dict:
        if invocations is None:
            invocations = ["a", "a", "b", "b", "b", "c", "c", "c"]
        if payloads is None:
            payloads = [health(CURRENT), health(ROLLBACK), health(CURRENT)]
        if prompts is None:
            prompts = [f"ACTIVATED {ROLLBACK.revision}", f"REACTIVATED {CURRENT.revision}"]
        if revisions is None:
            revisions = [CURRENT.revision, ROLLBACK.revision, CURRENT.revision]
        if source_statuses is None:
            source_statuses = ["clean", "clean", "clean"]
        if digests is None:
            digests = [CURRENT.frontend_sha256, ROLLBACK.frontend_sha256, CURRENT.frontend_sha256]
        invocation_iter = iter(invocations)
        health_iter = iter(payloads)
        prompt_iter = iter(prompts)
        revision_iter = iter(revisions)
        status_iter = iter(source_statuses)
        digest_iter = iter(digests)

        def prompt(message: str) -> str:
            self.prompts.append(message)
            return next(prompt_iter)

        def read_source(_: Path) -> dict[str, str]:
            revision = next(revision_iter)
            return {
                "project_version": CURRENT.version if revision == CURRENT.revision else ROLLBACK.version,
                "git_revision": revision,
                "git_revision_status": "ok",
                "git_worktree_status": next(status_iter),
            }

        with patch("scripts.drill_production_rollback._validate_report_target"):
            return run_rollback_drill(
                current=CURRENT, rollback=ROLLBACK,
                deployment_provider="example-provider",
                host_identity_sha256=HOST_SHA256,
                public_origin="https://analytics.example.com",
                report_path=self.root / "latest.json",
                deployment_root=self.root / "deployment",
                frontend_dir=self.root / "frontend",
                health_url=health_url, token="observer-only", prompt=prompt,
                service_observer=lambda: ServiceInvocation(next(invocation_iter) * 32, 100),
                health_probe=lambda *_: next(health_iter),
                source_reader=read_source,
                frontend_digest=lambda _: next(digest_iter),
                report_writer=writer or (lambda _, report: self.writes.append(copy.deepcopy(report))),
                clock=self.clock,
            )

    def test_real_sequence_produces_exact_verifier_report_after_reactivation(self) -> None:
        report = self.conduct()
        self.assertEqual(self.writes[0]["status"], "in_progress")
        self.assertEqual(self.writes[-1], report)
        self.assertEqual(report["status"], "ok")
        self.assertEqual([step["name"] for step in report["steps"]], list(ROLLBACK_DRILL_STEPS))
        self.assertEqual([step["revision"] for step in report["steps"]], [
            CURRENT.revision, ROLLBACK.revision, ROLLBACK.revision,
            CURRENT.revision, CURRENT.revision,
        ])
        for index in (1, 3):
            self.assertEqual(report["steps"][index]["api_version"], "")
            self.assertEqual(report["steps"][index]["runtime_source_revision"], "")
            self.assertEqual(report["steps"][index]["runtime_frontend_sha256"], "")
        self.assertEqual(len(self.prompts), 2)
        self.assertEqual(report["final_revision"], CURRENT.revision)
        self.assertNotIn("observer-only", str(report))

    def test_success_report_matches_the_production_verifier_contract(self) -> None:
        report = self.conduct()
        path = self.root / "latest.json"
        path.write_text(json.dumps(report), encoding="utf-8")
        original_lstat = Path.lstat

        def private_lstat(candidate: Path) -> os.stat_result:
            metadata = original_lstat(candidate)
            if candidate != path:
                return metadata
            fields = list(metadata)
            fields[0] = stat.S_IFREG | 0o600
            fields[4] = 0
            return os.stat_result(fields)

        # Windows fixture permissions cannot model POSIX root ownership/mode.
        # Only metadata and DNS are supplied; the verifier parses real report bytes.
        with (
            patch.object(Path, "lstat", autospec=True, side_effect=private_lstat),
            patch("scripts.verify_production_deployment._validated_public_origin", side_effect=lambda origin: origin),
        ):
            verdict = check_rollback_drill(
                path, expected_version=CURRENT.version,
                expected_current_revision=CURRENT.revision,
                expected_frontend_sha256=CURRENT.frontend_sha256,
                deployment_provider="example-provider",
                host_identity_sha256=HOST_SHA256,
                public_origin="https://analytics.example.com",
                clock=lambda: 1_800_000_050,
            )
        self.assertEqual(verdict["status"], "pass", verdict)
        self.assertEqual(verdict["step_count"], 5)

    def test_no_operator_confirmation_leaves_only_failed_evidence(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "not explicitly confirmed"):
            self.conduct(prompts=["", f"REACTIVATED {CURRENT.revision}"])
        self.assertEqual(self.writes[-1]["status"], "failed")
        self.assertEqual(self.writes[-1]["final_revision"], "")
        self.assertEqual(len(self.writes[-1]["steps"]), 1)

    def test_old_service_invocation_cannot_claim_rollback_activation(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "did not change for rollback"):
            self.conduct(invocations=["a", "a", "a"])
        self.assertEqual(self.writes[-1]["status"], "failed")
        self.assertEqual(len(self.writes[-1]["steps"]), 1)

    def test_wrong_runtime_identity_cannot_claim_healthy_rollback(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "running web service identity"):
            self.conduct(payloads=[health(CURRENT), health(CURRENT)])
        self.assertEqual(self.writes[-1]["status"], "failed")
        self.assertEqual(len(self.writes[-1]["steps"]), 1)

    def test_wrong_deployed_files_cannot_claim_healthy_rollback(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "active frontend files"):
            self.conduct(digests=[CURRENT.frontend_sha256, CURRENT.frontend_sha256])
        self.assertEqual(self.writes[-1]["status"], "failed")

    def test_dirty_rollback_checkout_cannot_claim_success(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "clean deployment checkout"):
            self.conduct(source_statuses=["clean", "dirty"])
        self.assertEqual(self.writes[-1]["status"], "failed")

    def test_non_loopback_health_url_fails_before_replacing_report(self) -> None:
        with self.assertRaisesRegex(ValueError, "HTTP loopback"):
            self.conduct(health_url="http://evil.example/api/health")
        self.assertEqual(self.writes, [])

    def test_failed_activation_rechecks_current_release_and_stays_ineligible(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "not explicitly confirmed"):
            self.conduct(
                prompts=["", f"RECOVERED {CURRENT.revision}"],
                invocations=["a", "a", "a", "a", "a"],
                payloads=[health(CURRENT), health(CURRENT)],
                revisions=[CURRENT.revision, CURRENT.revision],
                digests=[CURRENT.frontend_sha256, CURRENT.frontend_sha256],
            )
        self.assertEqual(self.writes[-1]["status"], "failed")
        self.assertEqual(self.writes[-1]["final_revision"], CURRENT.revision)
        self.assertEqual(len(self.writes[-1]["steps"]), 1)

    def test_previous_raw_report_is_preserved_before_latest_is_invalidated(self) -> None:
        latest = self.root / "latest.json"
        prior = b'{"status":"ok","whitespace": "preserved"}\n'
        latest.write_bytes(prior)
        archived = _archive_previous_report(latest)
        self.assertIsNotNone(archived)
        self.assertEqual(archived.read_bytes(), prior)
        self.assertEqual(latest.read_bytes(), prior)

    def test_report_path_cannot_replace_an_arbitrary_root_private_file(self) -> None:
        with self.assertRaisesRegex(ValueError, "dedicated path"):
            _validate_report_target(self.root / "sensitive.json")

    def test_systemd_observation_uses_named_properties_not_output_order(self) -> None:
        output = f"InvocationID={'a' * 32}\nMainPID=123\nActiveState=active\n"
        with patch(
            "scripts.drill_production_rollback.subprocess.run",
            return_value=subprocess.CompletedProcess(["systemctl"], 0, output, ""),
        ):
            self.assertEqual(observe_web_service(), ServiceInvocation("a" * 32, 123))

    def test_failed_journal_write_does_not_skip_current_release_recovery(self) -> None:
        def writer(_: Path, report: dict) -> None:
            self.writes.append(copy.deepcopy(report))
            if report["status"] == "failed":
                raise OSError("simulated disk full")

        with self.assertRaisesRegex(RuntimeError, "failed journal could not be durably saved"):
            self.conduct(
                prompts=["", f"RECOVERED {CURRENT.revision}"],
                invocations=["a", "a", "a", "a", "a"],
                payloads=[health(CURRENT), health(CURRENT)],
                revisions=[CURRENT.revision, CURRENT.revision],
                digests=[CURRENT.frontend_sha256, CURRENT.frontend_sha256],
                writer=writer,
            )
        self.assertEqual(len(self.prompts), 2)
        self.assertEqual(self.writes[-1]["status"], "failed")
        self.assertEqual(self.writes[-1]["final_revision"], CURRENT.revision)


if __name__ == "__main__":
    unittest.main()
