from __future__ import annotations

import math
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Mapping, Optional, Tuple

from .base import MarketAdapter
from .catalog import get_market_metadata
from .errors import MarketConfigurationError, MarketHTTPError
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
DEFAULT_OPINION_CLOB_HOST = "https://proxy.opinion.trade:8443"
DEFAULT_OPINION_CHAIN_ID = 56
OPINION_REFERENCES = (
    "https://docs.opinion.trade/developer-guide/opinion-open-api/overview",
    "https://docs.opinion.trade/developer-guide/opinion-open-api/market",
    "https://docs.opinion.trade/developer-guide/opinion-open-api/token",
    "https://docs.opinion.trade/developer-guide/opinion-open-api/trade",
    "https://docs.opinion.trade/developer-guide/opinion-open-api/position",
    "https://docs.opinion.trade/developer-guide/opinion-clob-python-sdk/overview",
    "https://pypi.org/project/opinion-clob-sdk/",
)


class OpinionAdapter(MarketAdapter):
    """Opinion Labs adapter using the documented OpenAPI and optional CLOB SDK."""

    metadata = get_market_metadata("opinion_labs")

    def health_check(self) -> Dict[str, Any]:
        health = super().health_check()
        api_key = self.resolve_credential("opinion_api_key", ("OPINION_API_KEY",), label="OPINION_API_KEY")
        health.update(
            {
                "api_base_url": self.api_base_url,
                "references": list(OPINION_REFERENCES),
                "credential_sources": [{"name": api_key.name, "source": api_key.source}] if api_key else [],
                "clob_host": self.clob_host,
                "chain_id": self.chain_id,
                "clob_sdk_available": self._clob_sdk_available(),
                "live_trading_supported": True,
                "live_trading_enabled": self.config_bool("live_trading_enabled", False),
                "copy_trading_supported": bool(self.capabilities.copy_trading),
                "activity_feed_supported": True,
            }
        )
        return health

    @property
    def api_base_url(self) -> str:
        configured = self.config.get("opinion_api_base_url") or self.config.get("api_base_url")
        return str(configured or DEFAULT_OPINION_BASE_URL).rstrip("/")

    @property
    def clob_host(self) -> str:
        configured = self.config.get("opinion_clob_host") or self.config.get("clob_host")
        return str(configured or DEFAULT_OPINION_CLOB_HOST).rstrip("/")

    @property
    def chain_id(self) -> int:
        value = self.config.get("opinion_chain_id", DEFAULT_OPINION_CHAIN_ID)
        try:
            chain_id = int(value)
        except (TypeError, ValueError) as exc:
            raise MarketConfigurationError("Opinion chain id must be an integer.") from exc
        if chain_id != DEFAULT_OPINION_CHAIN_ID:
            raise MarketConfigurationError("Opinion CLOB currently supports only BNB Chain (chain id 56).")
        return chain_id

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
        self.ensure_capability("live_trading")
        self._validate_order(order)
        audit = self.preflight_live_order(order, feature_name="Opinion CLOB live trading")
        market_id, outcome, token_id = self._split_contract_id(order.contract_id)
        if not market_id.isdigit() or int(market_id) <= 0:
            raise MarketConfigurationError("Opinion live orders require a positive numeric market id.")

        metadata = order.metadata if isinstance(order.metadata, Mapping) else {}
        order_kind = str(metadata.get("order_type") or metadata.get("orderType") or "limit").strip().lower()
        if order_kind not in {"limit", "limit_order", "market", "market_order"}:
            raise MarketConfigurationError("Opinion order_type must be limit or market.")
        is_market = order_kind in {"market", "market_order"}
        if is_market:
            sdk_price = "0"
        else:
            if order.limit_price is None:
                raise MarketConfigurationError("Opinion limit orders require a limit price.")
            price = self._safe_probability(order.limit_price)
            if price is None or price < 0.01 or price > 0.99:
                raise MarketConfigurationError("Opinion limit price must be between 0.01 and 0.99.")
            sdk_price = self._decimal_string(price)

        amount_quote = self._metadata_amount(
            metadata,
            "maker_amount_in_quote_token",
            "makerAmountInQuoteToken",
        )
        amount_base = self._metadata_amount(
            metadata,
            "maker_amount_in_base_token",
            "makerAmountInBaseToken",
        )
        if amount_quote is not None and amount_base is not None:
            raise MarketConfigurationError(
                "Opinion live orders must provide exactly one of maker_amount_in_quote_token or maker_amount_in_base_token."
            )
        if amount_quote is None and amount_base is None:
            # Preserve the common application order model: BUY size is quote
            # currency to spend; SELL size is outcome tokens to sell.
            if str(order.side or "").upper() == "BUY":
                amount_quote = self._decimal_string(order.size)
            else:
                amount_base = self._decimal_string(order.size)

        sdk_order = self._build_sdk_order(
            market_id=int(market_id),
            token_id=token_id,
            side=str(order.side or "").upper(),
            is_market=is_market,
            price=sdk_price,
            amount_quote=amount_quote,
            amount_base=amount_base,
        )
        client = self._create_clob_client()
        try:
            response = client.place_order(
                sdk_order,
                check_approval=self.config_bool("opinion_live_check_approval", False),
            )
        except Exception as exc:
            raise MarketHTTPError(f"Opinion CLOB order submission failed: {exc}") from exc
        return {
            "market_id": self.market_id,
            "opinion_market_id": int(market_id),
            "contract_id": self._contract_id(market_id, outcome, token_id),
            "side": str(order.side or "").upper(),
            "order_type": "market" if is_market else "limit",
            "live": True,
            "preflight": audit,
            "approval_check_requested": self.config_bool("opinion_live_check_approval", False),
            "request": {
                "marketId": int(market_id),
                "tokenId": token_id,
                "side": str(order.side or "").upper(),
                "price": sdk_price,
                "makerAmountInQuoteToken": amount_quote,
                "makerAmountInBaseToken": amount_base,
            },
            "response": self._json_safe(response),
        }

    def _create_clob_client(self):
        try:
            from opinion_clob_sdk import Client
        except ImportError as exc:
            raise MarketConfigurationError(
                "Opinion live trading requires the official opinion-clob-sdk package; "
                "install requirements-live.lock before enabling it."
            ) from exc

        api_key = self.resolve_credential("opinion_api_key", ("OPINION_API_KEY",), required=True, label="OPINION_API_KEY")
        private_key = self.resolve_credential(
            "opinion_private_key",
            ("OPINION_PRIVATE_KEY",),
            required=True,
            label="OPINION_PRIVATE_KEY",
        )
        multi_sig = self.resolve_credential(
            "opinion_multi_sig_address",
            ("OPINION_MULTI_SIG_ADDRESS",),
            required=True,
            label="OPINION_MULTI_SIG_ADDRESS",
        )
        rpc_url = self.resolve_credential(
            "opinion_rpc_url",
            ("OPINION_RPC_URL",),
            required=True,
            label="OPINION_RPC_URL",
        )
        return Client(
            host=self.clob_host,
            apikey=api_key.value,
            chain_id=self.chain_id,
            rpc_url=rpc_url.value,
            private_key=private_key.value,
            multi_sig_addr=multi_sig.value,
        )

    @staticmethod
    def _clob_sdk_available() -> bool:
        try:
            from opinion_clob_sdk import Client as _Client  # noqa: F401
        except ImportError:
            return False
        return True

    @staticmethod
    def _build_sdk_order(
        *,
        market_id: int,
        token_id: str,
        side: str,
        is_market: bool,
        price: str,
        amount_quote: Optional[str],
        amount_base: Optional[str],
    ) -> Any:
        try:
            from opinion_clob_sdk.chain.py_order_utils.model.order import PlaceOrderDataInput
            from opinion_clob_sdk.chain.py_order_utils.model.order_type import LIMIT_ORDER, MARKET_ORDER
            from opinion_clob_sdk.chain.py_order_utils.model.sides import BUY, SELL
        except ImportError as exc:
            raise MarketConfigurationError(
                "Opinion live trading requires the official opinion-clob-sdk order models."
            ) from exc
        return PlaceOrderDataInput(
            marketId=market_id,
            tokenId=token_id,
            side=BUY if side == "BUY" else SELL,
            orderType=MARKET_ORDER if is_market else LIMIT_ORDER,
            price=price,
            makerAmountInQuoteToken=amount_quote,
            makerAmountInBaseToken=amount_base,
        )

    @staticmethod
    def _metadata_amount(metadata: Mapping[str, Any], *keys: str) -> Optional[str]:
        value = next((metadata.get(key) for key in keys if metadata.get(key) not in (None, "")), None)
        if value is None:
            return None
        return OpinionAdapter._decimal_string(value, positive=True)

    @staticmethod
    def _decimal_string(value: Any, *, positive: bool = False) -> str:
        try:
            number = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise MarketConfigurationError("Opinion order amounts and prices must be finite decimals.") from exc
        if not number.is_finite() or (positive and number <= 0):
            raise MarketConfigurationError("Opinion order amounts must be finite and positive.")
        text = format(number, "f")
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        return text or "0"

    @staticmethod
    def _json_safe(value: Any) -> Any:
        if isinstance(value, Mapping):
            return {str(key): OpinionAdapter._json_safe(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [OpinionAdapter._json_safe(item) for item in value]
        for method_name in ("to_dict", "dict"):
            method = getattr(value, method_name, None)
            if callable(method):
                try:
                    return OpinionAdapter._json_safe(method())
                except Exception:
                    pass
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        return str(value)

    def list_activity(self, wallet_address: str, *, limit: int = 25) -> List[Dict[str, Any]]:
        """Return normalized filled trades for an official Opinion wallet feed.

        Opinion's OpenAPI exposes filled user trades by wallet address.  The
        adapter keeps this read-only and normalizes the response to the common
        wallet-activity shape consumed by the local copy-preview workflow.
        """

        self.ensure_capability("copy_trading")
        wallet = self._normalize_wallet(wallet_address)
        desired = max(1, min(int(limit or 25), 20))
        params: Dict[str, Any] = {"page": 1, "limit": desired}
        market_id = str(self.config.get("opinion_activity_market_id") or "").strip()
        chain_id = str(self.config.get("opinion_activity_chain_id") or "").strip()
        if market_id:
            params["marketId"] = market_id
        if chain_id:
            params["chainId"] = chain_id
        payload = self._get(f"/trade/user/{wallet}", params=params)
        return [self._activity_from_trade(wallet, item) for item in self._result_list(payload)]

    def copy_trade_from_activity(self, activity: Mapping[str, Any]) -> PaperOrderResult:
        self.ensure_capability("copy_trading")
        contract_id = str(activity.get("asset") or activity.get("contract_id") or "").strip()
        if not contract_id:
            raise MarketConfigurationError("Opinion activity has no contract token id.")
        side = str(activity.get("side") or "").strip().upper()
        if side not in {"BUY", "SELL"}:
            raise MarketConfigurationError("Opinion activity side must be BUY or SELL.")
        size = self._required_positive_number(activity.get("size"), "Opinion activity size")
        price = activity.get("price")
        limit_price = None if price in (None, "") else self._safe_probability(price)
        if price not in (None, "") and limit_price is None:
            raise MarketConfigurationError("Opinion activity price must be between 0 and 1.")
        return self.place_paper_order(
            PaperOrderRequest(
                market_id=self.market_id,
                contract_id=contract_id,
                side=side,
                size=size,
                limit_price=limit_price,
                metadata={"activity": dict(activity), "source": "opinion_trade_feed"},
            )
        )

    def _activity_from_trade(self, wallet: str, trade: Mapping[str, Any]) -> Dict[str, Any]:
        market_id = self._market_id(trade)
        outcome = self._outcome_from_trade(trade)
        token_id = str(
            trade.get("tokenId")
            or trade.get("token_id")
            or trade.get("outcomeTokenId")
            or trade.get("outcome_token_id")
            or ""
        ).strip()
        if not token_id:
            raise MarketConfigurationError(
                "Opinion trade response omitted tokenId; cannot safely map the trade to a contract."
            )
        contract_id = self._contract_id(market_id, outcome, token_id)
        return {
            "type": "TRADE",
            "proxyWallet": wallet,
            "wallet": wallet,
            "asset": contract_id,
            "contract_id": contract_id,
            "marketId": market_id,
            "side": self._side_from_trade(trade),
            "size": trade.get("shares") or trade.get("orderShares") or trade.get("size") or 0,
            "price": trade.get("price"),
            "timestamp": self._timestamp_seconds(trade.get("createdAt") or trade.get("timestamp")),
            "transactionHash": str(trade.get("txHash") or trade.get("transactionHash") or ""),
            "slug": str(trade.get("marketSlug") or market_id),
            "outcome": str(trade.get("outcome") or outcome),
            "pseudonym": str(trade.get("marketTitle") or ""),
            "raw": dict(trade),
        }

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
    def _normalize_wallet(wallet_address: str) -> str:
        wallet = str(wallet_address or "").strip().lower()
        if len(wallet) != 42 or not wallet.startswith("0x"):
            raise MarketConfigurationError("Opinion wallet address must be a 20-byte 0x address.")
        try:
            int(wallet[2:], 16)
        except ValueError as exc:
            raise MarketConfigurationError("Opinion wallet address must be hexadecimal.") from exc
        return wallet

    @staticmethod
    def _outcome_from_trade(trade: Mapping[str, Any]) -> str:
        raw = str(trade.get("outcomeSideEnum") or trade.get("outcome") or trade.get("outcomeSide") or "").strip()
        if raw.lower() in {"1", "yes", "true"} or raw.lower().startswith("yes"):
            return "YES"
        if raw.lower() in {"0", "no", "false"} or raw.lower().startswith("no"):
            return "NO"
        raise MarketConfigurationError("Opinion trade response has an unknown outcome side.")

    @staticmethod
    def _side_from_trade(trade: Mapping[str, Any]) -> str:
        raw = str(trade.get("side") or trade.get("sideEnum") or "").strip().upper()
        if raw in {"BUY", "SELL"}:
            return raw
        if raw in {"0", "1"}:
            return "BUY" if raw == "0" else "SELL"
        raise MarketConfigurationError("Opinion trade response has an unknown trade side.")

    @staticmethod
    def _timestamp_seconds(value: Any) -> int:
        try:
            timestamp = int(float(value or 0))
        except (TypeError, ValueError):
            return 0
        return timestamp // 1000 if timestamp > 10_000_000_000 else timestamp

    @staticmethod
    def _required_positive_number(value: Any, label: str) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise MarketConfigurationError(f"{label} must be numeric.") from exc
        if not math.isfinite(number) or number <= 0:
            raise MarketConfigurationError(f"{label} must be positive.")
        return number

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

