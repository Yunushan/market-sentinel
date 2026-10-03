# Public Position Cost Basis

MDD calculation version 9 reads native Data API v2 position economics. It does
not implement verified investment ROI or a full account-equity ledger. The
[official OpenAPI](https://data-api.polymarket.com/v2/openapi.json), reviewed on
October 3, 2026, defines the current units and fee components. See
[Data API v2 financial reads](DATA_API_V2_FINANCIAL_CONSUMERS.md) for cursor
coverage, endpoint migration and history limitations.

## Native v2 calculation

1. For open holdings, prefer `total_cost_usdc`, the remaining entry cost plus
   attributed BUY fees. Do not add its fee component again.
2. Otherwise use fee-exclusive `entry_cost_usdc`, adding `entry_fees_usdc` only
   when supplied. If entry cost is unavailable, `current_size * avg_price` is
   an estimate using remaining shares; fee provenance remains explicit.
3. For CLOSED positions, the residual entry basis is approximately zero and
   does not establish historical spending. `total_size * avg_price` is an
   acquisition-cost estimate based on lifetime bought **shares**. Its lifetime
   fee component remains unverified; residual attributed fees are not treated
   as proof of lifetime fees.
4. Without a cost or both price and quantity, report position cost as
   unavailable. Neither raw share count nor current market value is entry cost.

`total_pnl` must equal `realized_pnl + unrealized_pnl`; a missing component is
not silently replaced by zero. Unrealized PnL must equal `current_value -
entry_cost_usdc`, while `current_value` must agree with `current_size *
current_price`. Invalid, non-finite, negative or contradictory financial
components invalidate risk even with a user-supplied capital base. Comparisons
allow six-decimal source rounding. Duplicate open-token observations cannot
double their current PnL.

MDD requests `status=OPEN`, `filter_type=TOKENS`, `filter_amount=0` and
`include_archived=true` on each open-position cursor page. This includes
redeemable holdings and dust positions. CLOSED reads use `last_event_at`
chronology. Query scope and explicit cursor EOF evidence are recorded in
`mdd_history_coverage`; caps and unproven coverage retain unknown risk.

`position_capital_basis` records the native USDC unit, selected sources and
unknown-row counts in MDD and durable scan/CSV summaries. Missing fee components
remain visible. Source-reported PnL is not relabeled as a complete reconciled
net return. Increasing the calculation version invalidates older MDD scan
enrichment while preserving source-download evidence according to its original
API provenance.

## Historical compatibility

Synthetic and previously persisted v1 rows retain their original units.
`grossInitialValue` already includes attributed BUY fees; `initialValue` and
`avgPrice` exclude them. `totalBought` is bought share quantity, never dollars.
An explicit zero is known, while an absent field is unavailable. Historical
rows are not retroactively assigned current collateral or native v2 volume
semantics. The [historical position schema](https://github.com/Polymarket/polymarket-subgraph/blob/7a92ba026a9466c07381e0d245a323ba23ee8701/pnl-subgraph/schema.graphql)
is unit evidence, not a current production data source.

The automatic public denominator still uses aggregate position/trade estimates;
capital reuse can differ from this basis. Full historical cash flows, inventory,
valuations, fees and independent portfolio reconciliation remain required before
the account-level MDD <=20% requirement can be certified.
