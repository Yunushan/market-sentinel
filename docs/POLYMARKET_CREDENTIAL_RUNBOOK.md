# Polymarket Credential Runbook

This runbook inventories local credential inputs only. It does not make network
calls, instantiate the SDK, derive API keys, sign, place or cancel orders, or move
funds. A ready input set does not prove authentication, venue eligibility or
production acceptance.

## Inventory Command

```powershell
python scripts/verify_polymarket_credentials.py --json --report-file polymarket-credential-runbook.json
```

Use these stricter offline gates to prepare for the actual read probe:

```powershell
python scripts/verify_polymarket_credentials.py --require-authenticated-read-ready
python scripts/verify_polymarket_credentials.py --require-l2-read-ready
python scripts/verify_polymarket_credentials.py --require-user-websocket-ready
```

`--require-authenticated-read-ready` requires a locally ready current SDK CLOB
order-list or relayer authenticated-read candidate. WebSocket payload readiness
is reported separately and cannot satisfy this accepted-read gate.
`--require-l2-read-ready` now checks the current freshly signed SDK CLOB read,
not the presence of legacy pre-signed headers. This is an intentional change
from the former header-only check. The `direct_l2_read_headers` inventory remains
available for legacy consumers. `--require-user-websocket-ready` retains its
separate payload-building semantics; it does not authenticate with the venue.

## Environment Groups

| Group | Variables | Purpose |
| --- | --- | --- |
| Fresh SDK authenticated read | `POLYMARKET_PRIVATE_KEY` or `PRIVATE_KEY`; explicit `POLY_API_KEY`, `POLY_API_SECRET` or `POLY_SECRET`, and `POLY_PASSPHRASE`; optional `POLYMARKET_SIGNATURE_TYPE` or `SIGNATURE_TYPE`; `POLYMARKET_FUNDER_ADDRESS` or `FUNDER_ADDRESS` for types 1, 2 and 3 | Environment-only readiness for the current fresh CLOB order-list read; no key derivation or creation |
| General SDK signing inventory | Private key, signature type and funder from the broader settings/environment inventory, including `DEPOSIT_WALLET_ADDRESS` | Local general signing/config inventory, separate from inputs the standalone read CLI actually consumes |
| Legacy pre-signed L2 headers | `POLY_ADDRESS`, `POLY_API_KEY`, `POLY_PASSPHRASE`, `POLY_SIGNATURE`, `POLY_TIMESTAMP` | Header inventory only; does not prepare the current SDK read |
| CLOB L1 REST headers | `POLY_ADDRESS`, `POLY_SIGNATURE`, `POLY_TIMESTAMP`, `POLY_NONCE` | Explicit legacy REST-header inventory; signatures are not synthesized |
| User WebSocket | `POLY_API_KEY`, `POLY_API_SECRET` or `POLY_SECRET`, `POLY_PASSPHRASE` | Separate local subscription-payload readiness |
| Relayer | `RELAYER_API_KEY`, `RELAYER_API_KEY_ADDRESS` | Both nonblank, unpadded HTTP-compatible headers are required; local presence is not authentication evidence |
| Builder API | `POLY_BUILDER_API_KEY`, `POLY_BUILDER_TIMESTAMP`, `POLY_BUILDER_PASSPHRASE`, `POLY_BUILDER_SIGNATURE` | Builder-specific header inventory; does not satisfy the default accepted-read gate |

For the fresh SDK read, `POLYMARKET_` signer, signature-type and funder aliases
have priority over their generic aliases. `POLY_API_SECRET` has priority over
`POLY_SECRET`. Selection uses the first nonempty raw value: an invalid or
whitespace-only higher-priority value is not rescued by a lower-priority alias.
The private key must be unpadded `0x` plus 64 hexadecimal digits and a valid,
nonzero secp256k1 scalar. A supplied funder must be an unpadded EVM address.
Supported signature types are integer 0 through 3; 0 is the default. API
key/secret/passphrase are individually trimmed as in the SDK wrapper and must
all remain nonempty. The selected trimmed API secret must be accepted by the
same URL-safe base64 decoder used by the locked SDK. The trimmed API key and
passphrase must be ASCII and compatible with the SDK's HTTP header grammar;
opaque punctuation and ordinary internal spaces/tabs remain permitted. No
provider-specific byte length, UUID or key format is inferred. Relayer values must be nonblank, unpadded and
sendable as raw HTTP headers without control characters. This does not validate
a relayer address or account.

Settings-only credentials and `DEPOSIT_WALLET_ADDRESS` do not prepare this CLI
read because the CLI does not forward them. Every public inventory field is
redacted or contains presence, validity, source names or blockers; selected raw
credentials never appear in a report. Credentials must stay in `.env`, shell
environment, OS keychain tooling or approved external secret files. Do not store
them in `data/config.json`.

## Follow-Up Commands

Readiness and public/authenticated read report (no funded action):

```powershell
python scripts/verify_polymarket_live.py --report-file live-report.json
```

Credentialed read and optional user WebSocket check (no funded action):

```powershell
python scripts/verify_polymarket_live.py --require-authenticated-read-ok --include-user-websocket-connect --report-file live-auth-report.json
```

The accepted credentialed evidence is a semantically validated CLOB order-list
or relayer collection read. A WebSocket payload/connection, companion SDK status
or local readiness boolean does not substitute for that evidence. Local reports
are diagnostic; production points require the protected-main workflow's current
exact-source artifact and exact-byte attestation.

Dry-run order/cancel transcript (no funded action):

```powershell
python scripts/verify_polymarket_live.py --token-id <TOKEN> --side BUY --price <PRICE> --size <SIZE> --allow-token-id <TOKEN> --cancel-immediately --report-file live-dry-run-report.json
```

## Protected Funded Acceptance

Funded order/cancel verification is separate from this runbook. Normal product
mutation remains disabled. The one-shot audit is reviewed through the
[protected Polymarket evidence workflow](https://github.com/Yunushan/market-sentinel/actions/workflows/polymarket-evidence.yml).
Inspect its definition without dispatching it:

```powershell
gh workflow view polymarket-evidence.yml --repo Yunushan/market-sentinel --ref main
```

Before a funded dispatch, obtain explicit user authorization for the exact token,
side, price, size and caps. The production environment needs its configured
sole-owner reviewer approval, real eligible funded-account credentials, and an
independently configured canonical `POLYMARKET_FUNDED_TOKEN_ALLOWLIST`. The
collector needs a persistent self-hosted Linux x64 runner with the
`market-sentinel-production` label and a private durable recovery journal.
The workflow inputs are `tier=funded`, `token_id`, `side`, `price`, `size` and
`funded_confirmation`; the exact confirmation is defined in the workflow.
This runbook does not provide a dispatch command or a standalone funded
execution command. Never invent SHA/run/attempt/nonce values: the workflow
supplies and binds them, and the hosted reviewer verifies them.

The protected audit permits at most five shares and one USDC notional, checks
geographic eligibility, same-account authenticated reads, balance/allowance and
maker-side orderbook conditions, then places one post-only GTC order and
immediately cancels its exact ID. Acceptance requires zero-fill and post-cancel
proof, a resolved atomically updated durable journal, and hosted review and
attestation on the first attempt of the specifically approved run. Ambiguous or
interrupted outcomes require manual reconciliation before another attempt.
Windows funded journals remain unavailable because their owner-only directory
ACL cannot be established by this collector. Public and credential-only probes
remain available on Windows. None of these preparation checks or commands
authorizes unattended product trading or earns credentialed/funded points alone.
