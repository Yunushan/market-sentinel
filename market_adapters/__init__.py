from __future__ import annotations

from .azuro import AzuroAdapter
from .base import MarketAdapter
from .betfair import BetfairExchangeAdapter
from .catalog import MARKET_CATALOG, MARKET_IDS, get_market_metadata
from .errors import MarketAdapterError, MarketConfigurationError, MarketHTTPError, UnsupportedFeatureError
from .gemini import GeminiPredictionAdapter
from .kalshi import KalshiAdapter
from .legacy_web3 import AugurAdapter, OmenAdapter, ZeitgeistAdapter
from .limitless import LimitlessAdapter
from .manifold import ManifoldAdapter
from .metaculus import MetaculusAdapter
from .myriad import MyriadAdapter
from .opinion import OpinionAdapter
from .polymarket import PolymarketAdapter
from .predict_fun import PredictFunAdapter
from .predictit import PredictItAdapter
from .registry import AdapterRegistry, VERIFIED_BLOCKERS, build_default_registry
from .runtime import AdapterRuntime, RateLimiter, ResolvedCredential, load_json_fixture, load_market_fixture
from .stub import StubMarketAdapter, VerifiedBlockedAdapter, create_stub_adapter, create_verified_blocked_adapter
from .sx_bet import SxBetAdapter
from .xo import XOMarketAdapter
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
    "AugurAdapter",
    "AzuroAdapter",
    "BetfairExchangeAdapter",
    "GeminiPredictionAdapter",
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
    "LimitlessAdapter",
    "ManifoldAdapter",
    "MetaculusAdapter",
    "MyriadAdapter",
    "OpinionAdapter",
    "OrderBookLevel",
    "OrderBookSnapshot",
    "OmenAdapter",
    "PaperOrderRequest",
    "PaperOrderResult",
    "PolymarketAdapter",
    "PredictFunAdapter",
    "PredictItAdapter",
    "PriceSnapshot",
    "RateLimiter",
    "ResolvedCredential",
    "SxBetAdapter",
    "StubMarketAdapter",
    "UnsupportedFeatureError",
    "VERIFIED_BLOCKERS",
    "VerifiedBlockedAdapter",
    "XOMarketAdapter",
    "ZeitgeistAdapter",
    "build_default_registry",
    "create_stub_adapter",
    "create_verified_blocked_adapter",
    "get_market_metadata",
    "load_json_fixture",
    "load_market_fixture",
]
