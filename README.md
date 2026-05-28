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
- Polymarket alerts subscribe to the CLOB WebSocket market channel
- Polymarket adapter-backed alert refresh reads CLOB last-trade, midpoint, best bid, and best ask state
- Other implemented adapters can load events/contracts from the selected market and poll official price endpoints for adapter-backed alerts
- Alerts support **last trade / last value**, midpoint, best bid, and best ask sources where the selected adapter exposes them
- The React Alerts tab can create, edit, enable/disable, delete, and refresh market-scoped alerts against the local Python API
- The React Alerts tab shows adapter-backed status plus current last/midpoint/bid/ask state for each alert

### 2) Username / wallet address tracker
- Paste a **0x wallet/proxyWallet** OR search a **Polymarket username/pseudonym**
- Polls the Data API `/activity` endpoint and alerts on new `TRADE` entries
- The React Wallets tab can add, edit, enable/disable, delete, and manually poll tracked wallets
- The React Wallets tab shows recent activity cached by the local API session, including simulation copy previews

### 3) Polymarket user analytics
- Search public Polymarket profiles by username/pseudonym and return proxy wallets for tracking or copy setup
- Load public leaderboard rows and rank them by PnL USD, volume USD, or computed ROI %
- The default ROI view returns up to the top 100 rows from as many as 500 scanned public leaderboard rows
- Min/max filters are available for PnL USD, volume USD, and ROI %
- MDD USD/% v2 can be computed from a public-data historical equity curve: closed-position realized PnL, public activity/trade capital basis, and the current open-position snapshot
- MDD v2 supports min/max filters, MDD sorting, pagination controls for closed positions/activity/trades/open positions, and an optional `equity_base_usd` override
- Optional `mdd_mode=mark_replay` replays trade-derived token inventory against public CLOB batch price history for deeper sampled unrealized drawdown checks
- Mark replay is capped to 20 asset ids per request, reports missing/clipped/unreconstructable rows, and falls back to MDD v2 when replay cannot be built
- Optional accounting snapshot reconciliation parses the public ZIP of CSVs, uses max equity as the strongest available MDD percentage base, and reports position/cash-flow gaps
- Optional audit caching stores bounded per-wallet MDD artifacts locally, reports retention/health metadata, supports targeted purge controls, and exposes JSON/CSV export links without rerunning expensive public API calls
- Leaderboard and MDD payloads report Polymarket rate-limit/backoff metadata instead of hiding upstream 429 failures as generic errors
- MDD payloads include assumptions and limitations because the public Data API does not expose a complete deposit/withdrawal ledger or historical unrealized mark replay
- The React Analytics tab exposes user search, direct wallet MDD lookup/export, cached audit details, cache management, leaderboard sorting, and filters through the local Python API

