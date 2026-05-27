# Goal: Multi-market prediction-market support

## Objective

Refactor the existing `prediction-market-alert-and-copy-trade-gui` into a market-adapter based application and add configurable support for these markets:

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

- Article 28 React GUI Foundation and API Parity: Python/API scope complete; React source wired to the state API; frontend build verification pending successful `npm install`.
- Article 29 React Market Operations Parity: Python/API scope complete; React source wired for market selector, enable/disable, health/capability display, credential diagnostics, and safety setting persistence; frontend build verification pending successful `npm install`.
- Article 30 React Paper Trading Parity: Python/API scope complete; React source wired for quote preview, side-aware quote limit fill, paper order submission, impact preview, history refill, position refill, exposure summary, mark refresh, selected mark refresh, selected/full mark clearing, and history clearing; frontend build verification pending successful `npm install`.
- Article 31 React Alert Workflow Parity: Python/API scope complete; React source wired for create/edit/delete alerts, enable/disable toggles, market-scoped source selection, adapter-backed alert status, and current price state display; frontend build verification pending successful `npm install`.
- Article 32 React Wallet and Copy-Trading Parity: Python/API scope complete; React source wired for wallet watch add/edit/delete/toggle, manual activity polling status, recent activity display, simulation-first copy settings, and guarded live-copy preflight preview; frontend build verification pending successful `npm install`.
- Article 33 React Live Safety Parity: Python/API scope complete; React source wired for selected-market live gate controls, kill switch, max size/notional caps, live acknowledgement, non-ordering live preflight, and redacted audit display; frontend build verification pending successful `npm install`.
- Article 34 Local API Hardening: scope complete; local API now returns structured error envelopes, validates JSON body size/content shape, avoids raw internal exception leakage, recursively redacts sensitive settings/audit details, and writes config atomically with endpoint and storage tests.
- Article 35 Adapter Capability Expansion Re-Audit: scope complete; verified-blocked markets were re-checked against current official sources, no market was safely promotable without production APIs/entitlements/wallet safeguards/fixtures, and blocker notes were tightened for Context, Hyperliquid, Thales, Smarkets, CME, and consumer-app products.
- Article 36 Packaging and Launch UX: scope complete; Windows smart/dev/prod/build launch scripts now document and enforce React setup paths, `web_api.py` exposes React build/dev/prod launch metadata and serves built `frontend/dist` assets with SPA fallback, and Tkinter remains the explicit fallback through `run_gui.bat` or `python app.py`.
- Article 37 Final Parity Verification: Python/Tkinter/API scope complete; `python app.py --smoke-test`, `python -m pytest`, and `python verify.py` pass with explicit Tkinter fallback, React workflow API parity, launch UX, and frontend build-readiness checks. Strict React build/browser verification remains blocked in this environment because `npm install` timed out and `frontend/node_modules` plus `package-lock.json` are absent.
- Article 38 Polymarket User Analytics: Python/API/React source scope complete; public profile search and Data API leaderboard scanning are wired with computed ROI %, PnL/volume/ROI min-max filters, explicit unavailable MDD fields, docs, and focused unit tests. Strict React build/browser verification remains pending successful `npm install`.

## Active continuation goals

When continuing article by article, complete each task to 100% of its scoped objective with tests and docs. Do not reinterpret blocked markets as implemented unless the official-access constraints are solved.

- Frontend dependency unblocker: complete `cd frontend && npm install` or `build_web_gui.bat` in an environment where npm can finish, then run `npm run build`, `python verify.py --frontend-build`, and local browser smoke tests for the React state, markets, analytics, paper trading, alerts, wallets/copy, and live safety views.

## Non-negotiable remaining blockers

Some `No` cells may remain permanently correct. A market capability stays blocked when any of these is true:

- No official API, SDK, export, or documented protocol contract exists.
- Access requires unavailable paid entitlements, private account permissions, region/KYC eligibility, or broker/exchange approval.
- Only private mobile/web consumer endpoints are visible.
- Automation would require scraping authenticated sessions, bypassing controls, or storing unsafe credentials.
- Copy trading would require account activity data that the platform does not officially expose.
