# Goal: Multi-market prediction-market support

## Objective

Refactor the existing `MarketSentinel` app into a market-adapter based application and add configurable support for these markets:

- Polymarket
- Kalshi
- PredictIt
- Robinhood Prediction Markets
- Fanatics Markets
- DraftKings Predictions
- Interactive Brokers ForecastTrader / IBKR Prediction Markets
- ForecastEx
- CME Group Prediction Markets
- Nadex
- Crypto.com Predict / CDNA
- Hyperliquid
- Myriad Markets
- Context V2
- Frenzy Finance
- XO Market
- Manifold Markets
- Metaculus
- Good Judgment Open
- Hypermind
- Iowa Electronic Markets
- INFER / INFER-pub
- Fact Machine
- Opinion Labs
- Gemini Titan / Gemini Predictions
- Augur
- BetMGM
- PrizePicks
- Underdog Sports
- Drift BET
- Thales Market
- Hedgehog Markets
- Omen
- Zeitgeist
- Azuro
- SX Bet / SX Network
- Limitless Exchange
- Predict.fun
- Smarkets
- Betfair Exchange
- Probo

## Critical constraints

- Do not break existing working features.
- Keep Python support as >=3.10,<3.15, including Python 3.14.
- Do not hardcode API keys, cookies, credentials, private URLs, IPs, domains, tokens, or user data.
- Use official APIs/SDKs/documented endpoints only.
- If a platform has no reliable/official API support, create a graceful stub adapter with clear "unsupported" messages.
- Do not bypass authentication, scrape private data, or implement risky live trading by default.
- Live trading/copy trading must be disabled by default.
- Paper/dry-run mode must be the default.

## Implementation requirements

Create a market adapter architecture with:

- Common adapter interface
- Market discovery
- Event/contract listing
- Price/orderbook/odds reading where supported
- Alert support
- Paper trading support where possible
- Live trading capability flag
- Copy-trade capability flag
- Per-market config enable/disable
- Clear unsupported-feature errors

Add or update:

- GUI market selector
- Config files/examples
- README capability matrix
- Tests for adapter interface
- Offline fixture tests
- verify.py checks
- docs/BLOCKERS.md for platforms that cannot be fully implemented without official API access, KYC, credentials, or regional access

## Done when

- Existing Polymarket features still work.
- App starts successfully.
- All listed markets appear in config and GUI.
- Each market has either a working adapter or a documented stub adapter.
- README includes a capability matrix showing: alerts, read-only data, paper trading, live trading, copy trading, API required, credentials required, region/KYC limitation.
- `python verify.py` passes.
- `pytest` passes if tests exist.
- Any remaining blocker is documented in `docs/BLOCKERS.md` with exact reason.

## Full implementation policy

The next implementation pass must move beyond "cataloged stub" support.

- "100% implemented" means the adapter uses official APIs, SDKs, documented endpoints, or documented protocol contracts for every feature it marks supported.
- If a market has no official/documented API, or requires unavailable KYC, account permissions, credentials, paid data entitlements, regional access, or private consumer-app endpoints, the app must not fake support. The correct implementation is a verified unsupported adapter with exact blocker documentation.
- The README matrix must not use `TBD`. Every cell must be `Yes`, `No`, `Required`, `Not required`, `Blocked`, `N/A`, or a similarly concrete value.
- Live trading and copy trading remain disabled by default for every market.
- Paper/dry-run mode remains the default for every market.
- Every adapter implementation must include offline fixture tests and `verify.py` coverage.

## Current implementation status

This project is not 100% feature-complete across every listed market. The completed baseline means every listed market is represented in the catalog/config/GUI/docs and each market has either a working adapter or a verified unsupported adapter with blocker documentation.

Current catalog snapshot:

