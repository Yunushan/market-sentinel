from __future__ import annotations

import base64
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


DEFAULT_PROBABLE_MARKET_BASE_URL = "https://market-api.probable.markets/public/api/v1"
DEFAULT_PROBABLE_CLOB_BASE_URL = "https://api.probable.markets/public/api/v1"
PROBABLE_CHAIN_ID = 56
PROBABLE_REFERENCES = (
    "https://developer.probable.markets/",
    "https://www.npmjs.com/package/@prob/clob",
    "https://github.com/0xprobable/clob-examples",
)


class ProbableAdapter(MarketAdapter):
    """Probable adapter using the documented market and CLOB APIs.

    Public discovery and orderbook reads do not require credentials.  Live order
    submission accepts an already signed order payload and uses Probable's
    documented HMAC L2 headers; this keeps private-key signing outside the
    adapter until a dedicated, audited BSC signing workflow is provided.
    """

    metadata = get_market_metadata("probable")

    def health_check(self) -> Dict[str, Any]:
        health = super().health_check()
        credential_sources = []
        for config_key, env_vars, label in (
            ("probable_address", ("PROB_ADDRESS", "PROBABLE_ADDRESS", "PROB_WALLET_ADDRESS"), "PROB_ADDRESS"),
            ("probable_api_key", ("PROB_API_KEY", "PROBABLE_API_KEY"), "PROB_API_KEY"),
            ("probable_api_secret", ("PROB_API_SECRET", "PROBABLE_API_SECRET"), "PROB_API_SECRET"),
            ("probable_api_passphrase", ("PROB_PASSPHRASE", "PROBABLE_API_PASSPHRASE"), "PROB_PASSPHRASE"),
        ):
            credential = self.resolve_credential(config_key, env_vars, label=label)
            if credential:
                credential_sources.append({"name": credential.name, "source": credential.source})
        health.update(
            {
                "market_api_base_url": self.market_api_base_url,
                "clob_api_base_url": self.clob_api_base_url,
                "chain_id": self.chain_id,
                "references": list(PROBABLE_REFERENCES),
                "credential_sources": credential_sources,
                "live_trading_supported": True,
                "live_trading_enabled": self.config_bool("live_trading_enabled", False),
                "signed_order_required": True,
            }
        )
        return health

    @property
    def market_api_base_url(self) -> str:
        configured = self.config.get("probable_market_api_base_url") or self.config.get("api_base_url")
        return str(configured or DEFAULT_PROBABLE_MARKET_BASE_URL).rstrip("/")

    @property
    def clob_api_base_url(self) -> str:
        configured = self.config.get("probable_clob_api_base_url") or self.config.get("clob_api_base_url")
        return str(configured or DEFAULT_PROBABLE_CLOB_BASE_URL).rstrip("/")

    @property
    def chain_id(self) -> int:
        value = self.config.get("probable_chain_id", PROBABLE_CHAIN_ID)
        try:
            chain_id = int(value)
        except (TypeError, ValueError) as exc:
            raise MarketConfigurationError("Probable chain id must be an integer.") from exc
        if chain_id <= 0:
            raise MarketConfigurationError("Probable chain id must be positive.")
        return chain_id

    def list_events(self, query: str = "", limit: int = 50) -> List[MarketEvent]:
        self.ensure_capability("event_listing")
        desired = max(1, min(int(limit or 50), 100))
        params: Dict[str, Any] = {"limit": desired, "closed": False}
        status = str(self.config.get("probable_event_status") or "").strip()
        if status:
            params["status"] = status
        payload = self._public_get("/events", params=params)
        events = self._list_from_payload(payload, "events", "data")
        needle = str(query or "").strip().lower()
        if needle:
            events = [event for event in events if needle in self._search_text(event)]
        return [self._event_from_payload(event) for event in events[:desired]]

    def list_contracts(self, event_id: str) -> List[MarketContract]:
        self.ensure_capability("event_listing")
        clean_event_id = str(event_id or "").strip()
        if not clean_event_id:
            raise MarketConfigurationError("Probable event id cannot be empty.")
        event = self._get_event(clean_event_id)
        markets = self._list_from_payload(event, "markets", "data")
        if not markets:
            payload = self._public_get(
                "/markets",
                params={"event_id": clean_event_id, "limit": 100, "closed": False},
            )
            markets = self._list_from_payload(payload, "markets", "data")
        contracts: List[MarketContract] = []
        for market in markets:
            contracts.extend(self._contracts_from_market(market, event_id=clean_event_id))
        return contracts

    def get_orderbook(self, contract_id: str) -> OrderBookSnapshot:
        self.ensure_capability("orderbook_reading")
        market_id, token_ref = self._split_contract_id(contract_id)
        token_id, canonical_contract_id = self._resolve_token(market_id, token_ref)
        payload = self._clob_get("/book", params={"token_id": token_id})
        book = self._mapping_payload(payload)
        return OrderBookSnapshot(
            market_id=self.market_id,
            contract_id=canonical_contract_id,
            bids=self._levels(book.get("bids"), descending=True),
            asks=self._levels(book.get("asks")),
            raw=dict(book),
        )

    def get_price(self, contract_id: str) -> PriceSnapshot:
        self.ensure_capability("price_reading")
        market_id, token_ref = self._split_contract_id(contract_id)
        token_id, canonical_contract_id = self._resolve_token(market_id, token_ref)
        orderbook = self.get_orderbook(canonical_contract_id)
        bid = orderbook.bids[0].price if orderbook.bids else None
        ask = orderbook.asks[0].price if orderbook.asks else None
        if bid is None:
            bid = self._price_from_payload(self._clob_get("/price", params={"token_id": token_id, "side": "BUY"}))
        if ask is None:
            ask = self._price_from_payload(self._clob_get("/price", params={"token_id": token_id, "side": "SELL"}))
        midpoint = (bid + ask) / 2.0 if bid is not None and ask is not None else None
        return PriceSnapshot(
            market_id=self.market_id,
            contract_id=canonical_contract_id,
            last=midpoint or bid or ask,
            bid=bid,
            ask=ask,
            midpoint=midpoint,
            source="probable_clob",
            raw=dict(orderbook.raw),
        )

    def place_paper_order(self, order: PaperOrderRequest) -> PaperOrderResult:
        self.ensure_capability("paper_trading")
        self._validate_order(order)
        market_id, token_ref = self._split_contract_id(order.contract_id)
        return PaperOrderResult(
            market_id=self.market_id,
            contract_id=f"{market_id}:{token_ref}",
            accepted=True,
            message=(
                f"DRY RUN: would place Probable {str(order.side).upper()} "
                f"for {float(order.size):.4f} shares"
                + (f" at limit {float(order.limit_price):.4f}" if order.limit_price is not None else "")
            ),
            raw={"market_id": market_id, "token_ref": token_ref},
        )

    def place_live_order(self, order: PaperOrderRequest) -> Dict[str, Any]:
        self.ensure_capability("live_trading")
        self._validate_order(order)
        preflight = self.preflight_live_order(order)
        payload = self._live_order_payload(order)
        path = str(self.config.get("probable_order_path") or f"/orders/{self.chain_id}")
        credentials = self._l2_credentials()
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        headers = self._l2_headers("POST", path, body, credentials)
        response = self._request_json("POST", path, body, headers)
        return {
            "market_id": self.market_id,
            "contract_id": order.contract_id,
            "live": True,
            "preflight": preflight,
            "request": payload,
            "response": response,
        }

    def copy_trade_from_activity(self, activity: Mapping[str, Any]) -> PaperOrderResult:
        raise UnsupportedFeatureError(
            self.market_id,
            "copy_trading",
            "Probable copy trading is unsupported because the adapter does not mirror account activity.",
        )

    def _get_event(self, event_id: str) -> Mapping[str, Any]:
        payload = self._public_get(f"/events/{event_id}")
        event = self._mapping_payload(payload)
        if not event:
            raise MarketConfigurationError(f"Probable event {event_id!r} was not found.")
        return event

    def _get_market(self, market_id: str) -> Mapping[str, Any]:
        payload = self._public_get(f"/markets/{market_id}")
        market = self._mapping_payload(payload)
        if not market:
            raise MarketConfigurationError(f"Probable market {market_id!r} was not found.")
        return market

    def _resolve_token(self, market_id: str, token_ref: str) -> Tuple[str, str]:
        clean_ref = str(token_ref or "").strip()
        if not clean_ref:
            raise MarketConfigurationError("Probable contract token or outcome cannot be empty.")
        market = self._get_market(market_id)
        tokens = self._token_rows(market)
        for token in tokens:
            token_id = self._token_id(token)
            outcome = self._outcome_label(token)
            if clean_ref == token_id or clean_ref.upper() == outcome.upper():
                if not token_id:
                    break
                return token_id, f"{market_id}:{token_id}"
        if clean_ref not in {"YES", "NO"}:
            return clean_ref, f"{market_id}:{clean_ref}"
        raise MarketConfigurationError(f"Probable market {market_id!r} has no {clean_ref} token.")

    def _public_get(self, path: str, *, params: Optional[Mapping[str, Any]] = None) -> Any:
        return self.runtime.get_json(self._url(self.market_api_base_url, path), params=params)

    def _clob_get(self, path: str, *, params: Optional[Mapping[str, Any]] = None) -> Any:
        return self.runtime.get_json(self._url(self.clob_api_base_url, path), params=params)

    def _request_json(self, method: str, path: str, body: str, headers: Mapping[str, str]) -> Any:
        self.runtime.rate_limiter.wait()
        request_headers = {"Accept": "application/json", "User-Agent": self.runtime.user_agent, **dict(headers)}
        try:
            response = self.runtime.session.request(
                method.upper(),
                self._url(self.clob_api_base_url, path),
                data=body,
                headers=request_headers,
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

    def _l2_credentials(self) -> Dict[str, str]:
        values: Dict[str, str] = {}
        for key, env_vars, label in (
            ("address", ("PROB_ADDRESS", "PROBABLE_ADDRESS", "PROB_WALLET_ADDRESS"), "PROB_ADDRESS"),
            ("api_key", ("PROB_API_KEY", "PROBABLE_API_KEY"), "PROB_API_KEY"),
            ("secret", ("PROB_API_SECRET", "PROBABLE_API_SECRET"), "PROB_API_SECRET"),
            ("passphrase", ("PROB_PASSPHRASE", "PROBABLE_API_PASSPHRASE"), "PROB_PASSPHRASE"),
        ):
            credential = self.resolve_credential(f"probable_{key}", env_vars, required=True, label=label)
            assert credential is not None
            values[key] = credential.value
        return values

    @staticmethod
    def _l2_headers(method: str, path: str, body: str, credentials: Mapping[str, str]) -> Dict[str, str]:
        timestamp = str(int(time.time()))
        message = f"{timestamp}{method.upper()}{path}{body}"
        secret = str(credentials["secret"])
        padded = secret + "=" * (-len(secret) % 4)
        try:
            key = base64.b64decode(padded)
        except (ValueError, TypeError):
            key = secret.encode("utf-8")
        digest = hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()
        signature = base64.b64encode(digest).decode("ascii").replace("+", "-").replace("/", "_")
        headers = {
            "Content-Type": "application/json",
            "PROB_ADDRESS": str(credentials["address"]),
            "PROB_SIGNATURE": signature,
            "PROB_TIMESTAMP": timestamp,
            "PROB_API_KEY": str(credentials["api_key"]),
            "PROB_PASSPHRASE": str(credentials["passphrase"]),
        }
        return headers

    def _live_order_payload(self, order: PaperOrderRequest) -> Dict[str, Any]:
        existing = order.metadata.get("probable_payload")
        if isinstance(existing, Mapping):
            return dict(existing)
        signed_order = order.metadata.get("signed_order") or order.metadata.get("order")
        if not isinstance(signed_order, Mapping):
            raise MarketConfigurationError(
                "Probable live orders require order.metadata['signed_order'] with an EIP-712 signature."
            )
        owner = str(order.metadata.get("owner") or signed_order.get("signer") or "").strip()
        if not owner:
            raise MarketConfigurationError("Probable live orders require an owner or signed-order signer address.")
        order_type = str(order.metadata.get("order_type") or signed_order.get("timeInForce") or "GTC").upper()
        if order_type not in {"GTC", "GTD", "IOC", "FOK", "FAK"}:
            raise MarketConfigurationError("Probable order type must be GTC, GTD, IOC, FOK, or FAK.")
        payload: Dict[str, Any] = {
            "deferExec": bool(order.metadata.get("defer_exec", True)),
            "order": dict(signed_order),
            "owner": owner,
            "orderType": order_type,
        }
        slippage = order.metadata.get("slippage_tolerance")
        if slippage is not None:
            payload["slippageTolerance"] = {"minPrice": str(slippage)}
        return payload

    def _event_from_payload(self, event: Mapping[str, Any]) -> MarketEvent:
        event_id = self._id(event)
        return MarketEvent(
            market_id=self.market_id,
            event_id=event_id,
            title=str(event.get("title") or event.get("name") or event.get("question") or event_id),
            url=str(event.get("url") or event.get("slug") or ""),
            status=self._status(event),
            raw=dict(event),
        )

    def _contracts_from_market(self, market: Mapping[str, Any], *, event_id: str) -> List[MarketContract]:
        market_id = self._id(market)
        title = str(market.get("question") or market.get("title") or market_id)
        contracts: List[MarketContract] = []
        for token in self._token_rows(market):
            token_id = self._token_id(token)
            if not token_id:
                continue
            outcome = self._outcome_label(token)
            contracts.append(
                MarketContract(
                    market_id=self.market_id,
                    contract_id=f"{market_id}:{token_id}",
                    event_id=event_id,
                    title=f"{title} - {outcome}",
                    outcome=outcome,
                    url=str(market.get("url") or market.get("slug") or ""),
                    status=self._status(market),
                    raw={"market": dict(market), "token": dict(token)},
                )
            )
        return contracts

    def _validate_order(self, order: PaperOrderRequest) -> None:
        self.ensure_order_market(order)
        self._split_contract_id(order.contract_id)
        if str(order.side or "").upper() not in {"BUY", "SELL"}:
            raise MarketConfigurationError("Probable order side must be BUY or SELL.")
        try:
            size = float(order.size)
        except (TypeError, ValueError) as exc:
            raise MarketConfigurationError("Probable order size must be numeric.") from exc
        if not math.isfinite(size) or size <= 0:
            raise MarketConfigurationError("Probable order size must be positive and finite.")
        if order.limit_price is not None and self._safe_probability(order.limit_price) is None:
            raise MarketConfigurationError("Probable order limit price must be between 0 and 1.")

    @staticmethod
    def _url(base: str, path: str) -> str:
        return f"{base.rstrip('/')}/{str(path or '').strip('/')}"

    @staticmethod
    def _mapping_payload(payload: Any) -> Dict[str, Any]:
        if isinstance(payload, Mapping):
            data = payload.get("data")
            if isinstance(data, Mapping):
                return dict(data)
            return dict(payload)
        return {}

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
            if isinstance(data, Mapping):
                return ProbableAdapter._list_from_payload(data, *keys)
        return []

    @staticmethod
    def _id(payload: Mapping[str, Any]) -> str:
        return str(payload.get("id") or payload.get("marketId") or payload.get("eventId") or "").strip()

    @staticmethod
    def _status(payload: Mapping[str, Any]) -> str:
        value = payload.get("status") or payload.get("state") or payload.get("tradingStatus") or ""
        if isinstance(value, Mapping):
            value = value.get("name") or value.get("status") or value.get("value") or ""
        return str(value).strip().lower()

    @staticmethod
    def _search_text(payload: Mapping[str, Any]) -> str:
        values = [payload.get("id"), payload.get("title"), payload.get("name"), payload.get("question"), payload.get("description"), payload.get("slug")]
        return " ".join(str(value or "") for value in values).lower()

    @staticmethod
    def _token_rows(market: Mapping[str, Any]) -> List[Mapping[str, Any]]:
        tokens = market.get("tokens")
        if isinstance(tokens, list):
            return [dict(token) if isinstance(token, Mapping) else {"token_id": str(token)} for token in tokens]
        token_ids = market.get("clobTokenIds") or market.get("clob_token_ids")
        if isinstance(token_ids, str):
            try:
                token_ids = json.loads(token_ids)
            except json.JSONDecodeError:
                token_ids = [item.strip() for item in token_ids.split(",") if item.strip()]
        outcomes = market.get("outcomes")
        if isinstance(outcomes, str):
            try:
                outcomes = json.loads(outcomes)
            except json.JSONDecodeError:
                outcomes = [item.strip() for item in outcomes.split(",") if item.strip()]
        if not isinstance(token_ids, list):
            return []
        rows: List[Mapping[str, Any]] = []
        for index, token_id in enumerate(token_ids):
            outcome = outcomes[index] if isinstance(outcomes, list) and index < len(outcomes) else ("Yes" if index == 0 else "No")
            rows.append({"token_id": str(token_id), "outcome": str(outcome)})
        return rows

    @staticmethod
    def _token_id(token: Mapping[str, Any]) -> str:
        return str(token.get("token_id") or token.get("tokenId") or token.get("id") or "").strip()

    @staticmethod
    def _outcome_label(token: Mapping[str, Any]) -> str:
        return str(token.get("outcome") or token.get("label") or token.get("name") or "Outcome").strip()

    @staticmethod
    def _split_contract_id(contract_id: str) -> Tuple[str, str]:
        raw = str(contract_id or "").strip()
        if ":" not in raw:
            raise MarketConfigurationError("Probable contract id must be MARKET_ID:TOKEN_ID or MARKET_ID:YES|NO.")
        market_id, token_ref = raw.rsplit(":", 1)
        if not market_id.strip() or not token_ref.strip():
            raise MarketConfigurationError("Probable contract id must be MARKET_ID:TOKEN_ID or MARKET_ID:YES|NO.")
        return market_id.strip(), token_ref.strip()

    @staticmethod
    def _levels(raw: Any, *, descending: bool = False) -> List[OrderBookLevel]:
        rows: List[Any]
        if isinstance(raw, Mapping):
            rows = [[price, size] for price, size in raw.items()]
        elif isinstance(raw, list):
            rows = raw
        else:
            rows = []
        levels: List[OrderBookLevel] = []
        for item in rows:
            if isinstance(item, Mapping):
                price = item.get("price")
                size = item.get("size") or item.get("quantity") or item.get("qty") or item.get("amount")
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                price, size = item[0], item[1]
            else:
                continue
            try:
                parsed_price = float(price)
                parsed_size = float(size)
            except (TypeError, ValueError):
                continue
            if math.isfinite(parsed_price) and math.isfinite(parsed_size) and 0 <= parsed_price <= 1 and parsed_size > 0:
                levels.append(OrderBookLevel(price=parsed_price, size=parsed_size))
        levels.sort(key=lambda level: level.price, reverse=descending)
        return levels

    @staticmethod
    def _price_from_payload(payload: Any) -> Optional[float]:
        value = payload.get("price") if isinstance(payload, Mapping) else payload
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) and 0 <= number <= 1 else None

    @staticmethod
    def _safe_probability(value: Any) -> Optional[float]:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) and 0 <= number <= 1 else None
