# Contributing

## Before opening a pull request

1. Keep credentials, wallet keys, API tokens, and generated runtime data out of the diff.
2. Run `python verify.py --frontend-build --frontend-live-smoke`.
3. Add focused tests for behavior changes, especially safety gates and API routes.
4. Update user-facing documentation when installation, operational, or trading behavior changes.
5. Do not claim live or funded verification without the evidence required by the live-validation workflow.

## Review expectations

Changes to credentials, order placement, copy trading, release workflows,
dependency locks, and deployment files need owner review. Dependency updates
must preserve the hash lock and pass security checks.
