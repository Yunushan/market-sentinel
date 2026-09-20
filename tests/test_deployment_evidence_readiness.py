from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch

from core.unattended_worker import worker_invocation_sha256
from scripts.check_product_readiness import (
    DEPLOYMENT_COLLECTOR_JOB,
    DEPLOYMENT_EXTERNAL_PROBE_JOB,
    DEPLOYMENT_PREPARE_JOB,
    DEPLOYMENT_REVIEW_JOB,
    REQUIRED_DEPLOYMENT_COLLECTOR_STEPS,
    REQUIRED_DEPLOYMENT_EXTERNAL_PROBE_STEPS,
    REQUIRED_DEPLOYMENT_PREPARE_STEPS,
    REQUIRED_DEPLOYMENT_REVIEW_STEPS,
    _attested_deployment_report,
)
from scripts.verify_production_deployment import REQUIRED_WORKER_UNIT_CONTRACT_SHA256


REVISION = "a" * 40
VERSION = "1.0.11"
RUN_ID = 741
NOW = datetime(2026, 8, 26, 12, tzinfo=timezone.utc)
REPOSITORY = "Yunushan/market-sentinel"
ORIGIN = "https://markets.example.net"


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _systemd_time(value: datetime) -> str:
    return value.strftime("%a %Y-%m-%d %H:%M:%S UTC")


def _external_probe_report() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "report_type": "market-sentinel-external-deployment-probe",
        "probed_at": _iso(NOW - timedelta(minutes=3)),
        "status": "ok",
        "source_revision": REVISION,
        "collection": {
            "mode": "github_hosted_external_public_probe",
            "public_origin": ORIGIN,
            "expected_version": VERSION,
            "expected_source_revision": REVISION,
            "expected_frontend_sha256": "c" * 64,
            "run_id": RUN_ID,
            "run_attempt": 1,
            "nonce": f"{REVISION}:{RUN_ID}:1",
            "runner_environment": "github-hosted",
        },
        "checks": [
            {
                "name": "public_https_proxy",
                "status": "pass",
                "api_version": VERSION,
                "runtime_source_revision": REVISION,
                "runtime_frontend_sha256": "c" * 64,
                "unauthenticated_probes": 5,
            }
        ],
    }


