from __future__ import annotations

import math
import secrets
import time
from decimal import Decimal, ROUND_HALF_UP
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


DEFAULT_SX_BET_BASE_URL = "https://api.sx.bet"
DEFAULT_SX_BET_WS_URL = "wss://realtime.sx.bet/connection/websocket"
DEFAULT_SX_BET_CHAIN_ID = 4162
DEFAULT_SX_BET_EXPIRY = 2209006800
DEFAULT_SX_BET_BASE_TOKEN = "0x6629Ce1Cf35Cc1329ebB4F63202F3f197b3F050B"
DEFAULT_SX_BET_EXECUTOR = "0x52adf738AAD93c31f798a30b2C74D658e1E9a562"
ODDS_SCALE = 10**20
SX_BET_REFERENCES = (
    "https://docs.sx.bet/api-reference/introduction",
    "https://docs.sx.bet/api-reference/get-markets-active",
    "https://docs.sx.bet/api-reference/get-markets-find",
    "https://docs.sx.bet/api-reference/get-orders",
    "https://docs.sx.bet/api-reference/get-best-odds",
    "https://docs.sx.bet/api-reference/post-new-order",
    "https://docs.sx.bet/api-reference/api-key",
    "https://docs.sx.bet/api-reference/centrifugo-order-book-updates",
    "https://docs.sx.bet/api-reference/eip712-signing",
)


