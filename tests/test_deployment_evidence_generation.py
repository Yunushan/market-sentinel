from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch

from core.unattended_worker import worker_invocation_sha256
from scripts.generate_deployment_evidence import (
    ALLOWED_UNATTENDED_ENVIRONMENT_KEYS,
    DeploymentEvidenceGenerationError,
    UNATTENDED_ENVIRONMENT_FILE,
    UNATTENDED_LOCK_FILE,
    UNATTENDED_STATE_FILE,
    canonical_external_probe_bytes,
    generate_evidence,
    verify_external_probe_attestation,
)
from scripts.verify_production_deployment import (
    ALLOWED_WORKER_ENVIRONMENT_KEYS,
    DEFAULT_WORKER_ENVIRONMENT_PATH,
    DEFAULT_WORKER_LOCK_PATH,
    DEFAULT_WORKER_STATE_PATH,
    REQUIRED_WORKER_UNIT_CONTRACT_SHA256,
)


REVISION = "a" * 40
ORIGIN = "https://markets.example.net"
ONCALL_ORIGIN = "https://alerts.example.net"
MONITORING_NONCE = "9" * 64


def _unix(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def _unattended_workers() -> dict[str, Any]:
    completed = "Wed 2026-08-26 11:03:00 UTC"
    last_success = "2026-08-26T11:02:50Z"
    services = {
        "market-sentinel-alerts-refresh.service": {
            "task": "alerts-refresh",
            "timer": "market-sentinel-alerts-refresh.timer",
            "completed_at": completed,
            "completed_at_unix_seconds": _unix("2026-08-26T11:03:00Z"),
            "source_revision": REVISION,
            "unit_contract_sha256": REQUIRED_WORKER_UNIT_CONTRACT_SHA256[
                "market-sentinel-alerts-refresh.service"
            ],
        },
        "market-sentinel-wallets-poll.service": {
            "task": "wallets-poll",
            "timer": "market-sentinel-wallets-poll.timer",
            "completed_at": completed,
            "completed_at_unix_seconds": _unix("2026-08-26T11:03:00Z"),
            "source_revision": REVISION,
            "unit_contract_sha256": REQUIRED_WORKER_UNIT_CONTRACT_SHA256[
                "market-sentinel-wallets-poll.service"
            ],
        },
    }
    tasks: dict[str, Any] = {}
    for index, (service, service_summary) in enumerate(services.items(), start=1):
        task_name = service_summary["task"]
        tasks[task_name] = {
            "service": service,
            "timer": service_summary["timer"],
            "run_id": f"00000000-0000-4000-8000-{index:012d}",
            "source_revision": REVISION,
            "service_unit": service,
            "unit_contract_sha256": REQUIRED_WORKER_UNIT_CONTRACT_SHA256[service],
            "invocation_sha256": worker_invocation_sha256(
                task=task_name,
                service_unit=service,
                source_revision=REVISION,
                unit_contract_sha256=REQUIRED_WORKER_UNIT_CONTRACT_SHA256[service],
            ),
            "last_success_at": last_success,
            "last_success_at_unix_seconds": _unix(last_success),
            "freshness_age_seconds": 10.0,
            "max_age_seconds": 300 if task_name == "alerts-refresh" else 600,
            "attempts_completed": 1,
            "processed": 3,
            "emitted": 2,
            "total_runs": 4,
            "total_successes": 4,
            "total_failures": 0,
            "abandoned_runs": 0,
        }
    return {
        "state_file": "/var/lib/market-sentinel/unattended-worker-state.json",
        "state_sha256": "7" * 64,
        "lock_file": "/var/lib/market-sentinel/.unattended-worker.lock",
        "environment_file": "/etc/market-sentinel/market-sentinel-worker.env",
        "environment_keys": ["MANIFOLD_API_KEY", "MARKET_SENTINEL_SOURCE_REVISION"],
        "services": services,
        "tasks": tasks,
    }


def _deployment_review(*, include_unattended: bool = True) -> dict[str, Any]:
    review: dict[str, Any] = {
        "frontend_sha256": "b" * 64,
        "collected_at": "2026-08-26T11:03:00Z",
        "raw_report_sha256": "c" * 64,
        "check_count": 34,
        "deployment_provider": "bare-metal",
        "host_identity_sha256": "d" * 64,
        "restore_drill": {
            "completed_at": "2026-08-26T11:01:00Z",
            "backup_sha256": "e" * 64,
            "restored_file_count": 2,
            "restored_bytes": 5,
            "application": {
                "schema_version": 1,
                "config_loaded": True,
                "health_ready": True,
                "state_readable": True,
                "mutations_blocked": True,
                "outbound_attempts": 0,
                "files_unchanged": True,
                "sqlite_databases_checked": 0,
                "api_version": "1.0.11",
                "runtime_source_revision": REVISION,
                "runtime_frontend_sha256": "b" * 64,
            },
        },
        "rollback_drill": {
            "drill_id": "00000000-0000-4000-8000-000000000001",
            "report_sha256": "a" * 64,
            "completed_at": "2026-08-26T10:00:00Z",
            "rollback_revision": "f" * 40,
            "final_revision": REVISION,
            "step_count": 5,
        },
    }
    if include_unattended:
        review["unattended_workers"] = _unattended_workers()
    return review


def _external_review(report_sha256: str) -> dict[str, Any]:
    return {
        "probed_at": "2026-08-26T11:02:00Z",
        "report_sha256": report_sha256,
        "api_version": "1.0.11",
        "source_revision": REVISION,
        "frontend_sha256": "b" * 64,
        "unauthenticated_probes": 5,
    }


def _external_probe_source() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "report_type": "market-sentinel-external-deployment-probe",
        "probed_at": "2026-08-26T11:02:00Z",
        "status": "ok",
        "source_revision": REVISION,
        "collection": {
            "mode": "github_hosted_external_public_probe",
            "public_origin": ORIGIN,
            "expected_version": "1.0.11",
            "expected_source_revision": REVISION,
            "expected_frontend_sha256": "b" * 64,
            "run_id": 7,
            "run_attempt": 1,
            "nonce": f"{REVISION}:7:1",
            "runner_environment": "github-hosted",
        },
        "checks": [
            {
                "name": "public_https_proxy",
                "status": "pass",
                "api_version": "1.0.11",
                "runtime_source_revision": REVISION,
                "runtime_frontend_sha256": "b" * 64,
                "unauthenticated_probes": 5,
            }
        ],
    }


