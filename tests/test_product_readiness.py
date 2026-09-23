from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import nullcontext
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent.parent


def reviewed_at_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def repository_revision() -> str:
    from scripts.check_product_readiness import _repository_revision

    revision = _repository_revision()
    if len(revision) != 40:
        raise AssertionError("tests require a Git checkout with a resolvable HEAD")
    return revision


def raw_production_deployment_report(
    revision: str,
    version: str,
    *,
    now: datetime | None = None,
) -> dict[str, object]:
    """Build semantically valid but deliberately unattested collector output."""

    from core.unattended_worker import worker_invocation_sha256
    from scripts.review_deployment_evidence import required_check_names
    from scripts.verify_production_deployment import (
        DEFAULT_WORKER_ENVIRONMENT_PATH,
        DEFAULT_WORKER_LOCK_PATH,
        DEFAULT_WORKER_STATE_PATH,
        DURABLE_STATE_PATHS,
        HEALTH_CHECK_MAX_AGE_SECONDS,
        PUBLIC_PROXY_AUTH_PROBES,
        REQUIRED_HEALTH_SERVICE_PROPERTIES,
        REQUIRED_SYSTEMD_TIMER_CONTRACTS,
        REQUIRED_UNATTENDED_SERVICE_CONTRACTS,
        REQUIRED_WORKER_UNIT_CONTRACT_SHA256,
        REQUIRED_WEB_EXEC_START_PRE_COMMANDS,
        UNATTENDED_WORKER_SERVICES,
        UNATTENDED_WORKER_TASK_MAX_AGE_SECONDS,
    )

    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    collected_at = current - timedelta(minutes=2)
    backup_created_at = collected_at - timedelta(minutes=30)
    restore_completed_at = collected_at - timedelta(minutes=1)
    rollback_completed_at = collected_at - timedelta(seconds=30)
    frontend_sha256 = "b" * 64
    backup_sha256 = "c" * 64
    host_identity_sha256 = "d" * 64
    rollback_revision = "e" * 40
    backup_archive = "market-sentinel-state-test.tar.gz"
    checks: list[dict[str, object]] = [
        {"name": name, "status": "pass", "detail": "self-asserted test fixture"}
        for name in sorted(required_check_names())
    ]
    indexed = {str(check["name"]): check for check in checks}
    indexed["loopback_health"].update(
        {
            "api_version": version,
            "runtime_source_revision": revision,
            "runtime_frontend_sha256": frontend_sha256,
            "disk_frontend_sha256": frontend_sha256,
        }
    )
    indexed["public_https_proxy"].update(
        {
            "api_version": version,
            "unauthenticated_probes": len(PUBLIC_PROXY_AUTH_PROBES),
            "runtime_source_revision": revision,
            "runtime_frontend_sha256": frontend_sha256,
        }
    )
    indexed["deployment_host_identity"].update(
        {
            "deployment_provider": "test-provider",
            "host_identity_sha256": host_identity_sha256,
        }
    )
    indexed["durable_state_wiring"].update(
        {
            "durable_store_count": len(DURABLE_STATE_PATHS),
            "state_directory": "/var/lib/market-sentinel",
            "backup_source": "/var/lib/market-sentinel",
        }
    )
    indexed["health_credential_isolation"].update(
        {
            "environment_path": "/etc/market-sentinel/market-sentinel-health.env",
            "service_user": "market-sentinel-health",
            "service_group": "market-sentinel-health",
            "private_user_namespace": True,
            "process_visibility": "invisible",
            "verified_service_property_count": len(REQUIRED_HEALTH_SERVICE_PROPERTIES) + 1,
            "environment_variable_count": 1,
            "admin_environment_unset": True,
            "token_preflight_removed": True,
            "probe_requires_observability": True,
            "web_startup_probe_removed": True,
            "inline_environment_empty": True,
            "manager_environment_not_passed": True,
        }
    )
    indexed["web_startup_preflight"].update(
        {
            "expected_command_count": len(REQUIRED_WEB_EXEC_START_PRE_COMMANDS),
            "command_count": len(REQUIRED_WEB_EXEC_START_PRE_COMMANDS),
            "commands": [list(command) for command in REQUIRED_WEB_EXEC_START_PRE_COMMANDS],
            "commands_succeeded": True,
        }
    )
    indexed["systemd_timer_contracts"].update(
        {
            "expected_timer_count": len(REQUIRED_SYSTEMD_TIMER_CONTRACTS),
            "timer_count": len(REQUIRED_SYSTEMD_TIMER_CONTRACTS),
            "timers": deepcopy(REQUIRED_SYSTEMD_TIMER_CONTRACTS),
        }
    )
    monitoring_completed_at = collected_at - timedelta(minutes=1)
    indexed["systemd_recent_success_market-sentinel-health.service"].update(
        {
            "unit": "market-sentinel-health.service",
            "completed_at": monitoring_completed_at.strftime("%a %Y-%m-%d %H:%M:%S UTC"),
            "completed_at_unix_seconds": monitoring_completed_at.timestamp(),
            "age_seconds": 60,
            "max_age_seconds": HEALTH_CHECK_MAX_AGE_SECONDS,
        }
    )
    worker_finish_times = {
        "alerts-refresh": collected_at - timedelta(seconds=30.75),
        "wallets-poll": collected_at - timedelta(seconds=90.25),
    }
    worker_run_ids = {
        "alerts-refresh": "00000000-0000-4000-8000-000000000201",
        "wallets-poll": "00000000-0000-4000-8000-000000000202",
    }
    worker_tasks: dict[str, dict[str, object]] = {}
    worker_services: dict[str, dict[str, object]] = {}
    for service, identity in UNATTENDED_WORKER_SERVICES.items():
        task = identity["task"]
        finished = worker_finish_times[task]
        started = finished - timedelta(seconds=2)
        attempted = finished - timedelta(seconds=1)
        completed = finished.replace(microsecond=0)
        maximum_age = UNATTENDED_WORKER_TASK_MAX_AGE_SECONDS[task]
        worker_tasks[task] = {
            "service": service,
            "timer": identity["timer"],
            "state": "succeeded",
            "run_id": worker_run_ids[task],
            "source_revision": revision,
            "service_unit": service,
            "unit_contract_sha256": REQUIRED_WORKER_UNIT_CONTRACT_SHA256[service],
            "invocation_sha256": worker_invocation_sha256(
                task=task,
                service_unit=service,
                source_revision=revision,
                unit_contract_sha256=REQUIRED_WORKER_UNIT_CONTRACT_SHA256[service],
            ),
            "last_started_at": started.isoformat().replace("+00:00", "Z"),
            "last_started_at_unix_seconds": started.timestamp(),
            "last_attempt_at": attempted.isoformat().replace("+00:00", "Z"),
            "last_attempt_at_unix_seconds": attempted.timestamp(),
            "last_finished_at": finished.isoformat().replace("+00:00", "Z"),
            "last_finished_at_unix_seconds": finished.timestamp(),
            "last_success_at": finished.isoformat().replace("+00:00", "Z"),
            "last_success_at_unix_seconds": finished.timestamp(),
            "last_duration_seconds": 2.0,
            "deadline_seconds": 90.0,
            "max_attempts": 3,
            "attempts_completed": 1,
            "last_attempt_outcome": "succeeded",
            "last_outcome": "succeeded",
            "last_attempt_processed": 2,
            "last_attempt_problems": 0,
            "last_attempt_emitted": 1,
            "processed": 2,
            "problems": 0,
            "emitted": 1,
            "consecutive_failures": 0,
            "abandoned_runs": 0,
            "total_runs": 4,
            "total_successes": 3,
            "total_failures": 1,
            "freshness_age_seconds": (collected_at - finished).total_seconds(),
            "max_age_seconds": maximum_age,
        }
        completed_text = completed.strftime("%a %Y-%m-%d %H:%M:%S UTC")
        service_evidence = {
            "task": task,
            "timer": identity["timer"],
            "completed_at": completed_text,
            "completed_at_unix_seconds": completed.timestamp(),
            "age_seconds": (collected_at - completed).total_seconds(),
            "max_age_seconds": maximum_age,
            "source_revision": revision,
            "unit_contract_sha256": REQUIRED_WORKER_UNIT_CONTRACT_SHA256[service],
        }
        worker_services[service] = service_evidence
        indexed[f"systemd_recent_success_{service}"].update(
            {
                "unit": service,
                "completed_at": completed_text,
                "completed_at_unix_seconds": completed.timestamp(),
                "age_seconds": (collected_at - completed).total_seconds(),
                "max_age_seconds": maximum_age,
            }
        )
    latest_worker_finish = max(worker_finish_times.values())
    indexed["unattended_workers"].update(
        {
            "state_file": DEFAULT_WORKER_STATE_PATH.as_posix(),
            "state_schema_version": 1,
            "state_updated_at": latest_worker_finish.isoformat().replace("+00:00", "Z"),
            "state_updated_at_unix_seconds": latest_worker_finish.timestamp(),
            "state_sha256": "9" * 64,
            "lock_file": DEFAULT_WORKER_LOCK_PATH.as_posix(),
            "environment_file": DEFAULT_WORKER_ENVIRONMENT_PATH.as_posix(),
            "environment_keys": ["MARKET_SENTINEL_SOURCE_REVISION"],
            "expected_service_count": len(UNATTENDED_WORKER_SERVICES),
            "service_count": len(UNATTENDED_WORKER_SERVICES),
            "service_contracts": deepcopy(REQUIRED_UNATTENDED_SERVICE_CONTRACTS),
            "services": worker_services,
            "tasks": worker_tasks,
        }
    )
    indexed["verified_recent_state_backup"].update(
        {
            "created_at": backup_created_at.isoformat().replace("+00:00", "Z"),
            "backup_age_seconds": 30 * 60,
            "archive": backup_archive,
            "sha256": backup_sha256,
            "file_count": 1,
            "verified_bytes": 128,
            "verified_pairs": 1,
            "invalid_pairs": 0,
            "orphan_archives": 0,
            "orphan_manifests": 0,
        }
    )
    indexed["verified_restore_drill"].update(
        {
            "mode": "isolated_full_restore",
            "archive": backup_archive,
            "backup_created_at": backup_created_at.isoformat().replace("+00:00", "Z"),
            "backup_sha256": backup_sha256,
            "restored_file_count": 1,
            "restored_bytes": 128,
            "completed_at": restore_completed_at.isoformat().replace("+00:00", "Z"),
            "application": {
                "schema_version": 1, "config_loaded": True, "health_ready": True,
                "state_readable": True, "mutations_blocked": True, "outbound_attempts": 0,
                "files_unchanged": True, "sqlite_databases_checked": 0,
                "api_version": version, "runtime_source_revision": revision,
                "runtime_frontend_sha256": frontend_sha256,
            },
        }
    )
    indexed["verified_production_rollback_drill"].update(
        {
            "drill_id": "00000000-0000-4000-8000-000000000001",
            "report_sha256": "f" * 64,
            "completed_at": rollback_completed_at.isoformat().replace("+00:00", "Z"),
            "rollback_revision": rollback_revision,
            "final_revision": revision,
            "step_count": 5,
        }
    )
    return {
        "schema_version": 1,
        "collected_at": collected_at.isoformat().replace("+00:00", "Z"),
        "source": {
            "project_version": version,
            "git_revision": revision,
            "git_revision_status": "ok",
            "git_worktree_status": "clean",
        },
        "status": "ok",
        "checks": checks,
        "collection": {
            "mode": "production",
            "systemd_requested": True,
            "public_proxy_requested": True,
            "public_origin": "https://markets.example.net",
            "expected_version": version,
            "expected_source_revision": revision,
            "expected_frontend_sha256": frontend_sha256,
            "deployment_provider": "test-provider",
            "host_identity_sha256": host_identity_sha256,
            "restore_drill_requested": True,
            "rollback_drill_requested": True,
            "run_id": 0,
            "run_attempt": 0,
            "nonce": "",
        },
    }