### Polymarket official API coverage
- Official Polymarket docs checked on 2026-05-28: Gamma, Data, CLOB, Bridge, Relayer, and WebSocket surfaces are represented by local wrapper modules
- `polymarket.gamma` covers events, markets, tags, related tags, series, comments, sports metadata, teams, public search, and public profiles
- `polymarket.data_api` covers activity, positions, closed positions, trades, total value, traded markets, leaderboard, market positions, holders, open interest, live volume, accounting snapshot download, and builder analytics
- `polymarket.analytics_cache` stores bounded local MDD audit artifacts, lists health/retention metadata, purges selected or expired artifacts, and formats JSON/CSV exports for cached public analytics payloads
- `polymarket.clob_rest` covers public orderbook/pricing, price history, market parameters, CLOB market lists, rebates, public rewards, and builder trades
- `polymarket.trader` and `polymarket.clob_auth` cover guarded authenticated order placement, order lookup/cancel flows, trades, order scoring, heartbeat, and authenticated rewards
- `polymarket.bridge` covers supported assets, deposit addresses, quotes, status, and withdrawal-address creation
- `polymarket.relayer` covers guarded relayer submit/query, nonce, relay payload, deployment check, recent transactions, and API key listing
- `polymarket.ws_market`, `polymarket.ws_user`, and `polymarket.ws_sports` cover market, authenticated user, and sports WebSocket channels
- `polymarket.endpoints` and `polymarket.http_client` centralize official endpoint metadata, auth tiers, documented batch caps, retry/rate-limit handling, typed Polymarket errors, and response normalization helpers used by the wrappers
- `polymarket.auth_readiness` and `GET /api/polymarket/clob-readiness` report redacted CLOB v2 readiness for private key, signature type, funder/deposit wallet, L1 headers, and L2 read-only REST headers without deriving credentials or placing orders
- `GET /api/polymarket/live-validation` reports the local Polymarket live-validation stage gates for public probes, credential readiness, authenticated reads, user WebSocket checks, bridge checks, and funded order/cancel status without running funded actions from the GUI/API
- `polymarket.live_reports` and `GET/POST/DELETE /api/polymarket/live-validation/reports` persist redacted local live-validation snapshots, import CLI JSON reports, and compare the latest two stage-gate summaries without exposing funded execution in the GUI/API
- `polymarket.mdd` builds historical MDD v2 payloads from public Data API closed positions, current positions, activity, and trades; it reports USD/% drawdown, capital-basis source, pagination limits, cache boundaries, assumptions, and limitations
- `polymarket.mdd` also exposes an opt-in CLOB mark-replay mode using `/batch-prices-history`; the default API mode remains fast MDD v2 to avoid heavy price-history calls during normal scans
- `polymarket.accounting` parses `/v1/accounting/snapshot` ZIP CSVs and can reconcile MDD payloads against equity, positions, deposits, withdrawals, and cash-flow gaps when explicitly requested
- The GUI exposes the high-level workflows used by this app; the broader official API surface is available to backend code and summarized through `GET /api/polymarket/coverage`
- Full live end-to-end validation of authenticated trading, user WebSocket, relayer, and funded wallet flows still requires real credentials, eligible region/KYC status, funded wallets, and explicit live-mode opt-in

Polymarket coverage is intentionally reported by verification tier, not as a single "implemented" flag:

| Tier | Meaning |
| --- | --- |
| `wrapper_available` | Local Python request helper exists for the documented surface. |
| `app_workflow_available` | Tkinter/API/React exposes a user workflow for that surface. |
| `offline_tested` | Unit tests cover request construction, parsing, and guardrails. |
| `public_live_verified` | Safe non-credentialed live probe passed from this machine. |
| `credential_live_verified` | Real credentialed read/stream verified. Currently blocked without credentials. |
| `funded_live_verified` | Funded order/cancel or fund-movement flow verified. Currently blocked without explicit credentials and live-action approval. |

Current truthful status: public Gamma/Data/CLOB/Bridge probes pass; endpoint contracts are hardened offline against documented paths, auth tiers, and batch caps; CLOB authentication readiness is validated locally and redacted in API payloads; authenticated CLOB, user WebSocket, Relayer, Bridge address/fund movement, and funded order/cancel verification are blocked until credentials and explicit live parameters are supplied.

Authenticated CLOB readiness follows the official Polymarket split between L1/L2 authentication and local order signing:

| Readiness item | Current behavior |
| --- | --- |
| SDK trading readiness | Requires a 0x-prefixed private key, supported signature type, official CLOB host, Polygon chain id 137, and a funder/deposit wallet when the signature type requires one. |
| Direct L2 read readiness | Requires all explicit `POLY_ADDRESS`, `POLY_API_KEY`, `POLY_PASSPHRASE`, `POLY_SIGNATURE`, and `POLY_TIMESTAMP` headers. |
| L1 REST readiness | Reports presence of `POLY_ADDRESS`, `POLY_SIGNATURE`, `POLY_TIMESTAMP`, and `POLY_NONCE`; it does not synthesize signatures. |
| Redaction | Private keys and signed headers are never returned by readiness payloads; addresses are shortened. |
| Live action boundary | Readiness never derives API credentials, submits orders, or moves funds. Funded checks remain behind `scripts/verify_polymarket_live.py` explicit flags. |

The funded live order/cancel verifier is also disabled by default. Running
`python scripts/verify_polymarket_live.py --token-id <TOKEN> --side BUY --price <PRICE> --size <SIZE> --allow-token-id <TOKEN>`
returns a dry-run transcript. A real order/cancel verification requires all of the following: `--allow-funded-order`,
`--cancel-immediately`, an allow-listed token id, `--confirm-live-order-cancel I_UNDERSTAND_THIS_PLACES_A_REAL_POLYMARKET_ORDER`,
valid CLOB credentials, an eligible/funded account, a GTC order, size <= 5 shares, approximate notional <= 1 USDC, and a public
orderbook check proving the requested price is maker-side before placement. The harness immediately cancels the returned order id
and then fetches the order to verify it is no longer live.

