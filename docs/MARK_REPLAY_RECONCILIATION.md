# Mark Replay Reconciliation

Calculation version 8 rejects two additional sources of falsely low observed
drawdown. A trade-only replay cannot qualify a risk filter when activity contains
redemptions, splits, merges, conversions, rewards, rebates, referral rewards, or
an unknown non-trade type. These cash and inventory changes are not reconstructed.
`mark_replay.unsupported_activity` records their types and counts.

When current open positions are supplied, their token identities and sizes must
agree with reconstructed final inventory. A fetched, exhausted empty position
window also participates in this comparison. Each supplied current position
value must agree with that token's terminal replay value; offsetting token
disagreements cannot pass through an aggregate balance comparison. Contradictory
current-value aliases fail source quality before replay. The final sampled PnL must
agree with the observed closed realized PnL plus current open PnL when those
observations are available. Missing position identities/sizes, duplicates, and
inventory/value/PnL disagreements produce unavailable top-level risk. Six-decimal
source rounding is bounded by the number of observations, not balance size.

For example, a purchase of 100 shares at $0.50 followed only by a stale $0.50 mark
produces a sampled zero drawdown. A current position reporting $1 value and -$49
PnL contradicts that terminal replay. The sampled zero remains diagnostic;
`mdd_pct` becomes null and cannot pass a maximum-MDD filter.
A reported $1 current value also fails when the same row incorrectly claims zero
PnL; matching PnL alone cannot qualify the replay.

The point-in-time comparison is in
`mark_replay.current_snapshot_reconciliation`; it survives SQLite persistence
and JSON export. `mdd_unavailable_reasons` also survives CSV export. Prior
durable enrichment is invalidated when a scan resumes with calculation version 8.

Matching current observations does not establish lifetime history, historical
intra-sample valleys, complete fees, or investment return. Supplied rows without
a current snapshot remain explicitly unverified observed-public calculations.
Public endpoint windows remain bounded and can change between requests. A
reconciliation failure leaves the partial sampled curve available for diagnosis;
it does not invent cash movements or replace historical marks with current ones.

Leaderboard discovery validates each row before normalization, pagination,
checkpointing, or SQLite publication. The official
[v1 schema](https://docs.polymarket.com/api-reference/core/get-trader-leaderboard-rankings)
identifies wallets through `proxyWallet` and exposes PnL and `vol`; the
[v2 board](https://docs.polymarket.com/api-reference/boards/get-the-trader-leaderboard)
uses `user_id`, PnL, and share-denominated `volume`. V2 rows retain those units
and cannot enter v1 USD analytics.

Established v1 field aliases and nested user/profile/trader shapes remain
supported, as do valid numeric strings, case normalization, negative PnL, and
zero volume. Missing or malformed identities/economics, boolean or non-finite
financial values, negative volume, contradictory aliases, and derived ratio
overflow fail with a typed source error. Malformed row envelopes, non-object
rows, explicit upstream errors, and failed statuses cannot signal source
exhaustion. Valid empty arrays, supported legacy array envelopes, success
metadata, and empty/null error aliases retain their existing behavior.

Every persisted row is checked before a legacy database can migrate, resume,
serve status, or export. Invalid state remains preserved and requires a fresh
scan in a separate state file. Invalid newly fetched pages never advance durable
pagination, and failed file exports preserve the previous completed output.
Stored raw-source identity and economic binding uses the same nested
user/profile/trader traversal as source normalization.
Checkpoint row integrity is required before resumption; duplicate JSON keys and
non-finite values also fail. These guards do not claim a consistent board
snapshot or exhaustive discovery of every account.
