from __future__ import annotations

import unittest
from unittest.mock import patch

import scripts.verify_repository_settings as repository_settings
from scripts.verify_repository_settings import (
    GITHUB_ACTIONS_APP_ID,
    REQUIRED_CHECKS,
    REQUIRED_PRODUCTION_SECRETS,
    REQUIRED_PRODUCTION_VARIABLES,
    REQUIRED_RELEASE_ENVIRONMENT_CHECKS,
    REQUIRED_REPOSITORY_SETTINGS_CHECKS,
    REQUIRED_RELEASE_SECRETS,
    REQUIRED_RELEASE_TAG_POLICY,
    check_branch_protection,
    check_production_environment,
    check_production_variables,
    check_release_environment,
    check_release_variable,
    collect_governance_evidence,
    collect_checks,
    governance_state_sha256,
)


def _passing_protection() -> dict:
    return {
        "required_status_checks": {
            "strict": True,
            "contexts": sorted(REQUIRED_CHECKS),
            "checks": [
                {"context": context, "app_id": GITHUB_ACTIONS_APP_ID}
                for context in sorted(REQUIRED_CHECKS)
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
    }


def _passing_signature_protection() -> dict:
    return {"enabled": True}


def _passing_environment(*, release: bool = False, owner: str = "acme") -> dict:
    return {
        "protection_rules": [
            {
                "type": "required_reviewers",
                "prevent_self_review": False,
                "reviewers": [
                    {"type": "User", "reviewer": {"id": 1, "login": owner}},
                ],
            }
        ],
        "deployment_branch_policy": (
            {"protected_branches": False, "custom_branch_policies": True}
            if release
            else {"protected_branches": True}
        ),
    }


def _passing_release_policies() -> dict:
    return {
        "total_count": 2,
        "branch_policies": [
            {"type": "branch", "name": "main"},
            {"type": "tag", "name": REQUIRED_RELEASE_TAG_POLICY},
        ]
    }


def _passing_documents(repository: str = "acme/market-sentinel") -> dict[str, dict]:
    prefix = f"/repos/{repository}"
    owner = repository.split("/", 1)[0]
    return {
        f"{prefix}/branches/main/protection": _passing_protection(),
        f"{prefix}/branches/main/protection/required_signatures": _passing_signature_protection(),
        f"{prefix}/environments/release": _passing_environment(release=True, owner=owner),
        f"{prefix}/environments/release/deployment-branch-policies?per_page=100": _passing_release_policies(),
        f"{prefix}/environments/release/secrets?per_page=100": {
            "total_count": len(REQUIRED_RELEASE_SECRETS),
            "secrets": [
                {"name": name, "value": "must-not-escape"}
                for name in sorted(REQUIRED_RELEASE_SECRETS)
            ],
        },
        f"{prefix}/environments/production": _passing_environment(owner=owner),
        f"{prefix}/environments/production/secrets?per_page=100": {
            "total_count": len(REQUIRED_PRODUCTION_SECRETS),
            "secrets": [{"name": name, "value": "must-not-escape"} for name in sorted(REQUIRED_PRODUCTION_SECRETS)]
        },
        f"{prefix}/environments/production/variables?per_page=100": {
            "total_count": len(REQUIRED_PRODUCTION_VARIABLES),
            "variables": [{"name": name, "value": "must-not-escape"} for name in sorted(REQUIRED_PRODUCTION_VARIABLES)]
        },
        f"{prefix}/actions/variables/REQUIRE_WINDOWS_CODE_SIGNING": {"value": "true"},
    }


class RepositorySettingsTests(unittest.TestCase):
    def test_repository_settings_check_contract_matches_readiness_scorer(self) -> None:
        from scripts.check_product_readiness import REQUIRED_REPOSITORY_SETTINGS_CHECKS as SCORER_CHECKS
        from scripts.trusted_readiness_evidence import REPOSITORY_SETTINGS_CHECKS as MANIFEST_CHECKS

        generated = {
            check["name"]
            for check in check_branch_protection(
                _passing_protection(),
                _passing_signature_protection(),
            )
        }
        self.assertEqual(tuple(REQUIRED_REPOSITORY_SETTINGS_CHECKS), tuple(SCORER_CHECKS))
        self.assertEqual(tuple(REQUIRED_REPOSITORY_SETTINGS_CHECKS), tuple(MANIFEST_CHECKS))
        self.assertEqual(generated, set(REQUIRED_REPOSITORY_SETTINGS_CHECKS))

    def test_release_environment_check_contract_matches_readiness_scorer(self) -> None:
        from scripts.check_product_readiness import REQUIRED_RELEASE_ENVIRONMENT_CHECKS as SCORER_CHECKS

        generated = {
            check["name"]
            for check in [
                *check_release_environment(
                    _passing_environment(release=True),
                    REQUIRED_RELEASE_SECRETS,
                    _passing_release_policies(),
                    "main",
                    "acme",
                ),
                check_release_variable({"value": "true"}),
                *check_production_environment(_passing_environment(), REQUIRED_PRODUCTION_SECRETS, "acme"),
                check_production_variables(REQUIRED_PRODUCTION_VARIABLES),
            ]
        }
        self.assertEqual(tuple(REQUIRED_RELEASE_ENVIRONMENT_CHECKS), tuple(SCORER_CHECKS))
        self.assertEqual(generated, set(REQUIRED_RELEASE_ENVIRONMENT_CHECKS))

    def test_production_variables_require_alertmanager_group_id(self) -> None:
        variable_name = "MARKET_SENTINEL_ALERTMANAGER_GID"
        self.assertIn(variable_name, REQUIRED_PRODUCTION_VARIABLES)

        incomplete = REQUIRED_PRODUCTION_VARIABLES - {variable_name}
        check = check_production_variables(incomplete)

        self.assertEqual(check["status"], "fail")
        self.assertEqual(check["detail"], f"missing={variable_name}")

    def test_request_transport_uses_system_trust_store_when_available(self) -> None:
        previous = repository_settings._TRUSTSTORE_INJECTED
        try:
            repository_settings._TRUSTSTORE_INJECTED = False
            with patch.object(repository_settings, "truststore") as truststore:
                repository_settings._ensure_system_trust_store()
                truststore.inject_into_ssl.assert_called_once_with()
                self.assertTrue(repository_settings._TRUSTSTORE_INJECTED)
        finally:
            repository_settings._TRUSTSTORE_INJECTED = previous

    def test_branch_protection_requires_all_documented_controls(self) -> None:
        checks = check_branch_protection(_passing_protection(), _passing_signature_protection())
        self.assertTrue(all(check["status"] == "pass" for check in checks))

        weak = _passing_protection()
        weak["required_status_checks"] = {"strict": False, "contexts": ["CodeQL"]}
        weak["required_pull_request_reviews"] = {
            "required_approving_review_count": 1,
            "dismiss_stale_reviews": False,
            "require_code_owner_reviews": True,
            "require_last_push_approval": True,
        }
        weak["allow_force_pushes"] = {"enabled": True}
        names = {
            check["name"]
            for check in check_branch_protection(weak, {"enabled": False})
            if check["status"] == "fail"
        }
        self.assertIn("branch_required_status_checks", names)
        self.assertIn("branch_status_checks_bound_to_actions_app", names)
        self.assertIn("branch_require_up_to_date", names)
        self.assertIn("branch_force_pushes_disabled", names)
        self.assertIn("branch_solo_zero_approvals", names)
        self.assertIn("branch_dismiss_stale_reviews", names)
        self.assertIn("branch_solo_code_owner_gate_disabled", names)
        self.assertIn("branch_solo_last_push_gate_disabled", names)
        self.assertIn("branch_require_signed_commits", names)

    def test_release_environment_requires_reviewers_branches_and_signing_secrets(self) -> None:
        secret_names = sorted(REQUIRED_RELEASE_SECRETS)
        checks = check_release_environment(
            _passing_environment(release=True),
            secret_names,
            _passing_release_policies(),
            "main",
            "acme",
        )
        self.assertTrue(all(check["status"] == "pass" for check in checks))
        self.assertEqual(check_release_variable({"value": "true"})["status"], "pass")

        weak = {"protection_rules": [], "deployment_branch_policy": {"protected_branches": False}}
        failures = {
            check["name"]
            for check in check_release_environment(weak, [], {"branch_policies": []}, "main", "acme")
            if check["status"] == "fail"
        }
        self.assertEqual(
            failures,
            {
                "release_required_reviewers",
                "release_owner_reviewer",
                "release_allow_owner_approval",
                "release_deployment_refs",
                "release_signing_secrets",
            },
        )
        self.assertEqual(check_release_variable({"value": "false"})["status"], "fail")

        for label, policies in (
            ("missing-tag", {"branch_policies": [{"type": "branch", "name": "main"}]}),
            (
                "overbroad",
                {
                    "branch_policies": [
                        {"type": "branch", "name": "main"},
                        {"type": "tag", "name": "*"},
                    ]
                },
            ),
        ):
            with self.subTest(label=label):
                ref_check = next(
                    check
                    for check in check_release_environment(
                        _passing_environment(release=True),
                        secret_names,
                        policies,
                        "main",
                        "acme",
                    )
                    if check["name"] == "release_deployment_refs"
                )
                self.assertEqual(ref_check["status"], "fail")

    def test_environment_reviewer_must_be_exactly_the_repository_owner(self) -> None:
        for label, reviewers in (
            ("missing", []),
            ("other-user", [{"type": "User", "reviewer": {"id": 2, "login": "someone-else"}}]),
            ("team", [{"type": "Team", "reviewer": {"id": 1, "login": "acme"}}]),
            ("invalid-id", [{"type": "User", "reviewer": {"id": True, "login": "acme"}}]),
            (
                "extra-reviewer",
                [
                    {"type": "User", "reviewer": {"id": 1, "login": "acme"}},
                    {"type": "User", "reviewer": {"id": 2, "login": "someone-else"}},
                ],
            ),
        ):
            with self.subTest(label=label):
                release = _passing_environment(release=True)
                production = _passing_environment()
                release["protection_rules"][0]["reviewers"] = reviewers
                production["protection_rules"][0]["reviewers"] = reviewers
                release_checks = check_release_environment(
                    release, REQUIRED_RELEASE_SECRETS, _passing_release_policies(), "main", "acme"
                )
                production_checks = check_production_environment(
                    production, REQUIRED_PRODUCTION_SECRETS, "acme"
                )
                self.assertEqual(
                    next(check for check in release_checks if check["name"] == "release_owner_reviewer")["status"],
                    "fail",
                )
                self.assertEqual(
                    next(check for check in production_checks if check["name"] == "production_owner_reviewer")["status"],
                    "fail",
                )

        release = _passing_environment(release=True)
        production = _passing_environment()
        release["protection_rules"][0]["reviewers"][0]["reviewer"]["login"] = "ACME"
        production["protection_rules"][0]["reviewers"][0]["reviewer"]["login"] = "ACME"
        self.assertEqual(
            next(
                check
                for check in check_release_environment(
                    release, REQUIRED_RELEASE_SECRETS, _passing_release_policies(), "main", "acme"
                )
                if check["name"] == "release_owner_reviewer"
            )["status"],
            "pass",
        )
        self.assertEqual(
            next(
                check
                for check in check_production_environment(production, REQUIRED_PRODUCTION_SECRETS, "acme")
                if check["name"] == "production_owner_reviewer"
            )["status"],
            "pass",
        )

    def test_environment_owner_must_be_allowed_to_approve(self) -> None:
        release = _passing_environment(release=True)
        production = _passing_environment()
        release["protection_rules"][0]["prevent_self_review"] = True
        production["protection_rules"][0]["prevent_self_review"] = True
        release_failures = {
            check["name"]
            for check in check_release_environment(
                release, REQUIRED_RELEASE_SECRETS, _passing_release_policies(), "main", "acme"
            )
            if check["status"] == "fail"
        }
        production_failures = {
            check["name"]
            for check in check_production_environment(production, REQUIRED_PRODUCTION_SECRETS, "acme")
            if check["status"] == "fail"
        }
        self.assertEqual(release_failures, {"release_allow_owner_approval"})
        self.assertEqual(production_failures, {"production_allow_owner_approval"})

    def test_collection_uses_documented_read_only_api_endpoints(self) -> None:
        requested: list[str] = []
        documents = _passing_documents()

        def request(path: str, token: str, timeout: float):
            requested.append(path)
            self.assertEqual(token, "not-a-real-token")
            self.assertEqual(timeout, 5.0)
            return documents[path]

        checks = collect_checks("acme/market-sentinel", "main", "not-a-real-token", 5.0, request)
        self.assertEqual(requested, list(documents))
        self.assertTrue(all(check["status"] == "pass" for check in checks))

    def test_collection_rejects_truncated_or_oversized_inventories(self) -> None:
        for endpoint_suffix, collection_name in (
            ("environments/release/deployment-branch-policies?per_page=100", "branch_policies"),
            ("environments/release/secrets?per_page=100", "secrets"),
            ("environments/production/secrets?per_page=100", "secrets"),
            ("environments/production/variables?per_page=100", "variables"),
        ):
            for total_count in (101, 999):
                with self.subTest(endpoint=endpoint_suffix, total_count=total_count):
                    documents = _passing_documents()
                    endpoint = f"/repos/acme/market-sentinel/{endpoint_suffix}"
                    rows = documents[endpoint][collection_name]
                    documents[endpoint]["total_count"] = total_count
                    self.assertNotEqual(total_count, len(rows))
                    with self.assertRaisesRegex(RuntimeError, "incomplete"):
                        collect_governance_evidence(
                            "acme/market-sentinel",
                            "main",
                            "not-a-real-token",
                            5.0,
                            lambda path, _token, _timeout, current=documents: current[path],
                        )

    def test_collected_governance_state_is_canonical_redacted_and_digest_bound(self) -> None:
        documents = _passing_documents()

        def request(path: str, _token: str, _timeout: float):
            return documents[path]

        checks, state, digest = collect_governance_evidence(
            "acme/market-sentinel",
            "main",
            "not-a-real-token",
            5.0,
            request,
        )
        self.assertTrue(all(check["status"] == "pass" for check in checks))
        self.assertEqual(digest, governance_state_sha256(state))
        self.assertEqual(len(digest), 64)
        encoded = str(state)
        self.assertNotIn("must-not-escape", encoded)
        self.assertNotIn("value", state["production_environment"])
        self.assertEqual(
            state["repository_variables"],
            {"REQUIRE_WINDOWS_CODE_SIGNING": "true"},
        )
        self.assertFalse(
            state["branch_protection"]["required_pull_request_reviews"]["require_code_owner_reviews"]
        )
        self.assertEqual(state["release_environment"]["required_reviewers"], [{"type": "User", "id": 1, "login": "acme"}])
        self.assertTrue(state["branch_protection"]["required_signatures"])

        reordered = _passing_documents()
        for payload in reordered.values():
            if isinstance(payload.get("secrets"), list):
                payload["secrets"].reverse()
            if isinstance(payload.get("variables"), list):
                payload["variables"].reverse()
        reordered_checks, reordered_state, reordered_digest = collect_governance_evidence(
            "acme/market-sentinel",
            "main",
            "not-a-real-token",
            5.0,
            lambda path, _token, _timeout: reordered[path],
        )
        self.assertTrue(all(check["status"] == "pass" for check in reordered_checks))
        self.assertEqual(reordered_state, state)
        self.assertEqual(reordered_digest, digest)

    def test_governance_digest_changes_when_a_policy_relevant_control_changes(self) -> None:
        documents = _passing_documents()
        _, baseline, baseline_digest = collect_governance_evidence(
            "acme/market-sentinel",
            "main",
            "not-a-real-token",
            5.0,
            lambda path, _token, _timeout: documents[path],
        )
        documents["/repos/acme/market-sentinel/branches/main/protection"]["required_status_checks"][
            "strict"
        ] = False
        checks, changed, changed_digest = collect_governance_evidence(
            "acme/market-sentinel",
            "main",
            "not-a-real-token",
            5.0,
            lambda path, _token, _timeout: documents[path],
        )
        self.assertNotEqual(changed, baseline)
        self.assertNotEqual(changed_digest, baseline_digest)
        self.assertIn("branch_require_up_to_date", {item["name"] for item in checks if item["status"] == "fail"})

    def test_governance_digest_binds_environment_reviewer_login(self) -> None:
        documents = _passing_documents()
        _, baseline, baseline_digest = collect_governance_evidence(
            "acme/market-sentinel",
            "main",
            "not-a-real-token",
            5.0,
            lambda path, _token, _timeout: documents[path],
        )
        documents["/repos/acme/market-sentinel/environments/release"]["protection_rules"][0][
            "reviewers"
        ][0]["reviewer"]["login"] = "someone-else"
        checks, changed, changed_digest = collect_governance_evidence(
            "acme/market-sentinel",
            "main",
            "not-a-real-token",
            5.0,
            lambda path, _token, _timeout: documents[path],
        )
        self.assertNotEqual(changed, baseline)
        self.assertNotEqual(changed_digest, baseline_digest)
        self.assertIn("release_owner_reviewer", {item["name"] for item in checks if item["status"] == "fail"})

    def test_governance_digest_binds_code_owner_and_signed_commit_controls(self) -> None:
        for label, mutate, expected_failure in (
            (
                "code-owner-reviews",
                lambda documents: documents["/repos/acme/market-sentinel/branches/main/protection"][
                    "required_pull_request_reviews"
                ].__setitem__("require_code_owner_reviews", True),
                "branch_solo_code_owner_gate_disabled",
            ),
            (
                "signed-commits",
                lambda documents: documents[
                    "/repos/acme/market-sentinel/branches/main/protection/required_signatures"
                ].__setitem__("enabled", False),
                "branch_require_signed_commits",
            ),
        ):
            with self.subTest(control=label):
                baseline_documents = _passing_documents()
                _, baseline, baseline_digest = collect_governance_evidence(
                    "acme/market-sentinel",
                    "main",
                    "not-a-real-token",
                    5.0,
                    lambda path, _token, _timeout, documents=baseline_documents: documents[path],
                )
                changed_documents = _passing_documents()
                mutate(changed_documents)
                checks, changed, changed_digest = collect_governance_evidence(
                    "acme/market-sentinel",
                    "main",
                    "not-a-real-token",
                    5.0,
                    lambda path, _token, _timeout, documents=changed_documents: documents[path],
                )
                self.assertNotEqual(changed, baseline)
                self.assertNotEqual(changed_digest, baseline_digest)
                self.assertIn(
                    expected_failure,
                    {item["name"] for item in checks if item["status"] == "fail"},
                )


if __name__ == "__main__":
    unittest.main()