class SxBetAdapter(MarketAdapter):
    """SX Bet adapter using documented public REST and Centrifugo WebSocket APIs."""

    metadata = get_market_metadata("sx_bet")

    def health_check(self) -> Dict[str, Any]:
        health = super().health_check()
        api_key = self.resolve_credential("sx_bet_api_key", ("SX_BET_API_KEY",), label="SX_BET_API_KEY")
        maker = self.resolve_credential("sx_bet_maker_address", ("SX_BET_MAKER_ADDRESS",), label="SX_BET_MAKER_ADDRESS")
        private_key = self.resolve_credential("sx_bet_private_key", ("SX_BET_PRIVATE_KEY",), label="SX_BET_PRIVATE_KEY")
        credential_sources = []
        for credential in (api_key, maker, private_key):
            if credential:
                credential_sources.append({"name": credential.name, "source": credential.source})
        health.update(
            {
                "api_base_url": self.api_base_url,
                "websocket_url": self.websocket_url,
                "references": list(SX_BET_REFERENCES),
                "live_trading_enabled": self.config_bool("live_trading_enabled", False),
                "credential_sources": credential_sources,
                "copy_trading_supported": False,
            }
        )
        return health

    @property
    def api_base_url(self) -> str:
        configured = self.config.get("sx_bet_api_base_url") or self.config.get("api_base_url")
        return str(configured or DEFAULT_SX_BET_BASE_URL).rstrip("/")

    @property
    def websocket_url(self) -> str:
        configured = self.config.get("sx_bet_ws_url") or self.config.get("websocket_url")
        return str(configured or DEFAULT_SX_BET_WS_URL).rstrip("/")

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
        market_hash, outcome = self._split_contract_id(contract_id)
        orders = self._fetch_orders(market_hash)
        bids, asks = self._book_for_outcome(orders, outcome)
        return OrderBookSnapshot(
            market_id=self.market_id,
            contract_id=self._contract_id(market_hash, outcome),
            bids=bids,
            asks=asks,
            raw={"orders": [dict(order) for order in orders]},
        )

    def get_price(self, contract_id: str) -> PriceSnapshot:
        self.ensure_capability("price_reading")
        market_hash, outcome = self._split_contract_id(contract_id)
        orderbook = self.get_orderbook(self._contract_id(market_hash, outcome))
        bid = orderbook.bids[0].price if orderbook.bids else None
        ask = orderbook.asks[0].price if orderbook.asks else None
        midpoint = (bid + ask) / 2.0 if bid is not None and ask is not None else None
        last = midpoint
        if last is None:
            last = self._best_odds_price(market_hash, outcome)
        return PriceSnapshot(
            market_id=self.market_id,
            contract_id=self._contract_id(market_hash, outcome),
            last=last,
            bid=bid,
            ask=ask,
            midpoint=midpoint,
            source="sx_bet_orderbook",
            raw=orderbook.raw,
        )

    def place_paper_order(self, order: PaperOrderRequest) -> PaperOrderResult:
        self.ensure_capability("paper_trading")
        self._validate_order(order)
        market_hash, outcome = self._split_contract_id(order.contract_id)
        payload = self._build_unsigned_order(order, dry_run=True)
        return PaperOrderResult(
            market_id=self.market_id,
            contract_id=self._contract_id(market_hash, outcome),
            accepted=True,
            message=(
                f"DRY RUN: would place SX Bet {order.side.upper()} "
                f"for {order.size:.4f} {outcome} shares"
                + (f" at limit {order.limit_price:.4f}" if order.limit_price is not None else "")
            ),
            filled_size=0.0,
            average_price=None,
            raw={"request": payload},
        )

    def place_live_order(self, order: PaperOrderRequest) -> Dict[str, Any]:
        self.ensure_capability("live_trading")
        self._validate_order(order)
        preflight = self.preflight_live_order(order)
        signed_order = self._signed_order(order)
        response = self.runtime.request_json(
            "POST",
            self._url("/orders/new"),
            json_body={"orders": [signed_order]},
            headers={"Content-Type": "application/json"},
        )
        return {
            "market_id": self.market_id,
            "contract_id": self._contract_id(*self._split_contract_id(order.contract_id)),
            "live": True,
            "preflight": preflight,
            "request": {"orders": [signed_order]},
            "response": response,
        }

    def copy_trade_from_activity(self, activity: Mapping[str, Any]) -> PaperOrderResult:
        raise UnsupportedFeatureError(
            self.market_id,
            "copy_trading",
            "SX Bet copy trading is unsupported because this adapter has no official account activity mirroring model.",
        )

    def websocket_connection_info(
        self,
        *,
        market_hashes: Optional[List[str]] = None,
        event_ids: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        channels = self.websocket_channels(market_hashes=market_hashes, event_ids=event_ids)
        credential = self.resolve_credential("sx_bet_api_key", ("SX_BET_API_KEY",), label="SX_BET_API_KEY")
        return {
            "url": self.websocket_url,
            "token_endpoint": f"{self.api_base_url}/user/realtime-token/api-key",
            "requires_api_key_header": "X-Api-Key",
            "credential_source": credential.source if credential else None,
            "channels": channels,
            "subscription_options": {"positioned": True, "recoverable": True},
        }

    @staticmethod
    def websocket_channels(
        *,
        market_hashes: Optional[List[str]] = None,
        event_ids: Optional[List[str]] = None,
    ) -> List[str]:
        markets = [str(value).strip() for value in (market_hashes or []) if str(value).strip()]
        events = [str(value).strip() for value in (event_ids or []) if str(value).strip()]
        if not markets and not events:
            raise MarketConfigurationError("SX Bet WebSocket subscription requires market hashes or event ids.")
        channels = [f"order_book:market_{market}" for market in markets]
        channels.extend(f"order_book:event_{event_id}" for event_id in events)
        return channels

    def _fetch_active_markets(self, *, limit: int) -> List[Mapping[str, Any]]:
        params: Dict[str, Any] = {
            "pageSize": max(1, min(int(limit or 50), 100)),
        }
        for config_key, api_key in (
            ("sx_bet_only_main_line", "onlyMainLine"),
            ("sx_bet_live_only", "liveOnly"),
            ("sx_bet_league_id", "leagueId"),
            ("sx_bet_sport_ids", "sportIds"),
            ("sx_bet_bet_group", "betGroup"),
        ):
            value = self.config.get(config_key)
            if value not in (None, ""):
                params[api_key] = value
        data = self.runtime.get_json(self._url("/markets/active"), params=params)
        markets = data.get("data", {}).get("markets") if isinstance(data, Mapping) else []
        return [market for market in markets if isinstance(market, Mapping)] if isinstance(markets, list) else []

    def _get_market(self, market_hash: str) -> Mapping[str, Any]:
        clean_hash = str(market_hash or "").strip()
        if not clean_hash:
            raise MarketConfigurationError("SX Bet market hash cannot be empty.")
        data = self.runtime.get_json(self._url("/markets/find"), params={"marketHashes": clean_hash})
        markets = data.get("data") if isinstance(data, Mapping) else []
        if isinstance(markets, list):
            for market in markets:
                if isinstance(market, Mapping) and self._market_hash(market).lower() == clean_hash.lower():
                    return market
        raise MarketConfigurationError(f"SX Bet market {clean_hash!r} was not found.")

    def _fetch_orders(self, market_hash: str) -> List[Mapping[str, Any]]:
        params = {
            "marketHashes": market_hash,
            "baseToken": self.base_token,
            "perPage": max(1, min(int(self.config.get("sx_bet_orderbook_depth") or 100), 1000)),
            "sortBy": "percentage_odds",
            "sortAsc": False,
        }
        data = self.runtime.get_json(self._url("/orders"), params=params)
        orders = data.get("data") if isinstance(data, Mapping) else []
        return [order for order in orders if isinstance(order, Mapping)] if isinstance(orders, list) else []

    def _best_odds_price(self, market_hash: str, outcome: str) -> Optional[float]:
        data = self.runtime.get_json(
            self._url("/orders/odds/best"),
            params={"marketHashes": market_hash, "baseToken": self.base_token},
        )
        odds = data.get("data", {}).get("bestOdds") if isinstance(data, Mapping) else []
        if not isinstance(odds, list):
            return None
        for item in odds:
            if not isinstance(item, Mapping) or str(item.get("marketHash") or "").lower() != market_hash.lower():
                continue
            key = "outcomeOne" if outcome == "ONE" else "outcomeTwo"
            outcome_data = item.get(key)
            if isinstance(outcome_data, Mapping):
                return self._scaled_probability(outcome_data.get("percentageOdds"))
        return None

    def _url(self, path: str) -> str:
        clean_path = "/" + str(path or "").strip("/")
        return f"{self.api_base_url}{clean_path}"

    @property
    def base_token(self) -> str:
        return str(self.config.get("sx_bet_base_token") or DEFAULT_SX_BET_BASE_TOKEN)

    @property
    def executor_address(self) -> str:
        return str(self.config.get("sx_bet_executor_address") or DEFAULT_SX_BET_EXECUTOR)

    @property
    def base_token_decimals(self) -> int:
        return int(self.config.get("sx_bet_base_token_decimals") or 6)

    def _event_from_market(self, market: Mapping[str, Any]) -> MarketEvent:
        market_hash = self._market_hash(market)
        return MarketEvent(
            market_id=self.market_id,
            event_id=market_hash,
            title=self._market_title(market),
            url=self._market_url(market),
            status=self._status_from_market(market),
            raw=dict(market),
        )

    def _contracts_from_market(self, market: Mapping[str, Any]) -> List[MarketContract]:
        market_hash = self._market_hash(market)
        title = self._market_title(market)
        status = self._status_from_market(market)
        return [
            MarketContract(
                market_id=self.market_id,
                contract_id=self._contract_id(market_hash, "ONE"),
                event_id=market_hash,
                title=f"{title} - {str(market.get('outcomeOneName') or 'Outcome One')}",
                outcome=str(market.get("outcomeOneName") or "Outcome One"),
                url=self._market_url(market),
                status=status,
                raw={"market": dict(market), "outcome": "ONE"},
            ),
            MarketContract(
                market_id=self.market_id,
                contract_id=self._contract_id(market_hash, "TWO"),
                event_id=market_hash,
                title=f"{title} - {str(market.get('outcomeTwoName') or 'Outcome Two')}",
                outcome=str(market.get("outcomeTwoName") or "Outcome Two"),
                url=self._market_url(market),
                status=status,
                raw={"market": dict(market), "outcome": "TWO"},
            ),
        ]

    def _book_for_outcome(self, orders: List[Mapping[str, Any]], outcome: str) -> Tuple[List[OrderBookLevel], List[OrderBookLevel]]:
        desired_is_one = outcome == "ONE"
        bids: List[OrderBookLevel] = []
        asks: List[OrderBookLevel] = []
        for raw_order in orders:
            maker_is_one = bool(raw_order.get("isMakerBettingOutcomeOne"))
            maker_odds = self._scaled_probability(raw_order.get("percentageOdds"))
            remaining = self._remaining_order_size(raw_order)
            if maker_odds is None or remaining <= 0:
                continue
            if maker_is_one == desired_is_one:
                bids.append(OrderBookLevel(price=maker_odds, size=self._from_base_units(remaining)))
            else:
                taker_price = round(1.0 - maker_odds, 10)
                taker_size = self._taker_space(remaining, maker_odds)
                asks.append(OrderBookLevel(price=taker_price, size=self._from_base_units(taker_size)))
        bids.sort(key=lambda level: level.price, reverse=True)
        asks.sort(key=lambda level: level.price)
        return bids, asks

    def _build_unsigned_order(self, order: PaperOrderRequest, *, dry_run: bool) -> Dict[str, Any]:
        market_hash, outcome = self._split_contract_id(order.contract_id)
        side = str(order.side or "").upper()
        if order.limit_price is None:
            raise MarketConfigurationError("SX Bet orders require a limit price.")
        selected_price = self._limit_probability(order.limit_price)
        maker_is_one = outcome == "ONE" if side == "BUY" else outcome != "ONE"
        maker_price = selected_price if side == "BUY" else 1.0 - selected_price
        if maker_price <= 0.0 or maker_price >= 1.0:
            raise MarketConfigurationError("SX Bet maker odds must be between 0 and 1.")
        maker_address = str(order.metadata.get("maker") or self._maker_address(required=not dry_run))
        return {
            "marketHash": market_hash,
            "maker": maker_address,
            "totalBetSize": str(self._to_base_units(order.size)),
            "percentageOdds": str(self._to_odds_units(maker_price)),
            "baseToken": str(order.metadata.get("base_token") or self.base_token),
            "apiExpiry": int(order.metadata.get("api_expiry") or (time.time() + 3600)),
            "expiry": int(order.metadata.get("expiry") or DEFAULT_SX_BET_EXPIRY),
            "executor": str(order.metadata.get("executor") or self.executor_address),
            "isMakerBettingOutcomeOne": maker_is_one,
            "salt": str(order.metadata.get("salt") or secrets.randbits(256)),
        }

    def _signed_order(self, order: PaperOrderRequest) -> Dict[str, Any]:
        unsigned = self._build_unsigned_order(order, dry_run=False)
        explicit_signature = order.metadata.get("signature")
        if explicit_signature:
            return {**unsigned, "signature": str(explicit_signature)}
        private_key = self.resolve_credential(
            "sx_bet_private_key",
            ("SX_BET_PRIVATE_KEY",),
            required=True,
            label="SX_BET_PRIVATE_KEY",
        )
        return {**unsigned, "signature": self._sign_order(unsigned, private_key.value)}

    def _sign_order(self, order: Mapping[str, Any], private_key: str) -> str:
        try:
            from eth_abi.packed import encode_packed
            from eth_account import Account
            from eth_account.messages import encode_defunct
            from eth_utils import keccak, to_checksum_address
        except Exception as exc:
            raise MarketConfigurationError(
                "SX Bet live trading requires eth-account and eth-abi. Install project dependencies first."
            ) from exc
        encoded = encode_packed(
            ["bytes32", "address", "uint256", "uint256", "uint256", "uint256", "address", "address", "bool"],
            [
                bytes.fromhex(str(order["marketHash"]).removeprefix("0x")),
                to_checksum_address(str(order["baseToken"])),
                int(order["totalBetSize"]),
                int(order["percentageOdds"]),
                int(order["expiry"]),
                int(order["salt"]),
                to_checksum_address(str(order["maker"])),
                to_checksum_address(str(order["executor"])),
                bool(order["isMakerBettingOutcomeOne"]),
            ],
        )
        order_hash = keccak(encoded)
        signature = Account.sign_message(encode_defunct(primitive=order_hash), private_key=private_key).signature.hex()
        return signature if signature.startswith("0x") else f"0x{signature}"

    def _validate_order(self, order: PaperOrderRequest) -> None:
        self.ensure_order_market(order)
        self._split_contract_id(order.contract_id)
        side = str(order.side or "").upper()
        if side not in {"BUY", "SELL"}:
            raise MarketConfigurationError("SX Bet order side must be BUY or SELL.")
        if not self._is_positive_number(order.size):
            raise MarketConfigurationError("SX Bet order size must be positive.")
        if order.limit_price is not None:
            self._limit_probability(order.limit_price)

    def _maker_address(self, *, required: bool) -> str:
        credential = self.resolve_credential(
            "sx_bet_maker_address",
            ("SX_BET_MAKER_ADDRESS",),
            required=required,
            label="SX_BET_MAKER_ADDRESS",
        )
        if credential:
            return credential.value
        return "0x0000000000000000000000000000000000000000"

    @staticmethod
    def _market_matches_query(market: Mapping[str, Any], query: str) -> bool:
        haystack = " ".join(
            str(market.get(key) or "")
            for key in (
                "marketHash",
                "outcomeOneName",
                "outcomeTwoName",
                "teamOneName",
                "teamTwoName",
                "sportLabel",
                "leagueLabel",
                "group1",
                "sportXeventId",
            )
        ).lower()
        return query in haystack

    @staticmethod
    def _status_from_market(market: Mapping[str, Any]) -> str:
        status = str(market.get("status") or "").strip().lower()
        return "active" if status == "active" else status

    @staticmethod
    def _market_hash(market: Mapping[str, Any]) -> str:
        return str(market.get("marketHash") or "").strip()

    @staticmethod
    def _market_title(market: Mapping[str, Any]) -> str:
        one = str(market.get("outcomeOneName") or market.get("teamOneName") or "Outcome One")
        two = str(market.get("outcomeTwoName") or market.get("teamTwoName") or "Outcome Two")
        league = str(market.get("leagueLabel") or market.get("sportLabel") or "").strip()
        suffix = f" ({league})" if league else ""
        return f"{one} vs {two}{suffix}"

    @staticmethod
    def _market_url(market: Mapping[str, Any]) -> str:
        event_id = str(market.get("sportXeventId") or "").strip()
        if event_id:
            return f"https://sx.bet/event/{event_id}"
        return "https://sx.bet"

    @staticmethod
    def _split_contract_id(contract_id: str) -> Tuple[str, str]:
        raw = str(contract_id or "").strip()
        if not raw:
            raise MarketConfigurationError("SX Bet order requires a contract id.")
        if ":" in raw:
            market_hash, outcome = raw.rsplit(":", 1)
        else:
            market_hash, outcome = raw, "ONE"
        market_hash = market_hash.strip()
        outcome = outcome.strip().upper()
        if not market_hash:
            raise MarketConfigurationError("SX Bet contract id must include a market hash.")
        if outcome not in {"ONE", "TWO"}:
            raise MarketConfigurationError("SX Bet contract outcome must be ONE or TWO.")
        return market_hash, outcome

    @staticmethod
    def _contract_id(market_hash: str, outcome: str) -> str:
        return f"{market_hash}:{outcome.upper()}"

    @staticmethod
    def _remaining_order_size(order: Mapping[str, Any]) -> float:
        total = SxBetAdapter._safe_float(order.get("totalBetSize"))
        filled = SxBetAdapter._safe_float(order.get("fillAmount")) or 0.0
        pending = SxBetAdapter._safe_float(order.get("pendingFillAmount")) or 0.0
        return max(0.0, (total or 0.0) - filled - pending)

    @staticmethod
    def _taker_space(remaining_maker_size: float, maker_odds: float) -> float:
        if maker_odds <= 0:
            return 0.0
        return max(0.0, (remaining_maker_size * ODDS_SCALE / (maker_odds * ODDS_SCALE)) - remaining_maker_size)

    def _from_base_units(self, value: float) -> float:
        return float(value) / float(10**self.base_token_decimals)

    def _to_base_units(self, value: Any) -> int:
        amount = Decimal(str(value))
        units = amount * Decimal(10**self.base_token_decimals)
        return int(units.quantize(Decimal("1"), rounding=ROUND_HALF_UP))

    @staticmethod
    def _to_odds_units(value: Any) -> int:
        odds = Decimal(str(value)) * Decimal(ODDS_SCALE)
        return int(odds.quantize(Decimal("1"), rounding=ROUND_HALF_UP))

    @staticmethod
    def _scaled_probability(value: Any) -> Optional[float]:
        number = SxBetAdapter._safe_float(value)
        if number is None or not math.isfinite(number):
            return None
        if number > 1.0:
            number = number / ODDS_SCALE
        if number < 0.0 or number > 1.0:
            return None
        return number

    @staticmethod
    def _limit_probability(value: Any) -> float:
        number = SxBetAdapter._safe_float(value)
        if number is None or number <= 0.0 or number >= 1.0:
            raise MarketConfigurationError("SX Bet limit price must be between 0 and 1.")
        return number

    @staticmethod
    def _safe_float(value: Any) -> Optional[float]:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _is_positive_number(value: Any) -> bool:
        number = SxBetAdapter._safe_float(value)
        return number is not None and math.isfinite(number) and number > 0
