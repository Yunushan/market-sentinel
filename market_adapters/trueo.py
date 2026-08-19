from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Mapping, Optional, Tuple
from urllib.parse import urlsplit

from .base import MarketAdapter
from .catalog import get_market_metadata
from .errors import MarketConfigurationError, MarketHTTPError, UnsupportedFeatureError
from .types import MarketContract, MarketEvent, OrderBookSnapshot, PaperOrderRequest, PaperOrderResult, PriceSnapshot


DEFAULT_TRUEO_RPC_URL = "https://mainnet.base.org"
DEFAULT_TRUEO_MANAGER = "0x61A98Bef11867c69489B91f340fE545eEfc695d7"
TRUEO_REFERENCES = (
    "https://docs.trueo.com/deployments",
    "https://docs.trueo.com/markets",
    "https://docs.trueo.com/trading",
    "https://github.com/trueo-protocol/trueo-contracts",
)
ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")

# Keccak-256 selectors from the official TruthMarketManager, TruthMarket and
# Uniswap V3 pool interfaces. Keeping the selectors explicit avoids shipping a
# generated ABI or a wallet library just to perform read-only calls.
SELECTORS = {
    "active_count": "7d6a0d1a",
    "active_market": "dd5adfa3",
    "question": "066f69af",
    "source": "17447836",
    "additional_info": "4063c865",
    "end_of_trading": "d6a05e67",
    "status": "a3dd2619",
    "winning_position": "2486d671",
    "yes_token": "f0d9bb20",
    "no_token": "11a9f10a",
    "payment_token": "3013ce29",
    "pools": "e4b6db4c",
    "slot0": "3850c7bd",
    "token0": "0dfe1681",
    "token1": "d21220a7",
    "decimals": "313ce567",
}


