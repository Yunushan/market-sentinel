from __future__ import annotations

import argparse
import tarfile
from email.parser import BytesParser
from pathlib import Path, PurePosixPath
from zipfile import ZipFile

try:
    from scripts.release_version import normalize_release_version
except ModuleNotFoundError:  # Direct execution adds scripts/, rather than the repository root, to sys.path.
    from release_version import normalize_release_version


REQUIRED_WHEEL_MEMBERS = {
    "core/unattended_worker.py",
    "market_sentinel_cli.py",
    "market_adapters/crypto_com_predict.py",
    "market_adapters/registry.py",
    "polymarket/funded_policy.py",
    "polymarket/leaderboard_state.py",
    "polymarket/leaderboard_validation.py",
}

REQUIRED_SDIST_MEMBERS = {
    ".github/actionlint.yaml",
    ".github/workflows/ci.yml",
    ".github/workflows/security.yml",
    ".github/workflows/release.yml",
    ".github/workflows/deployment-evidence.yml",
    ".github/workflows/polymarket-evidence.yml",
    "CONTRIBUTING.md",
    "CODE_SIGNING_POLICY.md",
    "LICENSE",
    "MANIFEST.in",
    "PRIVACY.md",
    "SECURITY.md",
    "assets/marketsentinel.svg",
    "data/config.example.json",
    "deploy/caddy/Caddyfile.example",
    "deploy/caddy/market-sentinel-env.conf",
    "deploy/prometheus/market-sentinel-alerts.yml",
    "deploy/prometheus/market-sentinel-attestation-alertmanager.yml.example",
    "deploy/prometheus/market-sentinel-attestation-prometheus.yml.example",
    "deploy/prometheus/market-sentinel-scrape.yml",
    "deploy/systemd/market-sentinel-alerts-refresh.service",
    "deploy/systemd/market-sentinel-alerts-refresh.timer",
    "deploy/systemd/market-sentinel-backup.service",
    "deploy/systemd/market-sentinel-backup.timer",
    "deploy/systemd/market-sentinel-health.service",
    "deploy/systemd/market-sentinel-health.timer",
    "deploy/systemd/market-sentinel-health.env.example",
    "deploy/systemd/market-sentinel-wallets-poll.service",
    "deploy/systemd/market-sentinel-wallets-poll.timer",
    "deploy/systemd/market-sentinel-worker.env.example",
    "deploy/systemd/market-sentinel.env.example",
    "deploy/systemd/market-sentinel.conf",
    "deploy/systemd/market-sentinel-web.service",
    "docs/BLOCKERS.md",
    "docs/PRODUCTION_OPERATIONS.md",
    "docs/SIGNPATH_RELEASE_SIGNING.md",
    "deploy/signpath/market-sentinel-exe-v1.xml",
    "deploy/signpath/market-sentinel-msi-v1.xml",
    "frontend/package-lock.json",
    "frontend/package.json",
    "frontend/playwright.config.mjs",
    "frontend/browser-tests/workflows.spec.mjs",
    "frontend/tests/browser-projects.test.mjs",
    "frontend/scripts/run-tests.mjs",
    "frontend/src/App.tsx",
    "requirements.lock",
    "requirements-bootstrap.lock",
    "requirements-live.lock",
    "requirements-security.lock",
    "requirements-test.lock",
    "requirements-build.lock",
    "requirements.txt",
    "requirements-bootstrap.txt",
    "requirements-build.txt",
    "requirements-live.txt",
    "requirements-security.txt",
    "requirements-test.txt",
    "scripts/verify_dependency_lock.py",
    "scripts/verify_browser_workflows.py",
    "scripts/collect_platform_evidence.py",
    "scripts/collect_prometheus_delivery_evidence.py",
    "scripts/run_platform_evidence.py",
    "scripts/review_platform_evidence.py",
    "scripts/review_prometheus_delivery_evidence.py",
    "scripts/review_deployment_evidence.py",
    "scripts/generate_deployment_evidence.py",
    "scripts/initialize_production_config.py",
    "scripts/backup_state.py",
    "scripts/build_windows_release.py",
    "scripts/signpath_release.py",
    "scripts/windows_signing_policy.py",
    "scripts/verify_msi_signing_payload.ps1",
    "scripts/smoke_windows_msi.ps1",
    "scripts/verify_windows_release_metadata.ps1",
    "scripts/create_reproducible_zip.py",
    "scripts/normalize_python_sdist.py",
    "scripts/regenerate_dependency_locks.py",
    "scripts/verify_polymarket_live.py",
    "scripts/restore_state_backup.py",
    "scripts/verify_production_deployment.py",
    "scripts/verify_release_provenance.py",
    "scripts/verify_release_assets.py",
    "scripts/verify_release_policy.py",
    "scripts/verify_reproducible_python_dist.py",
    "scripts/verify_service_health.py",
    "scripts/release_version.py",
    "tests/fixtures/crypto_com_predict/events.json",
    "tests/fixtures/crypto_com_predict/contracts.json",
    "tests/fixtures/crypto_com_predict/price.json",
    "tests/fixtures/hypermind/outcomes.txt",
    "tests/fixtures/hypermind/prices.csv",
    "tests/fixtures/iowa_electronic_markets/powell_price_data.txt",
    "tests/test_crypto_com_predict_adapter.py",
    "tests/test_prometheus_delivery_evidence.py",
    "tests/test_funded_token_policy.py",
    "tests/test_unattended_worker.py",
}

