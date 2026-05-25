from __future__ import annotations

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

        data = gamma.public_search(query, search_profiles=False, search_tags=False, limit_per_type=limit)
        events = data.get("events") or []
        markets = data.get("markets") or []
        out: List[MarketEvent] = []

        for raw in events:
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
        if raw_event:
            contracts: List[MarketContract] = []
            for market in raw_event.get("markets") or []:
                contracts.extend(self._contracts_from_market(market))
            return contracts

        raw_market = self.get_market_by_id(ref) if ref.isdigit() else self.get_market_by_slug(ref)
        return self._contracts_from_market(raw_market) if raw_market else []

    def get_price(self, contract_id: str) -> PriceSnapshot:
        self.ensure_capability("price_reading")
        orderbook = self.get_orderbook(contract_id)
        midpoint = clob_rest.get_midpoint(contract_id)
        if midpoint is None and orderbook.bids and orderbook.asks:
            midpoint = (orderbook.bids[0].price + orderbook.asks[0].price) / 2.0
        return PriceSnapshot(
            market_id=self.market_id,
            contract_id=contract_id,
            bid=orderbook.bids[0].price if orderbook.bids else None,
            ask=orderbook.asks[0].price if orderbook.asks else None,
            midpoint=midpoint,
            source="polymarket_clob",
            raw=orderbook.raw,
        )

    def get_orderbook(self, contract_id: str) -> OrderBookSnapshot:
        self.ensure_capability("orderbook_reading")
        book = clob_rest.get_book(contract_id)
        return OrderBookSnapshot(
            market_id=self.market_id,
            contract_id=contract_id,
            bids=self._levels(book.get("bids") or book.get("buys") or []),
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
        if not bool(self.config.get("live_trading_enabled", False)):
            raise MarketConfigurationError("Polymarket live trading is disabled by adapter config.")

        geo = self.check_geoblock()
        if geo.get("blocked") is True:
            raise MarketConfigurationError("Polymarket geoblock check blocked live trading.")

        private_key = str(self.config.get("private_key") or os.getenv("PRIVATE_KEY") or "").strip()
        if not private_key:
            raise MarketConfigurationError("Missing PRIVATE_KEY for Polymarket live trading.")

        funder = str(self.config.get("funder_address") or os.getenv("FUNDER_ADDRESS") or "").strip() or None
        signature_type = int(str(self.config.get("signature_type") or os.getenv("SIGNATURE_TYPE") or "0").strip())
        trader = PolymarketTrader(
            TraderConfig(
                private_key=private_key,
                funder_address=funder,
                signature_type=signature_type,
            )
        )
        if order.limit_price is None:
            raise MarketConfigurationError("Polymarket live trading requires a limit price.")
        return trader.place_limit_order(
            token_id=order.contract_id,
            side=order.side,
            price=order.limit_price,
            size=order.size,
            tif=str(order.metadata.get("tif") or "FOK"),
        )

    def copy_trade_from_activity(self, activity: Mapping[str, Any]) -> PaperOrderResult:
        self.ensure_capability("copy_trading")
        token_id = str(activity.get("asset") or "")
        side = str(activity.get("side") or "").upper()
        size = float(activity.get("size") or 0.0)
        price = activity.get("price")
        order = PaperOrderRequest(
            market_id=self.market_id,
            contract_id=token_id,
            side=side,
            size=size,
            limit_price=float(price) if price is not None else None,
            metadata={"activity": dict(activity)},
        )
        return self.place_paper_order(order)

    def _contracts_from_market(self, market: Dict[str, Any]) -> List[MarketContract]:
        market_ref = str(market.get("id") or market.get("conditionId") or market.get("slug") or "")
        market_title = str(market.get("question") or market.get("title") or market.get("slug") or market_ref)
        status = self._status_from_raw(market)
        contracts: List[MarketContract] = []
        for outcome in self.parse_market_outcomes(market):
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
        if order.market_id != self.market_id:
            raise MarketConfigurationError(f"Order market mismatch: {order.market_id}")
        if not order.contract_id:
            raise MarketConfigurationError("Polymarket order requires a contract id.")
        if order.side.upper() not in ("BUY", "SELL"):
            raise MarketConfigurationError("Polymarket order side must be BUY or SELL.")
        if order.size <= 0:
            raise MarketConfigurationError("Polymarket order size must be positive.")
        if order.limit_price is not None and not 0 <= order.limit_price <= 1:
            raise MarketConfigurationError("Polymarket limit price must be between 0 and 1.")

    @staticmethod
    def _levels(raw_levels: List[Mapping[str, Any]]) -> List[OrderBookLevel]:
        levels: List[OrderBookLevel] = []
        for raw in raw_levels:
            try:
                levels.append(OrderBookLevel(price=float(raw["price"]), size=float(raw.get("size") or 0.0)))
            except (KeyError, TypeError, ValueError):
                continue
        return levels

    @staticmethod
    def _status_from_raw(raw: Mapping[str, Any]) -> str:
        if raw.get("closed") is True:
            return "closed"
        if raw.get("active") is True:
            return "active"
        return str(raw.get("status") or "")
