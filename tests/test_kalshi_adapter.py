from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from market_adapters import KalshiAdapter, PaperOrderRequest
from market_adapters.errors import MarketConfigurationError


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "kalshi"


def load_fixture(name: str):
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


class KalshiAdapterTests(unittest.TestCase):
    def make_adapter(self) -> KalshiAdapter:
        adapter = KalshiAdapter()
        markets = load_fixture("markets")
        orderbook = load_fixture("orderbook")

        def fake_get_json(url: str, *, params=None, headers=None):
            if url.endswith("/markets"):
                event_ticker = (params or {}).get("event_ticker")
                if event_ticker:
                    filtered = [
                        market
                        for market in markets["markets"]
                        if market.get("event_ticker") == event_ticker
                    ]
                    return {"markets": filtered, "cursor": ""}
                return markets
            if url.endswith("/markets/KXFED-26MAY-TARGET-425"):
                return {"market": markets["markets"][0]}
            if url.endswith("/markets/KXFED-26MAY-TARGET-425/orderbook"):
                return orderbook
            raise AssertionError(f"unexpected Kalshi URL: {url}")

        adapter.runtime.get_json = fake_get_json  # type: ignore[method-assign]
        return adapter

    def test_registered_metadata_advertises_supported_kalshi_features(self) -> None:
        adapter = KalshiAdapter()
        health = adapter.health_check()

        self.assertTrue(health["ok"])
        self.assertEqual(adapter.market_id, "kalshi")
        self.assertTrue(adapter.capabilities.event_listing)
        self.assertTrue(adapter.capabilities.price_reading)
        self.assertTrue(adapter.capabilities.orderbook_reading)
        self.assertTrue(adapter.capabilities.paper_trading)
        self.assertTrue(adapter.capabilities.live_trading)
        self.assertFalse(adapter.capabilities.copy_trading)
        self.assertIn("external-api.kalshi.com", health["api_base_url"])

    def test_list_events_groups_markets_by_event_and_filters_query(self) -> None:
        adapter = self.make_adapter()

        events = adapter.list_events("fed", limit=10)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event_id, "KXFED-26MAY")
        self.assertEqual(events[0].market_id, "kalshi")
        self.assertEqual(events[0].status, "active")
        self.assertEqual(len(events[0].raw["markets"]), 2)

    def test_list_contracts_creates_yes_and_no_contracts(self) -> None:
        adapter = self.make_adapter()

        contracts = adapter.list_contracts("KXFED-26MAY")

        self.assertEqual(len(contracts), 4)
        self.assertEqual(contracts[0].contract_id, "KXFED-26MAY-TARGET-425:YES")
        self.assertEqual(contracts[1].contract_id, "KXFED-26MAY-TARGET-425:NO")
        self.assertEqual(contracts[0].outcome, "Yes")
        self.assertEqual(contracts[1].outcome, "No")

    def test_orderbook_converts_opposite_side_bids_to_asks(self) -> None:
        adapter = self.make_adapter()

        book = adapter.get_orderbook("KXFED-26MAY-TARGET-425:YES")

        self.assertEqual([level.price for level in book.bids], [0.41, 0.39])
        self.assertEqual([level.price for level in book.asks], [0.42, 0.44])
        self.assertEqual([level.size for level in book.asks], [2.0, 7.0])

    def test_no_side_orderbook_and_price_are_supported(self) -> None:
        adapter = self.make_adapter()

        book = adapter.get_orderbook("KXFED-26MAY-TARGET-425:NO")
        price = adapter.get_price("KXFED-26MAY-TARGET-425:NO")

        self.assertEqual([level.price for level in book.bids], [0.58, 0.56])
        self.assertEqual([level.price for level in book.asks], [0.59, 0.61])
        self.assertAlmostEqual(price.bid or 0, 0.58)
        self.assertAlmostEqual(price.ask or 0, 0.59)
        self.assertAlmostEqual(price.midpoint or 0, 0.585)

    def test_paper_order_is_dry_run_and_validates_input(self) -> None:
        adapter = self.make_adapter()
        result = adapter.place_paper_order(
            PaperOrderRequest(
                market_id="kalshi",
                contract_id="KXFED-26MAY-TARGET-425:YES",
                side="BUY",
                size=3,
                limit_price=0.42,
            )
        )

        self.assertTrue(result.accepted)
        self.assertIn("DRY RUN", result.message)
        self.assertEqual(result.contract_id, "KXFED-26MAY-TARGET-425:YES")

        with self.assertRaises(MarketConfigurationError):
            adapter.place_paper_order(
                PaperOrderRequest(
                    market_id="kalshi",
                    contract_id="KXFED-26MAY-TARGET-425:MAYBE",
                    side="BUY",
                    size=3,
                    limit_price=0.42,
                )
            )

    def test_live_trading_is_disabled_by_default(self) -> None:
        adapter = self.make_adapter()

        with self.assertRaises(MarketConfigurationError) as ctx:
            adapter.place_live_order(
                PaperOrderRequest(
                    market_id="kalshi",
                    contract_id="KXFED-26MAY-TARGET-425:YES",
                    side="BUY",
                    size=3,
                    limit_price=0.42,
                )
            )

        self.assertIn("disabled", str(ctx.exception))

    def test_live_order_payload_maps_no_contract_to_yes_side_book(self) -> None:
        adapter = self.make_adapter()
        payload = adapter._build_live_order_payload(
            PaperOrderRequest(
                market_id="kalshi",
                contract_id="KXFED-26MAY-TARGET-425:NO",
                side="BUY",
                size=2,
                limit_price=0.35,
                metadata={"client_order_id": "client-1"},
            )
        )

        self.assertEqual(payload["ticker"], "KXFED-26MAY-TARGET-425")
        self.assertEqual(payload["client_order_id"], "client-1")
        self.assertEqual(payload["side"], "ask")
        self.assertEqual(payload["count"], "2.00")
        self.assertEqual(payload["price"], "0.6500")

    def test_live_trading_requires_credentials_when_enabled(self) -> None:
        adapter = KalshiAdapter({"live_trading_enabled": True})

        with self.assertRaises(MarketConfigurationError) as ctx:
            adapter.place_live_order(
                PaperOrderRequest(
                    market_id="kalshi",
                    contract_id="KXFED-26MAY-TARGET-425:YES",
                    side="BUY",
                    size=1,
                    limit_price=0.5,
                )
            )

        self.assertIn("KALSHI_API_KEY_ID", str(ctx.exception))

    def test_live_order_signs_and_posts_with_generated_credentials(self) -> None:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode("utf-8")
        adapter = KalshiAdapter({"live_trading_enabled": True})
        calls = []

        def fake_request_json(method: str, url: str, *, params=None, json_body=None, headers=None):
            calls.append((method, url, json_body, headers))
            return {"order_id": "order-1", "remaining_count": "1.00", "fill_count": "0.00"}

        adapter.runtime.request_json = fake_request_json  # type: ignore[method-assign]

        with patch.dict(
            "os.environ",
            {"KALSHI_API_KEY_ID": "unit-test-key-id", "KALSHI_PRIVATE_KEY_PEM": pem},
        ):
            result = adapter.place_live_order(
                PaperOrderRequest(
                    market_id="kalshi",
                    contract_id="KXFED-26MAY-TARGET-425:YES",
                    side="BUY",
                    size=1,
                    limit_price=0.5,
                    metadata={"client_order_id": "client-1"},
                )
            )

        self.assertEqual(result["response"]["order_id"], "order-1")
        method, url, payload, headers = calls[0]
        self.assertEqual(method, "POST")
        self.assertTrue(url.endswith("/portfolio/events/orders"))
        self.assertEqual(payload["side"], "bid")
        self.assertEqual(payload["price"], "0.5000")
        self.assertEqual(headers["KALSHI-ACCESS-KEY"], "unit-test-key-id")
        self.assertTrue(headers["KALSHI-ACCESS-SIGNATURE"])
        self.assertTrue(headers["KALSHI-ACCESS-TIMESTAMP"].isdigit())


if __name__ == "__main__":
    unittest.main()
