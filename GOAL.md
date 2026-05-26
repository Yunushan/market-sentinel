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

## Full implementation articles

Completed articles:

- Article 1 Adapter Foundation: 100%
- Article 2 Polymarket Adapter Migration: 100%
- Article 3 Market Stub Adapters: 100%
- Article 4 GUI Market Selector: 100%
- Article 5 Config Examples: 100%
- Article 6 README Capability Matrix: 100%
- Article 7 Blockers Documentation: 100%
- Article 8 Verification Expansion: 100%
- Article 9 Final Integration Pass: 100%
- Article 10 Capability De-TBD Pass: 100%
- Article 11 Adapter Runtime Infrastructure: 100%
- Article 12 Polymarket Production Hardening: 100%
- Article 13 Kalshi Adapter: 100%
- Article 14 Manifold Markets Adapter: 100%
- Article 15 Metaculus Adapter: 100%
- Article 16 Public Forecasting Adapters: 100%
- Article 17 PredictIt Adapter: 100%
- Article 18 Limitless Exchange Adapter: 100%
- Article 19 SX Bet / SX Network Adapter: 100%
- Article 20 Azuro Adapter: 100%
- Article 21 Legacy Web3 Protocol Adapters: 100%
- Article 22 Web3 Sports/DeFi Protocol Adapters: 100%
- Article 23 Broker/Exchange Regulated Adapters: 100%
- Article 24 Consumer-App / Sportsbook Entrants: 100%
- Article 25 Global Betting/Opinion Exchanges: 100%
- Article 26 GUI Full-Market Workflow: 100%
- Article 27 Final All-Market Verification: 100%
