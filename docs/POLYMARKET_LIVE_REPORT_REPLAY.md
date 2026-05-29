# Polymarket Live Validation Report Replay

`scripts/replay_polymarket_live_reports.py` is an offline operator tool for reviewing
previous Polymarket live-validation reports. It reads local JSON files only, validates the
schema, prints guarded promotion summaries, and can optionally import valid reports into
the redacted local report store. It never performs network calls, derives credentials,
places orders, cancels orders, or moves funds.

## Dry Run

Dry run is the default mode. It validates each supplied file and prints schema status,
warnings/errors, credential/funded promotion tier status, and promotion blockers:

```powershell
python scripts/replay_polymarket_live_reports.py live-report.json live-auth-report.json
python scripts/replay_polymarket_live_reports.py --json live-report.json
```

The command exits non-zero when any file is malformed or fails schema validation. Add
`--fail-on-warning` when warnings should also fail the command.

## Import

Use `--import` only after reviewing dry-run output. Valid reports are redacted and stored
through the same `polymarket.live_reports.store_live_validation_report` path used by the
GUI/API. Invalid reports are never stored. Each valid report receives a stable SHA-256
hash of the redacted canonical JSON payload and source-file provenance in replay output
and stored metadata.

```powershell
python scripts/replay_polymarket_live_reports.py --import --label-prefix replay live-auth-report.json
python scripts/replay_polymarket_live_reports.py --import --store-path data/polymarket_live_validation_reports.json live-auth-report.json
python scripts/replay_polymarket_live_reports.py --import --allow-duplicate live-auth-report.json
```

Duplicate redacted payload hashes are skipped by default, but the existing stored report
is updated with a duplicate-import audit event containing source, label, timestamp, source
file, and payload hash. Use `--allow-duplicate` only when the second report is intentional
evidence that should be retained as a separate audit entry.

Useful options:

| Option | Behavior |
| --- | --- |
| `--json` | Emit structured replay output without raw report payloads. |
| `--import` | Import valid reports into the local redacted report store. |
| `--store-path` | Override the report store path instead of using `POLYMARKET_LIVE_VALIDATION_REPORTS_PATH` or the default data path. |
| `--source` | Set the stored source label; default is `cli_replay`. |
| `--label-prefix` | Prefix stored labels with an operator label. |
| `--max-entries` | Apply the same bounded retention behavior as normal report storage. |
| `--fail-on-warning` | Exit non-zero when schema warnings are present. |
| `--skip-duplicates` | Skip duplicate redacted payload hashes during import and record an audit event. This is the default. |
| `--allow-duplicate` | Store duplicate redacted payload hashes as separate audit entries with `duplicate_of` provenance. |

## Verification

`python verify.py` replays deterministic valid and invalid fixtures, confirms dry-run mode
does not store reports, and confirms import mode writes only valid reports to a temporary
redacted store. It also verifies default duplicate skipping and explicit duplicate import.
Unit tests exercise the CLI JSON output, source-file provenance, duplicate counts, and
redaction behavior.
