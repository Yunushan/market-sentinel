from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

import web_api
from polymarket import data_api, mdd
from polymarket.history_v2 import PublicHistoryRows, fetch_cursor_history
from polymarket.http_client import PolymarketResponseError


WALLET = "0x" + "1" * 40


def page(rows, cursor=None):
    return {"data": rows, "pagination": {"next_cursor": cursor}}


def position(**changes):
    return {
        "proxy_wallet": WALLET, "token_id": "1", "current_size": 2, "total_size": 10,
        "avg_price": .4, "entry_cost_usdc": .8, "entry_fees_usdc": .04,
        "total_cost_usdc": .84, "current_price": .5, "current_value": 1,
        "realized_pnl": .5, "unrealized_pnl": .2, "total_pnl": .7,
        "last_event_at": 100, "first_entry_at": 50, **changes,
    }


class CursorHistoryTests(unittest.TestCase):
    def test_short_page_with_next_cursor_is_not_eof_and_filters_are_repeated(self):
        loader = Mock(side_effect=[page([{"id": 1}], "opaque"), page([{"id": 2}])])
        result = fetch_cursor_history(loader, limit=3, user=WALLET, start=1, end=100)
        self.assertEqual(result, [{"id": 1}, {"id": 2}])
        self.assertTrue(result.history_complete)
        self.assertEqual([call.kwargs["cursor"] for call in loader.call_args_list], [None, "opaque"])
        for call in loader.call_args_list:
            self.assertEqual((call.kwargs["user"], call.kwargs["start"], call.kwargs["end"]), (WALLET, 1, 100))
            self.assertNotIn("offset", call.kwargs)

    def test_full_terminal_page_proves_eof_at_exact_row_budget(self):
        result = fetch_cursor_history(Mock(return_value=page([{}, {}])), limit=2)
        self.assertEqual(len(result), 2)
        self.assertTrue(result.history_complete)

    def test_cap_before_next_cursor_and_truncated_terminal_page_remain_incomplete(self):
        result = fetch_cursor_history(Mock(return_value=page([{}, {}], "next")), limit=2)
        self.assertFalse(result.history_complete)
        loader = Mock(side_effect=[page([{}, {}], "next"), page([{}, {}])])
        result = fetch_cursor_history(loader, limit=3, page_size=2)
        self.assertEqual(len(result), 3)
        self.assertFalse(result.history_complete)

    def test_missing_cursor_and_cursor_cycles_fail_without_partial_result(self):
        for payloads in ([{"data": [], "pagination": {}}],
                         [page([], "a"), page([], "b"), page([], "a")]):
            with self.subTest(payloads=payloads):
                with self.assertRaises(PolymarketResponseError):
                    fetch_cursor_history(Mock(side_effect=payloads), limit=10)

    def test_empty_progress_pages_are_bounded(self):
        loader = Mock(side_effect=[page([], "a"), page([], "b")])
        with self.assertRaisesRegex(PolymarketResponseError, "page budget"):
            fetch_cursor_history(loader, limit=10, maximum_pages=2)


