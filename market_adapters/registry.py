from __future__ import annotations

from typing import Any, Callable, Dict, List, Mapping, Optional, Type

from .base import MarketAdapter
from .catalog import MARKET_CATALOG
from .errors import MarketConfigurationError
from .types import MarketMetadata


VERIFIED_BLOCKERS: Dict[str, Dict[str, Any]] = {
    "robinhood_prediction_markets": {
        "reason": (
            "Verified 2026-05-26: Robinhood exposes Prediction Markets as a consumer brokerage/app "
            "product, but no public documented Robinhood prediction-market API, SDK, or automation "
            "permission flow is published. The adapter must not automate the consumer app or private endpoints."
        ),
        "references": [
            "https://robinhood.com/us/en/prediction-markets",
            "https://robinhood.com/us/en/newsroom/robinhood-prediction-markets-hub/",
        ],
    },
    "fanatics_markets": {
        "reason": (
            "Verified 2026-05-26: Fanatics Markets is documented as a consumer prediction-market product "
            "built with Crypto.com/CDNA, but no public Fanatics Markets API, SDK, or automation terms are "
            "published for third-party app integration."
        ),
        "references": [
            "https://www.fanaticsinc.com/press-releases/fanatics-launches-fanatics-markets-the-first-prediction-market-at-the-intersection-of-sports-finance-and-culture",
            "https://crypto.com/en/prediction/",
        ],
    },
    "draftkings_predictions": {
        "reason": (
            "Verified 2026-05-26: DraftKings Predictions is published as an app product, but no public "
            "documented Predictions API, SDK, or account activity/order automation route is available. "
            "Private app endpoints are intentionally unsupported."
        ),
        "references": [
            "https://www.draftkings.com/draftkings-debuts-predictions-app-entering-prediction-markets",
            "https://www.draftkings.com",
        ],
    },
    "ibkr_forecasttrader": {
        "reason": (
            "Verified 2026-05-26: IBKR ForecastTrader/Forecast Contracts require an eligible IBKR account, "
            "trading permissions, and authenticated Web API or TWS/API session handling. This app cannot "
            "ship a reliable adapter without user-specific broker entitlements and contract mapping tests."
        ),
        "references": [
            "https://forecasttrader.interactivebrokers.com/",
            "https://www.interactivebrokers.com/campus/traders-insight/ibkr-toolbox/prediction-markets-101/",
            "https://portal.interactivebrokers.com/download/CP_API.pdf",
        ],
    },
    "forecastex": {
        "reason": (
            "Verified 2026-05-26: ForecastEx contracts are exchange/clearinghouse products purchased "
            "through ForecastEx members/FCMs. No direct public ForecastEx self-service market-data and "
            "order API is published for this app."
        ),
        "references": [
            "https://forecastex.com/about/how-forecast-contracts-work",
            "https://forecastex.com",
        ],
    },
    "cme_prediction_markets": {
        "reason": (
            "Verified 2026-05-26: CME event contracts can require CME market-data licensing/entitlements "
            "and a broker/order route. This app has no licensed data entitlement or documented broker "
            "order path for automated event-contract trading."
        ),
        "references": [
            "https://www.cmegroup.com/markets/event-contracts.html",
            "https://www.cmegroup.com/market-data/market-data-api.html",
        ],
    },
    "nadex": {
        "reason": (
            "Verified 2026-05-26: Nadex event contracts require a Nadex account on a CFTC-regulated "
            "exchange, and no public documented API suitable for third-party event-contract discovery, "
            "quotes, or order automation is published for this adapter."
        ),
        "references": [
            "https://www.nadex.com/product-market/",
            "https://www.nadex.com/learning/how-to-trade-event-contracts/",
        ],
    },
    "hyperliquid": {
        "reason": (
            "Verified 2026-05-26: Hyperliquid exposes HyperCore trading primitives, but this app does not "
            "yet have a first-party, stable HIP-4 prediction-market catalog/order-normalization contract "
            "with offline fixtures and wallet risk controls. Generic perp/spot APIs are not enough."
        ),
        "references": [
            "https://hyperliquid.gitbook.io/hyperliquid-docs/hypercore/overview",
            "https://hyperliquid.gitbook.io/hyperliquid-docs/hyperliquid-improvement-proposals-hips/hip-3-builder-deployed-perpetuals",
        ],
    },
    "context_v2": {
        "reason": (
            "Verified 2026-05-26: Context V2 publishes API/order documentation, but production use requires "
            "service credentials, market/order lifecycle validation, and account-specific automation terms "
            "that are not available in this local app."
        ),
        "references": [
            "https://docs.context.markets/api-reference/orders/create-order",
            "https://storage.googleapis.com/spur.us/website/resources/documentation/api-v2.pdf",
            "https://context.app",
        ],
    },
    "frenzy_finance": {
        "reason": (
            "Verified 2026-05-26: Frenzy Finance documents an on-chain short-duration prediction protocol, "
            "but safe integration requires wallet/network handling, contract address validation, settlement "
            "rules, and tests that are not available in this app."
        ),
        "references": [
            "https://frenzy.finance/docs",
            "https://frenzy.finance",
        ],
    },
    "fact_machine": {
        "reason": (
            "Verified 2026-05-26: Fact Machine does not publish a public documented API/SDK or stable "
            "protocol integration contract suitable for discovery, pricing, or order automation in this app."
        ),
        "references": [
            "https://factmachine.io",
        ],
    },
    "good_judgment_open": {
        "reason": (
            "Verified 2026-05-26: Good Judgment Open has public question pages and account-based "
            "forecasting, but no public documented API, SDK, or export endpoint suitable for app integration. "
            "HTML scraping and private session automation are intentionally unsupported."
        ),
        "references": [
            "https://www.gjopen.com",
            "https://www.gjopen.com/questions",
            "https://www.gjopen.com/privacy",
        ],
    },
    "hypermind": {
        "reason": (
            "Verified 2026-05-26: Hypermind describes dashboards, PDF reports, and API feeds as managed "
            "service deliverables, but does not publish a public API contract, SDK, or self-service data endpoint. "
            "Program or enterprise access is required before implementation."
        ),
        "references": [
            "https://www.hypermind.com/products-services/crowd",
            "https://www.hypermind.com/products-services/prescience",
            "https://predict.hypermind.com",
        ],
    },
    "iowa_electronic_markets": {
        "reason": (
            "Verified 2026-05-26: Iowa Electronic Markets publishes official site pages, quote pages, and "
            "price-history forms, but no stable documented API for discovery, contract listing, live quotes, "
            "or automated trading. Trading also requires IEM account eligibility."
        ),
        "references": [
            "https://iem.uiowa.edu",
            "https://iemweb.biz.uiowa.edu/pricehistory/pricehistory_SelectContract.cfm",
            "https://iemweb.biz.uiowa.edu/quotes/",
        ],
    },
    "infer": {
        "reason": (
            "Verified 2026-05-26: INFER-pub now redirects to the RAND Forecasting Initiative, which exposes "
            "public landing/forecast pages and account sign-in but no public documented API or export contract. "
            "The adapter must not scrape pages or automate private sessions."
        ),
        "references": [
            "https://www.infer-pub.com",
            "https://www.randforecastinginitiative.org",
            "https://www.randforecastinginitiative.org/forecasts",
        ],
    },
    "betmgm": {
        "reason": (
            "Verified 2026-05-26: BetMGM is a regulated consumer sportsbook/casino product and does not "
            "publish a public prediction/event-market API, SDK, or automation permission flow for this app. "
            "Consumer app automation is intentionally unsupported."
        ),
        "references": [
            "https://www.betmgm.com",
            "https://www.betmgminc.com",
        ],
    },
    "prizepicks": {
        "reason": (
            "Verified 2026-05-26: PrizePicks exposes consumer fantasy/pick products and support pages, "
            "but no public official developer API for programmatic projections, entries, market data, or "
            "automation is published for this app."
        ),
        "references": [
            "https://www.prizepicks.com",
            "https://www.prizepicks.com/helpcenter",
        ],
    },
    "underdog_sports": {
        "reason": (
            "Verified 2026-05-26: Underdog Sports/Fantasy is a consumer app with eligibility restrictions "
            "and support documentation, but no public official API or automation terms are published for "
            "third-party prediction/event-market integration."
        ),
        "references": [
            "https://www.underdogfantasy.com/",
            "https://help.underdogfantasy.com/",
        ],
    },
    "drift_bet": {
        "reason": (
            "Verified 2026-05-26: Drift BET depends on Drift/Solana wallet, margin, settlement, and market "
            "account handling. This app has no verified official BET-specific read/order integration with "
            "fixtures and wallet safeguards."
        ),
        "references": [
            "https://www.drift.trade",
            "https://docs.drift.trade/",
        ],
    },
    "thales_market": {
        "reason": (
            "Verified 2026-05-26: Thales documents on-chain positional/digital-option markets, but safe "
            "adapter support requires chain-specific contract/indexer integration, AMM accounting, and "
            "explicit wallet transaction handling that this app does not implement."
        ),
        "references": [
            "https://docs.thalesmarket.io/",
            "https://docs.thales.io/",
        ],
    },
    "hedgehog_markets": {
        "reason": (
            "Verified 2026-05-26: Hedgehog Markets does not publish a stable public API/SDK or documented "
            "protocol contract sufficient for this app's discovery, pricing, order, and settlement model."
        ),
        "references": [
            "https://hedgehog.markets",
        ],
    },
    "smarkets": {
        "reason": (
            "Verified 2026-05-26: Smarkets publishes API documentation and access terms, but API/data use "
            "requires account approval and written permission for platform data use. This app cannot ship "
            "working support without those user-specific entitlements."
        ),
        "references": [
            "https://help.smarkets.com/hc/en-gb/articles/34697834941085-Smarkets-API-Access-Integration-T-Cs",
            "https://help.smarkets.com/hc/en-gb/articles/34720906181021-Smarkets-API-Documentation-Resources",
        ],
    },
    "probo": {
        "reason": (
            "Verified 2026-05-26: Probo is a region-limited consumer opinion-market product and does not "
            "publish a public official API, SDK, or automation permission flow suitable for this app."
        ),
        "references": [
            "https://probo.in",
        ],
    },
}