- Total markets: 41
- Implemented or partially implemented adapters: 17
- Verified-blocked/stub adapters: 24
- Alerts supported: 16 yes, 25 no
- Read-only price data supported: 16 yes, 25 no
- Paper trading supported: 15 yes, 26 no
- Live trading supported: 11 guarded/off by default, 30 no
- Copy trading supported: 1 yes, 40 no

Important status rules:

- `100%` on an article means the article's scoped objective is complete, tested, and documented.
- `100%` does not mean every feature is available on every market.
- Polymarket official API coverage must be reported by tier: `wrapper_available`, `app_workflow_available`, `offline_tested`, `public_live_verified`, `credential_live_verified`, and `funded_live_verified`.
- A Polymarket category must not be described as fully production-verified unless every applicable tier is `yes`.
- Unsupported markets must remain unsupported unless official APIs, documented protocol contracts, credentials, entitlements, account permissions, regional access, and legal/KYC requirements make implementation safe.
- The app must never fake market support, scrape private consumer apps, bypass authentication, or enable risky live/copy trading by default.

## Completed baseline articles

These articles completed the catalog, adapter architecture, verification, docs, and implemented support where official/documented access was available:

- Article 1 Adapter Foundation: scope complete
- Article 2 Polymarket Adapter Migration: scope complete
- Article 3 Market Stub Adapters: scope complete
- Article 4 GUI Market Selector: scope complete
- Article 5 Config Examples: scope complete
- Article 6 README Capability Matrix: scope complete
- Article 7 Blockers Documentation: scope complete
- Article 8 Verification Expansion: scope complete
- Article 9 Final Integration Pass: scope complete
- Article 10 Capability De-TBD Pass: scope complete
- Article 11 Adapter Runtime Infrastructure: scope complete
- Article 12 Polymarket Production Hardening: scope complete
- Article 13 Kalshi Adapter: scope complete
- Article 14 Manifold Markets Adapter: scope complete
- Article 15 Metaculus Adapter: scope complete
- Article 16 Public Forecasting Adapters: scope complete
- Article 17 PredictIt Adapter: scope complete
- Article 18 Limitless Exchange Adapter: scope complete
- Article 19 SX Bet / SX Network Adapter: scope complete
- Article 20 Azuro Adapter: scope complete
- Article 21 Legacy Web3 Protocol Adapters: scope complete
- Article 22 Web3 Sports/DeFi Protocol Adapters: scope complete
- Article 23 Broker/Exchange Regulated Adapters: scope complete
- Article 24 Consumer-App / Sportsbook Entrants: scope complete
- Article 25 Global Betting/Opinion Exchanges: scope complete
- Article 26 GUI Full-Market Workflow: scope complete
- Article 27 All-Market Catalog and Blocker Verification: scope complete

## Continuation article status

