# prediction-market-alert-and-copy-trade-gui

A local multi-market prediction-market GUI app (Tkinter) for:
- **Price alerts** (token price triggers)
- **Wallet / username tracking** (monitor on-chain activity via Data API)
- **Copy-trading (optional)** (mirror trades with safety limits)

> ⚠️ Disclaimer  
> This is a developer MVP. It is **not financial advice** and it can lose money.  
> Only use each market in ways that comply with that market's terms and your local laws/regulations.
> The currently implemented Polymarket trading path performs a **geoblock check** and will refuse to trade if blocked.

## Features (what works today)

### 1) Price triggers
- Subscribe to CLOB WebSocket market channel
- Alerts on **last trade price** changes (and basic midpoint support via REST)

### 2) Username / wallet address tracker
- Paste a **0x wallet/proxyWallet** OR search a **Polymarket username/pseudonym**
- Polls the Data API `/activity` endpoint and alerts on new `TRADE` entries

### 3) Copy trading (paper mode by default)
- Follows a tracked wallet’s **BUY** trades (SELL optional, guarded)
- Default mode is **SIMULATION** (logs what it *would* do)
- Enable **LIVE** mode only if you know what you’re doing

### 4) Kalshi adapter support
- Lists Kalshi events/contracts through official REST market-data endpoints
- Reads binary orderbooks and derives YES/NO best bid/ask prices
- Supports dry-run/paper orders; live orders are opt-in and require signed API credentials

### 5) Manifold adapter support
- Searches and lists Manifold markets through the official API
- Reads binary and multiple-choice probabilities for alerts
- Supports paper orders locally and guarded MANA betting through documented API-key auth

## Install & Run

Requires **Python >=3.10,<3.15**. Python **3.14** is supported and verified.

### 1) Create a venv (recommended)
```bash
python -m venv .venv
source .venv/bin/activate  # (macOS/Linux)
# .venv\Scripts\activate  # (Windows)
```

### 2) Install deps
```bash
pip install -r requirements.txt
```

### 3) (Optional) set up LIVE trading credentials
Copy `.env.example` to `.env` and fill values:
```bash
cp .env.example .env
```

Configuration examples:
- `data/config.example.json` lists every supported market id, default enablement, and per-market settings.
- `data/config.json` is local state and is intentionally gitignored.
- Keep credentials in `.env` or your shell environment; config examples only reference env var names.

### 4) Start the GUI
```bash
python app.py
```

On Windows you can also double-click `run_gui.bat`. It uses `.venv` when it is healthy and falls back to the Python launcher.

## Market Capability Matrix

This matrix describes current application adapter support. Stub markets appear in the GUI and config, but their market-specific operations intentionally return clear unsupported-feature messages until an official adapter is implemented. For stub rows, the last three columns describe what is required before safe official support can be implemented or operated.

