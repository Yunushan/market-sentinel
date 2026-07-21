from __future__ import annotations

import unittest

from market_adapters import (
    MARKET_CATALOG,
    MARKET_IDS,
    AdapterRegistry,
    AugurAdapter,
    AzuroAdapter,
    BetfairExchangeAdapter,
    CryptoComPredictAdapter,
    GeminiPredictionAdapter,
    KalshiAdapter,
    LimitlessAdapter,
    ManifoldAdapter,
    MarketAdapter,
    MarketCapabilities,
    MarketMetadata,
    PaperOrderRequest,
    PaperOrderResult,
    PredictItAdapter,
    PredictFunAdapter,
    OmenAdapter,
    SxBetAdapter,
    StubMarketAdapter,
    UnsupportedFeatureError,
    MetaculusAdapter,
    MyriadAdapter,
    OpinionAdapter,
    VERIFIED_BLOCKERS,
    VerifiedBlockedAdapter,
    XOMarketAdapter,
    ZeitgeistAdapter,
    build_default_registry,
    create_stub_adapter,
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

IMPLEMENTED_MARKETS = {
    "polymarket",
    "kalshi",
    "predictit",
    "manifold",
    "metaculus",
    "limitless_exchange",
    "sx_bet",
    "azuro",
    "augur",
    "omen",
    "zeitgeist",
    "myriad_markets",
    "xo_market",
    "opinion_labs",
    "gemini_titan",
    "predict_fun",
    "betfair_exchange",
    "crypto_com_predict",
}
VERIFIED_BLOCKED_MARKETS = set(VERIFIED_BLOCKERS)


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

    def test_default_registry_exposes_catalog_metadata_and_implemented_adapters(self) -> None:
        registry = build_default_registry()

        self.assertEqual(set(registry.list_market_ids()), set(MARKET_IDS))
        self.assertTrue(all(registry.has_adapter(market_id) for market_id in MARKET_IDS))
        self.assertEqual(registry.get_metadata("polymarket").display_name, "Polymarket")
        self.assertEqual(registry.create("polymarket").market_id, "polymarket")
        self.assertEqual(registry.get_metadata("kalshi").display_name, "Kalshi")
        self.assertIsInstance(registry.create("kalshi"), KalshiAdapter)
        self.assertEqual(registry.get_metadata("predictit").display_name, "PredictIt")
        self.assertIsInstance(registry.create("predictit"), PredictItAdapter)
        self.assertEqual(registry.get_metadata("crypto_com_predict").display_name, "Crypto.com Predict / CDNA")
        self.assertIsInstance(registry.create("crypto_com_predict"), CryptoComPredictAdapter)
        self.assertEqual(registry.get_metadata("manifold").display_name, "Manifold Markets")
        self.assertIsInstance(registry.create("manifold"), ManifoldAdapter)
        self.assertEqual(registry.get_metadata("metaculus").display_name, "Metaculus")
        self.assertIsInstance(registry.create("metaculus"), MetaculusAdapter)
        self.assertEqual(registry.get_metadata("limitless_exchange").display_name, "Limitless Exchange")
        self.assertIsInstance(registry.create("limitless_exchange"), LimitlessAdapter)
        self.assertEqual(registry.get_metadata("sx_bet").display_name, "SX Bet / SX Network")
        self.assertIsInstance(registry.create("sx_bet"), SxBetAdapter)
        self.assertEqual(registry.get_metadata("azuro").display_name, "Azuro")
        self.assertIsInstance(registry.create("azuro"), AzuroAdapter)
        self.assertEqual(registry.get_metadata("augur").display_name, "Augur")
        self.assertIsInstance(registry.create("augur"), AugurAdapter)
        self.assertEqual(registry.get_metadata("omen").display_name, "Omen")
        self.assertIsInstance(registry.create("omen"), OmenAdapter)
        self.assertEqual(registry.get_metadata("zeitgeist").display_name, "Zeitgeist")
        self.assertIsInstance(registry.create("zeitgeist"), ZeitgeistAdapter)
        self.assertEqual(registry.get_metadata("myriad_markets").display_name, "Myriad Markets")
        self.assertIsInstance(registry.create("myriad_markets"), MyriadAdapter)
        self.assertEqual(registry.get_metadata("xo_market").display_name, "XO Market")
        self.assertIsInstance(registry.create("xo_market"), XOMarketAdapter)
        self.assertEqual(registry.get_metadata("opinion_labs").display_name, "Opinion Labs")
        self.assertIsInstance(registry.create("opinion_labs"), OpinionAdapter)
        self.assertEqual(registry.get_metadata("gemini_titan").display_name, "Gemini Titan / Gemini Predictions")
        self.assertIsInstance(registry.create("gemini_titan"), GeminiPredictionAdapter)
        self.assertEqual(registry.get_metadata("predict_fun").display_name, "Predict.fun")
        self.assertIsInstance(registry.create("predict_fun"), PredictFunAdapter)
        self.assertEqual(registry.get_metadata("betfair_exchange").display_name, "Betfair Exchange")
        self.assertIsInstance(registry.create("betfair_exchange"), BetfairExchangeAdapter)

    def test_non_implemented_catalog_entries_create_stub_adapters(self) -> None:
        registry = build_default_registry()

        for market_id in MARKET_IDS:
            if market_id in IMPLEMENTED_MARKETS:
                continue
            adapter = registry.create(market_id)
            self.assertIsInstance(adapter, StubMarketAdapter)
            self.assertEqual(adapter.market_id, market_id)
            self.assertFalse(adapter.health_check()["ok"])

    def test_verified_blocked_markets_have_specific_health_and_errors(self) -> None:
        registry = build_default_registry()

        for market_id in VERIFIED_BLOCKED_MARKETS:
            with self.subTest(market_id=market_id):
                adapter = registry.create(market_id)
                health = adapter.health_check()

                self.assertIsInstance(adapter, VerifiedBlockedAdapter)
                self.assertTrue(health["stub"])
                self.assertTrue(health["verified_blocker"])
                self.assertEqual(health["last_reviewed"], "2026-05-26")
                self.assertGreaterEqual(len(health["references"]), 1)
                self.assertIn("Verified 2026-05-26", health["message"])

                with self.assertRaises(UnsupportedFeatureError) as ctx:
                    adapter.list_events()
                self.assertEqual(ctx.exception.market_id, market_id)
                self.assertEqual(ctx.exception.feature, "event_listing")
                self.assertIn("verified blocked", str(ctx.exception))

    def test_catalog_stub_markets_do_not_advertise_working_capabilities(self) -> None:
        registry = build_default_registry()

        for market_id in MARKET_IDS:
            metadata = registry.get_metadata(market_id)
            if market_id in IMPLEMENTED_MARKETS:
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
            if market_id in IMPLEMENTED_MARKETS:
                continue
            adapter = registry.create(market_id)

            operations = (
                ("event_listing", lambda adapter=adapter: adapter.list_events()),
                ("event_listing", lambda adapter=adapter: adapter.list_contracts("event-1")),
                ("price_reading", lambda adapter=adapter: adapter.get_price("contract-1")),
                ("orderbook_reading", lambda adapter=adapter: adapter.get_orderbook("contract-1")),
                ("paper_trading", lambda adapter=adapter: adapter.place_paper_order(order)),
                ("live_trading", lambda adapter=adapter: adapter.place_live_order(order)),
                ("copy_trading", lambda adapter=adapter: adapter.copy_trade_from_activity({})),
            )

            for feature, operation in operations:
                with self.subTest(market_id=market_id, feature=feature):
                    with self.assertRaises(UnsupportedFeatureError) as ctx:
                        operation()
                    self.assertEqual(ctx.exception.market_id, market_id)
                    self.assertEqual(ctx.exception.feature, feature)

    def test_stub_adapter_raises_market_specific_unsupported_errors(self) -> None:
        adapter = create_stub_adapter(
            MarketMetadata(market_id="custom_stub", display_name="Custom Stub")
        )

        with self.assertRaises(UnsupportedFeatureError) as ctx:
            adapter.get_price("contract-1")

        self.assertEqual(ctx.exception.market_id, "custom_stub")
        self.assertEqual(ctx.exception.feature, "price_reading")
        self.assertIn("Custom Stub", str(ctx.exception))
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
