from __future__ import annotations

import re
from typing import Optional

from polymarket.util import normalize_wallet

from .errors import MarketConfigurationError


_MANIFOLD_USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$", re.IGNORECASE)


def normalize_activity_identity(market_id: str, raw: object) -> Optional[str]:
    """Normalize the identity used by a market's public activity feed.

    EVM venues continue to use canonical lower-case wallet addresses. Manifold
    activity is keyed by a public username, so it must be explicitly prefixed
    to prevent a username from being confused with a wallet or interpolated
    into an unsafe URL path.
    """

    market = str(market_id or "polymarket").strip().lower() or "polymarket"
    value = str(raw or "").strip()
    if market == "manifold":
        prefix = "manifold:"
        if not value.lower().startswith(prefix):
            return None
        username = value[len(prefix) :].strip().lower()
        if not _MANIFOLD_USERNAME_RE.fullmatch(username):
            return None
        return f"{prefix}{username}"
    return normalize_wallet(value)


def require_activity_identity(market_id: str, raw: object) -> str:
    """Return a normalized activity identity or a market-specific error."""

    market = str(market_id or "polymarket").strip().lower() or "polymarket"
    normalized = normalize_activity_identity(market, raw)
    if normalized:
        return normalized
    if market == "manifold":
        raise MarketConfigurationError(
            "Manifold activity identity must use the safe manifold:<username> format."
        )
    raise MarketConfigurationError("Activity identity must be a valid 0x wallet/proxyWallet address.")


def activity_identity_hint(market_id: str) -> str:
    """Return the UI input format for a selected market."""

    return "manifold:<username>" if str(market_id or "").strip().lower() == "manifold" else "0x wallet address"
