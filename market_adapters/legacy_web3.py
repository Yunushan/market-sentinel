from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .base import MarketAdapter
from .catalog import get_market_metadata
from .errors import MarketConfigurationError, MarketHTTPError, UnsupportedFeatureError
from .types import MarketContract, MarketEvent, PaperOrderRequest, PaperOrderResult, PriceSnapshot


DEFAULT_ZEITGEIST_INDEXER_URL = "https://processor.bsr.zeitgeist.pm/graphql"

AUGUR_REFERENCES = (
    "https://github.com/AugurProject/augur",
    "https://github.com/protofire/augur-v2-subgraph",
)
OMEN_REFERENCES = (
    "https://github.com/protofire/omen-exchange",
    "https://github.com/protofire/omen-subgraph",
    "https://omendotag.gitbook.io/omen",
)
ZEITGEIST_REFERENCES = (
    "https://docs.zeitgeist.pm/docs/build/sdk/v2/fetch-markets",
    "https://docs.zeitgeist.pm/docs/build/sdk/v2/indexer",
    "https://docs.zeitgeist.pm/docs/build/sdk/v2/calculating-current-prediction",
)


class _GraphQLAdapter(MarketAdapter):
    graphql_config_key = ""
    graphql_env_vars: Sequence[str] = ()
    default_graphql_url = ""

    @property
    def graphql_url(self) -> str:
        url, _source = self._graphql_url_with_source(required=True)
        return url

    def _graphql_url_with_source(self, *, required: bool = False) -> Tuple[str, str]:
        credential = self.resolve_credential(
            self.graphql_config_key,
            self.graphql_env_vars,
            required=False,
            label=self.graphql_config_key.upper(),
        )
        if credential and credential.value.strip():
            return credential.value.strip().rstrip("/"), credential.source
        if self.default_graphql_url:
            return self.default_graphql_url.rstrip("/"), "default"
        if required:
            names = ", ".join([self.graphql_config_key, *self.graphql_env_vars])
            raise MarketConfigurationError(f"{self.display_name} requires a configured GraphQL endpoint: {names}.")
        return "", "missing"

    def _graphql(self, query: str, variables: Optional[Mapping[str, Any]] = None) -> Mapping[str, Any]:
        payload = self.runtime.request_json(
            "POST",
            self.graphql_url,
            json_body={"query": query, "variables": dict(variables or {})},
            headers={"Content-Type": "application/json"},
        )
        if not isinstance(payload, Mapping):
            raise MarketHTTPError(f"{self.market_id} GraphQL response was not a JSON object.")
        errors = payload.get("errors")
        if errors:
            raise MarketHTTPError(f"{self.market_id} GraphQL errors: {errors}")
        data = payload.get("data")
        if not isinstance(data, Mapping):
            raise MarketHTTPError(f"{self.market_id} GraphQL response did not include data.")
        return data

    @staticmethod
    def _is_positive_number(value: Any) -> bool:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return False
        return math.isfinite(number) and number > 0

    @staticmethod
    def _probability(value: Any, *, allow_zero: bool = True) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise MarketConfigurationError("Probability must be numeric.")
        if not math.isfinite(number):
            raise MarketConfigurationError("Probability must be finite.")
        if number > 1.0 and number <= 100.0:
            number = number / 100.0
        lower_ok = number >= 0.0 if allow_zero else number > 0.0
        if not lower_ok or number > 1.0:
            raise MarketConfigurationError("Probability must be between 0 and 1.")
        return number

    @staticmethod
    def _optional_probability(value: Any) -> Optional[float]:
        if value in (None, ""):
            return None
        try:
            return _GraphQLAdapter._probability(value)
        except MarketConfigurationError:
            return None


