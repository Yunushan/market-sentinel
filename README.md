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

This matrix describes current application adapter support. Stub markets appear in the GUI and config, but their market-specific operations intentionally return clear unsupported-feature messages until an official adapter is implemented.

| Market | Adapter | Alerts | Read-only data | Paper trading | Live trading | Copy trading | API required | Credentials required | Region/KYC limitation |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Polymarket (`polymarket`) | Implemented | Yes | Yes | Yes | Guarded, off by default | Yes, dry-run default | Yes | Live trading only | Trading may be region/KYC limited |
| Kalshi (`kalshi`) | Stub | No | No | No | No | No | TBD | TBD | TBD |
| PredictIt (`predictit`) | Stub | No | No | No | No | No | TBD | TBD | TBD |
| Robinhood Prediction Markets (`robinhood_prediction_markets`) | Stub | No | No | No | No | No | TBD | TBD | TBD |
| Fanatics Markets (`fanatics_markets`) | Stub | No | No | No | No | No | TBD | TBD | TBD |
| DraftKings Predictions (`draftkings_predictions`) | Stub | No | No | No | No | No | TBD | TBD | TBD |
| Interactive Brokers ForecastTrader / IBKR Prediction Markets (`ibkr_forecasttrader`) | Stub | No | No | No | No | No | TBD | TBD | TBD |
| ForecastEx (`forecastex`) | Stub | No | No | No | No | No | TBD | TBD | TBD |
| CME Group Prediction Markets (`cme_prediction_markets`) | Stub | No | No | No | No | No | TBD | TBD | TBD |
| Nadex (`nadex`) | Stub | No | No | No | No | No | TBD | TBD | TBD |
| Crypto.com Predict / CDNA (`crypto_com_predict`) | Stub | No | No | No | No | No | TBD | TBD | TBD |
| Hyperliquid (`hyperliquid`) | Stub | No | No | No | No | No | TBD | TBD | TBD |
| Myriad Markets (`myriad_markets`) | Stub | No | No | No | No | No | TBD | TBD | TBD |
| Context V2 (`context_v2`) | Stub | No | No | No | No | No | TBD | TBD | TBD |
| Frenzy Finance (`frenzy_finance`) | Stub | No | No | No | No | No | TBD | TBD | TBD |
| XO Market (`xo_market`) | Stub | No | No | No | No | No | TBD | TBD | TBD |
| Manifold Markets (`manifold`) | Stub | No | No | No | No | No | TBD | TBD | TBD |
| Metaculus (`metaculus`) | Stub | No | No | No | No | No | TBD | TBD | TBD |
| Good Judgment Open (`good_judgment_open`) | Stub | No | No | No | No | No | TBD | TBD | TBD |
| Hypermind (`hypermind`) | Stub | No | No | No | No | No | TBD | TBD | TBD |
| Iowa Electronic Markets (`iowa_electronic_markets`) | Stub | No | No | No | No | No | TBD | TBD | TBD |
| INFER / INFER-pub (`infer`) | Stub | No | No | No | No | No | TBD | TBD | TBD |
| Fact Machine (`fact_machine`) | Stub | No | No | No | No | No | TBD | TBD | TBD |
| Opinion Labs (`opinion_labs`) | Stub | No | No | No | No | No | TBD | TBD | TBD |
| Gemini Titan / Gemini Predictions (`gemini_titan`) | Stub | No | No | No | No | No | TBD | TBD | TBD |
| Augur (`augur`) | Stub | No | No | No | No | No | TBD | TBD | TBD |
| BetMGM (`betmgm`) | Stub | No | No | No | No | No | TBD | TBD | TBD |
| PrizePicks (`prizepicks`) | Stub | No | No | No | No | No | TBD | TBD | TBD |
| Underdog Sports (`underdog_sports`) | Stub | No | No | No | No | No | TBD | TBD | TBD |
| Drift BET (`drift_bet`) | Stub | No | No | No | No | No | TBD | TBD | TBD |
| Thales Market (`thales_market`) | Stub | No | No | No | No | No | TBD | TBD | TBD |
| Hedgehog Markets (`hedgehog_markets`) | Stub | No | No | No | No | No | TBD | TBD | TBD |
| Omen (`omen`) | Stub | No | No | No | No | No | TBD | TBD | TBD |
| Zeitgeist (`zeitgeist`) | Stub | No | No | No | No | No | TBD | TBD | TBD |
| Azuro (`azuro`) | Stub | No | No | No | No | No | TBD | TBD | TBD |
| SX Bet / SX Network (`sx_bet`) | Stub | No | No | No | No | No | TBD | TBD | TBD |
| Limitless Exchange (`limitless_exchange`) | Stub | No | No | No | No | No | TBD | TBD | TBD |
| Predict.fun (`predict_fun`) | Stub | No | No | No | No | No | TBD | TBD | TBD |
| Smarkets (`smarkets`) | Stub | No | No | No | No | No | TBD | TBD | TBD |
| Betfair Exchange (`betfair_exchange`) | Stub | No | No | No | No | No | TBD | TBD | TBD |
| Probo (`probo`) | Stub | No | No | No | No | No | TBD | TBD | TBD |

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
