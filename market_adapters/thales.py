from __future__ import annotations

import math
import re
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple
from urllib.parse import urlsplit

from .base import MarketAdapter
from .catalog import get_market_metadata
from .errors import MarketConfigurationError, MarketHTTPError, UnsupportedFeatureError
from .types import (
    MarketContract,
    MarketEvent,
    PaperOrderRequest,
    PaperOrderResult,
    PriceSnapshot,
)


DEFAULT_THALES_API_BASE_URL = "https://overtimemarketsv2.xyz"
DEFAULT_THALES_RPC_URLS = {
    "10": "https://mainnet.optimism.io",
    "137": "https://polygon-rpc.com",
    "42161": "https://arb1.arbitrum.io/rpc",
    "8453": "https://mainnet.base.org",
}
THALES_REFERENCES = (
    "https://docs.thalesmarket.io/technical-documentation/thales-integration",
    "https://docs.thales.io/thales-digital-options/digital-options-integration",
    "https://docs.thales.io/thales-sports-markets/sports-markets-integration",
)
THALES_NETWORKS = {"10", "137", "42161", "8453"}
THALES_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


class ThalesMarketAdapter(MarketAdapter):
    """Official Thales Markets REST adapter for public AMM market data.

    The documented Thales API exposes public market rows and buy/sell quotes,
    but an actual trade is an on-chain wallet transaction against the AMM.
    Paper orders stay local; live orders accept only an externally signed raw
    transaction after explicit chain, target, method, and safety-gate checks.
    Addresses and network IDs are validated before being placed in request
    paths or transaction metadata to prevent path traversal and accidental
    submission to an unreviewed contract.
    """

    metadata = get_market_metadata("thales_market")
    live_order_sides = ("BUY", "SELL")

    def health_check(self) -> Dict[str, Any]:
        health = super().health_check()
        health.update(
            {
                "api_base_url": self.api_base_url,
                "network": self.network,
                "rpc_url": self.rpc_url,
                "amm_address_configured": bool(self.amm_address),
                "references": list(THALES_REFERENCES),
                "public_api": True,
                "live_trading_supported": True,
                "live_trading_enabled": self.config_bool("live_trading_enabled", False),
                "signed_transaction_submission_enabled": self.config_bool(
                    "thales_submit_signed_transactions", False
                ),
                "wallet_transaction_required": True,
                "collateral_required": True,
                "settlement_required": True,
                "signed_transaction_required": True,
            }
        )
        return health

    @property
    def api_base_url(self) -> str:
        configured = self.config.get("thales_api_base_url") or self.config.get("api_base_url")
        base = str(configured or DEFAULT_THALES_API_BASE_URL).strip().rstrip("/")
        parsed = urlsplit(base)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.query or parsed.fragment:
            raise MarketConfigurationError("Thales API base URL must be an absolute http(s) URL without query or fragment.")
        return base

    @property
    def network(self) -> str:
        value = str(self.config.get("thales_network") or "10").strip()
        if value not in THALES_NETWORKS:
            allowed = ", ".join(sorted(THALES_NETWORKS))
            raise MarketConfigurationError(f"Thales network must be one of: {allowed}.")
        return value

    @property
    def rpc_url(self) -> str:
        configured = self.config.get("thales_rpc_url") or self.config.get("evm_rpc_url")
        value = str(configured or DEFAULT_THALES_RPC_URLS[self.network]).strip().rstrip("/")
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.query or parsed.fragment:
            raise MarketConfigurationError("Thales RPC URL must be an absolute http(s) URL without query or fragment.")
        return value

    @property
    def amm_address(self) -> str:
        configured = (
            self.config.get("thales_amm_address")
            or self.config.get("thales_ranged_amm_address")
            or self.config.get("thales_sports_amm_address")
        )
        if configured in (None, ""):
            return ""
        return self._address(configured, label="AMM address")

    def list_events(self, query: str = "", limit: int = 50) -> List[MarketEvent]:
        self.ensure_capability("event_listing")
        desired = max(1, min(int(limit or 50), 100))
        payload = self._get("/markets", params={"ungroup": "true"})
        rows = self._market_rows(payload)
        needle = str(query or "").strip().lower()
        if needle:
            rows = [row for row in rows if needle in self._search_text(row)]

        events: List[MarketEvent] = []
        seen: set[str] = set()
        for row in rows:
            address = self._address(self._value(row, "address", "marketAddress"))
            if not address or address.lower() in seen:
                continue
            seen.add(address.lower())
            events.append(self._event_from_row(row, address))
            if len(events) >= desired:
                break
        return events

    def list_contracts(self, event_id: str) -> List[MarketContract]:
        self.ensure_capability("event_listing")
        address = self._address(event_id, label="market address")
        row = self._get_market(address)
        rows = self._market_rows(self._get("/markets", params={"ungroup": "true"}))
        matching = [candidate for candidate in rows if self._address(self._value(candidate, "address", "marketAddress")) == address]
        if not matching:
            matching = [row]
        return self._contracts_from_rows(address, matching)

    def get_price(self, contract_id: str) -> PriceSnapshot:
        self.ensure_capability("price_reading")
        address, outcome_index = self._split_contract_id(contract_id)
        rows = self._market_rows(self._get("/markets", params={"ungroup": "true"}))
        row = self._select_position(rows, address, outcome_index)
        if not row:
            row = self._get_market(address)
        price = self._probability(self._value(row, "price", "pricePerPosition"))
        if price is None:
            quote = self._get(
                f"/markets/{address}/buy-quote",
                params={"position": self._position(row, outcome_index), "buyIn": self._quote_buy_in()},
            )
            price = self._probability(self._value(self._mapping_payload(quote), "pricePerPosition", "price"))
        if price is None:
            raise MarketConfigurationError(f"Thales market {address!r} did not return a usable AMM price.")
        return PriceSnapshot(
            market_id=self.market_id,
            contract_id=self._contract_id(address, outcome_index),
            last=price,
            bid=price,
            ask=price,
            midpoint=price,
            source="thales_markets_rest",
            raw={"market": dict(row), "outcome_index": outcome_index},
        )

    def get_orderbook(self, contract_id: str):
        raise UnsupportedFeatureError(
            self.market_id,
            "orderbook_reading",
            "Thales Markets exposes AMM prices and buy/sell quotes, not a CLOB orderbook.",
        )

    def place_paper_order(self, order: PaperOrderRequest) -> PaperOrderResult:
        self.ensure_capability("paper_trading")
        address, outcome_index = self._validate_order(order)
        price = self._probability(order.limit_price)
        if price is None:
            price = self.get_price(self._contract_id(address, outcome_index)).last
        return PaperOrderResult(
            market_id=self.market_id,
            contract_id=self._contract_id(address, outcome_index),
            accepted=True,
            message=(
                f"DRY RUN: would place Thales {str(order.side).upper()} for {float(order.size):.4f} collateral"
                + (f" at AMM probability {float(price):.4f}" if price is not None else "")
            ),
            filled_size=0.0,
            average_price=price,
            raw={
                "dry_run": True,
                "request": {
                    "network": self.network,
                    "marketAddress": address,
                    "position": self._position({}, outcome_index),
                    "buyIn": float(order.size) if str(order.side).upper() == "BUY" else None,
                    "sellAmount": float(order.size) if str(order.side).upper() == "SELL" else None,
                    "limitPrice": price,
                },
            },
        )

    def place_live_order(self, order: PaperOrderRequest) -> Dict[str, Any]:
        self.ensure_capability("live_trading")
        address, outcome_index = self._validate_order(order)
        audit = self.preflight_live_order(order, feature_name="Thales live trading")
        if not self.config_bool("thales_submit_signed_transactions", False):
            raise MarketConfigurationError(
                "Thales live trading requires thales_submit_signed_transactions=true after reviewing the signed transaction."
            )
        expected_target = self.amm_address
        if not expected_target:
            raise MarketConfigurationError(
                "Thales live trading requires thales_amm_address (or a reviewed ranged/sports AMM address) in configuration."
            )

        metadata = order.metadata if isinstance(order.metadata, Mapping) else {}
        signed = metadata.get("signed_transaction") or metadata.get("signedTransaction")
        if not isinstance(signed, str) or not re.fullmatch(r"0x[0-9a-fA-F]+", signed) or len(signed) % 2:
            raise MarketConfigurationError(
                "Thales live orders require an externally signed raw transaction in metadata['signed_transaction']."
            )

        target = metadata.get("transaction_to") or metadata.get("to") or metadata.get("amm_address")
        if not target:
            raise MarketConfigurationError(
                "Thales live orders require metadata['transaction_to'] identifying the reviewed AMM contract."
            )
        target = self._address(target, label="transaction target")
        if target.lower() != expected_target.lower():
            raise MarketConfigurationError("Thales signed transaction target does not match configured thales_amm_address.")

        chain_id = metadata.get("chain_id", metadata.get("chainId"))
        if chain_id in (None, ""):
            raise MarketConfigurationError("Thales live orders require metadata['chain_id'] for the reviewed network.")
        if self._chain_id(chain_id) != int(self.network):
            raise MarketConfigurationError("Thales signed transaction chain_id does not match thales_network.")

        method = str(metadata.get("method") or metadata.get("transaction_method") or "").strip()
        if method not in {"buyFromAmm", "buyFromAMM", "sellToAmm", "sellToAMM", "buyFromRangedAmm", "sellToRangedAmm"}:
            raise MarketConfigurationError(
                "Thales live orders require a reviewed transaction method: buyFromAmm, sellToAmm, or the documented ranged equivalent."
            )
        if str(order.side).upper() == "BUY" and not method.lower().startswith("buy"):
            raise MarketConfigurationError("Thales BUY orders require a buy AMM transaction method.")
        if str(order.side).upper() == "SELL" and not method.lower().startswith("sell"):
            raise MarketConfigurationError("Thales SELL orders require a sell AMM transaction method.")

        data = metadata.get("data") or metadata.get("calldata")
        if not isinstance(data, str) or not re.fullmatch(r"0x[0-9a-fA-F]+", data) or len(data) < 10 or len(data) % 2:
            raise MarketConfigurationError(
                "Thales live orders require reviewed ABI calldata in metadata['data'] (at least a 4-byte selector)."
            )
        transaction_market = metadata.get("market_address") or metadata.get("marketAddress")
        if transaction_market not in (None, "") and self._address(transaction_market, label="transaction market") != address:
            raise MarketConfigurationError("Thales transaction market does not match the selected contract.")
        transaction_position = metadata.get("position")
        if transaction_position not in (None, ""):
            expected_position = self._position({}, outcome_index)
            if str(transaction_position).strip().upper() != expected_position:
                raise MarketConfigurationError("Thales transaction position does not match the selected outcome.")

        response = self._rpc("eth_sendRawTransaction", [signed])
        if not isinstance(response, str) or not re.fullmatch(r"0x[0-9a-fA-F]{64}", response):
            raise MarketHTTPError("Thales RPC did not return a valid transaction hash.")
        return {
            "live": True,
            "tx_hash": response,
            "audit": audit,
            "signed_transaction_submitted": True,
            "network": self.network,
            "transaction_to": target,
            "method": method,
        }

    def copy_trade_from_activity(self, activity: Mapping[str, Any]) -> PaperOrderResult:
        raise UnsupportedFeatureError(
            self.market_id,
            "copy_trading",
            "Thales copy trading is unsupported because the official public API does not expose account-activity mirroring.",
        )

    def _get(self, path: str, *, params: Optional[Mapping[str, Any]] = None) -> Any:
        return self.runtime.get_json(self._url(path), params=params, headers={})

    def _rpc(self, method: str, params: List[Any]) -> Any:
        payload = self.runtime.request_json(
            "POST",
            self.rpc_url,
            json_body={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
            headers={"Content-Type": "application/json"},
        )
        if not isinstance(payload, Mapping):
            raise MarketHTTPError("Thales RPC response was not a JSON object.")
        if payload.get("error"):
            raise MarketHTTPError(f"Thales RPC error: {payload['error']}")
        return payload.get("result")

    def _get_market(self, address: str) -> Mapping[str, Any]:
        payload = self._get(f"/markets/{address}")
        mapping = self._mapping_payload(payload)
        if isinstance(mapping.get("market"), Mapping):
            mapping = dict(mapping["market"])
        if not mapping or not self._address(self._value(mapping, "address", "marketAddress")):
            raise MarketConfigurationError(f"Thales market {address!r} was not found.")
        return mapping

    def _validate_order(self, order: PaperOrderRequest) -> Tuple[str, int]:
        self.ensure_order_market(order)
        address, outcome_index = self._split_contract_id(order.contract_id)
        side = str(order.side or "").upper()
        if side not in self.live_order_sides:
            raise MarketConfigurationError("Thales order side must be BUY or SELL.")
        try:
            size = float(order.size)
        except (TypeError, ValueError) as exc:
            raise MarketConfigurationError("Thales order size must be numeric.") from exc
        if not math.isfinite(size) or size <= 0:
            raise MarketConfigurationError("Thales order size must be positive and finite.")
        if order.limit_price is not None and self._probability(order.limit_price) is None:
            raise MarketConfigurationError("Thales order limit price must be between 0 and 1.")
        return address, outcome_index

    def _event_from_row(self, row: Mapping[str, Any], address: str) -> MarketEvent:
        asset = str(self._value(row, "asset", "symbol") or "Market")
        position = str(self._value(row, "position", "outcome") or "")
        strike = self._value(row, "strikePrice", "strike_price")
        maturity = str(self._value(row, "maturityDate", "maturity_date") or "")
        details = " ".join(part for part in (asset, position, f"@ {strike}" if strike is not None else "", f"({maturity})" if maturity else "") if part)
        return MarketEvent(
            market_id=self.market_id,
            event_id=address,
            title=details or address,
            url=f"{self.api_base_url}/thales/networks/{self.network}/markets/{address}",
            status=self._status(row),
            raw=dict(row),
        )

    def _contracts_from_rows(self, address: str, rows: Iterable[Mapping[str, Any]]) -> List[MarketContract]:
        rows = list(rows)
        base = rows[0] if rows else {}
        title = self._event_from_row(base, address).title
        status = self._status(base)
        contracts: List[MarketContract] = []
        seen: set[int] = set()
        for row in rows:
            index = self._outcome_index(self._value(row, "position", "outcome"))
            if index in seen:
                continue
            seen.add(index)
            outcome = str(self._value(row, "position", "outcome") or ("UP" if index == 0 else "DOWN"))
            contracts.append(
                MarketContract(
                    market_id=self.market_id,
                    contract_id=self._contract_id(address, index),
                    event_id=address,
                    title=f"{title} - {outcome}",
                    outcome=outcome,
                    url=f"{self.api_base_url}/thales/networks/{self.network}/markets/{address}",
                    status=self._status(row) or status,
                    raw={"market": dict(row), "outcome_index": index},
                )
            )
        if len(contracts) == 1:
            index = 1 - int(contracts[0].contract_id.rsplit(":", 1)[1])
            outcome = "DOWN" if index else "UP"
            contracts.append(
                MarketContract(
                    market_id=self.market_id,
                    contract_id=self._contract_id(address, index),
                    event_id=address,
                    title=f"{title} - {outcome}",
                    outcome=outcome,
                    url=f"{self.api_base_url}/thales/networks/{self.network}/markets/{address}",
                    status=status,
                    raw={"market": dict(base), "outcome_index": index, "synthetic": True},
                )
            )
        contracts.sort(key=lambda contract: contract.contract_id)
        return contracts

    @classmethod
    def _market_rows(cls, payload: Any) -> List[Mapping[str, Any]]:
        rows: List[Mapping[str, Any]] = []

        def visit(value: Any) -> None:
            if isinstance(value, Mapping):
                if cls._address(value.get("address") or value.get("marketAddress")):
                    rows.append(value)
                    return
                for child in value.values():
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)

        visit(payload)
        return rows

    def _select_position(self, rows: Iterable[Mapping[str, Any]], address: str, outcome_index: int) -> Mapping[str, Any]:
        for row in rows:
            if self._address(self._value(row, "address", "marketAddress")) != address:
                continue
            if self._outcome_index(self._value(row, "position", "outcome")) == outcome_index:
                return row
        return {}

    def _split_contract_id(self, contract_id: Any) -> Tuple[str, int]:
        text = str(contract_id or "").strip()
        parts = text.rsplit(":", 1)
        if len(parts) != 2 or parts[1] not in {"0", "1"}:
            raise MarketConfigurationError("Thales contract id must be '<0x-market-address>:0' or ':1'.")
        return self._address(parts[0], label="market address"), int(parts[1])

    @staticmethod
    def _contract_id(address: str, outcome_index: int) -> str:
        return f"{address}:{int(outcome_index)}"

    @staticmethod
    def _address(value: Any, *, label: str = "address") -> str:
        text = str(value or "").strip()
        if not THALES_ADDRESS_RE.fullmatch(text):
            return "" if not text else (_ for _ in ()).throw(MarketConfigurationError(f"Thales {label} must be a 20-byte hex address."))
        return text

    @staticmethod
    def _outcome_index(value: Any) -> int:
        text = str(value or "").strip().upper()
        if text in {"DOWN", "NO", "OUT", "0", "FALSE"}:
            return 1
        return 0

    @classmethod
    def _position(cls, row: Mapping[str, Any], outcome_index: int) -> str:
        value = cls._value(row, "position", "outcome")
        if value not in (None, ""):
            return str(value).upper()
        return "UP" if outcome_index == 0 else "DOWN"

    def _quote_buy_in(self) -> str:
        value = self.config.get("thales_quote_buy_in") or "100"
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise MarketConfigurationError("Thales quote buy-in must be numeric.") from exc
        if not math.isfinite(number) or number <= 0:
            raise MarketConfigurationError("Thales quote buy-in must be positive and finite.")
        return str(number)

    @staticmethod
    def _chain_id(value: Any) -> int:
        text = str(value or "").strip().lower()
        try:
            return int(text, 16) if text.startswith("0x") else int(text, 10)
        except (TypeError, ValueError) as exc:
            raise MarketConfigurationError("Thales chain_id must be a decimal or hexadecimal integer.") from exc

    def _url(self, path: str) -> str:
        segments = [segment for segment in str(path or "").split("/") if segment]
        if any(segment in {".", ".."} or "/" in segment or "\\" in segment for segment in segments):
            raise MarketConfigurationError("Thales API path contains an invalid segment.")
        return f"{self.api_base_url}/thales/networks/{self.network}/{'/'.join(segments)}"

    @staticmethod
    def _mapping_payload(payload: Any) -> Dict[str, Any]:
        if isinstance(payload, Mapping):
            data = payload.get("data")
            return dict(data) if isinstance(data, Mapping) else dict(payload)
        return {}

    @staticmethod
    def _value(payload: Mapping[str, Any], *keys: str) -> Any:
        for key in keys:
            value = payload.get(key)
            if value not in (None, ""):
                return value
        return None

    @classmethod
    def _probability(cls, value: Any) -> Optional[float]:
        if value in (None, ""):
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(number) or not 0 < number < 1:
            return None
        return number

    @classmethod
    def _search_text(cls, row: Mapping[str, Any]) -> str:
        return " ".join(str(cls._value(row, key) or "") for key in ("asset", "position", "address", "maturityDate", "strikePrice")).lower()

    @classmethod
    def _status(cls, row: Mapping[str, Any]) -> str:
        value = cls._value(row, "status", "state")
        if value not in (None, ""):
            return str(value).lower()
        return "open" if row.get("isOpen", True) else "closed"
