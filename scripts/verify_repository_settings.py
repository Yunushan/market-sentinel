from __future__ import annotations

import argparse
import hashlib
import json
import os
from typing import Any, Callable, Iterable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

try:
    import truststore
except ImportError:  # pragma: no cover - optional for minimal standalone use
    truststore = None


API_VERSION = "2026-03-10"
DEFAULT_API_URL = "https://api.github.com"
GOVERNANCE_STATE_SCHEMA_VERSION = 2
REQUIRED_CHECKS = frozenset(
    {
        "Python package build",
        "CodeQL",
        "Dependency review",
        "Frontend dependency audit",
        "Python dependency audit",
        "Secret history scan",
        "Workflow and shell lint",
    }
)
GITHUB_ACTIONS_APP_ID = 15368
MINIMUM_INDEPENDENT_REVIEWERS = 2
REQUIRED_RELEASE_TAG_POLICY = "v*.*.*"
REQUIRED_RELEASE_SECRETS = frozenset(
    {
        "READINESS_ADMIN_TOKEN",
        "WINDOWS_CODE_SIGNING_CERTIFICATE_BASE64",
        "WINDOWS_CODE_SIGNING_CERTIFICATE_PASSWORD",
    }
)
JsonRequest = Callable[[str, str, float], Any]
_TRUSTSTORE_INJECTED = False


def _canonical_json_bytes(payload: Mapping[str, Any]) -> bytes:
    """Serialize a governance state deterministically for exact digest binding."""

    return (json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def governance_state_sha256(payload: Mapping[str, Any]) -> str:
    """Return the digest used to bind collected and freshly re-read controls."""

    return hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()


def _ensure_system_trust_store() -> None:
    """Use the host trust store when the optional locked dependency is available."""
    global _TRUSTSTORE_INJECTED
    if _TRUSTSTORE_INJECTED or truststore is None:
        return
    truststore.inject_into_ssl()
    _TRUSTSTORE_INJECTED = True


def _request_json(path: str, token: str, timeout: float, api_url: str = DEFAULT_API_URL) -> Any:
    """Read a GitHub API document without including token material in errors."""
    _ensure_system_trust_store()
    base = api_url.rstrip("/")
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": API_VERSION,
        "User-Agent": "market-sentinel-repository-settings-verifier",
    }
    try:
        with urlopen(Request(f"{base}{path}", headers=headers, method="GET"), timeout=timeout) as response:
            if response.status != 200:
                raise RuntimeError(f"GitHub API {path} returned HTTP {response.status}")
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        try:
            raise RuntimeError(f"GitHub API {path} returned HTTP {exc.code}") from exc
        finally:
            exc.close()
    except (OSError, URLError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"GitHub API request failed for {path}: {type(exc).__name__}") from exc


def _check(name: str, passed: bool, detail: str) -> dict[str, str]:
    return {"name": name, "status": "pass" if passed else "fail", "detail": detail}


def _required_contexts(protection: dict[str, Any]) -> set[str]:
    status_checks = protection.get("required_status_checks")
    if not isinstance(status_checks, dict):
        return set()
    contexts = {str(value) for value in status_checks.get("contexts", []) if isinstance(value, str)}
    for entry in status_checks.get("checks", []):
        if isinstance(entry, dict) and isinstance(entry.get("context"), str):
            contexts.add(entry["context"])
    return contexts


def _actions_app_contexts(protection: dict[str, Any]) -> set[str]:
    status_checks = protection.get("required_status_checks")
    if not isinstance(status_checks, dict):
        return set()
    return {
        str(entry["context"])
        for entry in status_checks.get("checks", [])
        if isinstance(entry, dict)
        and isinstance(entry.get("context"), str)
        and entry.get("app_id") == GITHUB_ACTIONS_APP_ID
    }


def _reviewer_count(reviewer_rule: Any) -> int:
    if not isinstance(reviewer_rule, dict):
        return 0
    identities: set[tuple[str, int]] = set()
    for row in reviewer_rule.get("reviewers", []):
        reviewer = row.get("reviewer") if isinstance(row, dict) else None
        reviewer_type = row.get("type") if isinstance(row, dict) else None
        reviewer_id = reviewer.get("id") if isinstance(reviewer, dict) else None
        if isinstance(reviewer_type, str) and type(reviewer_id) is int and reviewer_id > 0:
            identities.add((reviewer_type, reviewer_id))
    return len(identities)