For live credential validation, use the verifier as a stage gate and keep the JSON report:

```powershell
python scripts/verify_polymarket_live.py --report-file live-report.json
python scripts/verify_polymarket_live.py --require-authenticated-read-ok --include-user-websocket-connect --report-file live-auth-report.json
python scripts/verify_polymarket_live.py --token-id <TOKEN> --side BUY --price <PRICE> --size <SIZE> --allow-token-id <TOKEN> --report-file live-dry-run-report.json
```

`--require-authenticated-read-ok` fails unless at least one non-destructive authenticated read/stream check succeeds. `--include-user-websocket-connect` opens the authenticated user WebSocket and sends the subscription payload; secrets are not returned in the report. Use `--skip-public-checks` or `--skip-authenticated-read-checks` only for local readiness/debug runs, not for a production live approval.

### 4) Copy trading (paper mode by default)
- Follows a tracked wallet’s **BUY** trades (SELL optional, guarded)
- Default mode is **SIMULATION** (logs what it *would* do)
- Copy sizing is a bounded **0..100%** setting; `0%` watches without copying and `100%` mirrors full detected size before max-USDC caps
- Multiple followed wallets are supported; the conflict guard skips duplicate or opposite-side same-token copies inside the guard window
- Enable **LIVE** mode only after the adapter live preflight settings are explicitly acknowledged
- The React Wallets & Copy tab edits simulation-first copy settings and previews guarded live-copy preflight without placing orders

### 5) Adapter-backed paper trading
- Load an implemented market, select a contract, and submit dry-run paper orders through the selected adapter
- Refresh the selected contract's quote/orderbook preview before sizing a paper or preflighted live order
- Fill the order limit from the selected contract's current quote using side-aware bid/ask selection
- Summarizes local paper exposure by market and contract from accepted paper-order history
- Refreshes paper exposure marks and unrealized P&L from adapter price feeds without placing orders
- Refreshes a selected paper exposure mark without replacing other active marks
- Clears a selected paper exposure mark without dropping other active marks
- Shows aggregate paper exposure totals, marked count, and unrealized P&L above the exposure table
- Shows whether each paper exposure mark came from bid, ask, midpoint, or last trade
- Shows local mark refresh time per exposure row and the latest mark time in the summary
- Revalues marked paper P&L from the current local exposure whenever paper history changes
- Prunes cached paper marks when a contract no longer has open local paper exposure
- Clears transient paper marks without deleting local paper-order history
- Previews how the current paper order form would change the selected contract's local paper exposure
- Reload a selected paper-exposure row into the order form as a position-sized closing order
- Reload a selected paper-history row into the order form for repeat or adjusted dry-run orders
- Uses the adapter’s own validation and dry-run payload builder, including market-specific price/odds rules
- Stores local paper-order history in `data/config.json`

### 6) Central live trading safety
- Every implemented live adapter and the Polymarket copy-trading live path run the same preflight before an order can be posted
- Preflight requires `live_trading_enabled=true` and `live_trading_confirmed=true`, honors `live_trading_kill_switch`, and blocks orders above configured size/notional caps
- Preflight returns a redacted audit payload with contract, side, size, approximate notional, metadata key names, dry-run preview text, and region/KYC/credential warnings
- The React Live Safety tab edits the selected market's live gate and displays the redacted preflight audit without placing orders
- The Paper Trading tab can run **Preview Live Preflight** for the current order form without submitting a paper or live order

### 7) Market safety and credential diagnostics
- The Markets tab shows the selected adapter's health, enabled capabilities, configured credential environment variables, and detected credential sources without secret values
- The Markets and Live Safety tabs persist selected-market enablement, live enablement, live acknowledgement, kill switch, max size, and max notional settings
- Adapter-backed market search, alerts, paper actions, quote previews, and live preflight previews require the selected market to be enabled in local config

### 8) Kalshi adapter support
- Lists Kalshi events/contracts through official REST market-data endpoints
- Reads binary orderbooks and derives YES/NO best bid/ask prices
- Supports dry-run/paper orders; live orders are opt-in and require signed API credentials

