from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping, Optional, Tuple

from .base import MarketAdapter
from .catalog import get_market_metadata
from .errors import MarketConfigurationError, MarketHTTPError
from .identity import require_activity_identity
from .types import (
    MarketContract,
    MarketEvent,
    MarketTrade,
    OrderBookLevel,
    OrderBookSnapshot,
    PaperOrderRequest,
    PaperOrderResult,
    PriceSnapshot,
)


DEFAULT_MYRIAD_BASE_URL = "https://api-v2.myriadprotocol.com"
MYRIAD_REFERENCES = (
    "https://docs.myriad.markets/builders/myriad-api-reference",
    "https://docs.myriad.markets/builders/myriad-order-book",
    "https://docs.myriad.markets/builders/javascript-sdk",
)


class MyriadAdapter(MarketAdapter):
    """Myriad Markets adapter using the documented public protocol API."""

    metadata = get_market_metadata("myriad_markets")

    def health_check(self) -> Dict[str, Any]:
        health = super().health_check()
        api_key = self.resolve_credential("myriad_api_key", ("MYRIAD_API_KEY",), label="MYRIAD_API_KEY")
        health.update(
            {
                "api_base_url": self.api_base_url,
                "references": list(MYRIAD_REFERENCES),
                "credential_sources": [{"name": api_key.name, "source": api_key.source}] if api_key else [],
                "live_trading_supported": True,
                "activity_feed_supported": bool(self.capabilities.copy_trading),
                "copy_trading_supported": bool(self.capabilities.copy_trading),
                "live_trading_enabled": self.config_bool("live_trading_enabled", False),
            }
        )
        return health

    @property
    def api_base_url(self) -> str:
        configured = self.config.get("myriad_api_base_url") or self.config.get("api_base_url")
        return str(configured or DEFAULT_MYRIAD_BASE_URL).rstrip("/")

    def list_events(self, query: str = "", limit: int = 50) -> List[MarketEvent]:
        self.ensure_capability("event_listing")
        desired = max(1, min(int(limit or 50), 100))
        params: Dict[str, Any] = {"page": 1, "limit": desired}
        if query:
            params["keyword"] = str(query).strip()
        payload = self._get("/questions", params=params)
        questions = self._list_from_payload(payload, "data", "questions", "results")
        return [self._event_from_question(question) for question in questions[:desired]]

    def list_contracts(self, event_id: str) -> List[MarketContract]:
        self.ensure_capability("event_listing")
        question = self._get_question(event_id)
        return self._contracts_from_question(question)

    def get_price(self, contract_id: str) -> PriceSnapshot:
        self.ensure_capability("price_reading")
        market_id, outcome_id = self._split_contract_id(contract_id)
        market = self._get_market(market_id)
        outcome = self._find_outcome(market, outcome_id)
        if not outcome:
            raise MarketConfigurationError(f"Myriad outcome {outcome_id!r} was not found in market {market_id!r}.")
        price = self._safe_probability(outcome.get("price"))
        return PriceSnapshot(
            market_id=self.market_id,
            contract_id=self._contract_id(market_id, outcome_id),
            last=price,
            bid=None,
            ask=None,
            midpoint=price,
            source="myriad_market_outcome",
            raw={"market": dict(market), "outcome": dict(outcome)},
        )

    def get_orderbook(self, contract_id: str) -> OrderBookSnapshot:
        self.ensure_capability("orderbook_reading")
        market_id, outcome_id = self._split_contract_id(contract_id)
        params: Dict[str, Any] = {"outcome": self._orderbook_outcome_param(outcome_id)}
        network_id = self.config.get("myriad_network_id")
        if network_id not in (None, ""):
            params["network_id"] = network_id
        payload = self._get(f"/markets/{market_id}/orderbook", params=params)
        data = payload.get("data") if isinstance(payload, Mapping) else payload
        orderbook = data if isinstance(data, Mapping) else payload if isinstance(payload, Mapping) else {}
        return OrderBookSnapshot(
            market_id=self.market_id,
            contract_id=self._contract_id(market_id, outcome_id),
            bids=self._book_levels(orderbook.get("bids"), descending=True),
            asks=self._book_levels(orderbook.get("asks")),
            raw=orderbook,
        )

    def list_trades(
        self,
        contract_id: str,
        *,
        limit: int = 50,
        before: Optional[float] = None,
        after: Optional[float] = None,
    ) -> List[MarketTrade]:
        """Return normalized public Myriad order-book trades for one outcome.

        Myriad documents ``GET /markets/:id/trades`` for recent order-book
        matches.  The endpoint accepts only pagination/outcome filters, so the
        shared timestamp bounds are applied locally after parsing the newest
        page.  Amounts are returned in the quote token's smallest unit and are
        normalized to the common human-sized trade model.
        """

        market_id, outcome_id = self._split_contract_id(contract_id)
        desired = self._trade_limit(limit)
        before_ts = self._history_timestamp_bound(before, "before") if before is not None else None
        after_ts = self._history_timestamp_bound(after, "after") if after is not None else None
        if before_ts is not None and after_ts is not None and before_ts <= after_ts:
            raise MarketConfigurationError("Myriad trade history requires before greater than after.")

        params: Dict[str, Any] = {
            "page": 1,
            "limit": desired,
            "outcome": self._orderbook_outcome_param(outcome_id),
        }
        network_id = self.config.get("myriad_network_id")
        if network_id not in (None, ""):
            params["network_id"] = network_id
        payload = self._get(f"/markets/{market_id}/trades", params=params)
        rows = self._list_from_payload(payload, "trades", "data", "results", "items")
        canonical = self._contract_id(market_id, outcome_id)
        trades: List[MarketTrade] = []
        for row in rows:
            raw_outcome = row.get("outcome")
            if raw_outcome not in (None, "") and str(raw_outcome).strip() != str(outcome_id):
                continue
            price = self._safe_probability(row.get("price"))
            size = self._scaled_decimal(row.get("amount") if row.get("amount") is not None else row.get("size"))
            side = str(row.get("side") or "").strip().upper()
            if price is None or size is None or size <= 0 or side not in {"BUY", "SELL", "SPLIT", "MERGE"}:
                continue
            timestamp = self._timestamp_seconds(row.get("timestamp") or row.get("ts"))
            if timestamp <= 0:
                continue
            if before_ts is not None and timestamp > before_ts:
                continue
            if after_ts is not None and timestamp < after_ts:
                continue
            trade_id = str(row.get("txHash") or row.get("tradeId") or row.get("id") or "").strip()
            if not trade_id:
                continue
            trades.append(
                MarketTrade(
                    market_id=self.market_id,
                    contract_id=canonical,
                    trade_id=trade_id,
                    side=side,
                    price=price,
                    size=size,
                    timestamp=float(timestamp),
                    raw=dict(row),
                )
            )
        return trades

    def place_paper_order(self, order: PaperOrderRequest) -> PaperOrderResult:
        self.ensure_capability("paper_trading")
        self._validate_order(order)
        market_id, outcome_id = self._split_contract_id(order.contract_id)
        quote_payload = self._quote_payload(order, market_id=market_id, outcome_id=outcome_id)
        return PaperOrderResult(
            market_id=self.market_id,
            contract_id=self._contract_id(market_id, outcome_id),
            accepted=True,
            message=(
                f"DRY RUN: would request a Myriad {order.side.upper()} quote for "
                f"{order.size:.4f} {'shares' if order.side.upper() == 'SELL' else 'collateral'}"
            ),
            raw={"request": quote_payload, "endpoint": "/markets/quote"},
        )

    def place_live_order(self, order: PaperOrderRequest) -> Dict[str, Any]:
        self.ensure_capability("live_trading")
        self._validate_order(order)
        preflight = self.preflight_live_order(order)
        payload = self._live_order_payload(order)
        response = self._post("/orders", payload)
        return {
            "market_id": self.market_id,
            "contract_id": order.contract_id,
            "live": True,
            "preflight": preflight,
            "request": payload,
            "response": response,
        }

    def copy_trade_from_activity(self, activity: Mapping[str, Any]) -> PaperOrderResult:
        self.ensure_capability("copy_trading")
        contract_id = str(activity.get("asset") or activity.get("contract_id") or "").strip()
        if not contract_id:
            raise MarketConfigurationError("Myriad activity has no market/outcome contract id.")
        side = str(activity.get("side") or "").strip().upper()
        if side not in {"BUY", "SELL"}:
            raise MarketConfigurationError("Myriad activity side must be BUY or SELL.")
        size = self._required_positive_number(activity.get("size"), "Myriad activity size")
        raw_price = activity.get("price")
        limit_price = None if raw_price in (None, "") else self._safe_probability(raw_price)
        if raw_price not in (None, "") and limit_price is None:
            raise MarketConfigurationError("Myriad activity reference price must be between 0 and 1.")
        metadata: Dict[str, Any] = {"activity": dict(activity), "source": "myriad_user_event_feed"}
        if side == "SELL":
            metadata["shares"] = activity.get("shares") or size
        return self.place_paper_order(
            PaperOrderRequest(
                market_id=self.market_id,
                contract_id=contract_id,
                side=side,
                size=size,
                limit_price=limit_price,
                metadata=metadata,
            )
        )

    def list_activity(self, wallet_address: str, *, limit: int = 25) -> List[Dict[str, Any]]:
        """Return normalized public buy/sell events for an EVM wallet.

        Myriad documents ``GET /users/:address/events`` as a public read for
        any wallet.  Only trade actions are admitted to the copy workflow;
        liquidity, claim, split, merge, and other account events are ignored.
        The normalized BUY size is collateral value and SELL size is shares,
        matching the Myriad quote contract.
        """

        self.ensure_capability("copy_trading")
        wallet = require_activity_identity(self.market_id, wallet_address)
        desired = max(1, min(int(limit or 25), 100))
        params: Dict[str, Any] = {
            "page": 1,
            "limit": desired,
            "trading_model": str(self.config.get("myriad_activity_trading_model") or "all"),
            "only_relevant": "true",
        }
        network_id = self.config.get("myriad_activity_network_id", self.config.get("myriad_network_id"))
        if network_id not in (None, ""):
            params["network_id"] = network_id
        market_id = str(self.config.get("myriad_activity_market_id") or "").strip()
        if market_id:
            params["market_id"] = market_id
        market_slug = str(self.config.get("myriad_activity_market_slug") or "").strip()
        if market_slug:
            params["market_slug"] = market_slug
        payload = self._get(f"/users/{wallet}/events", params=params)
        activities: List[Dict[str, Any]] = []
        configured_network = str(network_id).strip() if network_id not in (None, "") else ""
        for event in self._activity_rows(payload):
            action = str(event.get("action") or "").strip().lower()
            if action not in {"buy", "sell"}:
                continue
            event_network = event.get("networkId") or event.get("network_id")
            if configured_network and event_network not in (None, "") and str(event_network).strip() != configured_network:
                continue
            try:
                activities.append(self._activity_from_event(wallet, event))
            except MarketConfigurationError:
                # A malformed public event must never become an order intent.
                continue
        return activities[:desired]

    def _activity_from_event(self, wallet: str, event: Mapping[str, Any]) -> Dict[str, Any]:
        market_id = str(event.get("marketId") or event.get("market_id") or "").strip()
        outcome_id = str(event.get("outcomeId") or event.get("outcome_id") or "").strip()
        if not market_id or not outcome_id:
            raise MarketConfigurationError("Myriad user event omitted marketId or outcomeId.")
        action = str(event.get("action") or "").strip().lower()
        side = {"buy": "BUY", "sell": "SELL"}.get(action)
        if side is None:
            raise MarketConfigurationError("Myriad user event has an unsupported action.")
        shares = self._finite_number(event.get("shares"))
        value = self._finite_number(event.get("value"))
        size = value if side == "BUY" else shares
        if size is None or size <= 0:
            raise MarketConfigurationError("Myriad user event did not contain a positive trade size.")
        price = None
        if value is not None and shares is not None and shares > 0:
            price = self._safe_probability(value / shares)
        tx_hash = str(event.get("txId") or event.get("txHash") or event.get("transactionHash") or "").strip()
        timestamp = self._timestamp_seconds(event.get("timestamp") or event.get("createdAt"))
        stable_id = tx_hash or f"{market_id}:{outcome_id}:{timestamp}:{side}:{size}"
        contract_id = self._contract_id(market_id, outcome_id)
        return {
            "type": "TRADE",
            "proxyWallet": wallet,
            "wallet": wallet,
            "asset": contract_id,
            "contract_id": contract_id,
            "marketId": market_id,
            "networkId": event.get("networkId") or event.get("network_id"),
            "side": side,
            "size": size,
            "value": value,
            "shares": shares,
            "price": price,
            "timestamp": timestamp,
            "transactionHash": tx_hash or f"myriad-event:{stable_id}",
            "slug": str(event.get("marketSlug") or market_id),
            "outcome": str(event.get("outcomeTitle") or outcome_id),
            "pseudonym": str(event.get("marketTitle") or ""),
            "raw": dict(event),
        }

    def _get_question(self, question_id: str) -> Mapping[str, Any]:
        clean = str(question_id or "").strip()
        if not clean:
            raise MarketConfigurationError("Myriad question id cannot be empty.")
        payload = self._get(f"/questions/{clean}")
        data = payload.get("data") if isinstance(payload, Mapping) else None
        if isinstance(data, Mapping):
            return data
        if isinstance(payload, Mapping):
            return payload
        raise MarketConfigurationError(f"Myriad question {clean!r} was not found.")

    def _get_market(self, market_id: str) -> Mapping[str, Any]:
        clean = str(market_id or "").strip()
        if not clean:
            raise MarketConfigurationError("Myriad market id cannot be empty.")
        payload = self._get(f"/markets/{clean}")
        data = payload.get("data") if isinstance(payload, Mapping) else None
        if isinstance(data, Mapping):
            return data
        if isinstance(payload, Mapping):
            return payload
        raise MarketConfigurationError(f"Myriad market {clean!r} was not found.")

    def _get(self, path: str, *, params: Optional[Mapping[str, Any]] = None) -> Any:
        return self.runtime.get_json(self._url(path), params=params, headers=self._headers())

    def _post(self, path: str, payload: Mapping[str, Any]) -> Any:
        headers = {"Content-Type": "application/json", **self._headers(required=True)}
        self.runtime.rate_limiter.wait()
        try:
            response = self.runtime.session.request(
                "POST",
                self._url(path),
                json=dict(payload),
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

    def _headers(self, *, required: bool = False) -> Dict[str, str]:
        credential = self.resolve_credential(
            "myriad_api_key",
            ("MYRIAD_API_KEY",),
            required=required,
            label="MYRIAD_API_KEY",
        )
        return {"x-api-key": credential.value} if credential else {}

    def _trade_limit(value: Any) -> int:
        try:
            desired = int(value)
        except (TypeError, ValueError) as exc:
            raise MarketConfigurationError("Myriad trade limit must be an integer between 1 and 200.") from exc
        if desired < 1 or desired > 200:
            raise MarketConfigurationError("Myriad trade limit must be between 1 and 200.")
        return desired

    @staticmethod
    def _history_timestamp_bound(value: Any, label: str) -> int:
        number = MyriadAdapter._finite_number(value)
        if number is None or number <= 0:
            raise MarketConfigurationError(f"Myriad {label} timestamp must be a positive Unix timestamp.")
        return int(number)

    def _event_from_question(self, question: Mapping[str, Any]) -> MarketEvent:
        question_id = self._question_id(question)
        return MarketEvent(
            market_id=self.market_id,
            event_id=question_id,
            title=str(question.get("title") or question.get("question") or question_id),
            url=self._question_url(question),
            status=self._status_from_question(question),
            raw=dict(question),
        )

    def _contracts_from_question(self, question: Mapping[str, Any]) -> List[MarketContract]:
        question_id = self._question_id(question)
        title = str(question.get("title") or question.get("question") or question_id)
        contracts: List[MarketContract] = []
        for market in self._markets_from_question(question):
            market_id = self._market_id(market)
            status = self._status_from_market(market)
            for outcome in self._outcomes_from_market(market):
                outcome_id = self._outcome_id(outcome)
                if not market_id or not outcome_id:
                    continue
                outcome_title = str(outcome.get("title") or outcome.get("name") or outcome_id)
                contracts.append(
                    MarketContract(
                        market_id=self.market_id,
                        contract_id=self._contract_id(market_id, outcome_id),
                        event_id=question_id,
                        title=f"{title} - {outcome_title}",
                        outcome=outcome_title,
                        url=self._market_url(market),
                        status=status,
                        raw={"question": dict(question), "market": dict(market), "outcome": dict(outcome)},
                    )
                )
        return contracts

    def _quote_payload(self, order: PaperOrderRequest, *, market_id: str, outcome_id: str) -> Dict[str, Any]:
        side = str(order.side or "").upper()
        payload: Dict[str, Any] = {
            "market_id": int(market_id) if str(market_id).isdigit() else market_id,
            "outcome_id": int(outcome_id) if str(outcome_id).isdigit() else outcome_id,
            "action": "buy" if side == "BUY" else "sell",
            "slippage": float(order.metadata.get("slippage", self.config.get("myriad_slippage", 0.005))),
        }
        if side == "BUY":
            payload["value"] = float(order.size)
        else:
            payload["shares"] = float(order.size)
        if "network_id" in order.metadata:
            payload["network_id"] = order.metadata["network_id"]
        return payload

    def _live_order_payload(self, order: PaperOrderRequest) -> Dict[str, Any]:
        existing = order.metadata.get("myriad_order_payload") or order.metadata.get("signed_order_payload")
        if isinstance(existing, Mapping):
            return dict(existing)
        signed_order = order.metadata.get("order") or order.metadata.get("signed_order")
        if not isinstance(signed_order, Mapping):
            raise MarketConfigurationError("Myriad live orders require order.metadata['order'] with a signed EIP-712 order.")
        signature = str(order.metadata.get("signature") or "").strip()
        if not signature:
            raise MarketConfigurationError("Myriad live orders require order.metadata['signature'].")
        payload: Dict[str, Any] = {
            "order": dict(signed_order),
            "signature": signature,
            "time_in_force": str(order.metadata.get("time_in_force") or self.config.get("myriad_time_in_force") or "GTC"),
        }
        network_id = order.metadata.get("network_id", self.config.get("myriad_network_id"))
        if network_id not in (None, ""):
            payload["network_id"] = network_id
        return payload

    def _validate_order(self, order: PaperOrderRequest) -> None:
        self.ensure_order_market(order)
        self._split_contract_id(order.contract_id)
        if str(order.side or "").upper() not in {"BUY", "SELL"}:
            raise MarketConfigurationError("Myriad paper order side must be BUY or SELL.")
        if not self._is_positive_number(order.size):
            raise MarketConfigurationError("Myriad paper order size must be positive.")

    @staticmethod
    def _question_id(question: Mapping[str, Any]) -> str:
        return str(question.get("id") or question.get("questionId") or "").strip()

    @staticmethod
    def _market_id(market: Mapping[str, Any]) -> str:
        return str(market.get("id") or market.get("marketId") or market.get("market_id") or "").strip()

    @staticmethod
    def _outcome_id(outcome: Mapping[str, Any]) -> str:
        return str(outcome.get("id") or outcome.get("outcomeId") or outcome.get("outcome_id") or "").strip()

    @staticmethod
    def _contract_id(market_id: str, outcome_id: str) -> str:
        return f"{market_id}:{outcome_id}"

    @staticmethod
    def _split_contract_id(contract_id: str) -> Tuple[str, str]:
        raw = str(contract_id or "").strip()
        parts = [part.strip() for part in raw.split(":")]
        if len(parts) != 2 or not parts[0] or not parts[1]:
            raise MarketConfigurationError("Myriad contract id must be MARKET_ID:OUTCOME_ID.")
        return parts[0], parts[1]

    @staticmethod
    def _markets_from_question(question: Mapping[str, Any]) -> List[Mapping[str, Any]]:
        markets = question.get("markets")
        return [market for market in markets if isinstance(market, Mapping)] if isinstance(markets, list) else []

    @staticmethod
    def _outcomes_from_market(market: Mapping[str, Any]) -> List[Mapping[str, Any]]:
        outcomes = market.get("outcomes")
        return [outcome for outcome in outcomes if isinstance(outcome, Mapping)] if isinstance(outcomes, list) else []

    @staticmethod
    def _find_outcome(market: Mapping[str, Any], outcome_id: str) -> Optional[Mapping[str, Any]]:
        for outcome in MyriadAdapter._outcomes_from_market(market):
            if MyriadAdapter._outcome_id(outcome) == str(outcome_id):
                return outcome
        return None

    @staticmethod
    def _book_levels(raw: Any, *, descending: bool = False) -> List[OrderBookLevel]:
        levels: List[OrderBookLevel] = []
        if not isinstance(raw, list):
            return levels
        for item in raw:
            price = size = None
            if isinstance(item, Mapping):
                price = item.get("price")
                size = item.get("remaining_amount") or item.get("size") or item.get("amount")
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                price, size = item[0], item[1]
            parsed_price = MyriadAdapter._scaled_decimal(price)
            parsed_size = MyriadAdapter._scaled_decimal(size)
            if parsed_price is not None and parsed_size is not None and MyriadAdapter._is_positive_number(parsed_size):
                levels.append(OrderBookLevel(price=parsed_price, size=parsed_size))
        levels.sort(key=lambda level: level.price, reverse=descending)
        return levels

    @staticmethod
    def _scaled_decimal(value: Any) -> Optional[float]:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(number):
            return None
        if number > 10_000_000_000:
            number /= 1_000_000_000_000_000_000
        return number

    @staticmethod
    def _orderbook_outcome_param(outcome_id: str) -> Any:
        clean = str(outcome_id or "").strip()
        return int(clean) if clean.isdigit() else clean

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
    def _activity_rows(payload: Any) -> List[Mapping[str, Any]]:
        """Extract the list from the documented paginated user-events shape."""

        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, Mapping)]
        if isinstance(payload, Mapping):
            for key in ("data", "events", "results", "items"):
                value = payload.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, Mapping)]
                if isinstance(value, Mapping):
                    for nested_key in ("data", "events", "results", "items"):
                        nested = value.get(nested_key)
                        if isinstance(nested, list):
                            return [item for item in nested if isinstance(item, Mapping)]
        return []

    @staticmethod
    def _finite_number(value: Any) -> Optional[float]:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None

    @staticmethod
    def _required_positive_number(value: Any, label: str) -> float:
        number = MyriadAdapter._finite_number(value)
        if number is None:
            raise MarketConfigurationError(f"{label} must be numeric.")
        if number <= 0:
            raise MarketConfigurationError(f"{label} must be positive.")
        return number

    @staticmethod
    def _timestamp_seconds(value: Any) -> int:
        number = MyriadAdapter._finite_number(value)
        if number is None or number <= 0:
            return 0
        timestamp = int(number)
        return timestamp // 1000 if timestamp > 10_000_000_000 else timestamp

    @staticmethod
    def _status_from_question(question: Mapping[str, Any]) -> str:
        markets = MyriadAdapter._markets_from_question(question)
        if any(MyriadAdapter._status_from_market(market) == "open" for market in markets):
            return "open"
        return str(question.get("status") or question.get("state") or "").strip().lower()

    @staticmethod
    def _status_from_market(market: Mapping[str, Any]) -> str:
        return str(market.get("state") or market.get("status") or "").strip().lower()

    @staticmethod
    def _question_url(question: Mapping[str, Any]) -> str:
        markets = MyriadAdapter._markets_from_question(question)
        if markets:
            return MyriadAdapter._market_url(markets[0])
        question_id = MyriadAdapter._question_id(question)
        return f"https://myriad.markets/questions/{question_id}" if question_id else "https://myriad.markets"

    @staticmethod
    def _market_url(market: Mapping[str, Any]) -> str:
        raw = str(market.get("url") or "").strip()
        if raw:
            return raw
        slug = str(market.get("slug") or "").strip()
        return f"https://myriad.markets/markets/{slug}" if slug else "https://myriad.markets"

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
