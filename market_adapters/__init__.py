from __future__ import annotations

from .base import MarketAdapter
from .catalog import MARKET_CATALOG, MARKET_IDS, get_market_metadata
from .errors import MarketAdapterError, MarketConfigurationError, UnsupportedFeatureError
from .polymarket import PolymarketAdapter
from .registry import AdapterRegistry, build_default_registry
from .stub import StubMarketAdapter, create_stub_adapter
from .types import (
    MarketCapabilities,
    MarketContract,
    MarketEvent,
    MarketMetadata,
    OrderBookLevel,
    OrderBookSnapshot,
    PaperOrderRequest,
    PaperOrderResult,
    PriceSnapshot,
)

__all__ = [
    "AdapterRegistry",
    "MARKET_CATALOG",
    "MARKET_IDS",
    "MarketAdapter",
    "MarketAdapterError",
    "MarketCapabilities",
    "MarketConfigurationError",
    "MarketContract",
    "MarketEvent",
    "MarketMetadata",
    "OrderBookLevel",
    "OrderBookSnapshot",
    "PaperOrderRequest",
    "PaperOrderResult",
    "PolymarketAdapter",
    "PriceSnapshot",
    "StubMarketAdapter",
    "UnsupportedFeatureError",
    "build_default_registry",
    "create_stub_adapter",
    "get_market_metadata",
]
