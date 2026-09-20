from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from polymarket.funded_policy import (
    FUNDED_TOKEN_ALLOWLIST_VARIABLE,
    funded_token_allowlist_json,
    funded_token_allowlist_sha256,
)

from scripts.trusted_readiness_evidence import (
    CREDENTIALED_POLYMARKET_CHECKS,
    FUNDED_POLYMARKET_CHECKS,
    MAX_FUNDED_ENVIRONMENT_BYTES,
    PLATFORM_CHECKS,
    PLATFORM_CI_CHECKS,
    RELEASE_ENVIRONMENT_CHECKS,
    REPOSITORY,
    REPOSITORY_SETTINGS_CHECKS,
    TRUSTED_REF,
    WORKFLOW_CONTRACTS,
    TrustedEvidenceError,
    _required_job_labels,
    _required_job_names,
    _platform_job_contracts,
    build_governance_manifests,
    build_live_manifest,
    build_platform_manifests,
    build_platform_job_receipt,
    canonical_json_bytes,
    main as trusted_evidence_main,
    strict_json_bytes,
    validate_manifest,
    write_manifest,
)
from scripts.verify_repository_settings import (
    GITHUB_ACTIONS_APP_ID,
    REQUIRED_CHECKS as GOVERNANCE_REQUIRED_CHECKS,
    REQUIRED_PRODUCTION_SECRETS,
    REQUIRED_PRODUCTION_VARIABLES,
    REQUIRED_RELEASE_SECRETS,
    REQUIRED_RELEASE_TAG_POLICY,
    REQUIRED_REPOSITORY_SETTINGS_CHECKS as COLLECTOR_REPOSITORY_CHECKS,
    collect_governance_evidence,
    governance_state_sha256,
)


ROOT = Path(__file__).resolve().parent.parent
REVISION = "a" * 40
SOURCE_RUN_ID = 600
EVIDENCE_RUN_ID = 700
RUN_ATTEMPT = 1
ORDER_ID = "0x" + "1" * 64
RECOVERY_SHA256 = "b" * 64
COLLECTOR_STARTED_AT = "2026-09-17T10:00:00Z"
COLLECTOR_COMPLETED_AT = "2026-09-17T10:01:00Z"


def _governance_environment(*, release: bool) -> dict[str, object]:
    return {
        "protection_rules": [
            {
                "type": "required_reviewers",
                "prevent_self_review": True,
                "reviewers": [
                    {"type": "User", "reviewer": {"id": 1}},
                    {"type": "User", "reviewer": {"id": 2}},
                ],
            }
        ],
        "deployment_branch_policy": (
            {"protected_branches": False, "custom_branch_policies": True}
            if release
            else {"protected_branches": True, "custom_branch_policies": False}
        ),
    }


def governance_api_documents() -> dict[str, dict[str, object]]:
    prefix = f"/repos/{REPOSITORY}"
    return {
        f"{prefix}/branches/main/protection": {
            "required_status_checks": {
                "strict": True,
                "contexts": sorted(GOVERNANCE_REQUIRED_CHECKS),
                "checks": [
                    {"context": name, "app_id": GITHUB_ACTIONS_APP_ID}
                    for name in sorted(GOVERNANCE_REQUIRED_CHECKS)
                ],
            },
            "enforce_admins": {"enabled": True},
            "required_pull_request_reviews": {
                "required_approving_review_count": 1,
                "dismiss_stale_reviews": True,
                "require_code_owner_reviews": True,
                "require_last_push_approval": True,
            },
            "required_conversation_resolution": {"enabled": True},
            "required_linear_history": {"enabled": True},
            "allow_force_pushes": {"enabled": False},
            "allow_deletions": {"enabled": False},
        },
        f"{prefix}/branches/main/protection/required_signatures": {"enabled": True},
        f"{prefix}/environments/release": _governance_environment(release=True),
        f"{prefix}/environments/release/deployment-branch-policies?per_page=100": {
            "total_count": 2,
            "branch_policies": [
                {"type": "branch", "name": "main"},
                {"type": "tag", "name": REQUIRED_RELEASE_TAG_POLICY},
            ]
        },
        f"{prefix}/environments/release/secrets?per_page=100": {
            "total_count": len(REQUIRED_RELEASE_SECRETS),
            "secrets": [{"name": name} for name in sorted(REQUIRED_RELEASE_SECRETS)]
        },
        f"{prefix}/environments/production": _governance_environment(release=False),
        f"{prefix}/environments/production/secrets?per_page=100": {
            "total_count": len(REQUIRED_PRODUCTION_SECRETS),
            "secrets": [{"name": name} for name in sorted(REQUIRED_PRODUCTION_SECRETS)]
        },
        f"{prefix}/environments/production/variables?per_page=100": {
            "total_count": len(REQUIRED_PRODUCTION_VARIABLES),
            "variables": [{"name": name} for name in sorted(REQUIRED_PRODUCTION_VARIABLES)]
        },
        f"{prefix}/actions/variables/REQUIRE_WINDOWS_CODE_SIGNING": {"value": "true"},
    }


def governance_payload() -> dict[str, object]:
    documents = governance_api_documents()
    checks, governance_state, state_sha256 = collect_governance_evidence(
        REPOSITORY,
        "main",
        "",
        30.0,
        lambda path, _token, _timeout: documents[path],
    )
    assert all(check["status"] == "pass" for check in checks)
    return {
        "schema_version": 2,
        "repository": REPOSITORY,
        "branch": "main",
        "status": "ok",
        "checks": checks,
        "governance_state": governance_state,
        "governance_state_sha256": state_sha256,
    }


