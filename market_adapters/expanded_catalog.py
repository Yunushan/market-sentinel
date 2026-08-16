"""Additional platform metadata for the expanded prediction-market inventory.

The catalog is intentionally explicit: a platform can be visible in the GUI and
CLI without being presented as operationally supported.  Entries without a
verified adapter are paired with a blocker record in ``EXPANDED_VERIFIED_BLOCKERS``.
"""

from __future__ import annotations

from typing import Any, Dict, Tuple

from .types import MarketCapabilities, MarketMetadata


XMARKET_CAPABILITIES = MarketCapabilities(
    market_discovery=True,
    event_listing=True,
    price_reading=True,
    orderbook_reading=True,
    alerts=True,
    paper_trading=True,
    live_trading=True,
    copy_trading=False,
    api_required=True,
    credentials_required=True,
    kyc_required=False,
    region_limited=False,
)


EXPANDED_MARKET_CATALOG: Tuple[MarketMetadata, ...] = (
    MarketMetadata(
        market_id="coinbase_prediction_markets",
        display_name="Coinbase Prediction Markets",
        homepage_url="https://help.coinbase.com/en/coinbase/trading-and-funding/prediction-markets/intro",
        description="Verified blocked: Coinbase prediction markets are available through the Coinbase/CFM product, but no public prediction-market API contract is available for this adapter.",
    ),
    MarketMetadata(
        market_id="probable",
        display_name="Probable",
        homepage_url="https://developer.probable.markets/",
        description="Verified blocked: Probable publishes developer documentation, but this repository does not yet have a validated endpoint schema, credentials contract, and offline fixtures for safe integration.",
    ),
    MarketMetadata(
        market_id="kalshi_via_robinhood",
        display_name="Kalshi via Robinhood",
        homepage_url="https://robinhood.com/us/en/prediction-markets",
        description="Verified blocked: the Robinhood distribution surface is a consumer brokerage integration and does not publish a separate public automation contract for Kalshi-through-Robinhood accounts.",
    ),
    MarketMetadata(
        market_id="fanduel_predicts",
        display_name="FanDuel Predicts",
        homepage_url="https://www.fanduel.com/",
        description="Verified blocked: FanDuel Predicts is a consumer product without a public documented prediction-market API or third-party automation contract.",
    ),
    MarketMetadata(
        market_id="seer",
        display_name="Seer",
        homepage_url="https://seer-3.gitbook.io/seer-documentation/developers/interact-with-seer",
        description="Verified blocked: Seer documents smart-contract interaction, but a production-safe chain, wallet, settlement, and fixture-backed adapter is not implemented.",
    ),
    MarketMetadata(
        market_id="dflow",
        display_name="DFlow",
        homepage_url="https://pond.dflow.net/introduction",
        description="Verified blocked: DFlow exposes prediction-market metadata and execution APIs, but this app lacks the required API-key, Solana mint, wallet, and fixture-backed integration contract.",
    ),
    MarketMetadata(
        market_id="space",
        display_name="Space",
        homepage_url="https://docs.into.space/en/resources/tos",
        description="Verified blocked: Space is a wallet-based Solana prediction platform without a validated public REST/indexer contract in this repository.",
    ),
    MarketMetadata(
        market_id="xmarket",
        display_name="Xmarket",
        homepage_url="https://docs.xmarket.app/developers/quick-start",
        description="Official Xmarket API adapter for market discovery, outcome prices, orderbooks, paper orders, and guarded API-key order submission.",
        capabilities=XMARKET_CAPABILITIES,
    ),
    MarketMetadata(
        market_id="trueo",
        display_name="Trueo",
        homepage_url="https://docs.trueo.com/trading",
        description="Verified blocked: Trueo is an on-chain prediction market, but a validated contract/indexer adapter with settlement and wallet safeguards is not implemented.",
    ),
    MarketMetadata(
        market_id="prdt_finance",
        display_name="PRDT Finance",
        homepage_url="https://prdt.finance/en",
        description="Verified blocked: PRDT Finance is an on-chain price-prediction product without a validated public market-data/order adapter and offline fixtures here.",
    ),
    MarketMetadata(
        market_id="synstation",
        display_name="SynStation",
        homepage_url="https://synstation.ai",
        description="Verified blocked: no stable official market-data and order API contract has been validated for SynStation.",
    ),
    MarketMetadata(
        market_id="gnosis_prediction_markets",
        display_name="Gnosis Prediction Markets",
        homepage_url="https://omen.eth.limo",
        description="Verified blocked: Gnosis prediction-market surfaces overlap with Omen contracts, but a separate supported Gnosis market API and lifecycle contract is not implemented.",
    ),
    MarketMetadata(
        market_id="zeitgeist_sdk_markets",
        display_name="Zeitgeist SDK / Markets",
        homepage_url="https://docs.zeitgeist.pm/docs/build/sdk/v2/fetch-markets",
        description="Verified blocked: the existing Zeitgeist adapter covers the configured indexer surface, but this separate SDK/market target lacks an independently validated endpoint and fixture contract.",
    ),
    MarketMetadata(
        market_id="metadao",
        display_name="MetaDAO",
        homepage_url="https://docs.metadao.fi/protocol/analytics",
        description="Verified blocked: MetaDAO is a Solana futarchy/trading protocol and requires a validated market, wallet, settlement, and account-data integration.",
    ),
    MarketMetadata(
        market_id="levr_bet",
        display_name="Levr Bet",
        homepage_url="https://levr.bet",
        description="Verified blocked: no stable official API, contract schema, and settlement fixtures have been validated for Levr Bet.",
    ),
    MarketMetadata(
        market_id="dexsport",
        display_name="Dexsport",
        homepage_url="https://dexsport.io/docs-home/",
        description="Verified blocked: Dexsport documents a betting protocol, but this app lacks a validated prediction-market data, wallet, and settlement adapter.",
    ),
    MarketMetadata(
        market_id="lamas_finance",
        display_name="Lamas Finance",
        homepage_url="https://docs.lamas.co/1.0",
        description="Verified blocked: Lamas Finance is a Solana game/prediction protocol without a validated public API and fixture-backed contract integration here.",
    ),
    MarketMetadata(
        market_id="zetarium_world",
        display_name="Zetarium World",
        homepage_url="https://docs.zetarium.world/docs",
        description="Verified blocked: Zetarium World prediction-market V2/API support is not a stable production integration target in this repository.",
    ),
    MarketMetadata(
        market_id="blinq",
        display_name="Blinq",
        homepage_url="https://blinq.fi",
        description="Verified blocked: Blinq exposes leveraged prediction derivatives, but no validated public API and risk-controlled adapter contract is implemented.",
    ),
    MarketMetadata(
        market_id="zeitgeist_prediction_pools",
        display_name="Zeitgeist Prediction Pools",
        homepage_url="https://docs.zeitgeist.pm/docs/build/sdk/v2/fetch-markets",
        description="Verified blocked: prediction-pool support requires a separate pool schema, asset accounting, and fixture-backed lifecycle checks beyond the existing Zeitgeist indexer adapter.",
    ),
    MarketMetadata(
        market_id="reality_eth_markets",
        display_name="Reality.eth Markets",
        homepage_url="https://github.com/RealityETH/reality-eth-monorepo",
        description="Verified blocked: Reality.eth is an oracle/question protocol rather than a validated tradable-market API for this application.",
    ),
    MarketMetadata(
        market_id="sportstrade",
        display_name="SportsTrade",
        homepage_url="https://sportstrade.com",
        description="Verified blocked: SportsTrade does not expose a validated public API contract for this app's market, quote, and order model.",
    ),
    MarketMetadata(
        market_id="prophet_exchange",
        display_name="Prophet Exchange",
        homepage_url="https://docs.prophetx.co/docs/integration",
        description="Verified blocked: Prophet Exchange API access requires approval and partner credentials that are not available for this repository's public adapter tests.",
    ),
    MarketMetadata(
        market_id="sporttrade_products",
        display_name="Sporttrade Prediction / Exchange Products",
        homepage_url="https://sporttrade.com",
        description="Verified blocked: Sporttrade product access and automation require a validated account/API contract that is not publicly available here.",
    ),
    MarketMetadata(
        market_id="matchbook",
        display_name="Matchbook",
        homepage_url="https://developers.matchbook.com/",
        description="Verified blocked: Matchbook has an official API, but automated use requires account approval and express API/data-use authorization before this app can ship enabled support.",
    ),
    MarketMetadata(
        market_id="scicast",
        display_name="SciCast",
        homepage_url="https://scicast.wordpress.com/wp-content/uploads/2014/10/scicast_datamart_guide_v1-21.pdf",
        description="Verified blocked: SciCast's historical data-mart documentation is not a current production market/trading API contract for this application.",
    ),
    MarketMetadata(
        market_id="meta_arena",
        display_name="Meta Arena",
        homepage_url="https://docs.metaarena.world/",
        description="Verified blocked: Meta Arena is a game platform and does not expose a validated prediction-market API for this adapter model.",
    ),
)


