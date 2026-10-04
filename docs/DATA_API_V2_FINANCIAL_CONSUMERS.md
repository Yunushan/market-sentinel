# Data API v2 financial reads

Polymarket's [migration guide](https://docs.polymarket.com/migrate/data-api-v1-to-v2)
retires the original Data API routes on October 24, 2026. The public accounting
snapshot remains on `/v1/accounting/snapshot`; it is explicitly outside that
retirement. The v2 position, trade, activity and leaderboard contracts are
defined by the [official OpenAPI](https://data-api.polymarket.com/v2/openapi.json),
reviewed on October 3, 2026.

MDD calculation version 9 reads `/v2/positions`, `/v2/trades` and `/v2/activity`
through their native cursor envelope. It never advances an offset, falls back
to a retiring route, or interprets a short array as EOF. Only explicit
`pagination.next_cursor=null` proves source-window exhaustion. A full last page
can be complete; a short page with a cursor must continue. Local caps, discarded
overflow rows and missing cursor evidence keep historical risk unavailable.
Completeness metadata survives the process cache.

Open positions request `status=OPEN`, `filter_type=TOKENS`, `filter_amount=0`
and `include_archived=true`; this includes redeemable holdings. CLOSED positions
request `status=CLOSED` and chronological `last_event_at` ordering. Activity and
trades request `start=1` for full wallet history rather than the provider's
default three-year window. Trade reads include maker fills (`taker_only=false`),
and activity reads retain non-trade and deposit/withdrawal events as evidence
of replay limitations. Query anchors and filters are repeated with each cursor.
Reads have bounded page, row and time budgets.

Native position quantities are distinct: `current_size` is the current share
holding; `total_size` is lifetime bought shares. Open capital prefers
`total_cost_usdc`, the exact sum of fee-exclusive `entry_cost_usdc` and disclosed
`entry_fees_usdc`. It does not add those fees twice. CLOSED rows have approximately
zero residual entry basis; this is not lifetime acquisition spending. Their
`total_size * avg_price` is retained as an explicitly labeled acquisition-cost
estimate whose fee component is unverified. It does not become verified account
opening capital. Position timestamps use `last_event_at`, rather than an
invented v1 `timestamp`.

Native total PnL must equal realized plus unrealized PnL. Unrealized PnL must
equal current value minus fee-exclusive entry basis. Invalid finite values or
contradictory served cost/PnL/value components invalidate financial risk even
when an operator supplies an equity base. Missing values remain unavailable.
The Data API contract denominates these served economic fields in USDC; this
does not convert historical paper collateral or imply a funded settlement audit.

Leaderboard v2 `volume` is traded shares, not cash turnover. Its lifetime PnL is
realized-only, while finite-window PnL includes marked equity changes net of
flows. Neither `pnl / volume` nor a current position's percent field establishes
investment ROI. Compatibility fields named `volume_usd` or `roi_pct` must remain
unavailable for a native v2 share-volume row unless a separate matching cash
denominator is explicitly obtained. Historical persisted rows retain their
original unit provenance.

These migrations preserve the existing limitation: public position/trade
history and sampled mark replay are diagnostics. Cursor exhaustion is not
independent proof of a complete cash-flow, fee, settlement or intra-sample
account-equity ledger, and the application continues to report unverified
account equity.

New MDD payloads explicitly record `source_economics_currency`/`quote_currency`
for the PnL curve and a separate `equity_base_currency`. A numeric legacy
`equity_base_usd` input declares USD unless the caller explicitly supplies
`equity_base_currency=USDC`. Native derived capital uses USDC. USD and USDC are
not converted or assumed equal: a native USDC curve with a declared USD base
keeps its monetary drawdown, but its percentage is unavailable with
`equity_base_currency_mismatch`. Percentage-based risk filters cannot qualify
that result. Replay and accounting diagnostics cannot bypass this boundary.
Saved older audit records without currency proof remain unchanged and their
missing units remain unavailable.

The published v2 activity/trade rows do not promise a per-fill sequence ID.
Two fills can share transaction hash, token, timestamp, side, quantity and
price. MDD preserves observed multiplicity within a feed and takes the
greatest multiplicity across the overlapping activity/trade feeds. Repeated
indistinguishable transaction economics are flagged as ambiguous source
identity and cannot qualify exact risk. Open-position duplicate token
observations are also rejected rather than counted twice.