class AugurAdapter(_GraphQLAdapter):
    """Read-only Augur v2 protocol adapter backed by the documented subgraph schema."""

    metadata = get_market_metadata("augur")
    graphql_config_key = "augur_subgraph_url"
    graphql_env_vars = ("AUGUR_SUBGRAPH_URL",)

    MARKETS_QUERY = """
    query AugurMarkets($first: Int!) {
      markets(first: $first, orderBy: timestamp, orderDirection: desc) {
        id
        description
        longDescription
        categories
        status
        marketType
        endTimestamp
        timestamp
        numOutcomes
        outcomes {
          id
          value
          payoutNumerator
          isFinalNumerator
        }
      }
    }
    """

    MARKET_QUERY = """
    query AugurMarket($id: ID!) {
      market(id: $id) {
        id
        description
        longDescription
        categories
        status
        marketType
        endTimestamp
        timestamp
        numOutcomes
        outcomes {
          id
          value
          payoutNumerator
          isFinalNumerator
        }
      }
    }
    """

    def health_check(self) -> Dict[str, Any]:
        health = super().health_check()
        url, source = self._graphql_url_with_source(required=False)
        health.update(
            {
                "graphql_url_configured": bool(url),
                "graphql_url_source": source,
                "references": list(AUGUR_REFERENCES),
                "price_reading_supported": False,
                "live_trading_enabled": False,
            }
        )
        return health

    def list_events(self, query: str = "", limit: int = 50) -> List[MarketEvent]:
        self.ensure_capability("event_listing")
        desired = max(1, min(int(limit or 50), 100))
        data = self._graphql(self.MARKETS_QUERY, {"first": desired})
        markets = self._markets_from_payload(data)
        q = str(query or "").strip().lower()
        if q:
            markets = [market for market in markets if self._market_matches_query(market, q)]
        return [self._event_from_market(market) for market in markets[:desired]]

    def list_contracts(self, event_id: str) -> List[MarketContract]:
        self.ensure_capability("event_listing")
        market_id = str(event_id or "").strip()
        if not market_id:
            return []
        market = self._fetch_market(market_id)
        return self._contracts_from_market(market)

    def get_price(self, contract_id: str) -> PriceSnapshot:
        raise UnsupportedFeatureError(
            self.market_id,
            "price_reading",
            "Augur v2 subgraph market entities expose lifecycle/outcome data, but not a maintained live price or orderbook feed.",
        )

    def get_orderbook(self, contract_id: str):
        raise UnsupportedFeatureError(
            self.market_id,
            "orderbook_reading",
            "Augur orderbook/trading data is not exposed through a maintained official Python-compatible API in this adapter.",
        )

    def place_paper_order(self, order: PaperOrderRequest):
        raise UnsupportedFeatureError(
            self.market_id,
            "paper_trading",
            "Augur paper trading is disabled because the implemented adapter is read-only market discovery/listing.",
        )

    def place_live_order(self, order: PaperOrderRequest):
        raise UnsupportedFeatureError(
            self.market_id,
            "live_trading",
            "Augur live trading requires explicit wallet-signed protocol transactions and is not implemented.",
        )

    def copy_trade_from_activity(self, activity: Mapping[str, Any]):
        raise UnsupportedFeatureError(
            self.market_id,
            "copy_trading",
            "Augur copy trading is unsupported because no maintained official account activity mirroring API is used.",
        )

    def _fetch_market(self, market_id: str) -> Mapping[str, Any]:
        data = self._graphql(self.MARKET_QUERY, {"id": market_id})
        market = data.get("market")
        if isinstance(market, Mapping):
            return market
        raise MarketConfigurationError(f"Augur market {market_id!r} was not found.")

    def _event_from_market(self, market: Mapping[str, Any]) -> MarketEvent:
        market_id = self._market_id(market)
        return MarketEvent(
            market_id=self.market_id,
            event_id=market_id,
            title=str(market.get("description") or market.get("longDescription") or market_id),
            url="https://augur.net",
            status=str(market.get("status") or "").strip().lower(),
            raw=dict(market),
        )

    def _contracts_from_market(self, market: Mapping[str, Any]) -> List[MarketContract]:
        market_id = self._market_id(market)
        title = str(market.get("description") or market_id)
        status = str(market.get("status") or "").strip().lower()
        contracts: List[MarketContract] = []
        for index, outcome in enumerate(self._outcomes(market)):
            outcome_id = str(outcome.get("id") or index)
            outcome_name = str(outcome.get("value") or f"Outcome {index}")
            contracts.append(
                MarketContract(
                    market_id=self.market_id,
                    contract_id=self._contract_id(market_id, outcome_id),
                    event_id=market_id,
                    title=f"{title} - {outcome_name}",
                    outcome=outcome_name,
                    url="https://augur.net",
                    status=status,
                    raw={"market": dict(market), "outcome": dict(outcome), "outcome_index": index},
                )
            )
        return contracts

    @staticmethod
    def _markets_from_payload(data: Mapping[str, Any]) -> List[Mapping[str, Any]]:
        markets = data.get("markets")
        return [market for market in markets if isinstance(market, Mapping)] if isinstance(markets, list) else []

    @staticmethod
    def _outcomes(market: Mapping[str, Any]) -> List[Mapping[str, Any]]:
        outcomes = market.get("outcomes")
        return [outcome for outcome in outcomes if isinstance(outcome, Mapping)] if isinstance(outcomes, list) else []

    @staticmethod
    def _market_matches_query(market: Mapping[str, Any], query: str) -> bool:
        values = [
            market.get("id"),
            market.get("description"),
            market.get("longDescription"),
            market.get("marketType"),
            " ".join(str(category) for category in market.get("categories") or []),
            " ".join(str(outcome.get("value") or "") for outcome in AugurAdapter._outcomes(market)),
        ]
        return query in " ".join(str(value or "") for value in values).lower()

    @staticmethod
    def _market_id(market: Mapping[str, Any]) -> str:
        return str(market.get("id") or "").strip()

    @staticmethod
    def _contract_id(market_id: str, outcome_id: str) -> str:
        return f"{market_id}:{outcome_id}"


