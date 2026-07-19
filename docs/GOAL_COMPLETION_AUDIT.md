# Goal Completion Audit

This document records the current evidence for the multi-market support goal in
[`GOAL.md`](../GOAL.md). It is an evidence index, not a claim that external
account, jurisdiction, or platform-runner checks have occurred.

## Completion Scope

All repository-controlled requirements in the goal are complete. This includes
the application, adapter catalog, supported or truthful blocked adapters,
configuration, GUI and CLI paths, documentation, local safety controls, and
repeatable verification. The user explicitly excluded the external evidence
gates below from this local completion scope. They remain deliberately
unpromoted and cannot be inferred from local tests or CI configuration.

## Local Requirement Evidence

| Goal requirement | Evidence in the repository | Verification |
| --- | --- | --- |
| Common adapter architecture | `market_adapters/base.py`, `market_adapters/registry.py`, and `market_adapters/catalog.py` define the shared contract, registry, and 41-market catalog. | `tests/test_market_adapters.py` validates the full contract and registry. `verify.py` runs `run_adapter_catalog_check`. |
| Every listed market appears in config and GUI | `data/config.example.json`, `app.py`, `web_api.py`, and `frontend/src/App.tsx` consume the same market catalog. | `tests/test_config_examples.py`, `tests/test_market_adapters.py`, `tests/test_final_parity.py`, Tkinter smoke, React build, and browser smoke. |
| Working adapter or truthful stub for each market | The 18 implemented adapters live under `market_adapters/`; verified unsupported adapters use `VerifiedBlockedAdapter` and the exact reasons in [`BLOCKERS.md`](BLOCKERS.md). | `tests/test_market_adapters.py` verifies implemented adapters, 23 stubs, capability flags, and clear operational errors. `tests/test_blockers_doc.py` verifies all catalog entries have blocker coverage. |
| Official/documented integration policy | Adapter implementations cite official sources and the documented unsupported cases are preserved in [`BLOCKERS.md`](BLOCKERS.md). | Adapter fixture/unit tests and the blocker-document verifier run in `python verify.py`. |
| Discovery, contracts, prices, alerts, paper, guarded live, and copy capability declarations | Capability values are declared in `market_adapters/catalog.py` and enforced by `market_adapters/base.py` plus each adapter. | `tests/test_market_adapters.py` validates capability keys and unsupported-operation behavior; adapter-specific tests cover supported parser/order paths. |
| Live and copy safety defaults | `core/models.py`, `data/config.example.json`, `web_api.py`, and adapter preflight paths keep live/copy actions disabled unless explicitly enabled. | `tests/test_config_examples.py`, `tests/test_app_logic.py`, `tests/test_polymarket_api.py`, and `tests/test_web_api.py`. |
| Secret and endpoint hygiene | Application sources must not contain common access-token formats, private keys, credentialed URLs, non-loopback private-network addresses, or literal authorization/cookie values. | `tests/test_secret_hygiene.py` and the `verify.py` secret-hygiene gate. |
| GUI market selector and web parity | Tkinter integration is in `app.py`; the web UI is in `frontend/src/App.tsx`; state/API surface is in `web_api.py`. | `verify.py --frontend-build --frontend-live-smoke` runs Tkinter metadata smoke, React build, API checks, and a local headless browser smoke. CI and release additionally run `python app.py --gui-smoke-test` under Ubuntu Xvfb to construct the actual Tk widget tree and verify shutdown without network workers. |
| README capability matrix | [`README.md`](../README.md) contains all 41 rows and required capability columns. | `tests/test_readme_matrix.py` and `verify.py` reject missing rows, headers, and `TBD` values. |
| Offline fixtures and verifier | `tests/fixtures/` contains adapter and Polymarket fixtures, including the official Crypto.com Predictions event/contract/price shapes; `verify.py` validates fixture JSON, named required fixtures, imports, metadata, docs, workflows, and the test suite. It also requires a complete fixture-directory/test-file mapping for every implemented adapter. | `tests/test_verifier_coverage.py`, `tests/test_crypto_com_predict_adapter.py`, and `python verify.py --frontend-build --frontend-live-smoke`. |
| Cross-platform Python support contract | `pyproject.toml` requires Python `>=3.10`; `.github/workflows/ci.yml` defines Python 3.10 through 3.14 plus the moving 3.x lane and documented OS matrix. | `verify.py` checks the workflow contract and `tests/test_platform_support.py` checks that support is described without a false full-support claim. |
| Release metadata, version lineage, and installed artifacts | `pyproject.toml` carries the next unreleased version and modern SPDX/license-file metadata; `MANIFEST.in` defines reproducible source contents; CI/release inspect both archives and install the exact wheel from outside the source tree. | `verify.py` rejects tag reuse, shallow/stale histories, deprecated license metadata, and missing distribution gates; `scripts/verify_python_dist_artifacts.py` checks metadata and required/forbidden archive members, then the installed-wheel smoke checks CLI startup, all 41 registry entries, and the Crypto.com adapter import before upload. |
| CLI-only operation | `market_sentinel_cli.py` exposes market, alert, paper including durable position-mark refresh/clear, wallet, copy, analytics, durable leaderboard scan/status/export, Live Safety report/review/decision/proposal artifacts, and server commands without Tkinter. | `tests/test_cli.py` and the full verifier cover CLI parsing, local paper marks and report artifacts, state handling, exports, recovery, and status. |