EXPECTED_LICENSE_EXPRESSION = "0BSD"
REQUIRED_LICENSE_FRAGMENTS = (
    "BSD Zero Clause License",
    "Permission to use, copy, modify, and/or distribute this software",
    'THE SOFTWARE IS PROVIDED "AS IS"',
)


def _single_artifact(dist_dir: Path, pattern: str, label: str) -> Path:
    matches = sorted(path for path in dist_dir.glob(pattern) if path.is_file())
    if len(matches) != 1:
        names = ", ".join(path.name for path in matches) or "none"
        raise SystemExit(f"Expected exactly one {label} matching {pattern!r}; found {names}.")
    return matches[0]


def _canonical_version(value: str) -> str:
    try:
        return normalize_release_version(str(value))
    except ValueError as exc:
        raise SystemExit(f"Unsupported expected release version {value!r}: {exc}") from exc


def _missing(required: set[str], actual: set[str]) -> list[str]:
    return sorted(required - actual)


def _verify_license_text(text: str, label: str) -> None:
    missing = [fragment for fragment in REQUIRED_LICENSE_FRAGMENTS if fragment not in text]
    if missing:
        raise SystemExit(f"{label} does not contain the expected BSD Zero Clause License text.")


def _verify_artifact_privacy(names: set[str], label: str) -> None:
    forbidden = []
    generated_fragments = (
        "__pycache__", "node_modules", ".cache", ".test-dist", "test-results",
        "playwright-report", ".tmp", ".git", ".venv",
    )
    for name in names:
        clean_name = name.rstrip("/")
        path = PurePosixPath(clean_name)
        parts = path.parts
        folded_parts = tuple(part.casefold() for part in parts)
        lower_name = path.name.casefold()
        if (
            path.is_absolute() or ".." in parts or "\\" in name
            or any(":" in part or part.endswith((" ", ".")) for part in parts)
            or any(part in generated_fragments for part in folded_parts)
            or folded_parts[:2] == ("frontend", "dist")
            or (folded_parts and folded_parts[0] == "data" and clean_name not in {"data/config.example.json", "data"})
            or lower_name == ".coverage"
            or (
                (lower_name.startswith(".env") or ".env." in lower_name or lower_name.endswith(".env"))
                and not lower_name.endswith(".env.example")
            )
            or path.suffix.lower() in {".pem", ".key", ".pfx", ".p12", ".jks"}
        ):
            forbidden.append(name)
    if forbidden:
        raise SystemExit(
            f"{label} contains generated/private artifacts: {', '.join(sorted(forbidden)[:10])}"
        )


