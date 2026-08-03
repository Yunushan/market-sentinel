# Production Readiness

`python scripts/check_product_readiness.py` is the repository's conservative
readiness score. It reports a score out of 100 and separates repeatable local
proof from evidence that can only be collected on a real host, account, or
GitHub repository.

## Current Check

Run the local test suite and the no-credentials public Polymarket probe:

```bash
python scripts/check_product_readiness.py \
  --full-local \
  --run-public-live \
  --json
```

`--full-local` runs `verify.py --skip-pip-check --frontend-build
--frontend-live-smoke`. The public probe never derives credentials, opens the
authenticated user stream, places orders, or performs funded actions.

Use `--no-run-local` when inspecting the repository shape only. A skipped
check does not receive local test or security points.

## Score Model

| Area | Points | Local baseline | Additional proof required for full points |
| --- | ---: | ---: | --- |
| Architecture and scope | 18 | 18 | None beyond the repository contract |
| Tests and correctness | 18 | 18 after local verification | None |
| Security and safety | 17 | 16 after local verification | Reviewed repository-settings evidence |
| CI/CD and release | 17 | 14 when release tooling is present | +1 protected release environment; +1 release history; +1 current published release |
| Operations and recovery | 15 | 12 when deployment artifacts are present | Reviewed real-host deployment evidence |
| Platform evidence | 10 | 5 when the support matrix is present | +3 successful hosted CI lanes; +2 evidence for every full-support target |
| Live acceptance | 5 | 3 after public checks pass | Credentialed read and approved funded audit |

The scorer never treats a workflow matrix as proof that a runner completed.
It also does not promote Polymarket credentialed or funded tiers from a local
runbook, browser smoke test, or dry-run transcript.

## External Evidence Manifests

External points require a JSON manifest supplied with the corresponding
option. Every manifest must contain `verified: true`, non-empty `reviewed_by`
and `reviewed_at` values, and a non-empty `checks` array whose entries all have
`status` equal to `pass` or `ok`:

```json
{
  "verified": true,
  "reviewed_by": "operator-or-reviewer",
  "reviewed_at": "2026-08-03T18:00:00Z",
  "checks": [
    {"name": "source_revision", "status": "pass"}
  ]
}
```

Example after the evidence has actually been collected and reviewed:

```bash
python scripts/check_product_readiness.py \
  --full-local \
  --run-public-live \
  --deployment-evidence evidence/deployment.json \
  --platform-ci-evidence evidence/platform-ci.json \
  --platform-evidence evidence/platform.json \
  --repository-settings-evidence evidence/repository-settings.json \
  --release-environment-evidence evidence/release-environment.json \
  --release-history-evidence evidence/release-history.json \
  --release-evidence evidence/release.json \
  --credentialed-evidence evidence/polymarket-credentialed.json \
  --funded-evidence evidence/polymarket-funded.json \
  --require-100
```

Do not put venue credentials, private keys, cookies, or raw request logs in an
evidence manifest. Use the deployment and Polymarket runbooks to produce
redacted results, then review the source revision and check results before
passing a manifest to the scorer.