### 8) Manifold adapter support
- Searches and lists Manifold markets through the official API
- Reads binary and multiple-choice probabilities for alerts
- Supports paper orders locally and guarded MANA betting through documented API-key auth

### 9) Metaculus adapter support
- Lists authenticated Metaculus posts/questions through the official API
- Reads accessible binary, multiple-choice, and numeric forecast values for alerts
- Does not expose trading controls because Metaculus is a forecasting platform, not a cash market

### 10) Legacy Web3 protocol adapter support
- Lists Augur v2 markets/outcomes through a configured documented subgraph endpoint
- Reads Omen AMM marginal prices and Zeitgeist indexer asset prices for alerts
- Supports dry-run paper orders where reliable price data exists; wallet-signed live trading is not enabled

### 11) Additional official adapter support
- Reads Gemini Prediction Markets events/contracts and orderbooks through official endpoints
- Reads Myriad, Opinion, Predict.fun, XO, and Betfair market data through their documented APIs
- Keeps live trading off by default; Gemini, Myriad, Predict.fun, XO, and Betfair live order posting require explicit opt-in and documented credentials or pre-signed order payloads

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

### 5) Optional React/TypeScript GUI
The existing Tkinter app remains available through `run_gui.bat` or `python app.py`. The React GUI is a parallel local interface backed by a stdlib Python API; it does not replace the Python GUI.

Windows launch scripts:
- `run_web_gui.bat` is the smart launcher. It starts the production React build when `frontend/dist/index.html` exists, starts the Vite dev server when `frontend/node_modules` exists, or prints the exact setup commands plus the Tkinter fallback.
- `run_web_gui_dev.bat` starts `web_api.py` on `127.0.0.1:8765`, sets `VITE_API_BASE_URL`, and runs `npm run dev` from `frontend`.
- `build_web_gui.bat` installs frontend dependencies when needed and runs `npm run build`.
- `run_web_gui_prod.bat` serves the built React app from `frontend/dist` through `web_api.py` at `http://127.0.0.1:8765`.

Manual development startup:
```bash
python web_api.py --host 127.0.0.1 --port 8765
cd frontend
npm install
npm run dev
```

Then open `http://127.0.0.1:5173`.

Manual production startup:
```bash
cd frontend
npm install
npm run build
cd ..
python web_api.py --host 127.0.0.1 --port 8765 --frontend-dir frontend/dist
```

Then open `http://127.0.0.1:8765`.

