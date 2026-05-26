from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional

from .errors import MarketConfigurationError, UnsupportedFeatureError
from .runtime import AdapterRuntime, ResolvedCredential
from .types import (
    MarketCapabilities,
    MarketContract,
    MarketEvent,
    MarketMetadata,
    OrderBookSnapshot,
    PaperOrderRequest,
    PaperOrderResult,
    PriceSnapshot,
)


class MarketAdapter:
    """Base class for prediction-market adapters.

    Concrete adapters should override only the operations their market supports.
    Unsupported operations raise clear adapter errors by default.
    """

    metadata = MarketMetadata(market_id="base", display_name="Base")

    def __init__(
        self,
        config: Optional[Mapping[str, Any]] = None,
        *,
        runtime: Optional[AdapterRuntime] = None,
    ) -> None:
        self.config: Dict[str, Any] = dict(config or {})
        self.runtime = runtime or self._create_runtime()

    @property
    def market_id(self) -> str:
        return self.metadata.market_id

    @property
    def display_name(self) -> str:
        return self.metadata.display_name

    @property
    def capabilities(self) -> MarketCapabilities:
        return self.metadata.capabilities

    def _create_runtime(self) -> AdapterRuntime:
        return AdapterRuntime(self.market_id, self.config)

    def health_check(self) -> Dict[str, Any]:
        return {
            "market_id": self.market_id,
            "ok": True,
            "message": "Adapter loaded.",
            "adapter": type(self).__name__,
            "capabilities": self.capabilities.to_dict(),
            "runtime": self.runtime.describe(),
        }

    def ensure_capability(self, capability: str) -> None:
        if not getattr(self.capabilities, capability, False):
            raise UnsupportedFeatureError(self.market_id, capability)

    def config_bool(self, key: str, default: bool = False) -> bool:
        return self.runtime.config_bool(key, default)

    def resolve_credential(
        self,
        config_key: str,
        env_vars: Iterable[str] = (),
        *,
        required: bool = False,
        label: str = "",
    ) -> Optional[ResolvedCredential]:
        return self.runtime.resolve_credential(config_key, env_vars, required=required, label=label)

    def ensure_order_market(self, order: PaperOrderRequest) -> None:
        if order.market_id != self.market_id:
            raise MarketConfigurationError(f"Order market mismatch: {order.market_id}")

    def ensure_live_trading_enabled(self, feature_name: str = "live trading") -> None:
        if not self.config_bool("live_trading_enabled", False):
            raise MarketConfigurationError(f"{self.display_name} {feature_name} is disabled by adapter config.")

    def ensure_copy_trading_enabled(self, feature_name: str = "copy trading") -> None:
        if not self.config_bool("copy_trading_enabled", False):
            raise MarketConfigurationError(f"{self.display_name} {feature_name} is disabled by adapter config.")

    def list_events(self, query: str = "", limit: int = 50) -> List[MarketEvent]:
        self.ensure_capability("event_listing")
        raise UnsupportedFeatureError(self.market_id, "event_listing")

    def list_contracts(self, event_id: str) -> List[MarketContract]:
        self.ensure_capability("event_listing")
        raise UnsupportedFeatureError(self.market_id, "event_listing")

    def get_price(self, contract_id: str) -> PriceSnapshot:
        self.ensure_capability("price_reading")
        raise UnsupportedFeatureError(self.market_id, "price_reading")

    def get_orderbook(self, contract_id: str) -> OrderBookSnapshot:
        self.ensure_capability("orderbook_reading")
        raise UnsupportedFeatureError(self.market_id, "orderbook_reading")

    def place_paper_order(self, order: PaperOrderRequest) -> PaperOrderResult:
        self.ensure_capability("paper_trading")
        raise UnsupportedFeatureError(self.market_id, "paper_trading")

    def place_live_order(self, order: PaperOrderRequest) -> Dict[str, Any]:
        self.ensure_capability("live_trading")
        raise UnsupportedFeatureError(
            self.market_id,
            "live_trading",
            f"{self.display_name} live trading is not implemented in this adapter.",
        )

    def copy_trade_from_activity(self, activity: Mapping[str, Any]) -> PaperOrderResult:
        self.ensure_capability("copy_trading")
        raise UnsupportedFeatureError(self.market_id, "copy_trading")