def _external_probe_attestation(report: dict[str, Any]) -> list[dict[str, Any]]:
    workflow_ref = (
        "Yunushan/market-sentinel/.github/workflows/deployment-evidence.yml@refs/heads/main"
    )
    workflow_uri = f"https://github.com/{workflow_ref}"
    repository_uri = "https://github.com/Yunushan/market-sentinel"
    invocation_uri = f"{repository_uri}/actions/runs/7/attempts/1"
    return [
        {
            "attestation": {"bundle": "verified"},
            "verificationResult": {
                "mediaType": "application/vnd.dev.sigstore.verificationresult+json;version=0.1",
                "verifiedTimestamps": [{"timestamp": "2026-08-26T11:03:00Z"}],
                "statement": {
                    "_type": "https://in-toto.io/Statement/v1",
                    "predicateType": "https://slsa.dev/provenance/v1",
                    "subject": [
                        {
                            "name": "external-probe.json",
                            "digest": {
                                "sha256": hashlib.sha256(
                                    canonical_external_probe_bytes(report)
                                ).hexdigest()
                            },
                        }
                    ],
                    "predicate": {
                        "buildDefinition": {
                            "buildType": "https://actions.github.io/buildtypes/workflow/v1",
                            "externalParameters": {
                                "workflow": {
                                    "path": ".github/workflows/deployment-evidence.yml",
                                    "ref": "refs/heads/main",
                                    "repository": repository_uri,
                                }
                            },
                            "internalParameters": {
                                "github": {
                                    "event_name": "workflow_dispatch",
                                    "runner_environment": "github-hosted",
                                }
                            },
                            "resolvedDependencies": [
                                {
                                    "uri": f"git+{repository_uri}@refs/heads/main",
                                    "digest": {"gitCommit": REVISION},
                                }
                            ],
                        },
                        "runDetails": {
                            "builder": {"id": workflow_uri},
                            "metadata": {"invocationId": invocation_uri},
                        },
                    },
                },
                "signature": {
                    "certificate": {
                        "subjectAlternativeName": workflow_uri,
                        "issuer": "https://token.actions.githubusercontent.com",
                        "buildSignerURI": workflow_uri,
                        "buildSignerDigest": REVISION,
                        "runnerEnvironment": "github-hosted",
                        "sourceRepositoryURI": repository_uri,
                        "sourceRepositoryDigest": REVISION,
                        "sourceRepositoryRef": "refs/heads/main",
                        "sourceRepositoryOwnerURI": "https://github.com/Yunushan",
                        "buildConfigURI": workflow_uri,
                        "buildConfigDigest": REVISION,
                        "buildTrigger": "workflow_dispatch",
                        "runInvocationURI": invocation_uri,
                        "sourceRepositoryVisibilityAtSigning": "public",
                    }
                },
            },
        }
    ]