| Market | Adapter | Alerts | Read-only data | Paper trading | Live trading | Copy trading | API required | Credentials required | Region/KYC limitation |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Polymarket (`polymarket`) | Implemented | Yes | Yes | Yes | Guarded, off by default | Yes, dry-run default | Yes | Live trading only | Trading may be region/KYC limited |
| Kalshi (`kalshi`) | Implemented | Yes | Yes | Yes | Guarded, off by default | No | Required | Exchange account/API keys | Region/KYC limited |
| PredictIt (`predictit`) | Stub | No | No | No | No | No | Required | Account required for trading | Region/account limited |
| Robinhood Prediction Markets (`robinhood_prediction_markets`) | Stub | No | No | No | No | No | Required | Brokerage account required | Region/KYC limited |
| Fanatics Markets (`fanatics_markets`) | Stub | No | No | No | No | No | Required | Account required | Region/KYC limited |
| DraftKings Predictions (`draftkings_predictions`) | Stub | No | No | No | No | No | Required | Account required | Region/KYC limited |
| Interactive Brokers ForecastTrader / IBKR Prediction Markets (`ibkr_forecasttrader`) | Stub | No | No | No | No | No | Required | IBKR account required | Region/KYC limited |
| ForecastEx (`forecastex`) | Stub | No | No | No | No | No | Required | Exchange/broker account required | Region/KYC limited |
| CME Group Prediction Markets (`cme_prediction_markets`) | Stub | No | No | No | No | No | Required | Broker/data entitlement required | Region/KYC limited |
| Nadex (`nadex`) | Stub | No | No | No | No | No | Required | Exchange account required | Region/KYC limited |
| Crypto.com Predict / CDNA (`crypto_com_predict`) | Stub | No | No | No | No | No | Required | Crypto.com account required | Region/KYC limited |
| Hyperliquid (`hyperliquid`) | Stub | No | No | No | No | No | Required | Wallet required for trading | Jurisdiction varies |
| Myriad Markets (`myriad_markets`) | Stub | No | No | No | No | No | Required | Wallet required for trading | Jurisdiction varies |
| Context V2 (`context_v2`) | Stub | No | No | No | No | No | Required | API credentials required | Jurisdiction varies |
| Frenzy Finance (`frenzy_finance`) | Stub | No | No | No | No | No | Required | Wallet required for trading | Jurisdiction varies |
| XO Market (`xo_market`) | Stub | No | No | No | No | No | Required | Account required | Jurisdiction varies |
| Manifold Markets (`manifold`) | Implemented | Yes | Yes | Yes | Guarded, off by default | No | Required | Live trading only | Not KYC limited |
| Metaculus (`metaculus`) | Stub | No | No | No | No | No | Required | Account/API token required | Not trading/KYC limited |
| Good Judgment Open (`good_judgment_open`) | Stub | No | No | No | No | No | Required | Account/export access required | Not trading/KYC limited |
| Hypermind (`hypermind`) | Stub | No | No | No | No | No | Required | Program access required | Program access limited |
| Iowa Electronic Markets (`iowa_electronic_markets`) | Stub | No | No | No | No | No | Required | IEM account required | Eligibility limited |
| INFER / INFER-pub (`infer`) | Stub | No | No | No | No | No | Required | Account/export access required | Not trading/KYC limited |
| Fact Machine (`fact_machine`) | Stub | No | No | No | No | No | Required | Wallet/personhood required | Identity/jurisdiction limited |
| Opinion Labs (`opinion_labs`) | Stub | No | No | No | No | No | Required | Account or wallet required | Jurisdiction varies |
| Gemini Titan / Gemini Predictions (`gemini_titan`) | Stub | No | No | No | No | No | Required | Gemini account required | Region/KYC limited |
| Augur (`augur`) | Stub | No | No | No | No | No | Required | Wallet required for trading | Jurisdiction varies |
| BetMGM (`betmgm`) | Stub | No | No | No | No | No | Required | Account required | Region/KYC limited |
| PrizePicks (`prizepicks`) | Stub | No | No | No | No | No | Required | Account required | Region/KYC limited |
| Underdog Sports (`underdog_sports`) | Stub | No | No | No | No | No | Required | Account required | Region/KYC limited |
| Drift BET (`drift_bet`) | Stub | No | No | No | No | No | Required | Wallet required for trading | Jurisdiction varies |
| Thales Market (`thales_market`) | Stub | No | No | No | No | No | Required | Wallet required for trading | Jurisdiction varies |
| Hedgehog Markets (`hedgehog_markets`) | Stub | No | No | No | No | No | Required | Wallet required for trading | Jurisdiction varies |
| Omen (`omen`) | Stub | No | No | No | No | No | Required | Wallet required for trading | Jurisdiction varies |
| Zeitgeist (`zeitgeist`) | Stub | No | No | No | No | No | Required | Wallet required for trading | Jurisdiction varies |
| Azuro (`azuro`) | Stub | No | No | No | No | No | Required | Wallet required for trading | Jurisdiction varies |
| SX Bet / SX Network (`sx_bet`) | Stub | No | No | No | No | No | Required | Wallet required for trading | Jurisdiction varies |
| Limitless Exchange (`limitless_exchange`) | Stub | No | No | No | No | No | Required | Wallet required for trading | Jurisdiction varies |
| Predict.fun (`predict_fun`) | Stub | No | No | No | No | No | Required | Wallet required for trading | Jurisdiction varies |
| Smarkets (`smarkets`) | Stub | No | No | No | No | No | Required | Exchange account required | Region/KYC limited |
| Betfair Exchange (`betfair_exchange`) | Stub | No | No | No | No | No | Required | Exchange account required | Region/KYC limited |
| Probo (`probo`) | Stub | No | No | No | No | No | Required | Account required | Region limited |

## Verification

Project-level checks:
```bash
python verify.py
```

This runs:
- Python version check (`>=3.10,<3.15`, including 3.14)
- dependency import checks
- `pip check`
- `compileall`
- adapter catalog, config example, README matrix, and blocker documentation checks
- offline fixture JSON checks
- GUI market selector integration checks
- offline unit tests for config/storage, API wrapper parsing, alert crossing, copy-trade simulation sizing, and wallet activity de-duplication

Pytest is included in `requirements.txt`; run the pytest suite directly with:
```bash
python -m pytest
```

In-app checks:
- **About -> Check versions** compares installed dependency versions with PyPI.
- **Copy Trading -> Check Geoblock** verifies whether live trading should be blocked for the current location.
- Form validation guards wallet addresses, alert thresholds, and copy-trading numeric settings.
- Copy trading defaults to simulation mode and performs a geoblock check before live orders.

## Notes
- The app stores local settings in `data/config.json` (gitignored user-specific state).
- If you enable LIVE trading, do **not** store keys in plaintext beyond what you accept as your risk.
  Prefer env vars, a password manager, or OS keychain tooling.

## Project structure
- `app.py` – GUI + orchestration
- `polymarket/` – API wrappers + websocket client + trading wrapper
- `core/` – config models + storage helpers
- `data/` – local config and cache
