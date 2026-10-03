# Adapter wallet history coverage

`list_activity` retains its list-compatible interface while returning an
`ActivitySnapshot` with explicit upstream exhaustion metadata. A normalized
short list does not establish exhaustion: local normalization can exclude
pending orders, nontrade events, malformed records, other assets and other
wallets from a full upstream page.

The Manifold, Context, DFlow, Myriad, Opinion, Probable and Predict.fun feeds
calculate `history_complete` from the original upstream array length and the
effective requested limit. They also honor next-page/cursor and total-count
metadata. For example, Opinion's effective cap is 20 even when a caller asks
for 25. A full 20-row upstream page cannot establish exhaustion. Myriad clamps
its request to 100; 100 raw rows filtered down to one trade remain incomplete.
Unknown or contradictory response envelopes cannot establish exhaustion.

Azuro combines independent V3 and live-bet feeds. Both raw pages must exhaust,
and the combined normalized result must fit within the returned limit. Short
individual pages do not suffice when their combination is clipped. Configured
market/network subset filters also prevent a claim of complete wallet history.

Hyperliquid's recent `userFills`, MetaDAO's bounded global/ticker scan and Omen's
public creator preview explicitly report incomplete history even when their
normalized arrays are empty or short. Their bounds and source coverage differ
from a complete wallet account ledger. The metadata does not add undocumented
pagination or claim that historical venue data unavailable from these feeds
has been recovered.

Wallet delivery must retain its previous cursor whenever the collector cannot
prove coverage. The `history_complete` property applies to the supported
source window; it is not evidence of complete financial account history.
`history_contiguous` additionally controls whether an older matching event can
prove coverage back to a saved cursor. Global ticker scans and configured
market/network subsets set it false: one old match from a scanned market says
nothing about newer events in unscanned markets. Strict wallet-filtered recent
feeds can retain contiguous coverage while still reporting a bounded history.
