from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

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
        s = side.upper()
        side_const = BUY if s == "BUY" else SELL
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
        s = side.upper()
        side_const = BUY if s == "BUY" else SELL
        mo = MarketOrderArgs(token_id=token_id, amount=float(amount), side=side_const, order_type=getattr(OrderType, tif, OrderType.FOK))
        signed = self.client.create_market_order(mo)
        order_type = getattr(OrderType, tif, None) or OrderType.FOK
        return self.client.post_order(signed, order_type)
