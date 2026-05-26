from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Tuple
from urllib.parse import urlparse

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


DEFAULT_LIMITLESS_BASE_URL = "https://api.limitless.exchange"
DEFAULT_LIMITLESS_WS_URL = "wss://ws.limitless.exchange"
LIMITLESS_WS_NAMESPACE = "/markets"
LIMITLESS_REFERENCES = (
    "https://docs.limitless.exchange/api-reference/markets/browse-active",
    "https://docs.limitless.exchange/developers/sdk/python/markets",
    "https://docs.limitless.exchange/developers/authentication",
    "https://docs.limitless.exchange/developers/programmatic-api",
    "https://docs.limitless.exchange/developers/quickstart/websocket",
)


class LimitlessAdapter(MarketAdapter):
    """Limitless Exchange adapter using documented REST market data and HMAC trading APIs."""

    metadata = get_market_metadata("limitless_exchange")

    def health_check(self) -> Dict[str, Any]:
        health = super().health_check()
        token_id = self.resolve_credential(
            "limitless_token_id",
            ("LIMITLESS_TOKEN_ID", "LMTS_API_KEY"),
            label="LIMITLESS_TOKEN_ID",
        )
        token_secret = self.resolve_credential(
            "limitless_token_secret",
            ("LIMITLESS_TOKEN_SECRET",),
            label="LIMITLESS_TOKEN_SECRET",
        )
        on_behalf_of = self.resolve_credential(
            "limitless_on_behalf_of",
            ("LIMITLESS_ON_BEHALF_OF",),
            label="LIMITLESS_ON_BEHALF_OF",
        )
        credential_sources = []
        for credential in (token_id, token_secret, on_behalf_of):
            if credential:
                credential_sources.append({"name": credential.name, "source": credential.source})
        health.update(
            {
                "api_base_url": self.api_base_url,
                "websocket_url": self.websocket_url,
                "websocket_namespace": LIMITLESS_WS_NAMESPACE,
                "references": list(LIMITLESS_REFERENCES),
                "live_trading_enabled": self.config_bool("live_trading_enabled", False),
                "credential_sources": credential_sources,
            }
        )
        return health

    @property
    def api_base_url(self) -> str:
        configured = self.config.get("limitless_api_base_url") or self.config.get("api_base_url")
        return str(configured or DEFAULT_LIMITLESS_BASE_URL).rstrip("/")

    @property
    def websocket_url(self) -> str:
        configured = self.config.get("limitless_ws_url") or self.config.get("websocket_url")
        return str(configured or DEFAULT_LIMITLESS_WS_URL).rstrip("/")

    def list_events(self, query: str = "", limit: int = 50) -> List[MarketEvent]:
        self.ensure_capability("event_listing")
        desired = max(1, min(int(limit or 50), 100))
        markets = self._fetch_active_markets(limit=desired)
        q = str(query or "").strip().lower()
        if q:
            markets = [market for market in markets if self._market_matches_query(market, q)]
        return [self._event_from_market(market) for market in markets[:desired]]

    def list_contracts(self, event_id: str) -> List[MarketContract]:
        self.ensure_capability("event_listing")
        market = self._get_market(str(event_id or "").strip())
        if not market:
            return []
        return self._contracts_from_market(market)

    def get_orderbook(self, contract_id: str) -> OrderBookSnapshot:
        self.ensure_capability("orderbook_reading")
        slug, outcome = self._split_contract_id(contract_id)
        payload = self._get(f"/markets/{slug}/orderbook")
        yes_bids = self._levels(self._value_at(payload, "bids", "yesBids", "yes_bids"), descending=True)
        yes_asks = self._levels(self._value_at(payload, "asks", "yesAsks", "yes_asks"))

        if outcome == "YES":
            bids = yes_bids
            asks = yes_asks
        else:
            bids = self._opposite_bids_from_yes_asks(yes_asks)
            asks = self._opposite_asks_from_yes_bids(yes_bids)

        return OrderBookSnapshot(
            market_id=self.market_id,
            contract_id=self._contract_id(slug, outcome),
            bids=bids,
            asks=asks,
            raw=payload if isinstance(payload, dict) else {},
        )

    def get_price(self, contract_id: str) -> PriceSnapshot:
        self.ensure_capability("price_reading")
        slug, outcome = self._split_contract_id(contract_id)
        orderbook = self.get_orderbook(self._contract_id(slug, outcome))
        bid = orderbook.bids[0].price if orderbook.bids else None
        ask = orderbook.asks[0].price if orderbook.asks else None
        midpoint = (bid + ask) / 2.0 if bid is not None and ask is not None else None
        last = midpoint
        if last is None:
            market = self._get_market(slug)
            last = self._price_from_market(market, outcome)
        return PriceSnapshot(
            market_id=self.market_id,
            contract_id=self._contract_id(slug, outcome),
            last=last,
            bid=bid,
            ask=ask,
            midpoint=midpoint,
            source="limitless_orderbook",
            raw=orderbook.raw,
        )

    def place_paper_order(self, order: PaperOrderRequest) -> PaperOrderResult:
        self.ensure_capability("paper_trading")
        self._validate_order(order)
        slug, outcome = self._split_contract_id(order.contract_id)
        return PaperOrderResult(
            market_id=self.market_id,
            contract_id=self._contract_id(slug, outcome),
            accepted=True,
            message=(
                f"DRY RUN: would place Limitless {order.side.upper()} "
                f"for {order.size:.4f} {outcome} shares"
                + (f" at limit {order.limit_price:.2f}" if order.limit_price is not None else "")
            ),
            filled_size=0.0,
            average_price=None,
            raw={"request": self._build_delegated_order_payload(order, dry_run=True)},
        )

    def place_live_order(self, order: PaperOrderRequest) -> Dict[str, Any]:
        self.ensure_capability("live_trading")
        self._validate_order(order)
        preflight = self.preflight_live_order(order)
        payload = self._build_delegated_order_payload(order, dry_run=False)
        body = self._canonical_json(payload)
        response = self._post_signed_json("/orders", payload, body=body)
        return {
            "market_id": self.market_id,
            "contract_id": self._contract_id(*self._split_contract_id(order.contract_id)),
            "live": True,
            "preflight": preflight,
            "request": payload,
            "response": response,
        }

    def copy_trade_from_activity(self, activity: Mapping[str, Any]) -> PaperOrderResult:
        raise UnsupportedFeatureError(
            self.market_id,
            "copy_trading",
            "Limitless copy trading is unsupported because this adapter has no official account activity mirroring model.",
        )

    def websocket_connection_info(
        self,
        *,
        market_slugs: Optional[List[str]] = None,
        market_addresses: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Return the documented Socket.IO connection and subscription shape.

        The project does not maintain a long-running Socket.IO client here; this
        method keeps GUI/alert code from hardcoding Limitless channel names.
        """

        payload = self.websocket_market_subscription(
            market_slugs=market_slugs,
            market_addresses=market_addresses,
        )
        return {
            "url": self.websocket_url,
            "namespace": LIMITLESS_WS_NAMESPACE,
            "transports": ["websocket"],
            "events": ["newPriceData", "orderbookUpdate", "system", "exception"],
            "subscribe": payload,
        }

    @staticmethod
    def websocket_market_subscription(
        *,
        market_slugs: Optional[List[str]] = None,
        market_addresses: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        slugs = [str(slug).strip() for slug in (market_slugs or []) if str(slug).strip()]
        addresses = [str(address).strip() for address in (market_addresses or []) if str(address).strip()]
        if not slugs and not addresses:
            raise MarketConfigurationError("Limitless WebSocket market subscription requires slugs or addresses.")
        payload: Dict[str, Any] = {}
        if addresses:
            payload["marketAddresses"] = addresses
        if slugs:
            payload["marketSlugs"] = slugs
        return {
            "event": "subscribe_market_prices",
            "namespace": LIMITLESS_WS_NAMESPACE,
            "payload": payload,
        }

    def _fetch_active_markets(self, *, limit: int) -> List[Mapping[str, Any]]:
        params = {
            "page": 1,
            "limit": max(1, min(int(limit or 50), 100)),
            "sortBy": str(self.config.get("limitless_sort_by") or "volume"),
        }
        trade_type = str(self.config.get("limitless_trade_type") or "").strip()
        if trade_type:
            params["tradeType"] = trade_type
        data = self.runtime.get_json(self._url("/markets/active"), params=params)
        markets = data.get("data") if isinstance(data, Mapping) else []
        return [market for market in markets if isinstance(market, Mapping)] if isinstance(markets, list) else []

    def _get_market(self, slug_or_id: str) -> Mapping[str, Any]:
        ref = str(slug_or_id or "").strip()
        if not ref:
            raise MarketConfigurationError("Limitless market slug cannot be empty.")
        data = self._get(f"/markets/{ref}")
        if isinstance(data, Mapping):
            market = data.get("market")
            if isinstance(market, Mapping):
                return market
            if "slug" in data or "title" in data:
                return data
        raise MarketConfigurationError(f"Limitless market {ref!r} was not found.")

    def _get(self, path: str) -> Any:
        return self.runtime.get_json(self._url(path))

    def _url(self, path: str) -> str:
        clean_path = "/" + str(path or "").strip("/")
        return f"{self.api_base_url}{clean_path}"

    def _build_delegated_order_payload(self, order: PaperOrderRequest, *, dry_run: bool) -> Dict[str, Any]:
        slug, outcome = self._split_contract_id(order.contract_id)
        side = str(order.side or "").upper()
        order_type = str(order.metadata.get("order_type") or self.config.get("limitless_order_type") or "GTC").upper()
        if order_type not in {"GTC", "FAK", "FOK"}:
            raise MarketConfigurationError("Limitless order_type must be GTC, FAK, or FOK.")
        if order_type in {"GTC", "FAK"} and order.limit_price is None:
            raise MarketConfigurationError("Limitless GTC/FAK live and paper orders require a limit price.")

        args: Dict[str, Any] = {
            "tokenId": str(order.metadata.get("token_id") or self._token_id_for_outcome(slug, outcome)),
            "side": side,
        }
        if order_type == "FOK":
            maker_amount = order.metadata.get("maker_amount", order.size)
            if not self._is_positive_number(maker_amount):
                raise MarketConfigurationError("Limitless FOK maker_amount must be positive.")
            args["makerAmount"] = float(maker_amount)
        else:
            args["price"] = self._limit_probability(order.limit_price)
            args["size"] = float(order.size)
            if order_type == "GTC" and "post_only" in order.metadata:
                args["postOnly"] = bool(order.metadata["post_only"])

        payload: Dict[str, Any] = {
            "marketSlug": slug,
            "orderType": order_type,
            "onBehalfOf": str(
                order.metadata.get("on_behalf_of")
                or self.config.get("limitless_on_behalf_of")
                or self._required_on_behalf_of(dry_run=dry_run)
            ),
            "args": args,
        }
        if dry_run:
            payload["dryRun"] = True
        return payload

    def _token_id_for_outcome(self, slug: str, outcome: str) -> str:
        market = self._get_market(slug)
        tokens = market.get("tokens")
        if isinstance(tokens, Mapping):
            value = tokens.get(outcome.lower()) or tokens.get(outcome.upper())
            if value:
                return str(value)
        position_ids = market.get("positionIds") or market.get("position_ids")
        if isinstance(position_ids, list) and len(position_ids) >= 2:
            return str(position_ids[0 if outcome == "YES" else 1])
        raise MarketConfigurationError(f"Limitless market {slug!r} did not include token IDs for {outcome}.")

    def _required_on_behalf_of(self, *, dry_run: bool) -> str:
        credential = self.resolve_credential(
            "limitless_on_behalf_of",
            ("LIMITLESS_ON_BEHALF_OF",),
            required=not dry_run,
            label="LIMITLESS_ON_BEHALF_OF",
        )
        if credential:
            return credential.value
        return "dry-run-profile"

    def _hmac_headers(self, method: str, path: str, body: str = "") -> Dict[str, str]:
        token_id = self.resolve_credential(
            "limitless_token_id",
            ("LIMITLESS_TOKEN_ID", "LMTS_API_KEY"),
            required=True,
            label="LIMITLESS_TOKEN_ID",
        )
        token_secret = self.resolve_credential(
            "limitless_token_secret",
            ("LIMITLESS_TOKEN_SECRET",),
            required=True,
            label="LIMITLESS_TOKEN_SECRET",
        )
        timestamp = datetime.now(timezone.utc).isoformat()
        request_path = self._request_path(path)
        message = f"{timestamp}\n{method.upper()}\n{request_path}\n{body}"
        try:
            secret = base64.b64decode(token_secret.value)
        except Exception as exc:
            raise MarketConfigurationError("Limitless token secret must be base64-encoded.") from exc
        signature = base64.b64encode(hmac.new(secret, message.encode("utf-8"), hashlib.sha256).digest()).decode(
            "utf-8"
        )
        return {
            "lmts-api-key": token_id.value,
            "lmts-timestamp": timestamp,
            "lmts-signature": signature,
        }

    def _post_signed_json(self, path: str, payload: Mapping[str, Any], *, body: Optional[str] = None) -> Any:
        request_body = body if body is not None else self._canonical_json(payload)
        headers = self._hmac_headers("POST", path, request_body)
        headers.update({"Accept": "application/json", "Content-Type": "application/json", "User-Agent": self.runtime.user_agent})
        self.runtime.rate_limiter.wait()
        try:
            response = self.runtime.session.request(
                "POST",
                self._url(path),
                data=request_body,
                headers=headers,
                timeout=self.runtime.timeout_seconds,
            )
        except Exception as exc:
            raise MarketHTTPError(f"{self.market_id} HTTP request failed: {exc}") from exc

        status = int(getattr(response, "status_code", 0) or 0)
        if status >= 400:
            text = str(getattr(response, "text", "") or "")
            raise MarketHTTPError(f"{self.market_id} HTTP {status}: {text[:200]}")
        try:
            return response.json()
        except ValueError as exc:
            raise MarketHTTPError(f"{self.market_id} response was not valid JSON.") from exc

    def _request_path(self, path_or_url: str) -> str:
        parsed = urlparse(path_or_url if "://" in path_or_url else self._url(path_or_url))
        return parsed.path + (f"?{parsed.query}" if parsed.query else "")

    def _event_from_market(self, market: Mapping[str, Any]) -> MarketEvent:
        slug = self._market_slug(market)
        return MarketEvent(
            market_id=self.market_id,
            event_id=slug,
            title=str(market.get("title") or market.get("name") or slug),
            url=self._market_url(market),
            status=self._status_from_market(market),
            raw=dict(market),
        )

    def _contracts_from_market(self, market: Mapping[str, Any]) -> List[MarketContract]:
        slug = self._market_slug(market)
        if not slug:
            return []
        title = str(market.get("title") or market.get("name") or slug)
        status = self._status_from_market(market)
        return [
            MarketContract(
                market_id=self.market_id,
                contract_id=self._contract_id(slug, "YES"),
                event_id=slug,
                title=f"{title} - Yes",
                outcome="Yes",
                url=self._market_url(market),
                status=status,
                raw={"market": dict(market), "outcome": "YES"},
            ),
            MarketContract(
                market_id=self.market_id,
                contract_id=self._contract_id(slug, "NO"),
                event_id=slug,
                title=f"{title} - No",
                outcome="No",
                url=self._market_url(market),
                status=status,
                raw={"market": dict(market), "outcome": "NO"},
            ),
        ]

    def _validate_order(self, order: PaperOrderRequest) -> None:
        self.ensure_order_market(order)
        self._split_contract_id(order.contract_id)
        side = str(order.side or "").upper()
        if side not in {"BUY", "SELL"}:
            raise MarketConfigurationError("Limitless order side must be BUY or SELL.")
        if not self._is_positive_number(order.size):
            raise MarketConfigurationError("Limitless order size must be positive.")
        if order.limit_price is not None:
            self._limit_probability(order.limit_price)

    @staticmethod
    def _market_matches_query(market: Mapping[str, Any], query: str) -> bool:
        values = [
            market.get("id"),
            market.get("slug"),
            market.get("title"),
            market.get("description"),
            market.get("tradeType"),
            " ".join(str(tag) for tag in market.get("tags") or []),
            " ".join(str(category) for category in market.get("categories") or []),
        ]
        return query in " ".join(str(value or "") for value in values).lower()

    @staticmethod
    def _status_from_market(market: Mapping[str, Any]) -> str:
        if market.get("expired") is True:
            return "expired"
        status = str(market.get("status") or "").strip().lower()
        if status in {"funded", "open", "active"}:
            return "active"
        return status

    @staticmethod
    def _market_url(market: Mapping[str, Any]) -> str:
        raw = str(market.get("url") or "").strip()
        if raw:
            return raw
        slug = LimitlessAdapter._market_slug(market)
        return f"https://limitless.exchange/markets/{slug}" if slug else "https://limitless.exchange"

    @staticmethod
    def _market_slug(market: Mapping[str, Any]) -> str:
        return str(market.get("slug") or market.get("marketSlug") or market.get("id") or "").strip()

    @staticmethod
    def _split_contract_id(contract_id: str) -> Tuple[str, str]:
        raw = str(contract_id or "").strip()
        if not raw:
            raise MarketConfigurationError("Limitless order requires a contract id.")
        if ":" in raw:
            slug, outcome = raw.rsplit(":", 1)
        else:
            slug, outcome = raw, "YES"
        slug = slug.strip()
        outcome = outcome.strip().upper()
        if not slug:
            raise MarketConfigurationError("Limitless contract id must include a market slug.")
        if outcome not in {"YES", "NO"}:
            raise MarketConfigurationError("Limitless contract outcome must be YES or NO.")
        return slug, outcome

    @staticmethod
    def _contract_id(slug: str, outcome: str) -> str:
        return f"{slug}:{outcome.upper()}"

    @staticmethod
    def _price_from_market(market: Mapping[str, Any], outcome: str) -> Optional[float]:
        prices = market.get("prices")
        if isinstance(prices, list) and len(prices) >= 2:
            return LimitlessAdapter._safe_probability(prices[0 if outcome == "YES" else 1])
        return None

    @staticmethod
    def _value_at(data: Any, *keys: str) -> Any:
        if not isinstance(data, Mapping):
            return []
        for key in keys:
            value = data.get(key)
            if value is not None:
                return value
        orderbook = data.get("orderbook") or data.get("book")
        if isinstance(orderbook, Mapping):
            for key in keys:
                value = orderbook.get(key)
                if value is not None:
                    return value
        return []

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
                price = raw.get("price") or raw.get("p")
                size = raw.get("size") or raw.get("quantity") or raw.get("q")
            else:
                continue
            parsed_price = LimitlessAdapter._safe_probability(price)
            try:
                parsed_size = float(size)
            except (TypeError, ValueError):
                continue
            if parsed_price is None or not LimitlessAdapter._is_positive_number(parsed_size):
                continue
            levels.append(OrderBookLevel(price=parsed_price, size=parsed_size))
        levels.sort(key=lambda level: level.price, reverse=descending)
        return levels

    @staticmethod
    def _opposite_bids_from_yes_asks(levels: List[OrderBookLevel]) -> List[OrderBookLevel]:
        bids = [
            OrderBookLevel(price=round(1.0 - level.price, 10), size=level.size)
            for level in levels
            if 0.0 <= 1.0 - level.price <= 1.0
        ]
        bids.sort(key=lambda level: level.price, reverse=True)
        return bids

    @staticmethod
    def _opposite_asks_from_yes_bids(levels: List[OrderBookLevel]) -> List[OrderBookLevel]:
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
            if number <= 100.0:
                number = number / 100.0
            else:
                return None
        if number < 0.0 or number > 1.0:
            return None
        return number

    @staticmethod
    def _limit_probability(value: Any) -> float:
        probability = LimitlessAdapter._safe_probability(value)
        if probability is None or probability <= 0.0 or probability >= 1.0:
            raise MarketConfigurationError("Limitless limit price must be between 0 and 1.")
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
