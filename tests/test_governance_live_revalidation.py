from __future__ import annotations

from copy import deepcopy
from typing import Any
from unittest.mock import patch

from scripts.check_product_readiness import (
    PUBLIC_LIVE_REPOSITORY,
    _verify_live_governance_state,
)
from scripts.verify_repository_settings import (
    GITHUB_ACTIONS_APP_ID,
    REQUIRED_CHECKS,
    REQUIRED_PRODUCTION_SECRETS,
    REQUIRED_PRODUCTION_VARIABLES,
    REQUIRED_RELEASE_SECRETS,
    REQUIRED_RELEASE_TAG_POLICY,
    collect_governance_evidence,
)


def _owner_reviewed_environment(*, release: bool) -> dict[str, Any]:
    return {
        "protection_rules": [
            {
                "type": "required_reviewers",
                "prevent_self_review": False,
                "reviewers": [
                    {"type": "User", "reviewer": {"id": 1, "login": "Yunushan"}},
                ],
            }
        ],
        "deployment_branch_policy": (
            {"protected_branches": False, "custom_branch_policies": True}
            if release
            else {"protected_branches": True, "custom_branch_policies": False}
        ),
    }


def _governance_documents() -> dict[str, dict[str, Any]]:
    prefix = f"/repos/{PUBLIC_LIVE_REPOSITORY}"
    return {
        f"{prefix}/branches/main/protection": {
            "required_status_checks": {
                "strict": True,
                "contexts": sorted(REQUIRED_CHECKS),
                "checks": [
                    {"context": name, "app_id": GITHUB_ACTIONS_APP_ID}
                    for name in sorted(REQUIRED_CHECKS)
                ],
            },
            "enforce_admins": {"enabled": True},
            "required_pull_request_reviews": {
                "required_approving_review_count": 0,
                "dismiss_stale_reviews": True,
                "require_code_owner_reviews": False,
                "require_last_push_approval": False,
            },
            "required_conversation_resolution": {"enabled": True},
            "required_linear_history": {"enabled": True},
            "allow_force_pushes": {"enabled": False},
            "allow_deletions": {"enabled": False},
        },
        f"{prefix}/branches/main/protection/required_signatures": {"enabled": True},
        f"{prefix}/environments/release": _owner_reviewed_environment(release=True),
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
        f"{prefix}/environments/production": _owner_reviewed_environment(release=False),
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


def _digest(documents: dict[str, dict[str, Any]]) -> str:
    checks, _state, digest = collect_governance_evidence(
        PUBLIC_LIVE_REPOSITORY,
        "main",
        "",
        30.0,
        lambda path, _token, _timeout: documents[path],
    )
    assert all(check["status"] == "pass" for check in checks)
    return digest


def _github_query(documents: dict[str, dict[str, Any]]):
    def query(command: list[str], *, timeout: int = 30):
        assert command[:4] == ["gh", "api", "--method", "GET"]
        assert timeout == 30
        return deepcopy(documents["/" + command[-1]]), ""

    return query


def test_live_governance_revalidation_matches_exact_admin_documents() -> None:
    documents = _governance_documents()
    with patch(
        "scripts.check_product_readiness._run_gh_json",
        side_effect=_github_query(documents),
    ) as query:
        accepted, detail = _verify_live_governance_state(_digest(documents))

    assert accepted, detail
    assert "matches" in detail
    assert query.call_count == 9
    assert len({call.args[0][-1] for call in query.call_args_list}) == 9


def test_live_governance_revalidation_rejects_passing_but_drifted_state() -> None:
    attested_documents = _governance_documents()
    live_documents = deepcopy(attested_documents)
    secret_path = (
        f"/repos/{PUBLIC_LIVE_REPOSITORY}/environments/production/secrets?per_page=100"
    )
    live_documents[secret_path]["secrets"].append({"name": "NEW_UNREVIEWED_SECRET"})
    live_documents[secret_path]["total_count"] += 1

    with patch(
        "scripts.check_product_readiness._run_gh_json",
        side_effect=_github_query(live_documents),
    ):
        accepted, detail = _verify_live_governance_state(_digest(attested_documents))

    assert not accepted
    assert "drifted" in detail


def test_live_governance_revalidation_rejects_newly_failing_control() -> None:
    attested_documents = _governance_documents()
    live_documents = deepcopy(attested_documents)
    protection_path = f"/repos/{PUBLIC_LIVE_REPOSITORY}/branches/main/protection"
    live_documents[protection_path]["required_status_checks"]["strict"] = False

    with patch(
        "scripts.check_product_readiness._run_gh_json",
        side_effect=_github_query(live_documents),
    ):
        accepted, detail = _verify_live_governance_state(_digest(attested_documents))

    assert not accepted
    assert "branch_require_up_to_date" in detail


def test_live_governance_revalidation_rejects_solo_policy_or_signature_drift() -> None:
    attested_documents = _governance_documents()
    prefix = f"/repos/{PUBLIC_LIVE_REPOSITORY}/branches/main/protection"
    for control, mutate, expected_failure in (
        (
            "code-owner-gate",
            lambda documents: documents[prefix]["required_pull_request_reviews"].__setitem__(
                "require_code_owner_reviews", True
            ),
            "branch_solo_code_owner_gate_disabled",
        ),
        (
            "approval-count",
            lambda documents: documents[prefix]["required_pull_request_reviews"].__setitem__(
                "required_approving_review_count", 1
            ),
            "branch_solo_zero_approvals",
        ),
        (
            "last-push-gate",
            lambda documents: documents[prefix]["required_pull_request_reviews"].__setitem__(
                "require_last_push_approval", True
            ),
            "branch_solo_last_push_gate_disabled",
        ),
        (
            "signed-commits",
            lambda documents: documents[f"{prefix}/required_signatures"].__setitem__("enabled", False),
            "branch_require_signed_commits",
        ),
    ):
        with patch(
            "scripts.check_product_readiness._run_gh_json",
            side_effect=_github_query(live_documents := deepcopy(attested_documents)),
        ):
            mutate(live_documents)
            accepted, detail = _verify_live_governance_state(_digest(attested_documents))

        assert not accepted, control
        assert expected_failure in detail


def test_live_governance_revalidation_rejects_wrong_owner_or_self_review_gate() -> None:
    attested_documents = _governance_documents()
    prefix = f"/repos/{PUBLIC_LIVE_REPOSITORY}/environments"
    for environment, control, mutate, expected_failure in (
        (
            "release",
            "reviewer",
            lambda rule: rule["reviewers"][0]["reviewer"].__setitem__("login", "other-user"),
            "release_owner_reviewer",
        ),
        (
            "release",
            "self-review",
            lambda rule: rule.__setitem__("prevent_self_review", True),
            "release_allow_owner_approval",
        ),
        (
            "production",
            "reviewer",
            lambda rule: rule["reviewers"][0]["reviewer"].__setitem__("login", "other-user"),
            "production_owner_reviewer",
        ),
        (
            "production",
            "self-review",
            lambda rule: rule.__setitem__("prevent_self_review", True),
            "production_allow_owner_approval",
        ),
    ):
        live_documents = deepcopy(attested_documents)
        reviewer_rule = live_documents[f"{prefix}/{environment}"]["protection_rules"][0]
        mutate(reviewer_rule)
        with patch(
            "scripts.check_product_readiness._run_gh_json",
            side_effect=_github_query(live_documents),
        ):
            accepted, detail = _verify_live_governance_state(_digest(attested_documents))

        assert not accepted, (environment, control)
        assert expected_failure in detail


def test_live_governance_revalidation_fails_closed_without_admin_api_access() -> None:
    documents = _governance_documents()
    with patch(
        "scripts.check_product_readiness._run_gh_json",
        return_value=(None, "GitHub CLI verification failed"),
    ) as query:
        accepted, detail = _verify_live_governance_state(_digest(documents))

    assert not accepted
    assert "could not be re-read" in detail
    assert query.call_count == 1


def test_live_governance_revalidation_rejects_malformed_attested_digest_without_api_calls() -> None:
    with patch("scripts.check_product_readiness._run_gh_json") as query:
        accepted, detail = _verify_live_governance_state("not-a-sha256")

    assert not accepted
    assert "malformed" in detail
    query.assert_not_called()