def check_branch_protection(
    protection: dict[str, Any],
    signature_protection: dict[str, Any],
    required_checks: Iterable[str] = REQUIRED_CHECKS,
) -> list[dict[str, str]]:
    """Validate the branch protection controls required by the checked-in policy."""
    status_checks = protection.get("required_status_checks")
    strict = isinstance(status_checks, dict) and status_checks.get("strict") is True
    contexts = _required_contexts(protection)
    missing_contexts = sorted(set(required_checks) - contexts)
    missing_actions_bindings = sorted(set(required_checks) - _actions_app_contexts(protection))
    enforce_admins = isinstance(protection.get("enforce_admins"), dict) and protection["enforce_admins"].get("enabled") is True
    pull_request_rule = protection.get("required_pull_request_reviews")
    pull_requests = isinstance(pull_request_rule, dict)
    approvals = pull_request_rule.get("required_approving_review_count") if isinstance(pull_request_rule, dict) else None
    minimum_approvals = type(approvals) is int and approvals >= 1
    dismiss_stale = isinstance(pull_request_rule, dict) and pull_request_rule.get("dismiss_stale_reviews") is True
    code_owner_reviews = (
        isinstance(pull_request_rule, dict)
        and pull_request_rule.get("require_code_owner_reviews") is True
    )
    last_push_approval = (
        isinstance(pull_request_rule, dict) and pull_request_rule.get("require_last_push_approval") is True
    )
    signed_commits = signature_protection.get("enabled") is True
    conversation = isinstance(protection.get("required_conversation_resolution"), dict) and protection[
        "required_conversation_resolution"
    ].get("enabled") is True
    linear_history = isinstance(protection.get("required_linear_history"), dict) and protection["required_linear_history"].get(
        "enabled"
    ) is True
    force_pushes = isinstance(protection.get("allow_force_pushes"), dict) and protection["allow_force_pushes"].get("enabled") is True
    deletions = isinstance(protection.get("allow_deletions"), dict) and protection["allow_deletions"].get("enabled") is True
    return [
        _check("branch_required_status_checks", not missing_contexts, "missing=" + ",".join(missing_contexts) if missing_contexts else "all required checks configured"),
        _check(
            "branch_status_checks_bound_to_actions_app",
            not missing_actions_bindings,
            "missing=" + ",".join(missing_actions_bindings)
            if missing_actions_bindings
            else f"all required checks are bound to GitHub Actions app_id={GITHUB_ACTIONS_APP_ID}",
        ),
        _check("branch_require_up_to_date", strict, "required_status_checks.strict must be true"),
        _check("branch_enforce_admins", enforce_admins, "administrator bypass must be disabled"),
        _check("branch_require_pull_request", pull_requests, "required_pull_request_reviews must be configured"),
        _check("branch_minimum_approvals", minimum_approvals, "at least one approving review must be required"),
        _check("branch_dismiss_stale_reviews", dismiss_stale, "stale approvals must be dismissed after new commits"),
        _check(
            "branch_require_code_owner_reviews",
            code_owner_reviews,
            "Code Owner review must be required",
        ),
        _check(
            "branch_require_last_push_approval",
            last_push_approval,
            "the most recent push must be approved by someone other than its author",
        ),
        _check("branch_require_signed_commits", signed_commits, "signed commits must be required"),
        _check("branch_conversation_resolution", conversation, "required conversation resolution must be enabled"),
        _check("branch_linear_history", linear_history, "required linear history must be enabled"),
        _check("branch_force_pushes_disabled", not force_pushes, "force pushes must be disabled"),
        _check("branch_deletions_disabled", not deletions, "branch deletion must be disabled"),
    ]


