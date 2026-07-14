from __future__ import annotations

import unittest
import io
import json
import subprocess
import sys
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
from polymarket.credential_runbook import build_polymarket_credential_runbook
from polymarket.endpoints import ALL_POLYMARKET_ENDPOINTS, CLOB_ENDPOINTS
from polymarket.http_client import PolymarketRateLimitError, PolymarketValidationError
from polymarket.live_verification import (
    CONFIRM_LIVE_ORDER_CANCEL,
    LiveOrderCancelRequest,
    build_live_validation_stage_gates,
    build_live_order_cancel_plan,
    run_live_order_cancel_verification,
)
from polymarket.live_reports import (
    find_live_validation_report_duplicate,
    list_live_validation_coverage_promotion_proposal_snapshots,
    list_live_validation_reports,
    load_live_validation_coverage_promotion_proposal_snapshot,
    live_validation_coverage_promotion_proposal,
    live_validation_coverage_promotion_proposal_hash,
    live_validation_coverage_promotion_proposal_markdown,
    live_validation_promotion_proposal_snapshot_diff_markdown,
    live_validation_promotion_proposal_snapshot_markdown,
    live_validation_report_payload_hash,
    live_validation_report_decisions_markdown,
    live_validation_report_promotion,
    live_validation_report_promotion_inventory,
    live_validation_report_review_bundle,
    live_validation_report_review_bundle_hash,
    live_validation_report_review_markdown,
    live_validation_report_summary,
    list_live_validation_report_decisions,
    purge_live_validation_coverage_promotion_proposal_snapshots,
    record_live_validation_report_decision,
    store_live_validation_coverage_promotion_proposal_snapshot,
    store_live_validation_report,
)
from polymarket.live_report_replay import replay_live_validation_report_paths
from polymarket.live_report_schema import (
    LiveValidationReportSchemaError,
    ensure_live_validation_report_valid,
    parse_live_validation_report_json,
    validate_live_validation_report,
)
from polymarket.ws_market import build_market_subscription
from polymarket.ws_sports import sports_ws_url
from polymarket.ws_user import build_user_subscription, probe_user_websocket, user_ws_url


