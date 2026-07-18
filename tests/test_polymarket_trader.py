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


class _InitializableClient(_Client):
    instances: list["_InitializableClient"] = []

    def __init__(self, *args, **kwargs) -> None:
        super().__init__()
        self.init_args = args
        self.init_kwargs = kwargs
        self.api_credentials = None
        self.__class__.instances.append(self)

    def create_or_derive_api_creds(self):
        return {"key": "derived"}

    def set_api_creds(self, credentials):
        self.api_credentials = credentials


class _CompatibilityClient:
    def get_order_by_id(self, order_id):
        return {"id": order_id}

    def get_orders(self, **filters):
        return filters

    def cancel_order(self, order_id):
        return {"cancelled": order_id}

    def cancel_multiple_orders(self, order_ids):
        return {"cancelled": order_ids}

    def cancel_all_orders(self):
        return {"cancelled": "all"}

    def cancel_market_orders(self, condition_id):
        return {"cancelled": condition_id}

    def post_multiple_orders(self, orders, order_type):
        return {"orders": orders, "type": order_type}

    def get_trades(self, **filters):
        return filters

    def get_order_status(self, order_id):
        return {"status": order_id}

    def heartbeat(self):
        return {"heartbeat": "ok"}

    def get_builder_trades(self, **filters):
        return filters


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

    def test_initialization_derives_and_sets_l2_credentials(self) -> None:
        _InitializableClient.instances.clear()
        config = trader_module.TraderConfig(
            private_key="0x" + "1" * 64,
            funder_address="0x" + "2" * 40,
            signature_type=1,
            chain_id=137,
            host="https://example.invalid",
        )
        readiness = {"ok": True}
        with (
            patch.object(trader_module, "ClobClient", _InitializableClient),
            patch.object(trader_module, "validate_sdk_trading_readiness", return_value=readiness) as validate,
        ):
            instance = trader_module.PolymarketTrader(config)

        client = _InitializableClient.instances[-1]
        self.assertIs(instance.client, client)
        self.assertIs(instance.auth_readiness, readiness)
        validate.assert_called_once_with(
            private_key=config.private_key,
            signature_type=1,
            funder_address=config.funder_address,
            chain_id=137,
            host=config.host,
        )
        self.assertEqual(client.init_args, (config.host,))
        self.assertEqual(client.init_kwargs["funder"], config.funder_address)
        self.assertEqual(client.api_credentials, {"key": "derived"})

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

    def test_compatibility_wrappers_use_alternate_sdk_method_names(self) -> None:
        instance, _ = self._trader()
        instance.client = _CompatibilityClient()
        with patch.object(trader_module, "OrderType", _OrderType):
            self.assertEqual(instance.get_order("order-1"), {"id": "order-1"})
            self.assertEqual(instance.get_orders(market="market-1"), {"market": "market-1"})
            self.assertEqual(instance.cancel_order("order-1"), {"cancelled": "order-1"})
            self.assertEqual(instance.cancel_orders(["one", "", "two"]), {"cancelled": ["one", "two"]})
            self.assertEqual(instance.cancel_all_orders(), {"cancelled": "all"})
            self.assertEqual(instance.cancel_market_orders("condition-1"), {"cancelled": "condition-1"})
            self.assertEqual(
                instance.place_multiple_orders((item for item in ("signed-one", "signed-two")), tif="GTC"),
                {"orders": ["signed-one", "signed-two"], "type": "GTC"},
            )
            self.assertEqual(instance.get_trades(asset_id="asset-1"), {"asset_id": "asset-1"})
            self.assertEqual(instance.get_order_scoring_status("order-1"), {"status": "order-1"})
            self.assertEqual(instance.send_heartbeat(), {"heartbeat": "ok"})
            self.assertEqual(
                instance.get_builder_trades("builder-1", market="market-1"),
                {"builder_code": "builder-1", "market": "market-1"},
            )


if __name__ == "__main__":
    unittest.main()
