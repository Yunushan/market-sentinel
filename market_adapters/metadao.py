from __future__ import annotations

import base64
import binascii
import math
import re
from typing import Any, Dict, List, Mapping, Optional
from urllib.parse import urlsplit

from .base import MarketAdapter
from .catalog import get_market_metadata
from .errors import MarketConfigurationError, MarketHTTPError, UnsupportedFeatureError
from .types import MarketContract, MarketEvent, PaperOrderRequest, PaperOrderResult, PriceSnapshot


DEFAULT_METADAO_API_BASE_URL = "https://market-api.metadao.fi"
METADAO_REFERENCES = (
    "https://api-docs.metadao.fi/introduction",
    "https://api-docs.metadao.fi/api-reference/get-api-tickers",
    "https://api-docs.metadao.fi/configuration",
    "https://github.com/metaDAOproject/futarchy-external-api",
)

_SOLANA_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_SOLANA_INDEX = {character: index for index, character in enumerate(_SOLANA_ALPHABET)}
_SOLANA_ADDRESS_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,64}$")
_SOLANA_SIGNATURE_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,128}$")


def _decode_solana_address(value: Any, *, label: str) -> str:
    text = str(value or "").strip()
    if not _SOLANA_ADDRESS_RE.fullmatch(text):
        raise MarketConfigurationError(f"MetaDAO {label} must be a canonical base58 public key.")
    number = 0
    try:
        for character in text:
            number = number * 58 + _SOLANA_INDEX[character]
    except KeyError as exc:
        raise MarketConfigurationError(f"MetaDAO {label} contains an invalid base58 character.") from exc
    raw = number.to_bytes((number.bit_length() + 7) // 8, "big") if number else b""
    leading_zeroes = len(text) - len(text.lstrip("1"))
    if len((b"\x00" * leading_zeroes) + raw) != 32:
        raise MarketConfigurationError(f"MetaDAO {label} must decode to exactly 32 bytes.")
    return text


class MetaDAOAdapter(MarketAdapter):
    """Public MetaDAO Futarchy DEX ticker adapter.

    MetaDAO's current official API is a public CoinGecko-compatible feed of
    DAO/token pairs. It exposes prices, bid/ask summaries, volume, and
    liquidity, but not depth or a user-order endpoint. The adapter maps those
    documented rows to the shared market model and keeps paper orders local.
    The documented Futarchy API also exposes a configurable Solana router for
    swaps; live forwarding is limited to an operator-reviewed, externally
    signed transaction targeted at an explicit router allow-list. The adapter
    never signs, approves tokens, or settles positions.
    """

    metadata = get_market_metadata("metadao")
    live_order_sides = ("BUY", "SELL")

    def health_check(self) -> Dict[str, Any]:
        health = super().health_check()
        health.update(
            {
                "api_base_url": self.api_base_url,
                "references": list(METADAO_REFERENCES),
                "public_api": True,
                "rate_limit_per_minute": 60,
                "live_trading_supported": True,
                "live_trading_enabled": self.config_bool("live_trading_enabled", False),
                "signed_transaction_submission_enabled": self.config_bool(
                    "metadao_submit_signed_transactions", False
                ),
                "rpc_configured": bool(self._configured_rpc_url),
                "allowlisted_router_program_count": len(self.router_program_ids),
                "wallet_transaction_required": True,
            }
        )
        return health

    @property
    def api_base_url(self) -> str:
        configured = self.config.get("metadao_api_base_url") or self.config.get("api_base_url")
        base = str(configured or DEFAULT_METADAO_API_BASE_URL).strip().rstrip("/")
        parsed = urlsplit(base)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.query or parsed.fragment:
            raise MarketConfigurationError("MetaDAO API base URL must be an absolute http(s) URL without query or fragment.")
        return base

    def list_events(self, query: str = "", limit: int = 50) -> List[MarketEvent]:
        self.ensure_capability("event_listing")
        desired = max(1, min(int(limit or 50), 100))
        rows = self._tickers()
        needle = str(query or "").strip().lower()
        if needle:
            rows = [row for row in rows if needle in self._search_text(row)]
        events: List[MarketEvent] = []
        for row in rows[:desired]:
            ticker_id = self._ticker_id(row)
            if ticker_id:
                events.append(self._event_from_row(row, ticker_id))
        return events

    def list_contracts(self, event_id: str) -> List[MarketContract]:
        self.ensure_capability("event_listing")
        event_key = self._required_ticker_id(event_id)
        row = self._find_ticker(event_key)
        if not row:
            raise MarketConfigurationError(f"MetaDAO ticker {event_key!r} was not found.")
        title = self._title(row, event_key)
        return [
            MarketContract(
                market_id=self.market_id,
                contract_id=self._contract_id(event_key),
                event_id=event_key,
                title=title,
                outcome=str(self._value(row, "base_symbol", "base_name") or "BASE"),
                url=f"{self.api_base_url}/api/tickers",
                status="open",
                raw=dict(row),
            )
        ]

    def get_price(self, contract_id: str) -> PriceSnapshot:
        self.ensure_capability("price_reading")
        ticker_id = self._split_contract_id(contract_id)
        row = self._find_ticker(ticker_id)
        if not row:
            raise MarketConfigurationError(f"MetaDAO ticker {ticker_id!r} was not found.")
        last = self._positive_number(self._value(row, "last_price", "lastPrice"))
        bid = self._positive_number(self._value(row, "bid", "highest_bid"))
        ask = self._positive_number(self._value(row, "ask", "lowest_ask"))
        midpoint = (bid + ask) / 2.0 if bid is not None and ask is not None else last or bid or ask
        if last is None:
            last = midpoint
        if last is None:
            raise MarketConfigurationError(f"MetaDAO ticker {ticker_id!r} did not return a usable price.")
        return PriceSnapshot(
            market_id=self.market_id,
            contract_id=self._contract_id(ticker_id),
            last=last,
            bid=bid,
            ask=ask,
            midpoint=midpoint,
            source="metadao_futarchy_dex_api",
            raw={"ticker": dict(row)},
        )

    def get_orderbook(self, contract_id: str):
        raise UnsupportedFeatureError(
            self.market_id,
            "orderbook_reading",
            "MetaDAO's official ticker feed exposes bid/ask summaries, not orderbook depth.",
        )

    def place_paper_order(self, order: PaperOrderRequest) -> PaperOrderResult:
        self.ensure_capability("paper_trading")
        ticker_id = self._validate_order(order)
        price = self._positive_number(order.limit_price)
        if price is None:
            price = self.get_price(self._contract_id(ticker_id)).last
        return PaperOrderResult(
            market_id=self.market_id,
            contract_id=self._contract_id(ticker_id),
            accepted=True,
            message=(
                f"DRY RUN: would place MetaDAO {str(order.side).upper()} for {float(order.size):.4f} token units"
                + (f" at price {float(price):.8f}" if price is not None else "")
            ),
            raw={
                "dry_run": True,
                "request": {
                    "ticker_id": ticker_id,
                    "side": str(order.side).upper(),
                    "size": float(order.size),
                    "limit_price": price,
                },
            },
        )

    def place_live_order(self, order: PaperOrderRequest) -> Dict[str, Any]:
        self.ensure_capability("live_trading")
        ticker_id = self._validate_order(order)
        audit = self.preflight_live_order(order, feature_name="MetaDAO live trading")
        if not self.config_bool("metadao_submit_signed_transactions", False):
            raise MarketConfigurationError(
                "MetaDAO live trading requires metadao_submit_signed_transactions=true after reviewing the signed router transaction."
            )
        rpc_url = self._configured_rpc_url
        if not rpc_url:
            raise MarketConfigurationError(
                "MetaDAO live orders require an explicit metadao_solana_rpc_url or solana_rpc_url for transaction submission."
            )
        allowlisted = self.router_program_ids
        if not allowlisted:
            raise MarketConfigurationError(
                "MetaDAO live orders require at least one explicitly reviewed metadao_router_program_ids entry."
            )
        row = self._find_ticker(ticker_id)
        if not row:
            raise MarketConfigurationError(f"MetaDAO ticker {ticker_id!r} was not found.")
        metadata = dict(order.metadata or {})
        signed = str(
            metadata.get("signed_transaction") or metadata.get("signedTransaction") or ""
        ).strip()
        raw = self._decode_signed_transaction(signed)
        router = _decode_solana_address(
            metadata.get("router_program_id") or metadata.get("program_id"), label="router program id"
        )
        if not any(router.casefold() == address.casefold() for address in allowlisted):
            raise MarketConfigurationError(
                "MetaDAO signed transaction metadata targets a program outside the reviewed router allow-list."
            )
        reviewed_ticker = str(metadata.get("ticker_id") or metadata.get("market_id") or "").strip()
        if reviewed_ticker != ticker_id:
            raise MarketConfigurationError("MetaDAO signed transaction metadata targets a different ticker.")
        expected_pool = str(self._value(row, "pool_id", "poolId") or "").strip()
        if expected_pool and str(metadata.get("pool_id") or "").strip() != expected_pool:
            raise MarketConfigurationError("MetaDAO signed transaction metadata targets a different pool.")
        instruction = str(metadata.get("instruction") or metadata.get("method") or "").strip().lower()
        if instruction not in {"swap", "buy", "sell"}:
            raise MarketConfigurationError("MetaDAO live orders require reviewed swap/buy/sell instruction metadata.")
        side = str(order.side or "").upper()
        if (side == "BUY" and instruction == "sell") or (side == "SELL" and instruction == "buy"):
            raise MarketConfigurationError("MetaDAO instruction metadata does not match the requested order side.")
        instruction_data = str(metadata.get("instruction_data") or metadata.get("data") or "").strip()
        if not instruction_data:
            raise MarketConfigurationError("MetaDAO live orders require reviewed instruction_data metadata.")
        signature = self._solana_rpc(
            rpc_url,
            "sendTransaction",
            [signed, {"encoding": "base64", "skipPreflight": False}],
        )
        if not isinstance(signature, str) or not _SOLANA_SIGNATURE_RE.fullmatch(signature):
            raise MarketHTTPError("MetaDAO RPC did not return a valid transaction signature.")
        return {
            "market_id": self.market_id,
            "contract_id": self._contract_id(ticker_id),
            "live": True,
            "preflight": audit,
            "submission": "solana_rpc_sendTransaction",
            "signature": signature,
            "router_program_id": router,
            "ticker_id": ticker_id,
            "instruction": instruction,
            "signed_transaction_bytes": len(raw),
        }

    def copy_trade_from_activity(self, activity: Mapping[str, Any]) -> PaperOrderResult:
        raise UnsupportedFeatureError(
            self.market_id,
            "copy_trading",
            "MetaDAO copy trading is unsupported because the official API does not expose account-activity mirroring.",
        )

    def _tickers(self) -> List[Mapping[str, Any]]:
        payload = self.runtime.get_json(self._url("/api/tickers"), params=None, headers={})
        if isinstance(payload, list):
            return [row for row in payload if isinstance(row, Mapping)]
        if isinstance(payload, Mapping):
            for key in ("data", "tickers", "result"):
                rows = payload.get(key)
                if isinstance(rows, list):
                    return [row for row in rows if isinstance(row, Mapping)]
        raise MarketConfigurationError("MetaDAO /api/tickers returned an unsupported payload shape.")

    def _find_ticker(self, ticker_id: str) -> Mapping[str, Any]:
        for row in self._tickers():
            if self._ticker_id(row) == ticker_id:
                return row
        return {}

    def _event_from_row(self, row: Mapping[str, Any], ticker_id: str) -> MarketEvent:
        return MarketEvent(
            market_id=self.market_id,
            event_id=ticker_id,
            title=self._title(row, ticker_id),
            url=f"{self.api_base_url}/api/tickers",
            status="open",
            raw=dict(row),
        )

    @classmethod
    def _ticker_id(cls, row: Mapping[str, Any]) -> str:
        value = cls._value(row, "ticker_id", "tickerId", "id")
        return str(value).strip() if value not in (None, "") else ""

    @classmethod
    def _title(cls, row: Mapping[str, Any], ticker_id: str) -> str:
        base = str(cls._value(row, "base_symbol", "base_name") or "BASE")
        quote = str(cls._value(row, "target_symbol", "target_name") or "QUOTE")
        return f"{base}/{quote} ({ticker_id})"

    @classmethod
    def _search_text(cls, row: Mapping[str, Any]) -> str:
        return " ".join(
            str(cls._value(row, key) or "")
            for key in ("ticker_id", "base_currency", "target_currency", "base_symbol", "base_name", "target_symbol", "target_name", "pool_id")
        ).lower()

    def _validate_order(self, order: PaperOrderRequest) -> str:
        self.ensure_order_market(order)
        ticker_id = self._split_contract_id(order.contract_id)
        if str(order.side or "").upper() not in self.live_order_sides:
            raise MarketConfigurationError("MetaDAO order side must be BUY or SELL.")
        try:
            size = float(order.size)
        except (TypeError, ValueError) as exc:
            raise MarketConfigurationError("MetaDAO order size must be numeric.") from exc
        if not math.isfinite(size) or size <= 0:
            raise MarketConfigurationError("MetaDAO order size must be positive and finite.")
        if order.limit_price is not None and self._positive_number(order.limit_price) is None:
            raise MarketConfigurationError("MetaDAO order limit price must be positive and finite.")
        return ticker_id

    @staticmethod
    def _contract_id(ticker_id: str) -> str:
        return f"{ticker_id}:0"

    @staticmethod
    def _split_contract_id(contract_id: Any) -> str:
        text = str(contract_id or "").strip()
        parts = text.rsplit(":", 1)
        if len(parts) != 2 or parts[1] != "0":
            raise MarketConfigurationError("MetaDAO contract id must be '<ticker_id>:0'.")
        return MetaDAOAdapter._required_ticker_id(parts[0])

    @staticmethod
    def _required_ticker_id(value: Any) -> str:
        text = str(value or "").strip()
        if not text or any(char in text for char in "\\/?#%") or len(text) > 200:
            raise MarketConfigurationError("MetaDAO ticker id is invalid.")
        return text

    @staticmethod
    def _positive_number(value: Any) -> Optional[float]:
        if value in (None, ""):
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) and number > 0 else None

    @staticmethod
    def _value(payload: Mapping[str, Any], *keys: str) -> Any:
        for key in keys:
            value = payload.get(key)
            if value not in (None, ""):
                return value
        return None

    def _url(self, path: str) -> str:
        if path != "/api/tickers":
            raise MarketConfigurationError("MetaDAO request path is not an approved official endpoint.")
        return f"{self.api_base_url}{path}"

    @property
    def _configured_rpc_url(self) -> str:
        configured = self.config.get("metadao_solana_rpc_url") or self.config.get("solana_rpc_url")
        if not configured:
            return ""
        value = str(configured).strip().rstrip("/")
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.query or parsed.fragment:
            raise MarketConfigurationError("MetaDAO Solana RPC URL must be an absolute http(s) URL without query or fragment.")
        return value

    @property
    def router_program_ids(self) -> tuple[str, ...]:
        configured = self.config.get("metadao_router_program_ids")
        if configured in (None, ""):
            return ()
        values = configured if isinstance(configured, (list, tuple, set)) else str(configured).split(",")
        addresses: List[str] = []
        for value in values:
            address = _decode_solana_address(value, label="router program id")
            if address.casefold() not in {item.casefold() for item in addresses}:
                addresses.append(address)
        return tuple(addresses)

    @staticmethod
    def _decode_signed_transaction(value: str) -> bytes:
        if not value or len(value) > 1_400_000 or len(value) % 4:
            raise MarketConfigurationError("MetaDAO live orders require a canonical base64 wallet-signed transaction.")
        try:
            raw = base64.b64decode(value, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise MarketConfigurationError("MetaDAO live orders require a canonical base64 wallet-signed transaction.") from exc
        if len(raw) < 64 or len(raw) > 1_000_000:
            raise MarketConfigurationError("MetaDAO signed transaction has an invalid size.")
        if base64.b64encode(raw).decode("ascii") != value:
            raise MarketConfigurationError("MetaDAO signed transaction must use canonical base64 encoding.")
        return raw

    def _solana_rpc(self, url: str, method: str, params: List[Any]) -> Any:
        payload = self.runtime.request_json(
            "POST",
            url,
            json_body={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
            headers={"Content-Type": "application/json"},
        )
        if not isinstance(payload, Mapping):
            raise MarketHTTPError("MetaDAO RPC response was not a JSON object.")
        if payload.get("error"):
            raise MarketHTTPError(f"MetaDAO RPC error: {payload['error']}")
        return payload.get("result")
