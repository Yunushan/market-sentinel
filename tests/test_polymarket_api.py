from __future__ import annotations

import unittest
import io
import json
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import patch

from polymarket import bridge, clob_auth, clob_rest, data_api, gamma, relayer
from polymarket.analytics_cache import (
    POLYMARKET_MDD_AUDIT_KIND,
    load_analytics_artifact,
    mdd_payload_to_csv,
    store_analytics_artifact,
)
from polymarket.auth_readiness import build_clob_auth_readiness, validate_sdk_trading_readiness
from polymarket.coverage import polymarket_official_api_coverage
from polymarket.accounting import parse_accounting_snapshot_zip, reconcile_mdd_payload_with_accounting
from polymarket.endpoints import ALL_POLYMARKET_ENDPOINTS, CLOB_ENDPOINTS
from polymarket.http_client import PolymarketRateLimitError, PolymarketValidationError
from polymarket.live_verification import (
    CONFIRM_LIVE_ORDER_CANCEL,
    LiveOrderCancelRequest,
    build_live_validation_stage_gates,
    build_live_order_cancel_plan,
    run_live_order_cancel_verification,
)
from polymarket.ws_market import build_market_subscription
from polymarket.ws_sports import sports_ws_url
from polymarket.ws_user import build_user_subscription, probe_user_websocket, user_ws_url


HTTP_REQUEST = "polymarket.http_client.requests.request"


class FakeResponse:
    def __init__(self, payload, status_code: int = 200, *, headers=None, content: bytes = b"", text: str = "") -> None:
        self._payload = payload
        self.status_code = status_code
        self.headers = headers or {}
        self.content = content
        self.text = text

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def request_url(mock_request) -> str:
    return mock_request.call_args.args[1]


L2_HEADERS = {
    "POLY_ADDRESS": "0xabc",
    "POLY_API_KEY": "key",
    "POLY_PASSPHRASE": "pass",
    "POLY_SIGNATURE": "sig",
    "POLY_TIMESTAMP": "1",
}


