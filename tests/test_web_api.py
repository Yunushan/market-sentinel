from __future__ import annotations

import json
import io
import os
import threading
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from core.models import AppConfig, CopyTradeSettings, PaperTradeRecord, PriceAlert, WalletWatch
from core.storage import load_config, save_config
from market_adapters.base import MarketAdapter
from market_adapters.types import (
    MarketCapabilities,
    MarketMetadata,
    OrderBookLevel,
    OrderBookSnapshot,
    PaperOrderRequest,
    PaperOrderResult,
    PriceSnapshot,
)
from polymarket.analytics_cache import POLYMARKET_MDD_AUDIT_KIND, store_analytics_artifact
from polymarket.gamma import ProfileResult
from polymarket.http_client import PolymarketRateLimitError
from polymarket.mdd import MDD_METHOD_MARK_REPLAY, MDD_METHOD_V2
from web_api import (
    _read_json_body,
    add_wallet_watch,
    alert_from_payload,
    alerts_payload,
    api_error_payload,
    app_state_payload,
    apply_copy_settings_patch,
    apply_config_patch,
    apply_market_patch,
    copy_payload,
    copy_preview_payload,
    copy_trade_preview_from_activity,
    delete_alert,
    delete_wallet_watch,
    health_payload,
    history_refill_payload,
    live_preflight_payload,
    live_safety_payload,
    markets_payload,
    paper_payload,
    paper_order_impact,
    paper_order_from_payload,
    paper_quote_limit_payload,
    paper_quote_payload,
    paper_position_rows,
    polymarket_clob_readiness_payload,
    polymarket_live_validation_report_purge_payload,
    polymarket_live_validation_report_store_payload,
    polymarket_live_validation_reports_payload,
    polymarket_live_validation_payload,
    polymarket_leaderboard_payload,
    polymarket_mdd_export_payload,
    polymarket_user_mdd_payload,
    polymarket_user_search_payload,
    position_refill_payload,
    poll_wallet_activity,
    refresh_selected_paper_mark,
    refresh_alert_price,
    ReactGuiHandler,
    ReactGuiServer,
    submit_paper_order,
    update_wallet_watch,
    wallets_payload,
)


WALLET = "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
WALLET_2 = "0xcccccccccccccccccccccccccccccccccccccccc"


class FakePaperAdapter(MarketAdapter):
    metadata = MarketMetadata(
        market_id="kalshi",
        display_name="Kalshi",
        capabilities=MarketCapabilities(
            price_reading=True,
            orderbook_reading=True,
            alerts=True,
            paper_trading=True,
            live_trading=True,
        ),
    )

    def __init__(self) -> None:
        super().__init__({})
        self.prices: list[str] = []
        self.orderbooks: list[str] = []
        self.orders: list[PaperOrderRequest] = []

    def get_price(self, contract_id: str) -> PriceSnapshot:
        self.prices.append(contract_id)
        return PriceSnapshot(
            market_id="kalshi",
            contract_id=contract_id,
            last=0.62,
            bid=0.60,
            ask=0.64,
            source="test",
        )

    def get_orderbook(self, contract_id: str) -> OrderBookSnapshot:
        self.orderbooks.append(contract_id)
        return OrderBookSnapshot(
            market_id="kalshi",
            contract_id=contract_id,
            bids=[OrderBookLevel(price=0.58, size=12)],
            asks=[OrderBookLevel(price=0.66, size=15)],
        )

    def place_paper_order(self, order: PaperOrderRequest) -> PaperOrderResult:
        self.orders.append(order)
        return PaperOrderResult(
            market_id=order.market_id,
            contract_id=order.contract_id,
            accepted=True,
            message="accepted",
            filled_size=order.size,
            average_price=order.limit_price,
            raw={"dry_run": True},
        )


class FakePolymarketAdapter(MarketAdapter):
    metadata = MarketMetadata(
        market_id="polymarket",
        display_name="Polymarket",
        capabilities=MarketCapabilities(
            price_reading=True,
            alerts=True,
            orderbook_reading=True,
            live_trading=True,
            copy_trading=True,
        ),
    )

    def __init__(self) -> None:
        super().__init__({})
        self.prices: list[str] = []
        self.orderbooks: list[str] = []

    def get_price(self, contract_id: str) -> PriceSnapshot:
        self.prices.append(contract_id)
        return PriceSnapshot(
            market_id="polymarket",
            contract_id=contract_id,
            last=0.61,
            bid=0.60,
            ask=0.64,
            midpoint=0.62,
            source="test-polymarket",
        )

    def get_orderbook(self, contract_id: str) -> OrderBookSnapshot:
        self.orderbooks.append(contract_id)
        return OrderBookSnapshot(
            market_id="polymarket",
            contract_id=contract_id,
            bids=[OrderBookLevel(price=0.40, size=20)],
            asks=[OrderBookLevel(price=0.45, size=25)],
        )


class FakeRegistry:
    def __init__(self, adapter: MarketAdapter) -> None:
        self.adapter = adapter

    def create(self, _market_id: str, _settings=None) -> MarketAdapter:
        self.adapter.config = dict(_settings or {})
        self.adapter.runtime = self.adapter._create_runtime()
        return self.adapter


class SecretFailRegistry:
    def create(self, _market_id: str, _settings=None) -> MarketAdapter:
        raise RuntimeError("adapter failed with super-secret-token")


class FakeBodyHandler:
    def __init__(self, body: bytes, content_length: str | None = None) -> None:
        self.headers = {"Content-Length": content_length if content_length is not None else str(len(body))}
        self.rfile = io.BytesIO(body)