def _alert_review(identity_sha256: str, raw_sha256: str, *, nonce: str = MONITORING_NONCE) -> dict[str, Any]:
    binding = "6" * 64
    return {
        "acknowledged_at": "2026-08-26T11:02:04Z",
        "acknowledger_sha256": "a" * 64,
        "alert_fingerprint": "0123456789abcdef",
        "alertmanager_config_sha256": "e" * 64,
        "alertmanager_status_sha256": "f" * 64,
        "alert_name": f"MarketSentinelDeliveryAttestation_{binding[:24]}",
        "binding_sha256": binding,
        "deployment_identity_sha256": identity_sha256,
        "delivery_id_sha256": "b" * 64,
        "evidence_type": "reviewed-prometheus-alert-delivery",
        "nonce": nonce,
        "oncall_channel_sha256": "c" * 64,
        "oncall_provider": "pagerduty",
        "raw_report_sha256": raw_sha256,
        "receipt_origin_sha256": hashlib.sha256(ONCALL_ORIGIN.encode()).hexdigest(),
        "receipt_sha256": "d" * 64,
        "reviewed_at": "2026-08-26T11:02:07Z",
        "run_attempt": 1,
        "run_id": 7,
        "schema_version": 1,
        "source_revision": REVISION,
        "status": "ok",
        "timeline": {
            "alertmanager_observed_at": "2026-08-26T11:02:03Z",
            "alertmanager_config_observed_at": "2026-08-26T11:02:03.500000Z",
            "cleanup_rule_absent_at": "2026-08-26T11:02:05Z",
            "completed_at": "2026-08-26T11:02:06Z",
            "oncall_acknowledged_at": "2026-08-26T11:02:04Z",
            "oncall_dispatched_at": "2026-08-26T11:02:03Z",
            "oncall_receipt_observed_at": "2026-08-26T11:02:04Z",
            "oncall_received_at": "2026-08-26T11:02:02Z",
            "prometheus_alert_observed_at": "2026-08-26T11:02:02Z",
            "prometheus_rule_observed_at": "2026-08-26T11:02:01Z",
            "receiver_observed_at": "2026-08-26T11:02:04Z",
            "started_at": "2026-08-26T11:02:00Z",
        },
        "transcript_sha256": "5" * 64,
        "webhook_body_sha256": "7" * 64,
        "webhook_event_sha256": "8" * 64,
    }