class MddNativeEconomicsTests(unittest.TestCase):
    def setUp(self):
        mdd.clear_mdd_input_cache()

    def test_open_basis_uses_remaining_cost_not_lifetime_share_count_and_no_double_fee(self):
        result = mdd.build_historical_mdd_payload(mdd.MddInputs(WALLET, [], [position()], [], []))
        self.assertAlmostEqual(result["open_capital_basis_usd"], .84)
        self.assertEqual(result["position_capital_basis"]["sources"], {"v2_total_cost_usdc": 1})
        self.assertEqual(result["position_capital_basis"]["unit"], "USDC")
        self.assertAlmostEqual(result["open_pnl"], .7)

    def test_closed_zero_residual_basis_is_not_historical_acquisition_capital(self):
        closed = position(current_size=0, current_price=0, current_value=0, entry_cost_usdc=0,
                          entry_fees_usdc=0, total_cost_usdc=0, realized_pnl=2, unrealized_pnl=0, total_pnl=2)
        result = mdd.build_historical_mdd_payload(mdd.MddInputs(WALLET, [closed], [], [], []))
        self.assertEqual(result["closed_capital_basis_usd"], 4)
        self.assertEqual(result["points"][0]["timestamp"], 100)
        self.assertEqual(result["cumulative_realized_pnl"], 2)
        self.assertIn("v2_lifetime_bought_shares_times_average_price_fee_component_unverified", result["position_capital_basis"]["sources"])

    def test_missing_native_unrealized_pnl_never_becomes_realized_only_total(self):
        row = position(total_pnl=None, unrealized_pnl=None)
        self.assertIsNone(mdd._position_total_pnl(row))
        self.assertIsNone(web_api._position_total_pnl(row))
        result = mdd.build_historical_mdd_payload(mdd.MddInputs(WALLET, [], [row], [], []), equity_base_usd=100)
        self.assertFalse(result["mdd_available"])
        self.assertIsNone(result["mdd_pct"])

    def test_web_snapshot_helpers_use_native_cursor_scope_and_explicit_eof(self):
        rows = [position()]
        with patch.object(data_api, "get_positions_page_v2", side_effect=[page(rows, "next"), page([])]) as fetch:
            result = web_api._fetch_user_positions_all(WALLET, limit=2)
        self.assertEqual(result, rows)
        self.assertTrue(result.history_complete)
        for call in fetch.call_args_list:
            self.assertEqual(call.kwargs["user"], WALLET)
            self.assertEqual(call.kwargs["status"], "OPEN")
            self.assertEqual(call.kwargs["filter_amount"], 0)
            self.assertTrue(call.kwargs["include_archived"])
        with patch.object(data_api, "get_positions_page_v2", return_value=page(rows, "next")) as fetch:
            capped = web_api._fetch_user_closed_positions_all(WALLET, limit=1)
        self.assertFalse(capped.history_complete)
        self.assertEqual(fetch.call_args.kwargs["status"], "CLOSED")
        self.assertEqual(fetch.call_args.kwargs["sort_direction"], "ASC")

    def test_duplicate_open_token_snapshots_cannot_double_reported_pnl(self):
        result = mdd.build_historical_mdd_payload(
            mdd.MddInputs(WALLET, [], [position(), position(last_event_at=101)], [], []), equity_base_usd=100
        )
        self.assertFalse(result["mdd_available"])
        self.assertEqual(result["mdd_source_quality"]["sources"]["open_positions"]["reasons"],
                         {"duplicate_position_observation": 2})

    def test_duplicate_closed_native_token_at_different_times_cannot_qualify_risk(self):
        closed = position(current_size=0, current_price=0, current_value=0, entry_cost_usdc=0,
                          entry_fees_usdc=0, total_cost_usdc=0, realized_pnl=-5, unrealized_pnl=0, total_pnl=-5)
        duplicate = dict(closed, last_event_at=101, realized_pnl=20, total_pnl=20)
        inputs = mdd.MddInputs(WALLET, [closed, duplicate], [], [], [{
            "token_id": "1", "side": "BUY", "size": 10, "price": .4, "timestamp": 50,
        }])
        for calculate in (mdd.build_historical_mdd_payload, mdd.build_mark_replay_mdd_payload):
            with self.subTest(method=calculate.__name__), patch.object(mdd.clob_rest, "get_batch_price_history") as prices:
                result = calculate(inputs, equity_base_usd=100, equity_base_currency="USDC")
            self.assertFalse(result["mdd_available"])
            self.assertIsNone(result["mdd_usd"])
            self.assertIsNone(result["mdd_pct"])
            self.assertEqual(result["points"], [])
            self.assertEqual(result["cumulative_realized_pnl"], 0)
            self.assertEqual(result["closed_capital_basis_usd"], 0)
            self.assertEqual(result["mdd_source_quality"]["sources"]["closed_positions"]["reasons"],
                             {"duplicate_position_observation": 2})
            prices.assert_not_called()

    def test_distinct_native_tokens_and_legacy_timestamp_observations_remain_supported(self):
        closed = position(current_size=0, current_price=0, current_value=0, entry_cost_usdc=0,
                          entry_fees_usdc=0, total_cost_usdc=0, realized_pnl=-5, unrealized_pnl=0, total_pnl=-5)
        cases = (
            ([closed, dict(closed, token_id="2", last_event_at=101, realized_pnl=20, total_pnl=20)], "USDC"),
            ([{"asset": "1", "timestamp": 100, "realizedPnl": -5},
              {"asset": "1", "timestamp": 101, "realizedPnl": 20}], "USD"),
        )
        for rows, currency in cases:
            with self.subTest(currency=currency):
                result = mdd.build_historical_mdd_payload(
                    mdd.MddInputs(WALLET, rows, [], [], []), equity_base_usd=100, equity_base_currency=currency
                )
                self.assertTrue(result["mdd_available"])
                self.assertEqual(result["mdd_source_quality"]["status"], "valid")
                self.assertEqual(result["cumulative_realized_pnl"], 15)
                self.assertEqual(result["mdd_pct"], 5)

    def test_identical_transaction_fills_preserve_multiplicity_and_leave_identity_ambiguous(self):
        trade = {"transaction_hash": "0xabc", "token_id": "1", "side": "BUY",
                 "size": 2, "price": .4, "timestamp": 50}
        activity = [dict(trade, type="TRADE"), dict(trade, type="TRADE")]
        trades = [dict(trade), dict(trade)]
        canonical = mdd._canonical_trade_events(activity, trades)
        self.assertEqual(len(canonical), 2)
        self.assertEqual(mdd._trade_capital_stats(activity, trades)["buy_notional_usd"], 1.6)
        result = mdd.build_historical_mdd_payload(
            mdd.MddInputs(WALLET, [], [position()], activity, trades), equity_base_usd=100
        )
        self.assertFalse(result["mdd_available"])
        for source in ("activity_events", "trade_rows"):
            self.assertEqual(result["mdd_source_quality"]["sources"][source]["reasons"],
                             {"ambiguous_trade_identity": 2})

    def test_native_financial_identity_contradictions_and_nonfinite_values_invalidate_risk(self):
        for change, reason in (({"total_cost_usdc": 10}, "inconsistent_position_entry_cost"),
                               ({"total_pnl": 10}, "inconsistent_position_pnl"),
                               ({"current_value": 10}, "inconsistent_current_position_value"),
                               ({"unrealized_pnl": 10, "total_pnl": 10.5}, "inconsistent_unrealized_position_pnl"),
                               ({"current_size": float("nan")}, "invalid_numeric_field"),
                               ({"total_size": -1}, "invalid_numeric_field")):
            with self.subTest(change=change):
                result = mdd.build_historical_mdd_payload(mdd.MddInputs(WALLET, [], [position(**change)], [], []), equity_base_usd=100)
                self.assertFalse(result["mdd_available"])
                self.assertEqual(result["position_capital_basis"]["unit"], "USDC")
                self.assertIn(reason, result["mdd_source_quality"]["sources"]["open_positions"]["reasons"])

    def test_bare_short_lists_cannot_prove_history_exhaustion(self):
        with patch.object(mdd, "_fetch_closed_positions", return_value=[position(realized_pnl=-1)]), patch.object(
            mdd, "_fetch_open_positions", return_value=[]
        ), patch.object(mdd, "_fetch_activity_events", return_value=[]), patch.object(mdd, "_fetch_trade_rows", return_value=[]):
            inputs = mdd.fetch_mdd_inputs(WALLET)
        self.assertEqual(inputs.history_coverage["closed_positions"]["status"], "coverage_unproven")
        result = mdd.build_historical_mdd_payload(inputs, equity_base_usd=100)
        self.assertFalse(result["mdd_available"])
        self.assertIn("history_coverage_unproven:closed_positions", result["mdd_unavailable_reasons"])

    def test_actual_fetch_proven_full_final_page_differs_from_cap_and_raw_short_page(self):
        closed = position(current_size=0, current_price=0, current_value=0, entry_cost_usdc=0,
                          entry_fees_usdc=0, total_cost_usdc=0, realized_pnl=-1, unrealized_pnl=0, total_pnl=-1)

        def positions_page(**kwargs):
            return page([closed], "more" if kwargs["limit"] == 1 else None) if kwargs["status"] == "CLOSED" else page([])

        with patch.object(data_api, "get_positions_page_v2", side_effect=positions_page, create=True) as positions_fetch, patch.object(
            data_api, "get_activity_page_v2", return_value=page([])
        ) as activity_fetch, patch.object(data_api, "get_trades_page_v2", return_value=page([]), create=True) as trades_fetch:
            capped = mdd.fetch_mdd_inputs(WALLET, closed_limit=1)
            exhausted = mdd.fetch_mdd_inputs(WALLET, closed_limit=2)
        self.assertEqual(capped.history_coverage["closed_positions"]["status"], "limit_reached")
        self.assertEqual(exhausted.history_coverage["closed_positions"]["status"], "end_of_results")
        self.assertEqual(exhausted.history_coverage["closed_positions"]["exhaustion_evidence"], "pagination.next_cursor=null")
        self.assertEqual(exhausted.history_coverage["closed_positions"]["first_timestamp"], 100)
        for call in positions_fetch.call_args_list:
            if call.kwargs["status"] == "OPEN":
                self.assertEqual(call.kwargs["filter_amount"], 0)
                self.assertTrue(call.kwargs["include_archived"])
        self.assertFalse(trades_fetch.call_args.kwargs["taker_only"])
        self.assertEqual(trades_fetch.call_args.kwargs["start"], 1)
        self.assertFalse(activity_fetch.call_args.kwargs["exclude_deposits_withdrawals"])

    def test_cursor_exhaustion_metadata_survives_cache_without_mutation(self):
        with patch.object(mdd, "_fetch_closed_positions", return_value=PublicHistoryRows([], history_complete=True)), patch.object(
            mdd, "_fetch_open_positions", return_value=PublicHistoryRows([], history_complete=True)
        ), patch.object(mdd, "_fetch_activity_events", return_value=PublicHistoryRows([], history_complete=True)), patch.object(
            mdd, "_fetch_trade_rows", return_value=PublicHistoryRows([], history_complete=True)
        ):
            inputs = mdd.fetch_mdd_inputs(WALLET, cache_ttl_seconds=60)
        inputs.history_coverage["closed_positions"]["exhaustion_evidence"] = "corrupted"
        restored = mdd.fetch_mdd_inputs(WALLET, cache_ttl_seconds=60)
        self.assertTrue(restored.cache_hit)
        self.assertEqual(restored.history_coverage["closed_positions"]["exhaustion_evidence"], "pagination.next_cursor=null")

    def test_matching_mark_replay_cannot_promote_unproven_source_exhaustion(self):
        trade = {"token_id": "1", "side": "BUY", "size": 2, "price": .4, "timestamp": 50}
        snapshot = position(total_size=2, entry_fees_usdc=0, total_cost_usdc=.8,
                            realized_pnl=0, unrealized_pnl=.2, total_pnl=.2)
        coverage = {name: {"status": "coverage_unproven" if name == "trade_rows" else "end_of_results"}
                    for name in ("closed_positions", "open_positions", "activity_events", "trade_rows")}
        inputs = mdd.MddInputs(WALLET, [], [snapshot], [], [trade], history_coverage=coverage)
        with patch.object(mdd.clob_rest, "get_batch_price_history", return_value={
            "history": {"1": [{"t": 50, "p": .4}, {"t": 100, "p": .5}]}
        }):
            result = mdd.build_mark_replay_mdd_payload(inputs, equity_base_usd=100)
        self.assertEqual(result["mark_replay"]["current_snapshot_reconciliation"]["status"], "matched")
        self.assertEqual(result["mark_replay"]["status"], "partial")
        self.assertIn("history_coverage_unproven:trade_rows", result["mark_replay"]["incomplete_reasons"])
        self.assertFalse(result["mdd_available"])
