# Polymarket Live Validation Promotion Proposal

The promotion proposal export turns accepted decision-ledger entries into a guarded
manual patch proposal. It is intentionally not an automation that edits coverage or
documentation.

## What It Reads

- Stored redacted live-validation reports.
- The promotion decision ledger.
- Current sanitized review bundles for accepted decisions.

Rejected decisions are ignored. Accepted decisions whose current report payload hash
or review-bundle hash no longer matches the recorded decision are moved to
`stale_decisions` and cannot produce proposed changes.

## What It Emits

- `human_review_required=true`
- `automerge_enabled=false`
- `apply_by_default=false`
- `static_coverage_mutated=false`
- `funded_execution_exposed=false`
- Accepted, stale, and ignored decision sections.
- Manual proposed-change rows for `polymarket/coverage.py`, `README.md`, `GOAL.md`,
  and operator evidence docs.

The proposed-change rows are instructions for a later human-authored patch. They are
not executable patches and do not change static coverage by themselves.

## CLI

```powershell
python scripts/review_polymarket_live_decisions.py --export-proposal --json
python scripts/review_polymarket_live_decisions.py --export-proposal --markdown
python scripts/review_polymarket_live_decisions.py --export-proposal --target-tier credential_live_verified --markdown
```

## API

- `GET /api/polymarket/live-validation/promotion-proposal`
- `GET /api/polymarket/live-validation/promotion-proposal/export.json`
- `GET /api/polymarket/live-validation/promotion-proposal/export.md`

All routes accept optional `target_tier=public_live_verified`,
`target_tier=credential_live_verified`, or `target_tier=funded_live_verified`.

## React Preview

The Live Safety tab includes a read-only **Promotion Proposal Preview** panel. It can
refresh the current proposal, filter by target tier, show accepted/stale/ignored
counts, list review gates, and display accepted candidates plus proposed manual
changes. The panel only offers JSON/Markdown downloads; it has no apply, merge, or
coverage-edit action.

## Snapshot Archive

The same panel can store optional no-secrets proposal snapshots. A snapshot records
the proposal payload, proposal hash, target-tier filter, counts, storage provenance,
and safety flags. Snapshot list/open/export/delete controls are local archive
operations only; they do not apply coverage changes.

Snapshot metadata is rechecked against the current proposal hash when listed or
opened:

- `snapshot_status=current` means the stored proposal hash still matches the current
  proposal for the same target-tier filter.
- `snapshot_status=stale` means current proposal evidence changed, usually through a
  decision-ledger or stored-report change. Stale snapshots must be reviewed again
  before they are used as evidence.

Snapshot API routes:

- `GET /api/polymarket/live-validation/promotion-proposal/snapshots`
- `POST /api/polymarket/live-validation/promotion-proposal/snapshots`
- `GET /api/polymarket/live-validation/promotion-proposal/snapshots/{key}`
- `GET /api/polymarket/live-validation/promotion-proposal/snapshots/{key}/export.json`
- `GET /api/polymarket/live-validation/promotion-proposal/snapshots/{key}/export.md`
- `DELETE /api/polymarket/live-validation/promotion-proposal/snapshots/{key}`

## Required Review Gates

- A human reviewer must compare the proposal, decision ledger, and current review
  bundle.
- No automerge or automatic patch application is allowed.
- Stale decisions must be re-recorded before promotion.
- Credential/funded tiers require current accepted evidence from the sanitized review
  bundle.
- Region, KYC, account, wallet funding, and explicit live-action approval remain
  external prerequisites.
- Any manual coverage/docs patch must update tests/docs and pass the verifier.
