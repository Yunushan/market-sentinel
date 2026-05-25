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
