from __future__ import annotations

import argparse
import compileall
import importlib
import importlib.metadata
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Mapping, Tuple

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 compatibility.
    import tomli as tomllib


ROOT = Path(__file__).resolve().parent
MIN_PYTHON = (3, 10)
PROJECT_NAME = "market-sentinel"
APP_TITLE = "MarketSentinel"

REQUIRED_IMPORTS = {
    "requests": "requests",
    "urllib3": "urllib3",
    "truststore": "truststore",
    "websocket-client": "websocket",
    "python-dotenv": "dotenv",
    "py-clob-client-v2": "py_clob_client_v2",
    "packaging": "packaging",
    "pytest": "pytest",
    "coverage": "coverage",
    "ruff": "ruff",
    "cryptography": "cryptography",
    "eth-account": "eth_account",
    "eth-abi": "eth_abi",
}

MIN_TOTAL_BRANCH_COVERAGE = 72.0
MIN_BACKEND_BRANCH_COVERAGE = 76.0
# Windows Python 3.11+ exercises the Windows-only release and ACL branches
# that are intentionally skipped on POSIX and Python 3.10 lanes.  Keep the
# stricter floor for that canonical lane while using the repository's previous
# compatibility floor where those branches cannot be collected by design.
COMPATIBILITY_BACKEND_BRANCH_COVERAGE = 74.0
BACKEND_COVERAGE_INCLUDE = "core/*,market_adapters/*,polymarket/*,web_api.py,market_sentinel_cli.py"
RESOURCE_WARNING_POLICY = "error::ResourceWarning"

WORKFLOW_ACTION_PINS = {
    ".github/workflows/ci.yml": {
        "actions/checkout": (7, "3d3c42e5aac5ba805825da76410c181273ba90b1"),
        "actions/setup-python": (7, "5fda3b95a4ea91299a34e894583c3862153e4b97"),
        "actions/setup-node": (7, "820762786026740c76f36085b0efc47a31fe5020"),
        "actions/upload-artifact": (7, "043fb46d1a93c77aae656e7c1c64a875d1fc6a0a"),
        "actions/download-artifact": (8, "3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c"),
        "actions/attest-build-provenance": (4, "4d101475d8b20a2381f78447822ac1eab6504dd8"),
    },
    ".github/workflows/release.yml": {
        "actions/checkout": (7, "3d3c42e5aac5ba805825da76410c181273ba90b1"),
        "actions/setup-python": (7, "5fda3b95a4ea91299a34e894583c3862153e4b97"),
        "actions/setup-node": (7, "820762786026740c76f36085b0efc47a31fe5020"),
        "actions/upload-artifact": (7, "043fb46d1a93c77aae656e7c1c64a875d1fc6a0a"),
        "actions/download-artifact": (8, "3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c"),
        "actions/attest-build-provenance": (4, "4d101475d8b20a2381f78447822ac1eab6504dd8"),
    },
    ".github/workflows/security.yml": {
        "actions/checkout": (7, "3d3c42e5aac5ba805825da76410c181273ba90b1"),
        "actions/setup-python": (7, "5fda3b95a4ea91299a34e894583c3862153e4b97"),
        "actions/dependency-review-action": (5, "a1d282b36b6f3519aa1f3fc636f609c47dddb294"),
        "gitleaks/gitleaks-action": (3, "e0c47f4f8be36e29cdc102c57e68cb5cbf0e8d1e"),
        "raven-actions/actionlint": (2, "3d39aea434753780c3b3d4a1a31c854b4dbf49d7"),
        "github/codeql-action/init": (4, "b96794f015dfd88f77b49b1c93e0fa7110f94c63"),
        "github/codeql-action/analyze": (4, "b96794f015dfd88f77b49b1c93e0fa7110f94c63"),
    },
}
WORKFLOW_ACTION_REF_RE = re.compile(
    r"(?m)^\s*(?:-\s*)?uses:\s*['\"]?([A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+)@([0-9a-f]{40})['\"]?\s*#\s*v(\d+)(?:\.\d+\.\d+)?\s*$"
)
WORKFLOW_ACTION_USE_RE = re.compile(
    r"(?m)^\s*(?:-\s*)?uses:\s*['\"]?([^@'\"\s]+)@([^'\"\s#]+)['\"]?(?:\s*#\s*(.*))?$"
)


IMPLEMENTED_ADAPTER_FIXTURE_TESTS = {
    "polymarket": ("polymarket", "test_polymarket_adapter.py"),
    "betmgm": ("betmgm", "test_betmgm_adapter.py"),
    "blinq": ("blinq", "test_blinq_adapter.py"),
    "context_v2": ("context_v2", "test_additional_official_adapters.py"),
    "smarkets": ("smarkets", "test_additional_official_adapters.py"),
    "thales_market": ("thales_market", "test_additional_official_adapters.py"),
    "metadao": ("metadao", "test_additional_official_adapters.py"),
    "seer": ("seer", "test_additional_official_adapters.py"),
    "hyperliquid": ("hyperliquid", "test_additional_official_adapters.py"),
    "trueo": ("trueo", "test_additional_official_adapters.py"),
    "zeitgeist_sdk_markets": ("zeitgeist_sdk_markets", "test_legacy_web3_adapters.py"),
    "zeitgeist_prediction_pools": ("zeitgeist_prediction_pools", "test_legacy_web3_adapters.py"),
    "ibkr_forecasttrader": ("ibkr_forecasttrader", "test_additional_official_adapters.py"),
    "iowa_electronic_markets": ("iowa_electronic_markets", "test_iowa_electronic_markets.py"),
    "hypermind": ("hypermind", "test_hypermind_adapter.py"),
    "scicast": ("scicast", "test_scicast_adapter.py"),
    "forecastex": ("forecastex", "test_additional_official_adapters.py"),
    "cme_prediction_markets": ("cme_prediction_markets", "test_additional_official_adapters.py"),
    "probable": ("probable", "test_additional_official_adapters.py"),
    "matchbook": ("matchbook", "test_additional_official_adapters.py"),
    "prophet_exchange": ("prophet_exchange", "test_prophet_exchange_adapter.py"),
    "prdt_finance": ("prdt_finance", "test_prdt_finance_adapter.py"),
    "zetarium_world": ("zetarium_world", "test_zetarium_adapter.py"),
    "lamas_finance": ("lamas_finance", "test_lamas_finance_adapter.py"),
    "dflow": ("dflow", "test_additional_official_adapters.py"),
    "drift_bet": ("drift_bet", "test_drift_bet_adapter.py"),
    "frenzy_finance": ("frenzy_finance", "test_frenzy_finance_adapter.py"),
    "space": ("space", "test_space_adapter.py"),
    "hedgehog_markets": ("hedgehog_markets", "test_hedgehog_markets_adapter.py"),
    "kalshi": ("kalshi", "test_kalshi_adapter.py"),
    "predictit": ("predictit", "test_predictit_adapter.py"),
    "crypto_com_predict": ("crypto_com_predict", "test_crypto_com_predict_adapter.py"),
    "fanatics_markets": ("fanatics_markets", "test_fanatics_markets_adapter.py"),
    "fanduel_predicts": ("fanduel_predicts", "test_fanduel_predicts_adapter.py"),
    "nadex": ("nadex", "test_nadex_adapter.py"),
    "coinbase_prediction_markets": ("coinbase_prediction_markets", "test_coinbase_prediction_adapter.py"),
    "robinhood_prediction_markets": ("robinhood_prediction_markets", "test_distribution_alias_adapters.py"),
    "kalshi_via_robinhood": ("robinhood_prediction_markets", "test_distribution_alias_adapters.py"),
    "draftkings_predictions": ("draftkings_predictions", "test_distribution_alias_adapters.py"),
    "manifold": ("manifold", "test_manifold_adapter.py"),
    "metaculus": ("metaculus", "test_metaculus_adapter.py"),
    "good_judgment_open": ("good_judgment_open", "test_good_judgment_open_adapter.py"),
    "limitless_exchange": ("limitless_exchange", "test_limitless_adapter.py"),
    "sx_bet": ("sx_bet", "test_sx_bet_adapter.py"),
    "azuro": ("azuro", "test_azuro_adapter.py"),
    "augur": ("augur", "test_legacy_web3_adapters.py"),
    "reality_eth_markets": ("reality_eth_markets", "test_legacy_web3_adapters.py"),
    "omen": ("omen", "test_legacy_web3_adapters.py"),
    "gnosis_prediction_markets": ("gnosis_prediction_markets", "test_legacy_web3_adapters.py"),
    "zeitgeist": ("zeitgeist", "test_legacy_web3_adapters.py"),
    "myriad_markets": ("myriad_markets", "test_additional_official_adapters.py"),
    "xo_market": ("xo_market", "test_additional_official_adapters.py"),
    "opinion_labs": ("opinion_labs", "test_additional_official_adapters.py"),
    "gemini_titan": ("gemini", "test_additional_official_adapters.py"),
    "predict_fun": ("predict_fun", "test_additional_official_adapters.py"),
    "betfair_exchange": ("betfair_exchange", "test_additional_official_adapters.py"),
    "xmarket": ("xmarket", "test_additional_official_adapters.py"),
}


SECRET_HYGIENE_PATTERNS = {
    "common access token": re.compile(r"\b(?:ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|glpat-[A-Za-z0-9_-]{20,}|sk_live_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9]{20,}|AIza[0-9A-Za-z_-]{20,}|xox[baprs]-[A-Za-z0-9-]{20,}|A(?:KI|SI)A[0-9A-Z]{16})"),
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "credentialed URL": re.compile(r"https?://[^\s/@]+:[^\s/@]+@"),
    "private network address": re.compile(r"\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|169\.254\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[0-1])\.\d{1,3}\.\d{1,3})\b"),
    "hardcoded authorization or cookie": re.compile(r"(?i)(?:['\"]authorization['\"]|['\"]cookie['\"])\s*:\s*['\"][^'\"{}]{12,}['\"]"),
}
SECRET_HYGIENE_ALLOW_MARKER = "secret-scan: allow"
SECRET_HYGIENE_TEXT_SUFFIXES = frozenset(
    {
        ".bat",
        ".cjs",
        ".cmd",
        ".conf",
        ".css",
        ".example",
        ".html",
        ".ini",
        ".js",
        ".json",
        ".lock",
        ".md",
        ".mjs",
        ".pem",
        ".ps1",
        ".py",
        ".service",
        ".sh",
        ".svg",
        ".timer",
        ".toml",
        ".ts",
        ".tsx",
        ".txt",
        ".key",
        ".xml",
        ".yaml",
        ".yml",
    }
)
SECRET_HYGIENE_TEXT_NAMES = frozenset(
    {".env", ".gitignore", ".netrc", ".npmrc", ".pypirc", "Dockerfile", "LICENSE"}
)
SECRET_HYGIENE_FORBIDDEN_SECRET_SUFFIXES = frozenset(
    {".jks", ".kdbx", ".keystore", ".p12", ".pfx"}
)
SECRET_HYGIENE_FORBIDDEN_SECRET_NAMES = frozenset({"id_dsa", "id_ed25519", "id_rsa"})
SECRET_HYGIENE_MAX_FILE_BYTES = 8 * 1024 * 1024
SECRET_HYGIENE_EXCLUDED_DIRECTORIES = frozenset(
    {
        ".cache",
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "env",
        "node_modules",
        "venv",
    }
)
SECRET_HYGIENE_SELF_TEST_PATHS = frozenset(
    {
        Path("verify.py"),
        Path("tests/test_secret_hygiene.py"),
    }
)


