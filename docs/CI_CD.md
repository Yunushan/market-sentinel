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
- Windows 11 ARM hosted compatibility checks with Python `3.12` x64, matching the currently available wheel support for the project's transitive dependencies.
- An opt-in Windows 10 self-hosted job, enabled only when repository variable `ENABLE_WINDOWS_10_SELF_HOSTED=true` and a self-hosted runner labelled `windows-10` are available. `.github/actionlint.yaml` declares that intentional custom label so workflow linting remains strict for all other runner names.
- Mobile web smoke checks for Android 14/15/16 and iOS 15/16/18/26 user-agent and viewport profiles against the built React UI.
- Tkinter fallback smoke test with `python app.py --smoke-test`.
- Full project verification with `python verify.py`.
- Enforced branch-coverage floors of 65% for the full Python application and
  74% for the headless/backend surface. The verifier measures both and fails on
  regression.
- React production build with Node.js `24`.
- Python wheel and source distribution build, explicit artifact-content verification, and an installed-wheel CLI, metadata, registry, and adapter import smoke from outside the source tree. `MANIFEST.in` keeps the source archive's fixtures, config, docs, frontend source, scripts, workflows, and visual assets while excluding generated frontend/build directories.
- Short-retention artifacts for the frontend bundle and Python distributions.
- Every third-party action is pinned to a reviewed 40-character commit SHA,
  with its tracked major version retained as a comment for reviewability.
- Python dependencies install from hash-protected `requirements.lock`; editable
  installation uses `--no-deps` so CI cannot silently resolve newer packages.

The workflow uses read-only repository permissions by default and cancels stale runs on the same ref.

### Security

Workflow: `.github/workflows/security.yml`

Runs on pushes, pull requests, weekly schedule, and manual dispatch.

Jobs:

- Dependency review on pull requests; GitHub Dependency Graph must be enabled
  in the repository settings and high-severity dependency changes fail the job.
- A reproducible `npm ci --ignore-scripts` followed by `npm audit --omit=dev
  --audit-level=high` on every security workflow run. This fails closed for
  high-severity vulnerabilities in the production frontend dependency tree.
- CodeQL analysis for Python and JavaScript/TypeScript.

The CodeQL job is the only job with `security-events: write`; all other jobs use least-privilege read permissions unless they need more. Dependency review runs with the pull-request permissions required by GitHub's action and fails on high-severity dependency changes.

### Release

Workflow: `.github/workflows/release.yml`

Runs on tags matching `v*.*.*` and manual dispatch.

Release jobs:

- Validate release tag shape.
- Validate that `pyproject.toml` project version matches the release tag.
- Verify Python, Tkinter fallback, and project checks across the supported release range, including macOS `14`, macOS `15`, macOS `26`, and forward compatibility through future stable `3.x` releases when those interpreters are available.
- Build Python wheel/source distribution and smoke-install the exact wheel before upload.
- Build React production assets.
- Package `frontend/dist` as a zip file.
- Build a Windows x64 PyInstaller executable package.
- Package the Windows executable as a portable zip and MSI installer.
- Generate `SHA256SUMS.txt` and an SPDX 2.3 software bill of materials.
- Create GitHub build-provenance attestations for every release asset.
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
   python scripts/verify_dependency_lock.py
   ```

2. Make sure `[project].version` in `pyproject.toml` matches the release tag you plan to publish. `python verify.py` rejects reusing a tag that points to an older commit and requires an untagged version to be newer than the latest repository release tag. CI/release checkouts use full history (`fetch-depth: 0`), and local shallow clones must fetch complete history and tags before verification.

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
   - SPDX JSON SBOM
   - SHA256 checksums
   - GitHub build-provenance attestations

Manual releases can also be started from the GitHub Actions UI with `workflow_dispatch`.

## Dependency Automation

Config: `.github/dependabot.yml`

Dependabot opens grouped weekly pull requests for:

- GitHub Actions versions
- Python requirements
- Frontend npm dependencies

The committed Python lock is regenerated with `pip-compile --generate-hashes`
only during an intentional dependency update. CI installs it with
`pip install --require-hashes -r requirements.lock`; frontend CI uses `npm ci`
from `frontend/package-lock.json`.

## Windows Release Packages

Windows artifacts are produced by `scripts/build_windows_release.py` on the `windows-2025-vs2026` GitHub Actions runner. The release workflow pins WiX Toolset `6.0.2` for MSI packaging so the build is deterministic and does not silently accept newer WiX EULA prompts in CI.

The portable zip contains:

- `market-sentinel.exe`
- `start_tkinter_gui.bat`
- `start_web_gui.bat`
- bundled app icons in `assets\`
- bundled `frontend/dist` React assets
- `README.md`, `README_WINDOWS.txt`, `LICENSE`, `.env.example`, and `data/config.example.json`

The MSI installs the same payload under Program Files, creates Start Menu shortcuts for the Tkinter and React launchers, and supports normal Windows uninstall/upgrade behavior through MSI product metadata. Protected releases fail closed unless the `release` environment has `REQUIRE_WINDOWS_CODE_SIGNING=true`, `WINDOWS_CODE_SIGNING_CERTIFICATE_BASE64`, and `WINDOWS_CODE_SIGNING_CERTIFICATE_PASSWORD`. Before downloading build inputs or running WiX/PyInstaller, the release job verifies that the secret is a password-protected PFX with a private key and that the timestamp endpoint is HTTPS. This catches missing or malformed release configuration without building unsigned assets. `scripts/sign_windows_release.py` signs and verifies every EXE/MSI using an RFC 3161 timestamp URL; certificates are decoded only into a temporary file on the Windows runner.

The Windows launchers use `data/config.json` when the package folder is writable, which keeps the portable zip self-contained. If the app is installed under a protected folder such as Program Files, the launchers set `PREDICTION_MARKET_CONFIG_PATH` to `%APPDATA%\market-sentinel\data\config.json` so normal users can save settings without administrator privileges.

## Required Repository Settings

Recommended GitHub settings:

- Require the `CI / Python ...` matrix, `CI / React build`, `Security / CodeQL`, and `Security / Dependency review` checks before merging.
- Enable GitHub dependency graph; the dependency review job fails closed without it.
- Keep GitHub Actions workflow permissions as read-only by default.
- Create a protected `release` environment if production releases should require approval.
- Enable Dependabot alerts, secret scanning, push protection, and private vulnerability reporting.
- Use branch protection on `main` or `master`.

The release workflow uses the built-in `GITHUB_TOKEN` with `contents: write`,
`attestations: write`, and `id-token: write` only on the protected publish job.
Windows code-signing credentials are required separately before distributing an
installer publicly. Set `REQUIRE_WINDOWS_CODE_SIGNING=true` and the protected
code-signing secrets in the `release` environment. See `docs/REPOSITORY_SETTINGS.md` and
`docs/PRODUCTION_OPERATIONS.md` for the mandatory repository and deployment controls.