def verify_wheel(path: Path, expected_version: str) -> None:
    expected_version = _canonical_version(expected_version)
    dist_info = f"market_sentinel-{expected_version}.dist-info"
    metadata_name = f"{dist_info}/METADATA"
    entry_points_name = f"{dist_info}/entry_points.txt"
    license_name = f"{dist_info}/licenses/LICENSE"
    with ZipFile(path) as archive:
        names = set(archive.namelist())
        _verify_artifact_privacy(names, f"Wheel {path.name}")
        required = REQUIRED_WHEEL_MEMBERS | {metadata_name, entry_points_name, license_name}
        missing = _missing(required, names)
        if missing:
            raise SystemExit(f"Wheel {path.name} is missing required members: {', '.join(missing)}")

        metadata = BytesParser().parsebytes(archive.read(metadata_name))
        expected_fields = {
            "Name": "market-sentinel",
            "Version": expected_version,
            "Requires-Python": ">=3.10",
            "License-Expression": EXPECTED_LICENSE_EXPRESSION,
        }
        for key, expected in expected_fields.items():
            actual = metadata.get(key)
            if actual != expected:
                raise SystemExit(
                    f"Wheel {path.name} metadata {key} is {actual!r}; expected {expected!r}."
                )
        if metadata.get_all("License-File") != ["LICENSE"]:
            raise SystemExit(f"Wheel {path.name} must declare LICENSE exactly once.")
        test_dependency_entries = [
            value
            for value in metadata.get_all("Requires-Dist", [])
            if value.lower().startswith(("pytest", "coverage"))
        ]
        if len(test_dependency_entries) != 2 or any(
            'extra == "test"' not in value for value in test_dependency_entries
        ):
            raise SystemExit(
                f"Wheel {path.name} must expose pytest and coverage only through the test extra."
            )
        live_dependency_entries = [
            value
            for value in metadata.get_all("Requires-Dist", [])
            if value.lower().startswith("py-clob-client-v2")
        ]
        if len(live_dependency_entries) != 1 or 'extra == "live"' not in live_dependency_entries[0]:
            raise SystemExit(
                f"Wheel {path.name} must expose py-clob-client-v2 only through the live extra."
            )
        _verify_license_text(archive.read(license_name).decode("utf-8"), f"Wheel {path.name} LICENSE")
        entry_points = archive.read(entry_points_name).decode("utf-8")
        required_entry_points = {
            "market-sentinel = market_sentinel_cli:main",
            "market-sentinel-worker = core.unattended_worker:main",
        }
        missing_entry_points = sorted(item for item in required_entry_points if item not in entry_points)
        if missing_entry_points:
            raise SystemExit(
                f"Wheel {path.name} is missing required CLI entry points: "
                + ", ".join(missing_entry_points)
            )


def verify_sdist(path: Path, expected_version: str) -> None:
    expected_version = _canonical_version(expected_version)
    prefix = f"market_sentinel-{expected_version}/"
    with tarfile.open(path, "r:gz") as archive:
        names = set(archive.getnames())
        unexpected = sorted(name for name in names if name != prefix.rstrip("/") and not name.startswith(prefix))
        if unexpected:
            raise SystemExit(f"Source distribution {path.name} contains unexpected archive roots.")
        if any(member.issym() or member.islnk() for member in archive.getmembers()):
            raise SystemExit(f"Source distribution {path.name} contains symbolic or hard links.")
        relative_names = {name[len(prefix) :] for name in names if name.startswith(prefix)}
        _verify_artifact_privacy(relative_names, f"Source distribution {path.name}")
        missing = _missing(REQUIRED_SDIST_MEMBERS, relative_names)
        if missing:
            raise SystemExit(f"Source distribution {path.name} is missing: {', '.join(missing)}")
        license_file = archive.extractfile(f"{prefix}LICENSE")
        if license_file is None:
            raise SystemExit(f"Source distribution {path.name} is missing LICENSE content.")
        _verify_license_text(
            license_file.read().decode("utf-8"),
            f"Source distribution {path.name} LICENSE",
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify built MarketSentinel wheel and sdist contents.")
    parser.add_argument("--dist-dir", type=Path, default=Path("dist"))
    parser.add_argument("--expected-version", required=True)
    args = parser.parse_args()

    dist_dir = args.dist_dir.resolve()
    if not dist_dir.is_dir():
        raise SystemExit(f"Distribution directory does not exist: {dist_dir}")
    version = _canonical_version(str(args.expected_version or "").strip())
    wheel = _single_artifact(dist_dir, f"market_sentinel-{version}-*.whl", "wheel")
    sdist = _single_artifact(dist_dir, f"market_sentinel-{version}.tar.gz", "source distribution")
    verify_wheel(wheel, version)
    verify_sdist(sdist, version)
    print(f"[ok] Python distributions ({wheel.name}, {sdist.name})")


if __name__ == "__main__":
    main()