class OmenAdapter(_GraphQLAdapter):
    """Omen AMM adapter using the documented FixedProductMarketMaker subgraph schema."""

    metadata = get_market_metadata("omen")
    graphql_config_key = "omen_subgraph_url"
    graphql_env_vars = ("OMEN_SUBGRAPH_URL",)

    FPMMS_QUERY = """
    query OmenMarkets($first: Int!) {
      fixedProductMarketMakers(first: $first, orderBy: creationTimestamp, orderDirection: desc) {
        id
        title
        category
        outcomes
        outcomeTokenMarginalPrices
        outcomeTokenAmounts
        outcomeSlotCount
        openingTimestamp
        resolutionTimestamp
        currentAnswer
        answerFinalizedTimestamp
        scaledLiquidityMeasure
        scaledRunningDailyVolume
        collateralToken
        curatedByDxDao
        question {
          id
          title
          category
          outcomes
          openingTimestamp
        }
        condition {
          id
          resolutionTimestamp
          payouts
        }
      }
    }
    """

    FPMM_QUERY = """
    query OmenMarket($id: ID!) {
      fixedProductMarketMaker(id: $id) {
        id
        title
        category
        outcomes
        outcomeTokenMarginalPrices
        outcomeTokenAmounts
        outcomeSlotCount
        openingTimestamp
        resolutionTimestamp
        currentAnswer
        answerFinalizedTimestamp
        scaledLiquidityMeasure
        scaledRunningDailyVolume
        collateralToken
        curatedByDxDao
        question {
          id
          title
          category
          outcomes
          openingTimestamp
        }
        condition {
          id
          resolutionTimestamp
          payouts
        }
      }
    }
    """

    def health_check(self) -> Dict[str, Any]:
        health = super().health_check()
        url, source = self._graphql_url_with_source(required=False)
        health.update(
            {
                "graphql_url_configured": bool(url),
                "graphql_url_source": source,
                "references": list(OMEN_REFERENCES),
                "orderbook_supported": False,
                "live_trading_enabled": False,
            }
        )
        return health

    def list_events(self, query: str = "", limit: int = 50) -> List[MarketEvent]:
        self.ensure_capability("event_listing")
        desired = max(1, min(int(limit or 50), 100))
        data = self._graphql(self.FPMMS_QUERY, {"first": desired})
        markets = self._fpmms_from_payload(data)
        q = str(query or "").strip().lower()
        if q:
            markets = [market for market in markets if self._market_matches_query(market, q)]
        return [self._event_from_fpmm(market) for market in markets[:desired]]

    def list_contracts(self, event_id: str) -> List[MarketContract]:
        self.ensure_capability("event_listing")
        fpmm = self._fetch_fpmm(event_id)
        return self._contracts_from_fpmm(fpmm)

    def get_price(self, contract_id: str) -> PriceSnapshot:
        self.ensure_capability("price_reading")
        fpmm_id, outcome_index = self._split_contract_id(contract_id)
        fpmm = self._fetch_fpmm(fpmm_id)
        prices = self._marginal_prices(fpmm)
        if outcome_index >= len(prices) or prices[outcome_index] is None:
            raise MarketConfigurationError(f"Omen price for {contract_id!r} was not available from the subgraph.")
        price = prices[outcome_index]
        return PriceSnapshot(
            market_id=self.market_id,
            contract_id=self._contract_id(fpmm_id, outcome_index),
            last=price,
            midpoint=price,
            source="omen_subgraph_outcomeTokenMarginalPrices",
            raw={"fpmm": dict(fpmm), "outcome_index": outcome_index},
        )

    def get_orderbook(self, contract_id: str):
        raise UnsupportedFeatureError(
            self.market_id,
            "orderbook_reading",
            "Omen uses FixedProductMarketMaker AMM pools and the documented subgraph exposes marginal prices, not CLOB depth.",
        )

    def place_paper_order(self, order: PaperOrderRequest) -> PaperOrderResult:
        self.ensure_capability("paper_trading")
        self._validate_order(order)
        fpmm_id, outcome_index = self._split_contract_id(order.contract_id)
        average = order.limit_price if order.limit_price is not None else self.get_price(order.contract_id).last
        return PaperOrderResult(
            market_id=self.market_id,
            contract_id=self._contract_id(fpmm_id, outcome_index),
            accepted=True,
            message=(
                f"DRY RUN: would record Omen AMM {order.side.upper()} "
                f"for {order.size:.4f} outcome shares"
                + (f" at max probability {float(average):.4f}" if average is not None else "")
            ),
            filled_size=0.0,
            average_price=average,
            raw={"fpmm": fpmm_id, "outcome_index": outcome_index, "dry_run": True},
        )

    def place_live_order(self, order: PaperOrderRequest):
        raise UnsupportedFeatureError(
            self.market_id,
            "live_trading",
            "Omen live trading requires explicit wallet-signed AMM transactions and is not implemented.",
        )

    def copy_trade_from_activity(self, activity: Mapping[str, Any]):
        raise UnsupportedFeatureError(
            self.market_id,
            "copy_trading",
            "Omen copy trading is unsupported because this adapter has no official account activity mirroring API.",
        )

    def _fetch_fpmm(self, fpmm_id: str) -> Mapping[str, Any]:
        market_id = str(fpmm_id or "").strip()
        if not market_id:
            raise MarketConfigurationError("Omen market id cannot be empty.")
        data = self._graphql(self.FPMM_QUERY, {"id": market_id})
        fpmm = data.get("fixedProductMarketMaker")
        if isinstance(fpmm, Mapping):
            return fpmm
        raise MarketConfigurationError(f"Omen market {market_id!r} was not found.")

    def _event_from_fpmm(self, fpmm: Mapping[str, Any]) -> MarketEvent:
        fpmm_id = self._fpmm_id(fpmm)
        return MarketEvent(
            market_id=self.market_id,
            event_id=fpmm_id,
            title=self._title(fpmm),
            url=f"https://omen.eth.limo/#/{fpmm_id}" if fpmm_id else "https://omen.eth.limo",
            status=self._status(fpmm),
            raw=dict(fpmm),
        )

    def _contracts_from_fpmm(self, fpmm: Mapping[str, Any]) -> List[MarketContract]:
        fpmm_id = self._fpmm_id(fpmm)
        title = self._title(fpmm)
        prices = self._marginal_prices(fpmm)
        contracts: List[MarketContract] = []
        for index, outcome in enumerate(self._outcome_names(fpmm)):
            contracts.append(
                MarketContract(
                    market_id=self.market_id,
                    contract_id=self._contract_id(fpmm_id, index),
                    event_id=fpmm_id,
                    title=f"{title} - {outcome}",
                    outcome=outcome,
                    url=f"https://omen.eth.limo/#/{fpmm_id}" if fpmm_id else "https://omen.eth.limo",
                    status=self._status(fpmm),
                    raw={
                        "fpmm": dict(fpmm),
                        "outcome_index": index,
                        "marginal_price": prices[index] if index < len(prices) else None,
                    },
                )
            )
        return contracts

    def _validate_order(self, order: PaperOrderRequest) -> None:
        self.ensure_order_market(order)
        self._split_contract_id(order.contract_id)
        side = str(order.side or "").upper()
        if side not in {"BUY", "SELL"}:
            raise MarketConfigurationError("Omen paper order side must be BUY or SELL.")
        if not self._is_positive_number(order.size):
            raise MarketConfigurationError("Omen paper order size must be positive.")
        if order.limit_price is not None:
            self._probability(order.limit_price, allow_zero=False)

    @staticmethod
    def _fpmms_from_payload(data: Mapping[str, Any]) -> List[Mapping[str, Any]]:
        markets = data.get("fixedProductMarketMakers")
        return [market for market in markets if isinstance(market, Mapping)] if isinstance(markets, list) else []

    @staticmethod
    def _market_matches_query(fpmm: Mapping[str, Any], query: str) -> bool:
        values = [
            fpmm.get("id"),
            fpmm.get("title"),
            fpmm.get("category"),
            " ".join(OmenAdapter._outcome_names(fpmm)),
        ]
        question = fpmm.get("question")
        if isinstance(question, Mapping):
            values.extend([question.get("title"), question.get("category")])
        return query in " ".join(str(value or "") for value in values).lower()

    @staticmethod
    def _fpmm_id(fpmm: Mapping[str, Any]) -> str:
        return str(fpmm.get("id") or "").strip()

    @staticmethod
    def _title(fpmm: Mapping[str, Any]) -> str:
        question = fpmm.get("question")
        if isinstance(question, Mapping) and question.get("title"):
            return str(question["title"])
        return str(fpmm.get("title") or OmenAdapter._fpmm_id(fpmm))

    @staticmethod
    def _status(fpmm: Mapping[str, Any]) -> str:
        if fpmm.get("resolutionTimestamp") or (
            isinstance(fpmm.get("condition"), Mapping) and fpmm["condition"].get("resolutionTimestamp")
        ):
            return "resolved"
        if fpmm.get("currentAnswer") or fpmm.get("answerFinalizedTimestamp"):
            return "answering"
        return "active"

    @staticmethod
    def _outcome_names(fpmm: Mapping[str, Any]) -> List[str]:
        outcomes: Any = fpmm.get("outcomes")
        question = fpmm.get("question")
        if not isinstance(outcomes, list) and isinstance(question, Mapping):
            outcomes = question.get("outcomes")
        if isinstance(outcomes, list) and outcomes:
            return [str(outcome) for outcome in outcomes]
        try:
            count = int(fpmm.get("outcomeSlotCount") or 0)
        except (TypeError, ValueError):
            count = 0
        if count == 2:
            return ["Yes", "No"]
        return [f"Outcome {index}" for index in range(max(count, 0))]

    @staticmethod
    def _marginal_prices(fpmm: Mapping[str, Any]) -> List[Optional[float]]:
        raw = fpmm.get("outcomeTokenMarginalPrices")
        if not isinstance(raw, list):
            return []
        return [OmenAdapter._optional_probability(value) for value in raw]

    @staticmethod
    def _split_contract_id(contract_id: str) -> Tuple[str, int]:
        raw = str(contract_id or "").strip()
        if ":" not in raw:
            raise MarketConfigurationError("Omen contract id must be FPMM_ID:OUTCOME_INDEX.")
        market_id, outcome = raw.rsplit(":", 1)
        try:
            index = int(outcome)
        except ValueError as exc:
            raise MarketConfigurationError("Omen outcome index must be an integer.") from exc
        if not market_id.strip() or index < 0:
            raise MarketConfigurationError("Omen contract id must include a market id and non-negative outcome index.")
        return market_id.strip(), index

    @staticmethod
    def _contract_id(fpmm_id: str, outcome_index: int) -> str:
        return f"{fpmm_id}:{int(outcome_index)}"


