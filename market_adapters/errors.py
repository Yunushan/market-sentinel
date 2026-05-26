from __future__ import annotations


class MarketAdapterError(RuntimeError):
    """Base error for market adapter failures."""


class MarketConfigurationError(MarketAdapterError):
    """Raised when an adapter is missing required local configuration."""


class MarketHTTPError(MarketAdapterError):
    """Raised when an adapter HTTP request fails."""


class UnsupportedFeatureError(MarketAdapterError):
    """Raised when a market adapter does not support a requested feature."""

    def __init__(self, market_id: str, feature: str, message: str = "") -> None:
        self.market_id = market_id
        self.feature = feature
        detail = message or f"{market_id} does not support {feature} through this adapter."
        super().__init__(detail)
