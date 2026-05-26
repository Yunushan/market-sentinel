from __future__ import annotations

from typing import Any, Callable, Dict, List, Mapping, Optional, Type

from .base import MarketAdapter
from .catalog import MARKET_CATALOG
from .errors import MarketConfigurationError
from .types import MarketMetadata


AdapterFactory = Callable[[Optional[Mapping[str, Any]]], MarketAdapter]


class AdapterRegistry:
    """Registry for market adapter factories."""

    def __init__(self) -> None:
        self._metadata: Dict[str, MarketMetadata] = {}
        self._factories: Dict[str, AdapterFactory] = {}

    def register_metadata(self, metadata: MarketMetadata, *, replace: bool = False) -> None:
        market_id = self._normalize_market_id(metadata.market_id)
        if not replace and market_id in self._metadata:
            raise MarketConfigurationError(f"Market metadata already registered: {market_id}")
        self._metadata[market_id] = metadata

    def register_adapter(self, adapter_cls: Type[MarketAdapter], *, replace: bool = False) -> None:
        metadata = adapter_cls.metadata
        market_id = self._normalize_market_id(metadata.market_id)
        if market_id == "base":
            raise MarketConfigurationError("Base MarketAdapter cannot be registered directly.")
        if not replace and market_id in self._factories:
            raise MarketConfigurationError(f"Adapter already registered: {market_id}")
        self.register_metadata(metadata, replace=True)
        self._factories[market_id] = adapter_cls

    def register_factory(
        self,
        metadata: MarketMetadata,
        factory: AdapterFactory,
        *,
        replace: bool = False,
    ) -> None:
        market_id = self._normalize_market_id(metadata.market_id)
        if not replace and market_id in self._factories:
            raise MarketConfigurationError(f"Adapter already registered: {market_id}")
        self.register_metadata(metadata, replace=True)
        self._factories[market_id] = factory

    def create(self, market_id: str, config: Optional[Mapping[str, Any]] = None) -> MarketAdapter:
        normalized = self._normalize_market_id(market_id)
        factory = self._factories.get(normalized)
        if factory is None:
            raise MarketConfigurationError(f"No adapter registered for market: {normalized}")
        return factory(config)

    def get_metadata(self, market_id: str) -> MarketMetadata:
        normalized = self._normalize_market_id(market_id)
        try:
            return self._metadata[normalized]
        except KeyError as exc:
            raise MarketConfigurationError(f"Unknown market: {normalized}") from exc

    def list_metadata(self, *, enabled_ids: Optional[Mapping[str, bool]] = None) -> List[MarketMetadata]:
        metadata = list(self._metadata.values())
        metadata.sort(key=lambda item: item.display_name.lower())
        if enabled_ids is None:
            return metadata
        return [m for m in metadata if enabled_ids.get(m.market_id, False)]

    def list_market_ids(self) -> List[str]:
        return sorted(self._metadata)

    def has_adapter(self, market_id: str) -> bool:
        return self._normalize_market_id(market_id) in self._factories

    @staticmethod
    def _normalize_market_id(market_id: str) -> str:
        normalized = str(market_id or "").strip().lower()
        if not normalized:
            raise MarketConfigurationError("Market id cannot be empty.")
        return normalized


def build_default_registry() -> AdapterRegistry:
    registry = AdapterRegistry()
    for metadata in MARKET_CATALOG:
        registry.register_metadata(metadata)
    from .kalshi import KalshiAdapter
    from .manifold import ManifoldAdapter
    from .polymarket import PolymarketAdapter
    from .stub import create_stub_adapter

    implemented_adapters = (PolymarketAdapter, KalshiAdapter, ManifoldAdapter)
    registry.register_adapter(PolymarketAdapter, replace=True)
    registry.register_adapter(KalshiAdapter, replace=True)
    registry.register_adapter(ManifoldAdapter, replace=True)
    for metadata in MARKET_CATALOG:
        if metadata.market_id in {adapter.metadata.market_id for adapter in implemented_adapters}:
            continue
        registry.register_factory(
            metadata,
            lambda config=None, metadata=metadata: create_stub_adapter(metadata, config),
            replace=True,
        )
    return registry