def check_release_environment(
    environment: dict[str, Any],
    secret_names: Iterable[str],
    deployment_policies: dict[str, Any],
    protected_branch: str,
) -> list[dict[str, str]]:
    """Validate release approvals, exact ref restrictions, signing configuration, and secrets."""
    rules = environment.get("protection_rules")
    rules = rules if isinstance(rules, list) else []
    reviewer_rule = next((rule for rule in rules if isinstance(rule, dict) and rule.get("type") == "required_reviewers"), None)
    self_review_disabled = isinstance(reviewer_rule, dict) and reviewer_rule.get("prevent_self_review") is True
    reviewer_count = _reviewer_count(reviewer_rule)
    branch_policy = environment.get("deployment_branch_policy")
    custom_policy = (
        isinstance(branch_policy, dict)
        and branch_policy.get("protected_branches") is False
        and branch_policy.get("custom_branch_policies") is True
    )
    policy_rows = deployment_policies.get("branch_policies")
    policy_rows = policy_rows if isinstance(policy_rows, list) else []
    observed_policies = {
        (row.get("type"), row.get("name"))
        for row in policy_rows
        if isinstance(row, dict)
        and row.get("type") in {"branch", "tag"}
        and isinstance(row.get("name"), str)
    }
    required_policies = {
        ("branch", protected_branch),
        ("tag", REQUIRED_RELEASE_TAG_POLICY),
    }
    exact_ref_policy = custom_policy and len(policy_rows) == 2 and observed_policies == required_policies
    required_secret_names = set(REQUIRED_RELEASE_SECRETS)
    missing_secrets = sorted(required_secret_names - {str(name) for name in secret_names})
    return [
        _check("release_required_reviewers", reviewer_rule is not None, "release environment must require reviewer approval"),
        _check(
            "release_independent_reviewers",
            reviewer_count >= MINIMUM_INDEPENDENT_REVIEWERS,
            f"release environment needs at least {MINIMUM_INDEPENDENT_REVIEWERS} distinct eligible reviewers; found {reviewer_count}",
        ),
        _check("release_prevent_self_review", self_review_disabled, "release environment must prevent self approval"),
        _check(
            "release_deployment_refs",
            exact_ref_policy,
            (
                f"release environment must allow exactly branch {protected_branch!r} and "
                f"tag pattern {REQUIRED_RELEASE_TAG_POLICY!r}"
            ),
        ),
        _check("release_signing_secrets", not missing_secrets, "missing=" + ",".join(missing_secrets) if missing_secrets else "required release secrets present"),
    ]


def check_production_environment(environment: dict[str, Any], secret_names: Iterable[str]) -> list[dict[str, str]]:
    """Validate the independent approval and secret inventory used by production evidence lanes."""
    rules = environment.get("protection_rules")
    rules = rules if isinstance(rules, list) else []
    reviewer_rule = next(
        (rule for rule in rules if isinstance(rule, dict) and rule.get("type") == "required_reviewers"),
        None,
    )
    reviewer_count = _reviewer_count(reviewer_rule)
    branch_policy = environment.get("deployment_branch_policy")
    protected_branches = isinstance(branch_policy, dict) and branch_policy.get("protected_branches") is True
    missing_secrets = sorted(REQUIRED_PRODUCTION_SECRETS - {str(name) for name in secret_names})
    return [
        _check("production_required_reviewers", reviewer_rule is not None, "production environment must require reviewer approval"),
        _check(
            "production_independent_reviewers",
            reviewer_count >= MINIMUM_INDEPENDENT_REVIEWERS,
            f"production environment needs at least {MINIMUM_INDEPENDENT_REVIEWERS} distinct eligible reviewers; found {reviewer_count}",
        ),
        _check(
            "production_prevent_self_review",
            isinstance(reviewer_rule, dict) and reviewer_rule.get("prevent_self_review") is True,
            "production environment must prevent self approval",
        ),
        _check(
            "production_protected_branches",
            protected_branches,
            "production environment must restrict deployment to protected branches",
        ),
        _check(
            "production_secrets",
            not missing_secrets,
            "missing=" + ",".join(missing_secrets) if missing_secrets else "required production secrets present",
        ),
    ]


def check_production_variables(variable_names: Iterable[str]) -> dict[str, str]:
    observed = {str(name) for name in variable_names}
    missing = sorted(REQUIRED_PRODUCTION_VARIABLES - observed)
    return _check(
        "production_variables",
        not missing,
        "missing=" + ",".join(missing) if missing else "required production variables present",
    )


def check_release_variable(variable: dict[str, Any]) -> dict[str, str]:
    return _check(
        "release_windows_code_signing_required",
        variable.get("value") == "true",
        "REQUIRE_WINDOWS_CODE_SIGNING must equal true",
    )