def check_python_version() -> None:
    current = sys.version_info[:3]
    if current < MIN_PYTHON:
        raise SystemExit(
            "Unsupported Python "
            f"{current[0]}.{current[1]}.{current[2]}; expected >=3.10."
        )
    print(f"[ok] Python {current[0]}.{current[1]}.{current[2]}")


def check_dependency_imports() -> None:
    failures: list[str] = []
    for dist_name, module_name in REQUIRED_IMPORTS.items():
        try:
            version = importlib.metadata.version(dist_name)
            importlib.import_module(module_name)
        except Exception as exc:
            failures.append(f"{dist_name}: {exc}")
        else:
            print(f"[ok] {dist_name} {version}")
    if failures:
        raise SystemExit("Dependency import check failed:\n" + "\n".join(failures))


def run_pip_check() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "pip", "check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        output = (result.stdout + result.stderr).strip()
        raise SystemExit("pip check failed:\n" + output)
    print("[ok] pip check")


def npm_command() -> str:
    executable = "npm.cmd" if sys.platform == "win32" else "npm"
    resolved = shutil.which(executable)
    if not resolved:
        raise SystemExit(f"{executable} was not found on PATH; install Node.js/npm before running frontend verification.")
    return resolved


def run_compile_check() -> None:
    checks = [
        compileall.compile_file(str(ROOT / "app.py"), quiet=1),
        compileall.compile_file(str(ROOT / "market_sentinel_cli.py"), quiet=1),
        compileall.compile_file(str(ROOT / "verify.py"), quiet=1),
        compileall.compile_file(str(ROOT / "web_api.py"), quiet=1),
        compileall.compile_dir(str(ROOT / "core"), quiet=1),
        compileall.compile_dir(str(ROOT / "market_adapters"), quiet=1),
        compileall.compile_dir(str(ROOT / "polymarket"), quiet=1),
        compileall.compile_dir(str(ROOT / "scripts"), quiet=1),
        compileall.compile_dir(str(ROOT / "tests"), quiet=1),
    ]
    if not all(checks):
        raise SystemExit("Python compile check failed.")
    print("[ok] compileall")


def run_static_analysis() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "."],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        output = (result.stdout + result.stderr).strip()
        raise SystemExit("Ruff static analysis failed:\n" + output)
    print("[ok] Ruff static analysis")


def run_adapter_catalog_check() -> None:
    from market_adapters import (
        MARKET_CATALOG,
        MARKET_IDS,
        account_surface_issues,
        build_default_registry,
        capability_contract_issues,
        support_matrix_entry,
        VERIFIED_BLOCKERS,
    )
    import market_sentinel_cli

    if len(MARKET_IDS) != len(set(MARKET_IDS)):
        raise SystemExit("Adapter catalog contains duplicate market ids.")
    if "polymarket" not in MARKET_IDS:
        raise SystemExit("Adapter catalog must include polymarket.")
    registry = build_default_registry()
    if set(registry.list_market_ids()) != set(MARKET_IDS):
        raise SystemExit("Default adapter registry does not match the market catalog.")
    missing_adapters = [market_id for market_id in MARKET_IDS if not registry.has_adapter(market_id)]
    if missing_adapters:
        raise SystemExit("Default adapter registry is missing adapters: " + ", ".join(missing_adapters))
    if not registry.has_adapter("polymarket"):
        raise SystemExit("Default adapter registry must include the Polymarket adapter.")
    capability_issues = {
        market_id: capability_contract_issues(registry.create(market_id))
        for market_id in MARKET_IDS
    }
    failures = [
        f"{market_id}: {'; '.join(issues)}"
        for market_id, issues in capability_issues.items()
        if issues
    ]
    if failures:
        raise SystemExit("Advertised adapter capabilities are not implementation-backed: " + " | ".join(failures))
    account_failures = [
        f"{market_id}: {'; '.join(issues)}"
        for market_id in MARKET_IDS
        if (
            issues := account_surface_issues(
                registry.create(market_id),
                cli_account_operations=frozenset(market_sentinel_cli.MARKET_ACCOUNT_OPERATIONS),
                cli_order_operations=frozenset(market_sentinel_cli.MARKET_ORDER_MANAGEMENT_OPERATIONS),
            )
        )
    ]
    if account_failures:
        raise SystemExit("Authenticated operation surfaces are incomplete: " + " | ".join(account_failures))
    support_failures = []
    for market_id in MARKET_IDS:
        metadata = registry.get_metadata(market_id)
        adapter = registry.create(market_id)
        row = support_matrix_entry(metadata, adapter, blocker=VERIFIED_BLOCKERS.get(market_id))
        if row["implementation_status"] not in {"implemented", "verified_blocked"}:
            support_failures.append(f"{market_id}: invalid implementation status")
        if not row["audit"]["ok"]:
            support_failures.append(f"{market_id}: support matrix audit failed")
        if market_id in VERIFIED_BLOCKERS:
            if row["implementation_status"] != "verified_blocked" or any(
                item["status"] != "blocked" for item in row["operations"].values()
            ):
                support_failures.append(f"{market_id}: verified blocker is not represented as blocked")
        elif any(item["status"] == "blocked" for item in row["operations"].values()):
            support_failures.append(f"{market_id}: non-blocked adapter has a blocked operation")
    if support_failures:
        raise SystemExit("Support matrix is incomplete: " + " | ".join(support_failures))
    print(f"[ok] adapter catalog ({len(MARKET_CATALOG)} markets)")


def run_support_matrix_snapshot_check() -> None:
    """Keep the human catalog snapshot synchronized with the canonical matrix."""

    from market_adapters import (
        MARKET_CATALOG,
        VERIFIED_BLOCKERS,
        build_default_registry,
        support_matrix_entry,
        support_matrix_summary,
    )

    registry = build_default_registry()
    rows = [
        support_matrix_entry(
            registry.get_metadata(market_id),
            registry.create(market_id),
            blocker=VERIFIED_BLOCKERS.get(market_id),
        )
        for market_id in (market.market_id for market in MARKET_CATALOG)
    ]
    summary = support_matrix_summary(rows)
    goal_text = (ROOT / "GOAL.md").read_text(encoding="utf-8")

    implementation = summary["implementation"]
    expected_header_lines = (
        f"- Total markets: {summary['total_markets']}",
        f"- Implemented adapters: {implementation.get('implemented', 0)}",
        f"- Verified-blocked adapters: {implementation.get('verified_blocked', 0)}",
    )
    missing = [line for line in expected_header_lines if line not in goal_text]
    if missing:
        raise SystemExit("GOAL.md catalog snapshot is stale: " + "; ".join(missing))

    labels = {
        "market_discovery": "Market/event discovery supported",
        "alerts": "Alerts supported",
        "price_reading": "Read-only price data supported",
        "orderbook_reading": "Orderbook reading supported",
        "trade_history": "Trade history supported",
        "candle_history": "Candle history supported",
        "paper_trading": "Paper trading supported",
        "live_trading": "Live trading supported",
        "copy_trading": "Copy trading supported",
    }
    expected_operation_lines: list[str] = []
    for operation, label in labels.items():
        counts = summary["operations"][operation]
        parts = []
        if counts.get("supported"):
            parts.append(f"{counts['supported']} yes")
        if counts.get("guarded"):
            parts.append(f"{counts['guarded']} guarded/off by default")
        if counts.get("unsupported"):
            parts.append(f"{counts['unsupported']} unsupported")
        if counts.get("blocked"):
            parts.append(f"{counts['blocked']} blocked")
        expected_operation_lines.append(f"- {label}: {', '.join(parts)}")
    missing = [line for line in expected_operation_lines if line not in goal_text]
    if missing:
        raise SystemExit("GOAL.md capability snapshot is stale: " + "; ".join(missing))
    print("[ok] support matrix snapshot")


def run_project_metadata_check() -> None:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    if data.get("build-system", {}).get("requires") != ["setuptools==84.0.0"]:
        raise SystemExit(
            "pyproject.toml build-system must pin setuptools==84.0.0 so isolated resolution cannot drift."
        )
    name = data.get("project", {}).get("name")
    if name != PROJECT_NAME:
        raise SystemExit(f"pyproject.toml project name must be {PROJECT_NAME!r}; got {name!r}.")
    if "_" in name:
        raise SystemExit("pyproject.toml project name must use dashes, not underscores.")
    if data.get("project", {}).get("requires-python") != ">=3.10":
        raise SystemExit("pyproject.toml requires-python must allow Python >=3.10 without an artificial upper cap.")
    if data.get("project", {}).get("license") != "0BSD":
        raise SystemExit("pyproject.toml project.license must use the SPDX expression 0BSD.")
    if data.get("project", {}).get("license-files") != ["LICENSE"]:
        raise SystemExit("pyproject.toml project.license-files must include LICENSE.")
    license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    required_license_fragments = (
        "BSD Zero Clause License",
        "Permission to use, copy, modify, and/or distribute this software",
        'THE SOFTWARE IS PROVIDED "AS IS"',
    )
    missing_license_fragments = [
        fragment for fragment in required_license_fragments if fragment not in license_text
    ]
    if missing_license_fragments:
        raise SystemExit("LICENSE must contain the BSD Zero Clause License text only.")
    classifiers = set(data.get("project", {}).get("classifiers", []))
    for classifier in ("Programming Language :: Python :: 3.15", "Programming Language :: Python :: 3.16"):
        if classifier not in classifiers:
            raise SystemExit(f"pyproject.toml is missing classifier: {classifier}")

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    app = (ROOT / "app.py").read_text(encoding="utf-8")
    if f"# {APP_TITLE}" not in readme:
        raise SystemExit("README.md title must use the MarketSentinel app title.")
    if f'APP_TITLE = "{APP_TITLE}"' not in app:
        raise SystemExit("app.py window title must use the MarketSentinel app title.")
    if f'APP_ID = "{PROJECT_NAME}"' not in app:
        raise SystemExit("app.py AppUserModelID must use the dashed project id.")
    if 'APP_USER_AGENT = f"{APP_ID}/1.0"' not in app or 'headers={"User-Agent": APP_USER_AGENT}' not in app:
        raise SystemExit("app.py User-Agent must use the dashed project name.")
    forbidden = (
        "prediction-market-alert-and-copy-trade-gui",
        "polymarket-alert-and-copy-trade-gui",
        "polymarket-sentinel-gui",
        "Polymarket Sentinel GUI",
        "PolymarketSentinelGUI",
    )
    checked_files = (
        ROOT / "README.md",
        ROOT / "app.py",
        ROOT / "pyproject.toml",
        ROOT / "GOAL.md",
    )
    for path in checked_files:
        text = path.read_text(encoding="utf-8")
        for value in forbidden:
            if value in text:
                raise SystemExit(f"Old project branding {value!r} remains in {path.relative_to(ROOT)}.")
    print("[ok] project metadata")


def validate_release_version_history(
    project_version: str,
    release_tags: list[str],
    head_tags: list[str],
) -> str:
    from packaging.version import InvalidVersion, Version

    try:
        candidate = Version(str(project_version or "").strip())
    except InvalidVersion as exc:
        raise SystemExit(f"pyproject.toml project.version is not a valid release version: {project_version!r}.") from exc
    if not str(project_version or "").strip() or candidate.local is not None:
        raise SystemExit("pyproject.toml project.version must be a public release version without a local suffix.")

    parsed_tags: list[tuple[Version, str]] = []
    for tag in release_tags:
        clean_tag = str(tag or "").strip()
        if not clean_tag.startswith("v"):
            continue
        try:
            parsed_tags.append((Version(clean_tag[1:]), clean_tag))
        except InvalidVersion:
            continue

    expected_tag = f"v{project_version}"
    normalized_head_tags = {str(tag or "").strip() for tag in head_tags}
    all_tag_names = {tag for _, tag in parsed_tags}
    if expected_tag in all_tag_names:
        if expected_tag not in normalized_head_tags:
            raise SystemExit(
                f"pyproject.toml project.version {project_version} reuses existing tag {expected_tag} "
                "on an older commit. Bump the project version before release."
            )
        return expected_tag

    if parsed_tags:
        latest_version, latest_tag = max(parsed_tags, key=lambda item: item[0])
        if candidate <= latest_version:
            raise SystemExit(
                f"pyproject.toml project.version {project_version} must be newer than latest release "
                f"{latest_tag} while HEAD is untagged."
            )
    return expected_tag


