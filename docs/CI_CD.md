# CI/CD and Release Operations

This project uses GitHub Actions for pull-request validation, release artifact generation, security scanning, and dependency update automation.

## Workflows

### CI

Workflow: `.github/workflows/ci.yml`

Runs on pushes, pull requests, and manual dispatch.

Jobs:

- Python verification on Ubuntu, macOS `14`, macOS `15`, macOS `26`, and hosted Windows across Python `3.10`, `3.11`, `3.12`, `3.13`, and `3.14`.
- Forward Python compatibility checks through the moving latest stable `3.x` runner; this avoids prerelease runner failures while still following future stable Python releases above 3.16 as GitHub Actions publishes them.
- Enterprise Linux smoke checks through RHEL UBI 8/9/10, a RHEL 7-era manylinux2014 ABI container, and Rocky Linux 8/9/10 containers.
- Windows 11 ARM hosted compatibility checks with Python `3.12`.
- An opt-in Windows 10 self-hosted job, enabled only when repository variable `ENABLE_WINDOWS_10_SELF_HOSTED=true` and a self-hosted runner labelled `windows-10` are available.
- Mobile web smoke checks for Android 14/15/16 and iOS 15/16/18/26 user-agent and viewport profiles against the built React UI.
- Tkinter fallback smoke test with `python app.py --smoke-test`.
- Full project verification with `python verify.py`.
- React production build with Node.js `24`.
- Python wheel and source distribution build.
- Short-retention artifacts for the frontend bundle and Python distributions.

The workflow uses read-only repository permissions by default and cancels stale runs on the same ref.

### Security

Workflow: `.github/workflows/security.yml`

Runs on pushes, pull requests, weekly schedule, and manual dispatch.

Jobs:

- Dependency review on pull requests, automatically skipped when GitHub dependency graph is disabled for the repository.
- CodeQL analysis for Python and JavaScript/TypeScript.

The CodeQL job is the only job with `security-events: write`; all other jobs use least-privilege read permissions unless they need more. After dependency graph is enabled, dependency review will run normally and fail on high-severity dependency changes. If GitHub's repository metadata API cannot report dependency graph status, set repository variable `DEPENDENCY_REVIEW_ENABLED=true` to force the check.

### Release

Workflow: `.github/workflows/release.yml`

Runs on tags matching `v*.*.*` and manual dispatch.

Release jobs:

- Validate release tag shape.
- Validate that `pyproject.toml` project version matches the release tag.
- Verify Python, Tkinter fallback, and project checks across the supported release range, including macOS `14`, macOS `15`, macOS `26`, and forward compatibility through future stable `3.x` releases when those interpreters are available.
- Build Python wheel/source distribution.
- Build React production assets.
- Package `frontend/dist` as a zip file.
- Build a Windows x64 PyInstaller executable package.
- Package the Windows executable as a portable zip and MSI installer.
- Generate `SHA256SUMS.txt`.
- Publish or update a GitHub Release using the built-in `GITHUB_TOKEN`.

The publish job targets the `release` environment. Treat this as the release environment for production publishing, and configure protection rules for it in GitHub if releases should require manual approval.

See `docs/PLATFORM_SUPPORT.md` for the platform support tiers and the gates required before any additional OS or mobile platform is advertised as fully supported.

The normal verifier runs `python scripts/verify_platform_support.py` to ensure platform claims remain documented and honest. `python scripts/verify_platform_support.py --require-full` is the strict 100% platform certification gate; it must fail until every requested desktop, Unix, BSD/Solaris, Android, and iOS target has real repeatable test evidence.

## Release Process

1. Make sure local verification passes:

   ```bash
   python app.py --smoke-test
   python -m pytest -q
   python verify.py
   ```

2. Make sure `[project].version` in `pyproject.toml` matches the release tag you plan to publish.

3. Build frontend dependencies in an environment where npm can complete:

   ```bash
   cd frontend
   npm install
   npm run build
   cd ..
   python verify.py --frontend-build
   ```

4. Create and push a semver tag:

   ```bash
   git tag v0.1.0
   git push origin v0.1.0
   ```

5. Watch the Release workflow. A successful run publishes:

   - Python wheel
   - Python source distribution
   - React production frontend zip
   - Windows x64 portable zip with the bundled `.exe`, React assets, launchers, and example config
   - Windows x64 MSI installer
   - SHA256 checksums

Manual releases can also be started from the GitHub Actions UI with `workflow_dispatch`.

## Dependency Automation

Config: `.github/dependabot.yml`

Dependabot opens grouped weekly pull requests for:

- GitHub Actions versions
- Python requirements
- Frontend npm dependencies

Once `frontend/package-lock.json` exists, CI automatically prefers `npm ci` for deterministic frontend installs. Until then it falls back to `npm install --no-audit --no-fund`.

## Windows Release Packages

Windows artifacts are produced by `scripts/build_windows_release.py` on the `windows-2025-vs2026` GitHub Actions runner. The release workflow pins WiX Toolset `6.0.2` for MSI packaging so the build is deterministic and does not silently accept newer WiX EULA prompts in CI.

The portable zip contains:

- `market-sentinel.exe`
- `start_tkinter_gui.bat`
- `start_web_gui.bat`
- bundled app icons in `assets\`
- bundled `frontend/dist` React assets
- `README.md`, `README_WINDOWS.txt`, `LICENSE`, `.env.example`, and `data/config.example.json`

The MSI installs the same payload under Program Files, creates Start Menu shortcuts for the Tkinter and React launchers, and supports normal Windows uninstall/upgrade behavior through MSI product metadata. The package is currently unsigned; add code-signing certificate secrets before treating the installer as a polished public Windows distribution.

The Windows launchers use `data/config.json` when the package folder is writable, which keeps the portable zip self-contained. If the app is installed under a protected folder such as Program Files, the launchers set `PREDICTION_MARKET_CONFIG_PATH` to `%APPDATA%\market-sentinel\data\config.json` so normal users can save settings without administrator privileges.

## Required Repository Settings

Recommended GitHub settings:

- Require the `CI / Python ...` matrix, `CI / React build`, and `Security / CodeQL` checks before merging.
- Enable GitHub dependency graph before making `Security / Dependency review` a required blocking check.
- Keep GitHub Actions workflow permissions as read-only by default.
- Create a protected `release` environment if production releases should require approval.
- Enable Dependabot alerts and secret scanning.
- Use branch protection on `main` or `master`.

No custom release secrets are required. The release workflow uses the built-in `GITHUB_TOKEN` with `contents: write` only on the publish job.
