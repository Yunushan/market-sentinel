from __future__ import annotations

from .base import MarketAdapter
from .catalog import MARKET_CATALOG, MARKET_IDS, get_market_metadata
from .errors import MarketAdapterError, MarketConfigurationError, MarketHTTPError, UnsupportedFeatureError
from .kalshi import KalshiAdapter
from .manifold import ManifoldAdapter
from .polymarket import PolymarketAdapter
from .registry import AdapterRegistry, build_default_registry
from .runtime import AdapterRuntime, RateLimiter, ResolvedCredential, load_json_fixture, load_market_fixture
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
    "AdapterRuntime",
    "MARKET_CATALOG",
    "MARKET_IDS",
    "MarketAdapter",
    "MarketAdapterError",
    "MarketCapabilities",
    "MarketConfigurationError",
    "MarketContract",
    "MarketEvent",
    "MarketHTTPError",
    "MarketMetadata",
    "KalshiAdapter",
    "ManifoldAdapter",
    "OrderBookLevel",
    "OrderBookSnapshot",
    "PaperOrderRequest",
    "PaperOrderResult",
    "PolymarketAdapter",
    "PriceSnapshot",
    "RateLimiter",
    "ResolvedCredential",
    "StubMarketAdapter",
    "UnsupportedFeatureError",
    "build_default_registry",
    "create_stub_adapter",
    "get_market_metadata",
    "load_json_fixture",
    "load_market_fixture",
]