def run_release_version_check() -> None:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project_version = str(data.get("project", {}).get("version") or "").strip()
    git_metadata = ROOT / ".git"
    if not git_metadata.exists():
        expected_tag = validate_release_version_history(project_version, [], [])
        print(f"[ok] release version {project_version} (expected tag {expected_tag}; git history unavailable)")
        return

    def git_lines(*args: str) -> list[str]:
        result = subprocess.run(
            ["git", *args],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise SystemExit(f"Could not inspect release tags with git: {detail or 'unknown git error'}")
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]

    shallow_state = git_lines("rev-parse", "--is-shallow-repository")
    if shallow_state != ["false"]:
        raise SystemExit(
            "Release version verification requires complete git tag history. "
            "Fetch full history and tags before running verify.py."
        )
    release_tags = git_lines("tag", "--list", "v*")
    head_tags = git_lines("tag", "--points-at", "HEAD")
    expected_tag = validate_release_version_history(project_version, release_tags, head_tags)
    state = "tagged HEAD" if expected_tag in head_tags else "next unreleased version"
    print(f"[ok] release version {project_version} ({state}; expected tag {expected_tag})")


def run_config_example_check() -> None:
    import json

    from core.models import AppConfig
    from market_adapters import MARKET_IDS

    path = ROOT / "data" / "config.example.json"
    text = path.read_text(encoding="utf-8")
    if "TBD" in text:
        raise SystemExit("data/config.example.json must not contain TBD placeholders.")
    data = json.loads(text)
    if set(data.get("markets", {})) != set(MARKET_IDS):
        raise SystemExit("data/config.example.json does not match the market catalog.")
    cfg = AppConfig.from_dict(data)
    if cfg.selected_market_id != "polymarket":
        raise SystemExit("data/config.example.json must default to selected_market_id=polymarket.")
    if cfg.copytrading.enabled or cfg.copytrading.live:
        raise SystemExit("data/config.example.json must keep copy trading disabled by default.")
    print("[ok] config example")


def run_readme_matrix_check() -> None:
    from market_adapters import MARKET_IDS

    path = ROOT / "README.md"
    text = path.read_text(encoding="utf-8")
    required_headers = (
        "Market",
        "Adapter",
        "Alerts",
        "Read-only data",
        "Paper trading",
        "Live trading",
        "Copy trading",
        "API required",
        "Credentials required",
        "Region/KYC limitation",
    )
    if "## Market Capability Matrix" not in text:
        raise SystemExit("README.md is missing the market capability matrix.")
    if "TBD" in text:
        raise SystemExit("README.md capability matrix must not contain TBD placeholders.")
    missing_headers = [header for header in required_headers if header not in text]
    if missing_headers:
        raise SystemExit("README.md capability matrix is missing headers: " + ", ".join(missing_headers))
    missing_markets = [market_id for market_id in MARKET_IDS if f"`{market_id}`" not in text]
    if missing_markets:
        raise SystemExit("README.md capability matrix is missing markets: " + ", ".join(missing_markets))
    print("[ok] README capability matrix")


def run_blockers_doc_check() -> None:
    from market_adapters import MARKET_IDS

    path = ROOT / "docs" / "BLOCKERS.md"
    text = path.read_text(encoding="utf-8")
    if "TBD" in text:
        raise SystemExit("docs/BLOCKERS.md must not contain TBD placeholders.")
    required_sections = (
        "# Blockers",
        "## Summary",
        "## Market Blockers",
        "## Implementation Rules For Clearing A Blocker",
    )
    missing_sections = [section for section in required_sections if section not in text]
    if missing_sections:
        raise SystemExit("docs/BLOCKERS.md is missing sections: " + ", ".join(missing_sections))
    missing_markets = [market_id for market_id in MARKET_IDS if f"`{market_id}`" not in text]
    if missing_markets:
        raise SystemExit("docs/BLOCKERS.md is missing markets: " + ", ".join(missing_markets))
    print("[ok] blockers documentation")


def run_goal_completion_audit_check() -> None:
    path = ROOT / "docs" / "GOAL_COMPLETION_AUDIT.md"
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    required_fragments = (
        "# Goal Completion Audit",
        "## Local Requirement Evidence",
        "## Polymarket Evidence Tiers",
        "### Observed Public-Live Evidence",
        "## Open External Evidence Gates",
        "python verify.py --frontend-build --frontend-live-smoke",
        "does not promote a tier based only on local fixtures",
    )
    missing = [fragment for fragment in required_fragments if fragment not in text]
    if missing:
        raise SystemExit("Goal completion audit is missing: " + ", ".join(missing))
    print("[ok] goal completion audit")


def _secret_hygiene_source_paths() -> list[Path]:
    try:
        tracked_and_unignored = subprocess.run(
            [
                "git",
                "-C",
                str(ROOT),
                "ls-files",
                "-z",
                "--cached",
                "--others",
                "--exclude-standard",
            ],
            capture_output=True,
            check=False,
        )
    except OSError:
        tracked_and_unignored = None
    if tracked_and_unignored is not None and tracked_and_unignored.returncode == 0:
        candidates = [
            ROOT / os.fsdecode(raw_path)
            for raw_path in tracked_and_unignored.stdout.split(b"\0")
            if raw_path
        ]
        return sorted(
            path
            for path in candidates
            if path.relative_to(ROOT) not in SECRET_HYGIENE_SELF_TEST_PATHS
            and (
                path.suffix.lower() in SECRET_HYGIENE_TEXT_SUFFIXES
                or path.name in SECRET_HYGIENE_TEXT_NAMES
                or path.suffix.lower() in SECRET_HYGIENE_FORBIDDEN_SECRET_SUFFIXES
                or path.name in SECRET_HYGIENE_FORBIDDEN_SECRET_NAMES
            )
        )

    # Source archives and unusual developer environments may not include Git.
    # Fall back to a pruned filesystem walk while preserving the same file rules.
    paths: list[Path] = []
    for current_root, directory_names, file_names in os.walk(ROOT, topdown=True):
        current = Path(current_root)
        directory_names[:] = [
            name
            for name in directory_names
            if name not in SECRET_HYGIENE_EXCLUDED_DIRECTORIES
            and not name.startswith("pytest-cache-files-")
            and not name.endswith(".egg-info")
            and not (current / name / "pyvenv.cfg").is_file()
        ]
        for file_name in file_names:
            path = current / file_name
            relative = path.relative_to(ROOT)
            if relative in SECRET_HYGIENE_SELF_TEST_PATHS:
                continue
            if path.suffix.lower() not in SECRET_HYGIENE_TEXT_SUFFIXES and path.name not in SECRET_HYGIENE_TEXT_NAMES:
                if (
                    path.suffix.lower() not in SECRET_HYGIENE_FORBIDDEN_SECRET_SUFFIXES
                    and path.name not in SECRET_HYGIENE_FORBIDDEN_SECRET_NAMES
                ):
                    continue
            paths.append(path)
    return sorted(paths)


def _secret_hygiene_violations(paths: list[Path]) -> list[str]:
    violations: list[str] = []
    for path in paths:
        try:
            label = path.relative_to(ROOT).as_posix()
        except ValueError:
            label = str(path)
        if (
            path.suffix.lower() in SECRET_HYGIENE_FORBIDDEN_SECRET_SUFFIXES
            or path.name in SECRET_HYGIENE_FORBIDDEN_SECRET_NAMES
        ):
            violations.append(f"{label}: private credential container or key filename is forbidden")
            continue
        if path.is_symlink():
            violations.append(f"{label}: symbolic-link source is not allowed")
            continue
        try:
            with path.open("rb") as stream:
                raw = stream.read(SECRET_HYGIENE_MAX_FILE_BYTES + 1)
        except OSError as exc:
            violations.append(f"{label}: unreadable source ({type(exc).__name__})")
            continue
        if len(raw) > SECRET_HYGIENE_MAX_FILE_BYTES:
            violations.append(
                f"{label}: text source exceeds {SECRET_HYGIENE_MAX_FILE_BYTES} bytes"
            )
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            violations.append(f"{label}: text source is not valid UTF-8")
            continue
        for line_number, line in enumerate(text.splitlines(), start=1):
            if SECRET_HYGIENE_ALLOW_MARKER in line:
                continue
            for label_name, pattern in SECRET_HYGIENE_PATTERNS.items():
                # Tests deliberately exercise private-address and authorization
                # rejection. Their synthetic values are not infrastructure
                # secrets, while token/key/credentialed-URL rules still apply.
                if label.startswith("tests/") and label_name in {
                    "private network address",
                    "hardcoded authorization or cookie",
                }:
                    continue
                if pattern.search(line):
                    violations.append(f"{label}:{line_number}: {label_name}")
    return violations


def run_secret_hygiene_check() -> None:
    violations = _secret_hygiene_violations(_secret_hygiene_source_paths())
    if violations:
        raise SystemExit("Secret hygiene check failed: " + "; ".join(violations))
    print("[ok] secret hygiene")


