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


DEFAULT_PREDICT_FUN_BASE_URL = "https://api.predict.fun/v1"
PREDICT_FUN_REFERENCES = (
    "https://docs.predict.fun/developers/predict-rest-api",
    "https://dev.predict.fun/get-markets-25326905e0",
    "https://dev.predict.fun/get-the-orderbook-for-a-market-25326908e0",
)


class PredictFunAdapter(MarketAdapter):
    """Predict.fun adapter using the documented REST API for market data."""

    metadata = get_market_metadata("predict_fun")

    def health_check(self) -> Dict[str, Any]:
        health = super().health_check()
        api_key = self.resolve_credential("predict_fun_api_key", ("PREDICT_FUN_API_KEY",), label="PREDICT_FUN_API_KEY")
        health.update(
            {
                "api_base_url": self.api_base_url,
                "references": list(PREDICT_FUN_REFERENCES),
                "credential_sources": [{"name": api_key.name, "source": api_key.source}] if api_key else [],
                "live_trading_supported": False,
            }
        )
        return health

    @property
    def api_base_url(self) -> str:
        configured = self.config.get("predict_fun_api_base_url") or self.config.get("api_base_url")
        return str(configured or DEFAULT_PREDICT_FUN_BASE_URL).rstrip("/")

    def list_events(self, query: str = "", limit: int = 50) -> List[MarketEvent]:
        self.ensure_capability("event_listing")
        desired = max(1, min(int(limit or 50), 100))
        params: Dict[str, Any] = {"first": desired}
        status = str(self.config.get("predict_fun_market_status") or "").strip()
        if status:
            params["status"] = status
        payload = self._get("/markets", params=params)
        markets = self._list_from_payload(payload, "data", "markets")
        q = str(query or "").strip().lower()
        if q:
            markets = [market for market in markets if q in self._search_text(market)]
        return [self._event_from_market(market) for market in markets[:desired]]

    def list_contracts(self, event_id: str) -> List[MarketContract]:
        self.ensure_capability("event_listing")
        market = self._get_market(event_id)
        return self._contracts_from_market(market)

    def get_orderbook(self, contract_id: str) -> OrderBookSnapshot:
        self.ensure_capability("orderbook_reading")
        market_id, outcome = self._split_contract_id(contract_id)
        payload = self._get(f"/markets/{market_id}/orderbook")
        data = payload.get("data") if isinstance(payload, Mapping) else payload
        orderbook = data if isinstance(data, Mapping) else {}
        yes_bids = self._levels(orderbook.get("bids"), descending=True)
        yes_asks = self._levels(orderbook.get("asks"))
        if outcome == "YES":
            bids, asks = yes_bids, yes_asks
        else:
            bids = self._opposite_bids_from_yes_asks(yes_asks)
            asks = self._opposite_asks_from_yes_bids(yes_bids)
        return OrderBookSnapshot(
            market_id=self.market_id,
            contract_id=self._contract_id(market_id, outcome),
            bids=bids,
            asks=asks,
            raw=orderbook,
        )

    def get_price(self, contract_id: str) -> PriceSnapshot:
        self.ensure_capability("price_reading")
        market_id, outcome = self._split_contract_id(contract_id)
        orderbook = self.get_orderbook(self._contract_id(market_id, outcome))
        bid = orderbook.bids[0].price if orderbook.bids else None
        ask = orderbook.asks[0].price if orderbook.asks else None
        midpoint = (bid + ask) / 2.0 if bid is not None and ask is not None else None
        last = midpoint
        raw: Dict[str, Any] = dict(orderbook.raw)
        if last is None:
            market = self._get_market(market_id)
            last = self._price_from_market(market, outcome)
            raw["market"] = dict(market)
        return PriceSnapshot(
            market_id=self.market_id,
            contract_id=self._contract_id(market_id, outcome),
            last=last,
            bid=bid,
            ask=ask,
            midpoint=midpoint,
            source="predict_fun_orderbook",
            raw=raw,
        )

    def place_paper_order(self, order: PaperOrderRequest) -> PaperOrderResult:
        self.ensure_capability("paper_trading")
        self._validate_order(order)
        market_id, outcome = self._split_contract_id(order.contract_id)
        return PaperOrderResult(
            market_id=self.market_id,
            contract_id=self._contract_id(market_id, outcome),
            accepted=True,
            message=(
                f"DRY RUN: would place Predict.fun {order.side.upper()} "
                f"for {order.size:.4f} {outcome} shares"
                + (f" at limit {order.limit_price:.2f}" if order.limit_price is not None else "")
            ),
            raw={"market_id": market_id, "outcome": outcome},
        )

    def place_live_order(self, order: PaperOrderRequest) -> Dict[str, Any]:
        raise UnsupportedFeatureError(
            self.market_id,
            "live_trading",
            "Predict.fun live trading requires authenticated wallet signing/SDK flows and is not implemented.",
        )

    def copy_trade_from_activity(self, activity: Mapping[str, Any]) -> PaperOrderResult:
        raise UnsupportedFeatureError(
            self.market_id,
            "copy_trading",
            "Predict.fun copy trading is unsupported because this adapter does not mirror account activity.",
        )

    def _get_market(self, market_id: str) -> Mapping[str, Any]:
        clean = str(market_id or "").strip()
        if not clean:
            raise MarketConfigurationError("Predict.fun market id cannot be empty.")
        payload = self._get(f"/markets/{clean}")
        data = payload.get("data") if isinstance(payload, Mapping) else None
        if isinstance(data, Mapping):
            return data
        if isinstance(payload, Mapping):
            return payload
        raise MarketConfigurationError(f"Predict.fun market {clean!r} was not found.")

    def _get(self, path: str, *, params: Optional[Mapping[str, Any]] = None) -> Any:
        return self.runtime.get_json(self._url(path), params=params, headers=self._headers())

    def _url(self, path: str) -> str:
        clean_path = "/" + str(path or "").strip("/")
        return f"{self.api_base_url}{clean_path}"

    def _headers(self) -> Dict[str, str]:
        required = "api-testnet.predict.fun" not in self.api_base_url and "api-sepolia.predict.fun" not in self.api_base_url
        credential = self.resolve_credential(
            "predict_fun_api_key",
            ("PREDICT_FUN_API_KEY",),
            required=required,
            label="PREDICT_FUN_API_KEY",
        )
        return {"x-api-key": credential.value} if credential else {}

    def _event_from_market(self, market: Mapping[str, Any]) -> MarketEvent:
        market_id = self._market_id(market)
        return MarketEvent(
            market_id=self.market_id,
            event_id=market_id,
            title=str(market.get("title") or market.get("question") or market_id),
            url=self._market_url(market),
            status=self._status_from_market(market),
            raw=dict(market),
        )

    def _contracts_from_market(self, market: Mapping[str, Any]) -> List[MarketContract]:
        market_id = self._market_id(market)
        title = str(market.get("title") or market.get("question") or market_id)
        contracts: List[MarketContract] = []
        outcomes = self._outcomes_from_market(market)
        if not outcomes:
            outcomes = [{"name": "Yes", "side": "YES"}, {"name": "No", "side": "NO"}]
        for idx, outcome_payload in enumerate(outcomes):
            label = str(
                outcome_payload.get("name")
                or outcome_payload.get("title")
                or outcome_payload.get("label")
                or outcome_payload.get("side")
                or f"Outcome {idx + 1}"
            )
            outcome = "NO" if label.strip().lower() == "no" or str(outcome_payload.get("side")).upper() == "NO" else "YES"
            if len(outcomes) > 2:
                outcome = str(outcome_payload.get("id") or outcome_payload.get("outcomeId") or label).upper()
            contracts.append(
                MarketContract(
                    market_id=self.market_id,
                    contract_id=self._contract_id(market_id, outcome),
                    event_id=market_id,
                    title=f"{title} - {label}",
                    outcome=label,
                    url=self._market_url(market),
                    status=self._status_from_market(market),
                    raw={"market": dict(market), "outcome": dict(outcome_payload)},
                )
            )
        return contracts

    def _validate_order(self, order: PaperOrderRequest) -> None:
        self.ensure_order_market(order)
        self._split_contract_id(order.contract_id)
        if str(order.side or "").upper() not in {"BUY", "SELL"}:
            raise MarketConfigurationError("Predict.fun paper order side must be BUY or SELL.")
        if not self._is_positive_number(order.size):
            raise MarketConfigurationError("Predict.fun paper order size must be positive.")
        if order.limit_price is not None and self._safe_probability(order.limit_price) is None:
            raise MarketConfigurationError("Predict.fun paper order limit price must be between 0 and 1.")

    @staticmethod
    def _market_id(market: Mapping[str, Any]) -> str:
        return str(market.get("id") or market.get("marketId") or "").strip()

    @staticmethod
    def _contract_id(market_id: str, outcome: str) -> str:
        return f"{market_id}:{outcome.upper()}"

    @staticmethod
    def _split_contract_id(contract_id: str) -> Tuple[str, str]:
        raw = str(contract_id or "").strip()
        if ":" in raw:
            market_id, outcome = raw.rsplit(":", 1)
        else:
            market_id, outcome = raw, "YES"
        if not market_id.strip() or not outcome.strip():
            raise MarketConfigurationError("Predict.fun contract id must be MARKET_ID:OUTCOME.")
        return market_id.strip(), outcome.strip().upper()

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
    def _outcomes_from_market(market: Mapping[str, Any]) -> List[Mapping[str, Any]]:
        outcomes = market.get("outcomes")
        return [outcome for outcome in outcomes if isinstance(outcome, Mapping)] if isinstance(outcomes, list) else []

    @staticmethod
    def _status_from_market(market: Mapping[str, Any]) -> str:
        raw = market.get("tradingStatus") or market.get("status") or ""
        if isinstance(raw, Mapping):
            raw = raw.get("name") or raw.get("status") or raw.get("value") or raw.get("label") or ""
        return str(raw).strip().lower()

    @staticmethod
    def _market_url(market: Mapping[str, Any]) -> str:
        raw = str(market.get("url") or "").strip()
        if raw:
            return raw
        market_id = PredictFunAdapter._market_id(market)
        return f"https://predict.fun/markets/{market_id}" if market_id else "https://predict.fun"

    @staticmethod
    def _search_text(market: Mapping[str, Any]) -> str:
        values = [market.get("id"), market.get("title"), market.get("question"), market.get("description"), market.get("categorySlug")]
        return " ".join(str(value or "") for value in values).lower()

    @staticmethod
    def _price_from_market(market: Mapping[str, Any], outcome: str) -> Optional[float]:
        outcomes = PredictFunAdapter._outcomes_from_market(market)
        if outcome == "NO" and len(outcomes) >= 2:
            return PredictFunAdapter._safe_probability(outcomes[1].get("price") or outcomes[1].get("probability"))
        if outcomes:
            return PredictFunAdapter._safe_probability(outcomes[0].get("price") or outcomes[0].get("probability"))
        return None

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
            parsed_price = PredictFunAdapter._safe_probability(price)
            try:
                parsed_size = float(size)
            except (TypeError, ValueError):
                continue
            if parsed_price is not None and PredictFunAdapter._is_positive_number(parsed_size):
                levels.append(OrderBookLevel(price=parsed_price, size=parsed_size))
        levels.sort(key=lambda level: level.price, reverse=descending)
        return levels

    @staticmethod
    def _opposite_bids_from_yes_asks(levels: List[OrderBookLevel]) -> List[OrderBookLevel]:
        bids = [OrderBookLevel(price=round(1.0 - level.price, 10), size=level.size) for level in levels]
        bids.sort(key=lambda level: level.price, reverse=True)
        return bids

    @staticmethod
    def _opposite_asks_from_yes_bids(levels: List[OrderBookLevel]) -> List[OrderBookLevel]:
        asks = [OrderBookLevel(price=round(1.0 - level.price, 10), size=level.size) for level in levels]
        asks.sort(key=lambda level: level.price)
        return asks

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