Useful local API endpoints:
- `GET /api/state` returns the initial React GUI snapshot: health, config, markets, alerts, wallets, copy, live safety, and paper state.
- `GET /api/health` returns API version, route metadata, React dev/build/prod commands, build availability, and confirms the Tkinter fallback remains `run_gui.bat` or `python app.py`.
- `PATCH /api/config` updates shared local config fields such as selected market and theme.
- `GET /api/markets` returns market capabilities, health, status text, credential source diagnostics without secret values, and live-safety settings.
- `PATCH /api/markets/{market_id}` toggles a market and persists live-safety settings such as enablement, acknowledgement, kill switch, max size, and max notional.
- `GET /api/alerts` returns alert rows enriched with adapter-backed status and current in-memory price state.
- `POST /api/alerts` creates a market-scoped price alert after validating the selected adapter supports alerts.
- `PATCH /api/alerts/{alert_id}` edits alert fields or toggles alert enablement.
- `DELETE /api/alerts/{alert_id}` deletes an alert from local config.
- `POST /api/alerts/refresh` refreshes current prices for enabled alerts through adapter price feeds.
- `POST /api/alerts/{alert_id}/refresh` refreshes the selected alert's current price state.
- `GET /api/wallets` returns wallet watches, manual polling status, and recent wallet activity cached by the API session.
- `POST /api/wallets` creates a Polymarket wallet watch for a valid `0x` proxy wallet.
- `PATCH /api/wallets/{wallet_id}` edits wallet display name, enablement, or market-slug filter.
- `DELETE /api/wallets/{wallet_id}` deletes a wallet watch from local config.
- `POST /api/wallets/poll` polls enabled wallet watches once through the Polymarket Data API and updates dedupe state.
- `GET /api/polymarket/users/search?q=...` searches public Polymarket profiles and returns proxy-wallet candidates.
- `GET /api/polymarket/users/leaderboard` returns public leaderboard rows ranked by PnL USD, volume USD, computed ROI %, MDD USD, or MDD %, with min/max filters for PnL, volume, ROI, and MDD. MDD scans accept `mdd_mode`, `mdd_history_limit`, `mdd_activity_limit`, `mdd_trade_limit`, `mdd_open_limit`, `mdd_mark_replay_token_limit`, `mdd_mark_replay_interval`, `mdd_mark_replay_fidelity`, `mdd_include_accounting`, `mdd_persist_cache`, and `mdd_cache_ttl_seconds`; payloads include `analytics_cache` and `rate_limit` metadata.
- `GET /api/polymarket/users/mdd?user=0x...` computes one wallet's MDD USD/% v2 from public closed positions, activity/trade capital basis, and the current open-position snapshot. It accepts `mode=fast` by default or `mode=mark_replay` for CLOB price-history inventory replay, plus `include_accounting_snapshot=true` for accounting ZIP reconciliation, `persist_cache=true`, `closed_limit`, `activity_limit`, `trade_limit`, `open_limit`, `include_open`, `max_points`, `equity_base_usd`, `mark_replay_token_limit`, `mark_replay_interval`, `mark_replay_fidelity`, and `cache_ttl_seconds`.
- `GET /api/polymarket/users/mdd/cache` lists cached MDD audit artifacts with wallet, MDD, age, TTL, expiry, size, and cache path metadata.
- `GET /api/polymarket/users/mdd/cache/health` returns cache path, size, entry counts, active/expired counts, TTL, and retention bounds for MDD audit artifacts.
- `POST /api/polymarket/users/mdd/cache/purge` purges selected keys, expired artifacts, or all MDD audit artifacts from the local analytics cache.
- `DELETE /api/polymarket/users/mdd/cache/{key}` purges one cached MDD audit artifact by cache key.
- `GET /api/polymarket/users/mdd/export.json?key=...` and `GET /api/polymarket/users/mdd/export.csv?key=...` return cached per-wallet MDD audit artifacts created by `persist_cache=true` or `mdd_persist_cache=true`.
- `GET /api/polymarket/coverage` returns the official Polymarket API coverage manifest and live-validation requirements.
- `GET /api/polymarket/live-validation` returns the current local Polymarket live-validation stage-gate report for the React Live Safety view.
- `GET /api/polymarket/live-validation/reports` lists stored redacted live-validation report snapshots and the latest-vs-previous stage-gate comparison.
- `POST /api/polymarket/live-validation/reports` stores the current GUI readiness snapshot or imports a CLI JSON report from `report_json`.
- `DELETE /api/polymarket/live-validation/reports/{key}` deletes one stored live-validation report snapshot.
- `GET /api/copy` returns copy-trading settings, tracked-wallet status, and live gate state.
- `PATCH /api/copy` updates simulation-first copy settings, including multiple followed wallets, bounded copy percentage (`0..100`), and conflict-guard settings.
- `POST /api/copy/preview` previews copy-trade sizing and guarded live preflight without placing orders.
- `GET /api/live-safety` returns selected-market live gate state, blockers, and redaction metadata.
- `POST /api/live-safety/preflight` runs the shared live-order preflight for the current order form and returns a redacted pass/block audit without placing orders.
- `POST /api/paper/quote` returns the selected contract quote and orderbook snapshot for the paper order form.
- `POST /api/paper/quote-limit` fills a side-aware paper limit price from the selected contract's quote.
- `POST /api/paper/preview-impact` previews local exposure impact before recording a paper order.
- `POST /api/paper/orders` submits an adapter-backed paper order and stores the local history record.
- `POST /api/paper/history/use` reloads a paper-history row into the order form.
- `POST /api/paper/history/clear` clears local paper-order history.
- `POST /api/paper/positions/use` reloads an exposure row into a close-sized order form.
- `POST /api/paper/marks/refresh` refreshes current marks and unrealized P&L for all open paper exposure.
- `POST /api/paper/marks/refresh-selected` refreshes only the selected exposure mark.
- `POST /api/paper/marks/clear` clears transient paper marks without deleting history.
- `POST /api/paper/marks/clear-selected` clears only the selected exposure mark.

