from __future__ import annotations

import hashlib
import hmac
import json
import math
import time
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


DEFAULT_XO_BASE_URL = "https://api.xotrade.co/v1"
XO_REFERENCES = (
    "https://xotrade.co/documentation.html",
    "https://xotrade.co",
)


class XOMarketAdapter(MarketAdapter):
    """XO Markets adapter using the documented HMAC REST API."""

    metadata = get_market_metadata("xo_market")

    def health_check(self) -> Dict[str, Any]:
        health = super().health_check()
        api_key = self.resolve_credential("xo_api_key", ("XO_API_KEY",), label="XO_API_KEY")
        api_secret = self.resolve_credential("xo_api_secret", ("XO_API_SECRET",), label="XO_API_SECRET")
        credential_sources = []
        for credential in (api_key, api_secret):
            if credential:
                credential_sources.append({"name": credential.name, "source": credential.source})
        health.update(
            {
                "api_base_url": self.api_base_url,
                "references": list(XO_REFERENCES),
                "live_trading_enabled": self.config_bool("live_trading_enabled", False),
                "credential_sources": credential_sources,
            }
        )
        return health

    @property
    def api_base_url(self) -> str:
        configured = self.config.get("xo_api_base_url") or self.config.get("api_base_url")
        return str(configured or DEFAULT_XO_BASE_URL).rstrip("/")

    def list_events(self, query: str = "", limit: int = 50) -> List[MarketEvent]:
        self.ensure_capability("event_listing")
        desired = max(1, min(int(limit or 50), 500))
        params: Dict[str, Any] = {"limit": desired, "status": self.config.get("xo_market_status", "open")}
        if query:
            params["search"] = str(query).strip()
        payload = self._request("GET", "/markets", params=params)
        markets = self._list_from_payload(payload, "markets", "data")
        return [self._event_from_market(market) for market in markets[:desired]]

    def list_contracts(self, event_id: str) -> List[MarketContract]:
        self.ensure_capability("event_listing")
        market = self._get_market(event_id)
        return self._contracts_from_market(market)

    def get_orderbook(self, contract_id: str) -> OrderBookSnapshot:
        self.ensure_capability("orderbook_reading")
        market_id, outcome_id = self._split_contract_id(contract_id)
        payload = self._request("GET", f"/markets/{market_id}/outcomes/{outcome_id}/orderbook")
        book = payload.get("orderbook") if isinstance(payload, Mapping) else None
        if not isinstance(book, Mapping):
            book = payload if isinstance(payload, Mapping) else {}
        bids = self._levels(self._value_at(book, "bids", "buy"), descending=True)
        asks = self._levels(self._value_at(book, "asks", "sell"))
        return OrderBookSnapshot(
            market_id=self.market_id,
            contract_id=self._contract_id(market_id, outcome_id),
            bids=bids,
            asks=asks,
            raw=dict(book),
        )

    def get_price(self, contract_id: str) -> PriceSnapshot:
        self.ensure_capability("price_reading")
        market_id, outcome_id = self._split_contract_id(contract_id)
        orderbook = self.get_orderbook(self._contract_id(market_id, outcome_id))
        bid = orderbook.bids[0].price if orderbook.bids else None
        ask = orderbook.asks[0].price if orderbook.asks else None
        midpoint = (bid + ask) / 2.0 if bid is not None and ask is not None else None
        last = midpoint
        raw: Dict[str, Any] = dict(orderbook.raw)
        if last is None:
            market = self._get_market(market_id)
            outcome = self._find_outcome(market, outcome_id)
            last = self._safe_probability(outcome.get("price")) if outcome else None
            raw["market"] = dict(market)
        return PriceSnapshot(
            market_id=self.market_id,
            contract_id=self._contract_id(market_id, outcome_id),
            last=last,
            bid=bid,
            ask=ask,
            midpoint=midpoint,
            source="xo_orderbook",
            raw=raw,
        )

    def place_paper_order(self, order: PaperOrderRequest) -> PaperOrderResult:
        self.ensure_capability("paper_trading")
        self._validate_order(order)
        payload = self._order_payload(order)
        return PaperOrderResult(
            market_id=self.market_id,
            contract_id=order.contract_id,
            accepted=True,
            message=(
                f"DRY RUN: would place XO {order.side.upper()} order for "
                f"${order.size:.2f}"
                + (f" at limit {order.limit_price:.2f}" if order.limit_price is not None else "")
            ),
            raw={"request": payload},
        )

    def place_live_order(self, order: PaperOrderRequest) -> Dict[str, Any]:
        self.ensure_capability("live_trading")
        self._validate_order(order)
        self.ensure_live_trading_enabled()
        payload = self._order_payload(order)
        response = self._request("POST", "/orders", json_body=payload)
        return {"market_id": self.market_id, "contract_id": order.contract_id, "live": True, "response": response}

    def copy_trade_from_activity(self, activity: Mapping[str, Any]) -> PaperOrderResult:
        raise UnsupportedFeatureError(
            self.market_id,
            "copy_trading",
            "XO copy trading is unsupported because this adapter does not implement account activity mirroring.",
        )

    def _get_market(self, market_id: str) -> Mapping[str, Any]:
        clean = str(market_id or "").strip()
        if not clean:
            raise MarketConfigurationError("XO market id cannot be empty.")
        payload = self._request("GET", f"/markets/{clean}")
        market = payload.get("market") if isinstance(payload, Mapping) else None
        if isinstance(market, Mapping):
            return market
        if isinstance(payload, Mapping):
            return payload
        raise MarketConfigurationError(f"XO market {clean!r} was not found.")

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Mapping[str, Any]] = None,
        json_body: Any = None,
    ) -> Any:
        body = "" if json_body is None else self._canonical_json(json_body)
        headers = self._auth_headers(method, path, body)
        if json_body is None:
            return self.runtime.get_json(self._url(path), params=params, headers=headers)
        headers["Content-Type"] = "application/json"
        self.runtime.rate_limiter.wait()
        try:
            response = self.runtime.session.request(
                method.upper(),
                self._url(path),
                params=dict(params or {}),
                data=body,
                headers=headers,
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

    def _url(self, path: str) -> str:
        clean_path = "/" + str(path or "").strip("/")
        return f"{self.api_base_url}{clean_path}"

    def _auth_headers(self, method: str, path: str, body: str = "") -> Dict[str, str]:
        api_key = self.resolve_credential("xo_api_key", ("XO_API_KEY",), required=True, label="XO_API_KEY")
        api_secret = self.resolve_credential("xo_api_secret", ("XO_API_SECRET",), required=True, label="XO_API_SECRET")
        timestamp = str(int(time.time()))
        request_path = "/" + str(path or "").strip("/")
        message = f"{timestamp}{method.upper()}{request_path}{body}"
        signature = hmac.new(api_secret.value.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()
        return {
            "XO-API-KEY": api_key.value,
            "XO-TIMESTAMP": timestamp,
            "XO-SIGNATURE": signature,
        }

    def _event_from_market(self, market: Mapping[str, Any]) -> MarketEvent:
        market_id = self._market_id(market)
        return MarketEvent(
            market_id=self.market_id,
            event_id=market_id,
            title=str(market.get("title") or market.get("name") or market_id),
            url=self._market_url(market),
            status=str(market.get("status") or "").strip().lower(),
            raw=dict(market),
        )

    def _contracts_from_market(self, market: Mapping[str, Any]) -> List[MarketContract]:
        market_id = self._market_id(market)
        title = str(market.get("title") or market.get("name") or market_id)
        contracts: List[MarketContract] = []
        for outcome in self._outcomes_from_market(market):
            outcome_id = self._outcome_id(outcome)
            if not outcome_id:
                continue
            name = str(outcome.get("name") or outcome.get("title") or outcome_id)
            contracts.append(
                MarketContract(
                    market_id=self.market_id,
                    contract_id=self._contract_id(market_id, outcome_id),
                    event_id=market_id,
                    title=f"{title} - {name}",
                    outcome=name,
                    url=self._market_url(market),
                    status=str(market.get("status") or "").strip().lower(),
                    raw={"market": dict(market), "outcome": dict(outcome)},
                )
            )
        return contracts

    def _order_payload(self, order: PaperOrderRequest) -> Dict[str, Any]:
        market_id, outcome_id = self._split_contract_id(order.contract_id)
        payload: Dict[str, Any] = {
            "market_id": market_id,
            "outcome_id": outcome_id,
            "side": str(order.side or "").lower(),
            "type": str(order.metadata.get("type") or ("limit" if order.limit_price is not None else "market")),
            "amount_usd": float(order.size),
            "time_in_force": str(order.metadata.get("time_in_force") or "GTC"),
        }
        if order.limit_price is not None:
            payload["limit_price"] = self._limit_probability(order.limit_price)
        if "client_order_id" in order.metadata:
            payload["client_order_id"] = str(order.metadata["client_order_id"])
        return payload

    def _validate_order(self, order: PaperOrderRequest) -> None:
        self.ensure_order_market(order)
        self._split_contract_id(order.contract_id)
        if str(order.side or "").upper() not in {"BUY", "SELL"}:
            raise MarketConfigurationError("XO order side must be BUY or SELL.")
        if not self._is_positive_number(order.size):
            raise MarketConfigurationError("XO order amount_usd must be positive.")
        if order.limit_price is not None:
            self._limit_probability(order.limit_price)

    @staticmethod
    def _market_id(market: Mapping[str, Any]) -> str:
        return str(market.get("id") or market.get("market_id") or "").strip()

    @staticmethod
    def _outcome_id(outcome: Mapping[str, Any]) -> str:
        return str(outcome.get("id") or outcome.get("outcome_id") or "").strip()

    @staticmethod
    def _contract_id(market_id: str, outcome_id: str) -> str:
        return f"{market_id}:{outcome_id}"

    @staticmethod
    def _split_contract_id(contract_id: str) -> Tuple[str, str]:
        parts = [part.strip() for part in str(contract_id or "").split(":")]
        if len(parts) != 2 or not parts[0] or not parts[1]:
            raise MarketConfigurationError("XO contract id must be MARKET_ID:OUTCOME_ID.")
        return parts[0], parts[1]

    @staticmethod
    def _outcomes_from_market(market: Mapping[str, Any]) -> List[Mapping[str, Any]]:
        outcomes = market.get("outcomes")
        return [outcome for outcome in outcomes if isinstance(outcome, Mapping)] if isinstance(outcomes, list) else []

    @staticmethod
    def _find_outcome(market: Mapping[str, Any], outcome_id: str) -> Optional[Mapping[str, Any]]:
        for outcome in XOMarketAdapter._outcomes_from_market(market):
            if XOMarketAdapter._outcome_id(outcome) == str(outcome_id):
                return outcome
        return None

    @staticmethod
    def _list_from_payload(payload: Any, *keys: str) -> List[Mapping[str, Any]]:
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, Mapping)]
        if isinstance(payload, Mapping):
            for key in keys:
                value = payload.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, Mapping)]
        return []

    @staticmethod
    def _market_url(market: Mapping[str, Any]) -> str:
        raw = str(market.get("url") or "").strip()
        if raw:
            return raw
        market_id = XOMarketAdapter._market_id(market)
        return f"https://app.xotrade.co/markets/{market_id}" if market_id else "https://xotrade.co"

    @staticmethod
    def _value_at(data: Mapping[str, Any], *keys: str) -> Any:
        for key in keys:
            value = data.get(key)
            if value is not None:
                return value
        return []

    @staticmethod
    def _levels(raw: Any, *, descending: bool = False) -> List[OrderBookLevel]:
        levels: List[OrderBookLevel] = []
        if not isinstance(raw, list):
            return levels
        for item in raw:
            price = size = None
            if isinstance(item, Mapping):
                price = item.get("price")
                size = item.get("size") or item.get("qty") or item.get("quantity") or item.get("amount")
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                price, size = item[0], item[1]
            parsed_price = XOMarketAdapter._safe_probability(price)
            try:
                parsed_size = float(size)
            except (TypeError, ValueError):
                continue
            if parsed_price is not None and XOMarketAdapter._is_positive_number(parsed_size):
                levels.append(OrderBookLevel(price=parsed_price, size=parsed_size))
        levels.sort(key=lambda level: level.price, reverse=descending)
        return levels

    @staticmethod
    def _safe_probability(value: Any) -> Optional[float]:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(number):
            return None
        if number > 1.0 and number <= 100.0:
            number /= 100.0
        return number if 0.0 <= number <= 1.0 else None

    @staticmethod
    def _limit_probability(value: Any) -> float:
        probability = XOMarketAdapter._safe_probability(value)
        if probability is None or probability <= 0.0 or probability >= 1.0:
            raise MarketConfigurationError("XO limit price must be between 0 and 1.")
        return probability

    @staticmethod
    def _is_positive_number(value: Any) -> bool:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return False
        return math.isfinite(number) and number > 0

    @staticmethod
    def _canonical_json(data: Mapping[str, Any]) -> str:
        return json.dumps(data, separators=(",", ":"), sort_keys=True)