def run_fixture_check() -> None:
    fixture_root = ROOT / "tests" / "fixtures"
    fixture_paths = sorted(fixture_root.glob("**/*.json"))
    if not fixture_paths:
        raise SystemExit("No offline JSON fixtures found under tests/fixtures.")

    for path in fixture_paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise SystemExit(f"Invalid fixture JSON at {path.relative_to(ROOT)}: {exc}") from exc
        # Some official endpoints (notably IBKR Client Portal) return a
        # top-level array rather than an object.  Accept either response shape,
        # but reject scalar/null payloads and empty fixtures.
        if not isinstance(data, (dict, list)) or not data:
            raise SystemExit(f"Fixture must contain a non-empty JSON object or array: {path.relative_to(ROOT)}")

    required = {
        fixture_root / "polymarket" / "market.json",
        fixture_root / "polymarket" / "event.json",
        fixture_root / "polymarket" / "orderbook.json",
        fixture_root / "polymarket" / "activity_buy.json",
        fixture_root / "polymarket" / "clob_trades.json",
        fixture_root / "polymarket" / "price_history.json",
        fixture_root / "kalshi" / "markets.json",
        fixture_root / "kalshi" / "orderbook.json",
        fixture_root / "kalshi" / "trades.json",
        fixture_root / "kalshi" / "candlesticks.json",
        fixture_root / "manifold" / "search_markets.json",
        fixture_root / "manifold" / "market_binary.json",
        fixture_root / "manifold" / "market_multi.json",
        fixture_root / "manifold" / "prob_binary.json",
        fixture_root / "manifold" / "prob_multi.json",
        fixture_root / "manifold" / "bets_trades.json",
        fixture_root / "metaculus" / "posts.json",
        fixture_root / "metaculus" / "post_binary.json",
        fixture_root / "metaculus" / "post_multiple.json",
        fixture_root / "metaculus" / "post_numeric.json",
        fixture_root / "good_judgment_open" / "questions.json",
        fixture_root / "good_judgment_open" / "prediction_sets.json",
        fixture_root / "good_judgment_open" / "oauth_token.json",
        fixture_root / "good_judgment_open" / "prediction_submission.json",
        fixture_root / "predictit" / "all.json",
        fixture_root / "predictit" / "market.json",
        fixture_root / "limitless_exchange" / "active.json",
        fixture_root / "limitless_exchange" / "market.json",
        fixture_root / "limitless_exchange" / "orderbook.json",
        fixture_root / "limitless_exchange" / "historical_price.json",
        fixture_root / "limitless_exchange" / "events.json",
        fixture_root / "sx_bet" / "active_markets.json",
        fixture_root / "sx_bet" / "market_find.json",
        fixture_root / "sx_bet" / "orders.json",
        fixture_root / "sx_bet" / "best_odds.json",
        fixture_root / "azuro" / "games_by_filters.json",
        fixture_root / "azuro" / "games_by_ids.json",
        fixture_root / "azuro" / "conditions_by_game_ids.json",
        fixture_root / "azuro" / "order_response.json",
        fixture_root / "augur" / "markets.json",
        fixture_root / "augur" / "market.json",
        fixture_root / "omen" / "fpmms.json",
        fixture_root / "omen" / "fpmm.json",
        fixture_root / "zeitgeist" / "markets.json",
        fixture_root / "zeitgeist" / "market.json",
        fixture_root / "zeitgeist" / "assets.json",
        fixture_root / "gemini" / "events.json",
        fixture_root / "gemini" / "event.json",
        fixture_root / "gemini" / "orderbook.json",
        fixture_root / "gemini" / "order_response.json",
        fixture_root / "myriad_markets" / "questions.json",
        fixture_root / "myriad_markets" / "question.json",
        fixture_root / "myriad_markets" / "market.json",
        fixture_root / "myriad_markets" / "orderbook.json",
        fixture_root / "myriad_markets" / "trades.json",
        fixture_root / "myriad_markets" / "order_response.json",
        fixture_root / "opinion_labs" / "markets.json",
        fixture_root / "opinion_labs" / "market.json",
        fixture_root / "opinion_labs" / "price.json",
        fixture_root / "opinion_labs" / "orderbook.json",
        fixture_root / "opinion_labs" / "price_history.json",
        fixture_root / "probable" / "activity.json",
        fixture_root / "probable" / "prices_history.json",
        fixture_root / "ibkr_forecasttrader" / "history.json",
        fixture_root / "iowa_electronic_markets" / "market.json",
        fixture_root / "iowa_electronic_markets" / "powell_price_data.txt",
        fixture_root / "hypermind" / "export_metadata.json",
        fixture_root / "hypermind" / "prices.csv",
        fixture_root / "hypermind" / "outcomes.txt",
        fixture_root / "scicast" / "questions.json",
        fixture_root / "scicast" / "question_history.json",
        fixture_root / "scicast" / "trade_history.json",
        fixture_root / "predict_fun" / "markets.json",
        fixture_root / "predict_fun" / "market.json",
        fixture_root / "predict_fun" / "orderbook.json",
        fixture_root / "predict_fun" / "order_response.json",
        fixture_root / "xo_market" / "markets.json",
        fixture_root / "xo_market" / "market.json",
        fixture_root / "xo_market" / "orderbook.json",
        fixture_root / "xo_market" / "order_response.json",
        fixture_root / "betfair_exchange" / "market_catalogue.json",
        fixture_root / "betfair_exchange" / "market_book.json",
        fixture_root / "betfair_exchange" / "place_order_response.json",
        fixture_root / "hedgehog_markets" / "program_accounts.json",
        fixture_root / "frenzy_finance" / "rpc_responses.json",
        fixture_root / "prdt_finance" / "rpc_responses.json",
        fixture_root / "zetarium_world" / "rpc_responses.json",
        fixture_root / "lamas_finance" / "rpc_responses.json",
        fixture_root / "nadex" / "events.json",
        fixture_root / "nadex" / "contracts.json",
        fixture_root / "nadex" / "price.json",
        fixture_root / "hyperliquid" / "candles.json",
        fixture_root / "blinq" / "market.json",
        fixture_root / "blinq" / "event.json",
        fixture_root / "blinq" / "orderbook.json",
        fixture_root / "betmgm" / "fixtures.json",
    }
    missing = [str(path.relative_to(ROOT)) for path in sorted(required) if not path.exists()]
    if missing:
        raise SystemExit("Missing required offline fixtures: " + ", ".join(missing))
    print(f"[ok] offline fixtures ({len(fixture_paths)} files)")


def run_adapter_fixture_coverage_check() -> None:
    from market_adapters import MARKET_IDS, VERIFIED_BLOCKERS

    implemented = set(MARKET_IDS) - set(VERIFIED_BLOCKERS)
    mapped = set(IMPLEMENTED_ADAPTER_FIXTURE_TESTS)
    if implemented != mapped:
        missing = sorted(implemented - mapped)
        unexpected = sorted(mapped - implemented)
        details = []
        if missing:
            details.append("missing mappings: " + ", ".join(missing))
        if unexpected:
            details.append("unexpected mappings: " + ", ".join(unexpected))
        raise SystemExit("Implemented adapter fixture coverage is incomplete: " + "; ".join(details))

    fixture_root = ROOT / "tests" / "fixtures"
    missing_evidence = []
    for market_id, (fixture_dir, test_name) in IMPLEMENTED_ADAPTER_FIXTURE_TESTS.items():
        directory = fixture_root / fixture_dir
        test_path = ROOT / "tests" / test_name
        if not any(directory.glob("*.json")):
            missing_evidence.append(f"{market_id}: fixture directory {directory.relative_to(ROOT)}")
        if not test_path.is_file():
            missing_evidence.append(f"{market_id}: test file tests/{test_name}")
        elif fixture_dir not in test_path.read_text(encoding="utf-8"):
            missing_evidence.append(f"{market_id}: tests/{test_name} does not reference fixture directory {fixture_dir}")
    if missing_evidence:
        raise SystemExit("Implemented adapter fixture evidence is missing: " + "; ".join(missing_evidence))
    print(f"[ok] implemented adapter fixture coverage ({len(implemented)} markets)")


def run_polymarket_live_report_schema_check() -> None:
    from polymarket.live_report_schema import validate_live_validation_report

    fixture_root = ROOT / "tests" / "fixtures" / "polymarket" / "live_reports"
    valid_fixtures = {
        "valid_credentialed_read.json",
        "valid_funded_audit.json",
        "valid_dry_run.json",
        "valid_runbook.json",
        "valid_browser_smoke.json",
    }
    invalid_fixtures = {"invalid_missing_mode.json", "invalid_bad_stage_gates.json"}
    missing = sorted(name for name in valid_fixtures | invalid_fixtures if not (fixture_root / name).exists())
    if missing:
        raise SystemExit("Missing Polymarket live report schema fixtures: " + ", ".join(missing))

    failures: list[str] = []
    for name in sorted(valid_fixtures):
        report = json.loads((fixture_root / name).read_text(encoding="utf-8"))
        validation = validate_live_validation_report(report)
        if not validation["ok"]:
            failures.append(f"{name} should validate but failed: {validation['errors']}")
    for name in sorted(invalid_fixtures):
        report = json.loads((fixture_root / name).read_text(encoding="utf-8"))
        validation = validate_live_validation_report(report)
        if validation["ok"]:
            failures.append(f"{name} should fail schema validation.")
    if failures:
        raise SystemExit("Polymarket live report schema fixture check failed:\n" + "\n".join(failures))
    print("[ok] Polymarket live report schema fixtures")


def run_polymarket_live_report_replay_check() -> None:
    from polymarket.live_report_replay import replay_live_validation_report_paths

    fixture_root = ROOT / "tests" / "fixtures" / "polymarket" / "live_reports"
    script = ROOT / "scripts" / "replay_polymarket_live_reports.py"
    if not script.exists():
        raise SystemExit("Polymarket live report replay script is missing.")
    valid = [
        fixture_root / "valid_credentialed_read.json",
        fixture_root / "valid_dry_run.json",
    ]
    invalid = [fixture_root / "invalid_missing_mode.json"]
    dry_run = replay_live_validation_report_paths(valid + invalid)
    if dry_run.get("ok"):
        raise SystemExit("Polymarket live report replay dry-run should fail when invalid fixtures are included.")
    if dry_run.get("counts", {}).get("valid") != 2 or dry_run.get("counts", {}).get("invalid") != 1:
        raise SystemExit("Polymarket live report replay dry-run returned unexpected fixture counts.")
    if dry_run.get("counts", {}).get("imported") != 0:
        raise SystemExit("Polymarket live report replay dry-run must not import reports.")

    with tempfile.TemporaryDirectory() as tmp:
        store_path = Path(tmp) / "reports.json"
        imported = replay_live_validation_report_paths(valid, import_reports=True, store_path=store_path)
        if not imported.get("ok"):
            raise SystemExit("Polymarket live report replay import failed for valid fixtures.")
        if imported.get("counts", {}).get("imported") != len(valid):
            raise SystemExit("Polymarket live report replay import did not store all valid fixtures.")
        if not store_path.exists():
            raise SystemExit("Polymarket live report replay import did not create the report store.")
        duplicate_store_path = Path(tmp) / "duplicate-reports.json"
        duplicate_import = replay_live_validation_report_paths(
            [valid[0], valid[0]],
            import_reports=True,
            store_path=duplicate_store_path,
        )
        duplicate_counts = duplicate_import.get("counts", {})
        if duplicate_counts.get("imported") != 1 or duplicate_counts.get("skipped_duplicates") != 1:
            raise SystemExit("Polymarket live report replay did not skip duplicate imports by default.")
        allowed_store_path = Path(tmp) / "allowed-duplicate-reports.json"
        allowed_duplicate_import = replay_live_validation_report_paths(
            [valid[0], valid[0]],
            import_reports=True,
            store_path=allowed_store_path,
            allow_duplicate=True,
        )
        allowed_counts = allowed_duplicate_import.get("counts", {})
        if allowed_counts.get("imported") != 2 or allowed_counts.get("skipped_duplicates") != 0:
            raise SystemExit("Polymarket live report replay did not allow explicit duplicate imports.")
    print("[ok] Polymarket live report replay")


def run_polymarket_live_report_review_bundle_check() -> None:
    from polymarket.live_reports import (
        live_validation_report_review_bundle,
        live_validation_report_review_markdown,
        store_live_validation_report,
    )

    fixture_root = ROOT / "tests" / "fixtures" / "polymarket" / "live_reports"
    report = json.loads((fixture_root / "valid_dry_run.json").read_text(encoding="utf-8"))
    report["api_key"] = "verify-review-secret"
    report["operator_commands"] = {
        "safe_live_probe": "python scripts/verify_polymarket_live.py --timeout 8",
        "credentialed_read": "python scripts/verify_polymarket_live.py --require-authenticated-read-ok --report-file live-report.json",
    }
    with tempfile.TemporaryDirectory() as tmp:
        store_path = Path(tmp) / "reports.json"
        stored = store_live_validation_report(
            report,
            source="verify",
            label="review bundle",
            path=store_path,
            source_file="valid_dry_run.json",
        )
        store_live_validation_report(
            report,
            source="verify",
            label="review bundle duplicate",
            path=store_path,
            source_file="valid_dry_run-copy.json",
        )
        bundle = live_validation_report_review_bundle(stored["key"], path=store_path)
        if bundle is None:
            raise SystemExit("Polymarket live report review bundle was not generated.")
        bundle_text = json.dumps(bundle, sort_keys=True)
        markdown = live_validation_report_review_markdown(bundle)
        if "verify-review-secret" in bundle_text or "verify-review-secret" in markdown:
            raise SystemExit("Polymarket live report review bundle leaked a seeded secret.")
        if bundle.get("static_coverage_mutated") is not False:
            raise SystemExit("Polymarket live report review bundle must not mutate static coverage.")
        if bundle.get("funded_execution_exposed") is not False:
            raise SystemExit("Polymarket live report review bundle must not expose funded execution.")
        if bundle.get("duplicate_history", {}).get("duplicate_import_count") != 1:
            raise SystemExit("Polymarket live report review bundle did not include duplicate history.")
        if not bundle.get("operator_commands", {}).get("credentialed_read"):
            raise SystemExit("Polymarket live report review bundle did not include source CLI commands.")
        levels = bundle.get("coverage_tier_mapping", {}).get("levels", {})
        if not levels.get("credential_live_verified") or not levels.get("funded_live_verified"):
            raise SystemExit("Polymarket live report review bundle did not include coverage tier mapping.")
        if "Static coverage mutated: false" not in markdown:
            raise SystemExit("Polymarket live report review markdown did not include the static coverage guard.")
    print("[ok] Polymarket live report review bundle")