def _blocker(reason: str, *references: str) -> Dict[str, Any]:
    return {
        "reason": f"Verified 2026-08-16: {reason}",
        "references": list(references),
        "last_reviewed": "2026-08-16",
    }


EXPANDED_VERIFIED_BLOCKERS: Dict[str, Dict[str, Any]] = {
    "coinbase_prediction_markets": _blocker(
        "Coinbase prediction markets are exposed through the Coinbase/CFM product, but no public prediction-market API contract is available for third-party automation.",
        "https://help.coinbase.com/en/coinbase/trading-and-funding/prediction-markets/intro",
    ),
    "probable": _blocker(
        "Probable publishes developer documentation, but this repository has not validated the endpoint schema, credential contract, rate limits, and offline fixtures required for safe support.",
        "https://developer.probable.markets/",
    ),
    "kalshi_via_robinhood": _blocker(
        "Robinhood's distribution surface does not publish a separate public automation contract for Kalshi-through-Robinhood accounts; private brokerage endpoints are not supported.",
        "https://robinhood.com/us/en/prediction-markets",
        "https://kalshi.com/",
    ),
    "fanduel_predicts": _blocker(
        "FanDuel Predicts is a consumer product without a public documented prediction-market API or third-party automation contract.",
        "https://www.fanduel.com/",
    ),
    "seer": _blocker(
        "Seer documents smart-contract interaction, but production-safe chain, wallet, settlement, and fixture-backed adapter coverage is not implemented.",
        "https://seer-3.gitbook.io/seer-documentation/developers/interact-with-seer",
    ),
    "dflow": _blocker(
        "DFlow exposes prediction-market metadata and execution APIs, but this app lacks the required API-key, Solana mint, wallet, and fixture-backed integration contract.",
        "https://pond.dflow.net/introduction",
        "https://dflow.mintlify.app/build/metadata-api/live-data/live-data-by-mint",
    ),
    "space": _blocker(
        "Space is a wallet-based Solana prediction platform without a validated public REST/indexer contract in this repository.",
        "https://docs.into.space/en/resources/tos",
    ),
    "trueo": _blocker(
        "Trueo is an on-chain prediction market, but a validated contract/indexer adapter with settlement and wallet safeguards is not implemented.",
        "https://docs.trueo.com/trading",
    ),
    "prdt_finance": _blocker(
        "PRDT Finance is an on-chain price-prediction product without a validated public market-data/order adapter and offline fixtures here.",
        "https://prdt.finance/en",
    ),
    "synstation": _blocker(
        "No stable official market-data and order API contract has been validated for SynStation.",
        "https://synstation.ai",
    ),
    "gnosis_prediction_markets": _blocker(
        "Gnosis prediction-market surfaces overlap with Omen contracts, but a separate supported Gnosis market API and lifecycle contract is not implemented.",
        "https://omen.eth.limo",
        "https://docs.gnosis.io/",
    ),
    "zeitgeist_sdk_markets": _blocker(
        "The existing Zeitgeist adapter covers the configured indexer surface, but this separate SDK/market target lacks an independently validated endpoint and fixture contract.",
        "https://docs.zeitgeist.pm/docs/build/sdk/v2/fetch-markets",
    ),
    "metadao": _blocker(
        "MetaDAO is a Solana futarchy/trading protocol and requires a validated market, wallet, settlement, and account-data integration.",
        "https://docs.metadao.fi/protocol/analytics",
    ),
    "levr_bet": _blocker(
        "No stable official API, contract schema, and settlement fixtures have been validated for Levr Bet.",
        "https://levr.bet",
    ),
    "dexsport": _blocker(
        "Dexsport documents a betting protocol, but this app lacks a validated prediction-market data, wallet, and settlement adapter.",
        "https://dexsport.io/docs-home/",
    ),
    "lamas_finance": _blocker(
        "Lamas Finance is a Solana game/prediction protocol without a validated public API and fixture-backed contract integration here.",
        "https://docs.lamas.co/1.0",
    ),
    "zetarium_world": _blocker(
        "Zetarium World prediction-market V2/API support is not a stable production integration target in this repository.",
        "https://docs.zetarium.world/docs",
        "https://docs.zetarium.world/docs/overview/roadmap",
    ),
    "blinq": _blocker(
        "Blinq exposes leveraged prediction derivatives, but no validated public API and risk-controlled adapter contract is implemented.",
        "https://blinq.fi",
    ),
    "zeitgeist_prediction_pools": _blocker(
        "Prediction-pool support requires a separate pool schema, asset accounting, and fixture-backed lifecycle checks beyond the existing Zeitgeist indexer adapter.",
        "https://docs.zeitgeist.pm/docs/build/sdk/v2/fetch-markets",
    ),
    "reality_eth_markets": _blocker(
        "Reality.eth is an oracle/question protocol rather than a validated tradable-market API for this application.",
        "https://github.com/RealityETH/reality-eth-monorepo",
    ),
    "sportstrade": _blocker(
        "SportsTrade does not expose a validated public API contract for this app's market, quote, and order model.",
        "https://sportstrade.com",
    ),
    "prophet_exchange": _blocker(
        "Prophet Exchange API access requires approval and partner credentials that are not available for this repository's public adapter tests.",
        "https://docs.prophetx.co/docs/integration",
    ),
    "sporttrade_products": _blocker(
        "Sporttrade product access and automation require a validated account/API contract that is not publicly available here.",
        "https://sporttrade.com",
    ),
    "matchbook": _blocker(
        "Matchbook has an official API, but automated use requires account approval and express API/data-use authorization before this app can ship enabled support.",
        "https://developers.matchbook.com/",
        "https://www.matchbook.com/page/rules/terms-and-conditions/",
    ),
    "scicast": _blocker(
        "SciCast's historical data-mart documentation is not a current production market/trading API contract for this application.",
        "https://scicast.wordpress.com/wp-content/uploads/2014/10/scicast_datamart_guide_v1-21.pdf",
    ),
    "meta_arena": _blocker(
        "Meta Arena is a game platform and does not expose a validated prediction-market API for this adapter model.",
        "https://docs.metaarena.world/",
    ),
}

