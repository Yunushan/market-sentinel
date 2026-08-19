from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping, Optional, Tuple

from .base import MarketAdapter
from .catalog import get_market_metadata
from .errors import MarketConfigurationError, MarketHTTPError, UnsupportedFeatureError
from .types import (
    MarketContract,
    MarketEvent,
    OrderBookLevel,
    OrderBookSnapshot,
    PaperOrderRequest,
    PaperOrderResult,
    PriceSnapshot,
)


DEFAULT_CONTEXT_API_BASE_URL = "https://api.context.markets/v2"
CONTEXT_REFERENCES = (
    "https://docs.context.markets/developers/guides/api-keys",
    "https://docs.context.markets/api-reference/markets/list-markets",
    "https://docs.context.markets/api-reference/orders/create-order",
    "https://docs.context.markets/api-reference/orders/cancel-order",
    "https://docs.context.markets/agents/react-sdk/index",
)


class ContextV2Adapter(MarketAdapter):
    """Context Markets v2 REST adapter with a signed-order boundary.

    Context separates API-key authentication from wallet signing.  The adapter
    therefore accepts a complete, externally signed order payload for live
    submission and never handles a private key.  Read and paper operations are
    deterministic and fixture-friendly; live execution remains disabled by the
    shared safety gate unless the operator explicitly enables it.
    """

    metadata = get_market_metadata("context_v2")
    live_order_sides = ("BUY", "SELL")

    def health_check(self) -> Dict[str, Any]:
        health = super().health_check()
        credential = self.resolve_credential(
            "context_api_key", ("CONTEXT_API_KEY",), label="CONTEXT_API_KEY"
        )
        health.update(
            {
                "api_base_url": self.api_base_url,
                "api_key_configured": bool(credential),
                "api_key_source": credential.source if credential else "missing",
                "chain": str(self.config.get("context_chain") or "mainnet"),
                "references": list(CONTEXT_REFERENCES),
                "live_trading_supported": True,
                "live_trading_enabled": self.config_bool("live_trading_enabled", False),
                "signed_order_required": True,
                "private_key_handling": "external_wallet_only",
            }
        )
        return health

    @property
    def api_base_url(self) -> str:
        configured = self.config.get("context_api_base_url") or self.config.get("api_base_url")
        return str(configured or DEFAULT_CONTEXT_API_BASE_URL).rstrip("/")

    def list_events(self, query: str = "", limit: int = 50) -> List[MarketEvent]:
        self.ensure_capability("event_listing")
        desired = max(1, min(int(limit or 50), 50))
        params: Dict[str, Any] = {"limit": desired}
        status = str(self.config.get("context_market_status") or "active").strip()
        if status:
            params["status"] = status
        if str(query or "").strip():
            params["search"] = str(query).strip()
        payload = self._get("/markets", params=params)
        markets = self._rows(payload, "markets", "data")
        needle = str(query or "").strip().lower()
        if needle:
            markets = [market for market in markets if needle in self._search_text(market)]
        return [self._event_from_market(market) for market in markets[:desired]]

    def list_contracts(self, event_id: str) -> List[MarketContract]:
        self.ensure_capability("event_listing")
        market_id = self._required_id(event_id, "market")
        market = self._get_market(market_id)
        return self._contracts_from_market(market)

    def get_price(self, contract_id: str) -> PriceSnapshot:
        self.ensure_capability("price_reading")
        market_id, outcome_index = self._split_contract_id(contract_id)
        market = self._get_market(market_id)
        row = self._price_row(market, outcome_index)
        bid = self._probability(self._value(row, "bestBid", "best_bid", "buyPrice", "buy_price"))
        ask = self._probability(self._value(row, "bestAsk", "best_ask", "sellPrice", "sell_price"))
        last = self._probability(self._value(row, "lastPrice", "last_price"))
        midpoint = self._probability(self._value(row, "midPrice", "mid_price"))
        if midpoint is None and bid is not None and ask is not None:
            midpoint = (bid + ask) / 2.0
        if last is None:
            last = midpoint or bid or ask
        return PriceSnapshot(
            market_id=self.market_id,
            contract_id=self._contract_id(market_id, outcome_index),
            last=last,
            bid=bid,
            ask=ask,
            midpoint=midpoint,
            source="context_markets_v2",
            raw={"market": dict(market), "outcome_index": outcome_index, "price": dict(row)},
        )

    def get_orderbook(self, contract_id: str) -> OrderBookSnapshot:
        self.ensure_capability("orderbook_reading")
        market_id, outcome_index = self._split_contract_id(contract_id)
        payload = self._get(f"/markets/{market_id}/orderbook")
        book = self._orderbook_for_outcome(payload, outcome_index)
        bids = self._levels(book.get("bids"), descending=True)
        asks = self._levels(book.get("asks"), descending=False)
        if not bids and not asks:
            # Some responses expose only the quote summary.  Preserve a useful
            # one-level snapshot rather than silently returning an empty book.
            market = self._get_market(market_id)
            row = self._price_row(market, outcome_index)
            bid = self._probability(self._value(row, "bestBid", "best_bid", "buyPrice", "buy_price"))
            ask = self._probability(self._value(row, "bestAsk", "best_ask", "sellPrice", "sell_price"))
            if bid is not None:
                bids = [OrderBookLevel(price=bid, size=0.0)]
            if ask is not None:
                asks = [OrderBookLevel(price=ask, size=0.0)]
        return OrderBookSnapshot(
            market_id=self.market_id,
            contract_id=self._contract_id(market_id, outcome_index),
            bids=bids,
            asks=asks,
            raw={"orderbook": self._mapping_payload(payload), "outcome_index": outcome_index},
        )

    def place_paper_order(self, order: PaperOrderRequest) -> PaperOrderResult:
        self.ensure_capability("paper_trading")
        market_id, outcome_index = self._validate_order(order)
        return PaperOrderResult(
            market_id=self.market_id,
            contract_id=self._contract_id(market_id, outcome_index),
            accepted=True,
            message=(
                f"DRY RUN: would place Context {str(order.side).upper()} "
                f"for {float(order.size):.4f} shares"
                + (f" at probability {float(order.limit_price):.4f}" if order.limit_price is not None else "")
            ),
            raw={"request": self._order_payload(order, signed=False), "dry_run": True},
        )

    def place_live_order(self, order: PaperOrderRequest) -> Dict[str, Any]:
        self.ensure_capability("live_trading")
        market_id, outcome_index = self._validate_order(order)
        preflight = self.preflight_live_order(order)
        payload = self._order_payload(order, signed=True)
        response = self._request_json("POST", "/orders", payload, auth=True)
        return {
            "market_id": self.market_id,
            "contract_id": self._contract_id(market_id, outcome_index),
            "live": True,
            "preflight": preflight,
            "request": payload,
            "response": response,
        }

    def copy_trade_from_activity(self, activity: Mapping[str, Any]) -> PaperOrderResult:
        raise UnsupportedFeatureError(
            self.market_id,
            "copy_trading",
            "Context copy trading is unsupported because the official API does not provide account-activity mirroring.",
        )

    def _get_market(self, market_id: str) -> Mapping[str, Any]:
        payload = self._get(f"/markets/{market_id}")
        market = self._mapping_payload(payload)
        if isinstance(market.get("market"), Mapping):
            market = dict(market["market"])
        if not market:
            raise MarketConfigurationError(f"Context market {market_id!r} was not found.")
        return market

    def _get(self, path: str, *, params: Optional[Mapping[str, Any]] = None) -> Any:
        return self.runtime.get_json(
            self._url(self.api_base_url, path),
            params=params,
            headers=self._headers(required=True),
        )

    def _request_json(
        self,
        method: str,
        path: str,
        body: Mapping[str, Any],
        *,
        auth: bool = False,
    ) -> Any:
        self.runtime.rate_limiter.wait()
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if auth:
            headers.update(self._headers(required=True))
        try:
            response = self.runtime.session.request(
                method.upper(),
                self._url(self.api_base_url, path),
                json=dict(body),
                headers={"User-Agent": self.runtime.user_agent, **headers},
                timeout=self.runtime.timeout_seconds,
            )
        except Exception as exc:
            raise MarketHTTPError(f"{self.market_id} HTTP request failed: {exc}") from exc
        status = int(getattr(response, "status_code", 0) or 0)
        if status >= 400:
            raise MarketHTTPError(f"{self.market_id} HTTP {status}: {str(getattr(response, 'text', ''))[:200]}")
        try:
            return response.json()
        except ValueError as exc:
            raise MarketHTTPError(f"{self.market_id} response was not valid JSON.") from exc

    def _headers(self, *, required: bool) -> Dict[str, str]:
        credential = self.resolve_credential(
            "context_api_key", ("CONTEXT_API_KEY",), required=required, label="CONTEXT_API_KEY"
        )
        return {"Authorization": f"Bearer {credential.value}"} if credential else {}

    def _validate_order(self, order: PaperOrderRequest) -> Tuple[str, int]:
        self.ensure_order_market(order)
        market_id, outcome_index = self._split_contract_id(order.contract_id)
        side = str(order.side or "").upper()
        if side not in self.live_order_sides:
            raise MarketConfigurationError("Context order side must be BUY or SELL.")
        try:
            size = float(order.size)
        except (TypeError, ValueError) as exc:
            raise MarketConfigurationError("Context order size must be numeric.") from exc
        if not math.isfinite(size) or size <= 0:
            raise MarketConfigurationError("Context order size must be positive and finite.")
        if order.limit_price is not None and self._probability(order.limit_price) is None:
            raise MarketConfigurationError("Context order limit price must be between 0 and 1.")
        return market_id, outcome_index

    def _order_payload(self, order: PaperOrderRequest, *, signed: bool) -> Dict[str, Any]:
        existing = order.metadata.get("context_order") or order.metadata.get("signed_order")
        if signed and not isinstance(existing, Mapping):
            raise MarketConfigurationError(
                "Context live orders require order.metadata['context_order'] or ['signed_order'] with a wallet signature."
            )
        market_id, outcome_index = self._split_contract_id(order.contract_id)
        payload: Dict[str, Any] = dict(existing) if isinstance(existing, Mapping) else {}
        price = self._probability(order.limit_price)
        if price is None:
            price = 0.5
        size = float(order.size)
        payload.setdefault("type", "limit")
        payload.setdefault("marketId", market_id)
        payload.setdefault("outcomeIndex", outcome_index)
        payload.setdefault("side", 0 if str(order.side).upper() == "BUY" else 1)
        payload.setdefault("price", str(round(price * 1_000_000)))
        payload.setdefault("size", str(round(size * 1_000_000)))
        payload.setdefault("expiry", "0")
        payload.setdefault("maxFee", "0")
        payload.setdefault("makerRoleConstraint", 0)
        payload.setdefault("inventoryModeConstraint", 0)
        payload.setdefault("nonce", str(order.metadata.get("nonce") or "0x0"))
        if signed:
            trader = str(payload.get("trader") or order.metadata.get("trader") or "").strip()
            signature = str(payload.get("signature") or order.metadata.get("signature") or "").strip()
            if not trader or not signature:
                raise MarketConfigurationError(
                    "Context live orders require signed payload fields 'trader' and 'signature'."
                )
            payload["trader"] = trader
            payload["signature"] = signature
        return payload

    def _event_from_market(self, market: Mapping[str, Any]) -> MarketEvent:
        market_id = self._id(market)
        metadata = market.get("metadata") if isinstance(market.get("metadata"), Mapping) else {}
        slug = self._value(metadata, "slug") or self._value(market, "slug")
        return MarketEvent(
            market_id=self.market_id,
            event_id=market_id,
            title=str(self._value(market, "question", "shortQuestion", "title") or market_id),
            url=str(slug or market_id),
            status=str(self._value(market, "status", "resolutionStatus") or "").lower(),
            raw=dict(market),
        )

    def _contracts_from_market(self, market: Mapping[str, Any]) -> List[MarketContract]:
        market_id = self._id(market)
        title = str(self._value(market, "question", "shortQuestion", "title") or market_id)
        outcome_tokens = market.get("outcomeTokens") or market.get("outcome_tokens") or []
        outcomes = market.get("outcomes") or []
        if not isinstance(outcome_tokens, list):
            outcome_tokens = []
        if not isinstance(outcomes, list):
            outcomes = []
        contracts: List[MarketContract] = []
        count = max(len(outcome_tokens), len(outcomes), len(market.get("outcomePrices") or []))
        for index in range(count):
            token = str(outcome_tokens[index]) if index < len(outcome_tokens) else str(index)
            outcome = str(outcomes[index]) if index < len(outcomes) else f"Outcome {index}"
            contracts.append(
                MarketContract(
                    market_id=self.market_id,
                    contract_id=self._contract_id(market_id, index),
                    event_id=market_id,
                    title=f"{title} - {outcome}",
                    outcome=outcome,
                    url=str(self._value(market, "url", "slug") or market_id),
                    status=str(self._value(market, "status", "resolutionStatus") or "").lower(),
                    raw={"market": dict(market), "outcome_index": index, "outcome_token": token},
                )
            )
        return contracts

    @staticmethod
    def _price_row(market: Mapping[str, Any], outcome_index: int) -> Mapping[str, Any]:
        prices = market.get("outcomePrices") or market.get("outcome_prices") or []
        if not isinstance(prices, list):
            return {}
        for row in prices:
            if isinstance(row, Mapping):
                candidate = row.get("outcomeIndex", row.get("outcome_index", -1))
                try:
                    if int(candidate) == outcome_index:
                        return row
                except (TypeError, ValueError):
                    pass
        return prices[outcome_index] if outcome_index < len(prices) and isinstance(prices[outcome_index], Mapping) else {}

    @classmethod
    def _orderbook_for_outcome(cls, payload: Any, outcome_index: int) -> Mapping[str, Any]:
        book = cls._mapping_payload(payload)
        outcomes = book.get("outcomes") or book.get("orderbooks")
        if isinstance(outcomes, list):
            for row in outcomes:
                if isinstance(row, Mapping):
                    candidate = row.get("outcomeIndex", row.get("outcome_index", -1))
                    try:
                        if int(candidate) == outcome_index:
                            return row
                    except (TypeError, ValueError):
                        pass
        if isinstance(outcomes, Mapping):
            row = outcomes.get(str(outcome_index))
            if isinstance(row, Mapping):
                return row
        return book

    @classmethod
    def _levels(cls, value: Any, *, descending: bool) -> List[OrderBookLevel]:
        if not isinstance(value, list):
            return []
        levels: List[OrderBookLevel] = []
        for row in value:
            if isinstance(row, Mapping):
                price_value = cls._value(row, "price", "p")
                size_value = cls._value(row, "size", "quantity", "amount", "q")
            elif isinstance(row, (list, tuple)) and len(row) >= 2:
                price_value, size_value = row[0], row[1]
            else:
                continue
            price = cls._probability(price_value)
            try:
                size = float(size_value)
            except (TypeError, ValueError):
                continue
            if price is None or not math.isfinite(size) or size <= 0:
                continue
            levels.append(OrderBookLevel(price=price, size=size))
        levels.sort(key=lambda level: level.price, reverse=descending)
        return levels

    @staticmethod
    def _mapping_payload(payload: Any) -> Dict[str, Any]:
        if isinstance(payload, Mapping):
            data = payload.get("data")
            if isinstance(data, Mapping):
                return dict(data)
            return dict(payload)
        return {}

    @classmethod
    def _rows(cls, payload: Any, *keys: str) -> List[Mapping[str, Any]]:
        if isinstance(payload, list):
            return [row for row in payload if isinstance(row, Mapping)]
        mapping = cls._mapping_payload(payload)
        for key in keys:
            rows = mapping.get(key)
            if isinstance(rows, list):
                return [row for row in rows if isinstance(row, Mapping)]
        return []

    @staticmethod
    def _value(payload: Mapping[str, Any], *keys: str) -> Any:
        for key in keys:
            value = payload.get(key)
            if value not in (None, ""):
                return value
        return None

    @classmethod
    def _search_text(cls, market: Mapping[str, Any]) -> str:
        metadata = market.get("metadata") if isinstance(market.get("metadata"), Mapping) else {}
        values = [cls._value(market, "id", "question", "shortQuestion", "title", "status"), cls._value(metadata, "slug", "shortSummary")]
        return " ".join(str(value or "") for value in values).lower()

    @classmethod
    def _probability(cls, value: Any) -> Optional[float]:
        if value in (None, ""):
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(number):
            return None
        if number > 1.0 and number <= 1_000_000.0:
            number /= 1_000_000.0
        return number if 0.0 <= number <= 1.0 else None

    @staticmethod
    def _id(payload: Mapping[str, Any]) -> str:
        return str(payload.get("id") or payload.get("marketId") or "").strip()

    @staticmethod
    def _required_id(value: Any, label: str) -> str:
        clean = str(value or "").strip()
        if not clean:
            raise MarketConfigurationError(f"Context {label} id cannot be empty.")
        return clean

    @staticmethod
    def _contract_id(market_id: str, outcome_index: int) -> str:
        return f"{market_id}:{outcome_index}"

    @staticmethod
    def _split_contract_id(contract_id: str) -> Tuple[str, int]:
        parts = [part.strip() for part in str(contract_id or "").split(":")]
        if len(parts) != 2 or not parts[0] or not parts[1]:
            raise MarketConfigurationError("Context contract id must be MARKET_ID:OUTCOME_INDEX.")
        try:
            outcome_index = int(parts[1])
        except ValueError as exc:
            raise MarketConfigurationError("Context outcome index must be an integer.") from exc
        if outcome_index < 0:
            raise MarketConfigurationError("Context outcome index must be non-negative.")
        return parts[0], outcome_index

    @staticmethod
    def _url(base: str, path: str) -> str:
        return f"{base.rstrip('/')}/{str(path or '').strip('/')}"
