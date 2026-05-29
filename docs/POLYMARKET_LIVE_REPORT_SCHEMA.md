# Polymarket Live Validation Report Schema

The live-validation report schema is a local import contract for reports produced by
`scripts/verify_polymarket_live.py`, the GUI readiness snapshot, the credential runbook,
and deterministic browser-smoke fixtures. It validates shape and mode before a report is
stored. It does not promote a report to production verification by itself; promotion is
still handled by the live report promotion guard.

## Accepted Modes

| Mode | Intended producer | Storage behavior |
| --- | --- | --- |
| `strict_cli` | `scripts/verify_polymarket_live.py` public, credentialed-read, dry-run, or funded-audit reports | Accepted when `stage_gates` is an object. Missing check sections produce warnings. |
| `local_readiness_only` | `GET /api/polymarket/live-validation` GUI/API snapshot | Accepted when `stage_gates` is an object. It can never promote credentialed or funded verification tiers. |
| `credential_runbook_no_funded_actions` | `scripts/verify_polymarket_credentials.py` | Accepted only with `env_inventory`, `readiness`, `funded_execution_exposed=false`, and no network-call mode. It can never promote verification tiers. |
| `browser_smoke` | Local browser smoke reports | Accepted when `stage_gates` is an object. It can never promote verification tiers. |
| `browser_smoke_seed` | Deterministic seeded browser-smoke reports | Accepted when `stage_gates` is an object. It can never promote verification tiers. |

## Live-Stage Shape

Live-stage reports are all modes except `credential_runbook_no_funded_actions`.
They must be JSON objects with:

| Field | Requirement |
| --- | --- |
| `mode` | Required non-empty string and one of the accepted modes. |
| `generated_at` | Optional numeric timestamp; missing values are accepted with a warning. |
| `stage_gates` | Required object. |
| `stage_gates.credentialed_read_ok` | Optional boolean. Non-boolean values are errors. |
| `stage_gates.safe_to_attempt_funded_order` | Optional boolean. Non-boolean values are errors. |
| `stage_gates.requires_explicit_live_approval` | Optional boolean. Non-boolean values are errors. |
| `public_checks`, `authenticated_read_checks`, `bridge_address_checks` | Optional check-section objects. Missing or malformed sections are warnings so older reports remain inspectable when they have valid gates. |
| `funded_live_order_check` | Optional object with a status such as `ok`, `failed`, `blocked`, `skipped`, `dry_run`, or `ready_to_execute`. Missing status or unknown values are warnings. |

Recommended `stage_gates` keys are `public_live_checks`, `credential_readiness`,
`credentialed_read_checks`, `bridge_address_checks`, `funded_live_order_check`,
`credentialed_read_ok`, `safe_to_attempt_funded_order`, `requires_explicit_live_approval`,
and `next_step`. Missing recommended fields warn but do not block storage.

## Runbook Shape

Credential runbook reports must include:

| Field | Requirement |
| --- | --- |
| `mode` | Must be `credential_runbook_no_funded_actions`. |
| `env_inventory` | Required object. |
| `readiness` | Required object. |
| `funded_execution_exposed` | Must be exactly `false`. |
| `network_calls` | Must be absent or `none`. |

Runbook reports may include `stage_gates`, but the schema warns because promotion remains
blocked for the runbook mode.

## API Behavior

`POST /api/polymarket/live-validation/reports` validates imported `report_json` before
redaction and disk write. A malformed report returns HTTP 400 with error code
`live_validation_report_schema_error` and a `schema_validation` details object containing
`ok`, `mode`, `report_type`, `errors`, `warnings`, and `accepted_modes`.

Stored report metadata also includes compact `schema_validation`, so the UI and exports can
show whether a report was accepted with warnings. Storage additionally records a stable
`payload_hash` computed from the redacted canonical JSON payload plus a `provenance`
object containing the hash, optional source-file details, duplicate policy, and
`duplicate_of` when a duplicate is intentionally retained.

Duplicate redacted payload hashes are skipped by default for API, CLI, and React imports.
The existing entry is not duplicated, but it is updated with a `duplicate_imports` audit
event and `duplicate_import_count` so the attempted import is still traceable. Set
`allow_duplicate=true` on the API request, check `Allow duplicate import` in the React UI,
or pass `--allow-duplicate` to the replay CLI to store a second full audit entry.

The React Live Safety import panel displays accepted-mode guidance and the latest schema
diagnostics from an import, stored snapshot, or opened report. If an import is rejected,
the report count is unchanged and the returned schema errors/warnings stay visible to the
operator instead of being written to disk.

## Fixtures

Deterministic schema fixtures live in `tests/fixtures/polymarket/live_reports/`:

| Fixture | Expected result |
| --- | --- |
| `valid_credentialed_read.json` | Accepted `strict_cli`; can support credential promotion because it contains accepted authenticated-read evidence. |
| `valid_funded_audit.json` | Accepted `strict_cli`; can support funded promotion because it contains a real order/cancel audit shape. |
| `valid_dry_run.json` | Accepted `strict_cli`; funded promotion remains blocked. |
| `valid_runbook.json` | Accepted runbook mode; all promotion remains blocked. |
| `valid_browser_smoke.json` | Accepted browser-smoke seed; all promotion remains blocked. |
| `invalid_missing_mode.json` | Rejected because `mode` is missing. |
| `invalid_bad_stage_gates.json` | Rejected because `stage_gates` is not an object. |

`python verify.py` validates these fixtures as part of the local verification suite.

Use `scripts/replay_polymarket_live_reports.py` to validate previously saved report files
against this schema before importing them into the redacted local report store.
