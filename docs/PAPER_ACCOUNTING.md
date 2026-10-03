# Paper inventory and financial metrics

The desktop and API use the same chronological average-cost accountant in
`core/paper_accounting.py`. Each market and contract has separate inventory.
Adding shares updates their weighted entry price. A partial close removes the
closed shares at that entry price and records the difference as realized PnL.
Reversing a position closes the old inventory and starts the remaining opposite
inventory at the new fill price. Fully closed contracts leave the open position
table; their realized result remains in the accounting payload.

For example, buying 10 shares at 0.40 and selling 5 at 0.80 leaves 5 shares with
an entry price of 0.40 and cost basis of 2.00. Realized profit is 2.00. Marking
the remaining shares at 0.50 gives unrealized profit of 0.50. Sale proceeds do
not reduce the remaining average entry price to zero.

The durable ledger is stored newest first. Recorded timestamps establish
chronology; same-second timestamps use the reverse stored list order, preserving
insertion order. Random record UUIDs do not determine execution chronology.
Duplicate record identities, invalid fill quantities, invalid timestamps and
nonfinite values make the affected accounting unavailable. Actual partial fills
use `filled_size` and `average_price`, not the larger requested quantity or an
assumed execution price.

Accepted dry-run previews with no fills explicitly assume full execution at the
limit price. They are hypothetical inventory, not evidence that the venue could
fill the order. Without a usable fill/limit price, quantities may be known while
entry cost and PnL remain unavailable. Fees, executable depth, funding and
settlement are not simulated. The payload reports these assumptions and labels
its scope `retained_ledger_simulation`, with empty opening inventory assumed.

Only explicitly supported share/quote-currency venues receive share accounting.
Budget, stake, decimal-odds and forecast quantity models, including Manifold,
Myriad, Opinion (BUY is quote spend), XO (USD budget), Hedgehog (pooled deposit),
Azuro, exchange BACK/LAY and Metaculus previews, remain in history and
display an unavailable accounting row. They are not converted into equivalent
shares without a venue-specific model. USD, USDC, USDT and pUSD totals are not added
together without conversion: mixed-currency portfolio amounts are unavailable,
while each supported contract retains its native-currency metrics. Missing
marks or costs also make total portfolio unrealized PnL unavailable instead of
presenting a subtotal as a complete result.

Predict.fun and Probable use USDT collateral, while Limitless uses USDC.
Polymarket CLOB V2 and its read-only Blinq alias use pUSD. New paper records
persist their explicitly modeled `quote_currency`; historical records without
that field are not retroactively assigned the venue's current asset. Their
quantities remain available and their financial amounts stay unavailable until
explicit reconciliation. Collateral changes inside one contract also make its
financial basis unavailable instead of adding different assets. Context V2 and Xmarket have proven
share quantities but no verified collateral identity in the retained ledger;
their share count and probability entry price are shown while currency amounts
remain unavailable with `quote_currency_unavailable`.

Native-asset evidence: [Predict.fun SDK](https://github.com/PredictDotFun/sdk-python),
[Probable examples](https://github.com/0xprobable/clob-examples),
[Polymarket trading quickstart](https://docs.polymarket.com/trading/quickstart),
and [Limitless migration guide](https://docs.limitless.exchange/developers/migrate-from-polymarket).

History is retained up to the existing `MAX_PAPER_TRADES` configuration limit
(50,000 records), subject to the configuration file byte limit. Adding an order
at capacity fails explicitly before its adapter is called. The application does
not silently discard an older purchase to retain the newest 200 entries.
Export and reconcile the complete ledger before explicitly clearing it. Older
releases may already have pruned records; those lost opening positions cannot
be reconstructed from the retained ledger, and clearing history starts a new
simulation with empty opening inventory.

Manifold derived candles sort all normalized fills by timestamp before choosing
the open and close, including fills across multiple API bet rows. This handles
the upstream descending bet order without reversing candle opens and closes.
