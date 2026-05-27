from __future__ import annotations

import math
import os
from typing import Any, Dict, List, Mapping, Optional

from .base import MarketAdapter
from .catalog import get_market_metadata
from .errors import MarketConfigurationError, UnsupportedFeatureError
from .types import (
    MarketContract,
    MarketEvent,
    OrderBookLevel,
    OrderBookSnapshot,
    PaperOrderRequest,
    PaperOrderResult,
    PriceSnapshot,
)
from polymarket import clob_rest, gamma
from polymarket.geoblock import check_geoblock
from polymarket.trader import PolymarketTrader, TraderConfig


class PolymarketAdapter(MarketAdapter):
    metadata = get_market_metadata("polymarket")

    def health_check(self) -> Dict[str, Any]:
        health = super().health_check()
        credential_sources = []
        for config_key, env_vars in (
            ("private_key", ("PRIVATE_KEY",)),
            ("funder_address", ("FUNDER_ADDRESS",)),
            ("signature_type", ("SIGNATURE_TYPE",)),
        ):
            credential = self.resolve_credential(config_key, env_vars, label=env_vars[0])
            if credential:
                credential_sources.append({"name": credential.name, "source": credential.source})
        health.update(
            {
                "live_trading_enabled": self.config_bool("live_trading_enabled", False),
                "credential_sources": credential_sources,
                "credential_requirement": "live_trading_only",
                "geoblock_required_for_live": True,
            }
        )
        return health

    def search_profiles(self, query: str, limit: int = 10) -> List[gamma.ProfileResult]:
        return gamma.search_profiles(query, limit=limit)

    def get_market_by_slug(self, slug: str) -> Optional[Dict[str, Any]]:
        return gamma.get_market_by_slug(slug)

    def get_market_by_id(self, market_id: str) -> Optional[Dict[str, Any]]:
        return gamma.get_market_by_id(market_id)

    def get_event_by_slug(self, slug: str) -> Optional[Dict[str, Any]]:
        return gamma.get_event_by_slug(slug)

    def get_event_by_id(self, event_id: str) -> Optional[Dict[str, Any]]:
        return gamma.get_event_by_id(event_id)

    def parse_market_outcomes(self, market: Dict[str, Any]) -> List[gamma.MarketOutcome]:
        return gamma.parse_market_outcomes(market)

    def check_geoblock(self) -> Dict[str, Any]:
        return check_geoblock()

    def list_events(self, query: str = "", limit: int = 50) -> List[MarketEvent]:
        self.ensure_capability("event_listing")
        query = str(query or "").strip()
        if not query:
            return []
        limit = max(1, min(int(limit or 50), 100))

        data = gamma.public_search(query, search_profiles=False, search_tags=False, limit_per_type=limit)
        if not isinstance(data, Mapping):
            return []
        events = data.get("events") or []
        markets = data.get("markets") or []
        if not isinstance(events, list):
            events = []
        if not isinstance(markets, list):
            markets = []
        out: List[MarketEvent] = []

        for raw in events:
            if not isinstance(raw, Mapping):
                continue
            event_id = str(raw.get("id") or raw.get("slug") or "")
            title = str(raw.get("title") or raw.get("question") or raw.get("slug") or event_id)
            if event_id:
                out.append(
                    MarketEvent(
                        market_id=self.market_id,
                        event_id=event_id,
                        title=title,
                        url=str(raw.get("url") or ""),
                        status=self._status_from_raw(raw),
                        raw=raw,
                    )
                )

        for raw in markets:
            if not isinstance(raw, Mapping):
                continue
            market_id = str(raw.get("id") or raw.get("conditionId") or raw.get("slug") or "")
            title = str(raw.get("question") or raw.get("title") or raw.get("slug") or market_id)
            if market_id:
                out.append(
                    MarketEvent(
                        market_id=self.market_id,
                        event_id=market_id,
                        title=title,
                        url=str(raw.get("url") or ""),
                        status=self._status_from_raw(raw),
                        raw=raw,
                    )
                )

        return out[:limit]

    def list_contracts(self, event_id: str) -> List[MarketContract]:
        self.ensure_capability("event_listing")
        ref = str(event_id or "").strip()
        if not ref:
            return []

        raw_event = self.get_event_by_id(ref) if ref.isdigit() else self.get_event_by_slug(ref)
        if isinstance(raw_event, Mapping):
            contracts: List[MarketContract] = []
            markets = raw_event.get("markets") or []
            if isinstance(markets, list):
                for market in markets:
                    if isinstance(market, Mapping):
                        contracts.extend(self._contracts_from_market(market))
            return contracts

        raw_market = self.get_market_by_id(ref) if ref.isdigit() else self.get_market_by_slug(ref)
        return self._contracts_from_market(raw_market) if isinstance(raw_market, Mapping) else []

    def get_price(self, contract_id: str) -> PriceSnapshot:
        self.ensure_capability("price_reading")
        orderbook = self.get_orderbook(contract_id)
        try:
            midpoint = self._safe_probability(clob_rest.get_midpoint(contract_id))
        except Exception:
            midpoint = None
        try:
            last_trade = self._safe_probability(clob_rest.get_last_trade_price(contract_id))
        except Exception:
            last_trade = None
        if midpoint is None and orderbook.bids and orderbook.asks:
            midpoint = (orderbook.bids[0].price + orderbook.asks[0].price) / 2.0
        raw = dict(orderbook.raw)
        raw["last_trade"] = last_trade
        raw["midpoint"] = midpoint
        return PriceSnapshot(
            market_id=self.market_id,
            contract_id=contract_id,
            last=last_trade,
            bid=orderbook.bids[0].price if orderbook.bids else None,
            ask=orderbook.asks[0].price if orderbook.asks else None,
            midpoint=midpoint,
            source="polymarket_clob",
            raw=raw,
        )

    def get_orderbook(self, contract_id: str) -> OrderBookSnapshot:
        self.ensure_capability("orderbook_reading")
        book = clob_rest.get_book(contract_id)
        if not isinstance(book, Mapping):
            book = {}
        return OrderBookSnapshot(
            market_id=self.market_id,
            contract_id=contract_id,
            bids=self._levels(book.get("bids") or book.get("buys") or [], descending=True),
            asks=self._levels(book.get("asks") or book.get("sells") or []),
            raw=book,
        )

    def place_paper_order(self, order: PaperOrderRequest) -> PaperOrderResult:
        self.ensure_capability("paper_trading")
        self._validate_order(order)
        return PaperOrderResult(
            market_id=self.market_id,
            contract_id=order.contract_id,
            accepted=True,
            message=(
                f"DRY RUN: would place {order.side.upper()} order for "
                f"{order.size:.4f} shares"
                + (f" at limit {order.limit_price:.4f}" if order.limit_price is not None else "")
            ),
            filled_size=0.0,
            average_price=None,
            raw={"request": order.metadata},
        )

    def place_live_order(self, order: PaperOrderRequest) -> Dict[str, Any]:
        self.ensure_capability("live_trading")
        self._validate_order(order)
        preflight = self.preflight_live_order(order)
        if order.limit_price is None:
            raise MarketConfigurationError("Polymarket live trading requires a limit price.")

        geo = self.check_geoblock()
        if geo.get("blocked") is True:
            raise MarketConfigurationError("Polymarket geoblock check blocked live trading.")

        private_key = self.resolve_credential(
            "private_key",
            ("PRIVATE_KEY",),
            required=True,
            label="PRIVATE_KEY",
        )

        funder_credential = self.resolve_credential("funder_address", ("FUNDER_ADDRESS",), label="FUNDER_ADDRESS")
        funder = funder_credential.value.strip() if funder_credential else None
        try:
            signature_type = int(str(self.config.get("signature_type") or os.getenv("SIGNATURE_TYPE") or "0").strip())
        except (TypeError, ValueError) as exc:
            raise MarketConfigurationError("Polymarket SIGNATURE_TYPE must be an integer.") from exc
        trader = PolymarketTrader(
            TraderConfig(
                private_key=private_key.value,
                funder_address=funder,
                signature_type=signature_type,
            )
        )
        response = trader.place_limit_order(
            token_id=order.contract_id,
            side=order.side,
            price=order.limit_price,
            size=order.size,
            tif=str(order.metadata.get("tif") or "FOK"),
        )
        return {
            "market_id": self.market_id,
            "contract_id": order.contract_id,
            "live": True,
            "preflight": preflight,
            "response": response,
        }

    def copy_trade_from_activity(self, activity: Mapping[str, Any]) -> PaperOrderResult:
        self.ensure_capability("copy_trading")
        token_id = str(activity.get("asset") or "")
        side = str(activity.get("side") or "").upper()
        try:
            size = float(activity.get("size") or 0.0)
        except (TypeError, ValueError) as exc:
            raise MarketConfigurationError("Polymarket activity size must be numeric.") from exc
        price = activity.get("price")
        try:
            limit_price = float(price) if price is not None else None
        except (TypeError, ValueError) as exc:
            raise MarketConfigurationError("Polymarket activity price must be numeric when present.") from exc
        order = PaperOrderRequest(
            market_id=self.market_id,
            contract_id=token_id,
            side=side,
            size=size,
            limit_price=limit_price,
            metadata={"activity": dict(activity)},
        )
        return self.place_paper_order(order)

    def _contracts_from_market(self, market: Mapping[str, Any]) -> List[MarketContract]:
        market_ref = str(market.get("id") or market.get("conditionId") or market.get("slug") or "")
        market_title = str(market.get("question") or market.get("title") or market.get("slug") or market_ref)
        status = self._status_from_raw(market)
        contracts: List[MarketContract] = []
        try:
            outcomes = self.parse_market_outcomes(dict(market))
        except Exception:
            outcomes = []
        for outcome in outcomes:
            if not outcome.token_id:
                continue
            contracts.append(
                MarketContract(
                    market_id=self.market_id,
                    contract_id=outcome.token_id,
                    event_id=market_ref,
                    title=market_title,
                    outcome=outcome.outcome,
                    url=str(market.get("url") or ""),
                    status=status,
                    raw={"market": market, "outcome": outcome},
                )
            )
        return contracts

    def _validate_order(self, order: PaperOrderRequest) -> None:
        self.ensure_order_market(order)
        if not str(order.contract_id or "").strip():
            raise MarketConfigurationError("Polymarket order requires a contract id.")
        side = str(order.side or "").upper()
        if side not in ("BUY", "SELL"):
            raise MarketConfigurationError("Polymarket order side must be BUY or SELL.")
        if not self._is_positive_number(order.size):
            raise MarketConfigurationError("Polymarket order size must be positive.")
        if order.limit_price is not None and self._safe_probability(order.limit_price) is None:
            raise MarketConfigurationError("Polymarket limit price must be between 0 and 1.")

    @staticmethod
    def _levels(raw_levels: Any, *, descending: bool = False) -> List[OrderBookLevel]:
        levels: List[OrderBookLevel] = []
        if not isinstance(raw_levels, list):
            return levels
        for raw in raw_levels:
            if not isinstance(raw, Mapping):
                continue
            try:
                price = PolymarketAdapter._safe_probability(raw.get("price"))
                size = float(raw.get("size") or 0.0)
            except (KeyError, TypeError, ValueError):
                continue
            if price is None or not PolymarketAdapter._is_positive_number(size):
                continue
            levels.append(OrderBookLevel(price=price, size=size))
        levels.sort(key=lambda level: level.price, reverse=descending)
        return levels

    @staticmethod
    def _status_from_raw(raw: Mapping[str, Any]) -> str:
        if raw.get("closed") is True:
            return "closed"
        if raw.get("active") is True:
            return "active"
        return str(raw.get("status") or "")

    @staticmethod
    def _safe_probability(value: Any) -> Optional[float]:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(number) or number < 0 or number > 1:
            return None
        return number

    @staticmethod
    def _is_positive_number(value: Any) -> bool:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return False
        return math.isfinite(number) and number > 0
