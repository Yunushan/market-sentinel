from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Mapping, Optional, Tuple
from urllib.parse import urlsplit

from .base import MarketAdapter
from .catalog import get_market_metadata
from .errors import MarketConfigurationError
from .identity import require_activity_identity
from .types import MarketContract, MarketEvent, OrderBookLevel, OrderBookSnapshot, PaperOrderRequest, PaperOrderResult, PriceSnapshot


DEFAULT_HYPERLIQUID_MAINNET_URL = "https://api.hyperliquid.xyz"
DEFAULT_HYPERLIQUID_TESTNET_URL = "https://api.hyperliquid-testnet.xyz"
HYPERLIQUID_REFERENCES = (
    "https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint/spot",
    "https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint",
    "https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/asset-ids",
    "https://hyperliquid.gitbook.io/Hyperliquid-docs/for-developers/api/exchange-endpoint",
)
OUTCOME_ID_RE = re.compile(r"^[0-9]+$")


class HyperliquidAdapter(MarketAdapter):
    """Official Hyperliquid HIP-4 outcome-market adapter.

    HIP-4 exposes outcome metadata through the public ``info`` endpoint and
    represents each binary side as a synthetic spot coin ``#<encoding>`` where
    ``encoding = 10 * outcome + side``.  The adapter maps the documented
    metadata and ``l2Book`` responses to the shared model.  Live submission is
    accepted only when the caller supplies a complete externally signed
    HyperCore exchange payload; this class never handles private keys.
    """

    metadata = get_market_metadata("hyperliquid")
    live_order_sides = ("BUY", "SELL")

    def __init__(self, config: Optional[Mapping[str, Any]] = None, *, runtime=None) -> None:
        super().__init__(config, runtime=runtime)
        self._outcome_cache: Dict[str, Dict[str, Any]] = {}
        self._question_cache: Dict[str, Dict[str, Any]] = {}

    @property
    def api_base_url(self) -> str:
        configured = self.config.get("hyperliquid_api_base_url") or self.config.get("api_base_url")
        network = str(self.config.get("hyperliquid_network") or "mainnet").strip().lower()
        default = DEFAULT_HYPERLIQUID_TESTNET_URL if network == "testnet" else DEFAULT_HYPERLIQUID_MAINNET_URL
        base = str(configured or default).strip().rstrip("/")
        parsed = urlsplit(base)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.query or parsed.fragment:
            raise MarketConfigurationError("Hyperliquid API base URL must be an absolute http(s) URL without query or fragment.")
        if network not in {"mainnet", "testnet"}:
            raise MarketConfigurationError("Hyperliquid network must be 'mainnet' or 'testnet'.")
        return base

    @property
    def network(self) -> str:
        value = str(self.config.get("hyperliquid_network") or "mainnet").strip().lower()
        if value not in {"mainnet", "testnet"}:
            raise MarketConfigurationError("Hyperliquid network must be 'mainnet' or 'testnet'.")
        return value

    def health_check(self) -> Dict[str, Any]:
        health = super().health_check()
        health.update(
            {
                "api_base_url": self.api_base_url,
                "network": self.network,
                "references": list(HYPERLIQUID_REFERENCES),
                "public_api": True,
                "live_trading_supported": True,
                "live_trading_enabled": self.config_bool("live_trading_enabled", False),
                "external_signature_required": True,
                "activity_feed_supported": True,
                "copy_trading_supported": bool(self.capabilities.copy_trading),
            }
        )
        return health

    def list_events(self, query: str = "", limit: int = 50) -> List[MarketEvent]:
        self.ensure_capability("event_listing")
        desired = max(1, min(int(limit or 50), 1000))
        payload = self._info({"type": "outcomeMeta"})
        outcomes = payload.get("outcomes") if isinstance(payload, Mapping) else None
        questions = payload.get("questions") if isinstance(payload, Mapping) else None
        if not isinstance(outcomes, list):
            raise MarketConfigurationError("Hyperliquid outcomeMeta returned no outcomes array.")
        self._outcome_cache = {}
        self._question_cache = {}
        rows: List[Tuple[str, Mapping[str, Any]]] = []
        for row in outcomes:
            if isinstance(row, Mapping):
                outcome_id = self._outcome_id(row.get("outcome"))
                self._outcome_cache[outcome_id] = dict(row)
                rows.append((f"outcome:{outcome_id}", row))
        if isinstance(questions, list):
            for row in questions:
                if isinstance(row, Mapping) and row.get("question") is not None:
                    question_id = self._outcome_id(row.get("question"))
                    self._question_cache[question_id] = dict(row)
                    rows.append((f"question:{question_id}", row))

        needle = str(query or "").strip().lower()
        events: List[MarketEvent] = []
        for event_id, row in rows:
            text = self._search_text(row)
            if needle and needle not in text:
                continue
            events.append(self._event_from_row(event_id, row))
            if len(events) >= desired:
                break
        return events

    def list_contracts(self, event_id: str) -> List[MarketContract]:
        self.ensure_capability("event_listing")
        kind, item_id = self._split_event_id(event_id)
        if kind == "outcome":
            row = self._outcome_cache.get(item_id)
            if not row:
                row = self._load_outcomes().get(item_id, {})
            if not row:
                raise MarketConfigurationError(f"Hyperliquid outcome {item_id!r} was not found.")
            side_specs = row.get("sideSpecs")
            if not isinstance(side_specs, list) or not side_specs:
                raise MarketConfigurationError(f"Hyperliquid outcome {item_id!r} did not return side specifications.")
            return [self._contract_from_side(item_id, index, side, row) for index, side in enumerate(side_specs[:2])]

        question = self._question_cache.get(item_id)
        if not question:
            self._load_outcomes()
            question = self._question_cache.get(item_id, {})
        if not question:
            raise MarketConfigurationError(f"Hyperliquid question {item_id!r} was not found.")
        named = question.get("namedOutcomes")
        if not isinstance(named, list):
            named = []
        contracts: List[MarketContract] = []
        for index, outcome_id in enumerate(named):
            outcome_key = self._outcome_id(outcome_id)
            contracts.append(
                MarketContract(
                    market_id=self.market_id,
                    contract_id=f"question:{item_id}:{outcome_key}",
                    event_id=f"question:{item_id}",
                    title=f"{self._title(question, item_id)} - {outcome_key}",
                    outcome=outcome_key,
                    url=f"{self.api_base_url}/trade",
                    status="open",
                    raw={"question": dict(question), "outcome_id": outcome_key, "outcome_index": index},
                )
            )
        return contracts

    def get_price(self, contract_id: str) -> PriceSnapshot:
        self.ensure_capability("price_reading")
        outcome_id, side = self._split_contract_id(contract_id)
        book = self.get_orderbook(contract_id)
        bid = book.bids[0].price if book.bids else None
        ask = book.asks[0].price if book.asks else None
        midpoint = (bid + ask) / 2.0 if bid is not None and ask is not None else bid or ask
        if midpoint is None:
            mids = self._info({"type": "allMids"})
            coin = self._coin(outcome_id, side)
            midpoint = self._probability(mids.get(coin) if isinstance(mids, Mapping) else None)
        if midpoint is None:
            raise MarketConfigurationError(f"Hyperliquid outcome {outcome_id}:{side} has no available price.")
        return PriceSnapshot(
            market_id=self.market_id,
            contract_id=self._contract_id(outcome_id, side),
            last=midpoint,
            bid=bid,
            ask=ask,
            midpoint=midpoint,
            source="hyperliquid_hip4_l2book",
            raw={"orderbook": book.raw, "outcome": outcome_id, "side": side},
        )

    def get_orderbook(self, contract_id: str) -> OrderBookSnapshot:
        self.ensure_capability("orderbook_reading")
        outcome_id, side = self._split_contract_id(contract_id)
        payload = self._info({"type": "l2Book", "coin": self._coin(outcome_id, side)})
        levels = payload.get("levels") if isinstance(payload, Mapping) else None
        if not isinstance(levels, list) or len(levels) < 2:
            raise MarketConfigurationError("Hyperliquid l2Book returned an invalid levels payload.")
        bids = self._levels(levels[0])
        asks = self._levels(levels[1])
        return OrderBookSnapshot(
            market_id=self.market_id,
            contract_id=self._contract_id(outcome_id, side),
            bids=bids,
            asks=asks,
            raw=dict(payload) if isinstance(payload, Mapping) else {"payload": payload},
        )

    def list_activity(self, wallet_address: str, *, limit: int = 25) -> List[Dict[str, Any]]:
        """Return normalized public HIP-4 fills for a wallet.

        Hyperliquid's documented ``userFills`` response contains both perpetual
        and spot fills. HIP-4 outcome assets are the synthetic ``#<encoding>``
        coins, where the encoding is ``10 * outcome_id + side``. Only those
        rows are exposed to the copy workflow so ordinary perp/spot activity
        cannot be misinterpreted as a prediction-market order.
        """

        self.ensure_capability("copy_trading")
        wallet = require_activity_identity(self.market_id, wallet_address)
        desired = max(1, min(int(limit or 25), 100))
        payload = self._info({"type": "userFills", "user": wallet, "aggregateByTime": True})
        if not isinstance(payload, list):
            raise MarketConfigurationError("Hyperliquid userFills returned an invalid payload.")

        activities: List[Dict[str, Any]] = []
        for fill in payload:
            if not isinstance(fill, Mapping):
                continue
            try:
                activity = self._activity_from_fill(wallet, fill)
            except MarketConfigurationError:
                continue
            activities.append(activity)
            if len(activities) >= desired:
                break
        return activities

    def copy_trade_from_activity(self, activity: Mapping[str, Any]) -> PaperOrderResult:
        """Build a simulation-first paper order from a normalized HIP-4 fill."""

        self.ensure_capability("copy_trading")
        contract_id = str(activity.get("asset") or activity.get("contract_id") or "").strip()
        if not contract_id:
            raise MarketConfigurationError("Hyperliquid activity has no contract id.")
        outcome_id, side_index = self._split_contract_id(contract_id)
        canonical_contract = self._contract_id(outcome_id, side_index)
        order_side = str(activity.get("side") or "").strip().upper()
        if order_side not in self.live_order_sides:
            raise MarketConfigurationError("Hyperliquid activity side must be BUY or SELL.")
        size = self._required_positive_number(activity.get("size"), "Hyperliquid activity size")
        raw_price = activity.get("price")
        limit_price = None if raw_price in (None, "") else self._probability(raw_price)
        if raw_price not in (None, "") and limit_price is None:
            raise MarketConfigurationError("Hyperliquid activity price must be between 0 and 1.")
        return self.place_paper_order(
            PaperOrderRequest(
                market_id=self.market_id,
                contract_id=canonical_contract,
                side=order_side,
                size=size,
                limit_price=limit_price,
                metadata={"activity": dict(activity), "source": "hyperliquid_user_fills"},
            )
        )

    def place_paper_order(self, order: PaperOrderRequest) -> PaperOrderResult:
        self.ensure_capability("paper_trading")
        outcome_id, side = self._validate_order(order)
        price = self._probability(order.limit_price)
        if price is None:
            price = self.get_price(self._contract_id(outcome_id, side)).last
        action = self._order_action(outcome_id, side, order.side, order.size, price)
        return PaperOrderResult(
            market_id=self.market_id,
            contract_id=self._contract_id(outcome_id, side),
            accepted=True,
            message=(
                f"DRY RUN: would place Hyperliquid {str(order.side).upper()} for {float(order.size):.6f} outcome units"
                + (f" at {float(price):.6f}" if price is not None else "")
            ),
            filled_size=0.0,
            average_price=price,
            raw={"dry_run": True, "action": action},
        )

    def place_live_order(self, order: PaperOrderRequest) -> Dict[str, Any]:
        outcome_id, side = self._validate_order(order)
        audit = self.preflight_live_order(order, feature_name="Hyperliquid signed order submission")
        signed = order.metadata.get("signed_action")
        if not isinstance(signed, Mapping):
            raise MarketConfigurationError(
                "Hyperliquid live orders require metadata.signed_action containing the complete externally signed exchange payload."
            )
        payload = dict(signed)
        self._validate_signed_payload(payload, outcome_id, side, order)
        response = self.runtime.request_json(
            "POST",
            f"{self.api_base_url}/exchange",
            json_body=payload,
            headers={"Content-Type": "application/json"},
        )
        return {
            "live": True,
            "market_id": self.market_id,
            "contract_id": self._contract_id(outcome_id, side),
            "audit": audit,
            "response": response,
        }

    def _info(self, body: Mapping[str, Any]) -> Any:
        return self.runtime.request_json(
            "POST",
            f"{self.api_base_url}/info",
            json_body=dict(body),
            headers={"Content-Type": "application/json"},
        )

    def _load_outcomes(self) -> Dict[str, Dict[str, Any]]:
        payload = self._info({"type": "outcomeMeta"})
        rows = payload.get("outcomes") if isinstance(payload, Mapping) else None
        if not isinstance(rows, list):
            raise MarketConfigurationError("Hyperliquid outcomeMeta returned no outcomes array.")
        self._outcome_cache = {}
        self._question_cache = {}
        for row in rows:
            if isinstance(row, Mapping):
                self._outcome_cache[self._outcome_id(row.get("outcome"))] = dict(row)
        questions = payload.get("questions") if isinstance(payload, Mapping) else None
        if isinstance(questions, list):
            for row in questions:
                if isinstance(row, Mapping) and row.get("question") is not None:
                    self._question_cache[self._outcome_id(row.get("question"))] = dict(row)
        return self._outcome_cache

    def _event_from_row(self, event_id: str, row: Mapping[str, Any]) -> MarketEvent:
        item_id = event_id.split(":", 1)[1]
        return MarketEvent(
            market_id=self.market_id,
            event_id=event_id,
            title=self._title(row, item_id),
            url=f"{self.api_base_url}/trade",
            status="open",
            raw=dict(row),
        )

    def _contract_from_side(self, outcome_id: str, side: int, spec: Any, row: Mapping[str, Any]) -> MarketContract:
        name = str(spec.get("name") if isinstance(spec, Mapping) else spec or ("Yes" if side == 0 else "No"))
        return MarketContract(
            market_id=self.market_id,
            contract_id=self._contract_id(outcome_id, side),
            event_id=f"outcome:{outcome_id}",
            title=f"{self._title(row, outcome_id)} - {name}",
            outcome=name,
            url=f"{self.api_base_url}/trade",
            status="open",
            raw={"outcome": dict(row), "side": side, "coin": self._coin(outcome_id, side)},
        )

    def _validate_order(self, order: PaperOrderRequest) -> Tuple[str, int]:
        self.ensure_order_market(order)
        outcome_id, side = self._split_contract_id(order.contract_id)
        if str(order.side or "").upper() not in self.live_order_sides:
            raise MarketConfigurationError("Hyperliquid order side must be BUY or SELL.")
        try:
            size = float(order.size)
        except (TypeError, ValueError) as exc:
            raise MarketConfigurationError("Hyperliquid order size must be numeric.") from exc
        if not math.isfinite(size) or size <= 0:
            raise MarketConfigurationError("Hyperliquid order size must be positive and finite.")
        if order.limit_price is not None and self._probability(order.limit_price) is None:
            raise MarketConfigurationError("Hyperliquid order limit price must be between 0 and 1.")
        return outcome_id, side

    def _activity_from_fill(self, wallet: str, fill: Mapping[str, Any]) -> Dict[str, Any]:
        coin = str(fill.get("coin") or "").strip()
        match = re.fullmatch(r"#([0-9]+)", coin)
        if not match:
            raise MarketConfigurationError("Hyperliquid fill is not a HIP-4 outcome asset.")
        encoding = int(match.group(1))
        outcome_id, side_index = divmod(encoding, 10)
        if side_index not in {0, 1}:
            raise MarketConfigurationError("Hyperliquid HIP-4 fill has an unknown outcome side.")

        raw_side = str(fill.get("side") or "").strip().upper()
        if raw_side in {"B", "BUY"}:
            order_side = "BUY"
        elif raw_side in {"A", "S", "SELL"}:
            order_side = "SELL"
        else:
            raise MarketConfigurationError("Hyperliquid fill has an unknown trade side.")

        size = self._required_positive_number(fill.get("sz"), "Hyperliquid fill size")
        price = self._probability(fill.get("px"))
        fill_id = str(fill.get("hash") or fill.get("tid") or fill.get("oid") or "").strip()
        if not fill_id:
            raise MarketConfigurationError("Hyperliquid fill omitted a stable identifier.")
        timestamp = self._timestamp_seconds(fill.get("time") or fill.get("timestamp"))
        contract_id = self._contract_id(str(outcome_id), side_index)
        outcome = "YES" if side_index == 0 else "NO"
        return {
            "type": "TRADE",
            "proxyWallet": wallet,
            "wallet": wallet,
            "asset": contract_id,
            "contract_id": contract_id,
            "marketId": str(outcome_id),
            "side": order_side,
            "size": size,
            "shares": size,
            "price": price,
            "timestamp": timestamp,
            "transactionHash": f"hyperliquid-fill:{fill_id}",
            "activity_id": fill_id,
            "slug": f"outcome:{outcome_id}",
            "outcome": outcome,
            "raw": dict(fill),
        }

    def _validate_signed_payload(
        self, payload: Mapping[str, Any], outcome_id: str, side: int, order: PaperOrderRequest
    ) -> None:
        action = payload.get("action")
        if not isinstance(action, Mapping) or action.get("type") != "order":
            raise MarketConfigurationError("Hyperliquid signed_action.action.type must be 'order'.")
        orders = action.get("orders")
        if not isinstance(orders, list) or not orders or not isinstance(orders[0], Mapping):
            raise MarketConfigurationError("Hyperliquid signed_action.action.orders must contain an order.")
        wire = orders[0]
        expected_asset = self._asset_id(outcome_id, side)
        if int(wire.get("a", -1)) != expected_asset:
            raise MarketConfigurationError("Hyperliquid signed order asset does not match the selected outcome side.")
        expected_buy = str(order.side).upper() == "BUY"
        if bool(wire.get("b")) != expected_buy:
            raise MarketConfigurationError("Hyperliquid signed order side does not match the selected order side.")
        try:
            signed_size = float(wire.get("s"))
        except (TypeError, ValueError) as exc:
            raise MarketConfigurationError("Hyperliquid signed order size must be numeric.") from exc
        if not math.isclose(signed_size, float(order.size), rel_tol=0.0, abs_tol=1e-9):
            raise MarketConfigurationError("Hyperliquid signed order size does not match the requested size.")
        if not payload.get("signature") or payload.get("nonce") in (None, ""):
            raise MarketConfigurationError("Hyperliquid signed_action must include a signature and nonce.")

    @classmethod
    def _order_action(cls, outcome_id: str, side: int, order_side: Any, size: Any, price: Optional[float]) -> Dict[str, Any]:
        if price is None:
            raise MarketConfigurationError("Hyperliquid paper orders require a price when no quote is available.")
        return {
            "type": "order",
            "orders": [
                {
                    "a": cls._asset_id(outcome_id, side),
                    "b": str(order_side).upper() == "BUY",
                    "p": f"{float(price):.8f}",
                    "s": f"{float(size):.8f}",
                    "r": False,
                    "t": {"limit": {"tif": "Gtc"}},
                }
            ],
            "grouping": "na",
        }

    @staticmethod
    def _levels(rows: Any) -> List[OrderBookLevel]:
        if not isinstance(rows, list):
            return []
        levels: List[OrderBookLevel] = []
        for row in rows[:20]:
            if not isinstance(row, Mapping):
                continue
            try:
                price = float(row.get("px"))
                size = float(row.get("sz"))
            except (TypeError, ValueError):
                continue
            if math.isfinite(price) and price > 0 and math.isfinite(size) and size > 0:
                levels.append(OrderBookLevel(price=price, size=size))
        return levels

    @staticmethod
    def _title(row: Mapping[str, Any], item_id: str) -> str:
        name = str(row.get("name") or row.get("marketName") or item_id).strip()
        description = str(row.get("description") or "").strip()
        specs = HyperliquidAdapter._description_specs(description)
        details = " / ".join(str(specs[key]) for key in ("underlying", "targetPrice", "expiry") if key in specs)
        return f"{name} ({details})" if details else name

    @staticmethod
    def _description_specs(value: str) -> Dict[str, str]:
        result: Dict[str, str] = {}
        for part in str(value or "").split("|"):
            key, separator, raw = part.partition(":")
            if separator and key and raw:
                result[key.strip()] = raw.strip()
        return result

    @staticmethod
    def _search_text(row: Mapping[str, Any]) -> str:
        return " ".join(str(row.get(key) or "") for key in ("name", "description", "marketName", "underlying")).lower()

    @staticmethod
    def _outcome_id(value: Any) -> str:
        text = str(value or "").strip()
        if not OUTCOME_ID_RE.fullmatch(text):
            raise MarketConfigurationError("Hyperliquid outcome IDs must be non-negative decimal integers.")
        return text

    @classmethod
    def _split_event_id(cls, event_id: Any) -> Tuple[str, str]:
        text = str(event_id or "").strip()
        parts = text.split(":", 1)
        if len(parts) != 2 or parts[0] not in {"outcome", "question"}:
            raise MarketConfigurationError("Hyperliquid event id must be 'outcome:<id>' or 'question:<id>'.")
        return parts[0], cls._outcome_id(parts[1])

    @classmethod
    def _split_contract_id(cls, contract_id: Any) -> Tuple[str, int]:
        text = str(contract_id or "").strip()
        parts = text.split(":")
        if len(parts) != 3 or parts[0] != "outcome" or not parts[2].isdigit() or int(parts[2]) not in {0, 1}:
            raise MarketConfigurationError("Hyperliquid contract id must be 'outcome:<id>:0' or ':1'.")
        return cls._outcome_id(parts[1]), int(parts[2])

    @staticmethod
    def _encoding(outcome_id: str, side: int) -> int:
        return 10 * int(outcome_id) + int(side)

    @classmethod
    def _coin(cls, outcome_id: str, side: int) -> str:
        return f"#{cls._encoding(outcome_id, side)}"

    @classmethod
    def _asset_id(cls, outcome_id: str, side: int) -> int:
        return 100_000_000 + cls._encoding(outcome_id, side)

    @staticmethod
    def _contract_id(outcome_id: str, side: int) -> str:
        return f"outcome:{outcome_id}:{int(side)}"

    @staticmethod
    def _probability(value: Any) -> Optional[float]:
        if value in (None, ""):
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) and 0 < number < 1 else None

    @staticmethod
    def _required_positive_number(value: Any, label: str) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise MarketConfigurationError(f"{label} must be numeric.") from exc
        if not math.isfinite(number) or number <= 0:
            raise MarketConfigurationError(f"{label} must be positive and finite.")
        return number

    @staticmethod
    def _timestamp_seconds(value: Any) -> int:
        try:
            timestamp = int(float(value or 0))
        except (TypeError, ValueError):
            return 0
        return timestamp // 1000 if timestamp > 10_000_000_000 else timestamp