def score_deployment_evidence(path: Path, revision: str) -> dict[str, Any]:
    from scripts.check_product_readiness import _parser, build_report

    args = _parser().parse_args(["--no-run-local", "--deployment-evidence", str(path)])
    with (
        patch("scripts.check_product_readiness._repository_is_clean", return_value=True),
        patch("scripts.check_product_readiness._repository_revision", return_value=revision),
    ):
        return build_report(args)


def attested_public_live_payload(revision: str, *, now: datetime | None = None) -> dict[str, object]:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    started = current - timedelta(minutes=4)
    completed = current - timedelta(minutes=3)
    generated = current - timedelta(minutes=2)
    return {
        "ok": True,
        "mode": "public_only",
        "market_id": "polymarket",
        "public_checks": {
            name: {"status": "ok", "detail": "read-only endpoint responded"}
            for name in ("clob_time", "gamma_markets", "data_leaderboard", "bridge_supported_assets")
        },
        "safety": {
            "dotenv_loaded": False,
            "credentials_present": False,
            "credential_variables_present": [],
            "authenticated_reads_attempted": False,
            "authenticated_user_websocket_attempted": False,
            "bridge_mutations_attempted": False,
            "funded_orders_attempted": False,
            "public_requests_read_only": True,
        },
        "evidence": {
            "schema_version": 1,
            "profile": "public-only",
            "repository": "Yunushan/market-sentinel",
            "source_revision": revision,
            "run_id": 123456,
            "run_attempt": 1,
            "workflow": ".github/workflows/ci.yml",
            "workflow_name": "CI",
            "workflow_ref": "Yunushan/market-sentinel/.github/workflows/ci.yml@refs/heads/main",
            "event": "workflow_dispatch",
            "runner_environment": "github-hosted",
            "generated_at": generated.isoformat().replace("+00:00", "Z"),
            "started_at": started.isoformat().replace("+00:00", "Z"),
            "completed_at": completed.isoformat().replace("+00:00", "Z"),
        },
    }


