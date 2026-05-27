from __future__ import annotations

import argparse
import compileall
import importlib
import importlib.metadata
import json
import subprocess
import sys
import unittest
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 compatibility.
    import tomli as tomllib


ROOT = Path(__file__).resolve().parent
MIN_PYTHON = (3, 10)
MAX_PYTHON = (3, 15)
PROJECT_NAME = "prediction-market-alert-and-copy-trade-gui"

REQUIRED_IMPORTS = {
    "requests": "requests",
    "websocket-client": "websocket",
    "python-dotenv": "dotenv",
    "py-clob-client": "py_clob_client",
    "packaging": "packaging",
    "pytest": "pytest",
    "cryptography": "cryptography",
    "eth-account": "eth_account",
    "eth-abi": "eth_abi",
}


def check_python_version() -> None:
    current = sys.version_info[:3]
    if current < MIN_PYTHON or current >= MAX_PYTHON:
        raise SystemExit(
            "Unsupported Python "
            f"{current[0]}.{current[1]}.{current[2]}; expected >=3.10,<3.15."
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


def run_compile_check() -> None:
    checks = [
        compileall.compile_file(str(ROOT / "app.py"), quiet=1),
        compileall.compile_dir(str(ROOT / "core"), quiet=1),
        compileall.compile_dir(str(ROOT / "market_adapters"), quiet=1),
        compileall.compile_dir(str(ROOT / "polymarket"), quiet=1),
        compileall.compile_dir(str(ROOT / "tests"), quiet=1),
    ]
    if not all(checks):
        raise SystemExit("Python compile check failed.")
    print("[ok] compileall")


def run_adapter_catalog_check() -> None:
    from market_adapters import MARKET_CATALOG, MARKET_IDS, build_default_registry

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
    print(f"[ok] adapter catalog ({len(MARKET_CATALOG)} markets)")


def run_project_metadata_check() -> None:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    name = data.get("project", {}).get("name")
    if name != PROJECT_NAME:
        raise SystemExit(f"pyproject.toml project name must be {PROJECT_NAME!r}; got {name!r}.")
    if "_" in name:
        raise SystemExit("pyproject.toml project name must use dashes, not underscores.")

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    app = (ROOT / "app.py").read_text(encoding="utf-8")
    if f"# {PROJECT_NAME}" not in readme:
        raise SystemExit("README.md title must use the dashed project name.")
    if f'self.title("{PROJECT_NAME}")' not in app:
        raise SystemExit("app.py window title must use the dashed project name.")
    if f'User-Agent": "{PROJECT_NAME}/1.0"' not in app:
        raise SystemExit("app.py User-Agent must use the dashed project name.")
    forbidden = (
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
        if not isinstance(data, dict):
            raise SystemExit(f"Fixture must contain a JSON object: {path.relative_to(ROOT)}")

    required = {
        fixture_root / "polymarket" / "market.json",
        fixture_root / "polymarket" / "event.json",
        fixture_root / "polymarket" / "orderbook.json",
        fixture_root / "polymarket" / "activity_buy.json",
        fixture_root / "kalshi" / "markets.json",
        fixture_root / "kalshi" / "orderbook.json",
        fixture_root / "manifold" / "search_markets.json",
        fixture_root / "manifold" / "market_binary.json",
        fixture_root / "manifold" / "market_multi.json",
        fixture_root / "manifold" / "prob_binary.json",
        fixture_root / "manifold" / "prob_multi.json",
        fixture_root / "metaculus" / "posts.json",
        fixture_root / "metaculus" / "post_binary.json",
        fixture_root / "metaculus" / "post_multiple.json",
        fixture_root / "metaculus" / "post_numeric.json",
        fixture_root / "predictit" / "all.json",
        fixture_root / "predictit" / "market.json",
        fixture_root / "limitless_exchange" / "active.json",
        fixture_root / "limitless_exchange" / "market.json",
        fixture_root / "limitless_exchange" / "orderbook.json",
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
        fixture_root / "myriad_markets" / "order_response.json",
        fixture_root / "opinion_labs" / "markets.json",
        fixture_root / "opinion_labs" / "market.json",
        fixture_root / "opinion_labs" / "price.json",
        fixture_root / "opinion_labs" / "orderbook.json",
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
    }
    missing = [str(path.relative_to(ROOT)) for path in sorted(required) if not path.exists()]
    if missing:
        raise SystemExit("Missing required offline fixtures: " + ", ".join(missing))
    print(f"[ok] offline fixtures ({len(fixture_paths)} files)")


def run_gui_integration_check() -> None:
    from app import market_choice_label, market_id_from_choice
    from core.models import AppConfig
    from market_adapters import MARKET_IDS, StubMarketAdapter, build_default_registry

    registry = build_default_registry()
    cfg = AppConfig()
    implemented_markets = {
        "polymarket",
        "kalshi",
        "predictit",
        "manifold",
        "metaculus",
        "limitless_exchange",
        "sx_bet",
        "azuro",
        "augur",
        "omen",
        "zeitgeist",
        "myriad_markets",
        "xo_market",
        "opinion_labs",
        "gemini_titan",
        "predict_fun",
        "betfair_exchange",
    }
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


def run_ci_cd_workflow_check() -> None:
    required_files = {
        ROOT / ".github" / "workflows" / "ci.yml": (
            "actions/setup-python@v6",
            "actions/setup-node@v6",
            'node-version: "24"',
            "python verify.py",
            "npm run build",
        ),
        ROOT / ".github" / "workflows" / "release.yml": (
            "workflow_dispatch:",
            "environment: release",
            "contents: write",
            "Validate package version matches release tag",
            "Build Windows EXE and MSI",
            "scripts/build_windows_release.py",
            "windows-dist",
            "sha256sum * > SHA256SUMS.txt",
            "gh release create",
        ),
        ROOT / ".github" / "workflows" / "security.yml": (
            "actions/dependency-review-action@v5",
            "Detect dependency graph support",
            "DEPENDENCY_REVIEW_ENABLED",
            "github/codeql-action/init@v4",
            "security-events: write",
        ),
        ROOT / ".github" / "dependabot.yml": (
            "package-ecosystem: github-actions",
            "package-ecosystem: pip",
            "package-ecosystem: npm",
        ),
        ROOT / "docs" / "CI_CD.md": (
            "Release Process",
            "python verify.py --frontend-build",
            "Windows Release Packages",
            "No custom release secrets are required",
        ),
    }
    for path, expected_fragments in required_files.items():
        if not path.exists():
            raise SystemExit(f"Missing CI/CD file: {path.relative_to(ROOT)}")
        text = path.read_text(encoding="utf-8")
        missing = [fragment for fragment in expected_fragments if fragment not in text]
        if missing:
            raise SystemExit(f"{path.relative_to(ROOT)} is missing CI/CD fragments: {', '.join(missing)}")
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


def run_frontend_build_check(strict: bool = False) -> None:
    frontend = ROOT / "frontend"
    package_path = frontend / "package.json"
    if not package_path.exists():
        raise SystemExit("frontend/package.json is missing.")
    package = json.loads(package_path.read_text(encoding="utf-8"))
    scripts = package.get("scripts") or {}
    missing_scripts = [name for name in ("dev", "build", "preview") if name not in scripts]
    if missing_scripts:
        raise SystemExit("frontend/package.json is missing scripts: " + ", ".join(missing_scripts))

    node_modules = frontend / "node_modules"
    if not node_modules.exists():
        message = "frontend build skipped because frontend/node_modules is missing; run build_web_gui.bat or npm install."
        if strict:
            raise SystemExit(message)
        print(f"[skip] {message}")
        return

    result = subprocess.run(
        ["npm", "run", "build"],
        cwd=frontend,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        output = (result.stdout + result.stderr).strip()
        raise SystemExit("frontend build failed:\n" + output)
    print("[ok] frontend build")


def run_unit_tests() -> None:
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"))
    test_count = suite.countTestCases()
    if test_count == 0:
        raise SystemExit("No unit tests discovered under tests/.")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)
    print(f"[ok] unit tests ({test_count} tests)")


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
    args = parser.parse_args()

    check_python_version()
    check_dependency_imports()
    if not args.skip_pip_check:
        run_pip_check()
    run_compile_check()
    run_adapter_catalog_check()
    run_project_metadata_check()
    run_config_example_check()
    run_readme_matrix_check()
    run_blockers_doc_check()
    run_fixture_check()
    run_gui_integration_check()
    run_launch_ux_check()
    run_ci_cd_workflow_check()
    run_tkinter_smoke_check()
    run_frontend_build_check(strict=args.frontend_build)
    run_unit_tests()


if __name__ == "__main__":
    main()