def run_polymarket_live_report_decision_ledger_check() -> None:
    from polymarket.live_reports import (
        list_live_validation_report_decisions,
        live_validation_report_review_bundle,
        record_live_validation_report_decision,
        store_live_validation_report,
    )

    fixture_root = ROOT / "tests" / "fixtures" / "polymarket" / "live_reports"
    report = json.loads((fixture_root / "valid_credentialed_read.json").read_text(encoding="utf-8"))
    report["api_key"] = "verify-decision-secret"
    with tempfile.TemporaryDirectory() as tmp:
        report_path = Path(tmp) / "reports.json"
        decision_path = Path(tmp) / "decisions.json"
        stored = store_live_validation_report(
            report,
            source="verify",
            label="decision ledger",
            path=report_path,
            source_file="valid_credentialed_read.json",
        )
        bundle = live_validation_report_review_bundle(stored["key"], path=report_path)
        if bundle is None:
            raise SystemExit("Polymarket decision ledger check could not build review bundle.")
        review_hash = str(bundle.get("review_bundle_hash") or "")
        if not review_hash:
            raise SystemExit("Polymarket decision ledger check did not receive a review bundle hash.")
        accepted = record_live_validation_report_decision(
            report_key=stored["key"],
            payload_hash=stored["payload_hash"],
            target_tier="credential_live_verified",
            decision="accepted",
            reviewer_note="Credential evidence accepted for ledger test.",
            review_bundle_hash=review_hash,
            reviewer="verify",
            report_store_path=report_path,
            decision_path=decision_path,
        )
        if accepted.get("static_coverage_mutated") is not False:
            raise SystemExit("Polymarket decision ledger mutated static coverage.")
        try:
            record_live_validation_report_decision(
                report_key=stored["key"],
                payload_hash=stored["payload_hash"],
                target_tier="credential_live_verified",
                decision="accepted",
                reviewer_note="tamper",
                review_bundle_hash="tampered",
                reviewer="verify",
                report_store_path=report_path,
                decision_path=decision_path,
            )
        except ValueError as exc:
            if "review_bundle_hash mismatch" not in str(exc):
                raise SystemExit("Polymarket decision ledger returned the wrong tamper error.") from exc
        else:
            raise SystemExit("Polymarket decision ledger accepted a tampered review hash.")
        ledger = list_live_validation_report_decisions(path=decision_path)
        ledger_text = json.dumps(ledger, sort_keys=True)
        if ledger.get("counts", {}).get("entries") != 1:
            raise SystemExit("Polymarket decision ledger did not retain the accepted decision.")
        if "verify-decision-secret" in ledger_text:
            raise SystemExit("Polymarket decision ledger leaked a seeded secret.")
    print("[ok] Polymarket live report decision ledger")


def run_polymarket_live_report_promotion_proposal_check() -> None:
    from polymarket.live_reports import (
        live_validation_coverage_promotion_proposal,
        live_validation_coverage_promotion_proposal_markdown,
        live_validation_report_review_bundle,
        record_live_validation_report_decision,
        store_live_validation_report,
    )

    fixture_root = ROOT / "tests" / "fixtures" / "polymarket" / "live_reports"
    report = json.loads((fixture_root / "valid_credentialed_read.json").read_text(encoding="utf-8"))
    report["api_key"] = "verify-proposal-secret"
    with tempfile.TemporaryDirectory() as tmp:
        report_path = Path(tmp) / "reports.json"
        decision_path = Path(tmp) / "decisions.json"
        stored = store_live_validation_report(
            report,
            source="verify",
            label="promotion proposal",
            path=report_path,
            source_file="valid_credentialed_read.json",
        )
        bundle = live_validation_report_review_bundle(stored["key"], path=report_path)
        if bundle is None:
            raise SystemExit("Polymarket promotion proposal check could not build review bundle.")
        record_live_validation_report_decision(
            report_key=stored["key"],
            payload_hash=stored["payload_hash"],
            target_tier="credential_live_verified",
            decision="accepted",
            reviewer_note="Credential evidence accepted for proposal verifier.",
            review_bundle_hash=str(bundle.get("review_bundle_hash") or ""),
            reviewer="verify",
            report_store_path=report_path,
            decision_path=decision_path,
        )
        proposal = live_validation_coverage_promotion_proposal(
            report_store_path=report_path,
            decision_path=decision_path,
        )
        if proposal.get("static_coverage_mutated") is not False or proposal.get("automerge_enabled") is not False:
            raise SystemExit("Polymarket promotion proposal exposed an unsafe mutation/automerge flag.")
        if proposal.get("counts", {}).get("accepted_candidates") != 1:
            raise SystemExit("Polymarket promotion proposal did not retain the accepted decision candidate.")
        if proposal.get("counts", {}).get("proposed_changes", 0) < 1:
            raise SystemExit("Polymarket promotion proposal did not emit manual proposed changes.")
        proposal_text = json.dumps(proposal, sort_keys=True)
        markdown = live_validation_coverage_promotion_proposal_markdown(proposal)
        if "verify-proposal-secret" in proposal_text or "verify-proposal-secret" in markdown:
            raise SystemExit("Polymarket promotion proposal leaked a seeded secret.")
        if "Automerge enabled: false" not in markdown:
            raise SystemExit("Polymarket promotion proposal markdown did not include the automerge guard.")

        store = json.loads(report_path.read_text(encoding="utf-8"))
        store["reports"][stored["key"]]["payload_hash"] = "stale-payload-hash"
        report_path.write_text(json.dumps(store), encoding="utf-8")
        stale = live_validation_coverage_promotion_proposal(
            report_store_path=report_path,
            decision_path=decision_path,
        )
        if stale.get("counts", {}).get("stale_decisions") != 1:
            raise SystemExit("Polymarket promotion proposal did not detect a stale decision.")
    print("[ok] Polymarket live report promotion proposal")


def run_polymarket_live_report_promotion_proposal_snapshot_check() -> None:
    from polymarket.live_reports import (
        list_live_validation_coverage_promotion_proposal_snapshots,
        live_validation_coverage_promotion_proposal,
        live_validation_promotion_proposal_snapshot_diff_markdown,
        live_validation_promotion_proposal_snapshot_markdown,
        live_validation_report_review_bundle,
        load_live_validation_coverage_promotion_proposal_snapshot,
        record_live_validation_report_decision,
        store_live_validation_coverage_promotion_proposal_snapshot,
        store_live_validation_report,
    )

    fixture_root = ROOT / "tests" / "fixtures" / "polymarket" / "live_reports"
    report = json.loads((fixture_root / "valid_credentialed_read.json").read_text(encoding="utf-8"))
    report["api_key"] = "verify-snapshot-secret"
    with tempfile.TemporaryDirectory() as tmp:
        temp = Path(tmp)
        report_path = temp / "reports.json"
        decision_path = temp / "decisions.json"
        snapshot_path = temp / "snapshots.json"
        stored = store_live_validation_report(
            report,
            source="verify",
            label="proposal snapshot",
            path=report_path,
            source_file="valid_credentialed_read.json",
        )
        bundle = live_validation_report_review_bundle(stored["key"], path=report_path)
        if bundle is None:
            raise SystemExit("Polymarket promotion proposal snapshot check could not build review bundle.")
        record_live_validation_report_decision(
            report_key=stored["key"],
            payload_hash=stored["payload_hash"],
            target_tier="credential_live_verified",
            decision="accepted",
            reviewer_note="Credential evidence accepted for snapshot verifier.",
            review_bundle_hash=str(bundle.get("review_bundle_hash") or ""),
            reviewer="verify",
            report_store_path=report_path,
            decision_path=decision_path,
        )
        proposal = live_validation_coverage_promotion_proposal(
            report_store_path=report_path,
            decision_path=decision_path,
            target_tier="credential_live_verified",
        )
        snapshot = store_live_validation_coverage_promotion_proposal_snapshot(
            proposal=proposal,
            report_store_path=report_path,
            decision_path=decision_path,
            target_tier="credential_live_verified",
            path=snapshot_path,
            source="verify",
        )
        if snapshot.get("static_coverage_mutated") is not False or snapshot.get("snapshot_status") != "current":
            raise SystemExit("Polymarket promotion proposal snapshot was not stored as a current no-mutation snapshot.")
        opened = load_live_validation_coverage_promotion_proposal_snapshot(
            str(snapshot.get("key") or ""),
            path=snapshot_path,
            report_store_path=report_path,
            decision_path=decision_path,
        )
        if opened is None:
            raise SystemExit("Polymarket promotion proposal snapshot could not be opened.")
        snapshot_text = json.dumps(opened, sort_keys=True)
        markdown = live_validation_promotion_proposal_snapshot_markdown(opened)
        if "verify-snapshot-secret" in snapshot_text or "verify-snapshot-secret" in markdown:
            raise SystemExit("Polymarket promotion proposal snapshot leaked a seeded secret.")
        if "Promotion Proposal Snapshot" not in markdown or "Static coverage mutated: false" not in markdown:
            raise SystemExit("Polymarket promotion proposal snapshot markdown is missing safety metadata.")

        duplicate = store_live_validation_report(
            report,
            source="verify",
            label="proposal snapshot changed",
            path=report_path,
            source_file="valid_credentialed_read.json",
            allow_duplicate=True,
        )
        changed_bundle = live_validation_report_review_bundle(duplicate["key"], path=report_path)
        if changed_bundle is None:
            raise SystemExit("Polymarket promotion proposal snapshot check could not build changed review bundle.")
        record_live_validation_report_decision(
            report_key=duplicate["key"],
            payload_hash=duplicate["payload_hash"],
            target_tier="credential_live_verified",
            decision="accepted",
            reviewer_note="Changed evidence accepted for snapshot verifier.",
            review_bundle_hash=str(changed_bundle.get("review_bundle_hash") or ""),
            reviewer="verify",
            report_store_path=report_path,
            decision_path=decision_path,
        )
        listing = list_live_validation_coverage_promotion_proposal_snapshots(
            path=snapshot_path,
            report_store_path=report_path,
            decision_path=decision_path,
        )
        if listing.get("counts", {}).get("stale") != 1:
            raise SystemExit("Polymarket promotion proposal snapshot did not detect stale proposal hash.")
        stale_opened = load_live_validation_coverage_promotion_proposal_snapshot(
            str(snapshot.get("key") or ""),
            path=snapshot_path,
            report_store_path=report_path,
            decision_path=decision_path,
        )
        if stale_opened is None:
            raise SystemExit("Polymarket promotion proposal snapshot diff could not reopen the stored snapshot.")
        diff = stale_opened.get("diff") or {}
        diff_markdown = live_validation_promotion_proposal_snapshot_diff_markdown(diff)
        if not diff.get("changed") or "proposal_hash" not in (diff.get("change_categories") or []):
            raise SystemExit("Polymarket promotion proposal snapshot diff did not report changed proposal evidence.")
        if "Current-vs-Snapshot Diff" not in diff_markdown:
            raise SystemExit("Polymarket promotion proposal snapshot diff markdown is missing its review summary.")
        if "verify-snapshot-secret" in json.dumps(diff, sort_keys=True) or "verify-snapshot-secret" in diff_markdown:
            raise SystemExit("Polymarket promotion proposal snapshot diff leaked a seeded secret.")
    print("[ok] Polymarket live report promotion proposal snapshots")