REQUIRED_REPOSITORY_SETTINGS_CHECKS = (
    "branch_required_status_checks",
    "branch_status_checks_bound_to_actions_app",
    "branch_require_up_to_date",
    "branch_enforce_admins",
    "branch_require_pull_request",
    "branch_minimum_approvals",
    "branch_dismiss_stale_reviews",
    "branch_require_code_owner_reviews",
    "branch_require_last_push_approval",
    "branch_require_signed_commits",
    "branch_conversation_resolution",
    "branch_linear_history",
    "branch_force_pushes_disabled",
    "branch_deletions_disabled",
)
REQUIRED_RELEASE_ENVIRONMENT_CHECKS = (
    "release_required_reviewers",
    "release_independent_reviewers",
    "release_prevent_self_review",
    "release_deployment_refs",
    "release_signing_secrets",
    "release_windows_code_signing_required",
    "production_required_reviewers",
    "production_independent_reviewers",
    "production_prevent_self_review",
    "production_protected_branches",
    "production_secrets",
    "production_variables",
)
REQUIRED_PRODUCTION_SECRETS = frozenset(
    {
        "MARKET_SENTINEL_API_TOKEN",
        "MARKET_SENTINEL_ONCALL_RECEIPT_TOKEN",
        "MARKET_SENTINEL_PUBLIC_BASIC_PASSWORD",
        "MARKET_SENTINEL_PUBLIC_BASIC_USER",
        "POLY_ADDRESS",
        "POLY_API_KEY",
        "POLY_API_SECRET",
        "POLY_PASSPHRASE",
        "POLYMARKET_FUNDER_ADDRESS",
        "POLYMARKET_PRIVATE_KEY",
        "POLYMARKET_RECOVERY_STORE_URL",
        "POLYMARKET_RECOVERY_STORE_TOKEN",
        "POLYMARKET_RECOVERY_ENCRYPTION_KEY_BASE64",
        "POLYMARKET_SIGNATURE_TYPE",
        "RELAYER_API_KEY",
        "RELAYER_API_KEY_ADDRESS",
    }
)
REQUIRED_PRODUCTION_VARIABLES = frozenset(
    {
        "MARKET_SENTINEL_ALERTMANAGER_GID",
        "MARKET_SENTINEL_DEPLOYMENT_PROVIDER",
        "MARKET_SENTINEL_ONCALL_RECEIPT_ORIGIN",
        "MARKET_SENTINEL_PRODUCTION_HOST_ID_SHA256",
        "MARKET_SENTINEL_PRODUCTION_ORIGIN",
        "POLYMARKET_FUNDED_TOKEN_ALLOWLIST",
    }
    )


def _reviewer_state(environment: Mapping[str, Any]) -> list[dict[str, Any]]:
    rules = environment.get("protection_rules")
    rules = rules if isinstance(rules, list) else []
    reviewer_rule = next(
        (rule for rule in rules if isinstance(rule, Mapping) and rule.get("type") == "required_reviewers"),
        {},
    )
    rows = reviewer_rule.get("reviewers") if isinstance(reviewer_rule, Mapping) else []
    reviewers: list[dict[str, Any]] = []
    if isinstance(rows, list):
        for row in rows:
            reviewer = row.get("reviewer") if isinstance(row, Mapping) else None
            reviewer_type = row.get("type") if isinstance(row, Mapping) else None
            reviewer_id = reviewer.get("id") if isinstance(reviewer, Mapping) else None
            if isinstance(reviewer_type, str) and type(reviewer_id) is int and reviewer_id > 0:
                reviewers.append({"type": reviewer_type, "id": reviewer_id})
    return sorted(reviewers, key=lambda item: (item["type"], item["id"]))


def _environment_state(environment: Mapping[str, Any]) -> dict[str, Any]:
    rules = environment.get("protection_rules")
    rules = rules if isinstance(rules, list) else []
    reviewer_rule = next(
        (rule for rule in rules if isinstance(rule, Mapping) and rule.get("type") == "required_reviewers"),
        {},
    )
    branch_policy = environment.get("deployment_branch_policy")
    branch_policy = branch_policy if isinstance(branch_policy, Mapping) else {}
    return {
        "protection_rule_types": sorted(
            str(rule["type"])
            for rule in rules
            if isinstance(rule, Mapping) and isinstance(rule.get("type"), str)
        ),
        "required_reviewers": _reviewer_state(environment),
        "prevent_self_review": reviewer_rule.get("prevent_self_review")
        if isinstance(reviewer_rule, Mapping)
        else None,
        "deployment_branch_policy": {
            "protected_branches": branch_policy.get("protected_branches"),
            "custom_branch_policies": branch_policy.get("custom_branch_policies"),
        },
    }


