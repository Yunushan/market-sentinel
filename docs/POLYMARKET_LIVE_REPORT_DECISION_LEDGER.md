# Polymarket Live Validation Promotion Decision Ledger

The decision ledger records operator decisions about stored live-validation review bundles.
It is intentionally separate from the stored report and from static coverage metadata.
A decision never promotes production coverage by itself.

## Required Fields

Every decision requires:

| Field | Purpose |
| --- | --- |
| `report_key` | Stored live-validation report key. |
| `payload_hash` | Redacted payload hash from the review bundle. |
| `target_tier` | One of `public_live_verified`, `credential_live_verified`, or `funded_live_verified`. |
| `decision` | `accepted` or `rejected`. |
| `reviewer_note` | Required operator rationale. |
| `review_bundle_hash` | Current deterministic hash of the review bundle. |

The ledger recomputes the current review bundle before recording a decision. If the
payload hash or review-bundle hash does not match, the request fails with a mismatch
error so stale or tampered evidence cannot be accepted.

## API

```powershell
curl http://127.0.0.1:8765/api/polymarket/live-validation/decisions
curl http://127.0.0.1:8765/api/polymarket/live-validation/decisions/export.json
curl http://127.0.0.1:8765/api/polymarket/live-validation/decisions/export.md
```

Record a decision:

```powershell
curl -X POST http://127.0.0.1:8765/api/polymarket/live-validation/decisions `
  -H "Content-Type: application/json" `
  -d "{\"report_key\":\"...\",\"payload_hash\":\"...\",\"target_tier\":\"credential_live_verified\",\"decision\":\"rejected\",\"reviewer_note\":\"Evidence is insufficient.\",\"review_bundle_hash\":\"...\"}"
```

## CLI-Style Workflow

Use the local script to fetch the current review input and export the ledger:

```powershell
python scripts/review_polymarket_live_decisions.py --report-key <REPORT_KEY> --print-review-input
python scripts/review_polymarket_live_decisions.py --export-ledger --markdown
python scripts/review_polymarket_live_decisions.py --export-proposal --markdown
```

Record a decision from the command line:

```powershell
python scripts/review_polymarket_live_decisions.py --report-key <REPORT_KEY> --payload-hash <HASH> --target-tier credential_live_verified --decision rejected --reviewer-note "Evidence is insufficient." --review-bundle-hash <BUNDLE_HASH>
```

## Safety Rules

- `static_coverage_mutated` is always `false`.
- `funded_execution_exposed` is always `false`.
- `accepted` decisions for `credential_live_verified` and `funded_live_verified` require
  review-bundle evidence that can promote that tier; blocked tiers fail closed.
- Decision exports contain no raw report payload and no secrets.
- Coverage promotion still requires a separate code/docs change after human review.
- The optional promotion proposal export is documented in
  `docs/POLYMARKET_LIVE_REPORT_PROMOTION_PROPOSAL.md`; it detects stale accepted
  decisions but still does not apply coverage changes.