def run_gui_integration_check() -> None:
    from app import market_choice_label, market_id_from_choice
    from core.models import AppConfig
    from market_adapters import MARKET_IDS, StubMarketAdapter, build_default_registry

    registry = build_default_registry()
    cfg = AppConfig()
    implemented_markets = set(IMPLEMENTED_ADAPTER_FIXTURE_TESTS)
    choices = [market_choice_label(meta) for meta in registry.list_metadata()]
    choice_market_ids = {market_id_from_choice(choice) for choice in choices}
    if choice_market_ids != set(MARKET_IDS):
        missing = sorted(set(MARKET_IDS) - choice_market_ids)
        extra = sorted(choice_market_ids - set(MARKET_IDS))
        raise SystemExit(f"GUI market choices do not match catalog. missing={missing} extra={extra}")

    for market_id, market_cfg in cfg.markets.items():
        adapter = registry.create(market_id, market_cfg.settings)
        if adapter.market_id != market_id:
            raise SystemExit(f"Adapter market id mismatch for {market_id}: {adapter.market_id}")
        if market_id in implemented_markets:
            if isinstance(adapter, StubMarketAdapter):
                raise SystemExit(f"Implemented market must not use a stub adapter: {market_id}")
        elif not isinstance(adapter, StubMarketAdapter):
            raise SystemExit(f"Market must remain a documented stub until implemented: {market_id}")

    print("[ok] GUI market integration")


def run_launch_ux_check() -> None:
    required_scripts = {
        "run_gui.bat": ("app.py",),
        "run_web_gui.bat": ("run_web_gui_dev.bat", "run_web_gui_prod.bat", "run_gui.bat"),
        "run_web_gui_dev.bat": ("web_api.py", "npm run dev", "VITE_API_BASE_URL", "run_gui.bat"),
        "run_web_gui_prod.bat": ("web_api.py", "frontend\\dist", "run_gui.bat"),
        "build_web_gui.bat": ("npm install", "npm run build", "run_web_gui_prod.bat"),
    }
    for name, expected_fragments in required_scripts.items():
        path = ROOT / name
        if not path.exists():
            raise SystemExit(f"Missing launch script: {name}")
        text = path.read_text(encoding="utf-8")
        missing = [fragment for fragment in expected_fragments if fragment not in text]
        if missing:
            raise SystemExit(f"{name} is missing launch UX fragments: {', '.join(missing)}")

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for fragment in ("run_web_gui_dev.bat", "run_web_gui_prod.bat", "build_web_gui.bat", "run_gui.bat"):
        if fragment not in readme:
            raise SystemExit(f"README.md must document {fragment}.")

    from web_api import health_payload

    health = health_payload(ROOT / "data" / "config.json", ROOT / "frontend" / "dist")
    required_health_keys = (
        "tkinter_fallback",
        "react_dev_command",
        "react_build_command",
        "react_prod_command",
        "frontend_build_available",
    )
    missing_keys = [key for key in required_health_keys if key not in health]
    if missing_keys:
        raise SystemExit("web_api health payload is missing launch metadata: " + ", ".join(missing_keys))
    print("[ok] launch UX")


def workflow_action_pin_issues(
    text: str,
    expected_actions: Mapping[str, Tuple[int, str]],
) -> list[str]:
    observed: dict[str, list[Tuple[str, int]]] = {}
    for action, revision, major_text in WORKFLOW_ACTION_REF_RE.findall(text):
        observed.setdefault(action, []).append((revision, int(major_text)))

    issues: list[str] = []
    for action, (expected_major, expected_revision) in expected_actions.items():
        references = observed.get(action)
        if not references:
            issues.append(f"{action} must be pinned to a 40-character SHA with a # v{expected_major} comment")
            continue
        for revision, major in references:
            if major != expected_major:
                issues.append(f"{action} requires # v{expected_major}; found # v{major}")
            if revision != expected_revision:
                issues.append(f"{action} must use reviewed SHA {expected_revision}; found {revision}")
    return issues


def workflow_unpinned_action_issues(text: str) -> list[str]:
    """Reject every external workflow action that lacks a reviewed immutable pin."""

    issues: list[str] = []
    for action, revision, comment in WORKFLOW_ACTION_USE_RE.findall(text):
        if action.startswith("./"):
            continue
        if re.fullmatch(r"[0-9a-f]{40}", revision) is None:
            issues.append(f"{action}@{revision} must use a lowercase 40-character commit SHA")
        if re.match(r"v\d+(?:\.\d+\.\d+)?(?:\s|$)", comment.strip()) is None:
            issues.append(f"{action}@{revision} must have a # v<major or semver> review comment")
    return issues


def action_reference_files(root: Path = ROOT) -> list[Path]:
    """Return every GitHub workflow and composite-action manifest GitHub executes."""

    workflow_directory = root / ".github" / "workflows"
    action_directory = root / ".github" / "actions"
    paths = {
        *workflow_directory.glob("*.yml"),
        *workflow_directory.glob("*.yaml"),
        *action_directory.rglob("action.yml"),
        *action_directory.rglob("action.yaml"),
    }
    return sorted(path for path in paths if path.is_file())


def _logical_shell_commands(text: str) -> list[tuple[int, str]]:
    """Join simple shell continuation lines while preserving the first line number."""

    commands: list[tuple[int, str]] = []
    fragments: list[str] = []
    first_line = 0
    for line_number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not fragments:
            first_line = line_number
        continuation = bool(stripped) and stripped.endswith(("\\", "`", "^"))
        fragments.append(stripped[:-1].rstrip() if continuation else stripped)
        if not continuation:
            commands.append((first_line, " ".join(fragment for fragment in fragments if fragment)))
            fragments = []
    if fragments:
        commands.append((first_line, " ".join(fragment for fragment in fragments if fragment)))
    return commands


def _pip_install_arguments(command: str) -> str | None:
    match = re.search(r"\bpip(?:3)?(?:\.exe)?\s+install\b", command)
    return command[match.end() :] if match is not None else None


def _is_repository_source_install(arguments: str) -> bool:
    tokens = re.findall(r'''"[^"]*"|'[^']*'|\S+''', arguments)
    for raw_token in tokens:
        token = raw_token.strip("'\"").rstrip(";")
        if token.startswith("-e="):
            token = token[3:]
        elif token.startswith("--editable="):
            token = token[len("--editable=") :]
        base = token.split("[", 1)[0].rstrip("/")
        if base in {
            ".",
            "file:.",
            "$PWD",
            "${PWD}",
            "$GITHUB_WORKSPACE",
            "${GITHUB_WORKSPACE}",
            "/workspace",
        }:
            return True
        if base.startswith(("file:$PWD", "file:${PWD}", "file:$GITHUB_WORKSPACE", "file:${GITHUB_WORKSPACE}")):
            return True
        if "github.workspace" in base.casefold():
            return True
    return False


def source_install_policy_issues(text: str) -> list[str]:
    """Reject source installs that can resolve an unreviewed PEP 517 backend."""

    issues: list[str] = []
    commands = _logical_shell_commands(text)
    required_flags = ("--no-build-isolation", "--check-build-dependencies", "--no-deps")
    for command_index, (line_number, command) in enumerate(commands):
        arguments = _pip_install_arguments(command)
        if arguments is None or not _is_repository_source_install(arguments):
            continue
        argument_tokens = arguments.split()
        for flag in required_flags:
            if flag not in argument_tokens:
                issues.append(f"line {line_number}: source install is missing {flag}")
        bootstrap_window = "\n".join(
            value for _start, value in commands[max(0, command_index - 4) : command_index]
        )
        if "--require-hashes -r requirements-bootstrap.lock" not in bootstrap_window:
            issues.append(
                f"line {line_number}: source install is not preceded by the hash-locked build backend"
            )
    return issues


def locked_requirement_install_policy_issues(text: str) -> list[str]:
    """Reject documented lock installs that can fall back to dependency sdists."""

    issues: list[str] = []
    for line_number, command in _logical_shell_commands(text):
        arguments = _pip_install_arguments(command)
        if arguments is None or re.search(r"\.lock(?:\s|$|['\"])", arguments) is None:
            continue
        argument_tokens = arguments.split()
        if "--require-hashes" not in argument_tokens:
            issues.append(f"line {line_number}: locked dependency install is missing --require-hashes")
        if "--only-binary=:all:" not in arguments.split():
            issues.append(
                f"line {line_number}: locked dependency install is missing --only-binary=:all:"
            )
    return issues


def workflow_binary_install_policy_issues(text: str) -> list[str]:
    """Require workflow-wide wheel-only policy anywhere a Python lock is installed."""

    locked_installs = [
        (line_number, arguments)
        for line_number, command in _logical_shell_commands(text)
        if (arguments := _pip_install_arguments(command)) is not None
        and re.search(r"\.lock(?:\s|$|['\"])", arguments) is not None
    ]
    if not locked_installs:
        return []
    configured_values = [
        match.group(1).strip().strip("'\"")
        for match in re.finditer(r"(?m)^\s*PIP_ONLY_BINARY:\s*([^#\r\n]+)", text)
    ]
    issues: list[str] = []
    for line_number, arguments in locked_installs:
        if "--require-hashes" not in arguments.split():
            issues.append(f"line {line_number}: workflow lock install is missing --require-hashes")
        if "--no-binary" in arguments or "--no-binary=:all:" in arguments:
            issues.append(f"line {line_number}: workflow lock install permits or requires source distributions")
    if not configured_values or any(value != ":all:" for value in configured_values):
        issues.append("workflow lock installs require PIP_ONLY_BINARY=:all:")
    if re.search(r"\b(?:docker|podman)\s+run\b", text) and "-e PIP_ONLY_BINARY=:all:" not in text:
        issues.append("containerized lock installs must receive PIP_ONLY_BINARY=:all:")
    return issues