def _named_inventory(payload: Mapping[str, Any], key: str) -> list[str]:
    rows = payload.get(key)
    if not isinstance(rows, list):
        return []
    return sorted(
        {
            str(row["name"])
            for row in rows
            if isinstance(row, Mapping) and isinstance(row.get("name"), str)
        }
    )


def canonical_governance_state(
    repository: str,
    branch: str,
    documents: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Build a secret-free, policy-relevant snapshot of live GitHub controls."""

    protection = documents["branch_protection"]
    signature_protection = documents["required_signatures"]
    status_checks = protection.get("required_status_checks")
    status_checks = status_checks if isinstance(status_checks, Mapping) else {}
    pull_requests = protection.get("required_pull_request_reviews")
    pull_requests = pull_requests if isinstance(pull_requests, Mapping) else {}

    context_rows = status_checks.get("contexts")
    contexts = sorted(value for value in context_rows if isinstance(value, str)) if isinstance(context_rows, list) else []
    check_rows = status_checks.get("checks")
    checks: list[dict[str, Any]] = []
    if isinstance(check_rows, list):
        for row in check_rows:
            if isinstance(row, Mapping) and isinstance(row.get("context"), str):
                app_id = row.get("app_id")
                checks.append(
                    {"context": row["context"], "app_id": app_id if type(app_id) is int else None}
                )
    checks.sort(key=lambda item: (item["context"], -1 if item["app_id"] is None else item["app_id"]))

    release_environment = documents["release_environment"]
    production_environment = documents["production_environment"]
    release_policies = documents["release_policies"].get("branch_policies")
    release_policy_rows: list[dict[str, str]] = []
    if isinstance(release_policies, list):
        for row in release_policies:
            if (
                isinstance(row, Mapping)
                and isinstance(row.get("type"), str)
                and isinstance(row.get("name"), str)
            ):
                release_policy_rows.append({"type": row["type"], "name": row["name"]})
    release_policy_rows.sort(key=lambda item: (item["type"], item["name"]))

    release_state = _environment_state(release_environment)
    release_state.update(
        {
            "deployment_policies": release_policy_rows,
            "secret_names": _named_inventory(documents["release_secrets"], "secrets"),
        }
    )
    production_state = _environment_state(production_environment)
    production_state.update(
        {
            "secret_names": _named_inventory(documents["production_secrets"], "secrets"),
            "variable_names": _named_inventory(documents["production_variables"], "variables"),
        }
    )

    def enabled(name: str) -> Any:
        value = protection.get(name)
        return value.get("enabled") if isinstance(value, Mapping) else None

    return {
        "schema_version": GOVERNANCE_STATE_SCHEMA_VERSION,
        "repository": repository,
        "branch": branch,
        "branch_protection": {
            "required_status_checks": {
                "strict": status_checks.get("strict"),
                "contexts": contexts,
                "checks": checks,
            },
            "enforce_admins": enabled("enforce_admins"),
            "required_pull_request_reviews": {
                "required_approving_review_count": pull_requests.get("required_approving_review_count"),
                "dismiss_stale_reviews": pull_requests.get("dismiss_stale_reviews"),
                "require_code_owner_reviews": pull_requests.get("require_code_owner_reviews"),
                "require_last_push_approval": pull_requests.get("require_last_push_approval"),
            },
            "required_signatures": signature_protection.get("enabled"),
            "required_conversation_resolution": enabled("required_conversation_resolution"),
            "required_linear_history": enabled("required_linear_history"),
            "allow_force_pushes": enabled("allow_force_pushes"),
            "allow_deletions": enabled("allow_deletions"),
        },
        "release_environment": release_state,
        "production_environment": production_state,
        "repository_variables": {
            "REQUIRE_WINDOWS_CODE_SIGNING": documents["release_variable"].get("value")
        },
    }


def _collect_documents(
    repository: str,
    branch: str,
    token: str,
    timeout: float,
    request_json: JsonRequest,
) -> dict[str, dict[str, Any]]:
    owner, name = repository.split("/", 1)
    prefix = f"/repos/{quote(owner, safe='')}/{quote(name, safe='')}"
    paths = {
        "branch_protection": f"{prefix}/branches/{quote(branch, safe='')}/protection",
        "required_signatures": f"{prefix}/branches/{quote(branch, safe='')}/protection/required_signatures",
        "release_environment": f"{prefix}/environments/release",
        "release_policies": f"{prefix}/environments/release/deployment-branch-policies?per_page=100",
        "release_secrets": f"{prefix}/environments/release/secrets?per_page=100",
        "production_environment": f"{prefix}/environments/production",
        "production_secrets": f"{prefix}/environments/production/secrets?per_page=100",
        "production_variables": f"{prefix}/environments/production/variables?per_page=100",
        "release_variable": f"{prefix}/actions/variables/REQUIRE_WINDOWS_CODE_SIGNING",
    }
    documents = {key: request_json(path, token, timeout) for key, path in paths.items()}
    if not all(isinstance(value, dict) for value in documents.values()):
        raise RuntimeError("GitHub API returned an unexpected document shape")
    for document_name, collection_name in (
        ("release_policies", "branch_policies"),
        ("release_secrets", "secrets"),
        ("production_secrets", "secrets"),
        ("production_variables", "variables"),
    ):
        document = documents[document_name]
        rows = document.get(collection_name)
        total_count = document.get("total_count")
        if (
            not isinstance(rows, list)
            or type(total_count) is not int
            or total_count != len(rows)
            or total_count > 100
        ):
            raise RuntimeError(
                f"GitHub API returned an incomplete {document_name.replace('_', ' ')} inventory"
            )
    return documents


def collect_governance_evidence(
    repository: str,
    branch: str,
    token: str,
    timeout: float,
    request_json: JsonRequest,
) -> tuple[list[dict[str, str]], dict[str, Any], str]:
    """Collect checks plus a canonical state snapshot and exact digest."""

    documents = _collect_documents(repository, branch, token, timeout, request_json)
    protection = documents["branch_protection"]
    signature_protection = documents["required_signatures"]
    environment = documents["release_environment"]
    release_policies = documents["release_policies"]
    secrets = documents["release_secrets"]
    production_environment = documents["production_environment"]
    production_secrets = documents["production_secrets"]
    production_variables = documents["production_variables"]
    variable = documents["release_variable"]

    checks = [
        *check_branch_protection(protection, signature_protection),
        *check_release_environment(
            environment,
            _named_inventory(secrets, "secrets"),
            release_policies,
            branch,
        ),
        check_release_variable(variable),
        *check_production_environment(
            production_environment,
            _named_inventory(production_secrets, "secrets"),
        ),
        check_production_variables(_named_inventory(production_variables, "variables")),
    ]
    state = canonical_governance_state(repository, branch, documents)
    return checks, state, governance_state_sha256(state)


def collect_checks(repository: str, branch: str, token: str, timeout: float, request_json: JsonRequest) -> list[dict[str, str]]:
    checks, _state, _digest = collect_governance_evidence(
        repository,
        branch,
        token,
        timeout,
        request_json,
    )
    return checks


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect read-only GitHub production-governance evidence for MarketSentinel.")
    parser.add_argument("--repository", required=True, help="GitHub repository in OWNER/REPOSITORY form.")
    parser.add_argument("--branch", default="main")
    parser.add_argument("--token-env", default="GITHUB_TOKEN", help="Environment variable holding an administration-read token.")
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--api-url", default=DEFAULT_API_URL, help="GitHub API base URL; intended for GitHub Enterprise Server.")
    args = parser.parse_args()
    repository = args.repository.strip()
    if repository.count("/") != 1 or any(not value.strip() for value in repository.split("/", 1)):
        raise SystemExit("--repository must use OWNER/REPOSITORY form")
    token = os.environ.get(args.token_env, "").strip()
    if not token:
        raise SystemExit(
            f"{args.token_env} must contain a GitHub token with Administration, Environments, "
            "Actions, and Variables read access"
        )
    try:
        checks, governance_state, state_sha256 = collect_governance_evidence(
            repository,
            args.branch.strip() or "main",
            token,
            max(1.0, args.timeout),
            lambda path, request_token, request_timeout: _request_json(path, request_token, request_timeout, args.api_url),
        )
    except RuntimeError as exc:
        checks = [{"name": "repository_governance", "status": "fail", "detail": str(exc)}]
        governance_state = {}
        state_sha256 = ""
    payload = {
        "schema_version": 2,
        "repository": repository,
        "branch": args.branch.strip() or "main",
        "status": "ok" if all(check["status"] == "pass" for check in checks) else "failed",
        "checks": checks,
        "governance_state": governance_state,
        "governance_state_sha256": state_sha256,
    }
    print(json.dumps(payload, sort_keys=True))
    return 0 if payload["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