- Article 28 React GUI Foundation and API Parity: Python/API scope complete; React source wired to the state API; frontend build verification completed in Article 51.
- Article 29 React Market Operations Parity: Python/API scope complete; React source wired for market selector, enable/disable, health/capability display, credential diagnostics, and safety setting persistence; frontend build verification completed in Article 51.
- Article 30 React Paper Trading Parity: Python/API scope complete; React source wired for quote preview, side-aware quote limit fill, paper order submission, impact preview, history refill, position refill, exposure summary, mark refresh, selected mark refresh, selected/full mark clearing, and history clearing; frontend build verification completed in Article 51.
- Article 31 React Alert Workflow Parity: Python/API scope complete; React source wired for create/edit/delete alerts, enable/disable toggles, market-scoped source selection, adapter-backed alert status, and current price state display; frontend build verification completed in Article 51.
- Article 32 React Wallet and Copy-Trading Parity: Python/API scope complete; React source wired for wallet watch add/edit/delete/toggle, manual activity polling status, recent activity display, simulation-first copy settings, and guarded live-copy preflight preview; frontend build verification completed in Article 51.
- Article 33 React Live Safety Parity: Python/API scope complete; React source wired for selected-market live gate controls, kill switch, max size/notional caps, live acknowledgement, non-ordering live preflight, and redacted audit display; frontend build verification completed in Article 51.
- Article 34 Local API Hardening: scope complete; local API now returns structured error envelopes, validates JSON body size/content shape, avoids raw internal exception leakage, recursively redacts sensitive settings/audit details, and writes config atomically with endpoint and storage tests.
- Article 35 Adapter Capability Expansion Re-Audit: scope complete; verified-blocked markets were re-checked against current official sources, no market was safely promotable without production APIs/entitlements/wallet safeguards/fixtures, and blocker notes were tightened for Context, Hyperliquid, Thales, Smarkets, CME, and consumer-app products.
- Article 36 Packaging and Launch UX: scope complete; Windows smart/dev/prod/build launch scripts now document and enforce React setup paths, `web_api.py` exposes React build/dev/prod launch metadata and serves built `frontend/dist` assets with SPA fallback, and Tkinter remains the explicit fallback through `run_gui.bat` or `python app.py`.
- Article 37 Final Parity Verification: Python/Tkinter/API scope complete; `python app.py --smoke-test`, `python -m pytest`, and `python verify.py` pass with explicit Tkinter fallback, React workflow API parity, launch UX, and frontend build-readiness checks. Strict React build/browser verification completed in Article 51.
- Article 38 Polymarket User Analytics: Python/API/React source scope complete; public profile search and Data API leaderboard scanning are wired with computed ROI %, PnL/volume/ROI min-max filters, docs, and focused unit tests. Strict React build/browser verification completed in Article 51.
- Article 39 Polymarket Real MDD Analytics: Python/API/React source scope complete; user and leaderboard analytics compute MDD USD/% from public closed positions plus current open-position snapshots, support MDD sorting/filtering, expose the MDD percentage basis, update docs, and include focused unit tests. Strict React build/browser verification completed in Article 51.
- Article 40 Polymarket Official API Coverage: Python/API scope complete; official Gamma, Data, CLOB, Bridge, Relayer, and WebSocket surfaces are represented by public or guarded modules, authenticated endpoints require explicit credentials/headers, `GET /api/polymarket/coverage` reports coverage and live-validation requirements, README is updated, and focused unit tests cover request construction and guardrails. Strict live workflow validation remains pending real Polymarket credentials, eligible region/KYC status, funded wallets, and explicit live-mode opt-in.
- Article 41 Polymarket Truthful Coverage Model: scope complete; `/api/polymarket/coverage`, README, and GOAL now distinguish wrapper availability, app workflow availability, offline tests, public live verification, credentialed live verification, and funded live verification so the project cannot overclaim 100% production support before credentials and live checks are actually complete.
- Article 42 Polymarket Official Endpoint Contract Hardening: scope complete; official REST wrappers now share central endpoint metadata and HTTP behavior, use typed Polymarket errors, validate documented batch caps instead of silently truncating, retry safe transient public reads, normalize response contracts through shared helpers, document the contract layer, and include focused regression tests.
- Article 43 Polymarket Authenticated CLOB v2 Readiness: scope complete; authenticated CLOB setup now has redacted readiness validation for private keys, signature types, funder/deposit wallets, Polygon chain id, official CLOB host, L1/L2 header presence, adapter health, local API payloads, and safe live-verification reporting without deriving credentials or placing funded orders by default.
- Article 44 Polymarket Safe Live Order/Cancel Verification Harness: scope complete; funded verification now defaults to a dry-run transcript and actual execution requires explicit live flags, exact confirmation text, token allow-listing, GTC order type, hard size/notional caps, credential readiness, public maker-side orderbook preflight, immediate cancel, post-cancel order fetch verification, and redacted audit output.
- Article 45 Polymarket True Historical MDD v2: scope complete; MDD analytics now use a versioned public-data equity-curve builder from closed positions, current open positions, activity, and trades, expose USD/% drawdown with trade/activity capital-basis context, pagination controls, process-memory public-data cache boundaries, assumptions/limitations, React filter controls, coverage metadata, docs, and focused tests.
- Article 46 Polymarket Historical Mark Replay MDD: scope complete; optional `mark_replay` MDD mode now replays trade-derived token inventory against public CLOB batch price history, enforces the documented 20-token batch cap, reports missing/clipped/unreconstructable token rows and negative inventory, falls back to MDD v2 when replay is unavailable, exposes React controls, updates coverage/docs, and includes focused tests.
- Article 47 Polymarket Accounting Snapshot Reconciliation: scope complete; optional accounting snapshot parsing now reads the public ZIP CSV payload, extracts equity/position/cash-flow evidence, uses max statement equity as the strongest available MDD percentage base, reports deposits, withdrawals, cash-flow gaps, current-value and realized-PnL reconciliation deltas, updates API/React controls, docs, coverage metadata, and includes local ZIP fixture tests.
- Article 48 Polymarket Analytics Cache, Rate-Limit, and Export Hardening: scope complete; optional persistent bounded analytics cache metadata now stores per-wallet MDD audit artifacts for expensive leaderboard/MDD/accounting scans, API payloads expose Polymarket rate-limit/backoff state, JSON/CSV export endpoints serve cached MDD artifacts without rerunning public API calls, React exposes cache toggles and download links, docs/coverage are updated, and focused tests cover cache, exports, and rate-limit behavior.
- Article 49 Polymarket Analytics Detail UX and Browser Verification: scope complete; React now has direct wallet MDD lookup/export controls, profile-search wallet handoff, cached MDD audit detail loading from JSON artifacts, point previews, audit metadata, docs, regression tests, and Article 51 browser smoke coverage.
- Article 50 Polymarket Analytics Cache Management: scope complete; cache health/listing/purge helpers now expose retention metadata for stored Polymarket MDD audit artifacts, local API routes list cache contents, report health, purge expired/all/selected artifacts, React Analytics adds cache management controls, README documents the endpoints, regression tests cover list/health/purge behavior, and Article 51 browser smoke coverage verifies the cache view.
- Article 51 Frontend Dependency and Browser Verification Unblocker: scope complete; frontend dependencies were installed, `npm run build` now performs type-check-only TypeScript validation without generating shadow Vite config files, `python verify.py --frontend-build` passes with 291 tests, `verify.py` resolves `npm.cmd` on Windows, React tabs can be opened by URL for deterministic smoke routes, and local headless Edge smoke verified overview, markets, analytics, live safety, alerts, wallets/copy, paper trading, settings, `/api/state`, and Polymarket MDD cache fetches.
- Article 52 Polymarket Live Credential Validation Gate: local scope complete; `scripts/verify_polymarket_live.py` now emits explicit stage gates for public probes, redacted credential readiness, non-destructive authenticated reads, user WebSocket checks, bridge address checks, and funded order/cancel status; strict `--require-authenticated-read-ok` fails closed until a real authenticated read/stream succeeds; optional `--include-user-websocket-connect` probes the authenticated user WebSocket without returning secrets; `--report-file` writes durable JSON audit output; docs and tests cover the workflow. Real credentialed/funded verification remains blocked until user-provided credentials, eligible region/account state, funded wallet status, and explicit live-action approval are available.
- Article 53 Polymarket Live Validation API and React Surface: scope complete; `/api/polymarket/live-validation` now exposes a local no-funded-actions stage-gate report with generated timestamp, redacted credential presence, CLOB readiness, public/authenticated/bridge/funded check statuses, operator CLI audit commands, and the next blocked step; `/api/state` includes the report; React Live Safety renders and refreshes it without funded execution controls; README and tests cover the route and UI source.
- Article 54 Polymarket Live Validation Report Persistence: scope complete; `polymarket.live_reports` now stores bounded redacted JSON snapshots, `/api/state` includes report inventory, `GET/POST/DELETE /api/polymarket/live-validation/reports` list/import/store/delete GUI and CLI validation reports, React Live Safety can store the current snapshot, import CLI JSON, list reports, delete reports, and compare the latest two stage-gate summaries, and tests/docs verify secret redaction and no funded GUI execution.
- Article 55 Polymarket Live Validation Report Export and HTTP Smoke: scope complete; stored reports can now be opened by key through `GET /api/polymarket/live-validation/reports/{key}` and downloaded through `GET /api/polymarket/live-validation/reports/{key}/export.json`; React Live Safety adds Open and JSON controls plus opened-report details; HTTP tests exercise report POST/list/open/export/delete and 404 behavior; docs and source-parity tests cover the UI/API contract; `python verify.py --frontend-build` passes with 297 tests; and a temporary local Edge headless smoke verified the built Live Safety report-history controls render with a seeded report and export route.
- Article 56 Polymarket Live Validation Browser Smoke Automation: scope complete; `scripts/verify_live_validation_report_smoke.py` now starts a temporary local React API/server with temporary config/report storage, seeds a deterministic redacted report, verifies report open/export APIs, drives local Chromium/Edge headless through CDP until the built Live Safety report-history controls render, checks that seeded secrets are not exposed, and `verify.py` exposes `--frontend-live-smoke`; docs and source-parity tests cover the reusable no-credentials/no-funded-actions workflow.
- Article 57 Polymarket Credentialed Read Runbook and Secret Hygiene: scope complete; `polymarket.credential_runbook` and `scripts/verify_polymarket_credentials.py` now provide a local no-network/no-funded-actions credential inventory for SDK trading credentials, CLOB L2/L1 headers, user WebSocket auth, relayer headers, and builder headers; reports redact secrets, expose exact operator commands, support strict local readiness gates, feed the live-validation API/CLI reports, update coverage/docs, and are covered by verifier and regression tests.
- Article 58 Polymarket Credentialed Report Promotion Guard: scope complete; stored/imported live-validation reports now include a `verification_promotion` assessment that only allows `credential_live_verified=yes` with concrete authenticated-read or user-WebSocket evidence, only allows `funded_live_verified=yes` with a real funded order/cancel audit containing `live_action=true`, order id, placed/cancel/post-cancel sections, and post-cancel verification, blocks local-only runbook/browser-smoke/readiness reports from promotion, exposes guarded evidence candidates on `/api/polymarket/coverage` without mutating static coverage tiers, and shows credential/funded tier status in the React report UI with tests/docs.
- Article 59 Polymarket Live Validation Report Schema and Import Validation: scope complete; `polymarket.live_report_schema` now defines accepted report modes and shape validation, stored/imported reports are validated before redaction/disk write and carry compact `schema_validation` metadata, malformed HTTP imports return `live_validation_report_schema_error` with structured errors/warnings, deterministic valid/invalid fixtures cover credentialed-read, funded-audit, dry-run, runbook, and browser-smoke cases, `verify.py` validates the fixture contract, docs formalize the accepted shape, and the Live Safety browser smoke is more robust against blank-shell headless startup races.
- Article 60 Polymarket Live Validation Import UX Schema Details: scope complete; React Live Safety now shows accepted live-report schema mode guidance, carries structured schema diagnostics through typed API errors, surfaces validation errors/warnings from rejected imports without storing malformed reports, displays schema status for stored/opened reports, preserves schema metadata in opened/exported report details, and the browser smoke now checks schema UI fragments plus a malformed import rejection/no-storage path.
- Article 61 Polymarket Live Validation Report Replay CLI: scope complete; `polymarket.live_report_replay` and `scripts/replay_polymarket_live_reports.py` now validate one or more local report JSON files without network or funded actions, print schema diagnostics plus guarded credential/funded promotion summaries, support `--json`, `--fail-on-warning`, and optional redacted `--import` into the local report store, skip invalid files from import, preserve no raw payloads in replay output, and are covered by fixture replay tests, CLI tests, docs, and `verify.py`.
- Article 62 Polymarket Live Validation Report Provenance and Deduplication: scope complete; stored and replayed reports now carry stable SHA-256 hashes of redacted canonical payloads plus source-file provenance, duplicate imports are skipped by default while recording duplicate audit events, explicit API/UI/CLI allow-duplicate paths retain separate audit entries with `duplicate_of` provenance, React surfaces hash/source/duplicate state, docs describe the policy, and focused tests plus `python verify.py --frontend-build --frontend-live-smoke` cover the workflow.
- Article 63 Polymarket Live Validation Report Promotion Review Package: scope complete; stored reports now export sanitized JSON and Markdown review bundles containing schema status, redacted payload hash/provenance, duplicate history, guarded promotion evidence/blockers, source CLI commands, and coverage-tier mapping while keeping `static_coverage_mutated=false` and excluding raw payloads/secrets; API routes, React download links, docs, focused tests, verifier checks, and browser smoke cover the workflow.
- Article 64 Polymarket Live Validation Promotion Decision Ledger: scope complete; promotion decisions now live in a separate no-secrets ledger requiring report key, redacted payload hash, target tier, accepted/rejected decision, reviewer note, and deterministic review-bundle hash; mismatched payload/review hashes fail closed, blocked credential/funded tiers cannot be accepted without qualifying evidence, API/React/CLI-style JSON and Markdown exports are wired, docs and browser smoke cover the workflow, and `python verify.py --frontend-build --frontend-live-smoke` passes with 323 tests.
- Article 65 Polymarket Live Validation Coverage Promotion Proposal Export: scope complete; accepted promotion-decision ledger entries now produce a guarded no-automerge JSON/Markdown coverage/docs proposal with current review-bundle revalidation, stale-decision detection, human-review gates, CLI/API/React download surfaces, docs, focused tests, verifier checks, and no static coverage mutation.
- Article 66 Polymarket Live Validation Promotion Proposal React Preview: scope complete; the React Live Safety tab now has a read-only Promotion Proposal Preview with target-tier filtering, accepted/stale/ignored counts, review-gate display, accepted/stale/change tables, filtered JSON/Markdown downloads, no apply/automerge action, docs, browser-smoke fragments, source parity tests, and frontend build coverage.
- Article 67 Polymarket Live Validation Promotion Proposal Snapshot Archive: scope complete; promotion proposals can now be stored as bounded no-secrets snapshots with proposal hashes, provenance, current/stale hash checks, list/open/delete/export API routes, React archive controls, docs, smoke coverage, verifier checks, and regression tests while keeping static coverage unmutated.

## Active continuation goals

When continuing article by article, complete each task to 100% of its scoped objective with tests and docs. Do not reinterpret blocked markets as implemented unless the official-access constraints are solved.

- Article 68 Polymarket Live Validation Promotion Proposal Snapshot Diff Review: add current-vs-snapshot diff summaries for proposal counts, accepted/stale decisions, proposed files, review gates, and proposal hashes across API/React/Markdown exports, with docs, verifier checks, and tests.
- Real Polymarket credential/funded execution: blocked until the user supplies credentials through local environment variables or an approved secret mechanism, confirms eligible region/KYC/account status, provides a safe token/price/size allow-list, and explicitly approves each funded live check.

## Non-negotiable remaining blockers

Some `No` cells may remain permanently correct. A market capability stays blocked when any of these is true:

- No official API, SDK, export, or documented protocol contract exists.
- Access requires unavailable paid entitlements, private account permissions, region/KYC eligibility, or broker/exchange approval.
- Only private mobile/web consumer endpoints are visible.
- Automation would require scraping authenticated sessions, bypassing controls, or storing unsafe credentials.
- Copy trading would require account activity data that the platform does not officially expose.
