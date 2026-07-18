# Required GitHub Repository Settings

These controls are configured in GitHub, not source control. An administrator
must enable them before treating a release as production-ready.

## Solo-maintainer main branch

Protect `main` with:

1. Required successful `CI`, `Security`, and release-validation checks.
2. No force pushes, no branch deletion, and no direct administrator bypass for
   normal releases.
3. Required conversation resolution and linear history.

## Team production policy

When an independent maintainer is available, additionally require at least one
approving review, Code Owner review, and dismissal of stale approvals on new
commits. Keep `.github/CODEOWNERS` current before enabling that policy.

## Security and automation

1. Enable dependency graph, Dependabot alerts, Dependabot security updates,
   secret scanning, and push protection.
2. Enable private vulnerability reporting.
3. Review and merge or close the active Dependabot pull requests after CI.

## Release environment

Create the `release` environment with required reviewers, no self-approval,
and deployment-branch rules limited to protected `main` tags. Store code
signing credentials there: `WINDOWS_CODE_SIGNING_CERTIFICATE_BASE64`,
`WINDOWS_CODE_SIGNING_CERTIFICATE_PASSWORD`, and optional
`WINDOWS_CODE_SIGNING_TIMESTAMP_URL`. Set repository/environment variable
`REQUIRE_WINDOWS_CODE_SIGNING=true`. Never add venue credentials to GitHub
Actions.
