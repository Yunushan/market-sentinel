from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from market_adapters import (
    AdapterRuntime,
    MarketAdapter,
    MarketCapabilities,
    MarketHTTPError,
    MarketMetadata,
    PaperOrderRequest,
    RateLimiter,
)
from market_adapters.errors import MarketConfigurationError
from market_adapters.runtime import DEFAULT_USER_AGENT, load_market_fixture


class FakeResponse:
    def __init__(self, status_code: int = 200, payload=None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload if payload is not None else {"ok": True}
        self.text = text

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.calls = []

    def request(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.response


class LiveAdapter(MarketAdapter):
    metadata = MarketMetadata(
        market_id="live_dummy",
        display_name="Live Dummy",
        capabilities=MarketCapabilities(
            live_trading=True,
            paper_trading=True,
            credentials_required=True,
            kyc_required=True,
            region_limited=True,
        ),
    )


class AdapterRuntimeTests(unittest.TestCase):
    def test_http_runtime_adds_headers_timeout_and_params(self) -> None:
        session = FakeSession(FakeResponse(payload={"markets": []}))
        runtime = AdapterRuntime("dummy", {"http_timeout_seconds": 3}, session=session)

        data = runtime.get_json("https://example.test/markets", params={"q": "test"})

        self.assertEqual(data, {"markets": []})
        args, kwargs = session.calls[0]
        self.assertEqual(args, ("GET", "https://example.test/markets"))
        self.assertEqual(kwargs["params"], {"q": "test"})
        self.assertEqual(kwargs["timeout"], 3.0)
        self.assertEqual(kwargs["headers"]["User-Agent"], DEFAULT_USER_AGENT)
        self.assertEqual(kwargs["headers"]["Accept"], "application/json")

    def test_http_runtime_raises_market_http_error_for_bad_status(self) -> None:
        session = FakeSession(FakeResponse(status_code=429, text="rate limited"))
        runtime = AdapterRuntime("dummy", session=session)

        with self.assertRaises(MarketHTTPError) as ctx:
            runtime.get_json("https://example.test/markets")

        self.assertIn("HTTP 429", str(ctx.exception))
        self.assertIn("rate limited", str(ctx.exception))

    def test_rate_limiter_uses_configured_delay_without_real_sleep(self) -> None:
        clock_values = [0.0, 0.25, 0.25]
        sleeps = []
        limiter = RateLimiter(
            1.0,
            clock=lambda: clock_values.pop(0),
            sleeper=lambda seconds: sleeps.append(seconds),
        )

        first_delay = limiter.wait()
        second_delay = limiter.wait()

        self.assertEqual(first_delay, 0.0)
        self.assertEqual(second_delay, 0.75)
        self.assertEqual(sleeps, [0.75])

    def test_runtime_resolves_credentials_from_config_without_logging_secret(self) -> None:
        runtime = AdapterRuntime("dummy", {"api_key": "secret-value"})

        credential = runtime.resolve_credential("api_key", ("DUMMY_API_KEY",), required=True)

        self.assertIsNotNone(credential)
        self.assertEqual(credential.value, "secret-value")
        self.assertEqual(credential.source, "config:api_key")
        self.assertEqual(credential.redacted, "***")
        self.assertNotIn("secret-value", str(runtime.describe()))

    def test_runtime_resolves_credentials_from_environment(self) -> None:
        runtime = AdapterRuntime("dummy")

        with patch.dict(os.environ, {"DUMMY_API_KEY": "from-env"}):
            credential = runtime.resolve_credential("api_key", ("DUMMY_API_KEY",), required=True)

        self.assertIsNotNone(credential)
        self.assertEqual(credential.value, "from-env")
        self.assertEqual(credential.source, "env:DUMMY_API_KEY")

    def test_runtime_missing_required_credential_is_clear(self) -> None:
        runtime = AdapterRuntime("dummy")

        with self.assertRaises(MarketConfigurationError) as ctx:
            runtime.resolve_credential("api_key", ("DUMMY_API_KEY",), required=True)

        self.assertIn("Missing required credential", str(ctx.exception))
        self.assertIn("DUMMY_API_KEY", str(ctx.exception))

    def test_market_fixture_loader_reads_offline_json(self) -> None:
        fixture = load_market_fixture("polymarket", "market")

        self.assertEqual(fixture["id"], "market-1")
        self.assertIn("clobTokenIds", fixture)

    def test_base_adapter_health_includes_runtime_metadata(self) -> None:
        adapter = MarketAdapter({"http_timeout_seconds": 2, "min_request_interval_seconds": 0.5})
        health = adapter.health_check()

        self.assertTrue(health["ok"])
        self.assertEqual(health["adapter"], "MarketAdapter")
        self.assertEqual(health["runtime"]["timeout_seconds"], 2.0)
        self.assertEqual(health["runtime"]["min_request_interval_seconds"], 0.5)
        self.assertIn("capabilities", health)

    def test_base_adapter_live_gate_is_disabled_by_default(self) -> None:
        adapter = MarketAdapter()

        with self.assertRaises(MarketConfigurationError):
            adapter.ensure_live_trading_enabled()

        enabled_adapter = MarketAdapter({"live_trading_enabled": "true", "live_trading_confirmed": "true"})
        enabled_adapter.ensure_live_trading_enabled()

    def test_live_preflight_requires_acknowledgement_and_honors_kill_switch(self) -> None:
        order = PaperOrderRequest("live_dummy", "contract-1", "BUY", 2.0, 0.4)

        with self.assertRaises(MarketConfigurationError) as ack_ctx:
            LiveAdapter({"live_trading_enabled": True}).preflight_live_order(order)
        self.assertIn("acknowledgement", str(ack_ctx.exception))

        with self.assertRaises(MarketConfigurationError) as kill_ctx:
            LiveAdapter(
                {
                    "live_trading_enabled": True,
                    "live_trading_confirmed": True,
                    "live_trading_kill_switch": True,
                }
            ).preflight_live_order(order)
        self.assertIn("kill switch", str(kill_ctx.exception))

    def test_live_preflight_applies_size_notional_caps_and_returns_redacted_audit(self) -> None:
        adapter = LiveAdapter(
            {
                "live_trading_enabled": True,
                "live_trading_confirmed": True,
                "live_trading_max_size": 10,
                "live_trading_max_notional": 5,
            }
        )

        preflight = adapter.preflight_live_order(
            PaperOrderRequest(
                "live_dummy",
                "contract-1",
                "BUY",
                4.0,
                0.5,
                {"private_key": "secret", "client_order_id": "client-1"},
            )
        )

        self.assertEqual(preflight["market_id"], "live_dummy")
        self.assertEqual(preflight["approx_notional"], 2.0)
        self.assertEqual(preflight["metadata_keys"], ["client_order_id", "private_key"])
        self.assertIn("credentials_required", preflight["warnings"])
        self.assertIn("kyc_required", preflight["warnings"])
        self.assertIn("region_limited", preflight["warnings"])
        self.assertNotIn("secret", str(preflight))

        with self.assertRaises(MarketConfigurationError) as size_ctx:
            adapter.preflight_live_order(PaperOrderRequest("live_dummy", "contract-1", "BUY", 11.0, 0.4))
        self.assertIn("size", str(size_ctx.exception))

        with self.assertRaises(MarketConfigurationError) as notional_ctx:
            adapter.preflight_live_order(PaperOrderRequest("live_dummy", "contract-1", "BUY", 4.0, 2.0))
        self.assertIn("notional", str(notional_ctx.exception))

    def test_base_adapter_order_market_gate(self) -> None:
        adapter = MarketAdapter()

        with self.assertRaises(MarketConfigurationError):
            adapter.ensure_order_market(
                PaperOrderRequest(
                    market_id="other",
                    contract_id="contract-1",
                    side="BUY",
                    size=1.0,
                )
            )


if __name__ == "__main__":
    unittest.main()
