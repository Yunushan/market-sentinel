from __future__ import annotations

import unittest

from market_adapters import (
    MARKET_CATALOG,
    MARKET_IDS,
    AdapterRegistry,
    MarketAdapter,
    MarketCapabilities,
    MarketMetadata,
    PaperOrderRequest,
    PaperOrderResult,
    StubMarketAdapter,
    UnsupportedFeatureError,
    build_default_registry,
)
from market_adapters.errors import MarketConfigurationError


CAPABILITY_KEYS = {
    "market_discovery",
    "event_listing",
    "price_reading",
    "orderbook_reading",
    "alerts",
    "paper_trading",
    "live_trading",
    "copy_trading",
    "api_required",
    "credentials_required",
    "kyc_required",
    "region_limited",
}


class DummyAdapter(MarketAdapter):
    metadata = MarketMetadata(
        market_id="dummy",
        display_name="Dummy",
        capabilities=MarketCapabilities(event_listing=True, paper_trading=True),
    )

    def list_events(self, query: str = "", limit: int = 50):
        self.ensure_capability("event_listing")
        return []

    def place_paper_order(self, order: PaperOrderRequest) -> PaperOrderResult:
        self.ensure_capability("paper_trading")
        return PaperOrderResult(
            market_id=order.market_id,
            contract_id=order.contract_id,
            accepted=True,
            message="accepted",
            filled_size=order.size,
            average_price=order.limit_price,
        )


class AdapterFoundationTests(unittest.TestCase):
    def test_catalog_contains_goal_markets_with_unique_ids(self) -> None:
        self.assertEqual(len(MARKET_IDS), len(set(MARKET_IDS)))
        self.assertEqual(len(MARKET_CATALOG), 41)
        self.assertIn("polymarket", MARKET_IDS)
        self.assertIn("kalshi", MARKET_IDS)
        self.assertIn("limitless_exchange", MARKET_IDS)
        self.assertIn("fanatics_markets", MARKET_IDS)
        self.assertIn("hyperliquid", MARKET_IDS)
        self.assertIn("betfair_exchange", MARKET_IDS)
        self.assertIn("underdog_sports", MARKET_IDS)

    def test_default_registry_exposes_catalog_metadata_and_polymarket_adapter(self) -> None:
        registry = build_default_registry()

        self.assertEqual(set(registry.list_market_ids()), set(MARKET_IDS))
        self.assertTrue(all(registry.has_adapter(market_id) for market_id in MARKET_IDS))
        self.assertEqual(registry.get_metadata("polymarket").display_name, "Polymarket")
        self.assertEqual(registry.create("polymarket").market_id, "polymarket")

    def test_non_polymarket_catalog_entries_create_stub_adapters(self) -> None:
        registry = build_default_registry()

        for market_id in MARKET_IDS:
            if market_id == "polymarket":
                continue
            adapter = registry.create(market_id)
            self.assertIsInstance(adapter, StubMarketAdapter)
            self.assertEqual(adapter.market_id, market_id)
            self.assertFalse(adapter.health_check()["ok"])

    def test_catalog_stub_markets_do_not_advertise_working_capabilities(self) -> None:
        registry = build_default_registry()

        for market_id in MARKET_IDS:
            metadata = registry.get_metadata(market_id)
            if market_id == "polymarket":
                self.assertTrue(any(metadata.capabilities.to_dict().values()))
                continue
            self.assertEqual(metadata.capabilities.to_dict(), {key: False for key in CAPABILITY_KEYS})

    def test_all_default_adapters_satisfy_basic_contract(self) -> None:
        registry = build_default_registry()

        for market_id in MARKET_IDS:
            adapter = registry.create(market_id)
            health = adapter.health_check()

            self.assertEqual(adapter.metadata.market_id, market_id)
            self.assertEqual(adapter.market_id, market_id)
            self.assertTrue(adapter.display_name)
            self.assertEqual(set(adapter.capabilities.to_dict()), CAPABILITY_KEYS)
            self.assertEqual(health["market_id"], market_id)
            self.assertIn("ok", health)
            self.assertIn("message", health)

    def test_stub_adapters_reject_all_operational_methods(self) -> None:
        registry = build_default_registry()
        order = PaperOrderRequest(
            market_id="stub-market",
            contract_id="contract-1",
            side="BUY",
            size=1.0,
            limit_price=0.5,
        )

        for market_id in MARKET_IDS:
            if market_id == "polymarket":
                continue
            adapter = registry.create(market_id)

            operations = (
                ("event_listing", lambda: adapter.list_events()),
                ("event_listing", lambda: adapter.list_contracts("event-1")),
                ("price_reading", lambda: adapter.get_price("contract-1")),
                ("orderbook_reading", lambda: adapter.get_orderbook("contract-1")),
                ("paper_trading", lambda: adapter.place_paper_order(order)),
                ("live_trading", lambda: adapter.place_live_order(order)),
                ("copy_trading", lambda: adapter.copy_trade_from_activity({})),
            )

            for feature, operation in operations:
                with self.subTest(market_id=market_id, feature=feature):
                    with self.assertRaises(UnsupportedFeatureError) as ctx:
                        operation()
                    self.assertEqual(ctx.exception.market_id, market_id)
                    self.assertEqual(ctx.exception.feature, feature)

    def test_stub_adapter_raises_market_specific_unsupported_errors(self) -> None:
        registry = build_default_registry()
        adapter = registry.create("kalshi")

        with self.assertRaises(UnsupportedFeatureError) as ctx:
            adapter.get_price("contract-1")

        self.assertEqual(ctx.exception.market_id, "kalshi")
        self.assertEqual(ctx.exception.feature, "price_reading")
        self.assertIn("Kalshi", str(ctx.exception))
        self.assertIn("official adapter", str(ctx.exception))
        self.assertIn("not been implemented", str(ctx.exception))

    def test_registry_registers_adapter_and_creates_configured_instance(self) -> None:
        registry = AdapterRegistry()
        registry.register_adapter(DummyAdapter)

        adapter = registry.create("dummy", {"enabled": True})
        result = adapter.place_paper_order(
            PaperOrderRequest(
                market_id="dummy",
                contract_id="contract-1",
                side="BUY",
                size=3.0,
                limit_price=0.42,
            )
        )

        self.assertTrue(registry.has_adapter("dummy"))
        self.assertEqual(adapter.config, {"enabled": True})
        self.assertTrue(result.accepted)
        self.assertEqual(result.filled_size, 3.0)
        self.assertEqual(result.average_price, 0.42)

    def test_registry_rejects_duplicate_adapter_registration(self) -> None:
        registry = AdapterRegistry()
        registry.register_adapter(DummyAdapter)

        with self.assertRaises(MarketConfigurationError):
            registry.register_adapter(DummyAdapter)

    def test_unsupported_feature_error_is_clear(self) -> None:
        adapter = MarketAdapter()

        with self.assertRaises(UnsupportedFeatureError) as ctx:
            adapter.get_price("contract-1")

        self.assertEqual(ctx.exception.market_id, "base")
        self.assertEqual(ctx.exception.feature, "price_reading")
        self.assertIn("price_reading", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