class TrueoAdapter(MarketAdapter):
    """Official Trueo Base on-chain adapter.

    Trueo publishes no hosted market-data API: the supported integration is the
    deployed ``TruthMarketManager`` and each market's immutable on-chain fields.
    This adapter reads the manager/market contracts through JSON-RPC, derives a
    current YES/NO AMM price from the documented Uniswap V3 pools, and keeps
    paper trading local. Live execution accepts only a complete, externally
    signed raw transaction and remains disabled unless the operator enables the
    explicit submission gate.
    """

    metadata = get_market_metadata("trueo")
    live_order_sides = ("BUY", "SELL")

    def __init__(self, config: Optional[Mapping[str, Any]] = None, *, runtime=None) -> None:
        super().__init__(config, runtime=runtime)
        self._market_cache: Dict[str, Dict[str, Any]] = {}
        self._token_decimals_cache: Dict[str, int] = {}

    @property
    def rpc_url(self) -> str:
        configured = self.config.get("trueo_rpc_url") or self.config.get("evm_rpc_url")
        value = str(configured or DEFAULT_TRUEO_RPC_URL).strip().rstrip("/")
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.query or parsed.fragment:
            raise MarketConfigurationError("Trueo RPC URL must be an absolute http(s) URL without query or fragment.")
        return value

    @property
    def manager_address(self) -> str:
        value = self.config.get("trueo_manager_address") or DEFAULT_TRUEO_MANAGER
        return self._address(value, label="manager address")

    def health_check(self) -> Dict[str, Any]:
        health = super().health_check()
        health.update(
            {
                "rpc_url": self.rpc_url,
                "manager_address": self.manager_address,
                "network": "Base mainnet",
                "references": list(TRUEO_REFERENCES),
                "public_api": False,
                "onchain_reading": True,
                "wallet_transaction_required": True,
                "settlement_required": True,
            }
        )
        return health

    def list_events(self, query: str = "", limit: int = 50) -> List[MarketEvent]:
        self.ensure_capability("event_listing")
        desired = max(1, min(int(limit or 50), 100))
        count = self._call_uint(self.manager_address, SELECTORS["active_count"])
        rows: List[MarketEvent] = []
        needle = str(query or "").strip().lower()
        self._market_cache = {}
        for index in range(min(count, desired * 4)):
            address = self._call_address(
                self.manager_address,
                SELECTORS["active_market"] + self._uint_arg(index),
            )
            row = self._read_market(address)
            title = str(row.get("question") or address)
            if needle and needle not in title.lower() and needle not in str(row.get("source") or "").lower():
                continue
            self._market_cache[address.lower()] = row
            rows.append(
                MarketEvent(
                    market_id=self.market_id,
                    event_id=address,
                    title=title,
                    url=f"https://basescan.org/address/{address}",
                    status=str(row.get("status_name") or "unknown"),
                    raw=dict(row),
                )
            )
            if len(rows) >= desired:
                break
        return rows

    def list_contracts(self, event_id: str) -> List[MarketContract]:
        self.ensure_capability("event_listing")
        address = self._address(event_id, label="market address")
        row = self._market_cache.get(address.lower()) or self._read_market(address)
        title = str(row.get("question") or address)
        status = str(row.get("status_name") or "unknown")
        return [
            MarketContract(
                market_id=self.market_id,
                contract_id=f"{address}:0",
                event_id=address,
                title=f"{title} - YES",
                outcome="YES",
                url=f"https://basescan.org/address/{address}",
                status=status,
                raw={"market": dict(row), "outcome": 1, "token": row["yes_token"]},
            ),
            MarketContract(
                market_id=self.market_id,
                contract_id=f"{address}:1",
                event_id=address,
                title=f"{title} - NO",
                outcome="NO",
                url=f"https://basescan.org/address/{address}",
                status=status,
                raw={"market": dict(row), "outcome": 2, "token": row["no_token"]},
            ),
        ]

    def get_price(self, contract_id: str) -> PriceSnapshot:
        self.ensure_capability("price_reading")
        market_address, outcome_index = self._split_contract_id(contract_id)
        row = self._market_cache.get(market_address.lower()) or self._read_market(market_address)
        pool = row["yes_pool"] if outcome_index == 0 else row["no_pool"]
        payment_token = row["payment_token"]
        outcome_token = row["yes_token"] if outcome_index == 0 else row["no_token"]
        value, raw = self._pool_price(pool, outcome_token, payment_token)
        return PriceSnapshot(
            market_id=self.market_id,
            contract_id=f"{market_address}:{outcome_index}",
            last=value,
            midpoint=value,
            source="trueo_uniswap_v3_slot0",
            raw={"market": dict(row), "pool": pool, "pool_read": raw},
        )

    def get_orderbook(self, contract_id: str) -> OrderBookSnapshot:
        raise UnsupportedFeatureError(
            self.market_id,
            "orderbook_reading",
            "Trueo documents Uniswap liquidity pools rather than a CLOB; slot0-derived AMM prices are not an orderbook.",
        )

    def place_paper_order(self, order: PaperOrderRequest) -> PaperOrderResult:
        self.ensure_capability("paper_trading")
        self.ensure_order_market(order)
        market_address, outcome_index = self._split_contract_id(order.contract_id)
        side = str(order.side or "").upper()
        if side not in self.live_order_sides:
            raise MarketConfigurationError("Trueo order side must be BUY or SELL.")
        size = self._finite_float(order.size, "order size")
        if size <= 0:
            raise MarketConfigurationError("Trueo order size must be positive.")
        price = None
        if order.limit_price is not None:
            price = self._finite_float(order.limit_price, "limit price")
            if price <= 0 or price > 1:
                raise MarketConfigurationError("Trueo limit price must be greater than 0 and at most 1.")
        if price is None:
            price = self.get_price(f"{market_address}:{outcome_index}").last
        return PaperOrderResult(
            market_id=self.market_id,
            contract_id=f"{market_address}:{outcome_index}",
            accepted=True,
            message=f"DRY RUN: would place Trueo {side} for {size:.6f} outcome units at {price:.6f}",
            average_price=price,
            raw={
                "dry_run": True,
                "request": {
                    "market_address": market_address,
                    "outcome_index": outcome_index,
                    "side": side,
                    "size": size,
                    "limit_price": price,
                    "execution_model": "Uniswap V3 market pool",
                },
            },
        )

    def place_live_order(self, order: PaperOrderRequest) -> Dict[str, Any]:
        self.ensure_order_market(order)
        audit = self.preflight_live_order(order, feature_name="Trueo live trading")
        if not self.config_bool("trueo_submit_signed_transactions", False):
            raise MarketConfigurationError(
                "Trueo live trading requires trueo_submit_signed_transactions=true after reviewing the signed transaction."
            )
        signed = order.metadata.get("signed_transaction")
        if not isinstance(signed, str) or not re.fullmatch(r"0x[0-9a-fA-F]+", signed) or len(signed) % 2:
            raise MarketConfigurationError(
                "Trueo live orders require an externally signed raw transaction in metadata['signed_transaction']."
            )
        response = self._rpc("eth_sendRawTransaction", [signed])
        if not isinstance(response, str) or not response.startswith("0x"):
            raise MarketHTTPError("Trueo RPC did not return a transaction hash.")
        return {"live": True, "tx_hash": response, "audit": audit, "signed_transaction_submitted": True}

    def copy_trade_from_activity(self, activity: Mapping[str, Any]) -> PaperOrderResult:
        raise UnsupportedFeatureError(
            self.market_id,
            "copy_trading",
            "Trueo has no official account-activity mirroring API; copy trading is unsupported.",
        )

    def _read_market(self, address: str) -> Dict[str, Any]:
        row = {
            "address": address,
            "question": self._call_string(address, SELECTORS["question"]),
            "source": self._call_string(address, SELECTORS["source"]),
            "additional_info": self._call_string(address, SELECTORS["additional_info"]),
            "end_of_trading": self._call_uint(address, SELECTORS["end_of_trading"]),
            "status": self._call_uint(address, SELECTORS["status"]),
            "winning_position": self._call_uint(address, SELECTORS["winning_position"]),
            "yes_token": self._call_address(address, SELECTORS["yes_token"]),
            "no_token": self._call_address(address, SELECTORS["no_token"]),
            "payment_token": self._call_address(address, SELECTORS["payment_token"]),
        }
        pools = self._call(address, SELECTORS["pools"])
        decoded = self._decode(pools, ("address", "address"))
        row["yes_pool"], row["no_pool"] = self._address(decoded[0]), self._address(decoded[1])
        row["status_name"] = self._status_name(int(row["status"]))
        return row

    def _pool_price(self, pool: str, outcome_token: str, payment_token: str) -> Tuple[float, Dict[str, Any]]:
        token0 = self._call_address(pool, SELECTORS["token0"])
        token1 = self._call_address(pool, SELECTORS["token1"])
        slot0 = self._decode(self._call(pool, SELECTORS["slot0"]), ("uint160", "int24", "uint16", "uint16", "uint16", "uint8", "bool"))
        sqrt_price = int(slot0[0])
        if sqrt_price <= 0:
            raise MarketConfigurationError("Trueo pool returned a non-positive sqrt price.")
        decimals0 = self._token_decimals(token0)
        decimals1 = self._token_decimals(token1)
        raw_ratio = (sqrt_price * sqrt_price) / float(2**192)
        if token0.lower() == outcome_token.lower() and token1.lower() == payment_token.lower():
            price = raw_ratio * (10 ** (decimals0 - decimals1))
        elif token1.lower() == outcome_token.lower() and token0.lower() == payment_token.lower():
            price = (1.0 / raw_ratio) * (10 ** (decimals1 - decimals0))
        else:
            raise MarketConfigurationError("Trueo pool tokens do not match the market outcome/payment tokens.")
        if not math.isfinite(price) or price <= 0:
            raise MarketConfigurationError("Trueo pool returned an invalid outcome price.")
        return price, {"sqrt_price_x96": sqrt_price, "token0": token0, "token1": token1, "decimals0": decimals0, "decimals1": decimals1}

    def _token_decimals(self, token: str) -> int:
        key = token.lower()
        if key not in self._token_decimals_cache:
            value = int(self._call_uint(token, SELECTORS["decimals"]))
            if value < 0 or value > 36:
                raise MarketConfigurationError("Trueo token decimals are outside the supported range.")
            self._token_decimals_cache[key] = value
        return self._token_decimals_cache[key]

    def _rpc(self, method: str, params: List[Any]) -> Any:
        payload = self.runtime.request_json(
            "POST",
            self.rpc_url,
            json_body={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
            headers={},
        )
        if not isinstance(payload, Mapping):
            raise MarketHTTPError("Trueo RPC response was not a JSON object.")
        if payload.get("error"):
            raise MarketHTTPError(f"Trueo RPC error: {payload['error']}")
        return payload.get("result")

    def _call(self, address: str, data: str) -> str:
        result = self._rpc("eth_call", [{"to": self._address(address), "data": "0x" + data}, "latest"])
        if not isinstance(result, str) or not result.startswith("0x"):
            raise MarketHTTPError("Trueo eth_call did not return hex data.")
        return result

    def _call_uint(self, address: str, data: str) -> int:
        values = self._decode(self._call(address, data), ("uint256",))
        return int(values[0])

    def _call_address(self, address: str, data: str) -> str:
        values = self._decode(self._call(address, data), ("address",))
        return self._address(values[0])

    def _call_string(self, address: str, data: str) -> str:
        values = self._decode(self._call(address, data), ("string",))
        return str(values[0])

    @staticmethod
    def _decode(value: str, types: Tuple[str, ...]) -> Tuple[Any, ...]:
        try:
            from eth_abi import decode

            return tuple(decode(list(types), bytes.fromhex(value[2:])))
        except (ImportError, ValueError, TypeError, OverflowError) as exc:
            raise MarketConfigurationError("Trueo RPC returned data that did not match the documented ABI.") from exc

    @staticmethod
    def _uint_arg(value: int) -> str:
        return f"{int(value):064x}"

    @classmethod
    def _address(cls, value: Any, *, label: str = "address") -> str:
        text = str(value or "").strip()
        if not ADDRESS_RE.fullmatch(text):
            raise MarketConfigurationError(f"Trueo {label} must be a 20-byte hex address.")
        return text

    @classmethod
    def _split_contract_id(cls, value: Any) -> Tuple[str, int]:
        parts = str(value or "").strip().split(":")
        if len(parts) != 2 or not parts[1].isdigit() or int(parts[1]) not in {0, 1}:
            raise MarketConfigurationError("Trueo contract id must be '<market-address>:0' (YES) or ':1' (NO).")
        return cls._address(parts[0], label="market address"), int(parts[1])

    @staticmethod
    def _status_name(value: int) -> str:
        return {
            0: "created",
            1: "open_for_resolution",
            2: "resolution_proposed",
            3: "dispute_raised",
            4: "set_by_council",
            5: "reset_by_council",
            6: "escalated_dispute_raised",
            7: "finalized",
        }.get(value, f"unknown:{value}")

