# Wallet activity delivery

`POST /api/wallets/poll` requires an `Idempotency-Key` header. Its JSON body is
`{"limit": 25}` or `{"limit": 25, "acknowledge_receipt_id": "previous-receipt-UUID"}`.
`limit` controls source page size. A successful poll delivers at most 100 new
events and may deliver fewer to preserve the complete response and replay receipt.

The server first establishes complete source coverage for the fixed history
window. It processes the oldest pending events first across enabled wallets,
while returning the delivered batch in the existing newest-first display order.
Only events included in the complete batch and known market-filter skips advance
the corresponding wallet cursor. Undelivered events, including other fills at the
same timestamp, remain eligible for the next poll. One 60-second work budget
covers source reads, every wallet, and copy-preview enrichment. The aggregate
history cap is 50,000 observations; the complete replay receipt is at most 256 KiB.

The response retains `wallets`, `copy`, `message`, `activity`, `problems`, and
`polled_wallets`. It adds `delivered_activity`, `has_more`, `remaining_activity`,
`consumed_filtered`, `batch_limit`, and
`delivery: {"mode": "durable_replayable_batch", "receipt_id": "UUID",
"acknowledge_with_next_poll": true}`. Remaining counts describe matching events
in the completed observed window. They do not claim an immutable upstream snapshot.

Cursor changes and the exact activity batch receipt commit atomically before
response headers are sent. Retrying the identical body and key returns that batch
without fetching history or recomputing previews. Wallet and copy settings views
can refresh during replay. A client must retain the request key on network errors,
response parsing failures, and 5xx responses. A failed response write is not proof
that the client received the batch.

The client acknowledges only a fully received and parsed successful batch. It
persists that receipt ID before discarding its pending request key, then includes
the ID on its next explicit poll. That acknowledgement and the next batch commit
together. Failed source reads, enrichment, response preflight, or saves before
replacement leave the prior acknowledgement, cursors, and recent activity intact.
If replacement committed but filesystem synchronization failed, the durable
receipt supports the same-key retry; clients must not change their request identity.

Unacknowledged successful poll receipts are pinned against unrelated mutation
journal eviction. At most 16 may remain unacknowledged. If those slots or the
durable journal are full, polling fails before source work or cursor changes.
An acknowledged receipt can subsequently be evicted under the normal bounded
journal policy. Its receipt ID is then no longer valid for a new acknowledgement.
Already acknowledged IDs remain idempotent while their receipts are retained.

Source incompleteness returns `503 wallet_poll_incomplete`; aggregate deadline
exhaustion returns `503 wallet_poll_budget_exhausted`; an event or current state
that cannot fit a complete response returns `503 wallet_poll_response_budget`;
unacknowledged receipt capacity returns `503 wallet_poll_receipts_full`. Unknown
acknowledgement IDs return 400. Reusing a key with a changed body returns 409.
The browser does not automatically acknowledge on rendering or drain all batches.
