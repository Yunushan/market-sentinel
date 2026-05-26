from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping, Optional, Tuple

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


DEFAULT_MANIFOLD_BASE_URL = "https://api.manifold.markets/v0"


class ManifoldAdapter(MarketAdapter):
    """Manifold adapter using the documented public REST API."""

    metadata = get_market_metadata("manifold")

    def health_check(self) -> Dict[str, Any]:
        health = super().health_check()
        credential = self.resolve_credential("manifold_api_key", ("MANIFOLD_API_KEY",), label="MANIFOLD_API_KEY")
        health.update(
            {
                "api_base_url": self.api_base_url,
                "live_trading_enabled": self.config_bool("live_trading_enabled", False),
                "credential_sources": (
                    [{"name": credential.name, "source": credential.source}] if credential else []
                ),
                "orderbook_supported": False,
            }
        )
        return health

    @property
    def api_base_url(self) -> str:
        configured = self.config.get("manifold_api_base_url") or self.config.get("api_base_url")
        return str(configured or DEFAULT_MANIFOLD_BASE_URL).rstrip("/")

    def list_events(self, query: str = "", limit: int = 50) -> List[MarketEvent]:
        self.ensure_capability("event_listing")
        desired = max(1, min(int(limit or 50), 1000))
        params = {
            "term": str(query or ""),
            "sort": str(self.config.get("manifold_sort") or "most-popular"),
            "filter": str(self.config.get("manifold_market_filter") or "open"),
            "contractType": str(self.config.get("manifold_contract_type") or "ALL"),
            "limit": desired,
        }
        markets = self._as_market_list(self._get("/search-markets", params=params))
        return [self._event_from_market(market) for market in markets[:desired]]

    def list_contracts(self, event_id: str) -> List[MarketContract]:
        self.ensure_capability("event_listing")
        market = self._get_market(str(event_id or "").strip())
        if not market:
            return []
        return self._contracts_from_market(market)

    def get_price(self, contract_id: str) -> PriceSnapshot:
        self.ensure_capability("price_reading")
        market_id, outcome, answer_id = self._split_contract_id(contract_id)
        data = self._get(f"/market/{market_id}/prob")
        price = self._price_from_probability_payload(data, outcome, answer_id)
        return PriceSnapshot(
            market_id=self.market_id,
            contract_id=self._contract_id(market_id, outcome, answer_id),
            last=price,
            midpoint=price,
            source="manifold_probability",
            raw=data if isinstance(data, dict) else {},
        )

    def get_orderbook(self, contract_id: str):
        self.ensure_capability("orderbook_reading")
        raise UnsupportedFeatureError(
            self.market_id,
            "orderbook_reading",
            "Manifold exposes documented probabilities and bet history, not a CLOB orderbook endpoint.",
        )

    def place_paper_order(self, order: PaperOrderRequest) -> PaperOrderResult:
        self.ensure_capability("paper_trading")
        self._validate_order(order)
        payload, endpoint = self._build_order_payload(order, dry_run=True)
        return PaperOrderResult(
            market_id=self.market_id,
            contract_id=self._canonical_contract_id(order.contract_id),
            accepted=True,
            message=(
                f"DRY RUN: would place Manifold {order.side.upper()} "
                f"for {order.size:.4f} MANA-equivalent"
                + (f" at limit {order.limit_price:.2f}" if order.limit_price is not None else "")
            ),
            filled_size=0.0,
            average_price=None,
            raw={"endpoint": endpoint, "request": payload},
        )

    def place_live_order(self, order: PaperOrderRequest) -> Dict[str, Any]:
        self.ensure_capability("live_trading")
        self._validate_order(order)
        preflight = self.preflight_live_order(order)
        payload, endpoint = self._build_order_payload(order, dry_run=False)
        if order.side.upper() == "BUY" and self._split_contract_id(order.contract_id)[2]:
            raise MarketConfigurationError(
                "Manifold live BUY for one multiple-choice answer is not implemented because the documented "
                "/v0/multi-bet endpoint requires multiple answer IDs."
            )
        headers = self._auth_headers()
        response = self.runtime.request_json(
            "POST",
            self._url(endpoint),
            json_body=payload,
            headers=headers,
        )
        return {
            "market_id": self.market_id,
            "contract_id": self._canonical_contract_id(order.contract_id),
            "live": True,
            "endpoint": endpoint,
            "preflight": preflight,
            "request": payload,
            "response": response,
        }

    def _get_market(self, ref: str) -> Optional[Mapping[str, Any]]:
        if not ref:
            return None
        market_id = self._split_contract_id(ref)[0] if ":" in ref else ref
        try:
            data = self._get(f"/market/{market_id}")
        except MarketHTTPError:
            data = self._get(f"/slug/{market_id}")
        return data if isinstance(data, Mapping) else None

    def _get(self, path: str, *, params: Optional[Mapping[str, Any]] = None) -> Any:
        return self.runtime.get_json(self._url(path), params=params)

    def _url(self, path: str) -> str:
        clean_path = "/" + str(path or "").strip("/")
        return f"{self.api_base_url}{clean_path}"

    def _auth_headers(self) -> Dict[str, str]:
        credential = self.resolve_credential(
            "manifold_api_key",
            ("MANIFOLD_API_KEY",),
            required=True,
            label="MANIFOLD_API_KEY",
        )
        return {"Authorization": f"Key {credential.value}", "Content-Type": "application/json"}

    def _event_from_market(self, market: Mapping[str, Any]) -> MarketEvent:
        event_id = str(market.get("id") or "").strip()
        return MarketEvent(
            market_id=self.market_id,
            event_id=event_id,
            title=str(market.get("question") or event_id),
            url=str(market.get("url") or ""),
            status=self._status_from_market(market),
            raw=dict(market),
        )

    def _contracts_from_market(self, market: Mapping[str, Any]) -> List[MarketContract]:
        market_id = str(market.get("id") or "").strip()
        if not market_id:
            return []
        outcome_type = str(market.get("outcomeType") or "").upper()
        question = str(market.get("question") or market_id)
        status = self._status_from_market(market)
        if outcome_type == "BINARY":
            return [
                MarketContract(
                    market_id=self.market_id,
                    contract_id=self._contract_id(market_id, "YES"),
                    event_id=market_id,
                    title=f"{question} - Yes",
                    outcome="Yes",
                    url=str(market.get("url") or ""),
                    status=status,
                    raw={"market": dict(market), "outcome": "YES"},
                ),
                MarketContract(
                    market_id=self.market_id,
                    contract_id=self._contract_id(market_id, "NO"),
                    event_id=market_id,
                    title=f"{question} - No",
                    outcome="No",
                    url=str(market.get("url") or ""),
                    status=status,
                    raw={"market": dict(market), "outcome": "NO"},
                ),
            ]

        answers = market.get("answers") or []
        contracts: List[MarketContract] = []
        if isinstance(answers, list):
            for answer in answers:
                if not isinstance(answer, Mapping):
                    continue
                answer_id = str(answer.get("id") or "").strip()
                if not answer_id:
                    continue
                answer_text = str(answer.get("text") or answer.get("name") or answer_id)
                contracts.append(
                    MarketContract(
                        market_id=self.market_id,
                        contract_id=self._contract_id(market_id, "ANSWER", answer_id),
                        event_id=market_id,
                        title=f"{question} - {answer_text}",
                        outcome=answer_text,
                        url=str(market.get("url") or ""),
                        status=status,
                        raw={"market": dict(market), "answer": dict(answer)},
                    )
                )
        return contracts

    def _build_order_payload(self, order: PaperOrderRequest, *, dry_run: bool) -> Tuple[Dict[str, Any], str]:
        market_id, outcome, answer_id = self._split_contract_id(order.contract_id)
        side = str(order.side or "").upper()
        if side == "SELL":
            payload: Dict[str, Any] = {"shares": float(order.metadata.get("shares", order.size))}
            if outcome in {"YES", "NO"}:
                payload["outcome"] = outcome
            if answer_id:
                payload["answerId"] = answer_id
                payload.setdefault("outcome", "YES")
            return payload, f"/market/{market_id}/sell"

        payload = {
            "amount": float(order.size),
            "contractId": market_id,
            "outcome": "YES" if answer_id else outcome,
            "dryRun": dry_run,
        }
        if answer_id:
            payload["answerId"] = answer_id
        if order.limit_price is not None:
            payload["limitProb"] = self._limit_probability(order.limit_price)
        for key in ("expiresAt", "expiresMillisAfter"):
            if key in order.metadata:
                payload[key] = order.metadata[key]
        return payload, "/bet"

    def _validate_order(self, order: PaperOrderRequest) -> None:
        self.ensure_order_market(order)
        self._split_contract_id(order.contract_id)
        side = str(order.side or "").upper()
        if side not in {"BUY", "SELL"}:
            raise MarketConfigurationError("Manifold order side must be BUY or SELL.")
        if not self._is_positive_number(order.size):
            raise MarketConfigurationError("Manifold order size must be positive.")
        if order.limit_price is not None:
            self._limit_probability(order.limit_price)

    @staticmethod
    def _as_market_list(data: Any) -> List[Mapping[str, Any]]:
        if isinstance(data, list):
            return [item for item in data if isinstance(item, Mapping)]
        if isinstance(data, Mapping):
            markets = data.get("markets") or data.get("results") or data.get("contracts") or []
            if isinstance(markets, list):
                return [item for item in markets if isinstance(item, Mapping)]
        return []

    @staticmethod
    def _status_from_market(market: Mapping[str, Any]) -> str:
        if market.get("isResolved") is True:
            return "resolved"
        close_time = market.get("closeTime")
        if close_time is not None:
            try:
                if float(close_time) <= 0:
                    return "open"
            except (TypeError, ValueError):
                pass
        return "open"

    @staticmethod
    def _price_from_probability_payload(data: Any, outcome: str, answer_id: Optional[str]) -> float:
        if not isinstance(data, Mapping):
            raise MarketConfigurationError("Manifold probability response was not an object.")
        if answer_id:
            answer_probs = data.get("answerProbs")
            if not isinstance(answer_probs, Mapping) or answer_id not in answer_probs:
                raise MarketConfigurationError(f"Manifold probability response did not include answer {answer_id}.")
            probability = ManifoldAdapter._safe_probability(answer_probs.get(answer_id))
        else:
            probability = ManifoldAdapter._safe_probability(data.get("prob"))
            if outcome == "NO" and probability is not None:
                probability = 1.0 - probability
        if probability is None:
            raise MarketConfigurationError("Manifold probability must be between 0 and 1.")
        return probability

    @staticmethod
    def _split_contract_id(contract_id: str) -> Tuple[str, str, Optional[str]]:
        raw = str(contract_id or "").strip()
        if not raw:
            raise MarketConfigurationError("Manifold order requires a contract id.")
        parts = raw.split(":")
        market_id = parts[0].strip()
        if not market_id:
            raise MarketConfigurationError("Manifold order requires a market id.")
        if len(parts) == 1:
            return market_id, "YES", None
        outcome = parts[1].strip().upper()
        if outcome == "ANSWER":
            if len(parts) < 3 or not parts[2].strip():
                raise MarketConfigurationError("Manifold answer contract requires an answer id.")
            return market_id, "ANSWER", parts[2].strip()
        if outcome not in {"YES", "NO"}:
            raise MarketConfigurationError("Manifold binary contract outcome must be YES or NO.")
        return market_id, outcome, None

    @staticmethod
    def _contract_id(market_id: str, outcome: str, answer_id: Optional[str] = None) -> str:
        if outcome.upper() == "ANSWER":
            return f"{market_id}:ANSWER:{answer_id}"
        return f"{market_id}:{outcome.upper()}"

    @staticmethod
    def _canonical_contract_id(contract_id: str) -> str:
        return ManifoldAdapter._contract_id(*ManifoldAdapter._split_contract_id(contract_id))

    @staticmethod
    def _safe_probability(value: Any) -> Optional[float]:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(number) or number < 0.0 or number > 1.0:
            return None
        return number

    @staticmethod
    def _limit_probability(value: Any) -> float:
        probability = ManifoldAdapter._safe_probability(value)
        if probability is None or probability < 0.01 or probability > 0.99:
            raise MarketConfigurationError("Manifold limit price must be between 0.01 and 0.99.")
        rounded = round(probability, 2)
        if abs(probability - rounded) > 1e-9:
            raise MarketConfigurationError("Manifold limit price must use whole percentage points, e.g. 0.42.")
        return rounded

    @staticmethod
    def _is_positive_number(value: Any) -> bool:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return False
        return math.isfinite(number) and number > 0
