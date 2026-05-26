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


DEFAULT_BETFAIR_RPC_URL = "https://api.betfair.com/exchange/betting/json-rpc/v1"
BETFAIR_REFERENCES = (
    "https://developer.betfair.com/",
    "https://support.developer.betfair.com/hc/en-us/categories/360000245252-Exchange-API",
    "https://support.developer.betfair.com/hc/en-us/articles/115003864651-How-do-I-get-started",
    "https://support.developer.betfair.com/hc/en-us/articles/360016170431-How-do-I-place-bets-on-handicap-markets",
)


class BetfairExchangeAdapter(MarketAdapter):
    """Betfair Exchange read-only adapter using the official Exchange API JSON-RPC."""

    metadata = get_market_metadata("betfair_exchange")

    def health_check(self) -> Dict[str, Any]:
        health = super().health_check()
        app_key = self.resolve_credential("betfair_app_key", ("BETFAIR_APP_KEY",), label="BETFAIR_APP_KEY")
        session = self.resolve_credential(
            "betfair_session_token",
            ("BETFAIR_SESSION_TOKEN",),
            label="BETFAIR_SESSION_TOKEN",
        )
        credential_sources = []
        for credential in (app_key, session):
            if credential:
                credential_sources.append({"name": credential.name, "source": credential.source})
        health.update(
            {
                "api_base_url": self.api_base_url,
                "references": list(BETFAIR_REFERENCES),
                "credential_sources": credential_sources,
                "live_trading_supported": True,
                "live_trading_enabled": self.config_bool("live_trading_enabled", False),
            }
        )
        return health

    @property
    def api_base_url(self) -> str:
        configured = self.config.get("betfair_api_base_url") or self.config.get("api_base_url")
        return str(configured or DEFAULT_BETFAIR_RPC_URL).rstrip("/")

    def list_events(self, query: str = "", limit: int = 50) -> List[MarketEvent]:
        self.ensure_capability("event_listing")
        desired = max(1, min(int(limit or 50), 100))
        market_filter: Dict[str, Any] = {}
        text_query = str(query or self.config.get("betfair_text_query") or "").strip()
        if text_query:
            market_filter["textQuery"] = text_query
        event_type_ids = self.config.get("betfair_event_type_ids")
        if event_type_ids:
            market_filter["eventTypeIds"] = list(event_type_ids) if isinstance(event_type_ids, list) else [str(event_type_ids)]
        result = self._rpc(
            "SportsAPING/v1.0/listMarketCatalogue",
            {
                "filter": market_filter,
                "marketProjection": ["EVENT", "RUNNER_DESCRIPTION", "MARKET_START_TIME"],
                "sort": "FIRST_TO_START",
                "maxResults": str(desired),
            },
        )
        markets = [item for item in result if isinstance(item, Mapping)] if isinstance(result, list) else []
        return [self._event_from_market(market) for market in markets]

    def list_contracts(self, event_id: str) -> List[MarketContract]:
        self.ensure_capability("event_listing")
        market = self._get_market_catalogue(event_id)
        return self._contracts_from_market(market)

    def get_orderbook(self, contract_id: str) -> OrderBookSnapshot:
        self.ensure_capability("orderbook_reading")
        market_id, selection_id = self._split_contract_id(contract_id)
        result = self._rpc(
            "SportsAPING/v1.0/listMarketBook",
            {
                "marketIds": [market_id],
                "priceProjection": {"priceData": ["EX_BEST_OFFERS"], "virtualise": True},
            },
        )
        books = [item for item in result if isinstance(item, Mapping)] if isinstance(result, list) else []
        if not books:
            raise MarketConfigurationError(f"Betfair market {market_id!r} book was not found.")
        runner = self._find_runner(books[0], selection_id)
        if not runner:
            raise MarketConfigurationError(f"Betfair runner {selection_id!r} was not found in market {market_id!r}.")
        ex = runner.get("ex") if isinstance(runner.get("ex"), Mapping) else {}
        bids = self._levels_from_decimal_odds(ex.get("availableToBack"), descending=True)
        asks = self._levels_from_decimal_odds(ex.get("availableToLay"))
        return OrderBookSnapshot(
            market_id=self.market_id,
            contract_id=self._contract_id(market_id, selection_id),
            bids=bids,
            asks=asks,
            raw={"market_book": dict(books[0]), "runner": dict(runner)},
        )

    def get_price(self, contract_id: str) -> PriceSnapshot:
        self.ensure_capability("price_reading")
        market_id, selection_id = self._split_contract_id(contract_id)
        orderbook = self.get_orderbook(self._contract_id(market_id, selection_id))
        bid = orderbook.bids[0].price if orderbook.bids else None
        ask = orderbook.asks[0].price if orderbook.asks else None
        midpoint = (bid + ask) / 2.0 if bid is not None and ask is not None else None
        return PriceSnapshot(
            market_id=self.market_id,
            contract_id=self._contract_id(market_id, selection_id),
            last=midpoint,
            bid=bid,
            ask=ask,
            midpoint=midpoint,
            source="betfair_exchange_best_offers",
            raw=orderbook.raw,
        )

    def place_paper_order(self, order: PaperOrderRequest) -> PaperOrderResult:
        self.ensure_capability("paper_trading")
        self._validate_order(order)
        market_id, selection_id = self._split_contract_id(order.contract_id)
        return PaperOrderResult(
            market_id=self.market_id,
            contract_id=self._contract_id(market_id, selection_id),
            accepted=True,
            message=(
                f"DRY RUN: would place Betfair {order.side.upper()} "
                f"for {order.size:.4f} stake"
                + (f" at implied probability {order.limit_price:.3f}" if order.limit_price is not None else "")
            ),
            raw={"market_id": market_id, "selection_id": selection_id},
        )

    def place_live_order(self, order: PaperOrderRequest) -> Dict[str, Any]:
        self.ensure_capability("live_trading")
        self._validate_order(order)
        preflight = self.preflight_live_order(order)
        if order.limit_price is None:
            raise MarketConfigurationError("Betfair live orders require a limit probability.")
        market_id, selection_id = self._split_contract_id(order.contract_id)
        params = {
            "marketId": market_id,
            "instructions": [self._place_instruction(order, selection_id=selection_id)],
        }
        customer_ref = order.metadata.get("customer_ref") or order.metadata.get("customerRef")
        if customer_ref:
            params["customerRef"] = str(customer_ref)
        result = self._rpc("SportsAPING/v1.0/placeOrders", params)
        return {
            "market_id": self.market_id,
            "contract_id": order.contract_id,
            "live": True,
            "preflight": preflight,
            "request": params,
            "response": result,
        }

    def copy_trade_from_activity(self, activity: Mapping[str, Any]) -> PaperOrderResult:
        raise UnsupportedFeatureError(
            self.market_id,
            "copy_trading",
            "Betfair copy trading is unsupported because this adapter does not mirror account activity.",
        )

    def _get_market_catalogue(self, market_id: str) -> Mapping[str, Any]:
        clean = str(market_id or "").strip()
        if not clean:
            raise MarketConfigurationError("Betfair market id cannot be empty.")
        result = self._rpc(
            "SportsAPING/v1.0/listMarketCatalogue",
            {
                "filter": {"marketIds": [clean]},
                "marketProjection": ["EVENT", "RUNNER_DESCRIPTION", "MARKET_START_TIME"],
                "maxResults": "1",
            },
        )
        markets = [item for item in result if isinstance(item, Mapping)] if isinstance(result, list) else []
        if not markets:
            raise MarketConfigurationError(f"Betfair market {clean!r} was not found.")
        return markets[0]

    def _rpc(self, method: str, params: Mapping[str, Any]) -> Any:
        headers = self._headers(required=True)
        body = {"jsonrpc": "2.0", "method": method, "params": dict(params), "id": 1}
        self.runtime.rate_limiter.wait()
        try:
            response = self.runtime.session.request(
                "POST",
                self.api_base_url,
                json=body,
                headers=headers,
                timeout=self.runtime.timeout_seconds,
            )
        except Exception as exc:
            raise MarketHTTPError(f"{self.market_id} HTTP request failed: {exc}") from exc
        status = int(getattr(response, "status_code", 0) or 0)
        if status >= 400:
            raise MarketHTTPError(f"{self.market_id} HTTP {status}: {str(getattr(response, 'text', ''))[:200]}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise MarketHTTPError(f"{self.market_id} response was not valid JSON.") from exc
        if isinstance(payload, Mapping) and payload.get("error"):
            raise MarketHTTPError(f"{self.market_id} RPC error: {payload['error']}")
        return payload.get("result") if isinstance(payload, Mapping) else payload

    def _headers(self, *, required: bool = False) -> Dict[str, str]:
        app_key = self.resolve_credential(
            "betfair_app_key",
            ("BETFAIR_APP_KEY",),
            required=required,
            label="BETFAIR_APP_KEY",
        )
        session = self.resolve_credential(
            "betfair_session_token",
            ("BETFAIR_SESSION_TOKEN",),
            required=required,
            label="BETFAIR_SESSION_TOKEN",
        )
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if app_key:
            headers["X-Application"] = app_key.value
        if session:
            headers["X-Authentication"] = session.value
        return headers

    def _event_from_market(self, market: Mapping[str, Any]) -> MarketEvent:
        market_id = self._market_id(market)
        event = market.get("event") if isinstance(market.get("event"), Mapping) else {}
        name = str(market.get("marketName") or event.get("name") or market_id)
        return MarketEvent(
            market_id=self.market_id,
            event_id=market_id,
            title=name,
            url=f"https://www.betfair.com/exchange/plus/market/{market_id}",
            status=str(market.get("status") or "").strip().lower(),
            raw=dict(market),
        )

    def _contracts_from_market(self, market: Mapping[str, Any]) -> List[MarketContract]:
        market_id = self._market_id(market)
        market_name = str(market.get("marketName") or market_id)
        runners = market.get("runners")
        contracts: List[MarketContract] = []
        if isinstance(runners, list):
            for runner in runners:
                if not isinstance(runner, Mapping):
                    continue
                selection_id = str(runner.get("selectionId") or "").strip()
                if not selection_id:
                    continue
                runner_name = str(runner.get("runnerName") or selection_id)
                contracts.append(
                    MarketContract(
                        market_id=self.market_id,
                        contract_id=self._contract_id(market_id, selection_id),
                        event_id=market_id,
                        title=f"{market_name} - {runner_name}",
                        outcome=runner_name,
                        url=f"https://www.betfair.com/exchange/plus/market/{market_id}",
                        status=str(market.get("status") or "").strip().lower(),
                        raw={"market": dict(market), "runner": dict(runner)},
                    )
                )
        return contracts

    def _validate_order(self, order: PaperOrderRequest) -> None:
        self.ensure_order_market(order)
        self._split_contract_id(order.contract_id)
        if str(order.side or "").upper() not in {"BUY", "SELL", "BACK", "LAY"}:
            raise MarketConfigurationError("Betfair paper order side must be BUY/SELL or BACK/LAY.")
        if not self._is_positive_number(order.size):
            raise MarketConfigurationError("Betfair paper order stake must be positive.")
        if order.limit_price is not None and self._safe_probability(order.limit_price) is None:
            raise MarketConfigurationError("Betfair paper order limit probability must be between 0 and 1.")

    def _place_instruction(self, order: PaperOrderRequest, *, selection_id: str) -> Dict[str, Any]:
        probability = self._safe_probability(order.limit_price)
        if probability is None or probability <= 0:
            raise MarketConfigurationError("Betfair live order limit probability must be greater than 0 and at most 1.")
        side = str(order.side or "").upper()
        betfair_side = "LAY" if side in {"SELL", "LAY"} else "BACK"
        instruction: Dict[str, Any] = {
            "selectionId": int(selection_id) if selection_id.isdigit() else selection_id,
            "side": betfair_side,
            "orderType": "LIMIT",
            "limitOrder": {
                "size": str(order.size),
                "price": str(round(1.0 / probability, 4)),
                "persistenceType": str(
                    order.metadata.get("persistence_type")
                    or order.metadata.get("persistenceType")
                    or self.config.get("betfair_persistence_type")
                    or "LAPSE"
                ),
            },
        }
        handicap = order.metadata.get("handicap")
        if handicap not in (None, ""):
            instruction["handicap"] = str(handicap)
        return instruction

    @staticmethod
    def _market_id(market: Mapping[str, Any]) -> str:
        return str(market.get("marketId") or "").strip()

    @staticmethod
    def _contract_id(market_id: str, selection_id: str) -> str:
        return f"{market_id}:{selection_id}"

    @staticmethod
    def _split_contract_id(contract_id: str) -> Tuple[str, str]:
        parts = [part.strip() for part in str(contract_id or "").split(":")]
        if len(parts) != 2 or not parts[0] or not parts[1]:
            raise MarketConfigurationError("Betfair contract id must be MARKET_ID:SELECTION_ID.")
        return parts[0], parts[1]

    @staticmethod
    def _find_runner(market_book: Mapping[str, Any], selection_id: str) -> Optional[Mapping[str, Any]]:
        runners = market_book.get("runners")
        if not isinstance(runners, list):
            return None
        for runner in runners:
            if isinstance(runner, Mapping) and str(runner.get("selectionId") or "").strip() == str(selection_id):
                return runner
        return None

    @staticmethod
    def _levels_from_decimal_odds(raw: Any, *, descending: bool = False) -> List[OrderBookLevel]:
        levels: List[OrderBookLevel] = []
        if not isinstance(raw, list):
            return levels
        for item in raw:
            if not isinstance(item, Mapping):
                continue
            probability = BetfairExchangeAdapter._probability_from_decimal_odds(item.get("price"))
            try:
                size = float(item.get("size"))
            except (TypeError, ValueError):
                continue
            if probability is not None and BetfairExchangeAdapter._is_positive_number(size):
                levels.append(OrderBookLevel(price=probability, size=size))
        levels.sort(key=lambda level: level.price, reverse=descending)
        return levels

    @staticmethod
    def _probability_from_decimal_odds(value: Any) -> Optional[float]:
        try:
            odds = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(odds) or odds <= 1.0:
            return None
        return 1.0 / odds

    @staticmethod
    def _safe_probability(value: Any) -> Optional[float]:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(number):
            return None
        return number if 0.0 <= number <= 1.0 else None

    @staticmethod
    def _is_positive_number(value: Any) -> bool:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return False
        return math.isfinite(number) and number > 0
