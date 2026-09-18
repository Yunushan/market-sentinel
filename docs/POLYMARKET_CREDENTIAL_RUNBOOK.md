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
| SDK trading credentials | `POLYMARKET_PRIVATE_KEY` or `PRIVATE_KEY`; optional `POLYMARKET_SIGNATURE_TYPE` or `SIGNATURE_TYPE`; `POLYMARKET_FUNDER_ADDRESS`, `FUNDER_ADDRESS`, or `DEPOSIT_WALLET_ADDRESS` when required; explicit `POLY_API_KEY`, `POLY_API_SECRET`, and `POLY_PASSPHRASE` are preferred for V2 L2 use | Local `py-clob-client-v2` readiness and dry-run order/cancel transcript readiness |
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

Funded order/cancel verification is separate from this runbook. Normal product
mutation remains disabled, while the official `py-clob-client-v2` wrapper has a
dedicated one-shot audit factory that is not exposed to the application. The
audit is available only from the protected evidence workflow and requires
explicit production-environment approval, `--allow-funded-order`,
`--cancel-immediately`, an independently configured allow-listed token, hard
five-share/one-dollar caps, a clean stable source revision bound to the
canonical repository origin, geographic eligibility, same-client authenticated
reads, sufficient balance and allowance, post-only maker placement, exact
zero-fill/cancel proof, and an absolute `--recovery-journal` path in a private
directory. The capability is consumed before transport. The journal is
atomically updated and locked; an ambiguous or interrupted run cannot be
retried and must be manually reconciled before the evidence can qualify.
Native Windows funded mode fails closed because the tool cannot prove an
owner-only journal-directory ACL there. Public and credential-only probes
remain available on Windows. A funded run additionally requires:

```text
--confirm-live-order-cancel I_UNDERSTAND_THIS_PLACES_A_REAL_POLYMARKET_ORDER
```

Fail-closed diagnostic shape (expected without the protected workflow approval,
exact allow-list policy, credentials, and private recovery journal;
replace every placeholder only in an explicitly approved acceptance environment):

```powershell
python scripts/verify_polymarket_live.py --token-id <TOKEN> --side BUY --price <MAKER_PRICE> --size <SIZE> --allow-token-id <TOKEN> --cancel-immediately --allow-funded-order --recovery-journal <ABSOLUTE_PRIVATE_JOURNAL_PATH> --confirm-live-order-cancel I_UNDERSTAND_THIS_PLACES_A_REAL_POLYMARKET_ORDER --report-file live-funded-report.json
```