def successful_public_live_gh_run(
    revision: str,
    *,
    now: datetime | None = None,
    run_overrides: dict[str, object] | None = None,
    jobs_overrides: dict[str, object] | None = None,
    attestation_mutator: Callable[[list[dict[str, Any]]], None] | None = None,
    reverse_required_steps: bool = False,
) -> Callable[..., subprocess.CompletedProcess[bytes]]:
    current = now or datetime.now(timezone.utc)

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        if command[:3] == ["gh", "attestation", "verify"]:
            import hashlib

            report_hash = hashlib.sha256(Path(command[3]).read_bytes()).hexdigest()
            workflow_ref = "refs/heads/main"
            workflow_uri = (
                "https://github.com/Yunushan/market-sentinel/.github/workflows/ci.yml@" + workflow_ref
            )
            repository_uri = "https://github.com/Yunushan/market-sentinel"
            invocation_uri = "https://github.com/Yunushan/market-sentinel/actions/runs/123456/attempts/1"
            attestation = [
                {
                    "attestation": {"bundle": "verified"},
                    "verificationResult": {
                        "mediaType": "application/vnd.dev.sigstore.verificationresult+json;version=0.1",
                        "statement": {
                            "_type": "https://in-toto.io/Statement/v1",
                            "predicateType": "https://slsa.dev/provenance/v1",
                            "subject": [
                                {
                                    "name": "public-polymarket-live.json",
                                    "digest": {"sha256": report_hash},
                                }
                            ],
                            "predicate": {
                                "buildDefinition": {
                                    "buildType": "https://actions.github.io/buildtypes/workflow/v1",
                                    "externalParameters": {
                                        "workflow": {
                                            "path": ".github/workflows/ci.yml",
                                            "ref": workflow_ref,
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
                                            "uri": f"git+{repository_uri}@{workflow_ref}",
                                            "digest": {"gitCommit": revision},
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
                                "buildSignerDigest": revision,
                                "runnerEnvironment": "github-hosted",
                                "sourceRepositoryURI": repository_uri,
                                "sourceRepositoryDigest": revision,
                                "sourceRepositoryRef": workflow_ref,
                                "sourceRepositoryOwnerURI": "https://github.com/Yunushan",
                                "buildConfigURI": workflow_uri,
                                "buildConfigDigest": revision,
                                "buildTrigger": "workflow_dispatch",
                                "runInvocationURI": invocation_uri,
                                "sourceRepositoryVisibilityAtSigning": "public",
                            }
                        },
                        "verifiedTimestamps": [
                            {
                                "type": "Tlog",
                                "timestamp": (current - timedelta(minutes=1)).isoformat().replace(
                                    "+00:00", "Z"
                                ),
                            }
                        ],
                    },
                }
            ]
            if attestation_mutator is not None:
                attestation_mutator(attestation)
            return subprocess.CompletedProcess(command, 0, json.dumps(attestation).encode("utf-8"), b"")
        if command[:2] == ["gh", "api"] and "/jobs?" in command[-1]:
            jobs_payload = {
                "total_count": 1,
                "jobs": [
                    {
                        "name": "Public Polymarket live / GitHub-hosted",
                        "status": "completed",
                        "conclusion": "success",
                        "labels": ["ubuntu-24.04"],
                        "started_at": (current - timedelta(minutes=4)).isoformat().replace("+00:00", "Z"),
                        "completed_at": (current - timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
                        "steps": list(reversed([
                            {"name": name, "status": "completed", "conclusion": "success"}
                            for name in (
                                "Verify exact clean source before probe",
                                "Probe reviewed public Polymarket endpoints",
                                "Revalidate public-only evidence before attestation",
                                "Reverify exact clean source after probe",
                                "Attest exact public-live evidence file",
                                "Upload public-live evidence",
                            )
                        ])) if reverse_required_steps else [
                            {"name": name, "status": "completed", "conclusion": "success"}
                            for name in (
                                "Verify exact clean source before probe",
                                "Probe reviewed public Polymarket endpoints",
                                "Revalidate public-only evidence before attestation",
                                "Reverify exact clean source after probe",
                                "Attest exact public-live evidence file",
                                "Upload public-live evidence",
                            )
                        ],
                    }
                ],
                **(jobs_overrides or {}),
            }
            return subprocess.CompletedProcess(command, 0, json.dumps(jobs_payload).encode("utf-8"), b"")
        if command[:2] == ["gh", "api"]:
            payload = {
                "id": 123456,
                "head_sha": revision,
                "name": "CI",
                "path": ".github/workflows/ci.yml",
                "event": "workflow_dispatch",
                "status": "completed",
                "conclusion": "success",
                "run_attempt": 1,
                "head_branch": "main",
                "created_at": (current - timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
                "run_started_at": (current - timedelta(minutes=4)).isoformat().replace("+00:00", "Z"),
                "updated_at": (current - timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
                "head_repository": {"full_name": "Yunushan/market-sentinel"},
                **(run_overrides or {}),
            }
            return subprocess.CompletedProcess(command, 0, json.dumps(payload).encode("utf-8"), b"")
        raise AssertionError(f"unexpected command: {command}")

    return run


def successful_public_live_gh_json(
    revision: str,
    **kwargs: Any,
) -> Callable[..., tuple[object | None, str]]:
    run = successful_public_live_gh_run(revision, **kwargs)

    def query(command: list[str], **_kwargs: object) -> tuple[object | None, str]:
        result = run(command)
        if result.returncode != 0:
            return None, "GitHub CLI verification failed"
        return json.loads(result.stdout), ""

    return query


class ProductReadinessTests(unittest.TestCase):
    def test_public_live_probe_retries_transient_failure(self) -> None:
        calls = 0

        def run_probe(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            nonlocal calls
            calls += 1
            report_path = Path(command[command.index("--report-file") + 1])
            if calls == 2:
                report_path.write_text(
                    json.dumps(
                        {
                            "ok": True,
                            "public_checks": {
                                name: {"status": "ok"}
                                for name in ("clob_time", "gamma_markets", "data_leaderboard", "bridge_supported_assets")
                            },
                        }
                    ),
                    encoding="utf-8",
                )
                return subprocess.CompletedProcess(command, 0, "", "")
            return subprocess.CompletedProcess(command, 1, "", "transient endpoint failure")

        with (
            patch("scripts.check_product_readiness.subprocess.run", side_effect=run_probe),
            patch("scripts.check_product_readiness.time.sleep") as sleep,
        ):
            from scripts.check_product_readiness import _run_public_live

            result = _run_public_live()

        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["attempt"], 2)
        self.assertEqual(calls, 2)
        self.assertEqual(
            set(result["report"]["public_check_statuses"]),
            {"clob_time", "gamma_markets", "data_leaderboard", "bridge_supported_assets"},
        )
        self.assertIn("output", result)
        sleep.assert_called_once()

    def test_public_live_probe_requires_exact_check_keys(self) -> None:
        required = ("clob_time", "gamma_markets", "data_leaderboard", "bridge_supported_assets")

        for names in (required[:-1], (*required, "unreviewed_endpoint")):
            with self.subTest(names=names):

                def run_probe(
                    command: list[str],
                    check_names: tuple[str, ...] = names,
                    **kwargs: object,
                ) -> subprocess.CompletedProcess[str]:
                    report_path = Path(command[command.index("--report-file") + 1])
                    report_path.write_text(
                        json.dumps(
                            {
                                "ok": True,
                                "public_checks": {name: {"status": "ok"} for name in check_names},
                            }
                        ),
                        encoding="utf-8",
                    )
                    return subprocess.CompletedProcess(command, 0, "", "")

                with (
                    patch("scripts.check_product_readiness.subprocess.run", side_effect=run_probe),
                    patch("scripts.check_product_readiness.time.sleep"),
                ):
                    from scripts.check_product_readiness import _run_public_live

                    result = _run_public_live()

                self.assertEqual(result["status"], "fail")
                self.assertEqual(result["attempt"], 2)
                self.assertNotIn("unreviewed_endpoint", json.dumps(result))

    def test_gate_output_metadata_does_not_retain_output_contents(self) -> None:
        completed = subprocess.CompletedProcess(
            [sys.executable, "verify.py"],
            0,
            "stdout-secret-value\nsecond line\n",
            "stderr-secret-value\n",
        )
        with patch("scripts.check_product_readiness.subprocess.run", return_value=completed):
            from scripts.check_product_readiness import _run_local_gates

            result = _run_local_gates(False)

        serialized = json.dumps(result)
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["output"]["stdout"]["lines"], 2)
        self.assertEqual(result["output"]["stderr"]["lines"], 1)
        self.assertNotIn("stdout-secret-value", serialized)
        self.assertNotIn("stderr-secret-value", serialized)

    def test_public_live_probe_fails_after_retries(self) -> None:
        calls = 0

        def run_probe(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            nonlocal calls
            calls += 1
            return subprocess.CompletedProcess(command, 1, "", "transient endpoint failure")

        with (
            patch("scripts.check_product_readiness.subprocess.run", side_effect=run_probe),
            patch("scripts.check_product_readiness.time.sleep") as sleep,
        ):
            from scripts.check_product_readiness import _run_public_live

            result = _run_public_live()

        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["attempt"], 2)
        self.assertEqual(calls, 2)
        sleep.assert_called_once()

    def test_attested_public_live_report_accepts_exact_fresh_github_evidence(self) -> None:
        from scripts.check_product_readiness import _attested_public_live_report

        revision = repository_revision()
        now = datetime.now(timezone.utc)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "public-live.json"
            path.write_text(json.dumps(attested_public_live_payload(revision, now=now)), encoding="utf-8")
            with patch(
                "scripts.check_product_readiness._run_gh_json",
                side_effect=successful_public_live_gh_json(revision, now=now),
            ) as run:
                result = _attested_public_live_report(str(path), expected_revision=revision, now=now)

        self.assertEqual(result["status"], "pass", result)
        self.assertEqual(result["evidence"]["run_id"], 123456)
        self.assertEqual(result["evidence"]["github_job"], "verified")
        commands = [call.args[0] for call in run.call_args_list]
        attestation_command = commands[0]
        self.assertEqual(attestation_command[:3], ["gh", "attestation", "verify"])
        self.assertEqual(
            attestation_command[attestation_command.index("--repo") + 1],
            "Yunushan/market-sentinel",
        )
        # Identity and runner policy are enforced from the signed verification
        # result by _attestation_result_matches. Passing gh's optional verifier
        # filters here is not portable: current gh versions reject several
        # combinations before returning otherwise-valid attestations.
        for unsupported_filter in (
            "--cert-identity",
            "--signer-workflow",
            "--signer-digest",
            "--source-digest",
            "--source-ref",
            "--deny-self-hosted-runners",
        ):
            self.assertNotIn(unsupported_filter, attestation_command)
        self.assertTrue(any(command[-1].endswith("/actions/runs/123456") for command in commands))
        self.assertTrue(any("/actions/runs/123456/jobs?" in command[-1] for command in commands))

    def test_attested_public_live_report_rejects_forged_or_unsafe_content_before_gh(self) -> None:
        from scripts.check_product_readiness import _attested_public_live_report

        revision = repository_revision()
        now = datetime.now(timezone.utc)
        variants: list[tuple[str, dict[str, object]]] = []
        forged = attested_public_live_payload(revision, now=now)
        forged["evidence"]["source_revision"] = "f" * 40  # type: ignore[index]
        variants.append(("forged revision", forged))
        unsafe = attested_public_live_payload(revision, now=now)
        unsafe["safety"]["credentials_present"] = True  # type: ignore[index]
        variants.append(("credential present", unsafe))
        missing_check = attested_public_live_payload(revision, now=now)
        del missing_check["public_checks"]["clob_time"]  # type: ignore[index]
        variants.append(("missing public check", missing_check))
        injected_ref = attested_public_live_payload(revision, now=now)
        injected_ref["evidence"]["workflow_ref"] = (  # type: ignore[index]
            "Yunushan/market-sentinel/.github/workflows/ci.yml@refs/heads/main'$(touch injected)'"
        )
        variants.append(("shell metacharacters in ref", injected_ref))

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "public-live.json"
            for label, payload in variants:
                with self.subTest(label=label):
                    path.write_text(json.dumps(payload), encoding="utf-8")
                    with patch("scripts.check_product_readiness.subprocess.run") as run:
                        result = _attested_public_live_report(str(path), expected_revision=revision, now=now)
                    self.assertEqual(result["status"], "fail")
                    run.assert_not_called()

    def test_attested_public_live_report_rejects_duplicate_keys_and_nan(self) -> None:
        from scripts.check_product_readiness import _attested_public_live_report

        revision = repository_revision()
        malformed_values = (
            '{"ok":true,"ok":true}',
            '{"ok":NaN}',
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "public-live.json"
            for value in malformed_values:
                with self.subTest(value=value):
                    path.write_text(value, encoding="utf-8")
                    with patch("scripts.check_product_readiness.subprocess.run") as run:
                        result = _attested_public_live_report(str(path), expected_revision=revision)
                    self.assertEqual(result["status"], "fail")
                    self.assertIn("malformed", result["detail"])
                    run.assert_not_called()

    def test_attested_public_live_report_fails_closed_when_attestation_fails(self) -> None:
        from scripts.check_product_readiness import _attested_public_live_report

        revision = repository_revision()
        now = datetime.now(timezone.utc)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "public-live.json"
            path.write_text(json.dumps(attested_public_live_payload(revision, now=now)), encoding="utf-8")

            def fail_attestation(command: list[str], **kwargs: object) -> tuple[None, str]:
                self.assertEqual(command[:3], ["gh", "attestation", "verify"])
                return None, "GitHub CLI verification failed"

            with patch("scripts.check_product_readiness._run_gh_json", side_effect=fail_attestation) as run:
                result = _attested_public_live_report(str(path), expected_revision=revision, now=now)

        self.assertEqual(result["status"], "fail")
        self.assertIn("attestation", result["detail"].casefold())
        self.assertEqual(run.call_count, 1)

    def test_attested_public_live_report_rejects_github_run_mismatch(self) -> None:
        from scripts.check_product_readiness import _attested_public_live_report

        revision = repository_revision()
        now = datetime.now(timezone.utc)
        mismatches = (
            {"head_sha": "f" * 40},
            {"event": "push"},
            {"conclusion": "failure"},
            {"run_attempt": 2},
            {"path": ".github/workflows/release.yml"},
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "public-live.json"
            path.write_text(json.dumps(attested_public_live_payload(revision, now=now)), encoding="utf-8")
            for mismatch in mismatches:
                with self.subTest(mismatch=mismatch):
                    with patch(
                        "scripts.check_product_readiness._run_gh_json",
                        side_effect=successful_public_live_gh_json(
                            revision,
                            now=now,
                            run_overrides=mismatch,
                        ),
                    ):
                        result = _attested_public_live_report(str(path), expected_revision=revision, now=now)
                    self.assertEqual(result["status"], "fail")
                    self.assertIn("run identity", result["detail"])

    def test_attested_public_live_report_rejects_paginated_or_inconsistent_jobs(self) -> None:
        from scripts.check_product_readiness import _attested_public_live_report

        revision = repository_revision()
        now = datetime.now(timezone.utc)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "public-live.json"
            path.write_text(json.dumps(attested_public_live_payload(revision, now=now)), encoding="utf-8")
            for total_count in (2, 101):
                with self.subTest(total_count=total_count):
                    with patch(
                        "scripts.check_product_readiness._run_gh_json",
                        side_effect=successful_public_live_gh_json(
                            revision,
                            now=now,
                            jobs_overrides={"total_count": total_count},
                        ),
                    ):
                        result = _attested_public_live_report(str(path), expected_revision=revision, now=now)
                    self.assertEqual(result["status"], "fail")
                    self.assertIn("public job", result["detail"])

    def test_attested_public_live_report_rejects_reordered_safety_steps(self) -> None:
        from scripts.check_product_readiness import _attested_public_live_report

        revision = repository_revision()
        now = datetime.now(timezone.utc)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "public-live.json"
            path.write_text(json.dumps(attested_public_live_payload(revision, now=now)), encoding="utf-8")
            with patch(
                "scripts.check_product_readiness._run_gh_json",
                side_effect=successful_public_live_gh_json(
                    revision,
                    now=now,
                    reverse_required_steps=True,
                ),
            ):
                result = _attested_public_live_report(str(path), expected_revision=revision, now=now)
        self.assertEqual(result["status"], "fail")
        self.assertIn("in order", result["detail"])

    def test_attested_public_live_report_rejects_immutable_binding_mutations(self) -> None:
        from scripts.check_product_readiness import _attested_public_live_report

        revision = repository_revision()
        now = datetime.now(timezone.utc)
        mutations: tuple[tuple[str, tuple[str | int, ...], object], ...] = (
            ("subject name", (0, "verificationResult", "statement", "subject", 0, "name"), "other.json"),
            ("subject digest", (0, "verificationResult", "statement", "subject", 0, "digest", "sha256"), "0" * 64),
            ("predicate type", (0, "verificationResult", "statement", "predicateType"), "https://example.invalid"),
            ("certificate SAN", (0, "verificationResult", "signature", "certificate", "subjectAlternativeName"), "https://example.invalid"),
            ("signer digest", (0, "verificationResult", "signature", "certificate", "buildSignerDigest"), "f" * 40),
            ("source ref", (0, "verificationResult", "signature", "certificate", "sourceRepositoryRef"), "refs/heads/other"),
            ("runner", (0, "verificationResult", "signature", "certificate", "runnerEnvironment"), "self-hosted"),
            ("config digest", (0, "verificationResult", "signature", "certificate", "buildConfigDigest"), "f" * 40),
            ("trigger", (0, "verificationResult", "signature", "certificate", "buildTrigger"), "push"),
            ("invocation", (0, "verificationResult", "signature", "certificate", "runInvocationURI"), "https://example.invalid"),
            ("timestamps", (0, "verificationResult", "verifiedTimestamps"), []),
        )

        def set_path(value: list[dict[str, Any]], path: tuple[str | int, ...], replacement: object) -> None:
            target: Any = value
            for part in path[:-1]:
                target = target[part]
            target[path[-1]] = replacement

        with tempfile.TemporaryDirectory() as temporary:
            report_path = Path(temporary) / "public-live.json"
            report_path.write_text(
                json.dumps(attested_public_live_payload(revision, now=now)),
                encoding="utf-8",
            )
            for label, mutation_path, replacement in mutations:
                with self.subTest(label=label):
                    def mutate(
                        attestation: list[dict[str, Any]],
                        path: tuple[str | int, ...] = mutation_path,
                        changed: object = replacement,
                    ) -> None:
                        set_path(attestation, path, deepcopy(changed))

                    with patch(
                        "scripts.check_product_readiness._run_gh_json",
                        side_effect=successful_public_live_gh_json(
                            revision,
                            now=now,
                            attestation_mutator=mutate,
                        ),
                    ):
                        result = _attested_public_live_report(
                            str(report_path),
                            expected_revision=revision,
                            now=now,
                        )
                    self.assertEqual(result["status"], "fail")
                    self.assertIn("attestation", result["detail"].casefold())

    def test_direct_public_probe_is_diagnostic_when_attested_evidence_passes(self) -> None:
        from scripts.check_product_readiness import _parser, build_report

        revision = repository_revision()
        args = _parser().parse_args(
            ["--no-run-local", "--run-public-live", "--public-live-report", "attested.json"]
        )
        with (
            patch("scripts.check_product_readiness._repository_is_clean", return_value=True),
            patch("scripts.check_product_readiness._repository_revision", return_value=revision),
            patch("scripts.check_product_readiness._run_public_live", return_value={"status": "pass"}),
            patch(
                "scripts.check_product_readiness._attested_public_live_report",
                return_value={"status": "pass"},
            ),
        ):
            report = build_report(args)

        live = next(item for item in report["categories"] if item["name"] == "live_acceptance")
        self.assertEqual(live["earned"], 3)
        self.assertEqual(report["checks"]["public_live"]["award_source"], "attested")
        self.assertEqual(report["checks"]["public_live"]["diagnostic_status"], "pass")

    def test_attested_public_live_points_are_revoked_when_repository_changes(self) -> None:
        from scripts.check_product_readiness import _parser, build_report

        initial_revision = "a" * 40
        final_revision = "b" * 40
        args = _parser().parse_args(["--no-run-local", "--public-live-report", "attested.json"])
        with (
            patch("scripts.check_product_readiness._repository_is_clean", side_effect=(True, True)),
            patch(
                "scripts.check_product_readiness._repository_revision",
                side_effect=(initial_revision, final_revision),
            ),
            patch(
                "scripts.check_product_readiness._attested_public_live_report",
                return_value={"status": "pass"},
            ),
        ):
            report = build_report(args)

        live = next(item for item in report["categories"] if item["name"] == "live_acceptance")
        self.assertEqual(live["earned"], 0)
        self.assertTrue(any("public Polymarket" in item and "revoked" in item for item in live["missing"]))

    def test_direct_public_live_probe_never_earns_points(self) -> None:
        from scripts.check_product_readiness import _parser, build_report

        revision = "a" * 40
        args = _parser().parse_args(["--no-run-local", "--run-public-live"])
        with (
            patch("scripts.check_product_readiness._repository_is_clean", side_effect=(True, True)),
            patch("scripts.check_product_readiness._repository_revision", return_value=revision),
            patch("scripts.check_product_readiness._run_public_live", return_value={"status": "pass"}),
        ):
            report = build_report(args)

        live = next(item for item in report["categories"] if item["name"] == "live_acceptance")
        self.assertEqual(live["earned"], 0)
        self.assertEqual(report["checks"]["public_live"]["award_source"], "none")
        self.assertEqual(report["checks"]["public_live"]["diagnostic_status"], "pass")

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

    def test_complete_legacy_deployment_wrapper_cannot_award_points(self) -> None:
        from scripts.check_product_readiness import REQUIRED_DEPLOYMENT_CHECKS, _project_version

        revision = "a" * 40
        wrapper = {
            "verified": True,
            "schema_version": 1,
            "evidence_type": "deployment",
            "reviewed_by": "self-asserted-reviewer",
            "reviewed_at": reviewed_at_now(),
            "source": "self-asserted test wrapper",
            "scope": "production-host",
            "environment": "production",
            "expected_version": _project_version(),
            "source_revision": revision,
            "checks": [
                {"name": name, "status": "pass"} for name in REQUIRED_DEPLOYMENT_CHECKS
            ],
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "deployment.json"
            path.write_text(json.dumps(wrapper), encoding="utf-8")
            report = score_deployment_evidence(path, revision)

        operations = next(item for item in report["categories"] if item["name"] == "operations_recovery")
        self.assertEqual(operations["earned"], 10)
        self.assertTrue(any("strict semantic review" in item for item in operations["missing"]))

    def test_handwritten_valid_raw_deployment_report_remains_diagnostic_only(self) -> None:
        from scripts.check_product_readiness import _project_version
        from scripts.review_deployment_evidence import review_deployment_report

        revision = "a" * 40
        version = _project_version()
        raw = raw_production_deployment_report(revision, version)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "raw-deployment.json"
            path.write_text(json.dumps(raw, sort_keys=True), encoding="utf-8")
            semantic_review = review_deployment_report(
                path,
                expected_version=version,
                expected_revision=revision,
            )
            report = score_deployment_evidence(path, revision)

        self.assertEqual(semantic_review["status"], "ok")
        operations = next(item for item in report["categories"] if item["name"] == "operations_recovery")
        self.assertEqual(operations["earned"], 10)
        self.assertTrue(any("passed semantic review" in item for item in operations["missing"]))
        self.assertTrue(any("not score-eligible" in item for item in operations["missing"]))

    def test_deployment_reviewer_summary_cannot_award_points(self) -> None:
        from scripts.check_product_readiness import _project_version
        from scripts.review_deployment_evidence import review_deployment_report

        revision = "a" * 40
        version = _project_version()
        raw = raw_production_deployment_report(revision, version)
        with tempfile.TemporaryDirectory() as temporary:
            raw_path = Path(temporary) / "raw-deployment.json"
            summary_path = Path(temporary) / "review-summary.json"
            raw_path.write_text(json.dumps(raw, sort_keys=True), encoding="utf-8")
            summary = review_deployment_report(
                raw_path,
                expected_version=version,
                expected_revision=revision,
            )
            summary_path.write_text(json.dumps(summary, sort_keys=True), encoding="utf-8")
            report = score_deployment_evidence(summary_path, revision)

        self.assertEqual(summary["status"], "ok")
        operations = next(item for item in report["categories"] if item["name"] == "operations_recovery")
        self.assertEqual(operations["earned"], 10)
        self.assertTrue(any("strict semantic review" in item for item in operations["missing"]))

    def test_reviewed_partial_external_evidence_awards_only_its_scoped_points(self) -> None:
        from scripts.check_product_readiness import (
            REQUIRED_PLATFORM_CI_CHECKS,
            REQUIRED_RELEASE_ENVIRONMENT_CHECKS,
        )

        manifest = {
            "verified": True,
            "schema_version": 1,
            "reviewed_by": "test-reviewer",
            "reviewed_at": reviewed_at_now(),
            "checks": [{"name": name, "status": "pass"} for name in REQUIRED_PLATFORM_CI_CHECKS],
        }
        revision = repository_revision()
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
                        "source_revision": revision,
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
                        "checks": [
                            {"name": name, "status": "pass"} for name in REQUIRED_RELEASE_ENVIRONMENT_CHECKS
                        ],
                    }
                ),
                encoding="utf-8",
            )
            from scripts.check_product_readiness import _parser, build_report

            args = _parser().parse_args(
                [
                    "--no-run-local",
                    "--platform-ci-evidence",
                    str(platform_path),
                    "--release-environment-evidence",
                    str(release_environment_path),
                ]
            )
            with (
                patch("scripts.check_product_readiness._repository_is_clean", return_value=True),
                patch("scripts.check_product_readiness._repository_revision", return_value=revision),
            ):
                report = build_report(args)

        platform = next(item for item in report["categories"] if item["name"] == "platform_evidence")
        ci_cd = next(item for item in report["categories"] if item["name"] == "ci_cd_release")
        self.assertEqual(platform["earned"], 5)
        self.assertEqual(ci_cd["earned"], 14)
        self.assertTrue(any("diagnostic-only" in item for item in platform["missing"]))
        self.assertTrue(any("diagnostic-only" in item for item in ci_cd["missing"]))

    def test_release_environment_evidence_requires_exact_security_checks(self) -> None:
        from scripts.check_product_readiness import REQUIRED_RELEASE_ENVIRONMENT_CHECKS, _reviewed_evidence

        base = {
            "verified": True,
            "schema_version": 1,
            "evidence_type": "release-environment",
            "reviewed_by": "test-reviewer",
            "reviewed_at": reviewed_at_now(),
            "source": "test",
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "release-environment.json"
            path.write_text(
                json.dumps({**base, "checks": [{"name": "release_environment_exists", "status": "pass"}]}),
                encoding="utf-8",
            )
            incomplete, detail = _reviewed_evidence(
                str(path),
                "release-environment",
                evidence_type="release-environment",
                required_checks=REQUIRED_RELEASE_ENVIRONMENT_CHECKS,
            )

            path.write_text(
                json.dumps(
                    {
                        **base,
                        "checks": [
                            {"name": name, "status": "pass"} for name in REQUIRED_RELEASE_ENVIRONMENT_CHECKS
                        ],
                    }
                ),
                encoding="utf-8",
            )
            complete, complete_detail = _reviewed_evidence(
                str(path),
                "release-environment",
                evidence_type="release-environment",
                required_checks=REQUIRED_RELEASE_ENVIRONMENT_CHECKS,
            )

            legacy_checks = {
                "release_owner_reviewer": "release_independent_reviewers",
                "release_allow_owner_approval": "release_prevent_self_review",
                "production_owner_reviewer": "production_independent_reviewers",
                "production_allow_owner_approval": "production_prevent_self_review",
            }
            path.write_text(
                json.dumps(
                    {
                        **base,
                        "checks": [
                            {"name": legacy_checks.get(name, name), "status": "pass"}
                            for name in REQUIRED_RELEASE_ENVIRONMENT_CHECKS
                        ],
                    }
                ),
                encoding="utf-8",
            )
            legacy, legacy_detail = _reviewed_evidence(
                str(path),
                "release-environment",
                evidence_type="release-environment",
                required_checks=REQUIRED_RELEASE_ENVIRONMENT_CHECKS,
            )

            path.write_text(
                json.dumps(
                    {
                        **base,
                        "checks": [
                            *[
                                {"name": name, "status": "pass"}
                                for name in REQUIRED_RELEASE_ENVIRONMENT_CHECKS
                            ],
                            {"name": "renamed_or_unreviewed_check", "status": "pass"},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            unknown, unknown_detail = _reviewed_evidence(
                str(path),
                "release-environment",
                evidence_type="release-environment",
                required_checks=REQUIRED_RELEASE_ENVIRONMENT_CHECKS,
            )

        self.assertFalse(incomplete)
        self.assertIn("missing required checks", detail)
        self.assertTrue(complete, complete_detail)
        self.assertFalse(legacy)
        self.assertIn("missing required checks", legacy_detail)
        self.assertFalse(unknown)
        self.assertIn("unknown checks", unknown_detail)

    def test_every_evidence_type_has_an_exact_check_contract(self) -> None:
        from scripts.check_product_readiness import (
            REQUIRED_EVIDENCE_CHECKS,
            REQUIRED_EVIDENCE_FIELDS,
            _reviewed_evidence,
        )

        values = {
            "source": "test source",
            "scope": "test scope",
            "environment": "production",
            "expected_version": "1.0.11",
            "source_revision": "a" * 40,
            "tag": "v1.0.11",
            "target_commit": "a" * 40,
            "assets": ["artifact.whl"],
            "run_id": 1,
            "targets": ["Windows"],
            "target_tier": "test-tier",
            "report_hash": "b" * 64,
            "live_action": True,
        }
        self.assertEqual(set(REQUIRED_EVIDENCE_CHECKS), set(REQUIRED_EVIDENCE_FIELDS))
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "evidence.json"
            for evidence_type, required_checks in REQUIRED_EVIDENCE_CHECKS.items():
                with self.subTest(evidence_type=evidence_type):
                    self.assertTrue(required_checks)
                    self.assertEqual(len(required_checks), len(set(required_checks)))
                    payload = {
                        "verified": True,
                        "schema_version": 1,
                        "evidence_type": evidence_type,
                        "reviewed_by": "test-reviewer",
                        "reviewed_at": reviewed_at_now(),
                        "checks": [{"name": name, "status": "pass"} for name in required_checks],
                        **{field: values[field] for field in REQUIRED_EVIDENCE_FIELDS[evidence_type]},
                    }
                    path.write_text(json.dumps(payload), encoding="utf-8")
                    accepted, detail = _reviewed_evidence(
                        str(path),
                        evidence_type,
                        evidence_type=evidence_type,
                    )
                    self.assertTrue(accepted, detail)

                    payload["checks"] = payload["checks"][:-1]
                    path.write_text(json.dumps(payload), encoding="utf-8")
                    incomplete, incomplete_detail = _reviewed_evidence(
                        str(path),
                        evidence_type,
                        evidence_type=evidence_type,
                    )
                    self.assertFalse(incomplete)
                    self.assertIn("missing required checks", incomplete_detail)

    def test_evidence_scalar_types_are_not_coerced(self) -> None:
        from scripts.check_product_readiness import REQUIRED_REPOSITORY_SETTINGS_CHECKS, _reviewed_evidence

        base = {
            "verified": True,
            "schema_version": 1,
            "evidence_type": "repository-settings",
            "reviewed_by": "test-reviewer",
            "reviewed_at": reviewed_at_now(),
            "source": "test source",
            "checks": [{"name": name, "status": "pass"} for name in REQUIRED_REPOSITORY_SETTINGS_CHECKS],
        }
        invalid_variants = (
            {"schema_version": True},
            {"reviewed_by": "   "},
            {"reviewed_by": "reviewer\rsecond-line"},
            {"source": []},
            {"source": " \t "},
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "repository-settings.json"
            for invalid in invalid_variants:
                with self.subTest(invalid=invalid):
                    path.write_text(json.dumps({**base, **invalid}), encoding="utf-8")
                    accepted, detail = _reviewed_evidence(
                        str(path),
                        "repository settings",
                        evidence_type="repository-settings",
                    )
                    self.assertFalse(accepted, detail)

    def test_boolean_run_id_and_non_string_target_are_rejected(self) -> None:
        from scripts.check_product_readiness import (
            REQUIRED_PLATFORM_CHECKS,
            REQUIRED_PLATFORM_CI_CHECKS,
            _reviewed_evidence,
        )

        base = {
            "verified": True,
            "schema_version": 1,
            "reviewed_by": "test-reviewer",
            "reviewed_at": reviewed_at_now(),
            "source": "test source",
            "scope": "hosted",
            "source_revision": "a" * 40,
        }
        payloads = (
            {
                **base,
                "evidence_type": "platform-ci",
                "run_id": True,
                "checks": [{"name": name, "status": "pass"} for name in REQUIRED_PLATFORM_CI_CHECKS],
            },
            {
                **base,
                "evidence_type": "platform",
                "targets": ["Windows", 11],
                "checks": [{"name": name, "status": "pass"} for name in REQUIRED_PLATFORM_CHECKS],
            },
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "evidence.json"
            for payload in payloads:
                with self.subTest(evidence_type=payload["evidence_type"]):
                    path.write_text(json.dumps(payload), encoding="utf-8")
                    accepted, detail = _reviewed_evidence(
                        str(path),
                        str(payload["evidence_type"]),
                        evidence_type=str(payload["evidence_type"]),
                    )
                    self.assertFalse(accepted, detail)

    def test_legacy_repository_settings_manifest_is_rejected_after_policy_hardening(self) -> None:
        from scripts.check_product_readiness import _reviewed_evidence

        accepted, detail = _reviewed_evidence(
            str(ROOT / "evidence" / "repository-settings.json"),
            "repository settings",
            evidence_type="repository-settings",
            now=datetime(2026, 8, 26, tzinfo=timezone.utc),
        )

        self.assertFalse(accepted, detail)
        self.assertIn("missing required checks", detail)

    def test_checked_in_platform_ci_manifest_is_rejected_for_an_old_revision(self) -> None:
        from scripts.check_product_readiness import _reviewed_evidence

        revision = repository_revision()
        accepted, detail = _reviewed_evidence(
            str(ROOT / "evidence" / "platform-ci.json"),
            "platform CI",
            evidence_type="platform-ci",
            revision_field="source_revision",
            expected_revision=revision,
            now=datetime(2026, 8, 26, tzinfo=timezone.utc),
        )

        self.assertFalse(accepted)
        self.assertIn("must match the current repository revision", detail)

    def test_stale_reviewed_evidence_is_rejected(self) -> None:
        from scripts.check_product_readiness import EVIDENCE_MAX_AGE_DAYS, _reviewed_evidence

        now = datetime.now(timezone.utc)
        revision = repository_revision()
        payload = {
            "verified": True,
            "schema_version": 1,
            "evidence_type": "platform-ci",
            "reviewed_by": "test-reviewer",
            "reviewed_at": (now - timedelta(days=EVIDENCE_MAX_AGE_DAYS + 1)).isoformat(),
            "source": "test",
            "scope": "hosted-ci",
            "run_id": 1,
            "source_revision": revision,
            "checks": [{"name": "check", "status": "pass"}],
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "platform-ci.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            accepted, detail = _reviewed_evidence(
                str(path),
                "platform CI",
                evidence_type="platform-ci",
                required_fields=("scope", "run_id", "source_revision"),
                revision_field="source_revision",
                expected_revision=revision,
                now=now,
            )

        self.assertFalse(accepted)
        self.assertIn("stale", detail)

    def test_revision_bound_evidence_for_another_commit_is_rejected(self) -> None:
        from scripts.check_product_readiness import _reviewed_evidence

        revision = repository_revision()
        other_revision = "0" * 40 if revision != "0" * 40 else "1" * 40
        payload = {
            "verified": True,
            "schema_version": 1,
            "evidence_type": "platform-ci",
            "reviewed_by": "test-reviewer",
            "reviewed_at": reviewed_at_now(),
            "source": "test",
            "scope": "hosted-ci",
            "run_id": 1,
            "source_revision": other_revision,
            "checks": [{"name": "check", "status": "pass"}],
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "platform-ci.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            accepted, detail = _reviewed_evidence(
                str(path),
                "platform CI",
                evidence_type="platform-ci",
                required_fields=("scope", "run_id", "source_revision"),
                revision_field="source_revision",
                expected_revision=revision,
            )

        self.assertFalse(accepted)
        self.assertIn("must match the current repository revision", detail)

    def test_current_revision_platform_evidence_awards_only_platform_points(self) -> None:
        from scripts.check_product_readiness import REQUIRED_PLATFORM_CHECKS, REQUIRED_PLATFORM_CI_CHECKS

        revision = repository_revision()
        manifest = {
            "verified": True,
            "schema_version": 1,
            "reviewed_by": "test-reviewer",
            "reviewed_at": reviewed_at_now(),
            "source": "test",
            "scope": "hosted-ci",
            "source_revision": revision,
        }
        with tempfile.TemporaryDirectory() as temporary:
            platform_ci_path = Path(temporary) / "platform-ci.json"
            platform_path = Path(temporary) / "platform.json"
            platform_ci_path.write_text(
                json.dumps(
                    {
                        **manifest,
                        "evidence_type": "platform-ci",
                        "run_id": 1,
                        "checks": [
                            {"name": name, "status": "pass"} for name in REQUIRED_PLATFORM_CI_CHECKS
                        ],
                    }
                ),
                encoding="utf-8",
            )
            platform_path.write_text(
                json.dumps(
                    {
                        **manifest,
                        "evidence_type": "platform",
                        "targets": ["Windows"],
                        "checks": [{"name": name, "status": "pass"} for name in REQUIRED_PLATFORM_CHECKS],
                    }
                ),
                encoding="utf-8",
            )
            from scripts.check_product_readiness import _parser, build_report

            args = _parser().parse_args(
                [
                    "--no-run-local",
                    "--platform-ci-evidence",
                    str(platform_ci_path),
                    "--platform-evidence",
                    str(platform_path),
                ]
            )
            with (
                patch("scripts.check_product_readiness._repository_is_clean", return_value=True),
                patch("scripts.check_product_readiness._repository_revision", return_value=revision),
            ):
                report = build_report(args)

        platform = next(item for item in report["categories"] if item["name"] == "platform_evidence")
        self.assertEqual(platform["earned"], 5)
        self.assertTrue(any("diagnostic-only" in item for item in platform["missing"]))

    def test_core_local_profile_cannot_award_the_final_readiness_point(self) -> None:
        from scripts.check_product_readiness import _parser, build_report

        core_args = _parser().parse_args([])
        full_args = _parser().parse_args(["--full-local"])
        with (
            patch("scripts.check_product_readiness._run_local_gates", return_value={"status": "pass"}),
            patch("scripts.check_product_readiness._repository_is_clean", return_value=False),
        ):
            core_report = build_report(core_args)
            full_report = build_report(full_args)

        core_tests = next(item for item in core_report["categories"] if item["name"] == "tests_correctness")
        full_tests = next(item for item in full_report["categories"] if item["name"] == "tests_correctness")
        self.assertEqual(core_tests["earned"], 17)
        self.assertEqual(full_tests["earned"], 18)
        self.assertTrue(any("--full-local" in item for item in core_tests["missing"]))

    def test_revision_bound_points_are_revoked_when_head_changes_during_scoring(self) -> None:
        from scripts.check_product_readiness import REQUIRED_PLATFORM_CI_CHECKS, _parser, build_report

        initial_revision = "a" * 40
        final_revision = "b" * 40
        manifest = {
            "verified": True,
            "schema_version": 1,
            "evidence_type": "platform-ci",
            "reviewed_by": "test-reviewer",
            "reviewed_at": reviewed_at_now(),
            "source": "test source",
            "scope": "hosted-ci",
            "run_id": 1,
            "source_revision": initial_revision,
            "checks": [{"name": name, "status": "pass"} for name in REQUIRED_PLATFORM_CI_CHECKS],
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "platform-ci.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            args = _parser().parse_args(["--no-run-local", "--platform-ci-evidence", str(path)])
            with (
                patch("scripts.check_product_readiness._repository_is_clean", side_effect=(True, True)),
                patch(
                    "scripts.check_product_readiness._repository_revision",
                    side_effect=(initial_revision, final_revision),
                ),
            ):
                report = build_report(args)

        platform = next(item for item in report["categories"] if item["name"] == "platform_evidence")
        self.assertEqual(platform["earned"], 5)
        self.assertEqual(report["checks"]["repository"]["status"], "fail")
        self.assertEqual(report["checks"]["repository"]["initial_revision"], initial_revision)
        self.assertEqual(report["checks"]["repository"]["final_revision"], final_revision)
        self.assertTrue(any("diagnostic-only" in item for item in platform["missing"]))

    def test_dirty_worktree_cannot_receive_revision_bound_evidence_points(self) -> None:
        from scripts.check_product_readiness import _parser, build_report

        revision = repository_revision()
        manifest = {
            "verified": True,
            "schema_version": 1,
            "evidence_type": "platform-ci",
            "reviewed_by": "test-reviewer",
            "reviewed_at": reviewed_at_now(),
            "source": "test",
            "scope": "hosted-ci",
            "run_id": 1,
            "source_revision": revision,
            "checks": [{"name": "hosted_matrix", "status": "pass"}],
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "platform-ci.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            args = _parser().parse_args(["--no-run-local", "--platform-ci-evidence", str(path)])
            with patch("scripts.check_product_readiness._repository_is_clean", return_value=False):
                report = build_report(args)

        platform = next(item for item in report["categories"] if item["name"] == "platform_evidence")
        self.assertEqual(platform["earned"], 5)
        self.assertEqual(report["checks"]["repository"]["status"], "fail")
        self.assertTrue(any("revision is unavailable" in item for item in platform["missing"]))

    def test_manual_release_history_manifest_cannot_award_attested_points(self) -> None:
        revision = repository_revision()
        manifest = {
            "verified": True,
            "schema_version": 1,
            "evidence_type": "release-history",
            "reviewed_by": "test-reviewer",
            "reviewed_at": reviewed_at_now(),
            "source": "test",
            "scope": "published-release",
            "tag": "v0.0.0",
            "target_commit": revision,
            "checks": [{"name": "check", "status": "pass"}],
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "release-history.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/check_product_readiness.py",
                    "--no-run-local",
                    "--release-history-evidence",
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
        ci_cd = next(item for item in report["categories"] if item["name"] == "ci_cd_release")
        self.assertEqual(ci_cd["earned"], 14)
        self.assertTrue(any("Attested release evidence" in item for item in ci_cd["missing"]))

    def test_mislabeled_evidence_cannot_award_a_different_tier(self) -> None:
        manifest = {
            "verified": True,
            "schema_version": 1,
            "evidence_type": "release-environment",
            "reviewed_by": "test-reviewer",
            "reviewed_at": reviewed_at_now(),
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


    def test_external_gh_queries_require_an_operator_pinned_digest(self) -> None:
        from scripts.check_product_readiness import (
            _TOOL_TRUST_CONTEXT,
            _ToolTrustContext,
            _run_gh_json,
        )

        token = _TOOL_TRUST_CONTEXT.set(_ToolTrustContext(external_awards_requested=True))
        try:
            with (
                patch("scripts.check_product_readiness.shutil.which") as which,
                patch("scripts.check_product_readiness._run_bounded_process") as run,
            ):
                payload, error = _run_gh_json(["gh", "api", "repos/Yunushan/market-sentinel"])
        finally:
            _TOOL_TRUST_CONTEXT.reset(token)

        self.assertIsNone(payload)
        self.assertIn("ToolTrustError", error)
        which.assert_not_called()
        run.assert_not_called()

    def test_external_evidence_requires_an_operator_pinned_git_digest(self) -> None:
        from scripts.check_product_readiness import (
            _configured_tool_trust,
            _parser,
            _repository_revision,
        )

        args = _parser().parse_args(["--no-run-local", "--public-live-report", "evidence.json"])
        with (
            _configured_tool_trust(args),
            patch("scripts.check_product_readiness.shutil.which") as which,
            patch("scripts.check_product_readiness._run_bounded_process") as run,
        ):
            revision = _repository_revision()

        self.assertEqual(revision, "")
        which.assert_not_called()
        run.assert_not_called()

    def test_repo_local_gh_is_rejected_even_when_its_digest_is_pinned(self) -> None:
        from scripts.check_product_readiness import (
            _TOOL_TRUST_CONTEXT,
            _ToolTrustContext,
            _run_gh_json,
        )

        fake_gh = ROOT / "scripts" / "check_product_readiness.py"
        import hashlib

        pin = hashlib.sha256(fake_gh.read_bytes()).hexdigest()
        token = _TOOL_TRUST_CONTEXT.set(
            _ToolTrustContext(external_awards_requested=True, pins={"gh": pin})
        )
        try:
            with (
                patch("scripts.check_product_readiness.shutil.which", return_value=str(fake_gh)),
                patch("scripts.check_product_readiness._run_bounded_process") as run,
            ):
                payload, error = _run_gh_json(["gh", "api", "repos/Yunushan/market-sentinel"])
        finally:
            _TOOL_TRUST_CONTEXT.reset(token)

        self.assertIsNone(payload)
        self.assertIn("ToolTrustError", error)
        run.assert_not_called()

    @unittest.skipUnless(os.name == "posix", "symlink resolution is verified on POSIX hosts")
    def test_trusted_tool_resolves_system_link_before_trust_checks(self) -> None:
        from scripts.check_product_readiness import _resolve_executable_identity

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "git"
            target.write_bytes(b"#!/bin/sh\nexit 0\n")
            target.chmod(0o755)
            link = root / "path-git"
            link.symlink_to(target)
            with (
                patch("scripts.check_product_readiness.shutil.which", return_value=str(link)),
                patch("scripts.check_product_readiness._unsafe_executable_roots", return_value=()),
                patch("scripts.check_product_readiness._posix_path_is_safely_owned", return_value=True),
            ):
                identity = _resolve_executable_identity("git", require_pin=False)

        self.assertEqual(identity.path, target.resolve())

    def test_trusted_gh_uses_absolute_binary_private_cwd_and_scrubbed_environment(self) -> None:
        from scripts.check_product_readiness import (
            ROOT as SCORER_ROOT,
            _TOOL_TRUST_CONTEXT,
            _ToolTrustContext,
            _resolve_executable_identity,
            _run_gh_json,
        )

        identity = _resolve_executable_identity("git", require_pin=False)
        context = _ToolTrustContext(
            external_awards_requested=True,
            pins={"gh": identity.sha256},
        )
        private_cwd = SCORER_ROOT.parent / "operator-private-tool-cwd"
        completed = subprocess.CompletedProcess([], 0, b"{}", b"")
        token = _TOOL_TRUST_CONTEXT.set(context)
        try:
            with (
                patch.dict(
                    os.environ,
                    {
                        "GH_TOKEN": "test-token",
                        "GH_HOST": "attacker.invalid",
                        "GH_CONFIG_DIR": "attacker-config",
                        "GH_DEBUG": "api",
                        "HTTPS_PROXY": "http://attacker.invalid",
                        "GIT_DIR": "attacker-git-dir",
                    },
                    clear=False,
                ),
                patch("scripts.check_product_readiness._resolve_executable_identity", return_value=identity),
                patch(
                    "scripts.check_product_readiness._private_tool_work_directory",
                    return_value=nullcontext(private_cwd),
                ),
                patch("pathlib.Path.mkdir"),
                patch("scripts.check_product_readiness._run_bounded_process", return_value=completed) as run,
            ):
                payload, error = _run_gh_json(["gh", "api", "repos/Yunushan/market-sentinel"])
        finally:
            _TOOL_TRUST_CONTEXT.reset(token)

        self.assertEqual(payload, {})
        self.assertEqual(error, "")
        command = run.call_args.args[0]
        environment = run.call_args.kwargs["env"]
        self.assertTrue(Path(command[0]).is_absolute())
        self.assertEqual(run.call_args.kwargs["cwd"], private_cwd)
        self.assertNotEqual(private_cwd, SCORER_ROOT)
        self.assertEqual(environment["GH_HOST"], "github.com")
        self.assertEqual(environment["GH_PROMPT_DISABLED"], "1")
        self.assertEqual(environment["GH_TOKEN"], "test-token")
        for name in ("GH_DEBUG", "HTTPS_PROXY", "GIT_DIR", "PATH"):
            self.assertNotIn(name, environment)
        self.assertNotEqual(environment["GH_CONFIG_DIR"], "attacker-config")

    def test_trusted_tool_replacement_after_execution_is_rejected(self) -> None:
        from scripts.check_product_readiness import (
            ROOT as SCORER_ROOT,
            _TOOL_TRUST_CONTEXT,
            _ToolTrustContext,
            _ToolTrustError,
            _resolve_executable_identity,
            _run_gh_json,
        )

        identity = _resolve_executable_identity("git", require_pin=False)
        token = _TOOL_TRUST_CONTEXT.set(
            _ToolTrustContext(external_awards_requested=True, pins={"gh": identity.sha256})
        )
        try:
            with (
                patch("scripts.check_product_readiness._resolve_executable_identity", return_value=identity),
                patch(
                    "scripts.check_product_readiness._private_tool_work_directory",
                    return_value=nullcontext(SCORER_ROOT.parent / "operator-private-tool-cwd"),
                ),
                patch("pathlib.Path.mkdir"),
                patch(
                    "scripts.check_product_readiness._run_bounded_process",
                    return_value=subprocess.CompletedProcess([], 0, b"{}", b""),
                ),
                patch(
                    "scripts.check_product_readiness._recheck_executable_identity",
                    side_effect=(None, _ToolTrustError("changed")),
                ),
            ):
                payload, error = _run_gh_json(["gh", "api", "repos/Yunushan/market-sentinel"])
        finally:
            _TOOL_TRUST_CONTEXT.reset(token)

        self.assertIsNone(payload)
        self.assertIn("ToolTrustError", error)

    def test_external_process_stdout_is_strictly_bounded(self) -> None:
        from scripts.check_product_readiness import _ToolOutputLimitError, _run_bounded_process

        with self.assertRaises(_ToolOutputLimitError):
            _run_bounded_process(
                [sys.executable, "-c", "import sys; sys.stdout.write('x' * 4096)"],
                cwd=ROOT,
                env=os.environ.copy(),
                timeout=10,
                maximum_stdout_bytes=128,
                maximum_stderr_bytes=128,
            )

    def test_repository_cleanliness_rejects_sparse_unmerged_and_non_normal_index_flags(self) -> None:
        from scripts.check_product_readiness import _repository_is_clean

        root_output = (str(ROOT.resolve()) + "\n").encode()

        def completed(stdout: bytes = b"", returncode: int = 0) -> subprocess.CompletedProcess[bytes]:
            return subprocess.CompletedProcess([], returncode, stdout, b"")

        cases = {
            "skip-worktree": (completed(b"", 1), completed(), completed(b"S verify.py\x00")),
            "assume-unchanged": (completed(b"", 1), completed(), completed(b"h verify.py\x00")),
            "sparse-checkout": (completed(b"true\n"), completed(), completed(b"H verify.py\x00")),
            "unmerged": (completed(b"", 1), completed(b"100644 1 a\tverify.py\x00"), completed()),
        }
        for label, (sparse, unmerged, index) in cases.items():
            with self.subTest(label=label):
                results = (completed(root_output), completed(), sparse, unmerged, index)
                with patch("scripts.check_product_readiness._run_trusted_git", side_effect=results):
                    self.assertFalse(_repository_is_clean())

    def test_safe_report_derives_nested_release_evidence_status(self) -> None:
        from scripts.check_product_readiness import _safe_report_for_output

        for history, release, expected in (
            ("pass", "pass", "pass"),
            ("pass", "fail", "fail"),
            ("not_run", "not_run", "not_run"),
        ):
            with self.subTest(history=history, release=release):
                report = {
                    "categories": [],
                    "checks": {
                        "release_evidence": {
                            "history": {"status": history},
                            "release": {"status": release},
                        }
                    },
                }
                safe = _safe_report_for_output(report)
                self.assertEqual(safe["checks"]["release_evidence"]["status"], expected)


    def test_repository_git_probe_scrubs_overrides_and_disables_execution_hooks(self) -> None:
        from scripts.check_product_readiness import ROOT as SCORER_ROOT, _repository_revision

        completed = (
            subprocess.CompletedProcess([], 0, stdout=(str(SCORER_ROOT.resolve()) + "\n").encode(), stderr=b""),
            subprocess.CompletedProcess([], 0, stdout=("a" * 40 + "\n").encode(), stderr=b""),
        )
        with (
            patch.dict(
                "os.environ",
                {"GIT_DIR": "attacker", "GIT_WORK_TREE": "attacker", "GIT_ASKPASS": "attacker"},
                clear=False,
            ),
            patch("scripts.check_product_readiness._run_bounded_process", side_effect=completed) as run,
        ):
            self.assertEqual(_repository_revision(), "a" * 40)

        for call in run.call_args_list:
            environment = call.kwargs["env"]
            self.assertNotIn("GIT_DIR", environment)
            self.assertNotIn("GIT_WORK_TREE", environment)
            self.assertNotIn("GIT_ASKPASS", environment)
            command = call.args[0]
            self.assertTrue(Path(command[0]).is_absolute())
            self.assertIn("core.fsmonitor=false", command)
            self.assertIn("core.hooksPath=", command)

    def test_repository_git_probe_rejects_a_different_top_level(self) -> None:
        from scripts.check_product_readiness import _repository_revision

        forged = subprocess.CompletedProcess([], 0, stdout=(str(ROOT.parent) + "\n").encode(), stderr=b"")
        with patch("scripts.check_product_readiness._run_bounded_process", return_value=forged):
            self.assertEqual(_repository_revision(), "")


if __name__ == "__main__":
    unittest.main()