def platform_api_payloads(
    *, now: datetime | None = None
) -> tuple[dict[str, object], dict[str, object], dict[str, object], list[dict[str, object]]]:
    current = now or datetime.now(timezone.utc)
    run = {
        "id": SOURCE_RUN_ID,
        "head_sha": REVISION,
        "name": "CI",
        "path": ".github/workflows/ci.yml",
        "event": "push",
        "status": "completed",
        "conclusion": "success",
        "run_attempt": 1,
        "head_branch": "main",
        "head_repository": {"full_name": REPOSITORY},
        "created_at": (current - timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
        "run_started_at": (current - timedelta(minutes=59)).isoformat().replace("+00:00", "Z"),
        "updated_at": (current - timedelta(minutes=30)).isoformat().replace("+00:00", "Z"),
    }
    names = tuple(dict.fromkeys(name for group in _required_job_names().values() for name in group))
    expected_labels = _required_job_labels()
    contracts = _platform_job_contracts()
    receipt_time = current - timedelta(minutes=31)
    receipts = [
        build_platform_job_receipt(
            repository=REPOSITORY,
            source_revision=REVISION,
            run_id=SOURCE_RUN_ID,
            run_attempt=RUN_ATTEMPT,
            workflow_ref=f"{REPOSITORY}/.github/workflows/ci.yml@{TRUSTED_REF}",
            event="push",
            job_key=str(contracts[name]["job_key"]),
            matrix=contracts[name]["matrix"],
            runner_environment="github-hosted",
            runner_os=str(contracts[name]["runner_os"]),
            runner_arch="X64",
            generated_at=receipt_time,
        )
        for name in names
    ]
    jobs = {
        "total_count": len(names),
        "jobs": [
            {
                "id": 1_000 + index,
                "run_id": SOURCE_RUN_ID,
                "run_attempt": RUN_ATTEMPT,
                "head_sha": REVISION,
                "name": name,
                "status": "completed",
                "conclusion": "success",
                "labels": list(expected_labels[name]),
                "started_at": (current - timedelta(minutes=58)).isoformat().replace("+00:00", "Z"),
                "completed_at": (current - timedelta(minutes=30)).isoformat().replace("+00:00", "Z"),
                "steps": [
                    {
                        "name": "Publish cryptographically attested platform receipt",
                        "status": "completed",
                        "conclusion": "success",
                    }
                ],
            }
            for index, name in enumerate(names)
        ],
    }
    artifacts = {
        "total_count": len(receipts),
        "artifacts": [
            {
                "id": 2_000 + index,
                "name": receipt["artifact_name"],
                "size_in_bytes": len(canonical_json_bytes(receipt)),
                "expired": False,
                "created_at": (current - timedelta(minutes=29, seconds=50)).isoformat().replace(
                    "+00:00", "Z"
                ),
                "updated_at": (current - timedelta(minutes=29, seconds=40)).isoformat().replace(
                    "+00:00", "Z"
                ),
                "workflow_run": {"id": SOURCE_RUN_ID, "head_sha": REVISION},
            }
            for index, receipt in enumerate(receipts)
        ],
    }
    return run, jobs, artifacts, receipts


def clean_source_provenance(revision: str = REVISION) -> dict[str, object]:
    return {
        "schema_version": 1,
        "repository": "market-sentinel",
        "repository_origin": "github.com/yunushan/market-sentinel",
        "source_revision": revision,
        "initial_revision": revision,
        "final_revision": revision,
        "initial_clean": True,
        "final_clean": True,
        "stable": True,
    }


def public_checks() -> dict[str, dict[str, object]]:
    return {
        "clob_time": {"status": "ok", "semantic_check": "current_unix_time"},
        "gamma_markets": {"status": "ok", "semantic_check": "market_identity"},
        "data_leaderboard": {"status": "ok", "semantic_check": "leaderboard_identity"},
        "bridge_supported_assets": {"status": "ok", "semantic_check": "supported_asset_identity"},
    }


def authenticated_reads() -> dict[str, dict[str, object]]:
    return {
        "clob_l2_orders": {
            "status": "ok",
            "detail": "Authenticated CLOB order list responded.",
            "sample_type": "list",
            "semantic_check": "authenticated_order_collection",
            "records_observed": 0,
        }
    }


def credentialed_report() -> dict[str, object]:
    return {
        "ok": True,
        "generated_at": 1.0,
        "mode": "strict_cli",
        "market_id": "polymarket",
        "source_provenance": clean_source_provenance(),
        "public_checks": public_checks(),
        "authenticated_read_checks": authenticated_reads(),
        "funded_live_order_check": {"status": "blocked", "live_action": False},
        "stage_gates": {
            "credentialed_read_ok": True,
            "safe_to_attempt_funded_order": False,
            "requires_explicit_live_approval": True,
        },
    }


def funded_report() -> dict[str, object]:
    report = credentialed_report()
    report["funded_live_order_check"] = {
        "status": "ok",
        "live_action": True,
        "manual_reconciliation_required": False,
        "token_id": "123",
        "side": "BUY",
        "price": 0.01,
        "size": 1.0,
        "tif": "GTC",
        "funded_token_policy_receipt": {
            "schema_version": 1,
            "variable_name": FUNDED_TOKEN_ALLOWLIST_VARIABLE,
            "token_ids": ["123"],
            "allowlist_sha256": funded_token_allowlist_sha256(("123",)),
            "selected_token_id": "123",
        },
        "account_authenticated_read_preflight": {
            "status": "pass",
            "same_trading_client": True,
            "account_identity_present": True,
            "sample_type": "list",
            "records_observed": 0,
        },
        "account_preflight": {
            "status": "pass",
            "sufficient_balance": True,
            "sufficient_allowance": True,
        },
        "execution_guards": {
            "status": "pass",
            "post_only": True,
            "time_in_force": "GTC",
            "maker_price_verified": True,
        },
        "geoblock_preflight": {"status": "pass", "blocked": False},
        "source_revision_gate": {
            "status": "pass",
            "clean": True,
            "matches_initial_revision": True,
            "source_revision": REVISION,
            "repository_origin": "github.com/yunushan/market-sentinel",
        },
        "audit": {
            "order_id": ORDER_ID,
            "placed": {"orderID": ORDER_ID, "status": "live"},
            "cancel": {"canceled": [ORDER_ID]},
            "post_cancel_order": {
                "id": ORDER_ID,
                "status": "ORDER_STATUS_CANCELED",
                "size_matched": "0",
                "associate_trades": [],
            },
            "zero_fill_evidence": {
                "verified": True,
                "order_identity_matches": True,
                "size_matched_zero": True,
                "associated_trades_empty": True,
            },
            "recovery_journal": {"status": "resolved", "stage": "cancel_verified", "resolved": True},
            "post_cancel_verified": True,
        },
        "recovery_journal_receipt": {
            "schema_version": 1,
            "sha256": RECOVERY_SHA256,
            "source_revision": REVISION,
            "workflow_run_id": EVIDENCE_RUN_ID,
            "workflow_run_attempt": RUN_ATTEMPT,
            "evidence_nonce": f"{REVISION}:{EVIDENCE_RUN_ID}:{RUN_ATTEMPT}",
            "order_id": ORDER_ID,
            "stage": "cancel_verified",
            "resolved": True,
        },
    }
    report["stage_gates"] = {
        "credentialed_read_ok": True,
        "safe_to_attempt_funded_order": True,
        "requires_explicit_live_approval": True,
        "funded_live_order_check": "ok",
    }
    return report


def funded_recovery_journal() -> dict[str, object]:
    return {
        "schema_version": 2,
        "market_id": "polymarket",
        "token_id": "123",
        "side": "BUY",
        "price": 0.01,
        "size": 1.0,
        "tif": "GTC",
        "post_only": True,
        "account_address": "0x" + "2" * 40,
        "stage": "cancel_verified",
        "order_id": ORDER_ID,
        "manual_reconciliation_required": False,
        "resolved": True,
        "zero_fill_verified": True,
        "run_id": "8fa2660a-4a06-4b3a-9f25-6534b07351b5",
        "run_started_at": "2026-09-17T10:00:00Z",
        "source_revision": REVISION,
        "sequence": 3,
        "updated_at": "2026-09-17T10:00:01Z",
        "workflow_run_id": EVIDENCE_RUN_ID,
        "workflow_run_attempt": RUN_ATTEMPT,
        "evidence_nonce": f"{REVISION}:{EVIDENCE_RUN_ID}:{RUN_ATTEMPT}",
    }


def funded_run_jobs() -> dict[str, object]:
    step_names = (
        "Verify funded execution approval",
        "Prepare persistent funded recovery directory",
        "Verify exact clean source before funded action",
        "Run bounded funded order and immediate cancel",
        "Reverify exact clean source after funded action",
        "Collect resolved recovery journal",
        "Upload raw funded outcome and resolved journal",
    )
    return {
        "total_count": 1,
        "jobs": [
            {
                "id": 900,
                "run_id": EVIDENCE_RUN_ID,
                "run_attempt": RUN_ATTEMPT,
                "head_sha": REVISION,
                "name": "Collect bounded funded Polymarket outcome",
                "status": "completed",
                "conclusion": "success",
                "started_at": COLLECTOR_STARTED_AT,
                "completed_at": COLLECTOR_COMPLETED_AT,
                "labels": ["self-hosted", "linux", "x64", "market-sentinel-production"],
                "steps": [
                    {"name": name, "status": "completed", "conclusion": "success"}
                    for name in step_names
                ],
            }
        ],
    }


def funded_environment_config() -> dict[str, object]:
    return {
        "id": 321,
        "node_id": "ENV_production",
        "name": "production",
        "url": f"https://api.github.com/repos/{REPOSITORY}/environments/production",
        "html_url": f"https://github.com/{REPOSITORY}/deployments/activity_log?environments_filter=production",
        "created_at": "2026-09-17T08:00:00Z",
        "updated_at": "2026-09-17T09:00:00Z",
        "protection_rules": [
            {"id": 11, "node_id": "RULE_reviewers", "type": "required_reviewers", "prevent_self_review": True, "reviewers": [
                {"type": "Team", "reviewer": {"id": 41, "slug": "production-approvers"}}
            ]},
            {"id": 12, "node_id": "RULE_branch", "type": "branch_policy"},
        ],
        "deployment_branch_policy": {
            "protected_branches": True,
            "custom_branch_policies": False,
        },
    }


def funded_run_approvals() -> list[dict[str, object]]:
    environment = funded_environment_config()
    approval_environment = {
        field: environment[field]
        for field in (
            "id",
            "node_id",
            "name",
            "url",
            "html_url",
            "created_at",
            "updated_at",
        )
    }
    login = "production-reviewer"
    api_user = f"https://api.github.com/users/{login}"
    return [
        {
            "state": "approved",
            "comment": "Approved bounded production audit.",
            "environments": [approval_environment],
            "user": {
                "login": login,
                "id": 42,
                "node_id": "USER_production_reviewer",
                "avatar_url": "https://avatars.githubusercontent.com/u/42?v=4",
                "gravatar_id": "",
                "url": api_user,
                "html_url": f"https://github.com/{login}",
                "followers_url": f"{api_user}/followers",
                "following_url": f"{api_user}/following{{/other_user}}",
                "gists_url": f"{api_user}/gists{{/gist_id}}",
                "starred_url": f"{api_user}/starred{{/owner}}{{/repo}}",
                "subscriptions_url": f"{api_user}/subscriptions",
                "organizations_url": f"{api_user}/orgs",
                "repos_url": f"{api_user}/repos",
                "events_url": f"{api_user}/events{{/privacy}}",
                "received_events_url": f"{api_user}/received_events",
                "type": "User",
                "site_admin": False,
            },
        }
    ]


def funded_token_policy(*token_ids: str, updated_at: str = "2026-09-17T09:05:00Z") -> dict[str, object]:
    selected = token_ids or ("123",)
    return {
        "name": FUNDED_TOKEN_ALLOWLIST_VARIABLE,
        "value": funded_token_allowlist_json(selected),
        "created_at": "2026-09-17T08:30:00Z",
        "updated_at": updated_at,
    }


def workflow_ref(evidence_type: str) -> str:
    return f"{REPOSITORY}/{WORKFLOW_CONTRACTS[evidence_type]['workflow']}@{TRUSTED_REF}"


def all_manifests(now: datetime) -> tuple[dict[str, dict[str, object]], dict[str, object], dict[str, object]]:
    generated = now - timedelta(minutes=2)
    governance = build_governance_manifests(
        governance_payload(),
        repository=REPOSITORY,
        source_revision=REVISION,
        run_id=EVIDENCE_RUN_ID,
        run_attempt=RUN_ATTEMPT,
        workflow_ref=workflow_ref("repository-settings"),
        generated_at=generated,
    )
    source_run, source_jobs, source_artifacts, source_receipts = platform_api_payloads(now=now)
    platform = build_platform_manifests(
        source_run,
        source_jobs,
        source_artifacts,
        source_receipts,
        repository=REPOSITORY,
        source_revision=REVISION,
        source_run_id=SOURCE_RUN_ID,
        run_id=EVIDENCE_RUN_ID,
        run_attempt=RUN_ATTEMPT,
        workflow_ref=workflow_ref("platform-ci"),
        generated_at=generated,
    )
    credentialed = build_live_manifest(
        credentialed_report(),
        tier="credentialed",
        repository=REPOSITORY,
        source_revision=REVISION,
        run_id=EVIDENCE_RUN_ID,
        run_attempt=RUN_ATTEMPT,
        workflow_ref=workflow_ref("credentialed-polymarket"),
        generated_at=generated,
    )
    funded = build_live_manifest(
        funded_report(),
        tier="funded",
        repository=REPOSITORY,
        source_revision=REVISION,
        run_id=EVIDENCE_RUN_ID,
        run_attempt=RUN_ATTEMPT,
        workflow_ref=workflow_ref("funded-polymarket"),
        recovery_journal=funded_recovery_journal(),
        recovery_journal_sha256=RECOVERY_SHA256,
        run_jobs=funded_run_jobs(),
        environment_config=funded_environment_config(),
        run_approvals=funded_run_approvals(),
        token_policy=funded_token_policy(),
        generated_at=generated,
    )
    return {**governance, **platform}, credentialed, funded


def hosted_api_side_effect(
    evidence_type: str,
    manifest: dict[str, object],
    *,
    now: datetime,
):
    contract = WORKFLOW_CONTRACTS[evidence_type]
    source_run, source_jobs, source_artifacts, _source_receipts = platform_api_payloads(now=now)
    governance_documents = governance_api_documents()

    def query(command: list[str], *, timeout: int = 30):
        del timeout
        if command[:3] == ["gh", "attestation", "verify"]:
            return [
                {
                    "attestation": {"bundle": "checked by separately tested matcher"},
                    "verificationResult": {
                        "verifiedTimestamps": [
                            {
                                "timestamp": (now - timedelta(minutes=30, seconds=30))
                                .isoformat()
                                .replace("+00:00", "Z")
                            }
                        ]
                    },
                }
            ], ""
        endpoint = command[-1]
        if endpoint.endswith(f"/actions/runs/{EVIDENCE_RUN_ID}"):
            return {
                "id": EVIDENCE_RUN_ID,
                "head_sha": REVISION,
                "name": contract["workflow_name"],
                "path": contract["workflow"],
                "event": "workflow_dispatch",
                "status": "completed",
                "conclusion": "success",
                "run_attempt": RUN_ATTEMPT,
                "head_branch": "main",
                "head_repository": {"full_name": REPOSITORY},
                "created_at": (now - timedelta(minutes=6)).isoformat().replace("+00:00", "Z"),
                "run_started_at": (now - timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
                "updated_at": (now - timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
            }, ""
        if endpoint.endswith(f"/actions/runs/{EVIDENCE_RUN_ID}/jobs?filter=latest&per_page=100"):
            return {
                "total_count": 1,
                "jobs": [
                    {
                        "name": contract["job"],
                        "status": "completed",
                        "conclusion": "success",
                        "labels": ["ubuntu-24.04"],
                        "started_at": (now - timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
                        "completed_at": (now - timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
                        "steps": [
                            {"name": name, "status": "completed", "conclusion": "success"}
                            for name in contract["required_steps"]
                        ],
                    }
                ],
            }, ""
        if endpoint.endswith(f"/actions/runs/{EVIDENCE_RUN_ID}/artifacts?per_page=100"):
            evidence = manifest["evidence"]
            assert isinstance(evidence, dict)
            return {
                "total_count": 1,
                "artifacts": [
                    {
                        "id": 800,
                        "name": evidence["artifact_name"],
                        "size_in_bytes": len(canonical_json_bytes(manifest)),
                        "expired": False,
                        "created_at": (now - timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
                        "updated_at": (now - timedelta(seconds=30)).isoformat().replace("+00:00", "Z"),
                        "workflow_run": {"id": EVIDENCE_RUN_ID, "head_sha": REVISION},
                    }
                ],
            }, ""
        if endpoint.endswith(f"/actions/runs/{SOURCE_RUN_ID}"):
            return source_run, ""
        if endpoint.endswith(f"/actions/runs/{SOURCE_RUN_ID}/jobs?filter=latest&per_page=100"):
            return source_jobs, ""
        if endpoint.endswith(f"/actions/runs/{SOURCE_RUN_ID}/artifacts?per_page=100"):
            return source_artifacts, ""
        governance_endpoint = "/" + endpoint
        if governance_endpoint in governance_documents:
            return deepcopy(governance_documents[governance_endpoint]), ""
        raise AssertionError(f"unexpected GitHub query: {command}")

    return query


class TrustedReadinessEvidenceTests(unittest.TestCase):
    def test_strict_api_json_rejects_duplicate_keys_and_oversized_inputs(self) -> None:
        with self.assertRaises(ValueError):
            strict_json_bytes(b'{"name":"production","name":"staging"}')
        with self.assertRaises(ValueError):
            strict_json_bytes(
                b" " * (MAX_FUNDED_ENVIRONMENT_BYTES + 1),
                maximum_bytes=MAX_FUNDED_ENVIRONMENT_BYTES,
            )

    def test_generator_scorer_and_live_collectors_share_exact_check_contracts(self) -> None:
        from scripts.check_product_readiness import (
            REQUIRED_CREDENTIALED_POLYMARKET_CHECKS,
            REQUIRED_FUNDED_POLYMARKET_CHECKS,
            REQUIRED_PLATFORM_CHECKS,
            REQUIRED_PLATFORM_CI_CHECKS,
            REQUIRED_RELEASE_ENVIRONMENT_CHECKS,
            REQUIRED_REPOSITORY_SETTINGS_CHECKS,
        )
        from scripts.verify_repository_settings import (
            REQUIRED_RELEASE_ENVIRONMENT_CHECKS as COLLECTOR_RELEASE_CHECKS,
        )

        self.assertEqual(REQUIRED_REPOSITORY_SETTINGS_CHECKS, REPOSITORY_SETTINGS_CHECKS)
        self.assertEqual(COLLECTOR_REPOSITORY_CHECKS, REPOSITORY_SETTINGS_CHECKS)
        self.assertEqual(REQUIRED_RELEASE_ENVIRONMENT_CHECKS, RELEASE_ENVIRONMENT_CHECKS)
        self.assertEqual(COLLECTOR_RELEASE_CHECKS, RELEASE_ENVIRONMENT_CHECKS)
        self.assertEqual(REQUIRED_PLATFORM_CI_CHECKS, PLATFORM_CI_CHECKS)
        self.assertEqual(tuple(_required_job_names()), PLATFORM_CI_CHECKS)
        self.assertEqual(REQUIRED_PLATFORM_CHECKS, PLATFORM_CHECKS)
        self.assertEqual(REQUIRED_CREDENTIALED_POLYMARKET_CHECKS, CREDENTIALED_POLYMARKET_CHECKS)
        self.assertEqual(REQUIRED_FUNDED_POLYMARKET_CHECKS, FUNDED_POLYMARKET_CHECKS)

    def test_governance_builder_requires_every_live_control(self) -> None:
        now = datetime.now(timezone.utc)
        manifests = build_governance_manifests(
            governance_payload(),
            repository=REPOSITORY,
            source_revision=REVISION,
            run_id=EVIDENCE_RUN_ID,
            run_attempt=RUN_ATTEMPT,
            workflow_ref=workflow_ref("repository-settings"),
            generated_at=now,
        )
        self.assertEqual(set(manifests), {"repository-settings", "release-environment"})
        expected_digest = governance_payload()["governance_state_sha256"]
        for evidence_type, manifest in manifests.items():
            self.assertEqual(manifest["governance_state_sha256"], expected_digest)
            validation = validate_manifest(
                manifest,
                expected_evidence_type=evidence_type,
                expected_revision=REVISION,
                now=now,
            )
            self.assertTrue(validation["ok"], validation["errors"])

        incomplete = governance_payload()
        incomplete["checks"] = list(incomplete["checks"])[:-1]  # type: ignore[arg-type]
        with self.assertRaisesRegex(TrustedEvidenceError, "missing required checks"):
            build_governance_manifests(
                incomplete,
                repository=REPOSITORY,
                source_revision=REVISION,
                run_id=EVIDENCE_RUN_ID,
                run_attempt=RUN_ATTEMPT,
                workflow_ref=workflow_ref("repository-settings"),
            )

        tampered_state = governance_payload()
        tampered_state["governance_state"] = deepcopy(tampered_state["governance_state"])
        tampered_state["governance_state"]["branch_protection"]["strict"] = False  # type: ignore[index]
        with self.assertRaisesRegex(TrustedEvidenceError, "state snapshot or digest"):
            build_governance_manifests(
                tampered_state,
                repository=REPOSITORY,
                source_revision=REVISION,
                run_id=EVIDENCE_RUN_ID,
                run_attempt=RUN_ATTEMPT,
                workflow_ref=workflow_ref("repository-settings"),
            )

    def test_governance_cli_generates_canonical_files_that_validate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            raw = directory / "raw.json"
            output = directory / "out"
            output.mkdir()
            raw.write_text(json.dumps(governance_payload()), encoding="utf-8")
            with redirect_stdout(io.StringIO()):
                generated = trusted_evidence_main(
                    [
                        "governance",
                        "--input",
                        str(raw),
                        "--repository",
                        REPOSITORY,
                        "--source-revision",
                        REVISION,
                        "--run-id",
                        str(EVIDENCE_RUN_ID),
                        "--run-attempt",
                        str(RUN_ATTEMPT),
                        "--workflow-ref",
                        workflow_ref("repository-settings"),
                        "--output-directory",
                        str(output),
                    ]
                )
            self.assertEqual(generated, 0)
            for evidence_type in ("repository-settings", "release-environment"):
                path = output / str(WORKFLOW_CONTRACTS[evidence_type]["subject_name"])
                payload = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(path.read_bytes(), canonical_json_bytes(payload))
                with redirect_stdout(io.StringIO()):
                    validated = trusted_evidence_main(
                        [
                            "validate",
                            "--input",
                            str(path),
                            "--evidence-type",
                            evidence_type,
                            "--expected-revision",
                            REVISION,
                        ]
                    )
                self.assertEqual(validated, 0)

            with redirect_stdout(io.StringIO()):
                compared = trusted_evidence_main(
                    [
                        "compare-governance",
                        "--input",
                        str(raw),
                        "--manifest",
                        str(output / "repository-settings-evidence.json"),
                        "--manifest",
                        str(output / "release-environment-evidence.json"),
                    ]
                )
            self.assertEqual(compared, 0)

            changed = governance_payload()
            changed_state = changed["governance_state"]
            assert isinstance(changed_state, dict)
            changed_state["branch_protection"] = {"strict": False}
            changed["governance_state_sha256"] = governance_state_sha256(changed_state)
            raw.write_text(json.dumps(changed), encoding="utf-8")
            with redirect_stdout(io.StringIO()):
                rejected = trusted_evidence_main(
                    [
                        "compare-governance",
                        "--input",
                        str(raw),
                        "--manifest",
                        str(output / "repository-settings-evidence.json"),
                        "--manifest",
                        str(output / "release-environment-evidence.json"),
                    ]
                )
            self.assertEqual(rejected, 1)

    def test_platform_builder_requires_fresh_unique_attempt_bound_receipts(self) -> None:
        now = datetime.now(timezone.utc)
        source_run, source_jobs, source_artifacts, source_receipts = platform_api_payloads(now=now)
        manifests = build_platform_manifests(
            source_run,
            source_jobs,
            source_artifacts,
            source_receipts,
            repository=REPOSITORY,
            source_revision=REVISION,
            source_run_id=SOURCE_RUN_ID,
            run_id=EVIDENCE_RUN_ID,
            run_attempt=RUN_ATTEMPT,
            workflow_ref=workflow_ref("platform-ci"),
            generated_at=now,
        )
        self.assertEqual(set(manifests), {"platform-ci", "platform"})
        for evidence_type, manifest in manifests.items():
            self.assertTrue(
                validate_manifest(
                    manifest,
                    expected_evidence_type=evidence_type,
                    expected_revision=REVISION,
                    now=now,
                )["ok"]
            )

        for mutation in ("missing", "self-hosted", "wrong-hosted-runner"):
            with self.subTest(mutation=mutation):
                rejected_jobs = deepcopy(source_jobs)
                jobs = rejected_jobs["jobs"]
                assert isinstance(jobs, list)
                if mutation == "missing":
                    jobs.pop()
                    rejected_jobs["total_count"] = len(jobs)
                elif mutation == "self-hosted":
                    assert isinstance(jobs[0], dict)
                    jobs[0]["labels"] = ["self-hosted"]
                else:
                    arm = next(
                        job
                        for job in jobs
                        if isinstance(job, dict)
                        and job.get("name") == "Windows 11 ARM runner / Python 3.12 x64"
                    )
                    arm["labels"] = ["ubuntu-24.04"]
                with self.assertRaises(TrustedEvidenceError):
                    build_platform_manifests(
                        source_run,
                        rejected_jobs,
                        source_artifacts,
                        source_receipts,
                        repository=REPOSITORY,
                        source_revision=REVISION,
                        source_run_id=SOURCE_RUN_ID,
                        run_id=EVIDENCE_RUN_ID,
                        run_attempt=RUN_ATTEMPT,
                        workflow_ref=workflow_ref("platform-ci"),
                    )

        receipt_mutations: list[tuple[str, dict[str, object], dict[str, object], list[dict[str, object]]]] = []
        spoofed_runner = deepcopy(source_receipts)
        spoofed_runner[0]["runner_environment"] = "self-hosted"
        receipt_mutations.append(("spoofed-runner", source_run, source_artifacts, spoofed_runner))
        stale_receipts = deepcopy(source_receipts)
        stale_receipts[0]["generated_at"] = (now - timedelta(hours=25)).isoformat().replace("+00:00", "Z")
        receipt_mutations.append(("stale", source_run, source_artifacts, stale_receipts))
        replayed_receipts = deepcopy(source_receipts)
        replayed_receipts[-1] = deepcopy(replayed_receipts[0])
        receipt_mutations.append(("replayed", source_run, source_artifacts, replayed_receipts))
        cross_attempt_run = deepcopy(source_run)
        cross_attempt_run["run_attempt"] = RUN_ATTEMPT + 1
        receipt_mutations.append(("cross-attempt", cross_attempt_run, source_artifacts, source_receipts))
        manual_run = deepcopy(source_run)
        manual_run["event"] = "workflow_dispatch"
        receipt_mutations.append(("manual-source-run", manual_run, source_artifacts, source_receipts))
        duplicate_artifacts = deepcopy(source_artifacts)
        duplicate_artifact_list = duplicate_artifacts["artifacts"]
        assert isinstance(duplicate_artifact_list, list)
        duplicate_artifact_list.append(deepcopy(duplicate_artifact_list[0]))
        duplicate_artifacts["total_count"] = len(duplicate_artifact_list)
        receipt_mutations.append(("duplicate-artifact", source_run, duplicate_artifacts, source_receipts))
        for mutation, mutated_run, mutated_artifacts, mutated_receipts in receipt_mutations:
            with self.subTest(mutation=mutation):
                with self.assertRaises(TrustedEvidenceError):
                    build_platform_manifests(
                        mutated_run,
                        source_jobs,
                        mutated_artifacts,
                        mutated_receipts,
                        repository=REPOSITORY,
                        source_revision=REVISION,
                        source_run_id=SOURCE_RUN_ID,
                        run_id=EVIDENCE_RUN_ID,
                        run_attempt=RUN_ATTEMPT,
                        workflow_ref=workflow_ref("platform-ci"),
                        generated_at=now,
                    )

    def test_platform_source_attestations_reject_spoofed_or_out_of_window_provenance(self) -> None:
        from scripts.check_product_readiness import _verify_platform_source_receipt_attestations

        now = datetime.now(timezone.utc)
        _source_run, source_jobs, _source_artifacts, source_receipts = platform_api_payloads(now=now)

        def result_at(value: datetime) -> tuple[list[dict[str, object]], str]:
            return [
                {
                    "attestation": {"bundle": "verified"},
                    "verificationResult": {
                        "verifiedTimestamps": [
                            {"timestamp": value.isoformat().replace("+00:00", "Z")}
                        ]
                    },
                }
            ], ""

        with (
            patch(
                "scripts.check_product_readiness._run_gh_json",
                return_value=result_at(now - timedelta(minutes=30, seconds=30)),
            ),
            patch("scripts.check_product_readiness._attestation_result_matches", return_value=True),
        ):
            accepted, detail = _verify_platform_source_receipt_attestations(
                source_receipts,
                source_jobs,
                expected_revision=REVISION,
                source_run_id=SOURCE_RUN_ID,
                source_run_attempt=RUN_ATTEMPT,
                now=now,
            )
        self.assertTrue(accepted, detail)

        cases = (
            ("self-hosted-certificate", now - timedelta(minutes=30, seconds=30), False),
            ("late-attestation", now - timedelta(minutes=1), True),
        )
        for case, timestamp, matcher_result in cases:
            with self.subTest(case=case):
                with (
                    patch(
                        "scripts.check_product_readiness._run_gh_json",
                        return_value=result_at(timestamp),
                    ),
                    patch(
                        "scripts.check_product_readiness._attestation_result_matches",
                        return_value=matcher_result,
                    ),
                ):
                    accepted, _detail = _verify_platform_source_receipt_attestations(
                        source_receipts,
                        source_jobs,
                        expected_revision=REVISION,
                        source_run_id=SOURCE_RUN_ID,
                        source_run_attempt=RUN_ATTEMPT,
                        now=now,
                    )
                self.assertFalse(accepted)

    def test_ci_point_bearing_jobs_publish_attested_receipts(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        action = (ROOT / ".github" / "actions" / "platform-ci-receipt" / "action.yml").read_text(
            encoding="utf-8"
        )
        self.assertEqual(workflow.count("uses: ./.github/actions/platform-ci-receipt"), 8)
        self.assertEqual(workflow.count("Publish cryptographically attested platform receipt"), 8)
        self.assertEqual(
            workflow.count("if: github.event_name == 'push' && github.ref == 'refs/heads/main'"),
            8,
        )
        self.assertNotIn(
            "github.event_name == 'push' || github.event_name == 'workflow_dispatch'",
            workflow,
        )
        self.assertGreaterEqual(workflow.count("attestations: write"), 9)
        self.assertGreaterEqual(workflow.count("id-token: write"), 9)
        self.assertIn("actions/attest-build-provenance@4d101475", action)
        self.assertIn("actions/upload-artifact@043fb46", action)
        self.assertIn("--run-attempt \"${GITHUB_RUN_ATTEMPT}\"", action)
        self.assertIn("--runner-environment \"${RUNNER_ENVIRONMENT}\"", action)
        self.assertEqual(action.count("git diff --quiet --no-ext-diff"), 2)
        self.assertIn("--expected-sha256 \"${EXPECTED_RECEIPT_SHA256}\"", action)

    def test_live_builder_recomputes_credentialed_and_funded_promotion(self) -> None:
        now = datetime.now(timezone.utc)
        credentialed = build_live_manifest(
            credentialed_report(),
            tier="credentialed",
            repository=REPOSITORY,
            source_revision=REVISION,
            run_id=EVIDENCE_RUN_ID,
            run_attempt=RUN_ATTEMPT,
            workflow_ref=workflow_ref("credentialed-polymarket"),
            generated_at=now,
        )
        self.assertTrue(
            validate_manifest(
                credentialed,
                expected_evidence_type="credentialed-polymarket",
                expected_revision=REVISION,
                now=now,
            )["ok"]
        )
        funded = build_live_manifest(
            funded_report(),
            tier="funded",
            repository=REPOSITORY,
            source_revision=REVISION,
            run_id=EVIDENCE_RUN_ID,
            run_attempt=RUN_ATTEMPT,
            workflow_ref=workflow_ref("funded-polymarket"),
            recovery_journal=funded_recovery_journal(),
            recovery_journal_sha256=RECOVERY_SHA256,
            run_jobs=funded_run_jobs(),
            environment_config=funded_environment_config(),
            run_approvals=funded_run_approvals(),
            token_policy=funded_token_policy(),
            generated_at=now,
        )
        self.assertTrue(
            validate_manifest(
                funded,
                expected_evidence_type="funded-polymarket",
                expected_revision=REVISION,
                now=now,
            )["ok"]
        )

        tampered = deepcopy(credentialed)
        tampered["live_report"]["authenticated_read_checks"] = {}  # type: ignore[index]
        validation = validate_manifest(
            tampered,
            expected_evidence_type="credentialed-polymarket",
            expected_revision=REVISION,
            now=now,
        )
        self.assertFalse(validation["ok"])

    def test_funded_builder_rejects_missing_wrong_label_and_failed_collector(self) -> None:
        cases: list[tuple[str, dict[str, object]]] = []
        missing = funded_run_jobs()
        missing["jobs"] = []
        cases.append(("missing", missing))
        duplicate = funded_run_jobs()
        duplicate["jobs"].append(deepcopy(duplicate["jobs"][0]))  # type: ignore[union-attr]
        duplicate["total_count"] = 2
        cases.append(("duplicate", duplicate))
        incomplete = funded_run_jobs()
        incomplete["total_count"] = 2
        cases.append(("incomplete-metadata", incomplete))
        wrong_labels = funded_run_jobs()
        wrong_labels["jobs"][0]["labels"] = ["ubuntu-24.04"]  # type: ignore[index]
        cases.append(("wrong-label", wrong_labels))
        duplicate_labels = funded_run_jobs()
        duplicate_labels["jobs"][0]["labels"].append("linux")  # type: ignore[index]
        cases.append(("duplicate-label", duplicate_labels))
        failed = funded_run_jobs()
        failed["jobs"][0]["conclusion"] = "failure"  # type: ignore[index]
        cases.append(("failed", failed))
        wrong_revision = funded_run_jobs()
        wrong_revision["jobs"][0]["head_sha"] = "c" * 40  # type: ignore[index]
        cases.append(("wrong-revision", wrong_revision))
        wrong_attempt = funded_run_jobs()
        wrong_attempt["jobs"][0]["run_attempt"] = 2  # type: ignore[index]
        cases.append(("wrong-attempt", wrong_attempt))
        failed_step = funded_run_jobs()
        failed_step["jobs"][0]["steps"][2]["conclusion"] = "failure"  # type: ignore[index]
        cases.append(("failed-step", failed_step))
        duplicate_step = funded_run_jobs()
        duplicate_step["jobs"][0]["steps"].append(  # type: ignore[index]
            deepcopy(duplicate_step["jobs"][0]["steps"][0])  # type: ignore[index]
        )
        cases.append(("duplicate-step", duplicate_step))
        out_of_order = funded_run_jobs()
        steps = out_of_order["jobs"][0]["steps"]  # type: ignore[index]
        steps[1], steps[2] = steps[2], steps[1]
        cases.append(("out-of-order", out_of_order))
        for name, jobs in cases:
            with self.subTest(name=name), self.assertRaises(TrustedEvidenceError):
                build_live_manifest(
                    funded_report(),
                    tier="funded",
                    repository=REPOSITORY,
                    source_revision=REVISION,
                    run_id=EVIDENCE_RUN_ID,
                    run_attempt=RUN_ATTEMPT,
                    workflow_ref=workflow_ref("funded-polymarket"),
                    recovery_journal=funded_recovery_journal(),
                    recovery_journal_sha256=RECOVERY_SHA256,
                    run_jobs=jobs,
                    environment_config=funded_environment_config(),
                    run_approvals=funded_run_approvals(),
                    token_policy=funded_token_policy(),
                )

    def test_funded_builder_requires_preexisting_protections_and_independent_policy(self) -> None:
        def build(
            *,
            report: dict[str, object] | None = None,
            journal: dict[str, object] | None = None,
            environment: object = None,
            approvals: object = None,
            policy: object = None,
        ) -> dict[str, object]:
            return build_live_manifest(
                report or funded_report(),
                tier="funded",
                repository=REPOSITORY,
                source_revision=REVISION,
                run_id=EVIDENCE_RUN_ID,
                run_attempt=RUN_ATTEMPT,
                workflow_ref=workflow_ref("funded-polymarket"),
                recovery_journal=journal or funded_recovery_journal(),
                recovery_journal_sha256=RECOVERY_SHA256,
                run_jobs=funded_run_jobs(),
                environment_config=(
                    funded_environment_config() if environment is None else environment
                ),
                run_approvals=funded_run_approvals() if approvals is None else approvals,
                token_policy=funded_token_policy() if policy is None else policy,
            )

        valid = build()
        self.assertTrue(
            validate_manifest(
                valid,
                expected_evidence_type="funded-polymarket",
                expected_revision=REVISION,
                now=datetime.now(timezone.utc),
            )["ok"]
        )
        self.assertEqual(valid["environment_approval"]["state"], "approved")  # type: ignore[index]
        self.assertEqual(valid["environment_approval"]["run_id"], EVIDENCE_RUN_ID)  # type: ignore[index]

        missing_reviewers = funded_environment_config()
        missing_reviewers["protection_rules"][0]["reviewers"] = []  # type: ignore[index]
        self_review = funded_environment_config()
        self_review["protection_rules"][0]["prevent_self_review"] = False  # type: ignore[index]
        changed_after_start = funded_environment_config()
        changed_after_start["updated_at"] = "2026-09-17T10:00:01Z"
        unprotected_branches = funded_environment_config()
        unprotected_branches["deployment_branch_policy"] = {
            "protected_branches": False,
            "custom_branch_policies": True,
        }
        duplicate_reviewers = funded_environment_config()
        duplicate_reviewers["protection_rules"][0]["reviewers"].append(  # type: ignore[index]
            deepcopy(duplicate_reviewers["protection_rules"][0]["reviewers"][0])  # type: ignore[index]
        )
        stale_policy = funded_token_policy(updated_at="2026-09-17T10:00:01Z")
        malformed_policy = funded_token_policy()
        malformed_policy["unexpected"] = True

        cases = (
            ("missing-reviewers", missing_reviewers, funded_token_policy()),
            ("self-review", self_review, funded_token_policy()),
            ("config-updated-after-start", changed_after_start, funded_token_policy()),
            ("unprotected-branches", unprotected_branches, funded_token_policy()),
            ("duplicate-reviewer", duplicate_reviewers, funded_token_policy()),
            ("missing-policy", funded_environment_config(), {}),
            ("mismatched-policy", funded_environment_config(), funded_token_policy("999")),
            ("policy-updated-after-start", funded_environment_config(), stale_policy),
            ("malformed-policy", funded_environment_config(), malformed_policy),
        )
        for name, environment, policy in cases:
            with self.subTest(name=name), self.assertRaises(TrustedEvidenceError):
                build(environment=environment, policy=policy)

        rejected_approval = funded_run_approvals()
        rejected_approval[0]["state"] = "rejected"
        wrong_environment_approval = funded_run_approvals()
        wrong_environment_approval[0]["environments"][0]["id"] = 999  # type: ignore[index]
        malformed_approval = funded_run_approvals()
        malformed_approval[0]["user"]["unexpected"] = True  # type: ignore[index]
        approval_cases: tuple[tuple[str, object], ...] = (
            ("missing-run-approval", []),
            ("rejected-run-approval", rejected_approval),
            ("wrong-approval-environment", wrong_environment_approval),
            ("duplicate-run-approval", funded_run_approvals() * 2),
            ("malformed-run-approval", malformed_approval),
        )
        for name, approvals in approval_cases:
            with self.subTest(name=name), self.assertRaises(TrustedEvidenceError):
                build(approvals=approvals)

        replayed_attempt_jobs = funded_run_jobs()
        replayed_attempt_jobs["jobs"][0]["run_attempt"] = 2  # type: ignore[index]
        with self.assertRaises(TrustedEvidenceError):
            build_live_manifest(
                funded_report(),
                tier="funded",
                repository=REPOSITORY,
                source_revision=REVISION,
                run_id=EVIDENCE_RUN_ID,
                run_attempt=2,
                workflow_ref=workflow_ref("funded-polymarket"),
                recovery_journal=funded_recovery_journal(),
                recovery_journal_sha256=RECOVERY_SHA256,
                run_jobs=replayed_attempt_jobs,
                environment_config=funded_environment_config(),
                run_approvals=funded_run_approvals(),
                token_policy=funded_token_policy(),
            )

        missing_receipt_report = funded_report()
        missing_receipt_report["funded_live_order_check"].pop(  # type: ignore[union-attr]
            "funded_token_policy_receipt"
        )
        with self.assertRaises(TrustedEvidenceError):
            build(report=missing_receipt_report)

        arbitrary_report = funded_report()
        arbitrary_journal = funded_recovery_journal()
        arbitrary_report["funded_live_order_check"]["token_id"] = "dispatcher-choice"  # type: ignore[index]
        arbitrary_journal["token_id"] = "dispatcher-choice"
        with self.assertRaises(TrustedEvidenceError):
            build(report=arbitrary_report, journal=arbitrary_journal)

    def test_funded_manifest_rejects_tampered_protection_or_policy_summaries(self) -> None:
        manifest = build_live_manifest(
            funded_report(),
            tier="funded",
            repository=REPOSITORY,
            source_revision=REVISION,
            run_id=EVIDENCE_RUN_ID,
            run_attempt=RUN_ATTEMPT,
            workflow_ref=workflow_ref("funded-polymarket"),
            recovery_journal=funded_recovery_journal(),
            recovery_journal_sha256=RECOVERY_SHA256,
            run_jobs=funded_run_jobs(),
            environment_config=funded_environment_config(),
            run_approvals=funded_run_approvals(),
            token_policy=funded_token_policy(),
        )
        cases: list[tuple[str, dict[str, object]]] = []
        self_review = deepcopy(manifest)
        self_review["environment_protection"]["prevent_self_review"] = False  # type: ignore[index]
        cases.append(("self-review", self_review))
        stale_environment = deepcopy(manifest)
        stale_environment["environment_protection"]["updated_at"] = (  # type: ignore[index]
            "2026-09-17T10:00:01Z"
        )
        cases.append(("changed-environment", stale_environment))
        changed_policy = deepcopy(manifest)
        changed_policy["funded_token_policy"]["token_ids"] = ["999"]  # type: ignore[index]
        cases.append(("changed-policy", changed_policy))
        changed_approval = deepcopy(manifest)
        changed_approval["environment_approval"]["reviewer"]["id"] = 0  # type: ignore[index]
        cases.append(("changed-approval", changed_approval))
        for name, candidate in cases:
            with self.subTest(name=name):
                validation = validate_manifest(
                    candidate,
                    expected_evidence_type="funded-polymarket",
                    expected_revision=REVISION,
                    now=datetime.now(timezone.utc),
                )
                self.assertFalse(validation["ok"])

    def test_funded_builder_rejects_ambiguous_or_cross_run_recovery_journal(self) -> None:
        cases: list[tuple[str, dict[str, object]]] = []
        for name, field, value in (
            ("cross-run", "workflow_run_id", EVIDENCE_RUN_ID + 1),
            ("cross-attempt", "workflow_run_attempt", RUN_ATTEMPT + 1),
            ("wrong-nonce", "evidence_nonce", "wrong"),
            ("short-sequence", "sequence", 2),
            ("bad-account", "account_address", "not-an-address"),
            ("time-reversal", "updated_at", "2026-09-17T09:59:59Z"),
            ("unresolved", "resolved", False),
        ):
            journal = funded_recovery_journal()
            journal[field] = value
            cases.append((name, journal))
        extra = funded_recovery_journal()
        extra["unexpected"] = True
        cases.append(("unknown-field", extra))

        for name, journal in cases:
            with self.subTest(name=name), self.assertRaises(TrustedEvidenceError):
                build_live_manifest(
                    funded_report(),
                    tier="funded",
                    repository=REPOSITORY,
                    source_revision=REVISION,
                    run_id=EVIDENCE_RUN_ID,
                    run_attempt=RUN_ATTEMPT,
                    workflow_ref=workflow_ref("funded-polymarket"),
                    recovery_journal=journal,
                    recovery_journal_sha256=RECOVERY_SHA256,
                    run_jobs=funded_run_jobs(),
                    environment_config=funded_environment_config(),
                    run_approvals=funded_run_approvals(),
                    token_policy=funded_token_policy(),
                )

    def test_every_formerly_diagnostic_type_can_pass_exact_hosted_verification(self) -> None:
        from scripts.check_product_readiness import _attested_trusted_evidence

        now = datetime.now(timezone.utc)
        non_live, credentialed, funded = all_manifests(now)
        manifests = {**non_live, "credentialed-polymarket": credentialed, "funded-polymarket": funded}
        with tempfile.TemporaryDirectory() as temporary:
            for evidence_type, manifest in manifests.items():
                with self.subTest(evidence_type=evidence_type):
                    path = Path(temporary) / str(WORKFLOW_CONTRACTS[evidence_type]["subject_name"])
                    write_manifest(path, manifest)
                    api = hosted_api_side_effect(evidence_type, manifest, now=now)
                    patches = [
                        patch("scripts.check_product_readiness._run_gh_json", side_effect=api),
                        patch("scripts.check_product_readiness._attestation_result_matches", return_value=True),
                    ]
                    if evidence_type == "funded-polymarket":
                        patches.append(
                            patch("polymarket.live_reports.POLYMARKET_BOUNDED_AUDIT_MUTATIONS_SUPPORTED", True)
                        )
                    with patches[0], patches[1]:
                        if len(patches) == 3:
                            with patches[2]:
                                result = _attested_trusted_evidence(
                                    str(path),
                                    evidence_type,
                                    evidence_type=evidence_type,
                                    expected_revision=REVISION,
                                    now=now,
                                )
                        else:
                            result = _attested_trusted_evidence(
                                str(path),
                                evidence_type,
                                evidence_type=evidence_type,
                                expected_revision=REVISION,
                                now=now,
                            )
                    self.assertEqual(result["status"], "pass", result)

    def test_manual_manifest_and_tampered_attested_manifest_remain_fail_closed(self) -> None:
        from scripts.check_product_readiness import _attested_trusted_evidence

        now = datetime.now(timezone.utc)
        manual = {
            "schema_version": 1,
            "verified": True,
            "evidence_type": "repository-settings",
            "reviewed_by": "self",
            "reviewed_at": now.isoformat(),
            "source": "manual",
            "checks": [{"name": name, "status": "pass"} for name in REPOSITORY_SETTINGS_CHECKS],
        }
        manifests = build_governance_manifests(
            governance_payload(),
            repository=REPOSITORY,
            source_revision=REVISION,
            run_id=EVIDENCE_RUN_ID,
            run_attempt=RUN_ATTEMPT,
            workflow_ref=workflow_ref("repository-settings"),
            generated_at=now,
        )
        tampered = deepcopy(manifests["repository-settings"])
        tampered["checks"][0]["status"] = "fail"  # type: ignore[index]

        with tempfile.TemporaryDirectory() as temporary:
            manual_path = Path(temporary) / "manual.json"
            manual_path.write_text(json.dumps(manual), encoding="utf-8")
            manual_result = _attested_trusted_evidence(
                str(manual_path),
                "repository settings",
                evidence_type="repository-settings",
                expected_revision=REVISION,
                now=now,
            )
            self.assertEqual(manual_result["status"], "diagnostic")

            tampered_path = Path(temporary) / "tampered.json"
            write_manifest(tampered_path, tampered)
            tampered_result = _attested_trusted_evidence(
                str(tampered_path),
                "repository settings",
                evidence_type="repository-settings",
                expected_revision=REVISION,
                now=now,
            )
            self.assertEqual(tampered_result["status"], "fail")
            self.assertIn("semantic contract", tampered_result["detail"])

    def test_attested_evidence_rejects_absent_stale_cross_revision_and_replayed_artifacts(self) -> None:
        from scripts.check_product_readiness import _attested_trusted_evidence

        now = datetime.now(timezone.utc)
        missing = _attested_trusted_evidence(
            None,
            "repository settings",
            evidence_type="repository-settings",
            expected_revision=REVISION,
            now=now,
        )
        self.assertEqual(missing["status"], "not_run")

        stale = build_governance_manifests(
            governance_payload(),
            repository=REPOSITORY,
            source_revision=REVISION,
            run_id=EVIDENCE_RUN_ID,
            run_attempt=RUN_ATTEMPT,
            workflow_ref=workflow_ref("repository-settings"),
            generated_at=now - timedelta(hours=25),
        )["repository-settings"]
        current = build_governance_manifests(
            governance_payload(),
            repository=REPOSITORY,
            source_revision=REVISION,
            run_id=EVIDENCE_RUN_ID,
            run_attempt=RUN_ATTEMPT,
            workflow_ref=workflow_ref("repository-settings"),
            generated_at=now - timedelta(minutes=2),
        )["repository-settings"]

        with tempfile.TemporaryDirectory() as temporary:
            stale_path = Path(temporary) / "stale.json"
            write_manifest(stale_path, stale)
            stale_result = _attested_trusted_evidence(
                str(stale_path),
                "repository settings",
                evidence_type="repository-settings",
                expected_revision=REVISION,
                now=now,
            )
            self.assertEqual(stale_result["status"], "fail")
            self.assertIn("older than 24 hours", stale_result["detail"])

            current_path = Path(temporary) / "current.json"
            write_manifest(current_path, current)
            cross_revision = _attested_trusted_evidence(
                str(current_path),
                "repository settings",
                evidence_type="repository-settings",
                expected_revision="b" * 40,
                now=now,
            )
            self.assertEqual(cross_revision["status"], "fail")
            self.assertIn("semantic contract", cross_revision["detail"])

            normal_api = hosted_api_side_effect("repository-settings", current, now=now)

            def replayed_artifact(command: list[str], *, timeout: int = 30):
                payload, error = normal_api(command, timeout=timeout)
                if command[-1].endswith(f"/actions/runs/{EVIDENCE_RUN_ID}/artifacts?per_page=100"):
                    assert isinstance(payload, dict)
                    payload = deepcopy(payload)
                    artifacts = payload["artifacts"]
                    assert isinstance(artifacts, list)
                    artifacts.append(deepcopy(artifacts[0]))
                    payload["total_count"] = 2
                return payload, error

            with (
                patch("scripts.check_product_readiness._run_gh_json", side_effect=replayed_artifact),
                patch("scripts.check_product_readiness._attestation_result_matches", return_value=True),
            ):
                replayed = _attested_trusted_evidence(
                    str(current_path),
                    "repository settings",
                    evidence_type="repository-settings",
                    expected_revision=REVISION,
                    now=now,
                )
            self.assertEqual(replayed["status"], "fail")
            self.assertIn("exactly one distinct evidence artifact", replayed["detail"])

    def test_hosted_job_must_complete_every_review_and_attestation_step(self) -> None:
        from scripts.check_product_readiness import _attested_trusted_evidence

        now = datetime.now(timezone.utc)
        manifest = build_governance_manifests(
            governance_payload(),
            repository=REPOSITORY,
            source_revision=REVISION,
            run_id=EVIDENCE_RUN_ID,
            run_attempt=RUN_ATTEMPT,
            workflow_ref=workflow_ref("repository-settings"),
            generated_at=now - timedelta(minutes=2),
        )["repository-settings"]
        normal_api = hosted_api_side_effect("repository-settings", manifest, now=now)

        def missing_review_step(command: list[str], *, timeout: int = 30):
            payload, error = normal_api(command, timeout=timeout)
            if command[-1].endswith(f"/actions/runs/{EVIDENCE_RUN_ID}/jobs?filter=latest&per_page=100"):
                assert isinstance(payload, dict)
                payload = deepcopy(payload)
                jobs = payload["jobs"]
                assert isinstance(jobs, list) and isinstance(jobs[0], dict)
                jobs[0]["steps"] = list(jobs[0]["steps"])[:-1]
            return payload, error

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "repository-settings-evidence.json"
            write_manifest(path, manifest)
            with (
                patch("scripts.check_product_readiness._run_gh_json", side_effect=missing_review_step),
                patch("scripts.check_product_readiness._attestation_result_matches", return_value=True),
            ):
                result = _attested_trusted_evidence(
                    str(path),
                    "repository settings",
                    evidence_type="repository-settings",
                    expected_revision=REVISION,
                    now=now,
                )
        self.assertEqual(result["status"], "fail")
        self.assertIn("every required collection, review, and attestation step", result["detail"])

    def test_hosted_job_rejects_reordered_safety_steps(self) -> None:
        from scripts.check_product_readiness import _attested_trusted_evidence

        now = datetime.now(timezone.utc)
        manifest = build_governance_manifests(
            governance_payload(),
            repository=REPOSITORY,
            source_revision=REVISION,
            run_id=EVIDENCE_RUN_ID,
            run_attempt=RUN_ATTEMPT,
            workflow_ref=workflow_ref("repository-settings"),
            generated_at=now - timedelta(minutes=2),
        )["repository-settings"]
        normal_api = hosted_api_side_effect("repository-settings", manifest, now=now)

        def reordered_steps(command: list[str], *, timeout: int = 30):
            payload, error = normal_api(command, timeout=timeout)
            if command[-1].endswith(
                f"/actions/runs/{EVIDENCE_RUN_ID}/jobs?filter=latest&per_page=100"
            ):
                assert isinstance(payload, dict)
                payload = deepcopy(payload)
                jobs = payload["jobs"]
                assert isinstance(jobs, list) and isinstance(jobs[0], dict)
                steps = jobs[0]["steps"]
                assert isinstance(steps, list) and len(steps) >= 5
                steps[2], steps[4] = steps[4], steps[2]
            return payload, error

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "repository-settings-evidence.json"
            write_manifest(path, manifest)
            with (
                patch("scripts.check_product_readiness._run_gh_json", side_effect=reordered_steps),
                patch("scripts.check_product_readiness._attestation_result_matches", return_value=True),
            ):
                result = _attested_trusted_evidence(
                    str(path),
                    "repository settings",
                    evidence_type="repository-settings",
                    expected_revision=REVISION,
                    now=now,
                )
        self.assertEqual(result["status"], "fail")
        self.assertIn("in order", result["detail"])

    def test_generic_passing_deployment_object_retains_operations_ceiling(self) -> None:
        from scripts.check_product_readiness import _parser, build_report

        args = _parser().parse_args(
            [
                "--full-local",
                "--run-public-live",
                "--repository-settings-evidence",
                "repository.json",
                "--release-environment-evidence",
                "release-environment.json",
                "--release-history-evidence",
                "release.json",
                "--release-evidence",
                "release.json",
                "--deployment-evidence",
                "deployment.json",
                "--deployment-origin",
                "https://markets.example.net",
                "--platform-ci-evidence",
                "platform-ci.json",
                "--platform-evidence",
                "platform.json",
                "--credentialed-evidence",
                "credentialed.json",
                "--funded-evidence",
                "funded.json",
            ]
        )
        attested = {"status": "pass", "detail": "attested"}
        with (
            patch("scripts.check_product_readiness._repository_is_clean", return_value=True),
            patch("scripts.check_product_readiness._repository_revision", return_value=REVISION),
            patch("scripts.check_product_readiness._run_local_gates", return_value={"status": "pass"}),
            patch("scripts.check_product_readiness._run_public_live", return_value={"status": "pass"}),
            patch("scripts.check_product_readiness._attested_public_live_report", return_value=attested),
            patch("scripts.check_product_readiness._attested_trusted_evidence", return_value=attested),
            patch("scripts.check_product_readiness._attested_release_report", return_value=attested),
            patch(
                "scripts.check_product_readiness._deployment_evidence",
                return_value=(True, "attested", attested),
            ),
            patch(
                "scripts.check_product_readiness._live_evidence",
                return_value=(True, "attested", attested),
            ),
        ):
            report = build_report(args)

        self.assertEqual(report["score"], 98)
        self.assertEqual(report["status"], "not_ready")
        operations = next(
            category for category in report["categories"] if category["name"] == "operations_recovery"
        )
        self.assertEqual(operations["earned"], 13)
        self.assertEqual(operations["possible"], 15)
        self.assertTrue(any("Prometheus" in detail for detail in operations["missing"]))
        self.assertTrue(any("unattended" in detail for detail in operations["missing"]))

    def test_scorer_can_reach_100_only_with_explicit_operational_capabilities(self) -> None:
        from scripts.check_product_readiness import _parser, build_report

        args = _parser().parse_args(
            [
                "--full-local",
                "--run-public-live",
                "--repository-settings-evidence",
                "repository.json",
                "--release-environment-evidence",
                "release-environment.json",
                "--release-history-evidence",
                "release.json",
                "--release-evidence",
                "release.json",
                "--deployment-evidence",
                "deployment.json",
                "--deployment-origin",
                "https://markets.example.net",
                "--platform-ci-evidence",
                "platform-ci.json",
                "--platform-evidence",
                "platform.json",
                "--credentialed-evidence",
                "credentialed.json",
                "--funded-evidence",
                "funded.json",
            ]
        )
        attested = {"status": "pass", "detail": "attested"}
        operationally_attested = {
            **attested,
            "capabilities": {"alert_delivery": True, "unattended_workers": True},
        }
        with (
            patch("scripts.check_product_readiness._repository_is_clean", return_value=True),
            patch("scripts.check_product_readiness._repository_revision", return_value=REVISION),
            patch("scripts.check_product_readiness._run_local_gates", return_value={"status": "pass"}),
            patch("scripts.check_product_readiness._run_public_live", return_value={"status": "pass"}),
            patch("scripts.check_product_readiness._attested_public_live_report", return_value=attested),
            patch("scripts.check_product_readiness._attested_trusted_evidence", return_value=attested),
            patch("scripts.check_product_readiness._attested_release_report", return_value=attested),
            patch(
                "scripts.check_product_readiness._deployment_evidence",
                return_value=(True, "attested", operationally_attested),
            ),
            patch(
                "scripts.check_product_readiness._live_evidence",
                return_value=(True, "attested", attested),
            ),
        ):
            report = build_report(args)

        self.assertEqual(report["score"], 100)
        self.assertEqual(report["status"], "ready")
        operations = next(
            category for category in report["categories"] if category["name"] == "operations_recovery"
        )
        self.assertEqual(operations["earned"], 15)
        self.assertEqual(operations["possible"], 15)
        self.assertEqual(operations["missing"], [])

    def test_evidence_workflows_are_manual_main_only_and_fail_closed(self) -> None:
        paths = (
            ROOT / ".github" / "workflows" / "governance-evidence.yml",
            ROOT / ".github" / "workflows" / "platform-evidence.yml",
            ROOT / ".github" / "workflows" / "polymarket-evidence.yml",
        )
        for path in paths:
            with self.subTest(path=path.name):
                text = path.read_text(encoding="utf-8")
                self.assertIn("workflow_dispatch:", text)
                self.assertIn("github.ref == 'refs/heads/main'", text)
                self.assertNotIn("pull_request:", text)
                self.assertNotIn("\n  push:", text)
                self.assertIn("persist-credentials: false", text)
                self.assertIn("actions/attest-build-provenance@4d101475", text)
                self.assertIn("actions/upload-artifact@043fb46", text)
        funded = paths[-1].read_text(encoding="utf-8")
        platform = paths[1].read_text(encoding="utf-8")
        self.assertIn("Download exact source-job receipts", platform)
        self.assertIn("Verify every source-job receipt attestation", platform)
        self.assertIn("platform-source-artifacts.json", platform)
        self.assertIn("platform-source-receipts.json", platform)
        self.assertIn("scripts/platform_ci_receipt.py collect", platform)
        self.assertIn("gh attestation verify", platform)
        self.assertIn("I_UNDERSTAND_THIS_PLACES_A_REAL_POLYMARKET_ORDER", funded)
        self.assertIn("--cancel-immediately", funded)
        self.assertIn("--recovery-journal", funded)
        self.assertIn("/var/lib/market-sentinel-funded-audit", funded)
        self.assertIn("runs-on: [self-hosted, linux, x64, market-sentinel-production]", funded)
        self.assertIn("Review and attest funded Polymarket evidence", funded)
        self.assertIn("Download exact funded workflow job metadata", funded)
        self.assertIn("Download production funded policy and protections", funded)
        self.assertIn("Download exact funded environment approval history", funded)
        self.assertIn("POLYMARKET_FUNDED_TOKEN_ALLOWLIST: ${{ vars.POLYMARKET_FUNDED_TOKEN_ALLOWLIST }}", funded)
        self.assertIn("--allow-token-environment POLYMARKET_FUNDED_TOKEN_ALLOWLIST", funded)
        self.assertIn("environments/production/variables/POLYMARKET_FUNDED_TOKEN_ALLOWLIST", funded)
        self.assertIn("actions/runs/${GITHUB_RUN_ID}/approvals", funded)
        self.assertIn('--run-approvals "${RUNNER_TEMP}/funded-review/run-approvals.json"', funded)
        self.assertNotIn('--allow-token-id "${TOKEN_ID}"', funded)
        self.assertIn("environment: production", funded)
        self.assertEqual(funded.count("requirements-live.lock"), 3)
        self.assertNotIn("-r requirements.lock", funded)


if __name__ == "__main__":
    unittest.main()