def _external_probe_bytes(report: dict[str, Any]) -> bytes:
    return (json.dumps(report, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")


def _operations() -> dict[str, Any]:
    collected_at = NOW - timedelta(minutes=4)
    alert_started = NOW - timedelta(minutes=3, seconds=30)
    alert_completed = NOW - timedelta(minutes=3, seconds=24)
    services: dict[str, Any] = {}
    tasks: dict[str, Any] = {}
    worker_rows = (
        (
            "market-sentinel-alerts-refresh.service",
            "market-sentinel-alerts-refresh.timer",
            "alerts-refresh",
            NOW - timedelta(minutes=5),
            300,
            "00000000-0000-4000-8000-000000000001",
        ),
        (
            "market-sentinel-wallets-poll.service",
            "market-sentinel-wallets-poll.timer",
            "wallets-poll",
            NOW - timedelta(minutes=9),
            600,
            "00000000-0000-4000-8000-000000000002",
        ),
    )
    for service, timer, task, succeeded_at, max_age, worker_run_id in worker_rows:
        completed_at = succeeded_at + timedelta(seconds=10)
        services[service] = {
            "completed_at": _systemd_time(completed_at),
            "completed_at_unix_seconds": completed_at.timestamp(),
            "task": task,
            "timer": timer,
            "source_revision": REVISION,
            "unit_contract_sha256": REQUIRED_WORKER_UNIT_CONTRACT_SHA256[service],
        }
        tasks[task] = {
            "abandoned_runs": 0,
            "attempts_completed": 1,
            "emitted": 1,
            "freshness_age_seconds": (collected_at - succeeded_at).total_seconds(),
            "last_success_at": _iso(succeeded_at),
            "last_success_at_unix_seconds": succeeded_at.timestamp(),
            "max_age_seconds": max_age,
            "processed": 2,
            "run_id": worker_run_id,
            "service": service,
            "service_unit": service,
            "source_revision": REVISION,
            "timer": timer,
            "total_failures": 0,
            "total_runs": 2,
            "total_successes": 2,
            "unit_contract_sha256": REQUIRED_WORKER_UNIT_CONTRACT_SHA256[service],
            "invocation_sha256": worker_invocation_sha256(
                task=task,
                service_unit=service,
                source_revision=REVISION,
                unit_contract_sha256=REQUIRED_WORKER_UNIT_CONTRACT_SHA256[service],
            ),
        }
    return {
        "alert_delivery": {
            "acknowledged_at": _iso(alert_started + timedelta(seconds=4)),
            "acknowledger_sha256": "0" * 64,
            "alert_fingerprint": "0123456789abcdef",
            "alertmanager_config_sha256": "c" * 64,
            "alertmanager_status_sha256": "d" * 64,
            "binding_sha256": "1" * 64,
            "deployment_identity_sha256": "2" * 64,
            "delivery_id_sha256": "8" * 64,
            "nonce_sha256": "3" * 64,
            "oncall_channel_sha256": "9" * 64,
            "oncall_provider": "pagerduty",
            "raw_report_sha256": "4" * 64,
            "receipt_origin_sha256": "a" * 64,
            "receipt_sha256": "b" * 64,
            "review_report_sha256": "5" * 64,
            "run_attempt": 1,
            "run_id": RUN_ID,
            "source_revision": REVISION,
            "status": "ok",
            "timeline": {
                "alertmanager_observed_at": _iso(alert_started + timedelta(seconds=3)),
                "alertmanager_config_observed_at": _iso(
                    alert_started + timedelta(seconds=3, milliseconds=500)
                ),
                "cleanup_rule_absent_at": _iso(alert_started + timedelta(seconds=5)),
                "completed_at": _iso(alert_completed),
                "oncall_acknowledged_at": _iso(alert_started + timedelta(seconds=4)),
                "oncall_dispatched_at": _iso(alert_started + timedelta(seconds=3)),
                "oncall_receipt_observed_at": _iso(alert_started + timedelta(seconds=4)),
                "oncall_received_at": _iso(alert_started + timedelta(seconds=2)),
                "prometheus_alert_observed_at": _iso(alert_started + timedelta(seconds=2)),
                "prometheus_rule_observed_at": _iso(alert_started + timedelta(seconds=1)),
                "receiver_observed_at": _iso(alert_started + timedelta(seconds=4)),
                "started_at": _iso(alert_started),
            },
            "transcript_sha256": "6" * 64,
            "webhook_body_sha256": "e" * 64,
            "webhook_event_sha256": "f" * 64,
        },
        "unattended_workers": {
            "environment_file": "/etc/market-sentinel/market-sentinel-worker.env",
            "environment_keys": ["MARKET_SENTINEL_SOURCE_REVISION"],
            "lock_file": "/var/lib/market-sentinel/.unattended-worker.lock",
            "services": services,
            "state_file": "/var/lib/market-sentinel/unattended-worker-state.json",
            "state_sha256": "7" * 64,
            "tasks": tasks,
        },
    }


def _report() -> dict[str, Any]:
    tag = f"v{VERSION}"
    external_probe_report = _external_probe_report()
    return {
        "schema_version": 1,
        "report_type": "market-sentinel-deployment-evidence",
        "deployment": {
            "environment": "production",
            "public_origin": ORIGIN,
            "collected_at": _iso(NOW - timedelta(minutes=4)),
            "raw_report_sha256": "b" * 64,
            "workflow_nonce": f"{REVISION}:{RUN_ID}:1",
            "check_count": 31,
            "frontend_sha256": "c" * 64,
            "deployment_provider": "bare-metal",
            "host_identity_sha256": "f" * 64,
            "restore_drill": {
                "completed_at": _iso(NOW - timedelta(minutes=5)),
                "backup_sha256": "a" * 64,
                "restored_file_count": 2,
                "restored_bytes": 8,
                "application": {
                    "schema_version": 1, "config_loaded": True, "health_ready": True,
                    "state_readable": True, "mutations_blocked": True, "outbound_attempts": 0,
                    "files_unchanged": True, "sqlite_databases_checked": 0,
                    "api_version": VERSION, "runtime_source_revision": REVISION,
                    "runtime_frontend_sha256": "c" * 64,
                },
            },
            "rollback_drill": {
                "drill_id": "00000000-0000-4000-8000-000000000001",
                "report_sha256": "b" * 64,
                "completed_at": _iso(NOW - timedelta(minutes=30)),
                "rollback_revision": "f" * 40,
                "final_revision": REVISION,
                "step_count": 5,
            },
            "external_probe": {
                "probed_at": _iso(NOW - timedelta(minutes=3)),
                "raw_report_sha256": hashlib.sha256(
                    _external_probe_bytes(external_probe_report)
                ).hexdigest(),
                "runner_environment": "github-hosted",
                "api_version": VERSION,
                "source_revision": REVISION,
                "frontend_sha256": "c" * 64,
                "unauthenticated_probes": 5,
            },
            "release": {
                "id": 81,
                "tag": tag,
                "version": VERSION,
                "target_commit": REVISION,
                "published_at": _iso(NOW - timedelta(hours=2)),
                "html_url": f"https://github.com/{REPOSITORY}/releases/tag/{tag}",
                "asset": {"id": 82, "name": f"market-sentinel-{tag}-frontend-dist.zip", "size": 99, "sha256": "d" * 64},
            },
        },
        "operations": _operations(),
        "external_probe_report": external_probe_report,
        "evidence": {
            "repository": REPOSITORY,
            "source_revision": REVISION,
            "run_id": RUN_ID,
            "run_attempt": 1,
            "workflow": ".github/workflows/deployment-evidence.yml",
            "workflow_name": "Production deployment evidence",
            "workflow_ref": f"{REPOSITORY}/.github/workflows/deployment-evidence.yml@refs/heads/main",
            "source_ref": "refs/heads/main",
            "event": "workflow_dispatch",
            "runner_environment": "github-hosted",
            "collector_job": DEPLOYMENT_COLLECTOR_JOB,
            "external_probe_job": DEPLOYMENT_EXTERNAL_PROBE_JOB,
            "review_job": DEPLOYMENT_REVIEW_JOB,
            "collector_labels": ["linux", "market-sentinel-production", "self-hosted", "x64"],
            "artifact_name": f"deployment-evidence-{REVISION}-{RUN_ID}-1",
        },
    }


def _job(name: str, labels: list[str], steps: tuple[str, ...]) -> dict[str, Any]:
    return {
        "name": name, "labels": labels, "head_sha": REVISION, "run_attempt": 1,
        "status": "completed", "conclusion": "success",
        "steps": [{"name": step, "status": "completed", "conclusion": "success"} for step in steps],
    }


def _gh(
    *,
    labels: list[str] | None = None,
    duplicate_artifact: bool = False,
    stale_artifact: bool = False,
    failed_step: bool = False,
    missing_origin_step: bool = False,
    missing_oncall_step: bool = False,
):
    def query(command: list[str], **_: object):
        route = command[-1]
        if command[:3] == ["gh", "attestation", "verify"]:
            return [{"trusted": True}], ""
        if route.endswith(f"/actions/runs/{RUN_ID}"):
            return {"id": RUN_ID, "head_sha": REVISION, "head_branch": "main", "event": "workflow_dispatch", "name": "Production deployment evidence", "path": ".github/workflows/deployment-evidence.yml", "status": "completed", "conclusion": "success", "run_attempt": 1, "head_repository": {"full_name": REPOSITORY}, "created_at": _iso(NOW - timedelta(minutes=10)), "run_started_at": _iso(NOW - timedelta(minutes=9)), "updated_at": _iso(NOW - timedelta(minutes=1))}, ""
        if "/jobs?" in route:
            jobs = [
                _job(DEPLOYMENT_PREPARE_JOB, ["ubuntu-24.04"], REQUIRED_DEPLOYMENT_PREPARE_STEPS),
                _job(DEPLOYMENT_COLLECTOR_JOB, labels or ["self-hosted", "linux", "x64", "market-sentinel-production"], REQUIRED_DEPLOYMENT_COLLECTOR_STEPS),
                _job(DEPLOYMENT_EXTERNAL_PROBE_JOB, ["ubuntu-24.04"], REQUIRED_DEPLOYMENT_EXTERNAL_PROBE_STEPS),
                _job(DEPLOYMENT_REVIEW_JOB, ["ubuntu-24.04"], REQUIRED_DEPLOYMENT_REVIEW_STEPS),
            ]
            if missing_origin_step:
                jobs[0]["steps"] = [
                    step
                    for step in jobs[0]["steps"]
                    if step["name"] != "Require exact protected production origin"
                ]
            if missing_oncall_step:
                jobs[0]["steps"] = [
                    step
                    for step in jobs[0]["steps"]
                    if step["name"] != "Require protected public on-call receipt service"
                ]
            if failed_step:
                jobs[3]["steps"][2]["conclusion"] = "failure"
            return {"total_count": 4, "jobs": jobs}, ""
        if "/artifacts?" in route:
            item = {"id": 90, "name": f"deployment-evidence-{REVISION}-{RUN_ID}-1", "expired": False, "created_at": _iso(NOW - timedelta(days=2) if stale_artifact else NOW - timedelta(minutes=2)), "updated_at": _iso(NOW - timedelta(minutes=1)), "workflow_run": {"id": RUN_ID, "head_sha": REVISION}}
            return {"total_count": 2 if duplicate_artifact else 1, "artifacts": [item, dict(item)] if duplicate_artifact else [item]}, ""
        if "/releases/tags/" in route:
            tag = f"v{VERSION}"
            return {"id": 81, "tag_name": tag, "target_commitish": REVISION, "draft": False, "prerelease": False, "published_at": _iso(NOW - timedelta(hours=2)), "html_url": f"https://github.com/{REPOSITORY}/releases/tag/{tag}", "assets": [{"id": 82, "name": f"market-sentinel-{tag}-frontend-dist.zip", "state": "uploaded", "size": 99, "digest": f"sha256:{'d' * 64}"}]}, ""
        if route.endswith("/branches/main"):
            return {"protected": True, "commit": {"sha": "e" * 40}}, ""
        if "/compare/" in route:
            return {"status": "ahead", "base_commit": {"sha": REVISION}, "merge_base_commit": {"sha": REVISION}}, ""
        raise AssertionError(command)
    return query


class DeploymentEvidenceReadinessTests(unittest.TestCase):
    def _validate(
        self,
        report: dict[str, Any],
        *,
        attestation: bool = True,
        source_attestation: bool = True,
        **gh_options: object,
    ) -> dict[str, Any]:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "deployment-evidence.json")
            path.write_bytes((json.dumps(report, indent=2, sort_keys=True) + "\n").encode())
            def matches(_item: object, **kwargs: object) -> bool:
                if kwargs.get("subject_name") == "external-probe.json":
                    return attestation and source_attestation
                return attestation

            with patch("scripts.check_product_readiness._run_gh_json", side_effect=_gh(**gh_options)), patch("scripts.check_product_readiness._attestation_result_matches", side_effect=matches), patch("scripts.check_product_readiness._resolve_release_tag_commit", return_value=(REVISION, "")):
                return _attested_deployment_report(str(path), expected_revision=REVISION, expected_version=VERSION, expected_origin=ORIGIN, now=NOW)

    def test_accepts_exact_attested_production_deployment(self) -> None:
        result = self._validate(_report())
        self.assertEqual(result["status"], "pass")
        self.assertEqual(
            result["capabilities"],
            {"alert_delivery": True, "unattended_workers": True},
        )

    def test_rejects_origin_mutation(self) -> None:
        report = _report(); report["deployment"]["public_origin"] = "https://other.example.net"
        self.assertEqual(self._validate(report)["status"], "fail")

    def test_rejects_collector_label_mutation(self) -> None:
        self.assertEqual(self._validate(_report(), labels=["self-hosted", "linux", "x64"])["status"], "fail")

    def test_rejects_duplicate_or_stale_artifact(self) -> None:
        self.assertEqual(self._validate(_report(), duplicate_artifact=True)["status"], "fail")
        self.assertEqual(self._validate(_report(), stale_artifact=True)["status"], "fail")

    def test_rejects_frontend_release_asset_mutation(self) -> None:
        report = _report(); report["deployment"]["release"]["asset"]["sha256"] = "e" * 64
        self.assertEqual(self._validate(report)["status"], "fail")

    def test_rejects_missing_attestation_or_failed_review_step(self) -> None:
        self.assertEqual(self._validate(_report(), attestation=False)["status"], "fail")
        self.assertEqual(self._validate(_report(), source_attestation=False)["status"], "fail")
        self.assertEqual(self._validate(_report(), failed_step=True)["status"], "fail")

    def test_rejects_tampered_embedded_external_probe_source(self) -> None:
        report = _report()
        report["external_probe_report"]["checks"][0]["api_version"] = "9.9.9"
        self.assertEqual(self._validate(report)["status"], "fail")

        semantically_tampered = _report()
        semantically_tampered["external_probe_report"]["checks"][0]["api_version"] = "9.9.9"
        semantically_tampered["deployment"]["external_probe"]["raw_report_sha256"] = hashlib.sha256(
            _external_probe_bytes(semantically_tampered["external_probe_report"])
        ).hexdigest()
        self.assertEqual(self._validate(semantically_tampered)["status"], "fail")

    def test_rejects_prepare_job_without_exact_origin_gate(self) -> None:
        self.assertEqual(self._validate(_report(), missing_origin_step=True)["status"], "fail")
        self.assertEqual(self._validate(_report(), missing_oncall_step=True)["status"], "fail")

    def test_rejects_missing_or_tampered_operations_capability(self) -> None:
        missing = _report()
        missing.pop("operations")
        self.assertEqual(self._validate(missing)["status"], "fail")

        stale_worker = _report()
        stale_worker["operations"]["unattended_workers"]["tasks"]["alerts-refresh"][
            "freshness_age_seconds"
        ] = 301
        self.assertEqual(self._validate(stale_worker)["status"], "fail")

        stale_revision = _report()
        stale_revision["operations"]["unattended_workers"]["tasks"]["alerts-refresh"][
            "source_revision"
        ] = "f" * 40
        self.assertEqual(self._validate(stale_revision)["status"], "fail")

        invocation_mismatch = _report()
        invocation_mismatch["operations"]["unattended_workers"]["tasks"]["alerts-refresh"][
            "invocation_sha256"
        ] = "0" * 64
        self.assertEqual(self._validate(invocation_mismatch)["status"], "fail")

        noop_worker = _report()
        noop_worker["operations"]["unattended_workers"]["tasks"]["alerts-refresh"][
            "processed"
        ] = 0
        self.assertEqual(self._validate(noop_worker)["status"], "fail")

        out_of_order = _report()
        out_of_order["operations"]["alert_delivery"]["timeline"]["cleanup_rule_absent_at"] = _iso(
            NOW - timedelta(minutes=4)
        )
        self.assertEqual(self._validate(out_of_order)["status"], "fail")

        loopback_provider = _report()
        loopback_provider["operations"]["alert_delivery"]["oncall_provider"] = "loopback"
        self.assertEqual(self._validate(loopback_provider)["status"], "fail")

        mismatched_ack = _report()
        mismatched_ack["operations"]["alert_delivery"]["acknowledged_at"] = _iso(
            NOW - timedelta(minutes=1)
        )
        self.assertEqual(self._validate(mismatched_ack)["status"], "fail")

    def test_worker_future_skew_accepts_five_seconds_and_rejects_more(self) -> None:
        collected_at = NOW - timedelta(minutes=4)
        service_name = "market-sentinel-alerts-refresh.service"
        for offset, expected_status in ((5.0, "pass"), (5.001, "fail")):
            with self.subTest(offset=offset):
                report = _report()
                workers = report["operations"]["unattended_workers"]
                success = collected_at + timedelta(seconds=offset)
                workers["tasks"]["alerts-refresh"].update(
                    {
                        "last_success_at": _iso(success),
                        "last_success_at_unix_seconds": success.timestamp(),
                        "freshness_age_seconds": -offset,
                    }
                )
                workers["services"][service_name].update(
                    {
                        "completed_at": (success - timedelta(seconds=2)).strftime(
                            "%a %Y-%m-%d %H:%M:%S.%f UTC"
                        ),
                        "completed_at_unix_seconds": (
                            success - timedelta(seconds=2)
                        ).timestamp(),
                    }
                )
                self.assertEqual(self._validate(report)["status"], expected_status)

    def test_worker_freshness_is_recomputed_at_scoring_time(self) -> None:
        report = _report()
        collected_at = NOW - timedelta(minutes=4)
        success = collected_at - timedelta(seconds=61)
        task = report["operations"]["unattended_workers"]["tasks"]["alerts-refresh"]
        task.update(
            {
                "last_success_at": _iso(success),
                "last_success_at_unix_seconds": success.timestamp(),
                "freshness_age_seconds": 61.0,
            }
        )
        service = report["operations"]["unattended_workers"]["services"][
            "market-sentinel-alerts-refresh.service"
        ]
        completed = success + timedelta(seconds=10)
        service.update(
            {
                "completed_at": _systemd_time(completed),
                "completed_at_unix_seconds": completed.timestamp(),
            }
        )
        self.assertEqual(self._validate(report)["status"], "fail")


if __name__ == "__main__":
    unittest.main()
