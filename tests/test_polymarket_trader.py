from __future__ import annotations

import unittest
from unittest.mock import patch

from polymarket import trader as trader_module


class _OrderArgs:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class _MarketOrderArgs:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class _OrderType:
    FOK = "FOK"
    GTC = "GTC"


class _Client:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def create_order(self, order):
        self.calls.append(("create_order", order))
        return {"signed": "limit"}

    def create_market_order(self, order):
        self.calls.append(("create_market_order", order))
        return {"signed": "market"}

    def post_order(self, order, order_type):
        self.calls.append(("post_order", (order, order_type)))
        return {"orderID": "order-1"}


class PolymarketTraderTests(unittest.TestCase):
    def _trader(self) -> tuple[trader_module.PolymarketTrader, _Client]:
        client = _Client()
        instance = object.__new__(trader_module.PolymarketTrader)
        instance.client = client
        return instance, client

    def test_limit_order_rejects_unknown_side_before_client_call(self) -> None:
        instance, client = self._trader()

        with self.assertRaisesRegex(ValueError, "must be BUY or SELL"):
            instance.place_limit_order(token_id="token", side="hold", price=0.5, size=1)

        self.assertEqual(client.calls, [])

    def test_market_order_rejects_empty_side_before_client_call(self) -> None:
        instance, client = self._trader()

        with self.assertRaisesRegex(ValueError, "must be BUY or SELL"):
            instance.place_market_order_amount(token_id="token", side="", amount=1)

        self.assertEqual(client.calls, [])

    def test_limit_order_normalizes_valid_buy_side(self) -> None:
        instance, client = self._trader()
        with patch.multiple(
            trader_module,
            OrderArgs=_OrderArgs,
            OrderType=_OrderType,
            BUY="sdk-buy",
            SELL="sdk-sell",
        ):
            response = instance.place_limit_order(token_id="token", side=" buy ", price=0.42, size=3, tif="GTC")

        self.assertEqual(response, {"orderID": "order-1"})
        self.assertEqual(client.calls[0][0], "create_order")
        self.assertEqual(client.calls[0][1].kwargs["side"], "sdk-buy")
        self.assertEqual(client.calls[1], ("post_order", ({"signed": "limit"}, "GTC")))

    def test_market_order_normalizes_valid_sell_side(self) -> None:
        instance, client = self._trader()
        with patch.multiple(
            trader_module,
            MarketOrderArgs=_MarketOrderArgs,
            OrderType=_OrderType,
            BUY="sdk-buy",
            SELL="sdk-sell",
        ):
            response = instance.place_market_order_amount(token_id="token", side="SELL", amount=7, tif="GTC")

        self.assertEqual(response, {"orderID": "order-1"})
        self.assertEqual(client.calls[0][0], "create_market_order")
        self.assertEqual(client.calls[0][1].kwargs["side"], "sdk-sell")
        self.assertEqual(client.calls[0][1].kwargs["order_type"], "GTC")

    def test_call_client_falls_back_after_signature_mismatch(self) -> None:
        instance, _ = self._trader()

        class FallbackClient:
            @staticmethod
            def first(_value, _extra):
                raise AssertionError("first method should not be called with a matching signature")

            @staticmethod
            def second(value):
                return {"value": value}

        instance.client = FallbackClient()
        self.assertEqual(instance._call_client(("first", "second"), "ok"), {"value": "ok"})

    def test_call_client_rejects_missing_methods(self) -> None:
        instance, _ = self._trader()
        instance.client = object()

        with self.assertRaisesRegex(RuntimeError, "does not expose"):
            instance._call_client(("missing_one", "missing_two"))


if __name__ == "__main__":
    unittest.main()