class PolymarketApiWrapperTests(unittest.TestCase):
    @staticmethod
    def _accounting_zip(equity_csv: str, positions_csv: str) -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("equity.csv", equity_csv)
            archive.writestr("positions.csv", positions_csv)
        return buffer.getvalue()

    def test_analytics_cache_stores_prunes_and_loads_mdd_audit_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "analytics-cache.json"
            first = store_analytics_artifact(
                POLYMARKET_MDD_AUDIT_KIND,
                {"wallet": "0x1", "mode": "fast"},
                {"wallet": "0x1", "mdd_usd": 1.0, "points": []},
                path=cache_path,
                max_entries=1,
            )
            second = store_analytics_artifact(
                POLYMARKET_MDD_AUDIT_KIND,
                {"wallet": "0x2", "mode": "fast"},
                {"wallet": "0x2", "mdd_usd": 2.0, "points": []},
                path=cache_path,
                max_entries=1,
            )

            self.assertTrue(cache_path.exists())
            self.assertIsNone(load_analytics_artifact(first["key"], kind=POLYMARKET_MDD_AUDIT_KIND, path=cache_path))
            loaded = load_analytics_artifact(second["key"], kind=POLYMARKET_MDD_AUDIT_KIND, path=cache_path)

        self.assertIsNotNone(loaded)
        payload, metadata = loaded
        self.assertEqual(payload["wallet"], "0x2")
        self.assertTrue(metadata["hit"])

    def test_mdd_payload_to_csv_exports_summary_and_points(self) -> None:
        csv_text = mdd_payload_to_csv(
            {
                "wallet": "0xabc",
                "mdd_method": "test",
                "mdd_available": True,
                "mdd_usd": 12.5,
                "mdd_pct": 4.2,
                "equity_base_usd": 100.0,
                "peak_value": 20.0,
                "trough_value": 7.5,
                "mdd_pct_basis": "test_basis",
                "points": [{"timestamp": 10, "value": 20.0, "source": "closed_position"}],
            }
        )

        self.assertIn("section,wallet,mdd_method", csv_text)
        self.assertIn("summary,0xabc,test", csv_text)
        self.assertIn("point,0xabc,test,10,20.0", csv_text)

    def test_parse_market_outcomes_handles_json_encoded_fields(self) -> None:
        outcomes = gamma.parse_market_outcomes(
            {
                "clobTokenIds": '["token-yes", "token-no"]',
                "outcomes": '["Yes", "No"]',
                "outcomePrices": '["0.61", "0.39"]',
            }
        )

        self.assertEqual([o.outcome for o in outcomes], ["Yes", "No"])
        self.assertEqual([o.token_id for o in outcomes], ["token-yes", "token-no"])
        self.assertEqual([o.price for o in outcomes], [0.61, 0.39])

    def test_best_bid_ask_accepts_books_and_legacy_buy_sell_names(self) -> None:
        self.assertEqual(
            clob_rest.best_bid_ask_from_book(
                {"bids": [{"price": "0.49"}], "asks": [{"price": "0.51"}]}
            ),
            (0.49, 0.51),
        )
        self.assertEqual(
            clob_rest.best_bid_ask_from_book(
                {"buys": [{"price": "0.48"}], "sells": [{"price": "0.52"}]}
            ),
            (0.48, 0.52),
        )

    def test_get_midpoint_accepts_dict_payload(self) -> None:
        with patch(HTTP_REQUEST, return_value=FakeResponse({"midpoint": "0.42"})) as mock_get:
            midpoint = clob_rest.get_midpoint("token-1", timeout=3)

        self.assertEqual(midpoint, 0.42)
        self.assertEqual(mock_get.call_args.kwargs["params"], {"token_id": "token-1"})
        self.assertEqual(mock_get.call_args.kwargs["timeout"], 3)

    def test_get_last_trade_price_accepts_dict_payload(self) -> None:
        with patch(HTTP_REQUEST, return_value=FakeResponse({"price": "0.58"})) as mock_get:
            price = clob_rest.get_last_trade_price("token-1", timeout=3)

        self.assertEqual(price, 0.58)
        self.assertIn("/last-trade-price", request_url(mock_get))
        self.assertEqual(mock_get.call_args.kwargs["params"], {"token_id": "token-1"})
        self.assertEqual(mock_get.call_args.kwargs["timeout"], 3)

    def test_activity_request_clamps_limit_and_offset_and_passes_filters(self) -> None:
        with patch(HTTP_REQUEST, return_value=FakeResponse([{"id": 1}])) as mock_get:
            result = data_api.get_activity(
                "0xabc",
                limit=999,
                offset=-5,
                types=["TRADE"],
                side="BUY",
                market=["condition-1"],
                start=10,
                end=20,
                sort_direction="ASC",
                timeout=4,
            )

        params = mock_get.call_args.kwargs["params"]
        self.assertEqual(result, [{"id": 1}])
        self.assertEqual(params["limit"], 500)
        self.assertEqual(params["offset"], 0)
        self.assertEqual(params["type"], ["TRADE"])
        self.assertEqual(params["side"], "BUY")
        self.assertEqual(params["market"], ["condition-1"])
        self.assertEqual(params["start"], 10)
        self.assertEqual(params["end"], 20)
        self.assertEqual(params["sortBy"], "TIMESTAMP")
        self.assertEqual(params["sortDirection"], "ASC")
        self.assertEqual(mock_get.call_args.kwargs["timeout"], 4)

    def test_leaderboard_request_clamps_page_and_accepts_wrapped_payload(self) -> None:
        payload = {"data": [{"proxyWallet": "0xabc", "pnl": "12", "volume": "120"}]}
        with patch(HTTP_REQUEST, return_value=FakeResponse(payload)) as mock_get:
            result = data_api.get_leaderboard(
                limit=100,
                offset=-2,
                sort_by="ROI",
                sort_direction="SIDEWAYS",
                period="all",
                timeout=5,
            )

        params = mock_get.call_args.kwargs["params"]
        self.assertEqual(result, payload["data"])
        self.assertIn("/v1/leaderboard", request_url(mock_get))
        self.assertEqual(params["limit"], 50)
        self.assertEqual(params["offset"], 0)
        self.assertEqual(params["orderBy"], "PNL")
        self.assertEqual(params["timePeriod"], "ALL")
        self.assertEqual(params["category"], "OVERALL")
        self.assertEqual(mock_get.call_args.kwargs["timeout"], 5)

    def test_closed_positions_request_uses_public_profile_endpoint(self) -> None:
        payload = [{"asset": "token-yes", "realizedPnl": "-12", "timestamp": 10}]
        with patch(HTTP_REQUEST, return_value=FakeResponse(payload)) as mock_get:
            result = data_api.get_closed_positions(
                "0xabc",
                limit=99,
                offset=-1,
                sort_by="bad",
                sort_direction="bad",
                timeout=6,
            )

        params = mock_get.call_args.kwargs["params"]
        self.assertEqual(result, payload)
        self.assertIn("/closed-positions", request_url(mock_get))
        self.assertEqual(params["limit"], 50)
        self.assertEqual(params["offset"], 0)
        self.assertEqual(params["sortBy"], "TIMESTAMP")
        self.assertEqual(params["sortDirection"], "ASC")
        self.assertEqual(mock_get.call_args.kwargs["timeout"], 6)

    def test_accounting_snapshot_zip_parser_extracts_equity_positions_and_cashflow(self) -> None:
        raw = self._accounting_zip(
            "timestamp,equity,deposits,withdrawals\n"
            "10,1000,1000,0\n"
            "20,1200,0,0\n"
            "30,900,0,100\n",
            "asset,currentValue,realizedPnl,cashPnl,initialValue\n"
            "token-1,250,12,3,200\n"
            "token-2,100,-5,-1,90\n",
        )

        snapshot = parse_accounting_snapshot_zip(raw)

        self.assertEqual(snapshot["status"], "ok")
        self.assertEqual(snapshot["equity"]["base_equity_usd"], 1200.0)
        self.assertEqual(snapshot["equity"]["cash_flows"]["net_cash_flow_usd"], 900.0)
        self.assertEqual(snapshot["equity"]["cash_flows"]["cash_flow_gap_usd"], -1000.0)
        self.assertAlmostEqual(snapshot["positions"]["current_value_usd"], 350.0)
        self.assertAlmostEqual(snapshot["positions"]["realized_pnl_usd"], 7.0)

    def test_accounting_snapshot_reconciliation_overrides_mdd_percentage_base(self) -> None:
        snapshot = parse_accounting_snapshot_zip(
            self._accounting_zip(
                "timestamp,equity\n10,1000\n20,1200\n",
                "asset,currentValue,realizedPnl\nasset-1,40,50\n",
            )
        )
        payload = {
            "mdd_usd": 50.0,
            "mdd_pct": 25.0,
            "peak_value": 100.0,
            "equity_base_usd": 100.0,
            "open_current_value": 40.0,
            "cumulative_realized_pnl": 50.0,
        }

        reconciled = reconcile_mdd_payload_with_accounting(payload, snapshot)

        self.assertEqual(reconciled["equity_base_source"], "accounting_snapshot_max_equity")
        self.assertEqual(reconciled["equity_base_usd"], 1200.0)
        self.assertAlmostEqual(reconciled["mdd_pct"], 50.0 / 1300.0 * 100.0)
        self.assertEqual(reconciled["accounting_snapshot"]["reconciliation"]["status"], "reconciled")
        self.assertTrue(reconciled["accounting_snapshot"]["reconciliation"]["mdd_pct_uses_accounting_base"])

    def test_clob_public_wrappers_cover_batch_and_history_endpoints(self) -> None:
        with patch(HTTP_REQUEST, return_value=FakeResponse([{"asset_id": "token-1"}])) as mock_post:
            books = clob_rest.get_books(["token-1"], timeout=2)
        self.assertEqual(books, [{"asset_id": "token-1"}])
        self.assertEqual(mock_post.call_args.args[0], "POST")
        self.assertIn("/books", request_url(mock_post))
        self.assertEqual(mock_post.call_args.kwargs["json"], [{"token_id": "token-1"}])

        with patch(HTTP_REQUEST, return_value=FakeResponse({"history": []})) as mock_get:
            history = clob_rest.get_price_history("asset-1", start_ts=1, end_ts=2, interval="1h", fidelity=5)
        self.assertEqual(history, {"history": []})
        self.assertIn("/prices-history", request_url(mock_get))
        self.assertEqual(
            mock_get.call_args.kwargs["params"],
            {"market": "asset-1", "startTs": 1, "endTs": 2, "interval": "1h", "fidelity": 5},
        )

    def test_clob_public_rewards_builder_and_market_parameter_wrappers(self) -> None:
        with patch(HTTP_REQUEST, return_value=FakeResponse({"next_cursor": "LTE="})) as mock_get:
            rewards = clob_rest.get_current_rewards_config(sponsored=True, timeout=4)
        self.assertEqual(rewards, {"next_cursor": "LTE="})
        self.assertIn("/rewards/markets/current", request_url(mock_get))
        self.assertEqual(mock_get.call_args.kwargs["params"], {"sponsored": "true"})

        with patch(HTTP_REQUEST, return_value=FakeResponse({"data": []})) as mock_get:
            trades = clob_rest.get_builder_trades("0x" + "1" * 64, market="0xmarket")
        self.assertEqual(trades, {"data": []})
        self.assertIn("/builder/trades", request_url(mock_get))
        self.assertEqual(mock_get.call_args.kwargs["params"]["builder_code"], "0x" + "1" * 64)

    def test_gamma_wrappers_cover_discovery_tags_profiles_and_sports(self) -> None:
        with patch(HTTP_REQUEST, return_value=FakeResponse({"events": [{"id": "1"}]})) as mock_get:
            events = gamma.list_events_keyset(limit=999, after_cursor="next", closed=False)
        self.assertEqual(events, {"events": [{"id": "1"}]})
        self.assertIn("/events/keyset", request_url(mock_get))
        self.assertEqual(mock_get.call_args.kwargs["params"]["limit"], 500)
        self.assertEqual(mock_get.call_args.kwargs["params"]["after_cursor"], "next")
        self.assertFalse(mock_get.call_args.kwargs["params"]["closed"])

        with patch(HTTP_REQUEST, return_value=FakeResponse([{"slug": "politics"}])) as mock_get:
            related = gamma.get_tags_related_to_slug("election", status="active")
        self.assertEqual(related, [{"slug": "politics"}])
        self.assertIn("/tags/slug/election/related-tags/tags", request_url(mock_get))

        with patch(HTTP_REQUEST, return_value=FakeResponse({"marketTypes": ["moneyline"]})) as mock_get:
            market_types = gamma.get_sports_market_types()
        self.assertEqual(market_types, {"marketTypes": ["moneyline"]})
        self.assertIn("/sports/market-types", request_url(mock_get))

    def test_data_api_wrappers_cover_profile_market_and_builder_analytics(self) -> None:
        with patch(HTTP_REQUEST, return_value=FakeResponse([{"value": 12}])) as mock_get:
            value = data_api.get_total_value("0xabc", market=["0xmarket"], timeout=7)
        self.assertEqual(value, [{"value": 12}])
        self.assertIn("/value", request_url(mock_get))
        self.assertEqual(mock_get.call_args.kwargs["params"], {"user": "0xabc", "market": "0xmarket"})
        self.assertEqual(mock_get.call_args.kwargs["timeout"], 7)

        with patch(HTTP_REQUEST, return_value=FakeResponse([{"token": "asset"}])) as mock_get:
            holders = data_api.get_top_holders(["0xmarket-a", "0xmarket-b"], limit=99, min_balance=-1)
        self.assertEqual(holders, [{"token": "asset"}])
        self.assertIn("/holders", request_url(mock_get))
        self.assertEqual(mock_get.call_args.kwargs["params"]["limit"], 20)
        self.assertEqual(mock_get.call_args.kwargs["params"]["minBalance"], 0)
        self.assertEqual(mock_get.call_args.kwargs["params"]["market"], "0xmarket-a,0xmarket-b")

        with patch(HTTP_REQUEST, return_value=FakeResponse([{"builder": "test"}])) as mock_get:
            builders = data_api.get_builder_leaderboard(time_period="all", limit=100)
        self.assertEqual(builders, [{"builder": "test"}])
        self.assertIn("/v1/builders/leaderboard", request_url(mock_get))
        self.assertEqual(mock_get.call_args.kwargs["params"]["timePeriod"], "ALL")
        self.assertEqual(mock_get.call_args.kwargs["params"]["limit"], 50)

    def test_bridge_wrappers_cover_deposit_quote_status_and_withdrawal(self) -> None:
        with patch(HTTP_REQUEST, return_value=FakeResponse({"supportedAssets": []})) as mock_get:
            assets = bridge.get_supported_assets(timeout=3)
        self.assertEqual(assets, {"supportedAssets": []})
        self.assertIn("/supported-assets", request_url(mock_get))
        self.assertEqual(mock_get.call_args.kwargs["timeout"], 3)

        with patch(HTTP_REQUEST, return_value=FakeResponse({"quoteId": "q"})) as mock_post:
            quote = bridge.get_quote(
                from_amount_base_unit="100",
                from_chain_id="137",
                from_token_address="0xfrom",
                recipient_address="0xrecipient",
                to_chain_id="137",
                to_token_address="0xto",
            )
        self.assertEqual(quote, {"quoteId": "q"})
        self.assertEqual(mock_post.call_args.args[0], "POST")
        self.assertIn("/quote", request_url(mock_post))
        self.assertEqual(mock_post.call_args.kwargs["json"]["fromAmountBaseUnit"], "100")

        with patch(HTTP_REQUEST, return_value=FakeResponse({"address": {"evm": "0xdep"}})) as mock_post:
            withdrawal = bridge.create_withdrawal_addresses(
                address="0xpoly",
                to_chain_id="1",
                to_token_address="0xtoken",
                recipient_addr="0xrecipient",
            )
        self.assertEqual(withdrawal["address"]["evm"], "0xdep")
        self.assertIn("/withdraw", request_url(mock_post))

    def test_relayer_and_clob_auth_wrappers_require_explicit_credentials(self) -> None:
        with self.assertRaises(ValueError):
            relayer.submit_transaction({"from": "0xabc"}, {})

        relayer_headers = {"RELAYER_API_KEY": "key", "RELAYER_API_KEY_ADDRESS": "0xabc"}
        with patch(HTTP_REQUEST, return_value=FakeResponse({"transactionID": "tx"})) as mock_post:
            result = relayer.submit_transaction({"from": "0xabc"}, relayer_headers)
        self.assertEqual(result, {"transactionID": "tx"})
        self.assertIn("/submit", request_url(mock_post))
        self.assertEqual(mock_post.call_args.kwargs["headers"]["RELAYER_API_KEY"], "key")
        self.assertEqual(mock_post.call_args.kwargs["headers"]["RELAYER_API_KEY_ADDRESS"], "0xabc")
        self.assertEqual(mock_post.call_args.kwargs["headers"]["Content-Type"], "application/json")

        with self.assertRaises(ValueError):
            clob_auth.get_orders({})

        with patch(HTTP_REQUEST, return_value=FakeResponse({"data": []})) as mock_request:
            orders = clob_auth.get_orders(L2_HEADERS, market="0xmarket")
        self.assertEqual(orders, {"data": []})
        self.assertEqual(mock_request.call_args.args[:2], ("GET", "https://clob.polymarket.com/data/orders"))
        self.assertEqual(mock_request.call_args.kwargs["params"]["market"], "0xmarket")

    def test_polymarket_endpoint_registry_locks_documented_contract_caps(self) -> None:
        self.assertGreaterEqual(len(ALL_POLYMARKET_ENDPOINTS), 80)
        self.assertEqual(CLOB_ENDPOINTS["batch_prices_history"].max_items, 20)
        self.assertEqual(CLOB_ENDPOINTS["post_orders"].max_items, 15)
        self.assertEqual(CLOB_ENDPOINTS["cancel_orders"].max_items, 3000)
        self.assertEqual(CLOB_ENDPOINTS["post_orders"].auth, "l2")
        self.assertEqual(CLOB_ENDPOINTS["cancel_orders"].auth, "l2")
        self.assertTrue(all(endpoint.doc_url for endpoint in ALL_POLYMARKET_ENDPOINTS.values()))
        self.assertEqual(
            {endpoint.service for endpoint in ALL_POLYMARKET_ENDPOINTS.values()},
            {"gamma", "clob", "data", "bridge", "relayer"},
        )

    def test_documented_batch_caps_raise_instead_of_silently_truncating(self) -> None:
        with self.assertRaises(PolymarketValidationError):
            clob_rest.get_batch_price_history([str(i) for i in range(21)])

        with self.assertRaises(PolymarketValidationError):
            clob_auth.post_orders(({"order": i} for i in range(16)), L2_HEADERS)

        with self.assertRaises(PolymarketValidationError):
            clob_auth.cancel_orders((str(i) for i in range(3001)), L2_HEADERS)

    def test_shared_client_retries_transient_public_reads_and_raises_rate_limit(self) -> None:
        with (
            patch("polymarket.http_client.time.sleep") as mock_sleep,
            patch(
                HTTP_REQUEST,
                side_effect=[
                    FakeResponse({"error": "slow down"}, status_code=429, headers={"Retry-After": "0"}),
                    FakeResponse({"time": 123}),
                ],
            ) as mock_request,
        ):
            self.assertEqual(clob_rest.get_server_time(timeout=1), {"time": 123})

        self.assertEqual(mock_request.call_count, 2)
        mock_sleep.assert_called_once()

        with (
            patch("polymarket.http_client.time.sleep"),
            patch(
                HTTP_REQUEST,
                side_effect=[
                    FakeResponse({"error": "slow down"}, status_code=429, text="rate limited"),
                    FakeResponse({"error": "still slow"}, status_code=429, text="rate limited"),
                ],
            ),
        ):
            with self.assertRaises(PolymarketRateLimitError) as ctx:
                clob_rest.get_server_time(timeout=1)
        self.assertEqual(ctx.exception.status_code, 429)

    def test_clob_auth_readiness_distinguishes_sdk_l1_and_l2_auth(self) -> None:
        env = {
            "POLY_ADDRESS": "0xabc",
            "POLY_API_KEY": "key",
            "POLY_PASSPHRASE": "pass",
            "POLY_SIGNATURE": "sig",
            "POLY_TIMESTAMP": "1",
            "POLY_NONCE": "0",
        }
        readiness = build_clob_auth_readiness(
            {
                "private_key": "0x" + "1" * 64,
                "signature_type": 3,
                "funder_address": "0x" + "2" * 40,
            },
            environ=env,
        )

        self.assertTrue(readiness["ok"])
        self.assertTrue(readiness["sdk_trading_ready"])
        self.assertTrue(readiness["direct_l2_read_ready"])
        self.assertTrue(readiness["l1_rest_api_key_ready"])
        self.assertEqual(readiness["signature_type"]["name"], "POLY_1271")
        self.assertEqual(readiness["private_key"]["redacted"], "***")
        self.assertNotIn("1" * 64, str(readiness))

    def test_clob_auth_readiness_blocks_missing_required_funder_and_bad_key_shape(self) -> None:
        missing_funder = build_clob_auth_readiness(
            {"private_key": "0x" + "1" * 64, "signature_type": 3},
            environ={},
        )
        self.assertFalse(missing_funder["ok"])
        self.assertIn("requires an explicit funder", " ".join(missing_funder["blockers"]))

        bad_key = build_clob_auth_readiness(
            {"private_key": "not-a-key", "signature_type": 0},
            environ={},
        )
        self.assertFalse(bad_key["ok"])
        self.assertIn("0x-prefixed", " ".join(bad_key["blockers"]))

    def test_validate_sdk_trading_readiness_rejects_non_official_host_and_chain(self) -> None:
        with self.assertRaises(PolymarketValidationError):
            validate_sdk_trading_readiness(
                private_key="0x" + "1" * 64,
                signature_type=0,
                funder_address=None,
                chain_id=1,
            )

        with self.assertRaises(PolymarketValidationError):
            validate_sdk_trading_readiness(
                private_key="0x" + "1" * 64,
                signature_type=0,
                funder_address=None,
                host="https://example.invalid",
            )

    def test_live_order_cancel_harness_defaults_to_dry_run_and_redacts_credentials(self) -> None:
        plan = build_live_order_cancel_plan(
            LiveOrderCancelRequest(
                token_id="token-1",
                side="BUY",
                price="0.01",
                size="1",
                allow_token_ids=["token-1"],
                private_key="0x" + "1" * 64,
                cancel_immediately=True,
            )
        )

        self.assertEqual(plan["status"], "dry_run")
        self.assertFalse(plan["live_action"])
        self.assertEqual(plan["redacted_credentials"]["private_key"], "***")
        self.assertNotIn("1" * 64, str(plan))
        self.assertIn("Place one GTC limit order", " ".join(plan["transcript"]))

    def test_live_order_cancel_harness_blocks_missing_allow_list_confirmation_and_caps(self) -> None:
        plan = build_live_order_cancel_plan(
            LiveOrderCancelRequest(
                token_id="token-1",
                side="BUY",
                price="0.5",
                size="10",
                private_key="0x" + "1" * 64,
                execute=True,
                cancel_immediately=True,
            )
        )

        blockers = " ".join(plan["blockers"])
        self.assertEqual(plan["status"], "blocked")
        self.assertIn("Size 10 exceeds", blockers)
        self.assertIn("Missing token allow-list", blockers)
        self.assertIn("confirm-live-order-cancel", blockers)

    def test_live_order_cancel_harness_executes_place_cancel_and_post_cancel_verification(self) -> None:
        class FakeTrader:
            def __init__(self, _cfg):
                self.calls = []

            def place_limit_order(self, **kwargs):
                self.calls.append(("place", kwargs))
                return {"orderID": "order-1", "status": "live", "api_key": "secret"}

            def cancel_order(self, order_id):
                self.calls.append(("cancel", order_id))
                return {"canceled": [order_id], "not_canceled": {}}

            def get_order(self, order_id):
                self.calls.append(("get", order_id))
                return {"id": order_id, "status": "ORDER_STATUS_CANCELED"}

        result = run_live_order_cancel_verification(
            LiveOrderCancelRequest(
                token_id="token-1",
                side="BUY",
                price="0.01",
                size="1",
                allow_token_ids=["token-1"],
                private_key="0x" + "1" * 64,
                execute=True,
                cancel_immediately=True,
                confirmation=CONFIRM_LIVE_ORDER_CANCEL,
            ),
            trader_factory=FakeTrader,
            orderbook_getter=lambda _token_id: {"bids": [{"price": "0.02"}], "asks": [{"price": "0.04"}]},
        )

        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["live_action"])
        self.assertTrue(result["audit"]["post_cancel_verified"])
        self.assertEqual(result["audit"]["placed"]["api_key"], "***")

    def test_live_order_cancel_harness_blocks_market_taking_price_before_execution(self) -> None:
        class UnexpectedTrader:
            def __init__(self, _cfg):
                raise AssertionError("trader should not be created")

        result = run_live_order_cancel_verification(
            LiveOrderCancelRequest(
                token_id="token-1",
                side="BUY",
                price="0.04",
                size="1",
                allow_token_ids=["token-1"],
                private_key="0x" + "1" * 64,
                execute=True,
                cancel_immediately=True,
                confirmation=CONFIRM_LIVE_ORDER_CANCEL,
            ),
            trader_factory=UnexpectedTrader,
            orderbook_getter=lambda _token_id: {"bids": [{"price": "0.02"}], "asks": [{"price": "0.04"}]},
        )

        self.assertEqual(result["status"], "blocked")
        self.assertIn("best ask", " ".join(result["blockers"]))

    def test_live_validation_stage_gates_require_authenticated_read_before_funded(self) -> None:
        report = {
            "public_checks": {"clob_time": {"status": "ok"}},
            "authenticated_read_checks": {"clob_l2_orders": {"status": "blocked"}},
            "bridge_address_checks": {"deposit_address_creation": {"status": "skipped"}},
            "clob_auth_readiness": {"ok": True},
            "funded_live_order_check": {"status": "ready_to_execute", "live_action": True},
        }

        gates = build_live_validation_stage_gates(report)

        self.assertEqual(gates["credentialed_read_checks"], "blocked")
        self.assertFalse(gates["credentialed_read_ok"])
        self.assertFalse(gates["safe_to_attempt_funded_order"])
        self.assertIn("authenticated read", gates["next_step"])

        report["authenticated_read_checks"]["clob_l2_orders"] = {"status": "ok"}
        gates = build_live_validation_stage_gates(report)

        self.assertTrue(gates["credentialed_read_ok"])
        self.assertTrue(gates["safe_to_attempt_funded_order"])

    def test_websocket_subscription_builders_cover_market_user_and_sports_channels(self) -> None:
        self.assertEqual(
            build_market_subscription(["asset-1"], custom_feature_enabled=True),
            {"assets_ids": ["asset-1"], "type": "market", "custom_feature_enabled": True},
        )
        self.assertEqual(
            build_user_subscription(
                {"apiKey": "key", "secret": "secret", "passphrase": "pass"},
                ["0xcondition"],
            ),
            {
                "auth": {"apiKey": "key", "secret": "secret", "passphrase": "pass"},
                "type": "user",
                "markets": ["0xcondition"],
            },
        )
        with self.assertRaises(ValueError):
            build_user_subscription({"apiKey": "key"})
        self.assertEqual(sports_ws_url(), "wss://sports-api.polymarket.com/ws")

    def test_user_websocket_probe_sends_subscription_and_redacts_result(self) -> None:
        class FakeConnection:
            def __init__(self) -> None:
                self.sent = []
                self.closed = False

            def send(self, message: str) -> None:
                self.sent.append(message)

            def recv(self) -> str:
                return "PONG"

            def close(self) -> None:
                self.closed = True

        connections = []

        def factory(url, timeout):
            conn = FakeConnection()
            connections.append((url, timeout, conn))
            return conn

        result = probe_user_websocket(
            {"apiKey": "key", "secret": "secret-value", "passphrase": "pass"},
            ["condition-1"],
            timeout=4,
            connection_factory=factory,
        )

        self.assertEqual(connections[0][0], user_ws_url())
        self.assertEqual(connections[0][1], 4)
        self.assertTrue(result["connected"])
        self.assertTrue(result["subscription_sent"])
        self.assertTrue(connections[0][2].closed)
        subscription = json.loads(connections[0][2].sent[0])
        self.assertEqual(subscription["markets"], ["condition-1"])
        self.assertEqual(subscription["auth"]["secret"], "secret-value")
        self.assertNotIn("secret-value", str(result))

    def test_polymarket_official_api_coverage_manifest_uses_truthful_tiered_status(self) -> None:
        coverage = polymarket_official_api_coverage()
        self.assertEqual(coverage["docs_checked"], "2026-05-28")
        self.assertTrue(coverage["categories"])
        self.assertIn("polymarket.http_client", coverage["contract_hardening"]["modules"])
        self.assertIn("documented batch caps", " ".join(coverage["contract_hardening"]["features"]))
        self.assertEqual(coverage["authenticated_clob_readiness"]["api_route"], "/api/polymarket/clob-readiness")
        self.assertIn("polymarket.auth_readiness", coverage["authenticated_clob_readiness"]["module"])
        self.assertEqual(coverage["live_order_cancel_harness"]["default_mode"], "dry_run_transcript")
        self.assertEqual(coverage["live_order_cancel_harness"]["hard_caps"]["max_notional_usdc"], 1.0)
        self.assertEqual(coverage["live_credential_validation"]["default_mode"], "no_funded_actions")
        self.assertIn("stage_gates", coverage["live_credential_validation"]["report_fields"])
        self.assertEqual(coverage["historical_mdd_v2"]["method"], "public_data_historical_equity_curve_v2")
        self.assertIn("/api/polymarket/users/mdd", coverage["historical_mdd_v2"]["api_routes"])
        self.assertEqual(coverage["historical_mark_replay_mdd"]["method"], "clob_price_history_inventory_mark_replay_v1")
        self.assertEqual(coverage["historical_mark_replay_mdd"]["default"], "off")
        self.assertEqual(coverage["accounting_snapshot_reconciliation"]["module"], "polymarket.accounting")
        self.assertEqual(coverage["accounting_snapshot_reconciliation"]["default"], "off")
        self.assertEqual(coverage["analytics_cache_exports"]["module"], "polymarket.analytics_cache")
        self.assertIn("/api/polymarket/users/mdd/export.csv", coverage["analytics_cache_exports"]["api_routes"])
        expected_levels = set(coverage["coverage_level_definitions"])
        allowed_states = set(coverage["coverage_state_definitions"])
        self.assertEqual(
            expected_levels,
            {
                "wrapper_available",
                "app_workflow_available",
                "offline_tested",
                "public_live_verified",
                "credential_live_verified",
                "funded_live_verified",
            },
        )
        for item in coverage["categories"]:
            self.assertIn("truthful_status", item)
            self.assertEqual(set(item["coverage_levels"]), expected_levels)
            self.assertTrue(set(item["coverage_levels"].values()).issubset(allowed_states))
        self.assertIn("blocked", {item["coverage_levels"]["credential_live_verified"] for item in coverage["categories"]})
        self.assertNotIn("yes", {item["coverage_levels"]["funded_live_verified"] for item in coverage["categories"]})
        modules = " ".join(item["module"] for item in coverage["categories"])
        self.assertIn("polymarket.bridge", modules)
        self.assertIn("polymarket.relayer", modules)


if __name__ == "__main__":
    unittest.main()
