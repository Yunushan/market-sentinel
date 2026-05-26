from __future__ import annotations

from dataclasses import replace
from typing import Any, Dict, Mapping, Optional

from .base import MarketAdapter
from .errors import UnsupportedFeatureError
from .types import MarketCapabilities, MarketMetadata, PaperOrderRequest


class StubMarketAdapter(MarketAdapter):
    """Graceful placeholder for markets without an implemented adapter yet."""

    def __init__(
        self,
        metadata: MarketMetadata,
        config: Optional[Mapping[str, Any]] = None,
        reason: str = "",
    ) -> None:
        super().__init__(config=config)
        self.metadata = replace(metadata, capabilities=MarketCapabilities())
        self.runtime = self._create_runtime()
        self.reason = reason or (
            f"{self.display_name} is listed in the market catalog, but an official adapter "
            "has not been implemented yet."
        )

    def health_check(self) -> Dict[str, Any]:
        health = super().health_check()
        health.update({"ok": False, "message": self.reason, "stub": True})
        return health

    def ensure_capability(self, capability: str) -> None:
        raise UnsupportedFeatureError(self.market_id, capability, self.unsupported_message(capability))

    def unsupported_message(self, capability: str) -> str:
        return (
            f"{self.display_name} adapter does not currently support {capability}. "
            f"{self.reason}"
        )

    def list_events(self, query: str = "", limit: int = 50):
        self.ensure_capability("event_listing")

    def list_contracts(self, event_id: str):
        self.ensure_capability("event_listing")

    def get_price(self, contract_id: str):
        self.ensure_capability("price_reading")

    def get_orderbook(self, contract_id: str):
        self.ensure_capability("orderbook_reading")

    def place_paper_order(self, order: PaperOrderRequest):
        self.ensure_capability("paper_trading")

    def place_live_order(self, order: PaperOrderRequest):
        self.ensure_capability("live_trading")

    def copy_trade_from_activity(self, activity: Mapping[str, Any]):
        self.ensure_capability("copy_trading")


def create_stub_adapter(
    metadata: MarketMetadata,
    config: Optional[Mapping[str, Any]] = None,
    reason: str = "",
) -> StubMarketAdapter:
    return StubMarketAdapter(metadata=metadata, config=config, reason=reason)