HTTP_REQUEST = "polymarket.http_client.requests.request"
ROOT = Path(__file__).resolve().parent.parent
LIVE_REPORT_FIXTURE_ROOT = ROOT / "tests" / "fixtures" / "polymarket" / "live_reports"


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
        self.assertEqual(params["sortDirection"], "DESC")
        self.assertEqual(params["timePeriod"], "ALL")
        self.assertEqual(params["category"], "OVERALL")
        self.assertEqual(mock_get.call_args.kwargs["timeout"], 5)

    def test_leaderboard_request_allows_deep_scan_offsets(self) -> None:
        with patch(HTTP_REQUEST, return_value=FakeResponse({"data": []})) as mock_get:
            data_api.get_leaderboard(offset=2_500_000)

        params = mock_get.call_args.kwargs["params"]
        self.assertEqual(params["offset"], 2_500_000)

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

    def test_live_report_promotion_requires_concrete_authenticated_read_evidence(self) -> None:
        claimed_report = {
            "mode": "strict_cli",
            "stage_gates": {
                "credentialed_read_ok": True,
                "credentialed_read_checks": "ok",
                "funded_live_order_check": "blocked",
            },
            "authenticated_read_checks": {
                "py_clob_client_credentials": {"status": "ok", "detail": "credentials derived"},
            },
        }

        promotion = live_validation_report_promotion(claimed_report)

        self.assertEqual(promotion["credential_live_verified"], "blocked")
        self.assertFalse(promotion["can_promote_credential_live_verified"])
        self.assertIn("no accepted authenticated-read evidence", " ".join(promotion["blocked_reasons"]))

        verified_report = {
            "mode": "strict_cli",
            "authenticated_read_checks": {
                "clob_l2_orders": {
                    "status": "ok",
                    "detail": "Authenticated CLOB order list responded.",
                    "sample_type": "dict",
                }
            },
            "funded_live_order_check": {"status": "blocked"},
        }
        verified = live_validation_report_summary(verified_report)

        self.assertEqual(verified["credential_live_verified"], "yes")
        self.assertTrue(verified["can_promote_credential_live_verified"])
        self.assertEqual(
            verified["verification_promotion"]["credential_evidence"][0]["check"],
            "clob_l2_orders",
        )

    def test_live_report_promotion_requires_funded_order_cancel_audit_evidence(self) -> None:
        dry_run_report = {
            "mode": "strict_cli",
            "funded_live_order_check": {
                "status": "dry_run",
                "live_action": False,
                "transcript": ["would place and cancel"],
            },
        }

        dry_run = live_validation_report_promotion(dry_run_report)

        self.assertEqual(dry_run["funded_live_verified"], "blocked")
        self.assertFalse(dry_run["can_promote_funded_live_verified"])

        funded_report = {
            "mode": "strict_cli",
            "funded_live_order_check": {
                "status": "ok",
                "live_action": True,
                "audit": {
                    "order_id": "order-1",
                    "placed": {"status": "live"},
                    "cancel": {"canceled": ["order-1"]},
                    "post_cancel_order": {"status": "ORDER_STATUS_CANCELED"},
                    "post_cancel_verified": True,
                },
            },
        }
        funded = live_validation_report_summary(funded_report)

        self.assertEqual(funded["funded_live_verified"], "yes")
        self.assertTrue(funded["can_promote_funded_live_verified"])
        self.assertEqual(funded["verification_promotion"]["funded_evidence"][0]["check"], "funded_order_cancel")

    def test_live_report_promotion_blocks_local_runbook_and_browser_smoke_reports(self) -> None:
        for mode in ("local_readiness_only", "credential_runbook_no_funded_actions", "browser_smoke", "browser_smoke_seed"):
            promotion = live_validation_report_promotion(
                {
                    "mode": mode,
                    "authenticated_read_checks": {"clob_l2_orders": {"status": "ok"}},
                    "funded_live_order_check": {
                        "status": "ok",
                        "live_action": True,
                        "audit": {
                            "order_id": "order-1",
                            "placed": {},
                            "cancel": {},
                            "post_cancel_order": {},
                            "post_cancel_verified": True,
                        },
                    },
                }
            )

            self.assertEqual(promotion["credential_live_verified"], "blocked")
            self.assertEqual(promotion["funded_live_verified"], "blocked")
            self.assertIn("local-only", " ".join(promotion["blocked_reasons"]))

    def test_live_report_promotion_inventory_keeps_static_coverage_unmutated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "reports.json"
            store_live_validation_report(
                {
                    "mode": "strict_cli",
                    "authenticated_read_checks": {"user_websocket_connect": {"status": "ok", "detail": "connected"}},
                    "funded_live_order_check": {"status": "dry_run", "live_action": False},
                    "stage_gates": {
                        "credentialed_read_ok": True,
                        "credentialed_read_checks": "ok",
                        "funded_live_order_check": "dry_run",
                        "safe_to_attempt_funded_order": False,
                        "requires_explicit_live_approval": True,
                    },
                },
                source="cli",
                label="credential evidence",
                path=path,
            )
            store_live_validation_report(
                {
                    "mode": "browser_smoke",
                    "authenticated_read_checks": {"clob_l2_orders": {"status": "ok"}},
                    "funded_live_order_check": {
                        "status": "ok",
                        "live_action": True,
                        "audit": {
                            "order_id": "fake",
                            "placed": {},
                            "cancel": {},
                            "post_cancel_order": {},
                            "post_cancel_verified": True,
                        },
                    },
                    "stage_gates": {
                        "credentialed_read_ok": True,
                        "credentialed_read_checks": "ok",
                        "funded_live_order_check": "ok",
                        "safe_to_attempt_funded_order": False,
                        "requires_explicit_live_approval": True,
                    },
                },
                source="browser_smoke",
                label="local smoke",
                path=path,
            )

            inventory = live_validation_report_promotion_inventory(path=path)

        self.assertFalse(inventory["static_coverage_mutated"])
        self.assertEqual(inventory["credential_live_verified"], "yes")
        self.assertEqual(inventory["funded_live_verified"], "blocked")
        self.assertEqual(inventory["counts"]["credential_candidates"], 1)
        self.assertEqual(inventory["counts"]["funded_candidates"], 0)
        self.assertIn("credential evidence", inventory["credential_candidates"][0]["label"])

    def test_live_report_schema_accepts_deterministic_valid_fixtures(self) -> None:
        expected_modes = {
            "valid_credentialed_read.json": "strict_cli",
            "valid_funded_audit.json": "strict_cli",
            "valid_dry_run.json": "strict_cli",
            "valid_runbook.json": "credential_runbook_no_funded_actions",
            "valid_browser_smoke.json": "browser_smoke_seed",
        }
        for name, mode in expected_modes.items():
            payload = json.loads((LIVE_REPORT_FIXTURE_ROOT / name).read_text(encoding="utf-8"))
            validation = validate_live_validation_report(payload)

            self.assertTrue(validation["ok"], name)
            self.assertEqual(validation["mode"], mode)
            self.assertEqual(validation["schema_version"], 1)

        credentialed = live_validation_report_summary(
            json.loads((LIVE_REPORT_FIXTURE_ROOT / "valid_credentialed_read.json").read_text(encoding="utf-8"))
        )
        funded = live_validation_report_summary(
            json.loads((LIVE_REPORT_FIXTURE_ROOT / "valid_funded_audit.json").read_text(encoding="utf-8"))
        )
        dry_run = live_validation_report_summary(
            json.loads((LIVE_REPORT_FIXTURE_ROOT / "valid_dry_run.json").read_text(encoding="utf-8"))
        )
        runbook = live_validation_report_summary(
            json.loads((LIVE_REPORT_FIXTURE_ROOT / "valid_runbook.json").read_text(encoding="utf-8"))
        )
        browser = live_validation_report_summary(
            json.loads((LIVE_REPORT_FIXTURE_ROOT / "valid_browser_smoke.json").read_text(encoding="utf-8"))
        )

        self.assertEqual(credentialed["credential_live_verified"], "yes")
        self.assertEqual(funded["funded_live_verified"], "yes")
        self.assertEqual(dry_run["funded_live_verified"], "blocked")
        self.assertEqual(runbook["credential_live_verified"], "blocked")
        self.assertEqual(browser["credential_live_verified"], "blocked")

    def test_live_report_schema_rejects_invalid_fixtures_and_bad_json(self) -> None:
        for name in ("invalid_missing_mode.json", "invalid_bad_stage_gates.json"):
            payload = json.loads((LIVE_REPORT_FIXTURE_ROOT / name).read_text(encoding="utf-8"))
            validation = validate_live_validation_report(payload)

            self.assertFalse(validation["ok"], name)
            self.assertTrue(validation["errors"], name)
            with self.assertRaises(LiveValidationReportSchemaError):
                ensure_live_validation_report_valid(payload)

        with self.assertRaises(LiveValidationReportSchemaError) as ctx:
            parse_live_validation_report_json("[]")
        self.assertFalse(ctx.exception.validation["ok"])
        self.assertIn("decode to an object", " ".join(ctx.exception.validation["errors"]))

        with self.assertRaises(LiveValidationReportSchemaError) as bad_json:
            parse_live_validation_report_json("{not-json")
        self.assertFalse(bad_json.exception.validation["ok"])
        self.assertIn("valid JSON", " ".join(bad_json.exception.validation["errors"]))

    def test_live_report_store_attaches_schema_validation_and_rejects_invalid_reports(self) -> None:
        valid = json.loads((LIVE_REPORT_FIXTURE_ROOT / "valid_credentialed_read.json").read_text(encoding="utf-8"))
        invalid = json.loads((LIVE_REPORT_FIXTURE_ROOT / "invalid_bad_stage_gates.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "reports.json"
            stored = store_live_validation_report(valid, source="fixture", label="valid credentialed", path=path)

            self.assertTrue(stored["schema_validation"]["ok"])
            self.assertEqual(stored["schema_validation"]["mode"], "strict_cli")

            with self.assertRaises(LiveValidationReportSchemaError) as ctx:
                store_live_validation_report(invalid, source="fixture", label="bad", path=path)
            self.assertFalse(ctx.exception.validation["ok"])
            self.assertIn("stage_gates must be an object", " ".join(ctx.exception.validation["errors"]))

    def test_live_report_store_hashes_provenance_and_skips_duplicates_by_default(self) -> None:
        valid = json.loads((LIVE_REPORT_FIXTURE_ROOT / "valid_credentialed_read.json").read_text(encoding="utf-8"))
        valid["api_key"] = "redacted-hash-secret"
        same_redacted_payload = json.loads(json.dumps(valid))
        same_redacted_payload["api_key"] = "different-secret-same-redacted-payload"

        with tempfile.TemporaryDirectory() as tmp:
            temp = Path(tmp)
            path = temp / "reports.json"
            source_file = temp / "credentialed.json"
            first = store_live_validation_report(
                valid,
                source="fixture",
                label="credentialed",
                path=path,
                source_file=source_file,
            )

            expected_hash = live_validation_report_payload_hash(valid)
            self.assertEqual(len(expected_hash), 64)
            self.assertEqual(expected_hash, live_validation_report_payload_hash(same_redacted_payload))
            self.assertEqual(first["payload_hash"], expected_hash)
            self.assertEqual(first["provenance"]["source_file_name"], "credentialed.json")
            duplicate_lookup = find_live_validation_report_duplicate(expected_hash, path=path)
            self.assertIsNotNone(duplicate_lookup)
            self.assertEqual(duplicate_lookup["key"], first["key"])

            skipped = store_live_validation_report(
                same_redacted_payload,
                source="fixture_replay",
                label="credentialed replay",
                path=path,
                source_file=temp / "credentialed-copy.json",
            )

            self.assertFalse(skipped["stored"])
            self.assertTrue(skipped["duplicate"])
            self.assertEqual(skipped["duplicate_key"], first["key"])
            self.assertEqual(skipped["duplicate_policy"], "skip")
            self.assertEqual(skipped["duplicate_audit_event"]["source_file_name"], "credentialed-copy.json")
            listing = list_live_validation_reports(path=path)
            self.assertEqual(listing["counts"]["entries"], 1)
            self.assertEqual(listing["counts"]["duplicate_imports"], 1)
            self.assertEqual(listing["entries"][0]["duplicate_import_count"], 1)
            self.assertTrue(listing["entries"][0]["duplicate"])

            allowed = store_live_validation_report(
                same_redacted_payload,
                source="fixture_replay",
                label="credentialed allowed duplicate",
                path=path,
                source_file=temp / "credentialed-allowed.json",
                allow_duplicate=True,
            )

            self.assertTrue(allowed["stored"])
            self.assertTrue(allowed["duplicate"])
            self.assertEqual(allowed["duplicate_of"], first["key"])
            self.assertEqual(allowed["payload_hash"], expected_hash)
            listing = list_live_validation_reports(path=path)
            self.assertEqual(listing["counts"]["entries"], 2)
            self.assertEqual(listing["counts"]["duplicate_payloads"], 1)
            self.assertEqual({entry["payload_hash"] for entry in listing["entries"]}, {expected_hash})
            disk = path.read_text(encoding="utf-8")
            self.assertNotIn("redacted-hash-secret", disk)
            self.assertNotIn("different-secret-same-redacted-payload", disk)

    def test_live_report_review_bundle_is_sanitized_and_maps_promotion_to_coverage(self) -> None:
        report = json.loads((LIVE_REPORT_FIXTURE_ROOT / "valid_dry_run.json").read_text(encoding="utf-8"))
        report["api_key"] = "review-secret-api-key"
        report["operator_commands"] = {
            "safe_live_probe": "python scripts/verify_polymarket_live.py --timeout 8",
            "credentialed_read": "python scripts/verify_polymarket_live.py --require-authenticated-read-ok --report-file live-report.json",
        }

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "reports.json"
            stored = store_live_validation_report(
                report,
                source="fixture",
                label="review dry run",
                path=path,
                source_file="valid_dry_run.json",
            )
            store_live_validation_report(
                report,
                source="fixture",
                label="review dry run duplicate",
                path=path,
                source_file="valid_dry_run-copy.json",
            )

            bundle = live_validation_report_review_bundle(stored["key"], path=path)

        self.assertIsNotNone(bundle)
        assert bundle is not None
        self.assertEqual(bundle["source"], "polymarket_live_validation_report_review_bundle")
        self.assertFalse(bundle["static_coverage_mutated"])
        self.assertFalse(bundle["funded_execution_exposed"])
        self.assertEqual(bundle["report"]["payload_hash"], stored["payload_hash"])
        self.assertEqual(bundle["report"]["provenance"]["source_file_name"], "valid_dry_run.json")
        self.assertTrue(bundle["schema_validation"]["ok"])
        self.assertEqual(bundle["duplicate_history"]["duplicate_import_count"], 1)
        self.assertEqual(bundle["duplicate_history"]["duplicate_imports"][0]["source_file_name"], "valid_dry_run-copy.json")
        self.assertEqual(
            bundle["operator_commands"]["credentialed_read"],
            "python scripts/verify_polymarket_live.py --require-authenticated-read-ok --report-file live-report.json",
        )
        self.assertEqual(bundle["promotion_review"]["funded_live_verified"], "blocked")
        self.assertIn("Funded live verification requires", " ".join(bundle["promotion_review"]["blocked_reasons"]))
        self.assertFalse(bundle["coverage_tier_mapping"]["levels"]["funded_live_verified"]["can_promote_from_report"])
        self.assertTrue(bundle["coverage_tier_mapping"]["levels"]["credential_live_verified"]["can_promote_from_report"])
        self.assertEqual(
            bundle["coverage_tier_mapping"]["levels"]["credential_live_verified"]["review_effect"],
            "candidate_evidence_only",
        )
        self.assertNotIn("payload", bundle)
        bundle_text = json.dumps(bundle, sort_keys=True)
        self.assertNotIn("review-secret-api-key", bundle_text)
        markdown = live_validation_report_review_markdown(bundle)
        self.assertIn("Polymarket Live Validation Review Bundle", markdown)
        self.assertIn("Static coverage mutated: false", markdown)
        self.assertIn("python scripts/verify_polymarket_live.py --timeout 8", markdown)
        self.assertNotIn("review-secret-api-key", markdown)

    def test_live_report_decision_ledger_requires_matching_review_hash_and_exports(self) -> None:
        report = json.loads((LIVE_REPORT_FIXTURE_ROOT / "valid_credentialed_read.json").read_text(encoding="utf-8"))
        report["api_key"] = "decision-secret-api-key"
        with tempfile.TemporaryDirectory() as tmp:
            temp = Path(tmp)
            report_path = temp / "reports.json"
            decision_path = temp / "decisions.json"
            stored = store_live_validation_report(
                report,
                source="fixture",
                label="credential decision",
                path=report_path,
                source_file="valid_credentialed_read.json",
            )
            bundle = live_validation_report_review_bundle(stored["key"], path=report_path)
            self.assertIsNotNone(bundle)
            assert bundle is not None
            review_hash = live_validation_report_review_bundle_hash(bundle)
            self.assertEqual(bundle["review_bundle_hash"], review_hash)

            accepted = record_live_validation_report_decision(
                report_key=stored["key"],
                payload_hash=stored["payload_hash"],
                target_tier="credential_live_verified",
                decision="accepted",
                reviewer_note="Authenticated read evidence is present in the stored review bundle.",
                review_bundle_hash=review_hash,
                reviewer="unit-test",
                report_store_path=report_path,
                decision_path=decision_path,
            )

            self.assertTrue(accepted["stored"])
            self.assertEqual(accepted["decision"], "accepted")
            self.assertEqual(accepted["target_tier"], "credential_live_verified")
            self.assertTrue(accepted["review_bundle_hash_verified"])
            self.assertFalse(accepted["static_coverage_mutated"])
            self.assertEqual(accepted["promotion_effect"], "ledger_only_no_static_coverage_mutation")

            rejected = record_live_validation_report_decision(
                report_key=stored["key"],
                payload_hash=stored["payload_hash"],
                target_tier="funded_live_verified",
                decision="rejected",
                reviewer_note="Funded order/cancel evidence is absent.",
                review_bundle_hash=review_hash,
                reviewer="unit-test",
                report_store_path=report_path,
                decision_path=decision_path,
            )
            self.assertEqual(rejected["decision"], "rejected")

            with self.assertRaises(ValueError) as payload_mismatch:
                record_live_validation_report_decision(
                    report_key=stored["key"],
                    payload_hash="bad-payload-hash",
                    target_tier="credential_live_verified",
                    decision="accepted",
                    reviewer_note="bad",
                    review_bundle_hash=review_hash,
                    report_store_path=report_path,
                    decision_path=decision_path,
                )
            self.assertIn("payload_hash mismatch", str(payload_mismatch.exception))

            with self.assertRaises(ValueError) as tamper_mismatch:
                record_live_validation_report_decision(
                    report_key=stored["key"],
                    payload_hash=stored["payload_hash"],
                    target_tier="credential_live_verified",
                    decision="accepted",
                    reviewer_note="bad",
                    review_bundle_hash="bad-review-hash",
                    report_store_path=report_path,
                    decision_path=decision_path,
                )
            self.assertIn("review_bundle_hash mismatch", str(tamper_mismatch.exception))

            with self.assertRaises(ValueError) as blocked_accept:
                record_live_validation_report_decision(
                    report_key=stored["key"],
                    payload_hash=stored["payload_hash"],
                    target_tier="funded_live_verified",
                    decision="accepted",
                    reviewer_note="bad",
                    review_bundle_hash=review_hash,
                    report_store_path=report_path,
                    decision_path=decision_path,
                )
            self.assertIn("Cannot accept funded_live_verified", str(blocked_accept.exception))

            ledger = list_live_validation_report_decisions(path=decision_path)
            self.assertEqual(ledger["counts"]["entries"], 2)
            self.assertEqual(ledger["counts"]["accepted"], 1)
            self.assertEqual(ledger["counts"]["rejected"], 1)
            markdown = live_validation_report_decisions_markdown(ledger)
            self.assertIn("Promotion Decision Ledger", markdown)
            self.assertIn("static coverage tiers", markdown)
            self.assertNotIn("decision-secret-api-key", json.dumps(ledger, sort_keys=True))

            cli = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "review_polymarket_live_decisions.py"),
                    "--export-ledger",
                    "--markdown",
                    "--decision-path",
                    str(decision_path),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(cli.returncode, 0)
            self.assertIn("Promotion Decision Ledger", cli.stdout)
            self.assertNotIn("decision-secret-api-key", cli.stdout)

    def test_live_report_promotion_proposal_exports_candidates_and_detects_stale_decisions(self) -> None:
        report = json.loads((LIVE_REPORT_FIXTURE_ROOT / "valid_credentialed_read.json").read_text(encoding="utf-8"))
        report["api_key"] = "proposal-secret-api-key"
        with tempfile.TemporaryDirectory() as tmp:
            temp = Path(tmp)
            report_path = temp / "reports.json"
            decision_path = temp / "decisions.json"
            stored = store_live_validation_report(
                report,
                source="fixture",
                label="credential proposal",
                path=report_path,
                source_file="valid_credentialed_read.json",
            )
            bundle = live_validation_report_review_bundle(stored["key"], path=report_path)
            self.assertIsNotNone(bundle)
            assert bundle is not None
            review_hash = live_validation_report_review_bundle_hash(bundle)
            record_live_validation_report_decision(
                report_key=stored["key"],
                payload_hash=stored["payload_hash"],
                target_tier="credential_live_verified",
                decision="accepted",
                reviewer_note="Authenticated read evidence is accepted for proposal generation.",
                review_bundle_hash=review_hash,
                reviewer="unit-test",
                report_store_path=report_path,
                decision_path=decision_path,
            )

            proposal = live_validation_coverage_promotion_proposal(
                report_store_path=report_path,
                decision_path=decision_path,
            )
            self.assertEqual(proposal["source"], "polymarket_live_validation_coverage_promotion_proposal")
            self.assertTrue(proposal["human_review_required"])
            self.assertFalse(proposal["automerge_enabled"])
            self.assertFalse(proposal["apply_by_default"])
            self.assertFalse(proposal["static_coverage_mutated"])
            self.assertEqual(proposal["counts"]["accepted_candidates"], 1)
            self.assertEqual(proposal["counts"]["stale_decisions"], 0)
            self.assertGreaterEqual(proposal["counts"]["proposed_changes"], 4)
            self.assertEqual(proposal["proposal_hash"], live_validation_coverage_promotion_proposal_hash(proposal))
            self.assertIn("polymarket/coverage.py", proposal["patch_proposal"]["files"])
            markdown = live_validation_coverage_promotion_proposal_markdown(proposal)
            self.assertIn("Coverage Promotion Proposal", markdown)
            self.assertIn("Automerge enabled: false", markdown)
            self.assertNotIn("proposal-secret-api-key", json.dumps(proposal, sort_keys=True))
            self.assertNotIn("proposal-secret-api-key", markdown)

            cli = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "review_polymarket_live_decisions.py"),
                    "--export-proposal",
                    "--markdown",
                    "--report-store-path",
                    str(report_path),
                    "--decision-path",
                    str(decision_path),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(cli.returncode, 0)
            self.assertIn("Coverage Promotion Proposal", cli.stdout)
            self.assertNotIn("proposal-secret-api-key", cli.stdout)

            report_store = json.loads(report_path.read_text(encoding="utf-8"))
            report_store["reports"][stored["key"]]["payload_hash"] = "stale-payload-hash"
            report_path.write_text(json.dumps(report_store), encoding="utf-8")
            stale = live_validation_coverage_promotion_proposal(
                report_store_path=report_path,
                decision_path=decision_path,
            )
            self.assertEqual(stale["counts"]["accepted_candidates"], 0)
            self.assertEqual(stale["counts"]["stale_decisions"], 1)
            stale_reasons = stale["stale_decisions"][0]["stale_reasons"]
            self.assertIn("payload_hash_mismatch", stale_reasons)
            self.assertIn("review_bundle_hash_mismatch", stale_reasons)

    def test_live_report_promotion_proposal_snapshot_archive_detects_stale_and_prunes(self) -> None:
        report = json.loads((LIVE_REPORT_FIXTURE_ROOT / "valid_credentialed_read.json").read_text(encoding="utf-8"))
        report["api_key"] = "snapshot-secret-api-key"
        with tempfile.TemporaryDirectory() as tmp:
            temp = Path(tmp)
            report_path = temp / "reports.json"
            decision_path = temp / "decisions.json"
            snapshot_path = temp / "proposal-snapshots.json"
            stored = store_live_validation_report(
                report,
                source="fixture",
                label="snapshot proposal",
                path=report_path,
                source_file="valid_credentialed_read.json",
            )
            bundle = live_validation_report_review_bundle(stored["key"], path=report_path)
            self.assertIsNotNone(bundle)
            assert bundle is not None
            record_live_validation_report_decision(
                report_key=stored["key"],
                payload_hash=stored["payload_hash"],
                target_tier="credential_live_verified",
                decision="accepted",
                reviewer_note="Authenticated read evidence is accepted for snapshot storage.",
                review_bundle_hash=str(bundle["review_bundle_hash"]),
                reviewer="unit-test",
                report_store_path=report_path,
                decision_path=decision_path,
            )
            proposal = live_validation_coverage_promotion_proposal(
                report_store_path=report_path,
                decision_path=decision_path,
                target_tier="credential_live_verified",
            )
            collision_snapshot_path = temp / "collision-proposal-snapshots.json"
            with (
                patch("polymarket.live_reports._now", return_value=1700000000),
                patch("polymarket.live_reports.time.time_ns", return_value=1700000000000000000),
            ):
                first_collision = store_live_validation_coverage_promotion_proposal_snapshot(
                    proposal=proposal,
                    report_store_path=report_path,
                    decision_path=decision_path,
                    target_tier="credential_live_verified",
                    path=collision_snapshot_path,
                    source="unit-test",
                    label="same clock snapshot",
                )
                second_collision = store_live_validation_coverage_promotion_proposal_snapshot(
                    proposal=proposal,
                    report_store_path=report_path,
                    decision_path=decision_path,
                    target_tier="credential_live_verified",
                    path=collision_snapshot_path,
                    source="unit-test",
                    label="same clock snapshot",
                )
            self.assertNotEqual(first_collision["key"], second_collision["key"])
            collision_listing = list_live_validation_coverage_promotion_proposal_snapshots(
                path=collision_snapshot_path,
                report_store_path=report_path,
                decision_path=decision_path,
            )
            self.assertEqual(collision_listing["counts"]["entries"], 2)

            snapshot = store_live_validation_coverage_promotion_proposal_snapshot(
                proposal=proposal,
                report_store_path=report_path,
                decision_path=decision_path,
                target_tier="credential_live_verified",
                path=snapshot_path,
                source="unit-test",
                label="credential proposal snapshot",
            )
            self.assertTrue(snapshot["stored"])
            self.assertFalse(snapshot["static_coverage_mutated"])
            self.assertEqual(snapshot["snapshot_status"], "current")

            opened = load_live_validation_coverage_promotion_proposal_snapshot(
                snapshot["key"],
                path=snapshot_path,
                report_store_path=report_path,
                decision_path=decision_path,
            )
            self.assertIsNotNone(opened)
            assert opened is not None
            self.assertEqual(opened["entry"]["snapshot_status"], "current")
            self.assertNotIn("snapshot-secret-api-key", json.dumps(opened, sort_keys=True))
            markdown = live_validation_promotion_proposal_snapshot_markdown(opened)
            self.assertIn("Promotion Proposal Snapshot", markdown)
            self.assertIn("Static coverage mutated: false", markdown)
            self.assertNotIn("snapshot-secret-api-key", markdown)

            duplicate = store_live_validation_report(
                report,
                source="fixture",
                label="snapshot proposal changed",
                path=report_path,
                source_file="valid_credentialed_read.json",
                allow_duplicate=True,
            )
            changed_bundle = live_validation_report_review_bundle(duplicate["key"], path=report_path)
            self.assertIsNotNone(changed_bundle)
            assert changed_bundle is not None
            record_live_validation_report_decision(
                report_key=duplicate["key"],
                payload_hash=duplicate["payload_hash"],
                target_tier="credential_live_verified",
                decision="accepted",
                reviewer_note="Second accepted evidence changes the proposal hash.",
                review_bundle_hash=str(changed_bundle["review_bundle_hash"]),
                reviewer="unit-test",
                report_store_path=report_path,
                decision_path=decision_path,
            )
            stale_listing = list_live_validation_coverage_promotion_proposal_snapshots(
                path=snapshot_path,
                report_store_path=report_path,
                decision_path=decision_path,
            )
            self.assertEqual(stale_listing["counts"]["stale"], 1)
            self.assertIn("proposal_hash_mismatch", stale_listing["entries"][0]["stale_reasons"])
            stale_opened = load_live_validation_coverage_promotion_proposal_snapshot(
                snapshot["key"],
                path=snapshot_path,
                report_store_path=report_path,
                decision_path=decision_path,
            )
            self.assertIsNotNone(stale_opened)
            assert stale_opened is not None
            diff = stale_opened["diff"]
            self.assertTrue(diff["changed"])
            self.assertIn("proposal_hash", diff["change_categories"])
            self.assertEqual(len(diff["accepted_decisions"]["added"]), 1)
            self.assertEqual(diff["accepted_decisions"]["added"][0]["report_key"], duplicate["key"])
            self.assertEqual(diff["accepted_decisions"]["added"][0]["target_tier"], "credential_live_verified")
            self.assertIn("Current-vs-Snapshot Diff", live_validation_promotion_proposal_snapshot_markdown(stale_opened))
            diff_markdown = live_validation_promotion_proposal_snapshot_diff_markdown(diff)
            self.assertIn("Current-vs-Snapshot Diff", diff_markdown)
            self.assertNotIn("snapshot-secret-api-key", json.dumps(diff, sort_keys=True))
            self.assertNotIn("snapshot-secret-api-key", diff_markdown)

            changed = live_validation_coverage_promotion_proposal(
                report_store_path=report_path,
                decision_path=decision_path,
                target_tier="credential_live_verified",
            )
            second = store_live_validation_coverage_promotion_proposal_snapshot(
                proposal=changed,
                report_store_path=report_path,
                decision_path=decision_path,
                target_tier="credential_live_verified",
                path=snapshot_path,
                source="unit-test",
                label="retained snapshot",
                max_entries=1,
            )
            pruned = list_live_validation_coverage_promotion_proposal_snapshots(
                path=snapshot_path,
                report_store_path=report_path,
                decision_path=decision_path,
            )
            self.assertEqual(pruned["counts"]["entries"], 1)
            self.assertEqual(pruned["entries"][0]["key"], second["key"])

            purged = purge_live_validation_coverage_promotion_proposal_snapshots(keys=[second["key"]], path=snapshot_path)
            self.assertEqual(purged["deleted"], 1)
            self.assertEqual(purged["counts"]["entries"], 0)

    def test_live_report_replay_validates_valid_and_invalid_fixtures_without_import(self) -> None:
        result = replay_live_validation_report_paths(
            [
                LIVE_REPORT_FIXTURE_ROOT / "valid_credentialed_read.json",
                LIVE_REPORT_FIXTURE_ROOT / "valid_funded_audit.json",
                LIVE_REPORT_FIXTURE_ROOT / "invalid_missing_mode.json",
            ]
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["mode"], "dry_run")
        self.assertFalse(result["funded_execution_exposed"])
        self.assertEqual(result["counts"]["files"], 3)
        self.assertEqual(result["counts"]["valid"], 2)
        self.assertEqual(result["counts"]["invalid"], 1)
        self.assertEqual(result["counts"]["imported"], 0)
        credentialed = result["entries"][0]
        funded = result["entries"][1]
        invalid = result["entries"][2]
        self.assertEqual(credentialed["summary"]["credential_live_verified"], "yes")
        self.assertEqual(funded["summary"]["funded_live_verified"], "yes")
        self.assertFalse(invalid["schema_validation"]["ok"])
        self.assertIn("non-empty string mode", " ".join(invalid["schema_validation"]["errors"]))
        for entry in result["entries"]:
            self.assertNotIn("payload", entry)

    def test_live_report_replay_imports_only_valid_reports_redacted(self) -> None:
        valid = json.loads((LIVE_REPORT_FIXTURE_ROOT / "valid_credentialed_read.json").read_text(encoding="utf-8"))
        valid["api_key"] = "replay-secret-api-key"
        with tempfile.TemporaryDirectory() as tmp:
            temp = Path(tmp)
            report_path = temp / "credentialed.json"
            report_path.write_text(json.dumps(valid), encoding="utf-8")
            store_path = temp / "store.json"
            result = replay_live_validation_report_paths(
                [report_path, LIVE_REPORT_FIXTURE_ROOT / "invalid_bad_stage_gates.json"],
                import_reports=True,
                store_path=store_path,
                label_prefix="replay",
            )

            self.assertFalse(result["ok"])
            self.assertEqual(result["mode"], "import")
            self.assertEqual(result["counts"]["valid"], 1)
            self.assertEqual(result["counts"]["invalid"], 1)
            self.assertEqual(result["counts"]["imported"], 1)
            self.assertTrue(result["entries"][0]["imported"])
            self.assertEqual(result["entries"][0]["stored"]["label"], "replay credentialed")
            self.assertEqual(result["entries"][0]["stored"]["provenance"]["source_file_name"], "credentialed.json")
            self.assertEqual(len(result["entries"][0]["payload_hash"]), 64)
            self.assertFalse(result["entries"][1]["imported"])
            disk = store_path.read_text(encoding="utf-8")
            self.assertNotIn("replay-secret-api-key", disk)
            self.assertIn("***", disk)
            listing = list_live_validation_reports(path=store_path)
            self.assertEqual(listing["counts"]["entries"], 1)

    def test_live_report_replay_detects_and_skips_duplicate_imports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp = Path(tmp)
            store_path = temp / "store.json"
            duplicate_result = replay_live_validation_report_paths(
                [
                    LIVE_REPORT_FIXTURE_ROOT / "valid_credentialed_read.json",
                    LIVE_REPORT_FIXTURE_ROOT / "valid_credentialed_read.json",
                ],
                import_reports=True,
                store_path=store_path,
            )

            self.assertTrue(duplicate_result["ok"])
            self.assertEqual(duplicate_result["counts"]["valid"], 2)
            self.assertEqual(duplicate_result["counts"]["imported"], 1)
            self.assertEqual(duplicate_result["counts"]["duplicates"], 1)
            self.assertEqual(duplicate_result["counts"]["skipped_duplicates"], 1)
            self.assertTrue(duplicate_result["entries"][1]["duplicate"])
            self.assertTrue(duplicate_result["entries"][1]["duplicate_skipped"])
            listing = list_live_validation_reports(path=store_path)
            self.assertEqual(listing["counts"]["entries"], 1)
            self.assertEqual(listing["counts"]["duplicate_imports"], 1)

            allow_store_path = temp / "allow-store.json"
            with patch("polymarket.live_reports.time.time_ns", return_value=1234567890):
                allowed_result = replay_live_validation_report_paths(
                    [
                        LIVE_REPORT_FIXTURE_ROOT / "valid_credentialed_read.json",
                        LIVE_REPORT_FIXTURE_ROOT / "valid_credentialed_read.json",
                    ],
                    import_reports=True,
                    store_path=allow_store_path,
                    allow_duplicate=True,
                )

            self.assertTrue(allowed_result["ok"])
            self.assertEqual(allowed_result["counts"]["imported"], 2)
            self.assertEqual(allowed_result["counts"]["duplicates"], 1)
            self.assertEqual(allowed_result["counts"]["skipped_duplicates"], 0)
            allow_listing = list_live_validation_reports(path=allow_store_path)
            self.assertEqual(allow_listing["counts"]["entries"], 2)
            self.assertEqual(allow_listing["counts"]["duplicate_payloads"], 1)
            self.assertEqual(len({entry["key"] for entry in allow_listing["entries"]}), 2)

    def test_live_report_replay_cli_outputs_structured_json_and_nonzero_for_invalid(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "replay_polymarket_live_reports.py"),
                "--json",
                str(LIVE_REPORT_FIXTURE_ROOT / "valid_dry_run.json"),
                str(LIVE_REPORT_FIXTURE_ROOT / "invalid_missing_mode.json"),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 1)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["counts"]["valid"], 1)
        self.assertEqual(payload["counts"]["invalid"], 1)
        self.assertEqual(payload["entries"][0]["summary"]["funded_live_verified"], "blocked")
        self.assertIn("strict_cli", payload["entries"][1]["schema_validation"]["accepted_modes"])

    def test_credential_runbook_inventories_env_and_never_exposes_funded_actions(self) -> None:
        env = {
            "POLY_ADDRESS": "0x" + "a" * 40,
            "POLY_API_KEY": "api-key-secret",
            "POLY_PASSPHRASE": "passphrase-secret",
            "POLY_SIGNATURE": "signature-secret",
            "POLY_TIMESTAMP": "123",
            "POLY_API_SECRET": "websocket-secret",
            "RELAYER_API_KEY": "relayer-secret",
            "RELAYER_API_KEY_ADDRESS": "0x" + "b" * 40,
            "PRIVATE_KEY": "0x" + "1" * 64,
            "SIGNATURE_TYPE": "0",
        }

        runbook = build_polymarket_credential_runbook(environ=env)

        self.assertEqual(runbook["mode"], "credential_runbook_no_funded_actions")
        self.assertEqual(runbook["network_calls"], "none")
        self.assertFalse(runbook["funded_execution_exposed"])
        self.assertFalse(runbook["safe_to_attempt_funded_order"])
        self.assertEqual(runbook["readiness"]["direct_l2_read_headers"]["status"], "ok")
        self.assertEqual(runbook["readiness"]["user_websocket_auth_payload"]["status"], "ok")
        self.assertEqual(runbook["readiness"]["relayer_headers"]["status"], "ok")
        self.assertIn("clob_l2_orders", runbook["readiness"]["credentialed_read_candidates"])
        self.assertIn("verify_polymarket_credentials.py --json", runbook["operator_commands"]["credential_inventory"])
        self.assertIn("--require-authenticated-read-ok", runbook["operator_commands"]["credentialed_read_no_funded_actions"])
        self.assertIn("--allow-funded-order", runbook["operator_commands"]["funded_order_cancel_requires_approval"])
        self.assertIn(CONFIRM_LIVE_ORDER_CANCEL, runbook["operator_commands"]["funded_order_cancel_requires_approval"])
        self.assertNotIn("api-key-secret", str(runbook))
        self.assertNotIn("websocket-secret", str(runbook))
        self.assertNotIn("1" * 64, str(runbook))

    def test_credential_runbook_blocks_missing_authenticated_read_inputs(self) -> None:
        runbook = build_polymarket_credential_runbook(environ={})

        self.assertFalse(runbook["readiness"]["non_destructive_auth_ready"])
        self.assertEqual(runbook["readiness"]["direct_l2_read_headers"]["status"], "blocked")
        self.assertEqual(runbook["readiness"]["user_websocket_auth_payload"]["status"], "blocked")
        self.assertIn("POLY_API_KEY", runbook["env_inventory"]["user_websocket_auth"]["requirements"][0]["missing"])
        self.assertIn("Do not attempt funded verification", " ".join(runbook["next_steps"]))

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
        self.assertIn("polymarket.credential_runbook", coverage["live_credential_validation"]["module"])
        self.assertIn("polymarket.live_reports", coverage["live_credential_validation"]["module"])
        self.assertIn("runbook_command", coverage["live_credential_validation"])
        self.assertIn("promotion_guard", coverage["live_credential_validation"])
        self.assertIn("credential_runbook", coverage["live_credential_validation"]["report_fields"])
        self.assertIn("verification_promotion", coverage["live_credential_validation"]["report_fields"])
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
