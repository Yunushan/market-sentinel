from __future__ import annotations

from dataclasses import dataclass, asdict, field
from typing import Literal, Optional, Dict, Any, List
import uuid
import time

from market_adapters.catalog import MARKET_CATALOG


PriceSource = Literal["last_trade", "midpoint", "best_bid", "best_ask"]
Direction = Literal["above", "below"]
Theme = Literal["light", "dark"]
DEFAULT_MARKET_ID = "polymarket"


def _uuid() -> str:
    return str(uuid.uuid4())


@dataclass
class PriceAlert:
    token_id: str
    label: str
    direction: Direction
    threshold: float
    source: PriceSource = "last_trade"
    once: bool = True
    enabled: bool = True

    id: str = field(default_factory=_uuid)
    created_at: int = field(default_factory=lambda: int(time.time()))
    last_value: Optional[float] = None
    triggered: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "PriceAlert":
        return PriceAlert(**d)


@dataclass
class WalletWatch:
    """Tracks a wallet/proxyWallet and optionally enables copy-trading."""
    wallet: str
    display_name: str = ""
    enabled: bool = True
    id: str = field(default_factory=_uuid)

    # tracking state
    last_seen_ts: int = 0
    last_seen_tx: str = ""
    seen_activity_keys: List[str] = field(default_factory=list)

    # optional filters
    only_market_slug: str = ""  # if set, only emit events for this market slug

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "WalletWatch":
        return WalletWatch(**d)


@dataclass
class CopyTradeSettings:
    """Risk controls for copy trading."""
    enabled: bool = False
    live: bool = False  # False = paper/sim
    follow_wallet: str = ""  # wallet address to follow
    scale: float = 1.0  # multiplier on trade size/usdc
    max_usdc_per_trade: float = 25.0
    slippage: float = 0.02  # in price units (0..1)
    allow_sells: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "CopyTradeSettings":
        return CopyTradeSettings(**d)


@dataclass
class MarketConfig:
    market_id: str
    enabled: bool = False
    settings: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "settings": dict(self.settings),
        }

    @staticmethod
    def from_dict(market_id: str, d: Dict[str, Any]) -> "MarketConfig":
        settings = d.get("settings", {})
        if not isinstance(settings, dict):
            settings = {}
        return MarketConfig(
            market_id=str(d.get("market_id") or market_id),
            enabled=bool(d.get("enabled", False)),
            settings=dict(settings),
        )


def default_market_configs() -> Dict[str, MarketConfig]:
    return {
        meta.market_id: MarketConfig(market_id=meta.market_id, enabled=meta.default_enabled)
        for meta in MARKET_CATALOG
    }


@dataclass
class AppConfig:
    alerts: List[PriceAlert] = field(default_factory=list)
    wallets: List[WalletWatch] = field(default_factory=list)
    copytrading: CopyTradeSettings = field(default_factory=CopyTradeSettings)
    markets: Dict[str, MarketConfig] = field(default_factory=default_market_configs)
    selected_market_id: str = DEFAULT_MARKET_ID
    theme: Theme = "light"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "alerts": [a.to_dict() for a in self.alerts],
            "wallets": [w.to_dict() for w in self.wallets],
            "copytrading": self.copytrading.to_dict(),
            "markets": {market_id: cfg.to_dict() for market_id, cfg in self.markets.items()},
            "selected_market_id": self.selected_market_id,
            "theme": self.theme,
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "AppConfig":
        alerts = [PriceAlert.from_dict(x) for x in d.get("alerts", [])]
        wallets = [WalletWatch.from_dict(x) for x in d.get("wallets", [])]
        copytrading = CopyTradeSettings.from_dict(d.get("copytrading", {}))
        markets = default_market_configs()
        raw_markets = d.get("markets", {})
        if isinstance(raw_markets, dict):
            for market_id, raw_cfg in raw_markets.items():
                if isinstance(raw_cfg, dict):
                    cfg = MarketConfig.from_dict(str(market_id), raw_cfg)
                    markets[cfg.market_id] = cfg
        selected_market_id = str(d.get("selected_market_id") or DEFAULT_MARKET_ID).strip().lower()
        if selected_market_id not in markets:
            selected_market_id = DEFAULT_MARKET_ID
        raw_theme = str(d.get("theme") or "").lower()
        theme: Theme = "dark" if raw_theme == "dark" else "light"
        return AppConfig(
            alerts=alerts,
            wallets=wallets,
            copytrading=copytrading,
            markets=markets,
            selected_market_id=selected_market_id,
            theme=theme,
        )