API hardening:
- Error responses use `{ "ok": false, "error": { "code": "...", "message": "...", "status": 400 } }`.
- JSON mutation bodies must be objects and are rejected when malformed or larger than 1 MB.
- Internal server errors return a generic message rather than raw exception text.
- Credential-like keys in settings, diagnostics, and error details are redacted recursively.

## Market Capability Matrix

This matrix describes current application adapter support. Verified-blocked markets appear in the GUI and config, but their market-specific operations intentionally return clear unsupported-feature messages until official access, entitlements, or documented automation terms make support safe to add. Verified-blocked rows were checked against currently available official docs/pages.

Article 35 re-audited verified-blocked markets on 2026-05-26 and did not promote any blocked market. Context Markets is sunset, Hyperliquid outcome metadata is still not production-safe for this adapter, Thales needs chain-specific AMM/wallet safeguards and fixtures, and Smarkets/CME still require approval, data-use permission, or licensed entitlements before support can be added.

| Market | Adapter | Alerts | Read-only data | Paper trading | Live trading | Copy trading | API required | Credentials required | Region/KYC limitation |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Polymarket (`polymarket`) | Implemented | Yes | Yes | Yes | Guarded, off by default | Yes, dry-run default | Yes | Live trading only | Trading may be region/KYC limited |
| Kalshi (`kalshi`) | Implemented | Yes | Yes | Yes | Guarded, off by default | No | Required | Exchange account/API keys | Region/KYC limited |
| PredictIt (`predictit`) | Implemented | Yes | Yes | Yes | No | No | Required | No | Region/account limited |
| Robinhood Prediction Markets (`robinhood_prediction_markets`) | Verified blocked | No | No | No | No | No | Required | Brokerage account required | Region/KYC limited |
| Fanatics Markets (`fanatics_markets`) | Verified blocked | No | No | No | No | No | Required | Account required | Region/KYC limited |
| DraftKings Predictions (`draftkings_predictions`) | Verified blocked | No | No | No | No | No | Required | Account required | Region/KYC limited |
| Interactive Brokers ForecastTrader / IBKR Prediction Markets (`ibkr_forecasttrader`) | Verified blocked | No | No | No | No | No | Required | IBKR account required | Region/KYC limited |
| ForecastEx (`forecastex`) | Verified blocked | No | No | No | No | No | Required | Exchange/broker account required | Region/KYC limited |
| CME Group Prediction Markets (`cme_prediction_markets`) | Verified blocked | No | No | No | No | No | Required | Broker/data entitlement required | Region/KYC limited |
| Nadex (`nadex`) | Verified blocked | No | No | No | No | No | Required | Exchange account required | Region/KYC limited |
| Crypto.com Predict / CDNA (`crypto_com_predict`) | Verified blocked | No | No | No | No | No | Required | Crypto.com account required | Region/KYC limited |
| Hyperliquid (`hyperliquid`) | Verified blocked | No | No | No | No | No | Required | Wallet required for trading | Jurisdiction varies |
| Myriad Markets (`myriad_markets`) | Implemented | Yes | Yes | Yes | Guarded, off by default | No | Required | Optional API key | Jurisdiction varies |
| Context V2 (`context_v2`) | Verified blocked | No | No | No | No | No | Required | API credentials required | Jurisdiction varies |
| Frenzy Finance (`frenzy_finance`) | Verified blocked | No | No | No | No | No | Required | Wallet required for trading | Jurisdiction varies |
| XO Market (`xo_market`) | Implemented | Yes | Yes | Yes | Guarded, off by default | No | Required | API credentials required | Region/KYC limited |
| Manifold Markets (`manifold`) | Implemented | Yes | Yes | Yes | Guarded, off by default | No | Required | Live trading only | Not KYC limited |
| Metaculus (`metaculus`) | Implemented | Yes | Yes | No | No | No | Required | Account/API token required | Not trading/KYC limited |
| Good Judgment Open (`good_judgment_open`) | Verified blocked | No | No | No | No | No | Required | Account/export access required | Not trading/KYC limited |
| Hypermind (`hypermind`) | Verified blocked | No | No | No | No | No | Required | Program access required | Program access limited |
| Iowa Electronic Markets (`iowa_electronic_markets`) | Verified blocked | No | No | No | No | No | Required | IEM account required | Eligibility limited |
| INFER / INFER-pub (`infer`) | Verified blocked | No | No | No | No | No | Required | Account/export access required | Not trading/KYC limited |
| Fact Machine (`fact_machine`) | Verified blocked | No | No | No | No | No | Required | Wallet/personhood required | Identity/jurisdiction limited |
| Opinion Labs (`opinion_labs`) | Implemented | Yes | Yes | Yes | No | No | Required | API credentials required | Jurisdiction varies |
| Gemini Titan / Gemini Predictions (`gemini_titan`) | Implemented | Yes | Yes | Yes | Guarded, off by default | No | Required | Live trading only | Region/KYC limited |
| Augur (`augur`) | Implemented | No | Yes | No | No | No | Required | Subgraph endpoint required | Jurisdiction varies |
| BetMGM (`betmgm`) | Verified blocked | No | No | No | No | No | Required | Account required | Region/KYC limited |
| PrizePicks (`prizepicks`) | Verified blocked | No | No | No | No | No | Required | Account required | Region/KYC limited |
| Underdog Sports (`underdog_sports`) | Verified blocked | No | No | No | No | No | Required | Account required | Region/KYC limited |
| Drift BET (`drift_bet`) | Verified blocked | No | No | No | No | No | Required | Wallet required for trading | Jurisdiction varies |
| Thales Market (`thales_market`) | Verified blocked | No | No | No | No | No | Required | Wallet required for trading | Jurisdiction varies |
| Hedgehog Markets (`hedgehog_markets`) | Verified blocked | No | No | No | No | No | Required | Wallet required for trading | Jurisdiction varies |
| Omen (`omen`) | Implemented | Yes | Yes | Yes | No | No | Required | Subgraph endpoint required | Jurisdiction varies |
| Zeitgeist (`zeitgeist`) | Implemented | Yes | Yes | Yes | No | No | Required | Not required | Jurisdiction varies |
| Azuro (`azuro`) | Implemented | Yes | Yes | Yes | Guarded, off by default | No | Required | Live signed orders only | Jurisdiction varies |
| SX Bet / SX Network (`sx_bet`) | Implemented | Yes | Yes | Yes | Guarded, off by default | No | Required | Live/WebSocket only | Jurisdiction varies |
| Limitless Exchange (`limitless_exchange`) | Implemented | Yes | Yes | Yes | Guarded, off by default | No | Required | Live trading only | Jurisdiction varies |
| Predict.fun (`predict_fun`) | Implemented | Yes | Yes | Yes | Guarded, off by default | No | Required | API credentials required | Jurisdiction varies |
| Smarkets (`smarkets`) | Verified blocked | No | No | No | No | No | Required | Exchange account required | Region/KYC limited |
| Betfair Exchange (`betfair_exchange`) | Implemented | Yes | Yes | Yes | Guarded, off by default | No | Required | Exchange account/API keys | Region/KYC limited |
| Probo (`probo`) | Verified blocked | No | No | No | No | No | Required | Account required | Region limited |

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
- Windows launch UX checks
- Tkinter fallback smoke checks
- frontend build readiness checks; the build is skipped unless `frontend/node_modules` exists
- offline unit tests for config/storage, API wrapper parsing, alert crossing, copy-trade percentage sizing, and wallet activity de-duplication

