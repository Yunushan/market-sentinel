# Polymarket Data API v2 migration

Reviewed on 2026-10-03 against the official [migration guide](https://docs.polymarket.com/migrate/data-api-v1-to-v2), [shared v2 contract](https://docs.polymarket.com/quickstart/reference/data-api-v2), and endpoint references below. Polymarket retires the original Data API routes on **2026-10-24**. The accounting snapshot at `/v1/accounting/snapshot` is explicitly exempt and remains on its existing route.

The production wallet activity, history, portfolio position, leaderboard, and public verification consumers use explicit v2 clients. The legacy `data_api.py` helpers remain compatibility surfaces; callers outside this repository must migrate their direct uses before the retirement date. They do not silently fall back to v1 from v2.

| Client | Reviewed route and contract |
| --- | --- |
| `get_activity_page_v2` | [`/v2/activity`](https://docs.polymarket.com/api-reference/feeds/list-account-activity): keyset feed; `start=1` requests full history; only `TIMESTAMP` sorting |
| `get_positions_page_v2` | [`/v2/positions`](https://docs.polymarket.com/api-reference/wallet/list-positions-for-a-user-or-market): unified position lifecycle; `status=CLOSED` replaces `/closed-positions` |
| `get_trades_page_v2` | [`/v2/trades`](https://docs.polymarket.com/api-reference/feeds/list-trades): keyset feed; `taker_only=False` includes wallet maker fills |
| `get_leaderboard_v2_page` | [`/v2/leaderboard`](https://docs.polymarket.com/api-reference/boards/get-the-trader-leaderboard): cursor-bound board with native share volume |
| `get_value_v2` | [`/v2/value`](https://docs.polymarket.com/api-reference/wallet/get-portfolio-value): a single object inside `data`; a condition filter excludes the portfolio combo term |

## Pagination and completeness

Use the upstream `pagination.next_cursor` unchanged. `None` is the only documented end of a walk. An empty or short page can still have another cursor. The `offset` in response metadata is for display; it must never be converted into a request cursor or used for v2 offset arithmetic. Page clients validate the required `has_more`, `next_cursor`, `limit`, and `offset` fields, reject disagreement or invalid types, and reject nonobject rows, error envelopes, or a repeated input cursor.

Activity and trade feed cursors carry a seek anchor. Send identical user, condition/event/type/side filters and time bounds on every page. Activity cursors bind direction, so retain the same `sort_direction`. Cursor page size comes from the cursor; the client sends `limit` on the first request only. Keep a fixed explicit `end` for a bounded historical collection. Repeated cursors, exhausted budgets, invalid rows, and unfinished walks must remain an incomplete result and cannot advance a wallet checkpoint.

Position cursors bind status and sort order, but still require the original user or condition anchor. Continue sending narrowing filters, including `title`, `condition`, `start`, and `end`, because they can otherwise widen the cohort. The client's optional status/sort arguments let a resumed cursor adopt its original spine without accidentally asserting the default `OPEN` state. A closed request cannot include archived markets.

Leaderboard cursors bind the board query and can resume with the cursor alone. They enumerate that ranked board rather than all Polymarket accounts. Tied ranks can skip numbers; rank is not a pagination anchor.

## Financial fields

Clients preserve native snake_case rows and add only validated identity aliases used by the existing copy workflow. Activity also preserves its documented `usdc_size` and adds `usdcSize`. Conflicting aliases fail closed. Optional null economics stay null, and unknown fields remain present. No client manufactures zero or relabels an amount to make a legacy consumer accept it.

Native condition-level redemptions and merges can have an empty `token_id`;
account rewards can have both an empty token and condition. Non-trade events
preserve these empty strings and aliases without inventing asset identity.
Trades and position rows retain their separate identity validation. A bounded
public diagnostic on 2026-10-03 exercised 300 activity rows containing
`TRADE`, `REDEEM`, `MERGE`, and `REWARD`; this sample is not proof of complete
history, credentialed acceptance, or funded execution.

Position `current_size` and `total_size` are shares. `entry_cost_usdc`, `entry_fees_usdc`, and `total_cost_usdc` are explicit monetary amounts; closed positions can have a zero residual entry basis while still having nonzero lifetime activity. Position history time is `last_event_at`, not a fabricated trade timestamp. Unbounded position reads can include rows without a native event time; windowed reads cannot.

Leaderboard `volume` is outcome shares, not USD. The lifetime `all` PnL is realized-only; finite day/week/month windows represent marked equity change net of flows. A v1 USD-volume record cannot be resumed or merged into a native v2 share-volume scan. Preserve the source version and units in persisted analytics and exports.

## Remaining library surfaces

These legacy wrapper methods have no current application consumer but still target retiring routes: `get_total_markets_traded` (`/traded` → `/v2/user-stats`, distinct-market count is `data.trades`), `get_market_positions` (`/v1/market-positions` → `/v2/positions?condition=...`), `get_top_holders`, `get_open_interest`, `get_live_volume`, `get_builder_leaderboard`, and `get_builder_volume`. External library consumers must adopt the documented v2 envelope, names, and units for each route. Accounting snapshot downloads stay on the exempt v1 endpoint.

The checked-in JSON fixtures and wire tests are offline contract checks. They are not claims of public, credentialed, funded, or deployed verification. Fresh live evidence must be collected by the normal verification workflow against the final clean revision.
