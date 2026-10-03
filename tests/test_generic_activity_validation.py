from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from core.wallet_activity import (
    ACTIVITY_CLOCK_SKEW_SECONDS,
    ActivityHistoryIncompleteError,
    ActivitySnapshot,
    collect_adapter_activity,
)


def fill(timestamp=1000, identity="fill-a", **changes):
    return {"timestamp": timestamp, "activityId": identity, "asset": "token", "side": "BUY",
            "size": 1, "price": .4, **changes}


class GenericActivityValidationTests(unittest.TestCase):
    def collect(self, rows, *, complete=True, since=0):
        loader = Mock(return_value=ActivitySnapshot(rows, history_complete=complete))
        with patch("core.wallet_activity.time.time", return_value=1000):
            return collect_adapter_activity(loader, "wallet", since=since)

    def test_invalid_timestamp_cannot_prove_eof_or_old_cursor_coverage(self):
        for timestamp in (True, False, 1000.0, 12.9, "1000", None, 0, -1, float("nan"), float("inf")):
            for complete, since in ((True, 0), (False, 1100)):
                with self.subTest(timestamp=timestamp, complete=complete), self.assertRaisesRegex(
                    ActivityHistoryIncompleteError, "timestamp"
                ):
                    self.collect([fill(timestamp)], complete=complete, since=since)

    def test_future_cursor_poisoning_fails_and_documented_clock_skew_is_bounded(self):
        boundary = 1000 + ACTIVITY_CLOCK_SKEW_SECONDS
        self.assertEqual(self.collect([fill(boundary)])[0]["timestamp"], boundary)
        with self.assertRaisesRegex(ActivityHistoryIncompleteError, "timestamp"):
            self.collect([fill(boundary + 1)])

    def test_explicit_duplicate_identity_is_not_discarded_even_if_payloads_match(self):
        for duplicate in (fill(), fill(size=2), fill(timestamp=999)):
            with self.subTest(duplicate=duplicate), self.assertRaisesRegex(ActivityHistoryIncompleteError, "ambiguous"):
                self.collect([fill(), duplicate])

    def test_indistinguishable_transaction_fills_fail_without_inventing_sequences(self):
        row = {"timestamp": 1000, "transactionHash": "0xabc", "asset": "token",
               "proxyWallet": "wallet", "side": "BUY", "price": .4, "size": 1}
        with self.assertRaisesRegex(ActivityHistoryIncompleteError, "ambiguous"):
            self.collect([row, dict(row)])

    def test_distinct_explicit_fill_ids_preserve_same_transaction_multiplicity(self):
        rows = [fill(identity="a", transactionHash="0xabc"), fill(identity="b", transactionHash="0xabc")]
        self.assertEqual(self.collect(rows), rows)

    def test_expansion_uses_one_clock_bound_and_returns_no_partial_page_on_failure(self):
        rows = [fill(1000 - i, identity=str(i)) for i in range(25)]
        loader = Mock(side_effect=[ActivitySnapshot(rows, history_complete=False),
                                   ActivitySnapshot([*rows, fill(1400, identity="corrupt")], history_complete=True)])
        with patch("core.wallet_activity.time.time", side_effect=[1000, 2000]) as clock:
            with self.assertRaisesRegex(ActivityHistoryIncompleteError, "timestamp"):
                collect_adapter_activity(loader, "wallet", since=950)
        self.assertEqual(clock.call_count, 1)
        self.assertEqual([call.kwargs["limit"] for call in loader.call_args_list], [25, 50])