class ZeitgeistAdapter(_GraphQLAdapter):
    """Zeitgeist adapter using the documented Subsquid/indexer GraphQL market shape."""

    metadata = get_market_metadata("zeitgeist")
    graphql_config_key = "zeitgeist_indexer_url"
    graphql_env_vars = ("ZEITGEIST_INDEXER_URL",)
    default_graphql_url = DEFAULT_ZEITGEIST_INDEXER_URL

    MARKETS_QUERY = """
    query ZeitgeistMarkets($limit: Int!, $offset: Int!) {
      markets(limit: $limit, offset: $offset) {
        id
        marketId
        question
        description
        slug
        status
        resolvedOutcome
        outcomeAssets
        marketType {
          categorical
          scalar
        }
        categories {
          ticker
          name
          color
        }
        pool {
          id
          poolId
          poolStatus
          baseAsset
          volume
          ztgQty
          weights {
            assetId
            len
          }
        }
      }
    }
    """

    MARKET_QUERY = """
    query ZeitgeistMarket($marketId: Int!) {
      markets(limit: 1, where: { marketId_eq: $marketId }) {
        id
        marketId
        question
        description
        slug
        status
        resolvedOutcome
        outcomeAssets
        marketType {
          categorical
          scalar
        }
        categories {
          ticker
          name
          color
        }
        pool {
          id
          poolId
          poolStatus
          baseAsset
          volume
          ztgQty
          weights {
            assetId
            len
          }
        }
      }
    }
    """

    ASSET_QUERY = """
    query ZeitgeistAsset($assetId: String!) {
      assets(limit: 1, where: { assetId_eq: $assetId }) {
        id
        assetId
        poolId
        price
        amountInPool
      }
    }
    """

    def health_check(self) -> Dict[str, Any]:
        health = super().health_check()
        url, source = self._graphql_url_with_source(required=False)
        health.update(
            {
                "indexer_url_configured": bool(url),
                "indexer_url_source": source,
                "references": list(ZEITGEIST_REFERENCES),
                "orderbook_supported": False,
                "live_trading_enabled": False,
            }
        )
        return health

    def list_events(self, query: str = "", limit: int = 50) -> List[MarketEvent]:
        self.ensure_capability("event_listing")
        desired = max(1, min(int(limit or 50), 100))
        data = self._graphql(self.MARKETS_QUERY, {"limit": desired, "offset": 0})
        markets = self._markets_from_payload(data)
        status_filter = str(self.config.get("zeitgeist_market_status") or "").strip().lower()
        if status_filter:
            markets = [market for market in markets if str(market.get("status") or "").lower() == status_filter]
        q = str(query or "").strip().lower()
        if q:
            markets = [market for market in markets if self._market_matches_query(market, q)]
        return [self._event_from_market(market) for market in markets[:desired]]

    def list_contracts(self, event_id: str) -> List[MarketContract]:
        self.ensure_capability("event_listing")
        market = self._fetch_market(event_id)
        return self._contracts_from_market(market)

    def get_price(self, contract_id: str) -> PriceSnapshot:
        self.ensure_capability("price_reading")
        market_id, outcome_index = self._split_contract_id(contract_id)
        market = self._fetch_market(market_id)
        asset_id = self._asset_id_for_outcome(market, outcome_index)
        asset = self._fetch_asset(asset_id)
        price = self._optional_probability(asset.get("price"))
        if price is None:
            raise MarketConfigurationError(f"Zeitgeist asset price for {asset_id!r} was not available from the indexer.")
        return PriceSnapshot(
            market_id=self.market_id,
            contract_id=self._contract_id(str(self._market_id(market)), outcome_index),
            last=price,
            midpoint=price,
            source="zeitgeist_indexer_asset_price",
            raw={"market": dict(market), "asset": dict(asset), "outcome_index": outcome_index},
        )

    def get_orderbook(self, contract_id: str):
        raise UnsupportedFeatureError(
            self.market_id,
            "orderbook_reading",
            "Zeitgeist indexer support currently exposes market, pool, and asset prices, not CLOB depth.",
        )

    def place_paper_order(self, order: PaperOrderRequest) -> PaperOrderResult:
        self.ensure_capability("paper_trading")
        self._validate_order(order)
        market_id, outcome_index = self._split_contract_id(order.contract_id)
        average = order.limit_price if order.limit_price is not None else self.get_price(order.contract_id).last
        return PaperOrderResult(
            market_id=self.market_id,
            contract_id=self._contract_id(market_id, outcome_index),
            accepted=True,
            message=(
                f"DRY RUN: would simulate Zeitgeist {order.side.upper()} "
                f"for {order.size:.4f} outcome shares"
                + (f" at probability {float(average):.4f}" if average is not None else "")
            ),
            filled_size=0.0,
            average_price=average,
            raw={"market_id": market_id, "outcome_index": outcome_index, "dry_run": True},
        )

    def place_live_order(self, order: PaperOrderRequest):
        raise UnsupportedFeatureError(
            self.market_id,
            "live_trading",
            "Zeitgeist live trading requires explicit wallet-signed Substrate extrinsics and is not implemented.",
        )

    def copy_trade_from_activity(self, activity: Mapping[str, Any]):
        raise UnsupportedFeatureError(
            self.market_id,
            "copy_trading",
            "Zeitgeist copy trading is unsupported because this adapter has no official account activity mirroring API.",
        )

    def _fetch_market(self, market_id: Any) -> Mapping[str, Any]:
        try:
            parsed_market_id = int(str(market_id).strip())
        except (TypeError, ValueError) as exc:
            raise MarketConfigurationError("Zeitgeist market id must be an integer.") from exc
        data = self._graphql(self.MARKET_QUERY, {"marketId": parsed_market_id})
        markets = self._markets_from_payload(data)
        if markets:
            return markets[0]
        raise MarketConfigurationError(f"Zeitgeist market {parsed_market_id!r} was not found.")

    def _fetch_asset(self, asset_id: str) -> Mapping[str, Any]:
        data = self._graphql(self.ASSET_QUERY, {"assetId": asset_id})
        assets = data.get("assets")
        if isinstance(assets, list) and assets and isinstance(assets[0], Mapping):
            return assets[0]
        raise MarketConfigurationError(f"Zeitgeist asset {asset_id!r} was not found.")

    def _event_from_market(self, market: Mapping[str, Any]) -> MarketEvent:
        market_id = str(self._market_id(market))
        return MarketEvent(
            market_id=self.market_id,
            event_id=market_id,
            title=str(market.get("question") or market.get("description") or market_id),
            url=self._market_url(market),
            status=str(market.get("status") or "").strip().lower(),
            raw=dict(market),
        )

    def _contracts_from_market(self, market: Mapping[str, Any]) -> List[MarketContract]:
        market_id = str(self._market_id(market))
        title = str(market.get("question") or market_id)
        status = str(market.get("status") or "").strip().lower()
        assets = self._outcome_assets(market)
        categories = self._categories(market)
        contracts: List[MarketContract] = []
        for index, asset_id in enumerate(assets):
            outcome = self._category_name(categories, index) or asset_id
            contracts.append(
                MarketContract(
                    market_id=self.market_id,
                    contract_id=self._contract_id(market_id, index),
                    event_id=market_id,
                    title=f"{title} - {outcome}",
                    outcome=outcome,
                    url=self._market_url(market),
                    status=status,
                    raw={"market": dict(market), "asset_id": asset_id, "outcome_index": index},
                )
            )
        return contracts

    def _validate_order(self, order: PaperOrderRequest) -> None:
        self.ensure_order_market(order)
        self._split_contract_id(order.contract_id)
        side = str(order.side or "").upper()
        if side not in {"BUY", "SELL"}:
            raise MarketConfigurationError("Zeitgeist paper order side must be BUY or SELL.")
        if not self._is_positive_number(order.size):
            raise MarketConfigurationError("Zeitgeist paper order size must be positive.")
        if order.limit_price is not None:
            self._probability(order.limit_price, allow_zero=False)

    @staticmethod
    def _markets_from_payload(data: Mapping[str, Any]) -> List[Mapping[str, Any]]:
        markets = data.get("markets")
        return [market for market in markets if isinstance(market, Mapping)] if isinstance(markets, list) else []

    @staticmethod
    def _market_matches_query(market: Mapping[str, Any], query: str) -> bool:
        values = [
            market.get("id"),
            market.get("marketId"),
            market.get("question"),
            market.get("description"),
            market.get("slug"),
            market.get("status"),
            " ".join(ZeitgeistAdapter._outcome_assets(market)),
            " ".join(
                str(category.get("name") or category.get("ticker") or "")
                for category in ZeitgeistAdapter._categories(market)
            ),
        ]
        return query in " ".join(str(value or "") for value in values).lower()

    @staticmethod
    def _market_id(market: Mapping[str, Any]) -> Any:
        return market.get("marketId") if market.get("marketId") is not None else market.get("id")

    @staticmethod
    def _outcome_assets(market: Mapping[str, Any]) -> List[str]:
        assets = market.get("outcomeAssets")
        return [str(asset) for asset in assets if asset is not None] if isinstance(assets, list) else []

    @staticmethod
    def _categories(market: Mapping[str, Any]) -> List[Mapping[str, Any]]:
        categories = market.get("categories")
        return [category for category in categories if isinstance(category, Mapping)] if isinstance(categories, list) else []

    @staticmethod
    def _category_name(categories: List[Mapping[str, Any]], index: int) -> str:
        if index >= len(categories):
            return ""
        category = categories[index]
        return str(category.get("name") or category.get("ticker") or "").strip()

    @staticmethod
    def _asset_id_for_outcome(market: Mapping[str, Any], outcome_index: int) -> str:
        assets = ZeitgeistAdapter._outcome_assets(market)
        if outcome_index >= len(assets):
            raise MarketConfigurationError("Zeitgeist outcome index is outside this market's asset list.")
        return assets[outcome_index]

    @staticmethod
    def _market_url(market: Mapping[str, Any]) -> str:
        market_id = ZeitgeistAdapter._market_id(market)
        slug = str(market.get("slug") or "").strip()
        if slug:
            return f"https://app.zeitgeist.pm/markets/{slug}"
        return f"https://app.zeitgeist.pm/markets/{market_id}" if market_id is not None else "https://zeitgeist.pm"

    @staticmethod
    def _split_contract_id(contract_id: str) -> Tuple[str, int]:
        raw = str(contract_id or "").strip()
        if ":" not in raw:
            raise MarketConfigurationError("Zeitgeist contract id must be MARKET_ID:OUTCOME_INDEX.")
        market_id, outcome = raw.rsplit(":", 1)
        try:
            index = int(outcome)
        except ValueError as exc:
            raise MarketConfigurationError("Zeitgeist outcome index must be an integer.") from exc
        if not market_id.strip() or index < 0:
            raise MarketConfigurationError(
                "Zeitgeist contract id must include a market id and non-negative outcome index."
            )
        return market_id.strip(), index

    @staticmethod
    def _contract_id(market_id: str, outcome_index: int) -> str:
        return f"{market_id}:{int(outcome_index)}"