def run_ci_cd_workflow_check() -> None:
    required_files = {
        ROOT / ".github" / "workflows" / "ci.yml": (
            "macos-14",
            "macos-15",
            "macos-26",
            "windows-2025-vs2026",
            "RHEL 8 UBI / Python 3.12",
            "RHEL 9 UBI / Python 3.12",
            "RHEL 10 UBI / Python 3.12 minimal",
            "RHEL 7 ABI / manylinux2014 Python 3.10",
            "Rocky Linux 8 / Python 3.12",
            "Rocky Linux 9 / Python 3.12",
            "Rocky Linux 10 / Python 3.12",
            "registry.access.redhat.com/ubi8/python-312:latest",
            "registry.access.redhat.com/ubi9/python-312:latest",
            "registry.access.redhat.com/ubi10/python-312-minimal:latest",
            "quay.io/pypa/manylinux2014_x86_64:latest",
            "rockylinux/rockylinux:8",
            "rockylinux/rockylinux:9",
            "rockylinux/rockylinux:10",
            "scripts/ci_enterprise_linux_smoke.py",
            "Windows 11 ARM runner / Python 3.12 x64",
            "windows-11-arm",
            'architecture: "x64"',
            "Windows 10 self-hosted / Python 3.12",
            "ENABLE_WINDOWS_10_SELF_HOSTED",
            "windows-10",
            "Mobile web smoke",
            "scripts/verify_mobile_web_smoke.py",
            "Public Polymarket live / GitHub-hosted",
            "if: github.event_name == 'workflow_dispatch'",
            "github.ref == 'refs/heads/main'",
            "runs-on: ubuntu-24.04",
            "attestations: write",
            "id-token: write",
            "scripts/verify_polymarket_live.py",
            "--public-only",
            "--validate-public-only-report",
            '--source-revision "${GITHUB_SHA}"',
            '--source-workflow-ref "${GITHUB_WORKFLOW_REF}"',
            "subject-path: ${{ runner.temp }}/public-live/public-polymarket-live.json",
            "android-14",
            "android-15",
            "android-16",
            "ios-15",
            "ios-16",
            "ios-18",
            "ios-26",
            'node-version: "24"',
            "Future Python",
            '"3.x"',
            "PIP_NO_CACHE_DIR",
            "python -m pip install --no-cache-dir --require-hashes -r requirements-bootstrap.lock",
            "python -m pip install --no-cache-dir --require-hashes -r requirements-test.lock",
            "python -m pip install --no-cache-dir --require-hashes -r requirements-build.lock",
            "python -m pip install --no-cache-dir --no-build-isolation --check-build-dependencies --no-deps -e .",
            "python verify.py",
            "npm run build",
            "Smoke install built wheel",
            "python -m venv",
            '"${smoke_python}" -m pip install --no-cache-dir --no-index',
            "site.getsitepackages()",
            "git show -s --format=%ct",
            "git archive --format=tar",
            "scripts/normalize_python_sdist.py",
            "scripts/verify_reproducible_python_dist.py",
            "--second-dir dist-repro",
            "License-Expression",
            "fetch-depth: 0",
            "scripts/verify_python_dist_artifacts.py",
        ),
        ROOT / ".github" / "workflows" / "deployment-evidence.yml": (
            "workflow_dispatch:",
            "name: production",
            "market-sentinel-production",
            "Collect raw production deployment evidence",
            "Review raw report and bind exact release identity",
            "actions/attest-build-provenance",
            "deployment-evidence-${{ github.sha }}-${{ github.run_id }}-${{ github.run_attempt }}",
        ),
        ROOT / ".github" / "workflows" / "release.yml": (
            "workflow_dispatch:",
            "release-unsigned",
            "needs.metadata.outputs.windows_signing_required == 'true' && 'release' || 'release-unsigned'",
            "contents: write",
            "Validate package version matches release tag",
            "Require release tag to resolve to workflow commit on protected main",
            "scripts/verify_release_provenance.py",
            '--tag "${RELEASE_TAG}"',
            '--commit "${GITHUB_SHA}"',
            '--main-ref "origin/main"',
            "Python compatibility",
            '"3.x"',
            "npm ci --ignore-scripts",
            "npm install --ignore-scripts --no-audit --no-fund",
            "Audit frontend dependencies used for packaging",
            "npm audit --audit-level=high",
            "Build Windows EXE and MSI",
            "macos-14",
            "macos-26",
            "windows-2025-vs2026",
            "PIP_NO_CACHE_DIR",
            "python -m pip install --no-cache-dir --require-hashes -r requirements-bootstrap.lock",
            "python -m pip install --no-cache-dir --require-hashes -r requirements-test.lock",
            "python -m pip install --no-cache-dir --require-hashes -r requirements-build.lock",
            "python -m pip install --no-cache-dir --require-hashes -r requirements-security.lock",
            "python -m pip install --no-cache-dir --no-build-isolation --check-build-dependencies --no-deps -e .",
            "Audit locked Python dependencies used for packaging",
            "pip_audit --requirement requirements.lock --progress-spinner off",
            "pip_audit --requirement requirements-live.lock --progress-spinner off",
            "pip_audit --requirement requirements-test.lock --progress-spinner off",
            "pip_audit --requirement requirements-build.lock --progress-spinner off",
            "pip_audit --requirement requirements-bootstrap.lock --progress-spinner off",
            "pip_audit --requirement requirements-security.lock --progress-spinner off",
            "scripts/build_windows_release.py",
            "scripts/create_reproducible_zip.py",
            "scripts/normalize_python_sdist.py",
            "scripts/verify_release_policy.py",
            "scripts/verify_reproducible_python_dist.py",
            "Verify release publication policy",
            "Record verified Windows signing status",
            "SOURCE_DATE_EPOCH",
            "--second-dir dist-repro",
            "git archive --format=tar",
            "windows-dist",
            "sha256sum -- * > SHA256SUMS.txt",
            "Generate SPDX SBOM",
            "scripts/generate_release_sbom.py",
            "runs-on: ubuntu-24.04",
            "Generate exact published release evidence",
            "scripts/generate_release_evidence.py",
            "Attest exact published release evidence",
            "subject-path: release-evidence/release-evidence.json",
            "Upload published release evidence",
            "Re-draft release after evidence failure",
            "steps.publish_release.outputs.release_prepared == 'true'",
            "actions/attest-build-provenance@4d101475d8b20a2381f78447822ac1eab6504dd8 # v4.2.2",
            "attestations: write",
            "id-token: write",
            "Verify Windows signing configuration",
            "REQUIRE_WINDOWS_CODE_SIGNING",
            "WINDOWS_SIGNING_REQUIRED",
            "WINDOWS_CODE_SIGNING_CERTIFICATE_BASE64",
            "WINDOWS_CODE_SIGNING_CERTIFICATE_PASSWORD",
            "X509Certificate2",
            "EphemeralKeySet",
            "certificate base64 contains internal whitespace",
            "scripts/sign_windows_release.py",
            "Verify unsigned Windows artifacts",
            "gh release create",
            "Smoke install built wheel",
            "python -m venv",
            '"${smoke_python}" -m pip install --no-cache-dir --no-index',
            "site.getsitepackages()",
            "License-Expression",
            "fetch-depth: 0",
            "scripts/verify_python_dist_artifacts.py",
        ),
        ROOT / ".github" / "workflows" / "security.yml": (
            "actions/dependency-review-action",
            "fail-on-severity: high",
            "security-events: write",
            "Secret history scan",
            "fetch-depth: 0",
            "Workflow and shell lint",
            "Download pinned actionlint",
            'archive="actionlint_${version}_linux_amd64.tar.gz"',
            "8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8",
            "Install pinned pyflakes",
            "pyflakes==3.4.0",
            "Run actionlint with shellcheck and pyflakes",
            "-shellcheck=",
            "-pyflakes=",
            "Download pinned gitleaks",
            'archive="gitleaks_${version}_linux_x64.tar.gz"',
            "551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb",
            "--config .gitleaks.toml",
            '--log-opts="--all"',
        ),
        ROOT / ".github" / "dependabot.yml": (
            "package-ecosystem: github-actions",
            "package-ecosystem: pip",
            "package-ecosystem: npm",
        ),
        ROOT / ".github" / "actionlint.yaml": (
            "self-hosted-runner:",
            "windows-10",
        ),
        ROOT / "scripts" / "verify_python_dist_artifacts.py": (
            "REQUIRED_WHEEL_MEMBERS",
            "REQUIRED_SDIST_MEMBERS",
            "License-Expression",
            "frontend/node_modules/",
        ),
        ROOT / "scripts" / "verify_dependency_lock.py": (
            "requirements.lock",
            "requirements-live.lock",
            "requirements-test.lock",
            "requirements-build.lock",
            "requirements-bootstrap.lock",
            "requirements-security.lock",
            "hash protected",
            "direct dependency",
        ),
        ROOT / "scripts" / "regenerate_dependency_locks.py": (
            "REQUIRED_PYTHON",
            "REQUIRED_PIP_TOOLS",
            "requirements-bootstrap.lock",
            "requirements-security.lock",
            "--allow-unsafe",
            "--generate-hashes",
            "--strip-extras",
            "scripts/verify_dependency_lock.py",
        ),
        ROOT / "scripts" / "run_platform_evidence.py": (
            "requirements-bootstrap.lock",
            "requirements-test.lock",
            "--only-binary=:all:",
            "--no-build-isolation",
            "--check-build-dependencies",
            "--no-deps",
        ),
        ROOT / "deploy" / "prometheus" / "market-sentinel-scrape.yml": (
            "job_name: market-sentinel",
            "metrics_path: /metrics",
            "credentials_file: /etc/prometheus/market-sentinel-observability-token",
            "127.0.0.1:8765",
            "market-sentinel-alerts.yml",
        ),
        ROOT / "deploy" / "prometheus" / "market-sentinel-alerts.yml": (
            "MarketSentinelDown",
            "MarketSentinelHighServerErrorRatio",
            "MarketSentinelOverloaded",
            "MarketSentinelMutationSaturation",
            "MarketSentinelRestartLoop",
        ),
        ROOT / "scripts" / "generate_release_sbom.py": (
            "SPDX-2.3",
            "requirements-live.lock",
            "package-lock.json",
        ),
        ROOT / "scripts" / "generate_release_evidence.py": (
            "market-sentinel-release-evidence",
            "verify_remote_asset_inventory",
            "publishable_assets",
            "runner_environment",
            "github-hosted",
        ),
        ROOT / "scripts" / "sign_windows_release.py": (
            "signtool",
            "WINDOWS_CODE_SIGNING_CERTIFICATE_BASE64",
            "WINDOWS_CODE_SIGNING_CERTIFICATE_PASSWORD",
        ),
        ROOT / "deploy" / "systemd" / "market-sentinel-web.service": (
            "--host 127.0.0.1",
            "NoNewPrivileges=true",
            "ProtectSystem=strict",
            "ProtectKernelLogs=true",
        ),
        ROOT / "deploy" / "systemd" / "market-sentinel-health.service": (
            "User=market-sentinel-health",
            "Group=market-sentinel-health",
            "EnvironmentFile=/etc/market-sentinel/market-sentinel-health.env",
            "--require-observability-token",
            "PrivateUsers=true",
            "ProtectProc=invisible",
        ),
        ROOT / "deploy" / "systemd" / "market-sentinel-health.env.example": (
            "MARKET_SENTINEL_OBSERVABILITY_TOKEN=",
        ),
        ROOT / "deploy" / "caddy" / "Caddyfile.example": (
            "basic_auth",
            "X-Market-Sentinel-Token",
            "127.0.0.1:8765",
        ),
        ROOT / "SECURITY.md": (
            "Report a vulnerability",
            "loopback-only",
        ),
        ROOT / "docs" / "PRODUCTION_OPERATIONS.md": (
            "Incident response",
            "Restore drill",
            "Funded production acceptance",
        ),
        ROOT / "docs" / "REPOSITORY_SETTINGS.md": (
            "Independent-review prerequisite",
            "required Code Owner review",
            "Signed commits",
            "secret scanning",
            "REQUIRE_WINDOWS_CODE_SIGNING=true",
        ),
        ROOT / "docs" / "CI_CD.md": (
            "Release Process",
            "python verify.py --frontend-build",
            "Windows Release Packages",
            "docs/PLATFORM_SUPPORT.md",
            "Windows code-signing credentials are required",
            "docs/PRODUCTION_OPERATIONS.md",
        ),
        ROOT / "docs" / "PLATFORM_SUPPORT.md": (
            "Windows",
            "Ubuntu Linux",
            "macOS",
            "BSD",
            "Solaris",
            "Android",
            "iOS",
            "not marked fully supported",
        ),
        ROOT / "docs" / "PRODUCTION_READINESS.md": (
            "conservative",
            "Score Model",
            "External Evidence Manifests",
            "schema_version",
            "evidence_type",
            "source_revision",
            "--platform-ci-evidence",
            "--release-environment-evidence",
            "--release-history-evidence",
            "--require-100",
        ),
        ROOT / "scripts" / "check_product_readiness.py": (
            "CATEGORY_WEIGHTS",
            "--run-public-live",
            "--platform-ci-evidence",
            "--release-environment-evidence",
            "--release-history-evidence",
            "--require-100",
            "verified",
            "schema_version",
            "evidence_type",
            "source_revision",
        ),
    }
    for path, expected_fragments in required_files.items():
        if not path.exists():
            raise SystemExit(f"Missing CI/CD file: {path.relative_to(ROOT)}")
        text = path.read_text(encoding="utf-8")
        missing = [fragment for fragment in expected_fragments if fragment not in text]
        if missing:
            raise SystemExit(f"{path.relative_to(ROOT)} is missing CI/CD fragments: {', '.join(missing)}")

    for path in (
        ROOT / ".github" / "workflows" / "ci.yml",
        ROOT / ".github" / "workflows" / "release.yml",
        ROOT / "README.md",
        ROOT / "docs" / "PRODUCTION_OPERATIONS.md",
    ):
        issues = source_install_policy_issues(path.read_text(encoding="utf-8"))
        if issues:
            raise SystemExit(f"{path.relative_to(ROOT)} has unsafe source installs: {'; '.join(issues)}")

    for path in (ROOT / "README.md", ROOT / "docs" / "PRODUCTION_OPERATIONS.md"):
        issues = locked_requirement_install_policy_issues(path.read_text(encoding="utf-8"))
        if issues:
            raise SystemExit(
                f"{path.relative_to(ROOT)} has unsafe locked dependency installs: {'; '.join(issues)}"
            )

    for relative_path, expected_actions in WORKFLOW_ACTION_PINS.items():
        path = ROOT / relative_path
        issues = workflow_action_pin_issues(path.read_text(encoding="utf-8"), expected_actions)
        if issues:
            raise SystemExit(f"{relative_path} has invalid action versions: {'; '.join(issues)}")
    for path in action_reference_files():
        action_text = path.read_text(encoding="utf-8")
        issues = workflow_unpinned_action_issues(action_text)
        if issues:
            raise SystemExit(
                f"{path.relative_to(ROOT)} has unpinned or undocumented actions: {'; '.join(issues)}"
            )
        binary_install_issues = workflow_binary_install_policy_issues(action_text)
        if binary_install_issues:
            raise SystemExit(
                f"{path.relative_to(ROOT)} has unsafe locked dependency installs: "
                + "; ".join(binary_install_issues)
            )
    result = subprocess.run(
        [sys.executable, "scripts/verify_platform_support.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise SystemExit("Platform support claim check failed:\n" + (result.stdout + result.stderr).strip())
    result = subprocess.run(
        [sys.executable, "scripts/verify_dependency_lock.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise SystemExit("Dependency lock check failed:\n" + (result.stdout + result.stderr).strip())
    print("[ok] CI/CD workflows")


def run_tkinter_smoke_check() -> None:
    from app import tkinter_smoke_payload
    from market_adapters import MARKET_IDS

    payload = tkinter_smoke_payload()
    if not payload.get("ok"):
        raise SystemExit("Tkinter smoke payload did not report ok.")
    if not payload.get("tkinter_base"):
        raise SystemExit("App must remain a tkinter.Tk subclass.")
    if payload.get("market_count") != len(MARKET_IDS):
        raise SystemExit("Tkinter smoke payload market count does not match catalog.")
    if not payload.get("all_markets_configured"):
        raise SystemExit("Tkinter smoke payload reports missing market config entries.")
    print("[ok] Tkinter smoke")


def frontend_install_issues(frontend: Path) -> list[str]:
    """Check installed package versions against the committed npm graph, offline."""
    try:
        package = json.loads((frontend / "package.json").read_text(encoding="utf-8"))
        lock = json.loads((frontend / "package-lock.json").read_text(encoding="utf-8"))
        packages = lock["packages"]
        root = packages[""]
        if not isinstance(packages, dict) or not isinstance(root, dict):
            raise ValueError("invalid package graph")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return [f"Cannot verify frontend lockfile: {type(exc).__name__}"]
    issues = []
    for group in ("dependencies", "devDependencies", "optionalDependencies"):
        if package.get(group, {}) != root.get(group, {}):
            issues.append(f"package.json {group} differs from package-lock.json")
    modules = (frontend / "node_modules").resolve()
    for relative, expected in packages.items():
        if not relative:
            continue
        installed_path = (frontend / relative / "package.json").resolve()
        if not installed_path.is_relative_to(modules) or not isinstance(expected, dict) or expected.get("link"):
            issues.append(f"Unverifiable locked package: {relative}")
            continue
        if not installed_path.exists() and expected.get("optional"):
            continue
        try:
            installed = json.loads(installed_path.read_text(encoding="utf-8"))
            actual_version = installed.get("version")
        except (OSError, ValueError, AttributeError):
            actual_version = None
        if not expected.get("version") or actual_version != expected["version"]:
            issues.append(f"{relative}: expected {expected.get('version')}, installed {actual_version or 'missing'}")
    return issues


def run_frontend_build_check(strict: bool = False) -> None:
    frontend = ROOT / "frontend"
    package_path = frontend / "package.json"
    if not package_path.exists():
        raise SystemExit("frontend/package.json is missing.")
    package = json.loads(package_path.read_text(encoding="utf-8"))
    scripts = package.get("scripts") or {}
    missing_scripts = [name for name in ("dev", "test", "prebuild", "build", "preview") if name not in scripts]
    if missing_scripts:
        raise SystemExit("frontend/package.json is missing scripts: " + ", ".join(missing_scripts))

    node_modules = frontend / "node_modules"
    if not node_modules.exists():
        message = "frontend build skipped because frontend/node_modules is missing; run build_web_gui.bat or npm install."
        if strict:
            raise SystemExit(message)
        print(f"[skip] {message}")
        return

    install_issues = frontend_install_issues(frontend)
    if install_issues:
        raise SystemExit("Frontend installation does not match the lockfile; run npm ci --ignore-scripts in frontend.\n" + "\n".join(install_issues))

    result = subprocess.run(
        [npm_command(), "run", "build"],
        cwd=frontend,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        output = (result.stdout + result.stderr).strip()
        raise SystemExit("frontend build failed:\n" + output)
    print("[ok] frontend build")


def run_frontend_live_smoke_check() -> None:
    script = ROOT / "scripts" / "verify_live_validation_report_smoke.py"
    if not script.exists():
        raise SystemExit("Live Safety report-history smoke script is missing.")
    result = subprocess.run(
        [sys.executable, str(script)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    output = (result.stdout + result.stderr).strip()
    if result.returncode != 0:
        raise SystemExit("Live Safety report-history browser smoke failed:\n" + output)
    print(output or "[ok] Live Safety report-history browser smoke")


def run_polymarket_credential_runbook_check() -> None:
    from polymarket.credential_runbook import build_polymarket_credential_runbook

    script = ROOT / "scripts" / "verify_polymarket_credentials.py"
    if not script.exists():
        raise SystemExit("Polymarket credential runbook script is missing.")
    runbook = build_polymarket_credential_runbook(environ={})
    if runbook.get("mode") != "credential_runbook_no_funded_actions":
        raise SystemExit("Polymarket credential runbook reports an unexpected mode.")
    if runbook.get("funded_execution_exposed") is not False:
        raise SystemExit("Polymarket credential runbook must not expose funded execution.")
    commands = runbook.get("operator_commands") or {}
    if "verify_polymarket_credentials.py --json" not in str(commands.get("credential_inventory", "")):
        raise SystemExit("Polymarket credential runbook is missing the inventory command.")
    if "--require-authenticated-read-ok" not in str(commands.get("credentialed_read_no_funded_actions", "")):
        raise SystemExit("Polymarket credential runbook is missing the credentialed-read command.")
    print("[ok] Polymarket credential runbook")


def effective_backend_coverage_floor() -> float:
    """Return the backend floor supported by this matrix lane.

    Windows Python 3.11+ runs the Windows-only release and ACL tests.  POSIX
    lanes and Python 3.10 intentionally skip those branches, so they use the
    compatibility floor instead of failing on coverage that cannot be
    collected on that host.
    """
    if os.name == "nt" and sys.version_info >= (3, 11):
        return MIN_BACKEND_BRANCH_COVERAGE
    return COMPATIBILITY_BACKEND_BRANCH_COVERAGE


def run_unit_tests() -> None:
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"))
    test_count = suite.countTestCases()
    if test_count == 0:
        raise SystemExit("No unit tests discovered under tests/.")
    coverage_file = ROOT / ".coverage"
    env = dict(os.environ)
    env["COVERAGE_FILE"] = str(coverage_file)
    existing_warnings = env.get("PYTHONWARNINGS", "").strip()
    env["PYTHONWARNINGS"] = ",".join(filter(None, (existing_warnings, RESOURCE_WARNING_POLICY)))
    backend_coverage_floor = effective_backend_coverage_floor()
    commands = (
        [sys.executable, "-m", "coverage", "erase"],
        [
            sys.executable,
            "-m",
            "coverage",
            "run",
            "-m",
            "unittest",
            "discover",
            "-s",
            "tests",
            "-v",
        ],
        [
            sys.executable,
            "-m",
            "coverage",
            "report",
            f"--fail-under={MIN_TOTAL_BRANCH_COVERAGE:g}",
        ],
        [
            sys.executable,
            "-m",
            "coverage",
            "report",
            f"--include={BACKEND_COVERAGE_INCLUDE}",
            f"--fail-under={backend_coverage_floor:g}",
        ],
    )
    for command in commands:
        result = subprocess.run(command, cwd=ROOT, env=env)
        if result.returncode != 0:
            raise SystemExit(result.returncode)
    print(
        f"[ok] unit tests ({test_count} tests); combined statement/branch coverage "
        f">= {MIN_TOTAL_BRANCH_COVERAGE:g}% overall and >= {backend_coverage_floor:g}% backend"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run local verification checks.")
    parser.add_argument(
        "--skip-pip-check",
        action="store_true",
        help="Skip `python -m pip check` for constrained environments.",
    )
    parser.add_argument(
        "--frontend-build",
        action="store_true",
        help="Fail unless frontend dependencies exist and `npm run build` succeeds.",
    )
    parser.add_argument(
        "--frontend-live-smoke",
        action="store_true",
        help="Run a temporary-server/headless-browser smoke test for Live Safety report-history controls.",
    )
    args = parser.parse_args()

    check_python_version()
    check_dependency_imports()
    if not args.skip_pip_check:
        run_pip_check()
    run_compile_check()
    run_static_analysis()
    run_adapter_catalog_check()
    run_support_matrix_snapshot_check()
    run_project_metadata_check()
    run_release_version_check()
    run_config_example_check()
    run_readme_matrix_check()
    run_blockers_doc_check()
    run_goal_completion_audit_check()
    run_secret_hygiene_check()
    run_fixture_check()
    run_adapter_fixture_coverage_check()
    run_polymarket_live_report_schema_check()
    run_polymarket_live_report_replay_check()
    run_polymarket_live_report_review_bundle_check()
    run_polymarket_live_report_decision_ledger_check()
    run_polymarket_live_report_promotion_proposal_check()
    run_polymarket_live_report_promotion_proposal_snapshot_check()
    run_gui_integration_check()
    run_launch_ux_check()
    run_ci_cd_workflow_check()
    run_polymarket_credential_runbook_check()
    run_tkinter_smoke_check()
    run_frontend_build_check(strict=args.frontend_build)
    if args.frontend_live_smoke:
        run_frontend_live_smoke_check()
    run_unit_tests()


if __name__ == "__main__":
    main()