## Polymarket Evidence Tiers

Polymarket public and local safety paths are implemented and tested. The
application deliberately does not promote a tier based only on local fixtures,
a readiness check, or a browser smoke.

| Tier | Current evidence | Status |
| --- | --- | --- |
| `wrapper_available` | Official Gamma/Data/CLOB/Bridge wrappers and adapter paths. | Locally implemented. |
| `app_workflow_available` | GUI, API, CLI, alerts, paper, wallet tracking, guarded copy, MDD analytics, durable scans, and redacted validation reports. | Locally implemented. |
| `offline_tested` | Fixtures, parser tests, API tests, report-schema/replay/promotion tests, and browser smoke. | Verified locally. |
| `public_live_verified` | Safe public probes can be recorded by `scripts/verify_polymarket_live.py`. | Environment-dependent; not inferred from tests. |
| `credential_live_verified` | Requires a real authenticated CLOB/relayer read or authenticated user WebSocket report. | Requires user credentials and an eligible account. |
| `funded_live_verified` | Requires an explicitly approved, allow-listed funded order/cancel audit. | Requires user approval, a funded eligible account, and live parameters. |

The report schema, promotion guard, review bundle, decision ledger, proposal,
and snapshot archive ensure that incomplete local evidence cannot be promoted to
credentialed or funded verification. See the `POLYMARKET_LIVE_REPORT_*.md`
documents in this directory.

### Observed Public-Live Evidence

On 2026-07-15, 2026-07-18, and 2026-07-19, the following public-only command
exited successfully from this workspace:

```powershell
python scripts/verify_polymarket_live.py --skip-authenticated-read-checks --timeout 15
```

It returned `status=ok` for Gamma `/markets`, Data `/v1/leaderboard`, CLOB
`/time`, and Bridge `/supported-assets`. No authenticated read, user WebSocket,
bridge-address creation, credential derivation, funded order, or cancellation
flag was supplied. This is point-in-time public endpoint evidence only; it does
not change the credentialed or funded tier status.

## Open External Evidence Gates

These are deliberately not replaced with simulated success:

1. Polymarket authenticated read/user-WebSocket proof requires user-provided
   credentials through local environment variables and a region/KYC-eligible
   account.
2. Polymarket funded order/cancel proof requires explicit approval, a funded
   account, and an allow-listed token, price, and size.
3. Full platform-support claims require successful runs on the named physical or
   hosted runners documented in [`PLATFORM_SUPPORT.md`](PLATFORM_SUPPORT.md),
   including the optional Windows 10 self-hosted lane and the declared
   enterprise-Linux/BSD/Solaris evidence. CI configuration alone is not proof
   that those hosts have run successfully.
4. Verified-blocked markets need official APIs, permissions, entitlements, or
   documented contracts before their blockers can be cleared. The exact
   market-by-market prerequisites are maintained in [`BLOCKERS.md`](BLOCKERS.md).

## Repeatable Local Verification

Run the strict local evidence set from the repository root:

```powershell
python verify.py --frontend-build --frontend-live-smoke
```

This validates local implementation and regression coverage, but it must not be
used as evidence for any of the external gates above.
