from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional, Sequence

from .auth_readiness import validate_sdk_trading_readiness
from .constants import CLOB_API

try:
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import OrderArgs, OrderType, MarketOrderArgs
    from py_clob_client.order_builder.constants import BUY, SELL
except Exception:  # pragma: no cover
    ClobClient = None  # type: ignore
    OrderArgs = None  # type: ignore
    OrderType = None  # type: ignore
    MarketOrderArgs = None  # type: ignore
    BUY = "BUY"  # type: ignore
    SELL = "SELL"  # type: ignore


@dataclass
class TraderConfig:
    private_key: str
    funder_address: Optional[str] = None
    signature_type: int = 0
    chain_id: int = 137
    host: str = CLOB_API


def _order_side(side: str) -> Any:
    normalized = str(side or "").strip().upper()
    if normalized == "BUY":
        return BUY
    if normalized == "SELL":
        return SELL
    raise ValueError("Polymarket order side must be BUY or SELL.")


class PolymarketTrader:
    """
    Thin wrapper around Polymarket's official py-clob-client.

    It expects a private key that can sign for your Polymarket account.
    Some Polymarket accounts use a proxy wallet that actually holds funds; in that case,
    you also pass funder_address + signature_type.
    """

    def __init__(self, cfg: TraderConfig):
        if ClobClient is None:
            raise RuntimeError("py-clob-client is not installed. Install requirements.txt first.")
        self.cfg = cfg
        self.auth_readiness = validate_sdk_trading_readiness(
            private_key=self.cfg.private_key,
            signature_type=self.cfg.signature_type,
            funder_address=self.cfg.funder_address,
            chain_id=self.cfg.chain_id,
            host=self.cfg.host,
        )
        self.client = self._init_client()

    def _init_client(self):
        client = ClobClient(
            self.cfg.host,
            key=self.cfg.private_key,
            chain_id=self.cfg.chain_id,
            signature_type=self.cfg.signature_type,
            funder=self.cfg.funder_address,
        )

        # Create/derive API credentials (L2)
        creds = None
        for fn_name in ("create_or_derive_api_creds", "create_or_derive_api_key", "derive_api_key"):
            fn = getattr(client, fn_name, None)
            if callable(fn):
                creds = fn()
                break
        if creds is None:
            raise RuntimeError("Unable to derive API credentials with this client version.")

        # Some versions return dict, some return tuple-like; assume dict-ish.
        try:
            client.set_api_creds(creds)
        except Exception:
            # Try normalize
            if isinstance(creds, dict):
                client.set_api_creds(creds)
            else:
                raise

        return client

    def place_limit_order(
        self,
        *,
        token_id: str,
        side: str,
        price: float,
        size: float,
        tif: str = "FOK",
    ) -> Dict[str, Any]:
        if OrderArgs is None or OrderType is None:
            raise RuntimeError("py-clob-client missing order types.")
        side_const = _order_side(side)
        order = OrderArgs(token_id=token_id, price=float(price), size=float(size), side=side_const)
        signed = self.client.create_order(order)

        order_type = getattr(OrderType, tif, None) or OrderType.FOK
        return self.client.post_order(signed, order_type)

    def place_market_order_amount(
        self,
        *,
        token_id: str,
        side: str,
        amount: float,
        tif: str = "FOK",
    ) -> Dict[str, Any]:
        if MarketOrderArgs is None or OrderType is None:
            raise RuntimeError("py-clob-client missing market order types.")
        side_const = _order_side(side)
        mo = MarketOrderArgs(token_id=token_id, amount=float(amount), side=side_const, order_type=getattr(OrderType, tif, OrderType.FOK))
        signed = self.client.create_market_order(mo)
        order_type = getattr(OrderType, tif, None) or OrderType.FOK
        return self.client.post_order(signed, order_type)

    def _call_client(self, method_names: Sequence[str], *args: Any, **kwargs: Any) -> Any:
        last_error: Optional[Exception] = None
        for method_name in method_names:
            fn = getattr(self.client, method_name, None)
            if not callable(fn):
                continue
            try:
                return fn(*args, **kwargs)
            except TypeError as exc:
                last_error = exc
                continue
        names = ", ".join(method_names)
        if last_error is not None:
            raise RuntimeError(f"Polymarket client method signature mismatch for: {names}") from last_error
        raise RuntimeError(f"Polymarket client does not expose any of: {names}")

    def get_order(self, order_id: str) -> Dict[str, Any]:
        return self._call_client(("get_order", "get_order_by_id"), order_id)

    def get_orders(self, **filters: Any) -> Any:
        return self._call_client(("get_orders",), **filters)

    def cancel_order(self, order_id: str) -> Dict[str, Any]:
        return self._call_client(("cancel", "cancel_order"), order_id)

    def cancel_orders(self, order_ids: Iterable[str]) -> Dict[str, Any]:
        ids = [str(order_id) for order_id in order_ids if str(order_id)]
        return self._call_client(("cancel_orders", "cancel_multiple_orders"), ids)

    def cancel_all_orders(self) -> Dict[str, Any]:
        return self._call_client(("cancel_all", "cancel_all_orders"))

    def cancel_market_orders(self, condition_id: str) -> Dict[str, Any]:
        return self._call_client(("cancel_market_orders",), condition_id)

    def place_multiple_orders(self, signed_orders: Iterable[Any], tif: str = "FOK") -> Dict[str, Any]:
        if OrderType is None:
            raise RuntimeError("py-clob-client missing order types.")
        order_type = getattr(OrderType, tif, None) or OrderType.FOK
        return self._call_client(("post_orders", "post_multiple_orders"), list(signed_orders), order_type)

    def get_trades(self, **filters: Any) -> Any:
        return self._call_client(("get_trades",), **filters)

    def get_order_scoring_status(self, order_id: str) -> Any:
        return self._call_client(("get_order_scoring_status", "get_order_status"), order_id)

    def send_heartbeat(self) -> Any:
        return self._call_client(("send_heartbeat", "heartbeat"))

    def get_builder_trades(self, builder_code: str, **filters: Any) -> Any:
        params: Dict[str, Any] = {"builder_code": builder_code}
        params.update(filters)
        return self._call_client(("get_builder_trades",), **params)
