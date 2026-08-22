from __future__ import annotations

import base64
import math
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple
from urllib.parse import urlparse

from .base import MarketAdapter
from .catalog import get_market_metadata
from .errors import MarketConfigurationError
from .types import (
    MarketCandle,
    MarketContract,
    MarketEvent,
    MarketTrade,
    OrderBookLevel,
    OrderBookSnapshot,
    PaperOrderRequest,
    PaperOrderResult,
    PriceSnapshot,
)


DEFAULT_KALSHI_BASE_URL = "https://external-api.kalshi.com/trade-api/v2"
KALSHI_ORDER_PATH = "/portfolio/events/orders"


class KalshiAdapter(MarketAdapter):
    """Kalshi adapter using the documented REST API surface."""

    metadata = get_market_metadata("kalshi")

    def health_check(self) -> Dict[str, Any]:
        health = super().health_check()
        credential_sources = []
        for config_key, env_vars, label in (
            ("kalshi_api_key_id", ("KALSHI_API_KEY_ID",), "KALSHI_API_KEY_ID"),
            ("kalshi_private_key_path", ("KALSHI_PRIVATE_KEY_PATH",), "KALSHI_PRIVATE_KEY_PATH"),
            ("kalshi_private_key_pem", ("KALSHI_PRIVATE_KEY_PEM",), "KALSHI_PRIVATE_KEY_PEM"),
        ):
            credential = self.resolve_credential(config_key, env_vars, label=label)
            if credential:
                credential_sources.append({"name": credential.name, "source": credential.source})
        health.update(
            {
                "api_base_url": self.api_base_url,
                "live_trading_enabled": self.config_bool("live_trading_enabled", False),
                "credential_sources": credential_sources,
            }
        )
        return health

    @property
    def api_base_url(self) -> str:
        configured = self.config.get("kalshi_api_base_url") or self.config.get("api_base_url")
        return str(configured or DEFAULT_KALSHI_BASE_URL).rstrip("/")

    def list_events(self, query: str = "", limit: int = 50) -> List[MarketEvent]:
        self.ensure_capability("event_listing")
        desired = max(1, min(int(limit or 50), 100))
        markets = self._fetch_markets(limit=max(desired * 3, desired))
        q = str(query or "").strip().lower()
        if q:
            markets = [market for market in markets if self._market_matches_query(market, q)]

        grouped: Dict[str, List[Mapping[str, Any]]] = {}
        for market in markets:
            event_id = self._event_id_for_market(market)
            if event_id:
                grouped.setdefault(event_id, []).append(market)

        events: List[MarketEvent] = []
        for event_id, event_markets in grouped.items():
            first = event_markets[0]
            events.append(
                MarketEvent(
                    market_id=self.market_id,
                    event_id=event_id,
                    title=self._event_title(first),
                    url=self._market_url(first),
                    status=self._event_status(event_markets),
                    raw={"markets": list(event_markets)},
                )
            )
            if len(events) >= desired:
                break
        return events

    def list_contracts(self, event_id: str) -> List[MarketContract]:
        self.ensure_capability("event_listing")
        ref = str(event_id or "").strip().upper()
        if not ref:
            return []

        markets = self._fetch_markets(event_ticker=ref, limit=1000)
        if not markets:
            market = self._get_market(ref)
            markets = [market] if market else []

        contracts: List[MarketContract] = []
        for market in markets:
            contracts.extend(self._contracts_from_market(market))
        return contracts

    def get_price(self, contract_id: str) -> PriceSnapshot:
        self.ensure_capability("price_reading")
        orderbook = self.get_orderbook(contract_id)
        bid = orderbook.bids[0].price if orderbook.bids else None
        ask = orderbook.asks[0].price if orderbook.asks else None
        midpoint = (bid + ask) / 2.0 if bid is not None and ask is not None else None
        return PriceSnapshot(
            market_id=self.market_id,
            contract_id=contract_id,
            bid=bid,
            ask=ask,
            midpoint=midpoint,
            source="kalshi_orderbook",
            raw=orderbook.raw,
        )

    def get_orderbook(self, contract_id: str) -> OrderBookSnapshot:
        self.ensure_capability("orderbook_reading")
        ticker, outcome = self._split_contract_id(contract_id)
        payload = self._get(f"/markets/{ticker}/orderbook")
        book = self._orderbook_payload(payload)

        yes_bids = self._levels(
            book.get("yes_dollars")
            or book.get("yes")
            or book.get("yes_bids")
            or book.get("yesBid")
            or [],
            descending=True,
        )
        no_bids = self._levels(
            book.get("no_dollars")
            or book.get("no")
            or book.get("no_bids")
            or book.get("noBid")
            or [],
            descending=True,
        )

        if outcome == "yes":
            bids = yes_bids
            asks = self._asks_from_opposite_bids(no_bids)
        else:
            bids = no_bids
            asks = self._asks_from_opposite_bids(yes_bids)

        return OrderBookSnapshot(
            market_id=self.market_id,
            contract_id=self._contract_id(ticker, outcome),
            bids=bids,
            asks=asks,
            raw=payload if isinstance(payload, dict) else {},
        )

    def list_trades(
        self,
        contract_id: str,
        *,
        limit: int = 50,
        before: Optional[float] = None,
        after: Optional[float] = None,
    ) -> List[MarketTrade]:
        """Return normalized public Kalshi trades for one binary outcome.

        Kalshi's documented ``GET /markets/trades`` feed is public and can be
        filtered by ticker and timestamp.  Its ``taker_outcome_side`` field is
        an outcome (YES/NO), rather than a buy/sell direction, so that value is
        retained in the normalized ``side`` field and the raw row remains
        available for callers that need the exchange-specific semantics.
        """

        ticker, outcome = self._split_contract_id(contract_id)
        params: Dict[str, Any] = {
            "ticker": ticker,
            "limit": self._history_limit(limit),
        }
        if after is not None:
            params["min_ts"] = self._history_timestamp(after, "after")
        if before is not None:
            params["max_ts"] = self._history_timestamp(before, "before")
        payload = self._get("/markets/trades", params=params)
        rows = payload.get("trades") if isinstance(payload, Mapping) else []
        if not isinstance(rows, list):
            return []

        trades: List[MarketTrade] = []
        for raw in rows:
            if not isinstance(raw, Mapping):
                continue
            row_ticker = str(raw.get("ticker") or "").strip().upper()
            if row_ticker and row_ticker != ticker:
                continue
            row_outcome = str(raw.get("taker_outcome_side") or raw.get("taker_side") or "").strip().lower()
            if row_outcome and row_outcome not in {"yes", "no"}:
                continue
            if row_outcome and row_outcome != outcome:
                continue
            price = self._trade_price(raw, outcome)
            size = self._positive_number(raw.get("count_fp") or raw.get("count") or raw.get("quantity"))
            trade_id = str(raw.get("trade_id") or raw.get("tradeId") or "").strip()
            if price is None or size is None or not trade_id:
                continue
            timestamp = self._timestamp_seconds(raw.get("created_time") or raw.get("created_ts") or raw.get("timestamp"))
            trades.append(
                MarketTrade(
                    market_id=self.market_id,
                    contract_id=self._contract_id(ticker, outcome),
                    trade_id=trade_id,
                    side=(row_outcome or outcome).upper(),
                    price=price,
                    size=size,
                    timestamp=timestamp,
                    raw=dict(raw),
                )
            )
        return trades

    def list_candles(
        self,
        contract_id: str,
        *,
        resolution: str = "1h",
        from_timestamp: Optional[float] = None,
        to_timestamp: Optional[float] = None,
    ) -> List[MarketCandle]:
        """Return normalized Kalshi OHLCV history for one outcome.

        The official single-market endpoint requires a series ticker and a
        bounded time range.  The adapter obtains the series from the public
        market record, maps the common resolutions to Kalshi's documented
        1/60/1440-minute intervals, and complements YES prices for NO candles.
        """

        ticker, outcome = self._split_contract_id(contract_id)
        interval = self._candle_interval(resolution)
        market = self._get_market(ticker)
        series_ticker = str((market or {}).get("series_ticker") or self.config.get("kalshi_series_ticker") or "").strip().upper()
        if not series_ticker:
            raise MarketConfigurationError(
                f"Kalshi market {ticker} did not provide a series_ticker required for candlestick history."
            )

        end_ts = self._history_timestamp(to_timestamp, "to_timestamp") if to_timestamp is not None else int(time.time())
        default_lookback = max(interval * 100, 3600)
        start_ts = (
            self._history_timestamp(from_timestamp, "from_timestamp")
            if from_timestamp is not None
            else max(0, end_ts - default_lookback)
        )
        if end_ts <= start_ts:
            raise MarketConfigurationError("Kalshi candle history requires to_timestamp greater than from_timestamp.")

        path = f"/series/{series_ticker}/markets/{ticker}/candlesticks"
        payload = self._get(
            path,
            params={
                "start_ts": start_ts,
                "end_ts": end_ts,
                "period_interval": interval,
                "include_latest_before_start": False,
            },
        )
        rows = payload.get("candlesticks") if isinstance(payload, Mapping) else []
        if not isinstance(rows, list):
            return []

        candles: List[MarketCandle] = []
        for raw in rows:
            if not isinstance(raw, Mapping):
                continue
            timestamp = self._timestamp_seconds(raw.get("end_period_ts") or raw.get("end_period"))
            values = self._candle_values(raw, outcome)
            if timestamp is None or values is None:
                continue
            volume = self._nonnegative_number(raw.get("volume_fp") or raw.get("volume"))
            candles.append(
                MarketCandle(
                    market_id=self.market_id,
                    contract_id=self._contract_id(ticker, outcome),
                    timestamp=timestamp,
                    open=values[0],
                    high=values[1],
                    low=values[2],
                    close=values[3],
                    volume=volume,
                    raw=dict(raw),
                )
            )
        return candles

    def place_paper_order(self, order: PaperOrderRequest) -> PaperOrderResult:
        self.ensure_capability("paper_trading")
        self._validate_order(order)
        ticker, outcome = self._split_contract_id(order.contract_id)
        return PaperOrderResult(
            market_id=self.market_id,
            contract_id=self._contract_id(ticker, outcome),
            accepted=True,
            message=(
                f"DRY RUN: would place Kalshi {order.side.upper()} order for "
                f"{order.size:.4f} {outcome.upper()} contracts"
                + (f" at limit {order.limit_price:.4f}" if order.limit_price is not None else "")
            ),
            filled_size=0.0,
            average_price=None,
            raw={"request": dict(order.metadata), "ticker": ticker, "outcome": outcome},
        )

    def place_live_order(self, order: PaperOrderRequest) -> Dict[str, Any]:
        self.ensure_capability("live_trading")
        self._validate_order(order)
        preflight = self.preflight_live_order(order)
        if order.limit_price is None:
            raise MarketConfigurationError("Kalshi live trading requires a limit price.")

        payload = self._build_live_order_payload(order)
        headers = self._auth_headers("POST", KALSHI_ORDER_PATH)
        headers["Content-Type"] = "application/json"
        response = self.runtime.request_json(
            "POST",
            self._url(KALSHI_ORDER_PATH),
            json_body=payload,
            headers=headers,
        )
        return {
            "market_id": self.market_id,
            "contract_id": order.contract_id,
            "live": True,
            "preflight": preflight,
            "request": payload,
            "response": response,
        }

    def _fetch_markets(
        self,
        *,
        event_ticker: Optional[str] = None,
        limit: int = 100,
    ) -> List[Mapping[str, Any]]:
        params: Dict[str, Any] = {
            "limit": max(1, min(int(limit or 100), 1000)),
        }
        status = str(self.config.get("kalshi_market_status") or self.config.get("market_status") or "open").strip()
        if status:
            params["status"] = status
        if event_ticker:
            params["event_ticker"] = event_ticker
        data = self._get("/markets", params=params)
        markets = data.get("markets") if isinstance(data, Mapping) else []
        return [market for market in markets if isinstance(market, Mapping)] if isinstance(markets, list) else []

    def _get_market(self, ticker: str) -> Optional[Mapping[str, Any]]:
        data = self._get(f"/markets/{ticker}")
        if isinstance(data, Mapping):
            market = data.get("market")
            if isinstance(market, Mapping):
                return market
            if "ticker" in data:
                return data
        return None

    def _get(self, path: str, *, params: Optional[Mapping[str, Any]] = None) -> Any:
        return self.runtime.get_json(self._url(path), params=params)

    def _url(self, path: str) -> str:
        clean_path = "/" + str(path or "").strip("/")
        return f"{self.api_base_url}{clean_path}"

    def _request_path(self, path: str) -> str:
        return urlparse(self._url(path)).path

    @staticmethod
    def _history_limit(value: Any) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError) as exc:
            raise MarketConfigurationError("Kalshi history limit must be an integer between 1 and 1000.") from exc
        if parsed < 1 or parsed > 1000:
            raise MarketConfigurationError("Kalshi history limit must be between 1 and 1000.")
        return parsed

    @staticmethod
    def _history_timestamp(value: Any, label: str) -> int:
        try:
            parsed = float(value)
        except (TypeError, ValueError) as exc:
            raise MarketConfigurationError(f"Kalshi {label} timestamp must be numeric.") from exc
        if not math.isfinite(parsed) or parsed < 0:
            raise MarketConfigurationError(f"Kalshi {label} timestamp must be a finite non-negative number.")
        return int(parsed)

    @staticmethod
    def _candle_interval(resolution: str) -> int:
        normalized = str(resolution or "").strip().lower()
        intervals = {"1m": 1, "1h": 60, "1d": 1440, "60m": 60, "1440m": 1440}
        try:
            return intervals[normalized]
        except KeyError as exc:
            raise MarketConfigurationError("Kalshi candle resolution must be 1m, 1h, or 1d.") from exc

    @classmethod
    def _trade_price(cls, raw: Mapping[str, Any], outcome: str) -> Optional[float]:
        keys = (
            ("yes_price_dollars", "yes_price") if outcome == "yes" else ("no_price_dollars", "no_price")
        ) + ("price_dollars", "price")
        for key in keys:
            value = cls._safe_probability(raw.get(key))
            if value is not None:
                return value
        return None

    @classmethod
    def _candle_values(cls, raw: Mapping[str, Any], outcome: str) -> Optional[Tuple[float, float, float, float]]:
        price = raw.get("price")
        if not isinstance(price, Mapping):
            price = raw.get("yes_bid") if outcome == "yes" else raw.get("no_bid")
        if not isinstance(price, Mapping):
            return None
        values: Dict[str, float] = {}
        for name in ("open", "high", "low", "close"):
            value = cls._safe_probability(price.get(f"{name}_dollars") or price.get(name))
            if value is None:
                return None
            values[name] = value
        if outcome == "no":
            return (
                round(1.0 - values["open"], 10),
                round(1.0 - values["low"], 10),
                round(1.0 - values["high"], 10),
                round(1.0 - values["close"], 10),
            )
        return (values["open"], values["high"], values["low"], values["close"])

    @staticmethod
    def _timestamp_seconds(value: Any) -> Optional[float]:
        if value in (None, ""):
            return None
        if isinstance(value, (int, float)):
            try:
                parsed = float(value)
            except (TypeError, ValueError):
                return None
            if not math.isfinite(parsed):
                return None
            return parsed / 1000.0 if parsed > 10_000_000_000 else parsed
        text = str(value).strip()
        try:
            parsed = float(text)
            return parsed / 1000.0 if parsed > 10_000_000_000 else parsed
        except ValueError:
            pass
        try:
            parsed_dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if parsed_dt.tzinfo is None:
                parsed_dt = parsed_dt.replace(tzinfo=timezone.utc)
            return parsed_dt.timestamp()
        except ValueError:
            return None

    @staticmethod
    def _positive_number(value: Any) -> Optional[float]:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None
        return parsed if math.isfinite(parsed) and parsed > 0 else None

    @staticmethod
    def _nonnegative_number(value: Any) -> Optional[float]:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None
        return parsed if math.isfinite(parsed) and parsed >= 0 else None

    def _contracts_from_market(self, market: Mapping[str, Any]) -> List[MarketContract]:
        ticker = str(market.get("ticker") or "").strip().upper()
        if not ticker:
            return []
        event_id = self._event_id_for_market(market) or ticker
        title = str(market.get("title") or market.get("subtitle") or ticker)
        status = self._status_from_market(market)
        return [
            MarketContract(
                market_id=self.market_id,
                contract_id=self._contract_id(ticker, "yes"),
                event_id=event_id,
                title=f"{title} - Yes",
                outcome="Yes",
                url=self._market_url(market),
                status=status,
                raw={"market": dict(market), "outcome": "yes"},
            ),
            MarketContract(
                market_id=self.market_id,
                contract_id=self._contract_id(ticker, "no"),
                event_id=event_id,
                title=f"{title} - No",
                outcome="No",
                url=self._market_url(market),
                status=status,
                raw={"market": dict(market), "outcome": "no"},
            ),
        ]

    def _build_live_order_payload(self, order: PaperOrderRequest) -> Dict[str, Any]:
        ticker, outcome = self._split_contract_id(order.contract_id)
        if order.limit_price is None:
            raise MarketConfigurationError("Kalshi live trading requires a limit price.")
        side, yes_side_price = self._yes_side_order(order.side, outcome, order.limit_price)
        time_in_force = str(order.metadata.get("time_in_force") or self.config.get("kalshi_time_in_force") or "fill_or_kill")
        if time_in_force not in {"fill_or_kill", "good_till_canceled", "immediate_or_cancel"}:
            raise MarketConfigurationError("Kalshi time_in_force must be fill_or_kill, good_till_canceled, or immediate_or_cancel.")
        self_trade_prevention = str(
            order.metadata.get("self_trade_prevention_type")
            or self.config.get("kalshi_self_trade_prevention_type")
            or "taker_at_cross"
        )
        if self_trade_prevention not in {"taker_at_cross", "maker"}:
            raise MarketConfigurationError("Kalshi self_trade_prevention_type must be taker_at_cross or maker.")

        payload: Dict[str, Any] = {
            "ticker": ticker,
            "client_order_id": str(order.metadata.get("client_order_id") or f"pmacg-{uuid.uuid4().hex}"),
            "side": side,
            "count": self._fixed_decimal(order.size),
            "price": self._fixed_decimal(yes_side_price, places=4),
            "time_in_force": time_in_force,
            "self_trade_prevention_type": self_trade_prevention,
        }
        for key in ("expiration_time", "post_only", "cancel_order_on_pause", "reduce_only", "subaccount", "order_group_id"):
            if key in order.metadata:
                payload[key] = order.metadata[key]
        if "exchange_index" in order.metadata:
            payload["exchange_index"] = order.metadata["exchange_index"]
        return payload

    def _auth_headers(self, method: str, path: str) -> Dict[str, str]:
        api_key = self.resolve_credential(
            "kalshi_api_key_id",
            ("KALSHI_API_KEY_ID",),
            required=True,
            label="KALSHI_API_KEY_ID",
        )
        private_key_bytes = self._load_private_key_bytes()
        timestamp_ms = str(int(time.time() * 1000))
        request_path = self._request_path(path)
        message = f"{timestamp_ms}{method.upper()}{request_path}"
        signature = self._sign_pss(private_key_bytes, message)
        return {
            "KALSHI-ACCESS-KEY": api_key.value,
            "KALSHI-ACCESS-TIMESTAMP": timestamp_ms,
            "KALSHI-ACCESS-SIGNATURE": signature,
        }

    def _load_private_key_bytes(self) -> bytes:
        pem = self.resolve_credential(
            "kalshi_private_key_pem",
            ("KALSHI_PRIVATE_KEY_PEM",),
            label="KALSHI_PRIVATE_KEY_PEM",
        )
        if pem:
            return pem.value.encode("utf-8")

        path_credential = self.resolve_credential(
            "kalshi_private_key_path",
            ("KALSHI_PRIVATE_KEY_PATH",),
            required=True,
            label="KALSHI_PRIVATE_KEY_PATH",
        )
        path = Path(path_credential.value).expanduser()
        try:
            return path.read_bytes()
        except OSError as exc:
            raise MarketConfigurationError(f"Kalshi private key file could not be read: {path}") from exc

    def _sign_pss(self, private_key_bytes: bytes, message: str) -> str:
        try:
            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography.hazmat.primitives.asymmetric import padding
        except Exception as exc:
            raise MarketConfigurationError(
                "Kalshi live trading requires the cryptography package. Install project dependencies with pip install -r requirements.txt."
            ) from exc

        password_credential = self.resolve_credential(
            "kalshi_private_key_password",
            ("KALSHI_PRIVATE_KEY_PASSWORD",),
            label="KALSHI_PRIVATE_KEY_PASSWORD",
        )
        password = password_credential.value.encode("utf-8") if password_credential else None
        try:
            private_key = serialization.load_pem_private_key(private_key_bytes, password=password)
            signature = private_key.sign(
                message.encode("utf-8"),
                padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
                hashes.SHA256(),
            )
        except Exception as exc:
            raise MarketConfigurationError("Kalshi private key could not sign the request.") from exc
        return base64.b64encode(signature).decode("ascii")

    def _validate_order(self, order: PaperOrderRequest) -> None:
        self.ensure_order_market(order)
        self._split_contract_id(order.contract_id)
        side = str(order.side or "").upper()
        if side not in {"BUY", "SELL"}:
            raise MarketConfigurationError("Kalshi order side must be BUY or SELL.")
        if not self._is_positive_number(order.size):
            raise MarketConfigurationError("Kalshi order size must be positive.")
        if order.limit_price is not None and self._safe_probability(order.limit_price) is None:
            raise MarketConfigurationError("Kalshi limit price must be between 0 and 1.")

    @staticmethod
    def _orderbook_payload(payload: Any) -> Mapping[str, Any]:
        if not isinstance(payload, Mapping):
            return {}
        for key in ("orderbook_fp", "orderbook", "book"):
            value = payload.get(key)
            if isinstance(value, Mapping):
                return value
        return payload

    @staticmethod
    def _levels(raw_levels: Any, *, descending: bool = False) -> List[OrderBookLevel]:
        levels: List[OrderBookLevel] = []
        if not isinstance(raw_levels, list):
            return levels
        for raw in raw_levels:
            price: Any
            size: Any
            if isinstance(raw, (list, tuple)) and len(raw) >= 2:
                price, size = raw[0], raw[1]
            elif isinstance(raw, Mapping):
                price = raw.get("price") or raw.get("price_dollars") or raw.get("yes_price") or raw.get("no_price")
                size = raw.get("size") or raw.get("count") or raw.get("quantity") or raw.get("count_fp")
            else:
                continue
            parsed_price = KalshiAdapter._safe_probability(price)
            try:
                parsed_size = float(size)
            except (TypeError, ValueError):
                continue
            if parsed_price is None or not KalshiAdapter._is_positive_number(parsed_size):
                continue
            levels.append(OrderBookLevel(price=parsed_price, size=parsed_size))
        levels.sort(key=lambda level: level.price, reverse=descending)
        return levels

    @staticmethod
    def _asks_from_opposite_bids(levels: List[OrderBookLevel]) -> List[OrderBookLevel]:
        asks = [
            OrderBookLevel(price=round(1.0 - level.price, 10), size=level.size)
            for level in levels
            if 0.0 <= 1.0 - level.price <= 1.0
        ]
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
        if number > 1.0:
            if isinstance(value, str) and "." in value:
                return None
            number = number / 100.0
        if number < 0.0 or number > 1.0:
            return None
        return number

    @staticmethod
    def _is_positive_number(value: Any) -> bool:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return False
        return math.isfinite(number) and number > 0

    @staticmethod
    def _split_contract_id(contract_id: str) -> Tuple[str, str]:
        raw = str(contract_id or "").strip()
        if not raw:
            raise MarketConfigurationError("Kalshi order requires a contract id.")
        if ":" in raw:
            ticker, outcome = raw.rsplit(":", 1)
        else:
            ticker, outcome = raw, "yes"
        ticker = ticker.strip().upper()
        outcome = outcome.strip().lower()
        if not ticker:
            raise MarketConfigurationError("Kalshi order requires a market ticker.")
        if outcome not in {"yes", "no"}:
            raise MarketConfigurationError("Kalshi contract outcome must be YES or NO.")
        return ticker, outcome

    @staticmethod
    def _contract_id(ticker: str, outcome: str) -> str:
        return f"{ticker.upper()}:{outcome.upper()}"

    @staticmethod
    def _yes_side_order(order_side: str, outcome: str, limit_price: float) -> Tuple[str, float]:
        side = str(order_side or "").upper()
        price = float(limit_price)
        if outcome == "yes":
            return ("bid", price) if side == "BUY" else ("ask", price)
        yes_side_price = 1.0 - price
        return ("ask", yes_side_price) if side == "BUY" else ("bid", yes_side_price)

    @staticmethod
    def _fixed_decimal(value: Any, *, places: int = 2) -> str:
        return f"{float(value):.{places}f}"

    @staticmethod
    def _event_id_for_market(market: Mapping[str, Any]) -> str:
        return str(market.get("event_ticker") or market.get("ticker") or "").strip().upper()

    @staticmethod
    def _event_title(market: Mapping[str, Any]) -> str:
        return str(
            market.get("event_title")
            or market.get("title")
            or market.get("subtitle")
            or market.get("event_ticker")
            or market.get("ticker")
            or ""
        )

    @staticmethod
    def _event_status(markets: List[Mapping[str, Any]]) -> str:
        statuses = [KalshiAdapter._status_from_market(market) for market in markets]
        if any(status in {"open", "active"} for status in statuses):
            return "active"
        return statuses[0] if statuses else ""

    @staticmethod
    def _status_from_market(market: Mapping[str, Any]) -> str:
        status = str(market.get("status") or "").strip().lower()
        return "active" if status == "open" else status

    @staticmethod
    def _market_matches_query(market: Mapping[str, Any], query: str) -> bool:
        haystack = " ".join(
            str(market.get(key) or "")
            for key in (
                "ticker",
                "event_ticker",
                "series_ticker",
                "title",
                "subtitle",
                "yes_sub_title",
                "no_sub_title",
            )
        ).lower()
        return query in haystack

    @staticmethod
    def _market_url(market: Mapping[str, Any]) -> str:
        ticker = str(market.get("ticker") or "").strip()
        return f"https://kalshi.com/markets/{ticker}" if ticker else "https://kalshi.com"
