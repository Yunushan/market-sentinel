from __future__ import annotations

import unittest
from unittest.mock import patch

from core.wallet_activity import ActivityHistoryIncompleteError, ActivitySnapshot, collect_adapter_activity
from market_adapters.activity_history import activity_snapshot
from market_adapters.azuro import AzuroAdapter
from market_adapters.context_v2 import ContextV2Adapter
from market_adapters.dflow import DFlowAdapter
from market_adapters.hyperliquid import HyperliquidAdapter
from market_adapters.legacy_web3 import OmenAdapter
from market_adapters.manifold import ManifoldAdapter
from market_adapters.metadao import MetaDAOAdapter
from market_adapters.myriad import MyriadAdapter
from market_adapters.opinion import OpinionAdapter
from market_adapters.predict_fun import PredictFunAdapter
from market_adapters.probable import ProbableAdapter
from market_adapters.types import MarketTrade


WALLET = "0x" + "a" * 40
SOLANA = "solana:" + "1" * 32


class ActivityHistoryMetadataTests(unittest.TestCase):
    def assert_snapshot(self, rows, complete):
        self.assertIsInstance(rows, list)
        self.assertIsInstance(rows, ActivitySnapshot)
        self.assertEqual(rows.history_complete, complete)

    def test_generic_count_uses_raw_rows_not_filtered_rows(self):
        rows = activity_snapshot([], {"data": [{}, None, "malformed"]}, effective_limit=3, row_keys=("data",))
        self.assert_snapshot(rows, False)
        self.assert_snapshot(activity_snapshot([], {"data": []}, effective_limit=3, row_keys=("data",)), True)
        self.assert_snapshot(activity_snapshot([], {"unrecognized": []}, effective_limit=3, row_keys=("data",)), False)

    def test_next_page_metadata_blocks_short_page_exhaustion(self):
        for payload in ({"data": [], "nextCursor": "next"}, {"data": [], "pagination": {"hasMore": True}},
                        {"result": {"data": [], "total": 20}}):
            self.assert_snapshot(activity_snapshot([], payload, effective_limit=25, row_keys=("data",)), False)

    def test_manifold_pending_bets_do_not_disguise_a_full_page(self):
        adapter = ManifoldAdapter()
        with patch.object(adapter, "_get", return_value=[{"isFilled": False}] * 25):
            self.assert_snapshot(adapter.list_activity("manifold:alice"), False)
        with patch.object(adapter, "_get", return_value=[{"isFilled": False}] * 24):
            self.assert_snapshot(adapter.list_activity("manifold:alice"), True)

    def test_collector_cannot_treat_a_filtered_empty_full_page_as_eof(self):
        adapter = ManifoldAdapter()
        with patch.object(adapter, "_get", side_effect=lambda _path, params: [{"isFilled": False}] * params["limit"]):
            with self.assertRaises(ActivityHistoryIncompleteError):
                collect_adapter_activity(adapter.list_activity, "manifold:alice", since=100)

    def test_context_filtered_capped_orders_keep_history_unknown(self):
        adapter = ContextV2Adapter()
        with patch.object(adapter, "_get", return_value={"orders": [{"status": "cancelled"}] * 100}):
            self.assert_snapshot(adapter.list_activity(WALLET, limit=100), False)

    def test_dflow_unmappable_rows_keep_raw_page_size(self):
        adapter = DFlowAdapter()
        with patch.object(adapter, "resolve_credential"), patch.object(adapter, "_metadata_get", return_value={"data": [None] * 25}):
            self.assert_snapshot(adapter.list_activity(SOLANA), False)

    def test_myriad_nested_nontrade_events_and_provider_cap_do_not_prove_eof(self):
        adapter = MyriadAdapter()
        with patch.object(adapter, "_fetch_activity_payload", return_value={"data": {"items": [{"action": "claim"}] * 100}}):
            self.assert_snapshot(adapter.list_activity(WALLET, limit=200), False)
        subset = MyriadAdapter({"myriad_activity_market_id": "only_one_market"})
        with patch.object(subset, "_fetch_activity_payload", return_value={"data": []}):
            self.assert_snapshot(subset.list_activity(WALLET), False)

    def test_probable_mixed_events_do_not_hide_raw_page_saturation(self):
        adapter = ProbableAdapter()
        with patch.object(adapter, "_clob_get", return_value={"data": {"activity": [{"type": "SPLIT"}] * 25}}):
            self.assert_snapshot(adapter.list_activity(WALLET), False)

    def test_opinion_effective_twenty_row_cap_is_used_for_requested_twenty_five(self):
        adapter = OpinionAdapter()
        with patch.object(adapter, "_get", return_value={"result": {"list": [{}] * 20}}), patch.object(adapter, "_activity_from_trade", return_value={}):
            self.assert_snapshot(adapter.list_activity(WALLET, limit=25), False)
        subset = OpinionAdapter({"opinion_activity_market_id": "one_market"})
        with patch.object(subset, "_get", return_value={"result": {"list": []}}):
            self.assert_snapshot(subset.list_activity(WALLET), False)

    def test_predictfun_filtered_account_events_keep_raw_count(self):
        adapter = PredictFunAdapter()

        def account(operation, **kwargs):
            return {"data": {"address": WALLET}} if operation == "account" else {"data": [{}] * 25}

        with patch.object(adapter, "account_recovery", side_effect=account), patch.object(adapter, "_normalize_account_activity", return_value=[]):
            self.assert_snapshot(adapter.list_activity(WALLET), False)

    def test_azuro_both_raw_feeds_must_be_exhausted_and_unclipped(self):
        adapter = AzuroAdapter()
        with patch.object(adapter, "account_recovery", return_value={"v3_bets": [{}] * 25, "live_bets": []}):
            self.assert_snapshot(adapter.list_activity(WALLET), False)
        payload = {"v3_bets": [{}] * 12, "live_bets": [{}] * 12}

        def normalized(_wallet, source, desired):
            return [{}] * min(len(source.get("v3_bets", [])) + len(source.get("live_bets", [])), desired)

        with patch.object(adapter, "account_recovery", return_value=payload), patch.object(adapter, "_normalize_activity_payload", side_effect=normalized):
            self.assert_snapshot(adapter.list_activity(WALLET, limit=20), False)
            self.assert_snapshot(adapter.list_activity(WALLET, limit=25), True)

    def test_recent_and_global_preview_feeds_never_claim_complete_history(self):
        hyperliquid = HyperliquidAdapter()
        with patch.object(hyperliquid, "_info", return_value=[]):
            self.assert_snapshot(hyperliquid.list_activity(WALLET), False)
        metadao = MetaDAOAdapter()
        with patch.object(metadao, "_tickers", return_value=[]):
            self.assert_snapshot(metadao.list_activity(SOLANA), False)
        omen = OmenAdapter()
        with patch.object(omen, "_graphql", return_value={"fpmmTrades": []}):
            self.assert_snapshot(omen.list_activity(WALLET), False)

    def test_old_global_metadao_match_cannot_prove_unscanned_ticker_coverage(self):
        adapter = MetaDAOAdapter()
        trade = MarketTrade(
            market_id="metadao", contract_id="one_ticker", trade_id="old_match", side="BUY",
            price=.5, size=1, timestamp=50, raw={"maker": "1" * 32, "event": {"txnId": "signature"}},
        )
        with (
            patch.object(adapter, "_tickers", return_value=[{"ticker_id": "one_ticker"}]),
            patch.object(adapter, "_ticker_id", return_value="one_ticker"),
            patch.object(adapter, "_contract_id", return_value="one_ticker"),
            patch.object(adapter, "_scan_public_trades", return_value={"trades": [trade]}),
        ):
            snapshot = adapter.list_activity(SOLANA)
            self.assertEqual(snapshot[0]["timestamp"], 50)
            self.assertFalse(snapshot.history_contiguous)
            with self.assertRaises(ActivityHistoryIncompleteError):
                collect_adapter_activity(adapter.list_activity, SOLANA, since=100)