class WebApiTests(unittest.TestCase):
    @staticmethod
    def _accounting_zip(equity_csv: str, positions_csv: str) -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("equity.csv", equity_csv)
            archive.writestr("positions.csv", positions_csv)
        return buffer.getvalue()

    def _serve_api(self, config_path: Path, frontend_dir: Path):
        server = ReactGuiServer(
            ("127.0.0.1", 0),
            ReactGuiHandler,
            config_path=config_path,
            frontend_dir=frontend_dir,
            adapter_registry=FakeRegistry(FakePaperAdapter()),
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server, thread, f"http://127.0.0.1:{server.server_address[1]}"

    def _request_json(
        self,
        base_url: str,
        path: str,
        *,
        method: str = "GET",
        payload: object | None = None,
        raw: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict]:
        data = raw
        request_headers = dict(headers or {})
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            request_headers.setdefault("Content-Type", "application/json")
        request = Request(f"{base_url}{path}", data=data, headers=request_headers, method=method)
        try:
            with urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def _request_raw(self, base_url: str, path: str) -> tuple[int, dict[str, str], bytes]:
        request = Request(f"{base_url}{path}", method="GET")
        try:
            with urlopen(request, timeout=5) as response:
                return response.status, dict(response.headers), response.read()
        except HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read()

    def test_markets_payload_merges_catalog_with_local_enablement(self) -> None:
        cfg = AppConfig()
        cfg.markets["kalshi"].enabled = True
        cfg.selected_market_id = "kalshi"

        payload = markets_payload(cfg)

        kalshi = next(market for market in payload["markets"] if market["market_id"] == "kalshi")
        self.assertTrue(kalshi["enabled"])
        self.assertTrue(kalshi["capabilities"]["paper_trading"])
        self.assertEqual(payload["selected_market_id"], "kalshi")
        self.assertGreaterEqual(payload["counts"]["total"], 1)
        self.assertGreaterEqual(payload["counts"]["implemented"], 1)

    def test_markets_payload_includes_diagnostics_without_secret_values(self) -> None:
        cfg = AppConfig()
        cfg.markets["kalshi"].enabled = True
        cfg.markets["kalshi"].settings.update(
            {
                "credential_env_vars": ["KALSHI_API_KEY_ID"],
                "kalshi_api_key_id": "super-secret-key",
                "kalshi_private_key_path": "C:/secret/private.pem",
                "nested": {"api_token": "nested-secret-token", "public": "ok"},
                "live_trading_enabled": True,
                "live_trading_confirmed": True,
                "live_trading_max_size": 9,
            }
        )

        payload = markets_payload(cfg)

        kalshi = next(market for market in payload["markets"] if market["market_id"] == "kalshi")
        self.assertEqual(kalshi["settings"]["kalshi_api_key_id"], "***")
        self.assertEqual(kalshi["settings"]["kalshi_private_key_path"], "***")
        self.assertEqual(kalshi["settings"]["nested"]["api_token"], "***")
        self.assertEqual(kalshi["settings"]["nested"]["public"], "ok")
        self.assertEqual(kalshi["credential_env_vars"], ["KALSHI_API_KEY_ID"])
        self.assertIn({"name": "KALSHI_API_KEY_ID", "source": "config:kalshi_api_key_id"}, kalshi["credential_sources"])
        self.assertIn(
            {"name": "KALSHI_PRIVATE_KEY_PATH", "source": "config:kalshi_private_key_path"},
            kalshi["credential_sources"],
        )
        self.assertTrue(kalshi["safety"]["live_trading_enabled"])
        self.assertTrue(kalshi["safety"]["live_trading_confirmed"])
        self.assertEqual(kalshi["safety"]["live_trading_max_size"], 9)
        self.assertIn("live armed", kalshi["status_text"])
        rendered = json.dumps(kalshi)
        self.assertNotIn("super-secret-key", rendered)
        self.assertNotIn("private.pem", rendered)
        self.assertNotIn("nested-secret-token", rendered)

    def test_market_health_failure_does_not_leak_raw_exception_text(self) -> None:
        cfg = AppConfig()
        cfg.markets["kalshi"].enabled = True

        payload = markets_payload(cfg, SecretFailRegistry())

        rendered = json.dumps(payload)
        kalshi = next(market for market in payload["markets"] if market["market_id"] == "kalshi")
        self.assertFalse(kalshi["health"]["ok"])
        self.assertEqual(kalshi["health"]["message"], "Adapter health check failed.")
        self.assertEqual(kalshi["health"]["error_type"], "RuntimeError")
        self.assertNotIn("super-secret-token", rendered)

    def test_paper_payload_exposes_history_and_aggregated_positions(self) -> None:
        cfg = AppConfig()
        cfg.paper_trades = [
            PaperTradeRecord(
                market_id="kalshi",
                contract_id="KALSHI-CONTRACT",
                side="BUY",
                size=2,
                limit_price=0.44,
                accepted=True,
                message="accepted",
            ),
            PaperTradeRecord(
                market_id="kalshi",
                contract_id="KALSHI-CONTRACT",
                side="SELL",
                size=0.5,
                limit_price=0.60,
                accepted=True,
                message="accepted",
            ),
            PaperTradeRecord(
                market_id="kalshi",
                contract_id="REJECTED",
                side="BUY",
                size=1,
                limit_price=0.20,
                accepted=False,
                message="rejected",
            ),
        ]

        payload = paper_payload(cfg)

        self.assertEqual(payload["counts"]["history"], 3)
        self.assertEqual(payload["counts"]["accepted"], 2)
        self.assertEqual(payload["counts"]["rejected"], 1)
        self.assertEqual(len(payload["positions"]), 1)
        position = payload["positions"][0]
        self.assertEqual(position["market_id"], "kalshi")
        self.assertEqual(position["contract_id"], "KALSHI-CONTRACT")
        self.assertAlmostEqual(position["net_size"], 1.5)
        self.assertAlmostEqual(position["notional"], 0.58)
        self.assertEqual(payload["summary"]["positions"], 1)

    def test_paper_quote_and_side_aware_limit_use_adapter_data(self) -> None:
        adapter = FakePaperAdapter()
        cfg = AppConfig()
        cfg.markets["kalshi"].enabled = True
        payload = {"market_id": "kalshi", "contract_id": "KALSHI-CONTRACT", "side": "BUY"}

        quote = paper_quote_payload(cfg, FakeRegistry(adapter), payload)
        limit = paper_quote_limit_payload(cfg, FakeRegistry(adapter), payload)

        self.assertEqual(adapter.prices, ["KALSHI-CONTRACT", "KALSHI-CONTRACT"])
        self.assertEqual(adapter.orderbooks, ["KALSHI-CONTRACT", "KALSHI-CONTRACT"])
        self.assertEqual(quote["price"]["last"], 0.62)
        self.assertEqual(quote["best_bid"], 0.58)
        self.assertEqual(quote["best_ask"], 0.66)
        self.assertEqual(limit["limit_price"], 0.66)
        self.assertEqual(limit["source"], "best_ask")

    def test_paper_order_impact_and_submit_record_history(self) -> None:
        adapter = FakePaperAdapter()
        cfg = AppConfig()
        cfg.markets["kalshi"].enabled = True
        cfg.paper_trades = [
            PaperTradeRecord(
                market_id="kalshi",
                contract_id="KALSHI-CONTRACT",
                side="BUY",
                size=2,
                limit_price=0.44,
                accepted=True,
                message="accepted",
            )
        ]
        order = paper_order_from_payload(
            {
                "market_id": "kalshi",
                "contract_id": "KALSHI-CONTRACT",
                "side": "SELL",
                "size": 0.5,
                "limit_price": 0.60,
            }
        )

        impact = paper_order_impact(cfg.paper_trades, order)
        result = submit_paper_order(cfg, FakeRegistry(adapter), order.__dict__)

        self.assertEqual(impact["effect"], "reduces position")
        self.assertEqual(impact["projected_net"], 1.5)
        self.assertEqual(len(adapter.orders), 1)
        self.assertEqual(len(cfg.paper_trades), 2)
        self.assertTrue(result["record"]["accepted"])
        self.assertEqual(result["record"]["average_price"], 0.60)

    def test_history_and_position_refill_payloads_return_order_form_values(self) -> None:
        cfg = AppConfig()
        record = PaperTradeRecord(
            market_id="kalshi",
            contract_id="KALSHI-CONTRACT",
            side="BUY",
            size=2,
            limit_price=0.44,
            accepted=True,
            message="accepted",
        )
        cfg.paper_trades = [record]

        history = history_refill_payload(cfg, record.id)
        position = position_refill_payload(cfg, "kalshi", "KALSHI-CONTRACT")

        self.assertEqual(history["side"], "BUY")
        self.assertEqual(history["limit_price"], 0.44)
        self.assertEqual(position["side"], "SELL")
        self.assertEqual(position["size"], 2)
        self.assertIsNone(position["limit_price"])

    def test_refresh_selected_paper_mark_and_payload_unrealized(self) -> None:
        adapter = FakePaperAdapter()
        cfg = AppConfig()
        cfg.markets["kalshi"].enabled = True
        cfg.paper_trades = [
            PaperTradeRecord(
                market_id="kalshi",
                contract_id="KALSHI-CONTRACT",
                side="BUY",
                size=2,
                limit_price=0.44,
                accepted=True,
                message="accepted",
            )
        ]

        marks = refresh_selected_paper_mark(cfg, FakeRegistry(adapter), "kalshi", "KALSHI-CONTRACT", {})
        payload = paper_payload(cfg, marks)

        self.assertEqual(adapter.prices, ["KALSHI-CONTRACT"])
        self.assertEqual(marks[("kalshi", "KALSHI-CONTRACT")]["mark_price"], 0.60)
        self.assertEqual(payload["positions"][0]["mark_source"], "bid")
        self.assertAlmostEqual(payload["positions"][0]["unrealized"], 0.32)
        self.assertEqual(payload["summary"]["marked"], 1)

    def test_alert_payload_create_update_and_delete(self) -> None:
        cfg = AppConfig()
        cfg.markets["kalshi"].enabled = True
        adapter = FakePaperAdapter()

        alert = alert_from_payload(
            cfg,
            FakeRegistry(adapter),
            {
                "market_id": "kalshi",
                "contract_id": "KALSHI-CONTRACT",
                "label": "Kalshi alert",
                "direction": "above",
                "threshold": 0.65,
                "source": "midpoint",
                "once": False,
            },
        )
        cfg.alerts.append(alert)

        payload = alerts_payload(cfg, FakeRegistry(adapter), {})
        self.assertEqual(payload["counts"]["total"], 1)
        self.assertEqual(payload["counts"]["enabled"], 1)
        self.assertEqual(payload["alerts"][0]["status"]["label"], "waiting for midpoint")
        self.assertEqual(payload["alerts"][0]["contract_id"], "KALSHI-CONTRACT")

        alert_from_payload(
            cfg,
            FakeRegistry(adapter),
            {
                "market_id": "kalshi",
                "contract_id": "KALSHI-CONTRACT",
                "threshold": 0.5,
                "source": "best_bid",
                "enabled": False,
            },
            existing=alert,
        )
        self.assertFalse(alert.enabled)
        self.assertEqual(alert.source, "best_bid")
        deleted = delete_alert(cfg, alert.id)
        self.assertEqual(deleted.id, alert.id)
        self.assertEqual(alerts_payload(cfg, FakeRegistry(adapter))["counts"]["total"], 0)

    def test_refresh_alert_price_updates_current_state_and_triggers_once_alert(self) -> None:
        cfg = AppConfig()
        cfg.markets["kalshi"].enabled = True
        adapter = FakePaperAdapter()
        alert = PriceAlert(
            market_id="kalshi",
            token_id="KALSHI-CONTRACT",
            label="Kalshi last trade",
            direction="above",
            threshold=0.60,
            source="last_trade",
            once=True,
        )
        cfg.alerts.append(alert)
        price_state = {}

        result = refresh_alert_price(cfg, FakeRegistry(adapter), alert, price_state)
        payload = alerts_payload(cfg, FakeRegistry(adapter), price_state)

        self.assertEqual(adapter.prices, ["KALSHI-CONTRACT"])
        self.assertEqual(result["values"]["last_trade"], 0.62)
        self.assertTrue(alert.triggered)
        self.assertFalse(alert.enabled)
        self.assertEqual(alert.last_value, 0.62)
        self.assertEqual(payload["alerts"][0]["current_value"], 0.62)
        self.assertEqual(payload["alerts"][0]["status"]["label"], "triggered/disabled")

    def test_refresh_polymarket_alert_uses_last_trade_price(self) -> None:
        cfg = AppConfig()
        cfg.markets["polymarket"].enabled = True
        adapter = FakePolymarketAdapter()
        alert = PriceAlert(
            market_id="polymarket",
            token_id="token-yes",
            label="Polymarket last trade",
            direction="above",
            threshold=0.60,
            source="last_trade",
            once=True,
        )
        cfg.alerts.append(alert)
        price_state = {}

        result = refresh_alert_price(cfg, FakeRegistry(adapter), alert, price_state)

        self.assertEqual(adapter.prices, ["token-yes"])
        self.assertEqual(result["values"]["last_trade"], 0.61)
        self.assertTrue(alert.triggered)
        self.assertFalse(alert.enabled)

    def test_wallet_payload_add_update_delete(self) -> None:
        cfg = AppConfig()
        cfg.markets["polymarket"].enabled = True

        wallet = add_wallet_watch(cfg, {"wallet": WALLET.upper().replace("X", "x", 1), "display_name": "tracked"})
        payload = wallets_payload(cfg)

        self.assertEqual(payload["counts"]["total"], 1)
        self.assertEqual(payload["wallets"][0]["wallet"], WALLET)
        self.assertEqual(payload["wallets"][0]["display_name"], "tracked")

        update_wallet_watch(cfg, wallet.id, {"wallet": WALLET, "display_name": "renamed", "enabled": False, "only_market_slug": "slug"})
        self.assertFalse(cfg.wallets[0].enabled)
        self.assertEqual(cfg.wallets[0].only_market_slug, "slug")
        deleted = delete_wallet_watch(cfg, wallet.id)

        self.assertEqual(deleted.wallet, WALLET)
        self.assertEqual(wallets_payload(cfg)["counts"]["total"], 0)

    def test_poll_wallet_activity_updates_seen_state_and_copy_simulation_preview(self) -> None:
        cfg = AppConfig()
        cfg.markets["polymarket"].enabled = True
        cfg.wallets = [WalletWatch(wallet=WALLET, display_name="tracked")]
        cfg.copytrading = CopyTradeSettings(
            enabled=True,
            live=False,
            follow_wallet=WALLET,
            follow_wallets=[WALLET],
            scale=1.0,
            max_usdc_per_trade=1.0,
            slippage=0.02,
        )
        activity = [
            {
                "transactionHash": "tx2",
                "timestamp": 101,
                "proxyWallet": WALLET,
                "asset": "token-yes",
                "side": "BUY",
                "price": "0.44",
                "size": "10",
                "slug": "market",
                "outcome": "Yes",
            },
            {
                "transactionHash": "tx1",
                "timestamp": 100,
                "proxyWallet": WALLET,
                "asset": "token-yes",
                "side": "BUY",
                "price": "0.43",
                "size": "3",
                "slug": "market",
                "outcome": "Yes",
            },
        ]
        recent: list[dict] = []

        with patch("web_api.data_api.get_activity", return_value=activity):
            result = poll_wallet_activity(cfg, FakeRegistry(FakePolymarketAdapter()), recent)

        self.assertEqual(result["problems"], [])
        self.assertEqual(len(result["activity"]), 2)
        self.assertEqual(cfg.wallets[0].last_seen_ts, 101)
        self.assertEqual(set(cfg.wallets[0].seen_activity_keys), {"tx:tx1", "tx:tx2"})
        newest = result["activity"][0]
        self.assertEqual(newest["transaction_hash"], "tx2")
        preview = newest["copy_preview"]
        self.assertEqual(preview["status"], "simulation")
        self.assertFalse(preview["live"])
        self.assertTrue(preview["pricing"]["capped_by_max_usdc"])
        self.assertAlmostEqual(preview["order"]["limit_price"], 0.47)

    def test_copy_settings_and_live_preview_use_shared_preflight_without_ordering(self) -> None:
        cfg = AppConfig()
        cfg.markets["polymarket"].enabled = True
        cfg.markets["polymarket"].settings.update(
            {
                "live_trading_enabled": True,
                "live_trading_confirmed": True,
                "live_trading_max_size": 10,
                "live_trading_max_notional": 5,
            }
        )
        settings = apply_copy_settings_patch(
            cfg,
            {
                "enabled": True,
                "live": True,
                "follow_wallet": WALLET,
                "follow_wallets": [WALLET],
                "copy_percentage": 100,
                "max_usdc_per_trade": 2,
                "slippage": 0.01,
                "allow_sells": True,
            },
        )

        payload = copy_preview_payload(
            cfg,
            FakeRegistry(FakePolymarketAdapter()),
            {"proxyWallet": WALLET, "asset": "token-yes", "side": "BUY", "size": 2, "price": 0.44},
        )
        copy_state = copy_payload(cfg, FakeRegistry(FakePolymarketAdapter()))

        self.assertTrue(settings.live)
        self.assertEqual(copy_state["status"], "live requested")
        self.assertEqual(payload["preview"]["status"], "live_preflight")
        self.assertFalse(payload["preview"]["blocked"])
        self.assertEqual(payload["preview"]["preflight"]["feature"], "live copy trading")
        self.assertEqual(payload["preview"]["preflight"]["metadata_keys"], ["activity_key", "source", "tif"])

    def test_copy_settings_accept_zero_to_one_hundred_percent(self) -> None:
        cfg = AppConfig()
        cfg.markets["polymarket"].enabled = True

        settings = apply_copy_settings_patch(
            cfg,
            {
                "enabled": True,
                "follow_wallets": [WALLET, WALLET_2],
                "copy_percentage": 0,
                "max_usdc_per_trade": 2,
                "slippage": 0.01,
            },
        )

        self.assertEqual(settings.scale, 0.0)
        self.assertEqual(settings.normalized_follow_wallets(), [WALLET, WALLET_2])
        self.assertEqual(settings.to_dict()["copy_percentage"], 0.0)
        with self.assertRaises(ValueError):
            apply_copy_settings_patch(cfg, {"copy_percentage": 101})

    def test_copy_preview_supports_multiple_follow_wallets_and_conflict_guard(self) -> None:
        cfg = AppConfig()
        cfg.markets["polymarket"].enabled = True
        cfg.copytrading = CopyTradeSettings(
            enabled=True,
            live=False,
            follow_wallet=WALLET,
            follow_wallets=[WALLET, WALLET_2],
            scale=1.0,
            max_usdc_per_trade=10.0,
            slippage=0.01,
            conflict_guard=True,
        )
        conflict_state: dict[str, dict] = {}
        first = {
            "transactionHash": "tx1",
            "timestamp": 100,
            "proxyWallet": WALLET,
            "asset": "token-yes",
            "side": "BUY",
            "price": "0.44",
            "size": "2",
            "slug": "market",
            "outcome": "Yes",
        }
        duplicate = {**first, "transactionHash": "tx2", "proxyWallet": WALLET_2, "timestamp": 101}

        accepted = copy_trade_preview_from_activity(cfg, FakeRegistry(FakePolymarketAdapter()), first, conflict_state)
        skipped = copy_trade_preview_from_activity(cfg, FakeRegistry(FakePolymarketAdapter()), duplicate, conflict_state)

        self.assertEqual(accepted["status"], "simulation")
        self.assertEqual(skipped["status"], "skipped")
        self.assertIn("duplicate", skipped["reason"])

    def test_polymarket_leaderboard_payload_computes_roi_and_scans_pages(self) -> None:
        first_page = [
            {"rank": index, "proxyWallet": f"0x{index:040x}", "pseudonym": f"user-{index}", "pnl": "1", "volume": "100"}
            for index in range(1, 51)
        ]
        first_page[0] = {"rank": 1, "proxyWallet": "0xaaa", "pseudonym": "alpha", "pnl": "10", "volume": "100"}
        pages = [
            first_page,
            [
                {"rank": 51, "proxyWallet": "0xccc", "pseudonym": "gamma", "pnl": "4", "volume": "20"},
            ],
        ]

        def fake_leaderboard(*_args, **kwargs):
            return pages[0] if kwargs["offset"] == 0 else pages[1]

        with patch("web_api.data_api.get_leaderboard", side_effect=fake_leaderboard) as mock_get:
            payload = polymarket_leaderboard_payload(
                {
                    "sort": ["roi_pct"],
                    "limit": ["2"],
                    "scan_limit": ["51"],
                    "min_volume_usd": ["20"],
                }
            )

        self.assertEqual(mock_get.call_count, 2)
        self.assertEqual(payload["counts"]["scanned"], 51)
        self.assertEqual(payload["rows"][0]["display_name"], "gamma")
        self.assertAlmostEqual(payload["rows"][0]["roi_pct"], 20.0)
        self.assertEqual(payload["rows"][1]["display_name"], "alpha")
        self.assertFalse(payload["mdd_available"])
        self.assertEqual(payload["source_sort"], "PNL")

    def test_polymarket_user_mdd_payload_computes_usd_and_percentage_drawdown(self) -> None:
        with patch(
            "web_api.data_api.get_closed_positions",
            return_value=[
                {"timestamp": 10, "realizedPnl": "100", "totalBought": "1000"},
                {"timestamp": 20, "realizedPnl": "-40", "totalBought": "500"},
            ],
        ), patch(
            "web_api.data_api.get_positions",
            return_value=[{"totalPnl": "-10", "currentValue": "20", "initialValue": "100"}],
        ), patch(
            "web_api.data_api.get_activity",
            return_value=[],
        ), patch(
            "web_api.data_api.get_trades",
            return_value=[],
        ):
            payload = polymarket_user_mdd_payload(WALLET, closed_limit=10)

        self.assertTrue(payload["mdd_available"])
        self.assertEqual(payload["mdd_method"], MDD_METHOD_V2)
        self.assertEqual(payload["closed_positions"], 2)
        self.assertEqual(payload["open_positions"], 1)
        self.assertAlmostEqual(payload["mdd_usd"], 50.0)
        self.assertAlmostEqual(payload["mdd_pct"], 50.0 / 1700.0 * 100.0)
        self.assertEqual(payload["peak_value"], 100.0)
        self.assertEqual(payload["trough_value"], 50.0)
        self.assertIn("assumptions", payload)
        self.assertEqual(payload["trade_capital"]["events"], 0)

    def test_polymarket_user_mdd_payload_uses_accounting_snapshot_equity_base_when_requested(self) -> None:
        snapshot = self._accounting_zip(
            "timestamp,equity,deposits,withdrawals\n10,1000,1000,0\n20,1200,0,0\n",
            "asset,currentValue,realizedPnl\nasset-1,20,60\n",
        )
        with patch(
            "web_api.data_api.get_closed_positions",
            return_value=[
                {"timestamp": 10, "realizedPnl": "100", "totalBought": "100"},
                {"timestamp": 20, "realizedPnl": "-40", "totalBought": "100"},
            ],
        ), patch(
            "web_api.data_api.get_positions",
            return_value=[{"totalPnl": "0", "currentValue": "20", "initialValue": "50"}],
        ), patch(
            "web_api.data_api.get_activity",
            return_value=[],
        ), patch(
            "web_api.data_api.get_trades",
            return_value=[],
        ), patch(
            "polymarket.accounting.data_api.download_accounting_snapshot",
            return_value=snapshot,
        ) as mock_snapshot:
            payload = polymarket_user_mdd_payload(WALLET, closed_limit=10, include_accounting_snapshot=True)

        mock_snapshot.assert_called_once()
        self.assertEqual(payload["equity_base_source"], "accounting_snapshot_max_equity")
        self.assertEqual(payload["equity_base_usd"], 1200.0)
        self.assertAlmostEqual(payload["mdd_usd"], 40.0)
        self.assertAlmostEqual(payload["mdd_pct"], 40.0 / 1300.0 * 100.0)
        self.assertEqual(payload["accounting_snapshot"]["status"], "ok")
        self.assertTrue(payload["accounting_snapshot"]["reconciliation"]["mdd_pct_uses_accounting_base"])

    def test_polymarket_user_mdd_payload_uses_trade_activity_for_public_capital_basis(self) -> None:
        activity_pages = [
            [
                {
                    "type": "TRADE",
                    "side": "BUY",
                    "timestamp": 5,
                    "size": "200",
                    "price": "0.50",
                    "transactionHash": "0xbuy",
                    "asset": "token-1",
                },
                {
                    "type": "TRADE",
                    "side": "SELL",
                    "timestamp": 15,
                    "usdcSize": "40",
                    "transactionHash": "0xsell",
                    "asset": "token-1",
                },
                {"type": "REWARD", "timestamp": 16, "usdcSize": "3"},
            ],
            [],
        ]

        def fake_activity(*_args, **kwargs):
            return activity_pages[0] if kwargs["offset"] == 0 else activity_pages[1]

        with patch(
            "web_api.data_api.get_closed_positions",
            return_value=[
                {"timestamp": 10, "realizedPnl": "50", "totalBought": "25"},
                {"timestamp": 20, "realizedPnl": "-20", "totalBought": "25"},
            ],
        ), patch(
            "web_api.data_api.get_positions",
            return_value=[],
        ), patch(
            "web_api.data_api.get_activity",
            side_effect=fake_activity,
        ), patch(
            "web_api.data_api.get_trades",
            return_value=[],
        ):
            payload = polymarket_user_mdd_payload(WALLET, closed_limit=10, activity_limit=1000, trade_limit=0)

        self.assertEqual(payload["activity_events"], 3)
        self.assertEqual(payload["trade_events"], 0)
        self.assertEqual(payload["trade_capital"]["events"], 2)
        self.assertAlmostEqual(payload["trade_capital"]["buy_notional_usd"], 100.0)
        self.assertAlmostEqual(payload["trade_capital"]["sell_notional_usd"], 40.0)
        self.assertAlmostEqual(payload["trade_capital"]["max_deployed_notional_usd"], 100.0)
        self.assertAlmostEqual(payload["public_capital_basis_usd"], 100.0)
        self.assertAlmostEqual(payload["mdd_usd"], 20.0)
        self.assertAlmostEqual(payload["mdd_pct"], 20.0 / 150.0 * 100.0)

    def test_polymarket_user_mdd_payload_mark_replay_uses_clob_price_history(self) -> None:
        trade = {
            "type": "TRADE",
            "side": "BUY",
            "timestamp": 10,
            "size": "100",
            "price": "0.50",
            "transactionHash": "0xbuy",
            "asset": "token-1",
        }
        history = {
            "history": {
                "token-1": [
                    {"t": 10, "p": 0.50},
                    {"t": 20, "p": 0.20},
                    {"t": 30, "p": 0.80},
                ]
            }
        }
        with patch("web_api.data_api.get_closed_positions", return_value=[]), patch(
            "web_api.data_api.get_positions",
            return_value=[],
        ), patch(
            "web_api.data_api.get_activity",
            return_value=[trade],
        ), patch(
            "web_api.data_api.get_trades",
            return_value=[],
        ), patch(
            "polymarket.mdd.clob_rest.get_batch_price_history",
            return_value=history,
        ) as mock_history:
            payload = polymarket_user_mdd_payload(
                WALLET,
                mode="mark_replay",
                activity_limit=10,
                trade_limit=0,
                mark_replay_token_limit=20,
                mark_replay_interval="1h",
                mark_replay_fidelity=60,
            )

        mock_history.assert_called_once()
        self.assertEqual(payload["mdd_method"], MDD_METHOD_MARK_REPLAY)
        self.assertEqual(payload["mark_replay"]["status"], "ok")
        self.assertEqual(payload["mark_replay"]["token_count"], 1)
        self.assertEqual(payload["mark_replay"]["batch_cap"], 20)
        self.assertAlmostEqual(payload["mdd_usd"], 30.0)
        self.assertAlmostEqual(payload["mdd_pct"], 30.0 / 50.0 * 100.0)
        self.assertEqual(payload["peak_value"], 0.0)
        self.assertEqual(payload["trough_value"], -30.0)
        self.assertEqual(payload["fallback_v2"]["mdd_method"], MDD_METHOD_V2)
        self.assertGreaterEqual(payload["points_total"], 3)

    def test_polymarket_user_mdd_payload_mark_replay_reports_unreconstructable_tokens(self) -> None:
        trade_rows = [
            {"side": "BUY", "timestamp": 10, "size": "10", "price": "0.40", "asset": f"token-{index}"}
            for index in range(22)
        ]
        history = {"history": {"token-0": [{"t": 10, "p": 0.40}]}}
        with patch("web_api.data_api.get_closed_positions", return_value=[]), patch(
            "web_api.data_api.get_positions",
            return_value=[],
        ), patch(
            "web_api.data_api.get_activity",
            return_value=trade_rows,
        ), patch(
            "web_api.data_api.get_trades",
            return_value=[],
        ), patch(
            "polymarket.mdd.clob_rest.get_batch_price_history",
            return_value=history,
        ):
            payload = polymarket_user_mdd_payload(WALLET, mode="mark_replay", activity_limit=100, mark_replay_token_limit=20)

        self.assertEqual(payload["mdd_method"], MDD_METHOD_MARK_REPLAY)
        self.assertEqual(payload["mark_replay"]["status"], "partial")
        self.assertEqual(payload["mark_replay"]["token_count"], 20)
        self.assertIn("token-20", payload["mark_replay"]["clipped_token_ids"])
        self.assertIn("token-1", payload["mark_replay"]["missing_history_tokens"])

    def test_polymarket_leaderboard_payload_computes_and_sorts_mdd_filter(self) -> None:
        leaderboard = [
            {"rank": 1, "proxyWallet": WALLET, "pseudonym": "alpha", "pnl": "10", "volume": "100"},
            {"rank": 2, "proxyWallet": WALLET_2, "pseudonym": "beta", "pnl": "20", "volume": "500"},
        ]

        def fake_mdd(wallet, **_kwargs):
            value = 6.0 if wallet == WALLET else 12.0
            return {
                "mdd_usd": value * 10,
                "mdd_pct": value,
                "mdd_available": True,
                "mdd_method": "test",
                "mdd_pct_basis": "test",
                "points": [{"value": 0}],
                "closed_positions": 2,
                "open_positions": 1,
                "equity_base_usd": 1000,
                "peak_value": 100,
                "trough_value": 100 - value,
                "peak_timestamp": 10,
                "trough_timestamp": 20,
            }

        with patch(
            "web_api.data_api.get_leaderboard",
            return_value=leaderboard,
        ), patch("web_api.polymarket_user_mdd_payload", side_effect=fake_mdd):
            payload = polymarket_leaderboard_payload(
                {
                    "sort": ["mdd_pct"],
                    "direction": ["DESC"],
                    "limit": ["2"],
                    "scan_limit": ["2"],
                    "mdd_scan_limit": ["2"],
                    "min_mdd_pct": ["5"],
                }
            )

        self.assertEqual(payload["counts"]["mdd_computed"], 2)
        self.assertEqual(payload["counts"]["returned"], 2)
        self.assertEqual(payload["rows"][0]["wallet"], WALLET_2)
        self.assertAlmostEqual(payload["rows"][0]["mdd_pct"], 12.0)
        self.assertTrue(payload["mdd_available"])

    def test_polymarket_leaderboard_payload_persists_mdd_audit_cache_when_requested(self) -> None:
        leaderboard = [{"rank": 1, "proxyWallet": WALLET, "pseudonym": "alpha", "pnl": "10", "volume": "100"}]

        def fake_mdd(wallet, **_kwargs):
            return {
                "wallet": wallet,
                "mdd_usd": 50.0,
                "mdd_pct": 5.0,
                "mdd_available": True,
                "mdd_method": "test",
                "mdd_pct_basis": "test",
                "points": [{"timestamp": 10, "value": 100.0}],
                "closed_positions": 2,
                "open_positions": 1,
                "equity_base_usd": 1000.0,
                "peak_value": 100.0,
                "trough_value": 50.0,
                "peak_timestamp": 10,
                "trough_timestamp": 20,
            }

        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            {"POLYMARKET_ANALYTICS_CACHE_PATH": str(Path(tmp) / "analytics-cache.json")},
        ), patch("web_api.data_api.get_leaderboard", return_value=leaderboard), patch(
            "web_api.polymarket_user_mdd_payload", side_effect=fake_mdd
        ):
            payload = polymarket_leaderboard_payload(
                {
                    "compute_mdd": ["true"],
                    "limit": ["1"],
                    "scan_limit": ["1"],
                    "mdd_scan_limit": ["1"],
                    "mdd_persist_cache": ["true"],
                }
            )
            cache_key = payload["rows"][0]["mdd_audit_cache_key"]
            export = polymarket_mdd_export_payload(cache_key)

        self.assertTrue(payload["analytics_cache"]["enabled"])
        self.assertTrue(payload["rows"][0]["mdd_audit_cache_stored"])
        self.assertEqual(export["payload"]["wallet"], WALLET)
        self.assertEqual(export["cache"]["key"], cache_key)

    def test_polymarket_leaderboard_payload_reports_rate_limit_without_more_mdd_calls(self) -> None:
        leaderboard = [
            {"rank": 1, "proxyWallet": WALLET, "pseudonym": "alpha", "pnl": "10", "volume": "100"},
            {"rank": 2, "proxyWallet": WALLET_2, "pseudonym": "beta", "pnl": "20", "volume": "500"},
        ]
        exc = PolymarketRateLimitError(
            "limited",
            service="data",
            method="GET",
            url="https://data-api.polymarket.com/test",
            status_code=429,
        )
        with patch("web_api.data_api.get_leaderboard", return_value=leaderboard), patch(
            "web_api.polymarket_user_mdd_payload",
            side_effect=exc,
        ) as mock_mdd:
            payload = polymarket_leaderboard_payload(
                {"compute_mdd": ["true"], "limit": ["2"], "scan_limit": ["2"], "mdd_scan_limit": ["2"]}
            )

        mock_mdd.assert_called_once()
        self.assertTrue(payload["rate_limit"]["limited"])
        self.assertEqual(payload["rate_limit"]["events"][0]["status_code"], 429)
        self.assertIn("rate-limited", payload["warnings"][0])

    def test_polymarket_mdd_csv_export_route_serves_cached_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "config.json"
            frontend_dir = root / "dist"
            frontend_dir.mkdir()
            save_config(AppConfig(), config_path)
            with patch.dict(os.environ, {"POLYMARKET_ANALYTICS_CACHE_PATH": str(root / "analytics-cache.json")}):
                metadata = store_analytics_artifact(
                    POLYMARKET_MDD_AUDIT_KIND,
                    {"wallet": WALLET, "mode": "fast"},
                    {
                        "wallet": WALLET,
                        "mdd_method": "test",
                        "mdd_available": True,
                        "mdd_usd": 10.0,
                        "mdd_pct": 1.0,
                        "equity_base_usd": 1000.0,
                        "peak_value": 10.0,
                        "trough_value": 0.0,
                        "mdd_pct_basis": "test",
                        "points": [{"timestamp": 1, "value": 10.0}],
                    },
                )
                server, thread, base_url = self._serve_api(config_path, frontend_dir)
                try:
                    status, headers, body = self._request_raw(
                        base_url,
                        f"/api/polymarket/users/mdd/export.csv?key={metadata['key']}",
                    )
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join(timeout=5)

        self.assertEqual(status, 200)
        self.assertIn("text/csv", headers["Content-Type"])
        self.assertIn(b"summary,", body)
        self.assertIn(WALLET.encode("utf-8"), body)

    def test_polymarket_direct_mdd_route_persists_cache_and_json_export(self) -> None:
        def fake_mdd(wallet, **_kwargs):
            return {
                "wallet": wallet,
                "mdd_method": "test",
                "mdd_available": True,
                "mdd_usd": 25.0,
                "mdd_pct": 2.5,
                "mdd_pct_basis": "test",
                "points": [{"timestamp": 1, "value": 25.0}],
                "closed_positions": 1,
                "open_positions": 0,
                "equity_base_usd": 1000.0,
                "peak_value": 25.0,
                "trough_value": 0.0,
                "peak_timestamp": 1,
                "trough_timestamp": 2,
            }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "config.json"
            frontend_dir = root / "dist"
            frontend_dir.mkdir()
            save_config(AppConfig(), config_path)
            with patch.dict(os.environ, {"POLYMARKET_ANALYTICS_CACHE_PATH": str(root / "analytics-cache.json")}), patch(
                "web_api.polymarket_user_mdd_payload",
                side_effect=fake_mdd,
            ):
                server, thread, base_url = self._serve_api(config_path, frontend_dir)
                try:
                    status, mdd = self._request_json(base_url, f"/api/polymarket/users/mdd?wallet={WALLET}&persist_cache=true")
                    cache_key = mdd["audit_cache"]["key"]
                    export_status, export = self._request_json(base_url, f"/api/polymarket/users/mdd/export.json?key={cache_key}")
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join(timeout=5)

        self.assertEqual(status, 200)
        self.assertTrue(mdd["audit_cache"]["stored"])
        self.assertEqual(export_status, 200)
        self.assertEqual(export["payload"]["wallet"], WALLET)
        self.assertEqual(export["cache"]["key"], cache_key)

    def test_polymarket_mdd_cache_routes_list_health_and_purge_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "config.json"
            frontend_dir = root / "dist"
            cache_path = root / "analytics-cache.json"
            frontend_dir.mkdir()
            save_config(AppConfig(), config_path)
            with patch.dict(os.environ, {"POLYMARKET_ANALYTICS_CACHE_PATH": str(cache_path)}):
                fresh = store_analytics_artifact(
                    POLYMARKET_MDD_AUDIT_KIND,
                    {"wallet": WALLET, "mode": "fast"},
                    {
                        "wallet": WALLET,
                        "mdd_method": "test",
                        "mdd_available": True,
                        "mdd_usd": 10.0,
                        "mdd_pct": 1.0,
                        "equity_base_usd": 1000.0,
                        "points": [{"timestamp": 1, "value": 10.0}],
                    },
                )
                expired = store_analytics_artifact(
                    POLYMARKET_MDD_AUDIT_KIND,
                    {"wallet": WALLET_2, "mode": "fast"},
                    {
                        "wallet": WALLET_2,
                        "mdd_method": "test",
                        "mdd_available": True,
                        "mdd_usd": 20.0,
                        "mdd_pct": 2.0,
                        "equity_base_usd": 1000.0,
                        "points": [{"timestamp": 1, "value": 20.0}],
                    },
                )
                cache = json.loads(cache_path.read_text(encoding="utf-8"))
                cache["entries"][expired["key"]]["expires_at"] = 1
                cache_path.write_text(json.dumps(cache), encoding="utf-8")

                server, thread, base_url = self._serve_api(config_path, frontend_dir)
                try:
                    list_status, listing = self._request_json(base_url, "/api/polymarket/users/mdd/cache")
                    health_status, health = self._request_json(base_url, "/api/polymarket/users/mdd/cache/health")
                    purge_status, purge = self._request_json(
                        base_url,
                        "/api/polymarket/users/mdd/cache/purge",
                        method="POST",
                        payload={"expired_only": True},
                    )
                    delete_status, deleted = self._request_json(
                        base_url,
                        f"/api/polymarket/users/mdd/cache/{fresh['key']}",
                        method="DELETE",
                    )
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join(timeout=5)

        self.assertEqual(list_status, 200)
        self.assertEqual(listing["counts"]["entries"], 2)
        self.assertEqual(listing["counts"]["expired_entries"], 1)
        self.assertEqual(health_status, 200)
        self.assertEqual(health["cache"]["entries"], 2)
        self.assertEqual(health["cache"]["expired_entries"], 1)
        self.assertEqual(purge_status, 200)
        self.assertIn(expired["key"], purge["deleted_keys"])
        self.assertEqual(purge["counts"]["entries"], 1)
        self.assertEqual(delete_status, 200)
        self.assertIn(fresh["key"], deleted["deleted_keys"])
        self.assertEqual(deleted["counts"]["entries"], 0)

    def test_polymarket_user_search_payload_returns_profile_rows(self) -> None:
        with patch(
            "web_api.gamma.search_profiles",
            return_value=[
                ProfileResult(
                    pseudonym="Trader",
                    proxy_wallet=WALLET,
                    profile_image="https://example.test/avatar.png",
                    display_username_public=True,
                )
            ],
        ) as mock_search:
            payload = polymarket_user_search_payload("trade", limit=3)

        mock_search.assert_called_once_with("trade", limit=3)
        self.assertEqual(payload["counts"]["profiles"], 1)
        self.assertEqual(payload["profiles"][0]["proxy_wallet"], WALLET)

    def test_apply_config_patch_validates_selected_market_and_theme(self) -> None:
        cfg = AppConfig()

        apply_config_patch(cfg, {"selected_market_id": "kalshi", "theme": "dark"})

        self.assertEqual(cfg.selected_market_id, "kalshi")
        self.assertEqual(cfg.theme, "dark")
        with self.assertRaises(ValueError):
            apply_config_patch(cfg, {"selected_market_id": "missing"})
        with self.assertRaises(ValueError):
            apply_config_patch(cfg, {"theme": "blue"})

    def test_apply_market_patch_updates_enabled_and_settings(self) -> None:
        cfg = AppConfig()

        apply_market_patch(cfg, "kalshi", {"enabled": True, "settings": {"max_size": 3}})

        self.assertTrue(cfg.markets["kalshi"].enabled)
        self.assertEqual(cfg.markets["kalshi"].settings["max_size"], 3)
        with self.assertRaises(ValueError):
            apply_market_patch(cfg, "missing", {"enabled": True})
        with self.assertRaises(ValueError):
            apply_market_patch(cfg, "kalshi", {"settings": "bad"})

    def test_apply_market_patch_persists_validated_live_safety_fields(self) -> None:
        cfg = AppConfig()

        apply_market_patch(
            cfg,
            "kalshi",
            {
                "enabled": True,
                "live_trading_enabled": True,
                "live_trading_confirmed": True,
                "live_trading_kill_switch": False,
                "live_trading_max_size": "9",
                "live_trading_max_notional": "25.5",
            },
        )

        settings = cfg.markets["kalshi"].settings
        self.assertTrue(cfg.markets["kalshi"].enabled)
        self.assertTrue(settings["live_trading_enabled"])
        self.assertTrue(settings["live_trading_confirmed"])
        self.assertFalse(settings["live_trading_kill_switch"])
        self.assertEqual(settings["live_trading_max_size"], 9.0)
        self.assertEqual(settings["live_trading_max_notional"], 25.5)

        apply_market_patch(cfg, "kalshi", {"live_trading_max_size": "", "live_trading_max_notional": None})

        self.assertNotIn("live_trading_max_size", cfg.markets["kalshi"].settings)
        self.assertNotIn("live_trading_max_notional", cfg.markets["kalshi"].settings)
        with self.assertRaises(ValueError):
            apply_market_patch(cfg, "kalshi", {"live_trading_max_size": "-1"})

    def test_live_safety_payload_reports_selected_gate_state(self) -> None:
        cfg = AppConfig()
        cfg.selected_market_id = "kalshi"
        apply_market_patch(
            cfg,
            "kalshi",
            {
                "enabled": True,
                "live_trading_enabled": True,
                "live_trading_confirmed": True,
                "live_trading_kill_switch": False,
                "live_trading_max_size": "5",
                "live_trading_max_notional": "10",
            },
        )

        payload = live_safety_payload(cfg, FakeRegistry(FakePaperAdapter()))

        self.assertEqual(payload["selected_market_id"], "kalshi")
        self.assertEqual(payload["status"], "armed")
        self.assertEqual(payload["tone"], "good")
        self.assertTrue(payload["can_preflight"])
        self.assertEqual(payload["controls"]["live_trading_max_size"], 5.0)
        self.assertEqual(payload["controls"]["live_trading_max_notional"], 10.0)
        self.assertEqual(payload["blockers"], [])

    def test_live_preflight_payload_returns_redacted_audit_without_ordering(self) -> None:
        adapter = FakePaperAdapter()
        cfg = AppConfig()
        cfg.selected_market_id = "kalshi"
        apply_market_patch(
            cfg,
            "kalshi",
            {
                "enabled": True,
                "live_trading_enabled": True,
                "live_trading_confirmed": True,
                "live_trading_max_size": "5",
                "live_trading_max_notional": "10",
            },
        )

        payload = live_preflight_payload(
            cfg,
            FakeRegistry(adapter),
            {
                "market_id": "kalshi",
                "contract_id": "KALSHI-CONTRACT",
                "side": "BUY",
                "size": "2",
                "limit_price": "0.5",
                "metadata": {"client_order_id": "order-1", "private_key": "super-secret"},
            },
        )

        self.assertTrue(payload["ok"])
        self.assertFalse(payload["blocked"])
        self.assertEqual(adapter.orders, [])
        self.assertEqual(payload["preflight"]["feature"], "live preflight preview")
        self.assertEqual(payload["preflight"]["metadata_keys"], ["client_order_id", "private_key"])
        self.assertIn("Preflight OK", payload["message"])
        self.assertNotIn("super-secret", json.dumps(payload))

    def test_live_preflight_payload_returns_blocked_gate_audit(self) -> None:
        cfg = AppConfig()
        cfg.selected_market_id = "kalshi"
        apply_market_patch(
            cfg,
            "kalshi",
            {
                "enabled": True,
                "live_trading_enabled": True,
                "live_trading_confirmed": True,
                "live_trading_max_size": "1",
            },
        )

        payload = live_preflight_payload(
            cfg,
            FakeRegistry(FakePaperAdapter()),
            {
                "market_id": "kalshi",
                "contract_id": "KALSHI-CONTRACT",
                "side": "BUY",
                "size": "2",
                "limit_price": "0.5",
            },
        )

        self.assertFalse(payload["ok"])
        self.assertTrue(payload["blocked"])
        self.assertIn("exceeds configured max", payload["message"])
        self.assertEqual(payload["live_safety"]["status"], "armed")

    def test_api_payloads_roundtrip_with_file_storage(self) -> None:
        cfg = AppConfig()
        cfg.selected_market_id = "kalshi"
        cfg.paper_trades = [
            PaperTradeRecord(
                market_id="kalshi",
                contract_id="KALSHI-CONTRACT",
                side="BUY",
                size=2,
                limit_price=0.44,
                accepted=True,
                message="accepted",
            )
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "config.json"
            save_config(cfg, path)
            loaded = load_config(path)

        self.assertEqual(markets_payload(loaded)["selected_market_id"], "kalshi")
        self.assertEqual(len(paper_position_rows(loaded.paper_trades)), 1)

    def test_health_payload_documents_parallel_gui_contract(self) -> None:
        payload = health_payload(Path("local-config.json"), Path("frontend-dist"))

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["mode"], "parallel")
        self.assertTrue(payload["python_gui_available"])
        self.assertEqual(payload["python_gui_command"], "python app.py")
        self.assertEqual(payload["python_gui_script"], "run_gui.bat")
        self.assertEqual(payload["tkinter_fallback"], "run_gui.bat or python app.py")
        self.assertEqual(payload["react_dev_command"], "run_web_gui_dev.bat")
        self.assertIn("npm run dev", payload["react_dev_manual_command"])
        self.assertIn("npm run build", payload["react_build_command"])
        self.assertEqual(payload["react_prod_command"], "run_web_gui_prod.bat")
        self.assertFalse(payload["frontend_build_available"])
        self.assertIn("/api/state", payload["routes"]["GET"])
        self.assertIn("/api/live-safety", payload["routes"]["GET"])
        self.assertIn("/api/polymarket/coverage", payload["routes"]["GET"])
        self.assertIn("/api/polymarket/clob-readiness", payload["routes"]["GET"])
        self.assertIn("/api/polymarket/live-validation", payload["routes"]["GET"])
        self.assertIn("/api/polymarket/live-validation/reports", payload["routes"]["GET"])
        self.assertIn("/api/polymarket/users/mdd/cache", payload["routes"]["GET"])
        self.assertIn("/api/polymarket/users/mdd/cache/health", payload["routes"]["GET"])
        self.assertIn("/api/polymarket/users/mdd/export.json", payload["routes"]["GET"])
        self.assertIn("/api/polymarket/users/mdd/export.csv", payload["routes"]["GET"])
        self.assertIn("/api/config", payload["routes"]["PATCH"])
        self.assertIn("/api/live-safety/preflight", payload["routes"]["POST"])
        self.assertIn("/api/polymarket/users/mdd/cache/purge", payload["routes"]["POST"])
        self.assertIn("/api/polymarket/live-validation/reports", payload["routes"]["POST"])
        self.assertIn("/api/polymarket/users/mdd/cache/{key}", payload["routes"]["DELETE"])
        self.assertIn("/api/polymarket/live-validation/reports/{key}", payload["routes"]["DELETE"])

    def test_polymarket_clob_readiness_payload_redacts_credentials(self) -> None:
        cfg = AppConfig()
        cfg.selected_market_id = "polymarket"
        cfg.markets["polymarket"].settings.update(
            {
                "private_key": "0x" + "1" * 64,
                "signature_type": 3,
                "funder_address": "0x" + "2" * 40,
                "live_trading_enabled": True,
                "live_trading_confirmed": True,
            }
        )

        payload = polymarket_clob_readiness_payload(cfg)

        self.assertTrue(payload["selected"])
        self.assertTrue(payload["readiness"]["ok"])
        self.assertEqual(payload["readiness"]["private_key"]["redacted"], "***")
        self.assertEqual(payload["readiness"]["signature_type"]["name"], "POLY_1271")
        self.assertTrue(payload["live_safety"]["live_trading_enabled"])
        self.assertNotIn("1" * 64, str(payload))

    def test_polymarket_live_validation_payload_reports_stage_gates_without_live_actions(self) -> None:
        cfg = AppConfig()
        cfg.selected_market_id = "polymarket"
        cfg.markets["polymarket"].enabled = True
        cfg.markets["polymarket"].settings.update(
            {
                "private_key": "0x" + "1" * 64,
                "signature_type": 3,
                "funder_address": "0x" + "2" * 40,
            }
        )
        env = {
            "POLY_ADDRESS": "0xabc",
            "POLY_API_KEY": "key",
            "POLY_PASSPHRASE": "pass",
            "POLY_SIGNATURE": "sig",
            "POLY_TIMESTAMP": "1",
            "POLY_API_SECRET": "ws-secret",
        }

        with patch.dict(os.environ, env, clear=True):
            payload = polymarket_live_validation_payload(cfg)

        self.assertEqual(payload["mode"], "local_readiness_only")
        self.assertFalse(payload["funded_execution_exposed"])
        self.assertFalse(payload["stage_gates"]["credentialed_read_ok"])
        self.assertFalse(payload["stage_gates"]["safe_to_attempt_funded_order"])
        self.assertEqual(payload["authenticated_read_checks"]["clob_l2_orders"]["status"], "skipped")
        self.assertEqual(payload["authenticated_read_checks"]["user_websocket_auth_payload"]["status"], "skipped")
        self.assertIn("authenticated read", payload["stage_gates"]["next_step"])
        self.assertNotIn("1" * 64, str(payload))
        self.assertNotIn("ws-secret", str(payload))

    def test_polymarket_live_validation_reports_store_import_compare_and_redact(self) -> None:
        cfg = AppConfig()
        cfg.selected_market_id = "polymarket"
        cfg.markets["polymarket"].enabled = True
        cfg.markets["polymarket"].settings.update({"private_key": "0x" + "1" * 64})

        with tempfile.TemporaryDirectory() as tmpdir:
            report_path = Path(tmpdir) / "reports.json"
            with patch.dict(os.environ, {"POLYMARKET_LIVE_VALIDATION_REPORTS_PATH": str(report_path)}, clear=False):
                empty = polymarket_live_validation_reports_payload()
                self.assertEqual(empty["counts"]["entries"], 0)

                gui_payload = polymarket_live_validation_report_store_payload(cfg, {})
                gui_key = gui_payload["stored"]["key"]
                self.assertEqual(gui_payload["counts"]["entries"], 1)

                cli_report = {
                    "generated_at": 123.0,
                    "market_id": "polymarket",
                    "mode": "strict_cli",
                    "selected": True,
                    "enabled": True,
                    "api_key": "very-secret-api-key",
                    "private_key": "0x" + "9" * 64,
                    "clob_auth_readiness": {"direct_l2_read_ready": True, "sdk_trading_ready": True},
                    "stage_gates": {
                        "public_live_checks": "passed",
                        "credential_readiness": "passed",
                        "credentialed_read_checks": "passed",
                        "bridge_address_checks": "blocked",
                        "funded_live_order_check": "blocked",
                        "credentialed_read_ok": True,
                        "safe_to_attempt_funded_order": False,
                        "requires_explicit_live_approval": True,
                        "next_step": "funded order/cancel remains CLI-only",
                    },
                    "funded_execution_exposed": False,
                }

                imported = polymarket_live_validation_report_store_payload(
                    cfg,
                    {"report_json": json.dumps(cli_report), "label": "strict CLI read"},
                )

                self.assertEqual(imported["counts"]["entries"], 2)
                self.assertEqual(imported["stored"]["summary"]["credential_readiness"], "passed")
                self.assertTrue(imported["stored"]["summary"]["credentialed_read_ok"])
                self.assertIsNotNone(imported["comparison"])
                self.assertTrue(report_path.exists())
                disk = report_path.read_text(encoding="utf-8")
                self.assertNotIn("very-secret-api-key", disk)
                self.assertNotIn("9" * 64, disk)
                self.assertIn("***", disk)

                deleted = polymarket_live_validation_report_purge_payload({"key": gui_key})
                self.assertEqual(deleted["deleted"], 1)
                self.assertNotIn(gui_key, [entry["key"] for entry in deleted["entries"]])

    def test_api_error_payload_uses_structured_shape_and_redacts_detail_keys(self) -> None:
        payload = api_error_payload(
            400,
            "validation_error",
            "Invalid payload.",
            {"api_key": "super-secret-key", "field": "theme"},
        )

        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "validation_error")
        self.assertEqual(payload["error"]["status"], 400)
        self.assertEqual(payload["error"]["message"], "Invalid payload.")
        self.assertEqual(payload["error"]["details"]["api_key"], "***")
        self.assertNotIn("super-secret-key", json.dumps(payload))

    def test_json_body_reader_rejects_bad_shape_size_and_encoding(self) -> None:
        with self.assertRaisesRegex(ValueError, "JSON request body must be an object"):
            _read_json_body(FakeBodyHandler(b"[]"))
        with self.assertRaisesRegex(ValueError, "JSON request body is too large"):
            _read_json_body(FakeBodyHandler(b"{}", "1000001"))
        with self.assertRaisesRegex(ValueError, "Content-Length must be an integer"):
            _read_json_body(FakeBodyHandler(b"{}", "bad"))
        with self.assertRaisesRegex(ValueError, "JSON request body must be UTF-8"):
            _read_json_body(FakeBodyHandler(b"\xff"))

    def test_http_mutation_errors_use_structured_response_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.json"
            frontend_dir = Path(tmpdir) / "dist"
            server, thread, base_url = self._serve_api(config_path, frontend_dir)
            try:
                status, invalid_json = self._request_json(
                    base_url,
                    "/api/config",
                    method="PATCH",
                    raw=b"{not-json",
                    headers={"Content-Type": "application/json"},
                )
                status_validation, validation = self._request_json(
                    base_url,
                    "/api/config",
                    method="PATCH",
                    payload={"theme": "blue"},
                )
                status_not_found, not_found = self._request_json(base_url, "/api/missing")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

        self.assertEqual(status, 400)
        self.assertEqual(invalid_json["error"]["code"], "invalid_json")
        self.assertFalse(invalid_json["ok"])
        self.assertEqual(status_validation, 400)
        self.assertEqual(validation["error"]["code"], "validation_error")
        self.assertEqual(validation["error"]["message"], "theme must be light or dark.")
        self.assertEqual(status_not_found, 404)
        self.assertEqual(not_found["error"]["code"], "not_found")

    def test_http_static_route_reports_missing_react_build_with_fallbacks(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.json"
            frontend_dir = Path(tmpdir) / "dist"
            server, thread, base_url = self._serve_api(config_path, frontend_dir)
            try:
                status, payload = self._request_json(base_url, "/")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

        self.assertEqual(status, 404)
        self.assertEqual(payload["error"]["code"], "react_build_missing")
        self.assertIn("npm run build", payload["error"]["details"]["build_command"])
        self.assertEqual(payload["error"]["details"]["dev_command"], "run_web_gui_dev.bat")
        self.assertIn("run_gui.bat", payload["error"]["details"]["tkinter_fallback"])

    def test_http_static_route_serves_built_react_assets_and_spa_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.json"
            frontend_dir = Path(tmpdir) / "dist"
            asset_dir = frontend_dir / "assets"
            asset_dir.mkdir(parents=True)
            (frontend_dir / "index.html").write_text("<html><body>React app</body></html>", encoding="utf-8")
            (asset_dir / "app.js").write_text("console.log('ok');", encoding="utf-8")

            server, thread, base_url = self._serve_api(config_path, frontend_dir)
            try:
                root_status, root_headers, root_body = self._request_raw(base_url, "/")
                asset_status, asset_headers, asset_body = self._request_raw(base_url, "/assets/app.js")
                fallback_status, _fallback_headers, fallback_body = self._request_raw(base_url, "/settings/live-safety")
                traversal_status, _traversal_headers, traversal_body = self._request_raw(base_url, "/%2e%2e/README.md")
                health_status, health = self._request_json(base_url, "/api/health")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

        self.assertEqual(root_status, 200)
        self.assertIn("text/html", root_headers["Content-Type"])
        self.assertIn(b"React app", root_body)
        self.assertEqual(asset_status, 200)
        self.assertIn("javascript", asset_headers["Content-Type"])
        self.assertEqual(asset_body, b"console.log('ok');")
        self.assertEqual(fallback_status, 200)
        self.assertIn(b"React app", fallback_body)
        self.assertEqual(traversal_status, 200)
        self.assertIn(b"React app", traversal_body)
        self.assertEqual(health_status, 200)
        self.assertTrue(health["frontend_build_available"])

    def test_app_state_payload_combines_initial_react_gui_state(self) -> None:
        cfg = AppConfig()
        cfg.selected_market_id = "kalshi"
        cfg.markets["kalshi"].enabled = True
        cfg.paper_trades = [
            PaperTradeRecord(
                market_id="kalshi",
                contract_id="KALSHI-CONTRACT",
                side="BUY",
                size=2,
                limit_price=0.44,
                accepted=True,
                message="accepted",
            )
        ]

        payload = app_state_payload(cfg, Path("local-config.json"), Path("frontend-dist"))

        self.assertEqual(payload["health"]["mode"], "parallel")
        self.assertEqual(payload["config"]["selected_market_id"], "kalshi")
        self.assertEqual(payload["markets"]["selected_market_id"], "kalshi")
        self.assertEqual(payload["live_safety"]["selected_market_id"], "kalshi")
        self.assertEqual(payload["paper"]["summary"]["positions"], 1)

    def test_http_state_route_reads_config_file(self) -> None:
        cfg = AppConfig()
        cfg.selected_market_id = "kalshi"
        cfg.paper_trades = [
            PaperTradeRecord(
                market_id="kalshi",
                contract_id="KALSHI-CONTRACT",
                side="BUY",
                size=2,
                limit_price=0.44,
                accepted=True,
                message="accepted",
            )
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.json"
            save_config(cfg, config_path)
            payload = app_state_payload(
                load_config(config_path),
                config_path,
                Path(tmpdir) / "dist",
            )

        self.assertEqual(payload["health"]["status"], "ok")
        self.assertEqual(payload["config"]["selected_market_id"], "kalshi")
        self.assertEqual(payload["paper"]["counts"]["history"], 1)


if __name__ == "__main__":
    unittest.main()
