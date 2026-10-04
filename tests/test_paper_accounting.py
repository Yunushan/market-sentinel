from __future__ import annotations

import json
import random
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from app import App
from core.models import AppConfig, PaperTradeRecord
from core.paper_accounting import SHARE_QUOTE_CURRENCIES, paper_accounting, paper_order_impact, paper_summary, paper_unrealized
from core.storage import load_config, save_config
from market_adapters.manifold import ManifoldAdapter
from market_adapters.types import MarketTrade, PaperOrderRequest, PaperOrderResult
from web_api import paper_payload, record_paper_trade, submit_paper_order


def record(side: str, size: float, price: float | None, timestamp: int, *, market: str = "kalshi", **kwargs):
    return PaperTradeRecord(
        market_id=market, contract_id="contract", side=side, size=size,
        limit_price=price, accepted=True, message="paper accounting test",
        filled_size=kwargs.pop("filled_size", size), average_price=price,
        quote_currency=kwargs.pop("quote_currency", SHARE_QUOTE_CURRENCIES.get(market)),
        created_at=timestamp, **kwargs,
    )


class PaperAccountingTests(unittest.TestCase):
    def test_partial_close_separates_realized_profit_and_remaining_basis(self):
        ledger = [record("SELL", 5, .8, 2), record("BUY", 10, .4, 1)]
        result = paper_accounting(ledger)
        row = result["positions"][0]
        self.assertEqual(row["net_size"], 5)
        self.assertAlmostEqual(row["notional"], 2)
        self.assertAlmostEqual(row["average_price"], .4)
        self.assertAlmostEqual(result["realized"], 2)
        self.assertAlmostEqual(paper_unrealized(row, .5), .5)
        self.assertEqual(App._paper_position_rows(ledger), result["positions"])

    def test_complete_roundtrip_removes_position_and_retains_realized_profit(self):
        ledger = [record("SELL", 10, .8, 2), record("BUY", 10, .4, 1)]
        payload = paper_payload(AppConfig(paper_trades=ledger))
        self.assertEqual(payload["positions"], [])
        self.assertEqual(payload["summary"]["positions"], 0)
        self.assertAlmostEqual(payload["summary"]["realized"], 4)
        self.assertIsNone(payload["summary"]["unrealized"])
        self.assertEqual(payload["accounting"]["closed_positions"][0]["net_size"], 0)

    def test_weighted_additions_actual_partial_fills_and_multiple_fills(self):
        ledger = [record("BUY", 10, .6, 2, filled_size=5), record("BUY", 10, .4, 1, filled_size=5)]
        row = paper_accounting(ledger)["positions"][0]
        self.assertEqual(row["net_size"], 10)
        self.assertAlmostEqual(row["notional"], 5)
        self.assertAlmostEqual(row["average_price"], .5)
        self.assertEqual(row["execution_assumptions"], [])

    def test_reversal_and_partial_short_close_have_new_inventory_basis(self):
        ledger = [record("BUY", 2, .5, 3), record("SELL", 15, .8, 2), record("BUY", 10, .4, 1)]
        result = paper_accounting(ledger)
        row = result["positions"][0]
        self.assertEqual(row["net_size"], -3)
        self.assertAlmostEqual(row["average_price"], .8)
        self.assertAlmostEqual(row["notional"], -2.4)
        self.assertAlmostEqual(result["realized"], 4.6)
        self.assertAlmostEqual(paper_unrealized(row, .7), .3)

    def test_same_second_uses_durable_newest_first_insertion_order(self):
        ledger = [record("SELL", 5, .8, 100), record("BUY", 10, .4, 100)]
        result = paper_accounting(ledger)
        self.assertAlmostEqual(result["positions"][0]["average_price"], .4)
        self.assertAlmostEqual(result["realized"], 2)

    def test_preview_impact_uses_remaining_basis_and_discloses_assumption(self):
        ledger = [record("BUY", 10, .4, 1)]
        order = PaperOrderRequest(market_id="kalshi", contract_id="contract", side="SELL", size=5, limit_price=.8)
        impact = paper_order_impact(ledger, order)
        self.assertEqual(impact["projected_net"], 5)
        self.assertAlmostEqual(impact["projected_notional"], 2)
        self.assertAlmostEqual(impact["projected_average"], .4)
        self.assertAlmostEqual(impact["projected_realized"], 2)
        self.assertEqual(impact["execution_assumptions"], ["assumed_full_fill_at_limit"])

    def test_dry_run_full_fill_is_hypothetical_and_unpriced_cost_stays_unknown(self):
        priced = paper_accounting([record("BUY", 10, .4, 1, filled_size=0)])
        self.assertEqual(priced["execution_assumptions"], ["assumed_full_fill_at_limit"])
        unknown = paper_accounting([record("BUY", 10, None, 1, filled_size=0)])
        row = unknown["positions"][0]
        self.assertEqual(row["net_size"], 10)
        self.assertIsNone(row["notional"])
        self.assertIsNone(paper_unrealized(row, .5))
        self.assertEqual(unknown["status"], "incomplete")

    def test_budget_stake_and_forecast_venues_do_not_invent_share_metrics(self):
        for market in ("manifold", "myriad_markets", "azuro", "betfair_exchange", "metaculus",
                       "opinion_labs", "xo_market", "hedgehog_markets"):
            with self.subTest(market=market):
                result = paper_accounting([record("BUY", 10, .4, 1, market=market)])
                row = result["positions"][0]
                self.assertIsNone(row["net_size"])
                self.assertIsNone(row["notional"])
                self.assertIsNone(result["realized"])
                self.assertIn("unsupported_venue_quantity_model", result["incomplete_reasons"])
                summary = paper_summary(result["positions"], {}, result)
                self.assertIsNone(summary["gross_size"])
                self.assertIsNone(summary["entry_notional"])

    def test_cross_currency_portfolio_does_not_add_native_quote_assets(self):
        result = paper_accounting([record("BUY", 10, .4, 1), record("BUY", 10, .4, 1, market="polymarket")])
        marks = {(row["market_id"], row["contract_id"]): {"mark_price": .5} for row in result["positions"]}
        summary = paper_summary(result["positions"], marks, result)
        self.assertIsNone(summary["entry_notional"])
        self.assertIsNone(summary["unrealized"])
        self.assertIn("multiple_quote_currencies", summary["incomplete_reasons"])

    def test_native_collateral_assets_are_not_mislabeled_usdc(self):
        for market, currency in (("polymarket", "pUSD"), ("blinq", "pUSD"),
                                 ("predict_fun", "USDT"), ("probable", "USDT"),
                                 ("limitless_exchange", "USDC")):
            with self.subTest(market=market):
                result = paper_accounting([record("BUY", 10, .4, 1, market=market)])
                self.assertEqual(result["positions"][0]["currency"], currency)
                self.assertEqual(result["quote_currency"], currency)

    def test_unproven_collateral_keeps_share_count_but_no_currency_amounts(self):
        for market in ("context_v2", "xmarket"):
            with self.subTest(market=market):
                result = paper_accounting([record("BUY", 10, .4, 1, market=market)])
                row = result["positions"][0]
                self.assertEqual(row["net_size"], 10)
                self.assertAlmostEqual(row["average_price"], .4)
                self.assertIsNone(row["currency"])
                self.assertIsNone(row["notional"])
                self.assertIsNone(row["realized"])
                self.assertIsNone(paper_unrealized(row, .5))
                self.assertIn("quote_currency_unavailable", result["incomplete_reasons"])
                order = PaperOrderRequest(market_id=market, contract_id="contract", side="BUY", size=1, limit_price=.4)
                self.assertIsNone(paper_order_impact([], order)["order_notional"])

    def test_legacy_quote_asset_is_not_retroactively_inferred_from_current_venue(self):
        for market in ("kalshi", "polymarket", "predict_fun", "probable", "limitless_exchange"):
            with self.subTest(market=market):
                legacy = record("BUY", 10, .4, 1, market=market, quote_currency=None).to_dict()
                legacy.pop("quote_currency")
                restored = PaperTradeRecord.from_dict(legacy)
                result = paper_accounting([restored])
                self.assertEqual(result["positions"][0]["net_size"], 10)
                self.assertIsNone(result["positions"][0]["notional"])
                self.assertIsNone(result["realized"])
                self.assertIn("quote_currency_unavailable", result["incomplete_reasons"])

    def test_collateral_change_inside_one_contract_does_not_mix_financial_basis(self):
        ledger = [record("BUY", 5, .6, 2, market="polymarket", quote_currency="pUSD"),
                  record("BUY", 10, .4, 1, market="polymarket", quote_currency="USDC")]
        result = paper_accounting(ledger)
        row = result["positions"][0]
        self.assertEqual(row["net_size"], 15)
        self.assertIsNone(row["currency"])
        self.assertIsNone(row["notional"])
        self.assertIsNone(row["realized"])
        self.assertIn("inconsistent_quote_currency", result["incomplete_reasons"])

    def test_new_record_persists_native_quote_asset_after_reload(self):
        cfg = AppConfig()
        order = PaperOrderRequest(market_id="probable", contract_id="contract", side="BUY", size=1, limit_price=.4)
        result = PaperOrderResult(market_id="probable", contract_id="contract", accepted=True, message="preview")
        new_record = record_paper_trade(cfg, order, result)
        self.assertEqual(new_record.quote_currency, "USDT")
        restored = AppConfig.from_dict(cfg.to_dict())
        self.assertEqual(restored.paper_trades[0].quote_currency, "USDT")
        self.assertEqual(paper_accounting(restored.paper_trades)["quote_currency"], "USDT")

    def test_malformed_or_duplicate_fills_fail_closed_without_nonfinite_output(self):
        for invalid in (float("nan"), float("inf"), -1, True):
            with self.subTest(invalid=invalid):
                result = paper_accounting([record("BUY", 10, .4, 1, filled_size=invalid)])
                self.assertIsNone(result["positions"][0]["net_size"])
                self.assertEqual(result["status"], "incomplete")
                json.dumps(result, allow_nan=False)
        first = record("BUY", 10, .4, 1)
        result = paper_accounting([first, first])
        self.assertIsNone(result["positions"][0]["net_size"])
        self.assertIn("duplicate_record_identity", result["incomplete_reasons"])

    def test_invalid_timestamp_identity_and_marks_cannot_become_financial_evidence(self):
        for timestamp in (-1, .5, True, float("nan")):
            with self.subTest(timestamp=timestamp):
                result = paper_accounting([record("BUY", 1, .4, timestamp)])
                self.assertIsNone(result["positions"][0]["net_size"])
                self.assertIn("invalid_record_identity_or_timestamp", result["incomplete_reasons"])
        result = paper_accounting([record("BUY", 1, .4, 1, id=" ")])
        self.assertIsNone(result["positions"][0]["net_size"])
        known = paper_accounting([record("BUY", 1, .4, 1)])
        summary = paper_summary(known["positions"], {("kalshi", "contract"): {"mark_price": 5}}, known)
        self.assertEqual(summary["marked"], 0)
        self.assertIsNone(summary["unrealized"])

    def test_invalid_persisted_quote_asset_is_rejected(self):
        for currency in ("UNKNOWN", True, 1, ["USD"]):
            with self.subTest(currency=currency):
                data = record("BUY", 1, .4, 1).to_dict()
                data["quote_currency"] = currency
                with self.assertRaisesRegex(ValueError, "quote currency"):
                    PaperTradeRecord.from_dict(data)

    def test_more_than_200_history_records_preserve_old_inventory_after_reload(self):
        cfg = AppConfig()
        for index in range(201):
            contract = "old_inventory" if index == 0 else "later"
            order = PaperOrderRequest(market_id="kalshi", contract_id=contract, side="BUY", size=1, limit_price=.4)
            result = PaperOrderResult(market_id="kalshi", contract_id=contract, accepted=True, message="test", filled_size=1, average_price=.4)
            record_paper_trade(cfg, order, result)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.json"
            save_config(cfg, path)
            restored = load_config(path)
        self.assertEqual(len(restored.paper_trades), 201)
        positions = paper_accounting(restored.paper_trades)["positions"]
        self.assertEqual(next(row for row in positions if row["contract_id"] == "old_inventory")["net_size"], 1)

    def test_full_capacity_rejects_before_adapter_or_ledger_mutation(self):
        cfg = AppConfig(paper_trades=[record("BUY", 1, .4, 1)])
        registry = Mock()
        with patch("core.paper_accounting.MAX_PAPER_TRADES", 1):
            with self.assertRaisesRegex(ValueError, "capacity"):
                submit_paper_order(cfg, registry, {"market_id": "kalshi", "contract_id": "contract", "side": "BUY", "size": 1})
        self.assertEqual(len(cfg.paper_trades), 1)
        registry.create.assert_not_called()

    def test_desktop_keeps_more_than_200_records_and_rejects_capacity(self):
        cfg = AppConfig(paper_trades=[record("BUY", 1, .4, index) for index in range(200)])
        harness = Mock(cfg=cfg)
        order = PaperOrderRequest(market_id="kalshi", contract_id="older", side="BUY", size=1, limit_price=.4)
        result = PaperOrderResult(market_id="kalshi", contract_id="older", accepted=True, message="test")

        def persist(_harness, **changes):
            cfg.paper_trades = changes["paper_trades"]
            return True

        with patch.object(App, "_persist_config_changes", side_effect=persist), patch.object(App, "_refresh_paper_trade_table"):
            App._record_paper_trade(harness, order, result)
        self.assertEqual(len(cfg.paper_trades), 201)
        with patch("core.paper_accounting.MAX_PAPER_TRADES", 201), patch.object(App, "_persist_config_changes") as persist:
            with self.assertRaisesRegex(ValueError, "capacity"):
                App._record_paper_trade(harness, order, result)
        persist.assert_not_called()
        self.assertEqual(len(cfg.paper_trades), 201)

    def test_random_ledger_matches_independent_cashflow_equity_identity(self):
        rng = random.Random(20261003)
        for _case in range(100):
            chronological = [record(rng.choice(("BUY", "SELL")), rng.randint(1, 10), rng.randint(1, 99) / 100, index + 1)
                             for index in range(30)]
            result = paper_accounting(list(reversed(chronological)))
            quantity = sum(row.size * (1 if row.side == "BUY" else -1) for row in chronological)
            cash_spent = sum(row.size * row.average_price * (1 if row.side == "BUY" else -1) for row in chronological)
            marked_value = quantity * .5
            unrealized = sum(paper_unrealized(row, .5) for row in result["positions"])
            self.assertAlmostEqual(marked_value - cash_spent, result["realized"] + unrealized)
            self.assertAlmostEqual(sum(row["net_size"] for row in result["positions"]), quantity)

    def test_manifold_candles_order_distinct_prices_across_bets_and_fills(self):
        adapter = ManifoldAdapter()
        trades = [MarketTrade(market_id="manifold", contract_id="example:YES", trade_id=identity, side="BUY", price=price, size=1, timestamp=timestamp)
                  for identity, price, timestamp in (("new", .8, 1760000030), ("old", .2, 1760000010), ("middle", .5, 1760000020))]
        with patch.object(adapter, "list_trades", return_value=trades):
            candle = adapter.list_candles("example:YES", resolution="1m")[0]
        self.assertEqual((candle.open, candle.close), (.2, .8))
        self.assertEqual((candle.high, candle.low, candle.volume), (.8, .2, 3))
        self.assertEqual(candle.raw["trade_ids"], ["old", "middle", "new"])
