from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Tuple

from .base import MarketAdapter
from .catalog import get_market_metadata
from .errors import MarketConfigurationError, MarketHTTPError, UnsupportedFeatureError
from .types import (
    MarketContract,
    MarketCandle,
    MarketEvent,
    OrderBookLevel,
    OrderBookSnapshot,
    PaperOrderRequest,
    PaperOrderResult,
    PriceSnapshot,
)


DEFAULT_GEMINI_BASE_URL = "https://api.gemini.com"
GEMINI_REFERENCES = (
    "https://developer.gemini.com/prediction-markets-spec",
    "https://developer.gemini.com/prediction-markets-spec/markets",
    "https://developer.gemini.com/prediction-markets-spec/trading",
    "https://developer.gemini.com/prediction-markets-spec/terms",
    "https://developer.gemini.com/prediction-markets/websocket/streams",
)


class GeminiPredictionAdapter(MarketAdapter):
    """Gemini Prediction Markets read-only adapter using official public endpoints."""

    metadata = get_market_metadata("gemini_titan")

    def health_check(self) -> Dict[str, Any]:
        health = super().health_check()
        api_key = self.resolve_credential("gemini_api_key", ("GEMINI_API_KEY",), label="GEMINI_API_KEY")
        health.update(
            {
                "api_base_url": self.api_base_url,
                "references": list(GEMINI_REFERENCES),
                "credential_sources": [{"name": api_key.name, "source": api_key.source}] if api_key else [],
                "live_trading_supported": True,
                "live_trading_enabled": self.config_bool("live_trading_enabled", False),
            }
        )
        return health

    @property
    def api_base_url(self) -> str:
        configured = self.config.get("gemini_api_base_url") or self.config.get("api_base_url")
        return str(configured or DEFAULT_GEMINI_BASE_URL).rstrip("/")

    def list_events(self, query: str = "", limit: int = 50) -> List[MarketEvent]:
        self.ensure_capability("event_listing")
        desired = max(1, min(int(limit or 50), 500))
        params: Dict[str, Any] = {"limit": desired}
        if query:
            params["search"] = str(query).strip()
        status = str(self.config.get("gemini_event_status") or "active").strip()
        if status:
            params["status"] = status
        payload = self._get("/v1/prediction-markets/events", params=params)
        events = self._list_from_payload(payload, "data", "events")
        return [self._event_from_payload(event) for event in events[:desired]]

    def list_contracts(self, event_id: str) -> List[MarketContract]:
        self.ensure_capability("event_listing")
        event = self._get_event(event_id)
        return self._contracts_from_event(event)

    def get_orderbook(self, contract_id: str) -> OrderBookSnapshot:
        self.ensure_capability("orderbook_reading")
        event_ticker, instrument_symbol = self._split_contract_id(contract_id)
        event = self._get_event(event_ticker)
        payload = self._contract_orderbook(event, instrument_symbol)
        if payload is None:
            raise MarketConfigurationError(
                f"Gemini event {event_ticker} did not include an orderbook for {instrument_symbol}. "
                "The documented prediction-market REST contract exposes depth in the event response; "
                "live streaming depth remains available through the official WebSocket stream."
            )
        bids = self._book_levels(self._value_at(payload, "bids"), descending=True)
        asks = self._book_levels(self._value_at(payload, "asks"))
        return OrderBookSnapshot(
            market_id=self.market_id,
            contract_id=self._contract_id(event_ticker, instrument_symbol),
            bids=bids,
            asks=asks,
            raw=payload if isinstance(payload, dict) else {},
        )

    def get_price(self, contract_id: str) -> PriceSnapshot:
        self.ensure_capability("price_reading")
        event_ticker, instrument_symbol = self._split_contract_id(contract_id)
        event = self._get_event(event_ticker)
        contract = self._find_contract(event, instrument_symbol)
        prices = contract.get("prices") if isinstance(contract.get("prices"), Mapping) else {}
        bid = self._safe_probability(self._value_at(prices, "bestBid", "best_bid"))
        ask = self._safe_probability(self._value_at(prices, "bestAsk", "best_ask"))
        last = self._safe_probability(self._value_at(prices, "lastTradePrice", "last_trade_price"))
        if bid is None or ask is None:
            orderbook = self._contract_orderbook(event, instrument_symbol)
            if orderbook is not None:
                bids = self._book_levels(self._value_at(orderbook, "bids"), descending=True)
                asks = self._book_levels(self._value_at(orderbook, "asks"))
                bid = bid if bid is not None else (bids[0].price if bids else None)
                ask = ask if ask is not None else (asks[0].price if asks else None)
        midpoint = (bid + ask) / 2.0 if bid is not None and ask is not None else None
        if last is None:
            last = midpoint
        return PriceSnapshot(
            market_id=self.market_id,
            contract_id=self._contract_id(event_ticker, instrument_symbol),
            last=last,
            bid=bid,
            ask=ask,
            midpoint=midpoint,
            source="gemini_prediction_contract",
            raw={"event": dict(event), "contract": dict(contract)},
        )

    def list_candles(
        self,
        contract_id: str,
        *,
        resolution: str = "1h",
        from_timestamp: Optional[float] = None,
        to_timestamp: Optional[float] = None,
    ) -> List[MarketCandle]:
        """Return Gemini's documented contract price-history points.

        Gemini exposes irregular ``priceHistory`` snapshots on the contract
        detail response rather than exchange-style OHLCV bars.  Each point is
        represented as a flat candle and volume is intentionally left unset;
        no resampling is claimed.
        """

        self.ensure_capability("candle_history")
        requested_resolution = str(resolution or "1h").strip().lower()
        if requested_resolution not in {"raw", "price", "1h", "1d"}:
            raise MarketConfigurationError(
                "Gemini price history accepts resolution 'raw', 'price', '1h', or '1d'; "
                "the irregular official snapshots are not resampled."
            )
        lower = self._history_timestamp(from_timestamp, "from_timestamp") if from_timestamp is not None else None
        upper = self._history_timestamp(to_timestamp, "to_timestamp") if to_timestamp is not None else None
        if lower is not None and upper is not None and upper < lower:
            raise MarketConfigurationError("Gemini price history to_timestamp must not precede from_timestamp.")

        event_ticker, instrument_symbol = self._split_contract_id(contract_id)
        event = self._get_event(event_ticker)
        contract = self._find_contract(event, instrument_symbol)
        history = contract.get("priceHistory")
        if history is None:
            history = contract.get("price_history")
        if history is None:
            raise MarketConfigurationError(
                f"Gemini event {event_ticker} did not include price history for {instrument_symbol}."
            )
        if not isinstance(history, list):
            raise MarketConfigurationError("Gemini priceHistory must be a list of timestamp/price points.")

        canonical = self._contract_id(event_ticker, instrument_symbol)
        candles: List[MarketCandle] = []
        for point in history:
            if not isinstance(point, Mapping):
                continue
            timestamp = self._history_timestamp(point.get("timestamp"), "timestamp")
            price = self._safe_probability(point.get("price"))
            if timestamp is None or price is None:
                continue
            if lower is not None and timestamp < lower:
                continue
            if upper is not None and timestamp > upper:
                continue
            candles.append(
                MarketCandle(
                    market_id=self.market_id,
                    contract_id=canonical,
                    timestamp=timestamp,
                    open=price,
                    high=price,
                    low=price,
                    close=price,
                    volume=None,
                    raw={"source": "gemini_prediction_markets", "resolution_requested": requested_resolution, **dict(point)},
                )
            )
        candles.sort(key=lambda candle: candle.timestamp)
        return candles

    def place_paper_order(self, order: PaperOrderRequest) -> PaperOrderResult:
        self.ensure_capability("paper_trading")
        self._validate_order(order)
        event_ticker, instrument_symbol = self._split_contract_id(order.contract_id)
        return PaperOrderResult(
            market_id=self.market_id,
            contract_id=self._contract_id(event_ticker, instrument_symbol),
            accepted=True,
            message=(
                f"DRY RUN: would place Gemini Prediction {order.side.upper()} "
                f"for {order.size:.4f} contracts"
                + (f" at limit {order.limit_price:.2f}" if order.limit_price is not None else "")
            ),
            raw={"event_ticker": event_ticker, "instrument_symbol": instrument_symbol},
        )

    def place_live_order(self, order: PaperOrderRequest) -> Dict[str, Any]:
        self.ensure_capability("live_trading")
        self._validate_order(order)
        preflight = self.preflight_live_order(order)
        if order.limit_price is None:
            raise MarketConfigurationError("Gemini live orders require a limit price.")
        event_ticker, instrument_symbol = self._split_contract_id(order.contract_id)
        self._ensure_prediction_terms_accepted()
        payload = self._live_order_payload(
            order,
            instrument_symbol=instrument_symbol,
        )
        response = self._authenticated_post("/v1/prediction-markets/order", payload)
        return {
            "market_id": self.market_id,
            "event_ticker": event_ticker,
            "contract_id": self._contract_id(event_ticker, instrument_symbol),
            "instrument_symbol": instrument_symbol,
            "live": True,
            "preflight": preflight,
            "request": payload,
            "response": response,
        }

    def copy_trade_from_activity(self, activity: Mapping[str, Any]) -> PaperOrderResult:
        raise UnsupportedFeatureError(
            self.market_id,
            "copy_trading",
            "Gemini Prediction Markets does not expose an account activity mirroring model in this adapter.",
        )

    def _get_event(self, event_id: str) -> Mapping[str, Any]:
        ticker = str(event_id or "").strip()
        if not ticker:
            raise MarketConfigurationError("Gemini event ticker cannot be empty.")
        payload = self._get(f"/v1/prediction-markets/events/{ticker}")
        if isinstance(payload, Mapping):
            data = payload.get("data")
            if isinstance(data, Mapping):
                return data
            return payload
        raise MarketConfigurationError(f"Gemini event {ticker!r} was not found.")

    @classmethod
    def _find_contract(cls, event: Mapping[str, Any], instrument_symbol: str) -> Mapping[str, Any]:
        for contract in cls._list_from_payload(event, "contracts"):
            if cls._instrument_symbol(contract) == instrument_symbol:
                return contract
        raise MarketConfigurationError(
            f"Gemini event {cls._event_ticker(event)!r} did not include contract {instrument_symbol!r}."
        )

    @classmethod
    def _contract_orderbook(cls, event: Mapping[str, Any], instrument_symbol: str) -> Optional[Mapping[str, Any]]:
        contract = cls._find_contract(event, instrument_symbol)
        for key in ("orderbook", "orderBook", "book"):
            direct = contract.get(key)
            if isinstance(direct, Mapping):
                return direct
        orderbooks = event.get("contractOrderbooks") or event.get("contract_orderbooks")
        if isinstance(orderbooks, Mapping):
            contract_id = str(contract.get("id") or "").strip()
            contract_ticker = str(contract.get("ticker") or "").strip()
            for key in (instrument_symbol, contract_id, contract_ticker):
                if key and isinstance(orderbooks.get(key), Mapping):
                    return orderbooks[key]
        return None

    def _get(self, path: str, *, params: Optional[Mapping[str, Any]] = None) -> Any:
        return self.runtime.get_json(self._url(path), params=params)

    def _authenticated_post(self, path: str, payload: Mapping[str, Any]) -> Any:
        request_payload = dict(payload)
        request_payload.setdefault("request", path)
        request_payload.setdefault("nonce", self._next_nonce())
        return self._authenticated_request("POST", path, request_payload, send_body=True)

    def _authenticated_get(self, path: str) -> Any:
        request_payload = {"request": path, "nonce": self._next_nonce()}
        return self._authenticated_request("GET", path, request_payload, send_body=False)

    def _authenticated_request(
        self,
        method: str,
        path: str,
        payload: Mapping[str, Any],
        *,
        send_body: bool,
    ) -> Any:
        body = json.dumps(dict(payload), separators=(",", ":")) if send_body else ""
        headers = self._auth_headers(payload, content_type="application/json" if send_body else "text/plain")
        self.runtime.rate_limiter.wait()
        try:
            response = self.runtime.session.request(
                method,
                self._url(path),
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

    def _ensure_prediction_terms_accepted(self) -> None:
        status = self._authenticated_get("/v1/prediction-markets/terms/status")
        if not isinstance(status, Mapping) or status.get("hasAcceptedLatest") is not True:
            raise MarketConfigurationError(
                "Gemini Prediction Markets terms are not accepted for this account. "
                "Accept the latest terms through Gemini before enabling live orders."
            )

    def _url(self, path: str) -> str:
        return f"{self.api_base_url}/{'/'.join(part for part in str(path or '').split('/') if part)}"

    def _auth_headers(self, payload: Mapping[str, Any], *, content_type: str = "text/plain") -> Dict[str, str]:
        api_key = self.resolve_credential(
            "gemini_api_key",
            ("GEMINI_API_KEY",),
            required=True,
            label="GEMINI_API_KEY",
        )
        api_secret = self.resolve_credential(
            "gemini_api_secret",
            ("GEMINI_API_SECRET",),
            required=True,
            label="GEMINI_API_SECRET",
        )
        encoded = base64.b64encode(json.dumps(dict(payload), separators=(",", ":")).encode("utf-8"))
        signature = hmac.new(api_secret.value.encode("utf-8"), encoded, hashlib.sha384).hexdigest()
        return {
            "Content-Type": content_type,
            "Cache-Control": "no-cache",
            "X-GEMINI-APIKEY": api_key.value,
            "X-GEMINI-PAYLOAD": encoded.decode("ascii"),
            "X-GEMINI-SIGNATURE": signature,
        }

    def _event_from_payload(self, event: Mapping[str, Any]) -> MarketEvent:
        event_id = self._event_ticker(event)
        return MarketEvent(
            market_id=self.market_id,
            event_id=event_id,
            title=str(event.get("title") or event.get("name") or event_id),
            url=self._event_url(event),
            status=str(event.get("status") or "").strip().lower(),
            raw=dict(event),
        )

    def _contracts_from_event(self, event: Mapping[str, Any]) -> List[MarketContract]:
        event_ticker = self._event_ticker(event)
        title = str(event.get("title") or event_ticker)
        contracts = []
        for contract in self._list_from_payload(event, "contracts"):
            symbol = self._instrument_symbol(contract)
            if not symbol:
                continue
            outcome = str(
                contract.get("outcome")
                or contract.get("name")
                or contract.get("title")
                or contract.get("side")
                or symbol
            )
            contracts.append(
                MarketContract(
                    market_id=self.market_id,
                    contract_id=self._contract_id(event_ticker, symbol),
                    event_id=event_ticker,
                    title=f"{title} - {outcome}",
                    outcome=outcome,
                    url=self._event_url(event),
                    status=str(contract.get("status") or event.get("status") or "").strip().lower(),
                    raw={"event": dict(event), "contract": dict(contract)},
                )
            )
        return contracts

    def _validate_order(self, order: PaperOrderRequest) -> None:
        self.ensure_order_market(order)
        self._split_contract_id(order.contract_id)
        if str(order.side or "").upper() not in {"BUY", "SELL"}:
            raise MarketConfigurationError("Gemini paper order side must be BUY or SELL.")
        if not self._is_positive_number(order.size):
            raise MarketConfigurationError("Gemini paper order size must be positive.")
        if order.limit_price is not None and self._safe_probability(order.limit_price) is None:
            raise MarketConfigurationError("Gemini paper order limit price must be between 0 and 1.")

    def _live_order_payload(
        self,
        order: PaperOrderRequest,
        *,
        instrument_symbol: str,
    ) -> Dict[str, Any]:
        outcome = self._order_outcome(order, instrument_symbol=instrument_symbol)
        order_type = str(order.metadata.get("order_type") or "limit").strip().lower()
        if order_type not in {"limit", "stop-limit"}:
            raise MarketConfigurationError("Gemini prediction order_type must be limit or stop-limit.")
        time_in_force = str(order.metadata.get("time_in_force") or "good-til-cancel").strip().lower()
        if time_in_force not in {"good-til-cancel", "immediate-or-cancel", "fill-or-kill"}:
            raise MarketConfigurationError(
                "Gemini prediction time_in_force must be good-til-cancel, immediate-or-cancel, or fill-or-kill."
            )
        payload: Dict[str, Any] = {
            "symbol": instrument_symbol,
            "orderType": order_type,
            "side": "buy" if str(order.side or "").upper() == "BUY" else "sell",
            "quantity": str(order.size),
            "price": str(order.limit_price),
            "outcome": outcome,
            "timeInForce": time_in_force,
        }
        stop_price = order.metadata.get("stop_price")
        if order_type == "stop-limit":
            parsed_stop = self._safe_probability(stop_price)
            if parsed_stop is None:
                raise MarketConfigurationError("Gemini stop-limit orders require stop_price between 0 and 1.")
            payload["stopPrice"] = str(parsed_stop)
        elif stop_price is not None:
            raise MarketConfigurationError("Gemini stop_price is only valid for stop-limit orders.")
        if "maker_or_cancel" in order.metadata:
            payload["makerOrCancel"] = bool(order.metadata["maker_or_cancel"])
        client_order_id = order.metadata.get("client_order_id") or order.metadata.get("clientOrderId")
        if client_order_id:
            payload["clientOrderId"] = str(client_order_id)
        return payload

    def _order_outcome(self, order: PaperOrderRequest, *, instrument_symbol: str) -> str:
        candidate = order.metadata.get("outcome")
        if candidate is None:
            suffix = instrument_symbol.rsplit("-", 1)[-1].strip().lower()
            if suffix in {"yes", "no"}:
                candidate = suffix
        outcome = str(candidate or "").strip().lower()
        if outcome not in {"yes", "no"}:
            raise MarketConfigurationError(
                "Gemini live orders require metadata['outcome'] set to 'yes' or 'no'; "
                "the event ticker alone does not identify the outcome."
            )
        return outcome

    def _next_nonce(self, preferred: Any = None) -> int:
        try:
            requested = int(preferred) if preferred is not None else 0
        except (TypeError, ValueError):
            requested = 0
        now = int(time.time() * 1000)
        previous = int(getattr(self, "_last_nonce", 0) or 0)
        nonce = max(now, requested, previous + 1)
        self._last_nonce = nonce
        return nonce

    @staticmethod
    def _event_ticker(event: Mapping[str, Any]) -> str:
        return str(event.get("ticker") or event.get("eventTicker") or event.get("id") or event.get("slug") or "").strip()

    @staticmethod
    def _instrument_symbol(contract: Mapping[str, Any]) -> str:
        return str(
            contract.get("instrumentSymbol")
            or contract.get("instrument_symbol")
            or contract.get("symbol")
            or contract.get("id")
            or ""
        ).strip()

    @staticmethod
    def _contract_id(event_ticker: str, instrument_symbol: str) -> str:
        return f"{event_ticker}:{instrument_symbol}"

    @staticmethod
    def _split_contract_id(contract_id: str) -> Tuple[str, str]:
        raw = str(contract_id or "").strip()
        if not raw:
            raise MarketConfigurationError("Gemini contract id cannot be empty.")
        if ":" not in raw:
            return raw, raw
        event_ticker, instrument_symbol = raw.split(":", 1)
        if not event_ticker.strip() or not instrument_symbol.strip():
            raise MarketConfigurationError("Gemini contract id must be EVENT_TICKER:INSTRUMENT_SYMBOL.")
        return event_ticker.strip(), instrument_symbol.strip()

    @staticmethod
    def _event_url(event: Mapping[str, Any]) -> str:
        raw = str(event.get("url") or "").strip()
        if raw:
            return raw
        ticker = GeminiPredictionAdapter._event_ticker(event)
        return f"https://www.gemini.com/prediction-markets/{ticker}" if ticker else "https://www.gemini.com"

    @staticmethod
    def _list_from_payload(payload: Any, *keys: str) -> List[Mapping[str, Any]]:
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, Mapping)]
        if isinstance(payload, Mapping):
            for key in keys:
                value = payload.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, Mapping)]
            data = payload.get("data")
            if isinstance(data, list):
                return [item for item in data if isinstance(item, Mapping)]
        return []

    @staticmethod
    def _book_levels(raw: Any, *, descending: bool = False) -> List[OrderBookLevel]:
        levels: List[OrderBookLevel] = []
        if not isinstance(raw, list):
            return levels
        for item in raw:
            price = size = None
            if isinstance(item, Mapping):
                price = item.get("price")
                size = item.get("amount") or item.get("size") or item.get("quantity")
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                price, size = item[0], item[1]
            parsed_price = GeminiPredictionAdapter._safe_probability(price)
            try:
                parsed_size = float(size)
            except (TypeError, ValueError):
                continue
            if parsed_price is not None and GeminiPredictionAdapter._is_positive_number(parsed_size):
                levels.append(OrderBookLevel(price=parsed_price, size=parsed_size))
        levels.sort(key=lambda level: level.price, reverse=descending)
        return levels

    @staticmethod
    def _history_timestamp(value: Any, label: str) -> Optional[float]:
        if value is None:
            return None
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            number = float(value)
            if not math.isfinite(number):
                return None
            return number / 1000.0 if number > 100_000_000_000 else number
        raw = str(value).strip()
        if not raw:
            return None
        try:
            parsed = float(raw)
            if math.isfinite(parsed):
                return parsed / 1000.0 if parsed > 100_000_000_000 else parsed
        except ValueError:
            pass
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.timestamp()
        except (TypeError, ValueError, OverflowError) as exc:
            raise MarketConfigurationError(
                f"Gemini {label} must be a Unix timestamp or ISO-8601 value."
            ) from exc

    @staticmethod
    def _value_at(data: Any, *keys: str) -> Any:
        if not isinstance(data, Mapping):
            return []
        for key in keys:
            value = data.get(key)
            if value is not None:
                return value
        return []

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
        if 0.0 <= number <= 1.0:
            return number
        return None

    @staticmethod
    def _is_positive_number(value: Any) -> bool:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return False
        return math.isfinite(number) and number > 0
