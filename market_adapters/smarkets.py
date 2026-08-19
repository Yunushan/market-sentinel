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


DEFAULT_SMARKETS_API_BASE_URL = "https://api.smarkets.com/v3"
SMARKETS_REFERENCES = (
    "https://docs.smarkets.com/",
    "https://help.smarkets.com/hc/en-gb/articles/34720906181021-Smarkets-API-Documentation-Resources",
    "https://help.smarkets.com/hc/en-gb/articles/34697834941085-Smarkets-API-Access-Integration-T-Cs",
)


class SmarketsAdapter(MarketAdapter):
    """Smarkets REST exchange adapter with explicit API-approval gates.

    Smarkets prices and quantities are represented in exchange integer units
    (probability/quantity scaled by 10,000).  The adapter normalizes those
    values into probabilities and stake sizes, keeps paper mode local, and only
    submits a guarded order after the operator supplies an approved session
    token.  No browser/private-session scraping is used.
    """

    metadata = get_market_metadata("smarkets")
    live_order_sides = ("BUY", "SELL")

    def health_check(self) -> Dict[str, Any]:
        health = super().health_check()
        credential = self.resolve_credential(
            "smarkets_session_token",
            ("SMARKETS_SESSION_TOKEN", "SMARKETS_API_TOKEN"),
            label="SMARKETS_SESSION_TOKEN",
        )
        health.update(
            {
                "api_base_url": self.api_base_url,
                "session_token_configured": bool(credential),
                "session_token_source": credential.source if credential else "missing",
                "references": list(SMARKETS_REFERENCES),
                "api_approval_required": True,
                "live_trading_supported": True,
                "live_trading_enabled": self.config_bool("live_trading_enabled", False),
            }
        )
        return health

    @property
    def api_base_url(self) -> str:
        configured = self.config.get("smarkets_api_base_url") or self.config.get("api_base_url")
        return str(configured or DEFAULT_SMARKETS_API_BASE_URL).rstrip("/")

    def list_events(self, query: str = "", limit: int = 50) -> List[MarketEvent]:
        self.ensure_capability("event_listing")
        desired = max(1, min(int(limit or 50), 100))
        params: Dict[str, Any] = {"limit": desired}
        state = str(self.config.get("smarkets_event_state") or "upcoming").strip()
        if state:
            params["state"] = state
        if str(query or "").strip():
            params["search"] = str(query).strip()
        payload = self._get("/events/", params=params)
        events = self._rows(payload, "events", "data")
        needle = str(query or "").strip().lower()
        if needle:
            events = [event for event in events if needle in self._search_text(event)]
        return [self._event_from_payload(event) for event in events[:desired]]

    def list_contracts(self, event_id: str) -> List[MarketContract]:
        self.ensure_capability("event_listing")
        event = self._required_id(event_id, "event")
        markets_payload = self._get(f"/events/{event}/markets/", params={"limit": 100})
        markets = self._rows(markets_payload, "markets", "data")
        contracts: List[MarketContract] = []
        for market in markets:
            market_id = self._id(market, "market_id")
            if not market_id:
                continue
            rows = self._rows(market, "contracts")
            if not rows:
                contracts_payload = self._get(f"/markets/{market_id}/contracts/", params={"limit": 100})
                rows = self._rows(contracts_payload, "contracts", "data")
            contracts.extend(self._contracts_from_rows(event, market, rows))
        return contracts

    def get_orderbook(self, contract_id: str) -> OrderBookSnapshot:
        self.ensure_capability("orderbook_reading")
        market_id, contract_ref = self._split_contract_id(contract_id)
        payload = self._get(f"/markets/{market_id}/quotes/", params=None)
        quote = self._quote_for_contract(payload, contract_ref)
        bids = self._levels(self._value(quote, "back_offers", "backOffers", "bids", "buy"), reverse=True)
        asks = self._levels(self._value(quote, "lay_offers", "layOffers", "asks", "sell", "offers"), reverse=False)
        return OrderBookSnapshot(
            market_id=self.market_id,
            contract_id=self._contract_id(market_id, contract_ref),
            bids=bids,
            asks=asks,
            raw={"quote": dict(quote)},
        )

    def get_price(self, contract_id: str) -> PriceSnapshot:
        self.ensure_capability("price_reading")
        book = self.get_orderbook(contract_id)
        bid = book.bids[0].price if book.bids else None
        ask = book.asks[0].price if book.asks else None
        midpoint = (bid + ask) / 2.0 if bid is not None and ask is not None else None
        quote = book.raw.get("quote") if isinstance(book.raw.get("quote"), Mapping) else {}
        last = self._probability(self._value(quote, "last_executed_price", "lastExecutedPrice", "last_price"))
        if last is None:
            last = midpoint or bid or ask
        return PriceSnapshot(
            market_id=self.market_id,
            contract_id=book.contract_id,
            last=last,
            bid=bid,
            ask=ask,
            midpoint=midpoint,
            source="smarkets_v3_quotes",
            raw=book.raw,
        )

    def place_paper_order(self, order: PaperOrderRequest) -> PaperOrderResult:
        self.ensure_capability("paper_trading")
        market_id, contract_id = self._validate_order(order)
        return PaperOrderResult(
            market_id=self.market_id,
            contract_id=self._contract_id(market_id, contract_id),
            accepted=True,
            message=(
                f"DRY RUN: would place Smarkets {str(order.side).upper()} "
                f"for {float(order.size):.4f} stake"
                + (f" at probability {float(order.limit_price):.4f}" if order.limit_price is not None else "")
            ),
            raw={"request": self._order_payload(order, signed=False), "dry_run": True},
        )

    def place_live_order(self, order: PaperOrderRequest) -> Dict[str, Any]:
        self.ensure_capability("live_trading")
        market_id, contract_id = self._validate_order(order)
        preflight = self.preflight_live_order(order)
        payload = self._order_payload(order, signed=False)
        response = self._request_json("POST", "/orders/", payload, auth=True)
        return {
            "market_id": self.market_id,
            "contract_id": self._contract_id(market_id, contract_id),
            "live": True,
            "preflight": preflight,
            "request": payload,
            "response": response,
        }

    def copy_trade_from_activity(self, activity: Mapping[str, Any]) -> PaperOrderResult:
        raise UnsupportedFeatureError(
            self.market_id,
            "copy_trading",
            "Smarkets copy trading is unsupported because account activity mirroring is not an official adapter feature.",
        )

    def _get(self, path: str, *, params: Optional[Mapping[str, Any]]) -> Any:
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
        auth: bool,
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
            "smarkets_session_token",
            ("SMARKETS_SESSION_TOKEN", "SMARKETS_API_TOKEN"),
            required=required,
            label="SMARKETS_SESSION_TOKEN",
        )
        return {"Authorization": f"Session-Token {credential.value}"} if credential else {}

    def _validate_order(self, order: PaperOrderRequest) -> Tuple[str, str]:
        self.ensure_order_market(order)
        market_id, contract_id = self._split_contract_id(order.contract_id)
        if str(order.side or "").upper() not in self.live_order_sides:
            raise MarketConfigurationError("Smarkets order side must be BUY or SELL.")
        try:
            size = float(order.size)
        except (TypeError, ValueError) as exc:
            raise MarketConfigurationError("Smarkets order quantity must be numeric.") from exc
        if not math.isfinite(size) or size <= 0:
            raise MarketConfigurationError("Smarkets order quantity must be positive and finite.")
        if order.limit_price is not None and self._probability(order.limit_price) is None:
            raise MarketConfigurationError("Smarkets order price must be between 0 and 1.")
        return market_id, contract_id

    def _order_payload(self, order: PaperOrderRequest, *, signed: bool) -> Dict[str, Any]:
        existing = order.metadata.get("smarkets_order")
        if isinstance(existing, Mapping):
            return dict(existing)
        market_id, contract_id = self._split_contract_id(order.contract_id)
        probability = self._probability(order.limit_price)
        if probability is None:
            raise MarketConfigurationError("Smarkets live/paper order requires a limit probability.")
        price_scale = self._positive_scale("smarkets_price_scale", 10_000.0)
        quantity_scale = self._positive_scale("smarkets_quantity_scale", 10_000.0)
        return {
            "market_id": market_id,
            "contract_id": contract_id,
            "side": "buy" if str(order.side).upper() == "BUY" else "sell",
            "quantity": str(round(float(order.size) * quantity_scale)),
            "price": str(round(probability * price_scale)),
            "type": str(order.metadata.get("order_type") or "limit").lower(),
        }

    @classmethod
    def _contracts_from_rows(
        cls,
        event_id: str,
        market: Mapping[str, Any],
        rows: List[Mapping[str, Any]],
    ) -> List[MarketContract]:
        market_id = cls._id(market, "market_id")
        title = str(cls._value(market, "name", "title", "market_name") or market_id)
        status = str(cls._value(market, "state", "status") or "").lower()
        contracts: List[MarketContract] = []
        for row in rows:
            contract_id = cls._id(row, "contract_id")
            if not contract_id:
                continue
            outcome = str(cls._value(row, "name", "title", "contract_name", "label") or contract_id)
            contracts.append(
                MarketContract(
                    market_id=cls.metadata.market_id,
                    contract_id=cls._contract_id(market_id, contract_id),
                    event_id=event_id,
                    title=f"{title} - {outcome}",
                    outcome=outcome,
                    url=str(cls._value(market, "url", "slug") or market_id),
                    status=status,
                    raw={"market": dict(market), "contract": dict(row)},
                )
            )
        return contracts

    def _event_from_payload(self, event: Mapping[str, Any]) -> MarketEvent:
        event_id = self._id(event, "event_id")
        return MarketEvent(
            market_id=self.market_id,
            event_id=event_id,
            title=str(self._value(event, "name", "title", "description") or event_id),
            url=str(self._value(event, "url", "slug") or event_id),
            status=str(self._value(event, "state", "status") or "").lower(),
            raw=dict(event),
        )

    @classmethod
    def _quote_for_contract(cls, payload: Any, contract_id: str) -> Mapping[str, Any]:
        rows = cls._rows(payload, "quotes", "data")
        if not rows:
            mapping = cls._mapping_payload(payload)
            for key, value in mapping.items():
                if str(key) == contract_id and isinstance(value, Mapping):
                    return value
        for row in rows:
            if cls._id(row, "contract_id") == contract_id:
                return row
        return rows[0] if len(rows) == 1 else {}

    @classmethod
    def _levels(cls, value: Any, *, reverse: bool) -> List[OrderBookLevel]:
        if isinstance(value, Mapping):
            value = value.get("levels") or value.get("offers") or value.get("orders") or []
        if not isinstance(value, list):
            return []
        levels: List[OrderBookLevel] = []
        for row in value:
            if isinstance(row, Mapping):
                price_value = cls._value(row, "price", "odds", "rate")
                size_value = cls._value(row, "quantity", "size", "amount", "stake", "volume")
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
        levels.sort(key=lambda level: level.price, reverse=reverse)
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
            if isinstance(rows, Mapping):
                return [dict(value, **({"id": key_id} if isinstance(value, Mapping) else {})) for key_id, value in rows.items() if isinstance(value, Mapping)]
        return []

    @classmethod
    def _search_text(cls, payload: Mapping[str, Any]) -> str:
        values = [
            payload.get("id"),
            payload.get("event_id"),
            payload.get("name"),
            payload.get("title"),
            payload.get("description"),
            payload.get("slug"),
        ]
        return " ".join(str(value or "") for value in values).lower()

    @staticmethod
    def _id(payload: Mapping[str, Any], *aliases: str) -> str:
        for key in ("id", *aliases):
            value = payload.get(key)
            if value not in (None, ""):
                return str(value).strip()
        return ""

    @staticmethod
    def _value(payload: Mapping[str, Any], *keys: str) -> Any:
        for key in keys:
            value = payload.get(key)
            if value not in (None, ""):
                return value
        return None

    @staticmethod
    def _probability(value: Any) -> Optional[float]:
        if value in (None, ""):
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(number):
            return None
        if number > 1.0:
            number /= 10_000.0
        return number if 0.0 <= number <= 1.0 else None

    def _positive_scale(self, key: str, default: float) -> float:
        value = self.config.get(key, default)
        try:
            value = float(value)
        except (TypeError, ValueError) as exc:
            raise MarketConfigurationError(f"Smarkets config {key} must be numeric.") from exc
        if not math.isfinite(value) or value <= 0:
            raise MarketConfigurationError(f"Smarkets config {key} must be greater than 0.")
        return value

    @staticmethod
    def _required_id(value: Any, label: str) -> str:
        clean = str(value or "").strip()
        if not clean:
            raise MarketConfigurationError(f"Smarkets {label} id cannot be empty.")
        return clean

    @staticmethod
    def _contract_id(market_id: str, contract_id: str) -> str:
        return f"{market_id}:{contract_id}"

    @staticmethod
    def _split_contract_id(contract_id: str) -> Tuple[str, str]:
        parts = [part.strip() for part in str(contract_id or "").split(":")]
        if len(parts) != 2 or any(not part for part in parts):
            raise MarketConfigurationError("Smarkets contract id must be MARKET_ID:CONTRACT_ID.")
        return parts[0], parts[1]

    @staticmethod
    def _url(base: str, path: str) -> str:
        return f"{base.rstrip('/')}/{str(path or '').strip('/')}/"
