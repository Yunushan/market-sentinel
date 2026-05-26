from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping, Optional, Tuple

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


DEFAULT_OPINION_BASE_URL = "https://openapi.opinion.trade/openapi"
OPINION_REFERENCES = (
    "https://docs.opinion.trade/developer-guide/opinion-open-api/overview",
    "https://docs.opinion.trade/developer-guide/opinion-open-api/market",
    "https://docs.opinion.trade/developer-guide/opinion-open-api/token",
)


class OpinionAdapter(MarketAdapter):
    """Opinion Labs read-only adapter using the documented Opinion OpenAPI."""

    metadata = get_market_metadata("opinion_labs")

    def health_check(self) -> Dict[str, Any]:
        health = super().health_check()
        api_key = self.resolve_credential("opinion_api_key", ("OPINION_API_KEY",), label="OPINION_API_KEY")
        health.update(
            {
                "api_base_url": self.api_base_url,
                "references": list(OPINION_REFERENCES),
                "credential_sources": [{"name": api_key.name, "source": api_key.source}] if api_key else [],
                "live_trading_supported": False,
            }
        )
        return health

    @property
    def api_base_url(self) -> str:
        configured = self.config.get("opinion_api_base_url") or self.config.get("api_base_url")
        return str(configured or DEFAULT_OPINION_BASE_URL).rstrip("/")

    def list_events(self, query: str = "", limit: int = 50) -> List[MarketEvent]:
        self.ensure_capability("event_listing")
        desired = max(1, min(int(limit or 50), 20))
        params: Dict[str, Any] = {
            "page": 1,
            "limit": desired,
            "marketType": int(self.config.get("opinion_market_type", 2)),
            "sortBy": int(self.config.get("opinion_sort_by", 5)),
        }
        status = str(self.config.get("opinion_market_status") or "activated").strip()
        if status:
            params["status"] = status
        payload = self._get("/market", params=params)
        markets = self._result_list(payload)
        q = str(query or "").strip().lower()
        if q:
            markets = [market for market in markets if q in self._search_text(market)]
        return [self._event_from_market(market) for market in markets[:desired]]

    def list_contracts(self, event_id: str) -> List[MarketContract]:
        self.ensure_capability("event_listing")
        market = self._get_market(event_id)
        return self._contracts_from_market(market)

    def get_price(self, contract_id: str) -> PriceSnapshot:
        self.ensure_capability("price_reading")
        market_id, outcome, token_id = self._split_contract_id(contract_id)
        payload = self._get("/token/latest-price", params={"token_id": token_id})
        result = self._result_mapping(payload)
        price = self._safe_probability(result.get("price"))
        return PriceSnapshot(
            market_id=self.market_id,
            contract_id=self._contract_id(market_id, outcome, token_id),
            last=price,
            bid=None,
            ask=None,
            midpoint=price,
            source="opinion_latest_price",
            raw=result,
        )

    def get_orderbook(self, contract_id: str) -> OrderBookSnapshot:
        self.ensure_capability("orderbook_reading")
        market_id, outcome, token_id = self._split_contract_id(contract_id)
        payload = self._get("/token/orderbook", params={"token_id": token_id})
        result = self._result_mapping(payload)
        bids = self._levels(self._value_at(result, "bids", "buy"), descending=True)
        asks = self._levels(self._value_at(result, "asks", "sell"))
        return OrderBookSnapshot(
            market_id=self.market_id,
            contract_id=self._contract_id(market_id, outcome, token_id),
            bids=bids,
            asks=asks,
            raw=result,
        )

    def place_paper_order(self, order: PaperOrderRequest) -> PaperOrderResult:
        self.ensure_capability("paper_trading")
        self._validate_order(order)
        market_id, outcome, token_id = self._split_contract_id(order.contract_id)
        return PaperOrderResult(
            market_id=self.market_id,
            contract_id=self._contract_id(market_id, outcome, token_id),
            accepted=True,
            message=(
                f"DRY RUN: would place Opinion {order.side.upper()} "
                f"for {order.size:.4f} {outcome} shares"
                + (f" at limit {order.limit_price:.2f}" if order.limit_price is not None else "")
            ),
            raw={"market_id": market_id, "outcome": outcome, "token_id": token_id},
        )

    def place_live_order(self, order: PaperOrderRequest) -> Dict[str, Any]:
        raise UnsupportedFeatureError(
            self.market_id,
            "live_trading",
            "Opinion live trading requires the separate Opinion CLOB SDK and wallet/order signing.",
        )

    def copy_trade_from_activity(self, activity: Mapping[str, Any]) -> PaperOrderResult:
        raise UnsupportedFeatureError(
            self.market_id,
            "copy_trading",
            "Opinion copy trading is unsupported because this adapter only uses public market-data endpoints.",
        )

    def _get_market(self, market_id: str) -> Mapping[str, Any]:
        clean = str(market_id or "").strip()
        if not clean:
            raise MarketConfigurationError("Opinion market id cannot be empty.")
        payload = self._get(f"/market/{clean}")
        result = self._result_mapping(payload)
        data = result.get("data")
        return data if isinstance(data, Mapping) else result

    def _get(self, path: str, *, params: Optional[Mapping[str, Any]] = None) -> Any:
        return self.runtime.get_json(self._url(path), params=params, headers=self._headers(required=True))

    def _url(self, path: str) -> str:
        clean_path = "/" + str(path or "").strip("/")
        return f"{self.api_base_url}{clean_path}"

    def _headers(self, *, required: bool = False) -> Dict[str, str]:
        credential = self.resolve_credential(
            "opinion_api_key",
            ("OPINION_API_KEY",),
            required=required,
            label="OPINION_API_KEY",
        )
        return {"apikey": credential.value} if credential else {}

    def _event_from_market(self, market: Mapping[str, Any]) -> MarketEvent:
        market_id = self._market_id(market)
        return MarketEvent(
            market_id=self.market_id,
            event_id=market_id,
            title=str(market.get("marketTitle") or market.get("title") or market_id),
            url=self._market_url(market),
            status=str(market.get("statusEnum") or market.get("status") or "").strip().lower(),
            raw=dict(market),
        )

    def _contracts_from_market(self, market: Mapping[str, Any]) -> List[MarketContract]:
        parent_id = self._market_id(market)
        title = str(market.get("marketTitle") or market.get("title") or parent_id)
        markets = self._child_markets(market) or [market]
        contracts: List[MarketContract] = []
        for child in markets:
            market_id = self._market_id(child) or parent_id
            child_title = str(child.get("marketTitle") or child.get("title") or title)
            status = str(child.get("statusEnum") or child.get("status") or market.get("statusEnum") or "").strip().lower()
            for outcome, token_key, label_key in (
                ("YES", "yesTokenId", "yesLabel"),
                ("NO", "noTokenId", "noLabel"),
            ):
                token_id = str(child.get(token_key) or "").strip()
                if not token_id:
                    continue
                label = str(child.get(label_key) or outcome.title())
                contracts.append(
                    MarketContract(
                        market_id=self.market_id,
                        contract_id=self._contract_id(market_id, outcome, token_id),
                        event_id=parent_id or market_id,
                        title=f"{child_title} - {label}",
                        outcome=label,
                        url=self._market_url(child),
                        status=status,
                        raw={"market": dict(market), "child": dict(child), "outcome": outcome, "token_id": token_id},
                    )
                )
        return contracts

    def _validate_order(self, order: PaperOrderRequest) -> None:
        self.ensure_order_market(order)
        self._split_contract_id(order.contract_id)
        if str(order.side or "").upper() not in {"BUY", "SELL"}:
            raise MarketConfigurationError("Opinion paper order side must be BUY or SELL.")
        if not self._is_positive_number(order.size):
            raise MarketConfigurationError("Opinion paper order size must be positive.")
        if order.limit_price is not None and self._safe_probability(order.limit_price) is None:
            raise MarketConfigurationError("Opinion paper order limit price must be between 0 and 1.")

    @staticmethod
    def _result_mapping(payload: Any) -> Mapping[str, Any]:
        if not isinstance(payload, Mapping):
            return {}
        result = payload.get("result")
        if isinstance(result, Mapping):
            return result
        return payload

    @staticmethod
    def _result_list(payload: Any) -> List[Mapping[str, Any]]:
        result = OpinionAdapter._result_mapping(payload)
        value = result.get("list") or result.get("data") or result.get("markets")
        return [item for item in value if isinstance(item, Mapping)] if isinstance(value, list) else []

    @staticmethod
    def _child_markets(market: Mapping[str, Any]) -> List[Mapping[str, Any]]:
        children = market.get("childMarkets")
        return [child for child in children if isinstance(child, Mapping)] if isinstance(children, list) else []

    @staticmethod
    def _market_id(market: Mapping[str, Any]) -> str:
        return str(market.get("marketId") or market.get("id") or "").strip()

    @staticmethod
    def _contract_id(market_id: str, outcome: str, token_id: str) -> str:
        return f"{market_id}:{outcome.upper()}:{token_id}"

    @staticmethod
    def _split_contract_id(contract_id: str) -> Tuple[str, str, str]:
        parts = [part.strip() for part in str(contract_id or "").split(":")]
        if len(parts) != 3 or not all(parts):
            raise MarketConfigurationError("Opinion contract id must be MARKET_ID:YES|NO:TOKEN_ID.")
        outcome = parts[1].upper()
        if outcome not in {"YES", "NO"}:
            raise MarketConfigurationError("Opinion contract outcome must be YES or NO.")
        return parts[0], outcome, parts[2]

    @staticmethod
    def _market_url(market: Mapping[str, Any]) -> str:
        raw = str(market.get("url") or "").strip()
        if raw:
            return raw
        market_id = OpinionAdapter._market_id(market)
        return f"https://opinion.trade/market/{market_id}" if market_id else "https://opinion.trade"

    @staticmethod
    def _search_text(market: Mapping[str, Any]) -> str:
        values = [market.get("marketId"), market.get("marketTitle"), market.get("title"), market.get("rules")]
        return " ".join(str(value or "") for value in values).lower()

    @staticmethod
    def _value_at(data: Mapping[str, Any], *keys: str) -> Any:
        for key in keys:
            value = data.get(key)
            if value is not None:
                return value
        orderbook = data.get("orderbook")
        if isinstance(orderbook, Mapping):
            for key in keys:
                value = orderbook.get(key)
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
                size = item.get("size") or item.get("shares") or item.get("amount")
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                price, size = item[0], item[1]
            parsed_price = OpinionAdapter._safe_probability(price)
            try:
                parsed_size = float(size)
            except (TypeError, ValueError):
                continue
            if parsed_price is not None and OpinionAdapter._is_positive_number(parsed_size):
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
    def _is_positive_number(value: Any) -> bool:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return False
        return math.isfinite(number) and number > 0
