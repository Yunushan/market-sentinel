from __future__ import annotations

from contextlib import closing
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import web_api
from polymarket import mdd
from polymarket.analytics_cache import _payload_summary, mdd_payload_to_csv
from polymarket.leaderboard_state import LeaderboardStateStore


WALLET = "0x" + "1" * 40


def native_closed():
    return mdd.MddInputs(WALLET, [{"token_id": "token", "last_event_at": 100,
        "current_size": 0, "total_size": 20, "avg_price": .5, "current_value": 0,
        "entry_cost_usdc": 0, "entry_fees_usdc": 0, "total_cost_usdc": 0,
        "realized_pnl": -5, "unrealized_pnl": 0, "total_pnl": -5}], [], [], [])


class MddCurrencyTests(unittest.TestCase):
    def test_native_derived_basis_and_curve_are_explicit_usdc(self):
        result = mdd.build_historical_mdd_payload(native_closed())
        self.assertEqual((result["source_economics_currency"], result["quote_currency"],
                          result["equity_base_currency"]), ("USDC", "USDC", "USDC"))
        self.assertEqual(result["mdd_pct"], 50)
        self.assertTrue(result["mdd_percentage_available"])

    def test_explicit_legacy_usd_base_cannot_divide_native_usdc_losses(self):
        result = mdd.build_historical_mdd_payload(native_closed(), equity_base_usd=100)
        self.assertEqual(result["equity_base_currency"], "USD")
        self.assertEqual(result["quote_currency"], "USDC")
        self.assertEqual(result["mdd_usd"], 5)
        self.assertIsNone(result["mdd_pct"])
        self.assertIsNone(result["pct_peak_timestamp"])
        self.assertEqual(result["mdd_currency_status"], "mismatch")
        self.assertIn("equity_base_currency_mismatch", result["mdd_unavailable_reasons"])

    def test_explicit_usdc_basis_qualifies_native_percentage_without_conversion(self):
        result = mdd.build_historical_mdd_payload(native_closed(), equity_base_usd=100, equity_base_currency="USDC")
        self.assertEqual(result["mdd_pct"], 5)
        self.assertEqual(result["mdd_currency_status"], "consistent")

    def test_legacy_supplied_calculator_keeps_its_declared_usd_contract(self):
        inputs = mdd.MddInputs(WALLET, [{"timestamp": 100, "realizedPnl": -5}], [], [], [])
        result = mdd.build_historical_mdd_payload(inputs, equity_base_usd=100)
        self.assertEqual(result["quote_currency"], "USD")
        self.assertEqual(result["equity_base_currency"], "USD")
        self.assertEqual(result["mdd_pct"], 5)

    def test_mark_replay_cannot_overwrite_the_dimensional_rejection(self):
        inputs = mdd.MddInputs(WALLET, [], [], [], [{"token_id": "token", "side": "BUY",
            "size": 100, "price": 1, "timestamp": 50}])
        with patch.object(mdd.clob_rest, "get_batch_price_history", return_value={
            "history": {"token": [{"t": 50, "p": 1}, {"t": 100, "p": .5}]}
        }):
            result = mdd.build_mark_replay_mdd_payload(inputs, equity_base_usd=100)
        self.assertEqual(result["mdd_usd"], 50)
        self.assertIsNone(result["mdd_pct"])
        self.assertIn("equity_base_currency_mismatch", result["mdd_unavailable_reasons"])

    def test_stored_summary_preserves_new_currency_without_relabeling_old_audits(self):
        payload = mdd.build_historical_mdd_payload(native_closed(), equity_base_usd=100)
        summary = LeaderboardStateStore._mdd_summary(payload)
        self.assertEqual(summary["quote_currency"], "USDC")
        self.assertEqual(summary["equity_base_currency"], "USD")
        historical = LeaderboardStateStore._mdd_summary({"mdd_usd": 5, "mdd_pct": 5})
        self.assertNotIn("quote_currency", historical)
        self.assertNotIn("equity_base_currency", historical)
        self.assertEqual(_payload_summary(payload)["quote_currency"], "USDC")
        self.assertNotIn("quote_currency", _payload_summary({"mdd_usd": 5}))
        self.assertIn("quote_currency,equity_base_currency", mdd_payload_to_csv(payload).splitlines()[0])

    def test_reopened_legacy_board_keeps_usd_separate_from_native_mdd_usdc(self):
        source = {"proxyWallet": WALLET, "pnl": 10, "vol": 100}
        row = web_api.normalize_polymarket_leaderboard_row(source, 1)
        payload = mdd.build_historical_mdd_payload(native_closed(), equity_base_usd=100, equity_base_currency="USDC")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "board.sqlite3"
            with closing(LeaderboardStateStore(path)) as store:
                store.prepare({}, resume=False)
                store.record_page(0, 1, [row])
                saved = next(store.iter_results({}, require_mdd=False, sort="pnl_usd", direction="DESC", limit=None))
                store.set_mdd(saved["id"], payload)
            with closing(LeaderboardStateStore(path)) as store:
                restored = next(store.iter_results({}, require_mdd=True, sort="pnl_usd", direction="DESC", limit=None))
        self.assertEqual(restored["source_api_version"], 1)
        self.assertEqual(restored["quote_currency"], "USD")
        self.assertEqual(restored["volume_unit"], "USD")
        self.assertEqual((restored["pnl_usd"], restored["volume_usd"], restored["roi_pct"]), (10, 100, 10))
        self.assertIsNone(restored["volume_shares"])
        self.assertEqual(restored["raw"], source)
        self.assertEqual((restored["mdd_quote_currency"], restored["mdd_source_economics_currency"],
                          restored["mdd_equity_base_currency"]), ("USDC", "USDC", "USDC"))
        self.assertEqual(restored["mdd_pct"], 5)

    def test_percentage_filter_rejects_unconverted_usd_base_and_explicit_usdc_opts_in(self):
        page = {"data": [{"user_id": WALLET, "pnl": 10, "volume": 100}],
                "pagination": {"limit": 50, "offset": 0, "has_more": False, "next_cursor": None}}
        query = {"compute_mdd": ["true"], "max_mdd_pct": ["20"], "equity_base_usd": ["100"]}
        with patch.object(web_api.data_api, "get_leaderboard_v2_page", return_value=page), patch.object(
            mdd, "fetch_mdd_inputs", return_value=native_closed()
        ), patch.object(web_api, "attach_polymarket_mdd_audit_cache", return_value={}):
            unknown = web_api.polymarket_leaderboard_payload(query)
            qualified = web_api.polymarket_leaderboard_payload({**query, "equity_base_currency": ["USDC"]})
        self.assertEqual(unknown["counts"]["returned"], 0)
        self.assertEqual(qualified["counts"]["returned"], 1)
        row = qualified["rows"][0]
        self.assertEqual((row["mdd_quote_currency"], row["mdd_equity_base_currency"]), ("USDC", "USDC"))
        self.assertEqual(row["mdd_pct"], 5)

    def test_invalid_currency_rejected_before_history_network(self):
        for value in ("USDT", "usd", "", False):
            for reader in (mdd.polymarket_user_mdd_payload_v2, mdd.polymarket_user_mdd_payload_mark_replay):
                with self.subTest(value=value, reader=reader.__name__), patch.object(mdd, "fetch_mdd_inputs") as fetch:
                    with self.assertRaisesRegex(ValueError, "currency"):
                        reader(WALLET, equity_base_usd=100, equity_base_currency=value)
                fetch.assert_not_called()

    def test_mixed_native_and_declared_legacy_currency_is_unavailable(self):
        inputs = native_closed()
        inputs.closed_positions.append({"timestamp": 101, "realizedPnl": -1})
        result = mdd.build_historical_mdd_payload(inputs, equity_base_usd=100)
        self.assertIsNone(result["quote_currency"])
        self.assertIsNone(result["mdd_pct"])