AdapterFactory = Callable[[Optional[Mapping[str, Any]]], MarketAdapter]


class AdapterRegistry:
    """Registry for market adapter factories."""

    def __init__(self) -> None:
        self._metadata: Dict[str, MarketMetadata] = {}
        self._factories: Dict[str, AdapterFactory] = {}

    def register_metadata(self, metadata: MarketMetadata, *, replace: bool = False) -> None:
        market_id = self._normalize_market_id(metadata.market_id)
        if not replace and market_id in self._metadata:
            raise MarketConfigurationError(f"Market metadata already registered: {market_id}")
        self._metadata[market_id] = metadata

    def register_adapter(self, adapter_cls: Type[MarketAdapter], *, replace: bool = False) -> None:
        metadata = adapter_cls.metadata
        market_id = self._normalize_market_id(metadata.market_id)
        if market_id == "base":
            raise MarketConfigurationError("Base MarketAdapter cannot be registered directly.")
        if not replace and market_id in self._factories:
            raise MarketConfigurationError(f"Adapter already registered: {market_id}")
        self.register_metadata(metadata, replace=True)
        self._factories[market_id] = adapter_cls

    def register_factory(
        self,
        metadata: MarketMetadata,
        factory: AdapterFactory,
        *,
        replace: bool = False,
    ) -> None:
        market_id = self._normalize_market_id(metadata.market_id)
        if not replace and market_id in self._factories:
            raise MarketConfigurationError(f"Adapter already registered: {market_id}")
        self.register_metadata(metadata, replace=True)
        self._factories[market_id] = factory

    def create(self, market_id: str, config: Optional[Mapping[str, Any]] = None) -> MarketAdapter:
        normalized = self._normalize_market_id(market_id)
        factory = self._factories.get(normalized)
        if factory is None:
            raise MarketConfigurationError(f"No adapter registered for market: {normalized}")
        return factory(config)

    def get_metadata(self, market_id: str) -> MarketMetadata:
        normalized = self._normalize_market_id(market_id)
        try:
            return self._metadata[normalized]
        except KeyError as exc:
            raise MarketConfigurationError(f"Unknown market: {normalized}") from exc

    def list_metadata(self, *, enabled_ids: Optional[Mapping[str, bool]] = None) -> List[MarketMetadata]:
        metadata = list(self._metadata.values())
        metadata.sort(key=lambda item: item.display_name.lower())
        if enabled_ids is None:
            return metadata
        return [m for m in metadata if enabled_ids.get(m.market_id, False)]

    def list_market_ids(self) -> List[str]:
        return sorted(self._metadata)

    def has_adapter(self, market_id: str) -> bool:
        return self._normalize_market_id(market_id) in self._factories

    @staticmethod
    def _normalize_market_id(market_id: str) -> str:
        normalized = str(market_id or "").strip().lower()
        if not normalized:
            raise MarketConfigurationError("Market id cannot be empty.")
        return normalized