class DeploymentEvidenceGenerationTests(unittest.TestCase):
    def _files(self, root: Path) -> dict[str, Any]:
        paths: dict[str, Any] = {
            "raw": root / "raw.json",
            "external": root / "external.json",
            "alert_raw": root / "alert-raw.json",
            "alert_review": root / "alert-review.json",
            "identity": root / "identity.json",
        }
        paths["raw"].write_text(
            json.dumps(
                {
                    "collection": {
                        "public_origin": ORIGIN,
                        "run_id": 7,
                        "run_attempt": 1,
                        "nonce": f"{REVISION}:7:1",
                    }
                }
            ),
            encoding="utf-8",
        )
        paths["external"].write_bytes(canonical_external_probe_bytes({}))
        paths["alert_raw"].write_text("{}", encoding="utf-8")
        paths["identity"].write_text(
            json.dumps(
                {
                    "identity_type": "market-sentinel-deployment-release-identity",
                    "repository": "Yunushan/market-sentinel",
                    "frontend_sha256": "b" * 64,
                    "release": {"target_commit": REVISION, "version": "1.0.11"},
                }
            ),
            encoding="utf-8",
        )
        identity_sha256 = hashlib.sha256(paths["identity"].read_bytes()).hexdigest()
        raw_sha256 = hashlib.sha256(paths["alert_raw"].read_bytes()).hexdigest()
        alert_review = _alert_review(identity_sha256, raw_sha256)
        paths["alert_review"].write_text(json.dumps(alert_review), encoding="utf-8")
        paths["alert_review_payload"] = alert_review
        return paths

    def _generate(
        self,
        paths: dict[str, Any],
        *,
        deployment_review: dict[str, Any] | None = None,
        independent_alert_review: dict[str, Any] | None = None,
        public_origin: str = ORIGIN,
        oncall_receipt_origin: str = ONCALL_ORIGIN,
        artifact_name: str = f"deployment-evidence-{REVISION}-7-1",
        monitoring_nonce: str = MONITORING_NONCE,
    ) -> dict[str, Any]:
        with (
            patch(
                "scripts.generate_deployment_evidence.review_deployment_report",
                return_value=deployment_review or _deployment_review(),
            ),
            patch(
                "scripts.generate_deployment_evidence.review_external_probe_report",
                return_value=_external_review(hashlib.sha256(paths["external"].read_bytes()).hexdigest()),
            ),
            patch(
                "scripts.generate_deployment_evidence.review_alert_delivery_report",
                return_value=independent_alert_review or paths["alert_review_payload"],
            ),
        ):
            return generate_evidence(
                paths["raw"],
                paths["external"],
                paths["alert_raw"],
                paths["alert_review"],
                paths["identity"],
                public_origin=public_origin,
                oncall_receipt_origin=oncall_receipt_origin,
                monitoring_nonce=monitoring_nonce,
                run_id=7,
                run_attempt=1,
                workflow_ref=(
                    "Yunushan/market-sentinel/.github/workflows/"
                    "deployment-evidence.yml@refs/heads/main"
                ),
                source_ref="refs/heads/main",
                artifact_name=artifact_name,
            )

    def test_binds_reviewed_operations_to_exact_run_and_release_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = self._files(Path(directory))
            expected_review_sha256 = hashlib.sha256(paths["alert_review"].read_bytes()).hexdigest()
            report = self._generate(paths)
        self.assertEqual(report["deployment"]["public_origin"], ORIGIN)
        self.assertEqual(report["deployment"]["frontend_sha256"], "b" * 64)
        self.assertEqual(report["deployment"]["external_probe"]["runner_environment"], "github-hosted")
        self.assertEqual(report["external_probe_report"], {})
        self.assertEqual(report["deployment"]["restore_drill"]["application"]["health_ready"], True)
        alert_delivery = report["operations"]["alert_delivery"]
        self.assertEqual(alert_delivery["status"], "ok")
        self.assertEqual(alert_delivery["binding_sha256"], "6" * 64)
        self.assertEqual(alert_delivery["source_revision"], REVISION)
        self.assertEqual(alert_delivery["run_id"], 7)
        self.assertEqual(alert_delivery["run_attempt"], 1)
        self.assertEqual(alert_delivery["oncall_provider"], "pagerduty")
        self.assertEqual(alert_delivery["acknowledger_sha256"], "a" * 64)
        self.assertEqual(
            alert_delivery["receipt_origin_sha256"],
            hashlib.sha256(ONCALL_ORIGIN.encode()).hexdigest(),
        )
        self.assertEqual(alert_delivery["nonce_sha256"], hashlib.sha256(MONITORING_NONCE.encode()).hexdigest())
        self.assertEqual(alert_delivery["review_report_sha256"], expected_review_sha256)
        self.assertEqual(report["operations"]["unattended_workers"], _unattended_workers())
        serialized_operations = json.dumps(report["operations"], sort_keys=True)
        self.assertNotIn(MONITORING_NONCE, serialized_operations)
        self.assertNotIn('"body":', serialized_operations)

    def test_rejects_origin_or_artifact_name_mutation_before_attestation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = self._files(Path(directory))
            with self.assertRaises(DeploymentEvidenceGenerationError):
                self._generate(paths, public_origin="https://other.example.net", artifact_name="wrong")

    def test_rejects_collector_nonce_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = self._files(Path(directory))
            paths["raw"].write_text(
                json.dumps(
                    {
                        "collection": {
                            "public_origin": ORIGIN,
                            "run_id": 7,
                            "run_attempt": 1,
                            "nonce": "tampered",
                        }
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(DeploymentEvidenceGenerationError):
                self._generate(paths)

    def test_rejects_alert_delivery_nonce_or_review_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = self._files(Path(directory))
            with self.assertRaisesRegex(DeploymentEvidenceGenerationError, "exact deployment run"):
                self._generate(paths, monitoring_nonce="8" * 64)
            tampered = dict(paths["alert_review_payload"])
            tampered["alert_fingerprint"] = "fedcba9876543210"
            paths["alert_review"].write_text(json.dumps(tampered), encoding="utf-8")
            with self.assertRaisesRegex(DeploymentEvidenceGenerationError, "disagrees"):
                self._generate(paths)

    def test_rejects_alert_raw_or_release_identity_byte_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = self._files(Path(directory))
            original_alert_raw = paths["alert_raw"].read_bytes()
            paths["alert_raw"].write_text('{"tampered":true}', encoding="utf-8")
            with self.assertRaisesRegex(DeploymentEvidenceGenerationError, "exact deployment run"):
                self._generate(paths)
            paths["alert_raw"].write_bytes(original_alert_raw)
            paths["identity"].write_text(paths["identity"].read_text(encoding="utf-8") + "\n", encoding="utf-8")
            with self.assertRaisesRegex(DeploymentEvidenceGenerationError, "exact deployment run"):
                self._generate(paths)

    def test_rejects_missing_unattended_worker_summary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = self._files(Path(directory))
            with self.assertRaisesRegex(DeploymentEvidenceGenerationError, "unattended-workers"):
                self._generate(paths, deployment_review=_deployment_review(include_unattended=False))

    def test_rejects_unattended_worker_path_or_environment_inventory_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = self._files(Path(directory))
            wrong_path = _deployment_review()
            wrong_path["unattended_workers"]["state_file"] = "/var/lib/market-sentinel/other.json"
            with self.assertRaisesRegex(DeploymentEvidenceGenerationError, "reviewed production paths"):
                self._generate(paths, deployment_review=wrong_path)
            wrong_environment = copy.deepcopy(_deployment_review())
            wrong_environment["unattended_workers"]["environment_keys"] = ["POLYMARKET_PRIVATE_KEY"]
            with self.assertRaisesRegex(DeploymentEvidenceGenerationError, "environment key inventory"):
                self._generate(paths, deployment_review=wrong_environment)

    def test_rejects_stale_misbound_or_noop_worker_summary(self) -> None:
        mutations = (
            (
                "stale revision",
                lambda workers: workers["tasks"]["alerts-refresh"].update(
                    source_revision="d" * 40
                ),
            ),
            (
                "invocation mismatch",
                lambda workers: workers["tasks"]["alerts-refresh"].update(
                    invocation_sha256="0" * 64
                ),
            ),
            (
                "zero processed",
                lambda workers: workers["tasks"]["alerts-refresh"].update(processed=0),
            ),
        )
        for label, mutate in mutations:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                paths = self._files(Path(directory))
                review = copy.deepcopy(_deployment_review())
                mutate(review["unattended_workers"])
                with self.assertRaises(DeploymentEvidenceGenerationError):
                    self._generate(paths, deployment_review=review)

    def test_worker_future_skew_accepts_five_seconds_and_rejects_more(self) -> None:
        collected = datetime.fromisoformat("2026-08-26T11:03:00+00:00")
        service_name = "market-sentinel-alerts-refresh.service"
        for offset, accepted in ((5.0, True), (5.001, False)):
            with self.subTest(offset=offset), tempfile.TemporaryDirectory() as directory:
                paths = self._files(Path(directory))
                review = copy.deepcopy(_deployment_review())
                workers = review["unattended_workers"]
                success = collected.timestamp() + offset
                success_text = datetime.fromtimestamp(success, collected.tzinfo).isoformat().replace(
                    "+00:00", "Z"
                )
                task = workers["tasks"]["alerts-refresh"]
                task.update(
                    {
                        "last_success_at": success_text,
                        "last_success_at_unix_seconds": success,
                        "freshness_age_seconds": -offset,
                    }
                )
                service = workers["services"][service_name]
                completed = success - 2
                service.update(
                    {
                        "completed_at": datetime.fromtimestamp(completed, collected.tzinfo).strftime(
                            "%a %Y-%m-%d %H:%M:%S.%f UTC"
                        ),
                        "completed_at_unix_seconds": completed,
                    }
                )
                if accepted:
                    self.assertEqual(
                        self._generate(paths, deployment_review=review)["operations"][
                            "unattended_workers"
                        ],
                        workers,
                    )
                else:
                    with self.assertRaises(DeploymentEvidenceGenerationError):
                        self._generate(paths, deployment_review=review)

    def test_canonical_worker_contract_matches_the_live_deployment_verifier(self) -> None:
        self.assertEqual(ALLOWED_UNATTENDED_ENVIRONMENT_KEYS, ALLOWED_WORKER_ENVIRONMENT_KEYS)
        self.assertEqual(UNATTENDED_STATE_FILE, DEFAULT_WORKER_STATE_PATH.as_posix())
        self.assertEqual(UNATTENDED_LOCK_FILE, DEFAULT_WORKER_LOCK_PATH.as_posix())
        self.assertEqual(UNATTENDED_ENVIRONMENT_FILE, DEFAULT_WORKER_ENVIRONMENT_PATH.as_posix())

    def test_external_probe_source_attestation_requires_exact_hosted_run_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report_path = root / "external-probe.json"
            attestation_path = root / "attestation.json"
            report = _external_probe_source()
            report_path.write_bytes(canonical_external_probe_bytes(report))
            attestation = _external_probe_attestation(report)
            attestation_path.write_text(json.dumps(attestation), encoding="utf-8")
            digest = verify_external_probe_attestation(
                report_path,
                attestation_path,
                revision=REVISION,
                run_id=7,
                run_attempt=1,
                workflow_ref=(
                    "Yunushan/market-sentinel/.github/workflows/"
                    "deployment-evidence.yml@refs/heads/main"
                ),
                source_ref="refs/heads/main",
                now=datetime(2026, 8, 26, 11, 4, tzinfo=timezone.utc),
            )
            self.assertEqual(digest, hashlib.sha256(report_path.read_bytes()).hexdigest())

            self_hosted = copy.deepcopy(attestation)
            self_hosted[0]["verificationResult"]["signature"]["certificate"][
                "runnerEnvironment"
            ] = "self-hosted"
            attestation_path.write_text(json.dumps(self_hosted), encoding="utf-8")
            with self.assertRaisesRegex(DeploymentEvidenceGenerationError, "self-hosted"):
                verify_external_probe_attestation(
                    report_path,
                    attestation_path,
                    revision=REVISION,
                    run_id=7,
                    run_attempt=1,
                    workflow_ref=(
                        "Yunushan/market-sentinel/.github/workflows/"
                        "deployment-evidence.yml@refs/heads/main"
                    ),
                    source_ref="refs/heads/main",
                    now=datetime(2026, 8, 26, 11, 4, tzinfo=timezone.utc),
                )

            attestation_path.write_text(json.dumps(attestation), encoding="utf-8")
            changed_report = copy.deepcopy(report)
            changed_report["checks"][0]["api_version"] = "9.9.9"
            report_path.write_bytes(canonical_external_probe_bytes(changed_report))
            with self.assertRaisesRegex(DeploymentEvidenceGenerationError, "misbound"):
                verify_external_probe_attestation(
                    report_path,
                    attestation_path,
                    revision=REVISION,
                    run_id=7,
                    run_attempt=1,
                    workflow_ref=(
                        "Yunushan/market-sentinel/.github/workflows/"
                        "deployment-evidence.yml@refs/heads/main"
                    ),
                    source_ref="refs/heads/main",
                    now=datetime(2026, 8, 26, 11, 4, tzinfo=timezone.utc),
                )

    def test_rejects_noncanonical_external_probe_bytes_before_envelope_attestation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = self._files(Path(directory))
            paths["external"].write_bytes(b"{ }\n")
            with self.assertRaisesRegex(DeploymentEvidenceGenerationError, "not canonical"):
                self._generate(paths)

    def test_workflow_collects_and_independently_reviews_bound_alert_delivery(self) -> None:
        workflow = Path(".github/workflows/deployment-evidence.yml").read_text(encoding="utf-8")
        self.assertIn("secrets.token_hex(32)", workflow)
        self.assertIn("monitoring_nonce: ${{ steps.monitoring.outputs.nonce }}", workflow)
        self.assertIn("oncall_receipt_origin: ${{ steps.oncall.outputs.receipt_origin }}", workflow)
        self.assertIn("Require protected public on-call receipt service", workflow)
        self.assertIn("${{ vars.MARKET_SENTINEL_ONCALL_RECEIPT_ORIGIN }}", workflow)
        self.assertIn("Collect raw Prometheus alert-delivery evidence", workflow)
        self.assertIn('"${GITHUB_WORKSPACE}/scripts/collect_prometheus_delivery_evidence.py"', workflow)
        self.assertIn("--deployment-identity-sha256", workflow)
        self.assertIn('--nonce "${MONITORING_NONCE}"', workflow)
        self.assertIn("--prometheus-origin http://127.0.0.1:9090", workflow)
        self.assertIn("--alertmanager-origin http://127.0.0.1:9093", workflow)
        self.assertIn('--oncall-receipt-origin "${ONCALL_RECEIPT_ORIGIN}"', workflow)
        self.assertIn("MARKET_SENTINEL_ONCALL_RECEIPT_TOKEN: ${{ secrets.MARKET_SENTINEL_ONCALL_RECEIPT_TOKEN }}", workflow)
        self.assertIn("EXPECTED_ALERTMANAGER_GID: ${{ vars.MARKET_SENTINEL_ALERTMANAGER_GID }}", workflow)
        self.assertIn("--preserve-env=MARKET_SENTINEL_ONCALL_RECEIPT_TOKEN", workflow)
        self.assertIn("--oncall-url-file /etc/market-sentinel/alertmanager-oncall-webhook-url", workflow)
        self.assertIn(
            "--oncall-credentials-file /etc/market-sentinel/alertmanager-oncall-bearer-token",
            workflow,
        )
        self.assertIn('--expected-alertmanager-gid "${EXPECTED_ALERTMANAGER_GID}"', workflow)
        self.assertIn("--receiver-port 19094", workflow)
        self.assertIn('install -d -o root -g prometheus -m 0750 "${rule_dir}"', workflow)
        self.assertIn("Review raw Prometheus alert-delivery evidence", workflow)
        self.assertIn("scripts/review_prometheus_delivery_evidence.py", workflow)
        self.assertIn("--expected-rule-directory /var/lib/prometheus/market-sentinel-attestation", workflow)
        self.assertIn('--expected-oncall-receipt-origin "${ONCALL_RECEIPT_ORIGIN}"', workflow)
        self.assertIn("--alert-delivery-report", workflow)
        self.assertIn("--alert-delivery-review", workflow)
        self.assertIn('--oncall-receipt-origin "${ONCALL_RECEIPT_ORIGIN}"', workflow)
        self.assertIn("raw-alert-delivery.json", workflow)

    def test_workflow_executes_protected_checkout_verifier_not_deployed_script(self) -> None:
        workflow = Path(".github/workflows/deployment-evidence.yml").read_text(encoding="utf-8")
        self.assertEqual(workflow.count("uses: actions/checkout@"), workflow.count("persist-credentials: false"))
        self.assertIn('"${GITHUB_WORKSPACE}/scripts/verify_production_deployment.py"', workflow)
        self.assertIn("--deployment-root /opt/market-sentinel", workflow)
        self.assertIn("--external-public-probe-only", workflow)
        self.assertIn("Attest exact GitHub-hosted external probe", workflow)
        self.assertIn("Verify exact external-probe source attestation", workflow)
        self.assertIn("--deny-self-hosted-runners", workflow)
        self.assertIn('subject-path: ${{ runner.temp }}/external-deployment-probe/external-probe.json', workflow)
        self.assertIn('--run-attempt "${GITHUB_RUN_ATTEMPT}"', workflow)
        self.assertIn("runs-on: ubuntu-24.04", workflow)
        self.assertNotIn("/opt/market-sentinel/scripts/verify_production_deployment.py", workflow)

    def test_workflow_reuses_bounded_public_origin_validation_before_collection(self) -> None:
        workflow = Path(".github/workflows/deployment-evidence.yml").read_text(encoding="utf-8")
        prepare = workflow.split("  prepare:\n", 1)[1].split("  collect:\n", 1)[0]
        self.assertIn("from scripts.verify_production_deployment import _validated_public_origin", prepare)
        self.assertIn("_validated_public_origin(sys.argv[1], timeout=10)", prepare)
        self.assertIn("origin == sys.argv[1]", prepare)
        self.assertIn('test "${REQUESTED_ORIGIN}" = "${APPROVED_ORIGIN}"', prepare)
        self.assertLess(prepare.index("Checkout protected main"), prepare.index("Require exact protected production origin"))
        self.assertNotIn("getaddrinfo", prepare)
        self.assertNotIn("${{ secrets.", prepare)
        self.assertIn("needs: prepare", workflow.split("  collect:\n", 1)[1])


if __name__ == "__main__":
    unittest.main()