Pytest is included in `requirements.txt`; run the pytest suite directly with:
```bash
python -m pytest
```

Tkinter fallback smoke check:
```bash
python app.py --smoke-test
```

React frontend checks:
```bash
cd frontend
npm install
npm run build
```

Strict final frontend verification:
```bash
python verify.py --frontend-build
```

If `frontend/node_modules` is missing, the normal verifier records frontend build readiness and skips the build. The strict command above fails until `npm install` or `build_web_gui.bat` has completed successfully.

## CI/CD and Releases

GitHub Actions workflows live under `.github/workflows`:
- `ci.yml` runs Python verification across Ubuntu/Windows and Python `3.10` through `3.14`, builds the React frontend with Node.js `24`, and builds Python distributions.
- `security.yml` runs CodeQL analysis and runs dependency review only when GitHub dependency graph is enabled.
- `release.yml` publishes tagged releases (`v*.*.*`) with Python package artifacts, a zipped React production bundle, Windows x64 portable/installer packages, and SHA256 checksums.

Dependabot is configured in `.github/dependabot.yml` for GitHub Actions, Python, and frontend npm dependency updates.

See `docs/CI_CD.md` for the release process, recommended branch protection, release environment setup, and strict frontend build verification.

In-app checks:
- **About -> Check versions** compares installed dependency versions with PyPI.
- **Copy Trading -> Check Geoblock** verifies whether live trading should be blocked for the current location.
- **Markets** displays selected-adapter health and edits live safety gates without touching credentials.
- Disabled markets remain visible but adapter-backed actions are blocked until enabled in **Markets** or **Live Safety**.
- **Live Safety** displays selected-market live gate status, blockers, max caps, acknowledgement, redacted preflight audits, the Polymarket live-validation stage-gate report, and local redacted report history/import/compare controls.
- **Alerts** creates, edits, toggles, deletes, and refreshes market-scoped price alerts.
- **Alerts** exposes last trade, midpoint, best bid, and best ask source selection for each alert.
- **Alerts -> Refresh Prices** polls adapter-backed current price state and updates trigger status without placing orders.
- **Wallets & Copy** manages tracked Polymarket proxy wallets and manual Data API activity polling.
- **Wallets & Copy** shows recent wallet activity with the copy-trading simulation or skip reason for each item.
- **Wallets & Copy** edits followed wallets, copy percentage, max USDC, slippage, live mode, SELL-copy permission, and the same-token conflict guard.
- **Wallets & Copy -> Preview** runs the live-copy preflight gate for a sample activity and does not place an order.
- **Analytics** searches public Polymarket profiles and loads leaderboard rows by ROI %, PnL USD, volume USD, MDD USD, or MDD %.
- **Analytics** computes MDD USD/% on demand from closed-position realized PnL plus current open-position PnL.
- **Analytics** can compute a single wallet's MDD directly, use a profile-search wallet as input, and inspect cached audit detail without rerunning the public API calls.
- **Analytics** can persist MDD audit artifacts, inspect cache health/retention, purge selected/expired/all cache entries, and download artifacts as JSON or CSV.
- **Paper Trading -> Refresh Quote** previews the selected contract's current adapter quote/orderbook without placing an order.
- **Paper Trading -> Use Quote Limit** fills the limit field from best ask for BUY/BACK and best bid for SELL/LAY where available.
- **Paper Trading** keeps a local paper exposure summary above the order-history table.
- **Paper Trading -> Refresh Marks** marks paper exposure against current adapter prices and shows unrealized P&L.
- **Paper Trading -> Refresh Selected Mark** marks only the selected exposure row and preserves other active marks.
- **Paper Trading -> Clear Selected Mark** clears only the selected exposure row's mark and preserves other active marks.
- **Paper Trading** totals gross size, entry notional, marked rows, and aggregate unrealized P&L above the exposure table.
- **Paper Trading** shows mark source per exposure row and aggregates mark-source counts in the summary.
- **Paper Trading** shows mark time per exposure row and the latest mark time in the summary.
- **Paper Trading** recomputes marked P&L from the latest local paper exposure after new or cleared history.
- **Paper Trading** drops cached marks for contracts that no longer appear in the local exposure table.
- **Paper Trading -> Clear Marks** clears mark price/source/time/P&L while preserving paper history.
- **Paper Trading -> Preview Impact** shows current net, order net, projected net, effect, and projected notional before recording a paper order.
- **Paper Trading -> Use Position** loads the selected exposure row into a close-sized order form and clears the limit for a fresh quote.
- **Paper Trading -> Use History Order** reloads the selected paper-history row into the order form without placing an order.
- **Paper Trading -> Preview Live Preflight** validates a proposed order against the live safety gate without posting it.
- Form validation guards wallet addresses, alert thresholds, and copy-trading numeric settings.
- Copy trading defaults to simulation mode and performs a geoblock check plus the shared adapter live preflight before live orders.

## Notes
- The app stores local settings and paper-order history in `data/config.json` (gitignored user-specific state) using atomic replace writes.
- If you enable LIVE trading, do **not** store keys in plaintext beyond what you accept as your risk.
  Prefer env vars, a password manager, or OS keychain tooling.

## Project structure
- `app.py` – GUI + orchestration
- `polymarket/` – API wrappers + websocket client + trading wrapper
- `core/` – config models + storage helpers
- `data/` – local config and cache