def build_default_registry() -> AdapterRegistry:
    registry = AdapterRegistry()
    for metadata in MARKET_CATALOG:
        registry.register_metadata(metadata)
    from .azuro import AzuroAdapter
    from .betfair import BetfairExchangeAdapter
    from .crypto_com_predict import CryptoComPredictAdapter
    from .gemini import GeminiPredictionAdapter
    from .kalshi import KalshiAdapter
    from .legacy_web3 import AugurAdapter, OmenAdapter, ZeitgeistAdapter
    from .limitless import LimitlessAdapter
    from .manifold import ManifoldAdapter
    from .metaculus import MetaculusAdapter
    from .myriad import MyriadAdapter
    from .opinion import OpinionAdapter
    from .polymarket import PolymarketAdapter
    from .predict_fun import PredictFunAdapter
    from .predictit import PredictItAdapter
    from .sx_bet import SxBetAdapter
    from .stub import create_stub_adapter, create_verified_blocked_adapter
    from .xo import XOMarketAdapter

    implemented_adapters = (
        PolymarketAdapter,
        KalshiAdapter,
        PredictItAdapter,
        CryptoComPredictAdapter,
        ManifoldAdapter,
        MetaculusAdapter,
        LimitlessAdapter,
        SxBetAdapter,
        AzuroAdapter,
        AugurAdapter,
        OmenAdapter,
        ZeitgeistAdapter,
        GeminiPredictionAdapter,
        MyriadAdapter,
        OpinionAdapter,
        PredictFunAdapter,
        XOMarketAdapter,
        BetfairExchangeAdapter,
    )
    registry.register_adapter(PolymarketAdapter, replace=True)
    registry.register_adapter(KalshiAdapter, replace=True)
    registry.register_adapter(PredictItAdapter, replace=True)
    registry.register_adapter(CryptoComPredictAdapter, replace=True)
    registry.register_adapter(ManifoldAdapter, replace=True)
    registry.register_adapter(MetaculusAdapter, replace=True)
    registry.register_adapter(LimitlessAdapter, replace=True)
    registry.register_adapter(SxBetAdapter, replace=True)
    registry.register_adapter(AzuroAdapter, replace=True)
    registry.register_adapter(AugurAdapter, replace=True)
    registry.register_adapter(OmenAdapter, replace=True)
    registry.register_adapter(ZeitgeistAdapter, replace=True)
    registry.register_adapter(GeminiPredictionAdapter, replace=True)
    registry.register_adapter(MyriadAdapter, replace=True)
    registry.register_adapter(OpinionAdapter, replace=True)
    registry.register_adapter(PredictFunAdapter, replace=True)
    registry.register_adapter(XOMarketAdapter, replace=True)
    registry.register_adapter(BetfairExchangeAdapter, replace=True)
    for metadata in MARKET_CATALOG:
        if metadata.market_id in {adapter.metadata.market_id for adapter in implemented_adapters}:
            continue
        blocker = VERIFIED_BLOCKERS.get(metadata.market_id)
        if blocker:
            registry.register_factory(
                metadata,
                lambda config=None, metadata=metadata, blocker=blocker: create_verified_blocked_adapter(
                    metadata,
                    config,
                    reason=str(blocker["reason"]),
                    references=blocker.get("references", ()),
                    last_reviewed="2026-05-26",
                ),
                replace=True,
            )
            continue
        registry.register_factory(
            metadata,
            lambda config=None, metadata=metadata: create_stub_adapter(metadata, config),
            replace=True,
        )
    return registry
