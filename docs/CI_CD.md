# CI/CD and Release Operations

This project uses GitHub Actions for pull-request validation, release artifact generation, security scanning, and dependency update automation.

## Workflows

### CI

Workflow: `.github/workflows/ci.yml`

Runs on pushes, pull requests, and manual dispatch.

Jobs:

- Python verification on Ubuntu and Windows across Python `3.10`, `3.11`, `3.12`, `3.13`, and `3.14`.
- Tkinter fallback smoke test with `python app.py --smoke-test`.
- Full project verification with `python verify.py`.
- React production build with Node.js `22`.
- Python wheel and source distribution build.
- Short-retention artifacts for the frontend bundle and Python distributions.

The workflow uses read-only repository permissions by default and cancels stale runs on the same ref.

### Security

Workflow: `.github/workflows/security.yml`

Runs on pushes, pull requests, weekly schedule, and manual dispatch.

Jobs:

- Dependency review on pull requests, failing on high-severity dependency changes.
- CodeQL analysis for Python and JavaScript/TypeScript.

The CodeQL job is the only job with `security-events: write`; all other jobs use least-privilege read permissions unless they need more.

### Release

Workflow: `.github/workflows/release.yml`

Runs on tags matching `v*.*.*` and manual dispatch.

Release jobs:

- Validate release tag shape.
- Verify Python, Tkinter fallback, and project checks.
- Build Python wheel/source distribution.
- Build React production assets.
- Package `frontend/dist` as a zip file.
- Generate `SHA256SUMS.txt`.
- Publish or update a GitHub Release using the built-in `GITHUB_TOKEN`.

The publish job targets the `release` environment. Treat this as the release environment for production publishing, and configure protection rules for it in GitHub if releases should require manual approval.

## Release Process

1. Make sure local verification passes:

   ```bash
   python app.py --smoke-test
   python -m pytest -q
   python verify.py
   ```

2. Build frontend dependencies in an environment where npm can complete:

   ```bash
   cd frontend
   npm install
   npm run build
   cd ..
   python verify.py --frontend-build
   ```

3. Create and push a semver tag:

   ```bash
   git tag v0.1.0
   git push origin v0.1.0
   ```

4. Watch the Release workflow. A successful run publishes:

   - Python wheel
   - Python source distribution
   - React production frontend zip
   - SHA256 checksums

Manual releases can also be started from the GitHub Actions UI with `workflow_dispatch`.

## Dependency Automation

Config: `.github/dependabot.yml`

Dependabot opens grouped weekly pull requests for:

- GitHub Actions versions
- Python requirements
- Frontend npm dependencies

Once `frontend/package-lock.json` exists, CI automatically prefers `npm ci` for deterministic frontend installs. Until then it falls back to `npm install --no-audit --no-fund`.

## Required Repository Settings

Recommended GitHub settings:

- Require the `CI / Python ...` matrix, `CI / React build`, and `Security / CodeQL` checks before merging.
- Keep GitHub Actions workflow permissions as read-only by default.
- Create a protected `release` environment if production releases should require approval.
- Enable Dependabot alerts and secret scanning.
- Use branch protection on `main` or `master`.

No custom release secrets are required. The release workflow uses the built-in `GITHUB_TOKEN` with `contents: write` only on the publish job.
