# Polymarket Credential Runbook

This runbook is for local credential readiness only. It does not make network calls, derive API keys, sign orders, submit orders, cancel orders, or move funds.

## Inventory Command

```powershell
python scripts/verify_polymarket_credentials.py --json --report-file polymarket-credential-runbook.json
```

Use the stricter local gate when preparing for a real credentialed read:

```powershell
python scripts/verify_polymarket_credentials.py --require-authenticated-read-ready
```

The stricter gate exits non-zero until at least one non-destructive authenticated read or stream candidate is locally ready.

## Environment Groups

| Group | Variables | Purpose |
| --- | --- | --- |
| SDK trading credentials | `POLYMARKET_PRIVATE_KEY` or `PRIVATE_KEY`; optional `POLYMARKET_SIGNATURE_TYPE` or `SIGNATURE_TYPE`; `POLYMARKET_FUNDER_ADDRESS`, `FUNDER_ADDRESS`, or `DEPOSIT_WALLET_ADDRESS` when required | Local py-clob-client readiness and dry-run order/cancel transcript readiness |
| Direct CLOB L2 reads | `POLY_ADDRESS`, `POLY_API_KEY`, `POLY_PASSPHRASE`, `POLY_SIGNATURE`, `POLY_TIMESTAMP` | Non-destructive authenticated CLOB order-list/read checks |
| CLOB L1 REST headers | `POLY_ADDRESS`, `POLY_SIGNATURE`, `POLY_TIMESTAMP`, `POLY_NONCE` | Explicit L1-authenticated REST calls; signatures are not synthesized |
| User WebSocket | `POLY_API_KEY`, `POLY_API_SECRET` or `POLY_SECRET`, `POLY_PASSPHRASE` | Authenticated user WebSocket subscription check |
| Relayer | `RELAYER_API_KEY`, `RELAYER_API_KEY_ADDRESS` | Non-destructive relayer authenticated reads |
| Builder API | `POLY_BUILDER_API_KEY`, `POLY_BUILDER_TIMESTAMP`, `POLY_BUILDER_PASSPHRASE`, `POLY_BUILDER_SIGNATURE` | Builder-specific authenticated endpoints |

Credentials must stay in `.env`, shell environment variables, OS keychain tooling, or approved external secret files. Do not store them in `data/config.json`.

## Follow-Up Commands

Public readiness and local report:

```powershell
python scripts/verify_polymarket_live.py --report-file live-report.json
```

Credentialed read and user WebSocket check, still with no funded actions:

```powershell
python scripts/verify_polymarket_live.py --require-authenticated-read-ok --include-user-websocket-connect --report-file live-auth-report.json
```

Dry-run order/cancel transcript, still with no funded actions:

```powershell
python scripts/verify_polymarket_live.py --token-id <TOKEN> --side BUY --price <PRICE> --size <SIZE> --allow-token-id <TOKEN> --report-file live-dry-run-report.json
```

Funded order/cancel verification is separate and must not be run until the operator explicitly approves a live check. It requires `--allow-funded-order`, `--cancel-immediately`, an allow-listed token, hard size/notional caps, maker-side public orderbook preflight, and:

```text
--confirm-live-order-cancel I_UNDERSTAND_THIS_PLACES_A_REAL_POLYMARKET_ORDER
```

