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


PROBABLE_CAPABILITIES = MarketCapabilities(
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
    region_limited=True,
)


METADAO_CAPABILITIES = MarketCapabilities(
    market_discovery=True,
    event_listing=True,
    price_reading=True,
    orderbook_reading=False,
    alerts=True,
    paper_trading=True,
    live_trading=True,
    copy_trading=False,
    api_required=True,
    credentials_required=False,
    kyc_required=False,
    region_limited=True,
)


SEER_CAPABILITIES = MarketCapabilities(
    market_discovery=True,
    event_listing=True,
    price_reading=True,
    orderbook_reading=False,
    alerts=True,
    paper_trading=True,
    live_trading=True,
    copy_trading=False,
    api_required=True,
    credentials_required=False,
    kyc_required=False,
    region_limited=True,
)


TRUEO_CAPABILITIES = MarketCapabilities(
    market_discovery=True,
    event_listing=True,
    price_reading=True,
    orderbook_reading=False,
    alerts=True,
    paper_trading=True,
    live_trading=True,
    copy_trading=False,
    api_required=True,
    credentials_required=False,
    kyc_required=False,
    region_limited=True,
)


ZEITGEIST_SDK_CAPABILITIES = MarketCapabilities(
    market_discovery=True,
    event_listing=True,
    price_reading=True,
    orderbook_reading=False,
    alerts=True,
    paper_trading=True,
    # Same guarded HybridRouter extrinsic boundary as the primary adapter.
    live_trading=True,
    copy_trading=False,
    api_required=True,
    credentials_required=False,
    kyc_required=False,
    region_limited=True,
)


GNOSIS_PREDICTION_CAPABILITIES = MarketCapabilities(
    market_discovery=True,
    event_listing=True,
    price_reading=True,
    orderbook_reading=False,
    alerts=True,
    paper_trading=True,
    live_trading=True,
    copy_trading=False,
    api_required=True,
    credentials_required=True,
    kyc_required=False,
    region_limited=True,
)


ZEITGEIST_POOL_CAPABILITIES = MarketCapabilities(
    market_discovery=True,
    event_listing=True,
    price_reading=True,
    orderbook_reading=False,
    alerts=True,
    paper_trading=True,
    # Pool-scoped live forwarding still requires reviewed pool metadata and an
    # externally signed HybridRouter extrinsic; the adapter never signs or settles.
    live_trading=True,
    copy_trading=False,
    api_required=True,
    credentials_required=False,
    kyc_required=False,
    region_limited=True,
)


REALITY_ETH_CAPABILITIES = MarketCapabilities(
    market_discovery=True,
    event_listing=True,
    price_reading=False,
    orderbook_reading=False,
    alerts=True,
    paper_trading=False,
    live_trading=False,
    copy_trading=False,
    api_required=True,
    credentials_required=True,
    kyc_required=False,
    region_limited=True,
)


MATCHBOOK_CAPABILITIES = MarketCapabilities(
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
    kyc_required=True,
    region_limited=True,
)


PROPHET_EXCHANGE_CAPABILITIES = MarketCapabilities(
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
    kyc_required=True,
    region_limited=True,
)


DFLOW_CAPABILITIES = MarketCapabilities(
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
    kyc_required=True,
    region_limited=True,
)


SPACE_CAPABILITIES = MarketCapabilities(
    market_discovery=True,
    event_listing=True,
    price_reading=True,
    orderbook_reading=True,
    alerts=True,
    paper_trading=True,
    live_trading=False,
    copy_trading=False,
    api_required=True,
    credentials_required=False,
    kyc_required=False,
    region_limited=True,
)


COINBASE_PREDICTION_CAPABILITIES = MarketCapabilities(
    market_discovery=True,
    event_listing=True,
    price_reading=True,
    orderbook_reading=True,
    alerts=True,
    paper_trading=True,
    live_trading=False,
    copy_trading=False,
    api_required=True,
    credentials_required=False,
    kyc_required=True,
    region_limited=True,
)


PRDT_FINANCE_CAPABILITIES = MarketCapabilities(
    market_discovery=True,
    event_listing=True,
    price_reading=True,
    orderbook_reading=False,
    alerts=True,
    paper_trading=True,
    live_trading=False,
    copy_trading=False,
    api_required=True,
    credentials_required=False,
    kyc_required=False,
    region_limited=True,
)


EXPANDED_MARKET_CATALOG: Tuple[MarketMetadata, ...] = (
    MarketMetadata(
        market_id="coinbase_prediction_markets",
        display_name="Coinbase Prediction Markets",
        homepage_url="https://help.coinbase.com/en/coinbase/trading-and-funding/prediction-markets/intro",
        description=(
            "Read-only Coinbase prediction-market alias over the official Kalshi venue market-data API. "
            "It supports discovery, contracts, prices, orderbooks, alerts, and local paper orders; "
            "Coinbase-specific live and copy-trading APIs are not published."
        ),
        capabilities=COINBASE_PREDICTION_CAPABILITIES,
    ),
    MarketMetadata(
        market_id="probable",
        display_name="Probable",
        homepage_url="https://developer.probable.markets/",
        description=(
            "Official Probable market and CLOB API adapter for discovery, token prices, orderbooks, "
            "alerts, paper orders, and guarded signed-order submission."
        ),
        capabilities=PROBABLE_CAPABILITIES,
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
        description=(
            "Official Seer serverless API adapter for market discovery, outcome prices, alerts, "
            "local paper orders, and guarded externally signed transactions to an explicitly reviewed "
            "third-party DEX; CLOB depth, wallet signing, approvals, and settlement remain operator-owned."
        ),
        capabilities=SEER_CAPABILITIES,
    ),
    MarketMetadata(
        market_id="dflow",
        display_name="DFlow",
        homepage_url="https://pond.dflow.net/introduction",
        description=(
            "Official DFlow Metadata/Trade API adapter for event and market discovery, outcome prices, "
            "orderbooks, paper orders, and guarded wallet-signed Solana transaction submission."
        ),
        capabilities=DFLOW_CAPABILITIES,
    ),
    MarketMetadata(
        market_id="space",
        display_name="Space",
        homepage_url="https://docs.into.space/en/api/rest",
        description=(
            "Official Space REST adapter for public market discovery, binary/multi-outcome contracts, "
            "prices, orderbooks, alerts, and local paper orders; wallet-signed live execution and copy "
            "trading remain unsupported while the public API release is pending."
        ),
        capabilities=SPACE_CAPABILITIES,
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
        description=(
            "Official Trueo Base on-chain adapter for TruthMarketManager discovery, immutable market fields, "
            "Uniswap V3 outcome prices, alerts, paper orders, and guarded externally signed transactions; "
            "CLOB depth and copy trading remain unsupported."
        ),
        capabilities=TRUEO_CAPABILITIES,
    ),
    MarketMetadata(
        market_id="prdt_finance",
        display_name="PRDT Finance",
        homepage_url="https://prdt.finance/en",
        description=(
            "Configured PRDT Prediction-contract adapter for on-chain event discovery, bull/bear pool-share "
            "prices, alerts, and local paper intents. Explicit deployed Prediction addresses are required; "
            "CLOB depth, live wallet execution, settlement, and copy trading remain unsupported."
        ),
        capabilities=PRDT_FINANCE_CAPABILITIES,
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
        description=(
            "Official Gnosis/Omen FixedProductMarketMaker adapter for market discovery, outcome prices, "
            "alerts, local paper orders, and guarded externally signed FPMM transactions; CLOB depth, "
            "collateral approval, settlement, and copy trading remain unsupported."
        ),
        capabilities=GNOSIS_PREDICTION_CAPABILITIES,
    ),
    MarketMetadata(
        market_id="zeitgeist_sdk_markets",
        display_name="Zeitgeist SDK / Markets",
        homepage_url="https://docs.zeitgeist.pm/docs/build/sdk/v2/fetch-markets",
        description=(
            "Official Zeitgeist SDK/Markets alias using the documented Subsquid/indexer GraphQL market and asset "
            "contract for discovery, outcome prices, alerts, and paper orders."
        ),
        capabilities=ZEITGEIST_SDK_CAPABILITIES,
    ),
    MarketMetadata(
        market_id="metadao",
        display_name="MetaDAO",
        homepage_url="https://docs.metadao.fi/protocol/analytics",
        description=(
            "Official MetaDAO Futarchy DEX API adapter for public DAO ticker discovery, bid/ask/price reads, "
            "alerts, local paper orders, and guarded externally signed Solana router transactions; orderbook "
            "depth, wallet signing, approvals, settlement, and copy trading remain operator-owned or unsupported."
        ),
        capabilities=METADAO_CAPABILITIES,
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
        description=(
            "Official Zeitgeist pool-aware indexer adapter for market discovery, pool-backed outcome prices, "
            "alerts, and local paper orders; CLOB depth, wallet execution, and pool settlement remain unsupported."
        ),
        capabilities=ZEITGEIST_POOL_CAPABILITIES,
    ),
    MarketMetadata(
        market_id="reality_eth_markets",
        display_name="Reality.eth Markets",
        homepage_url="https://reality.eth.limo",
        description=(
            "Official Reality.eth subgraph adapter for read-only question discovery, response-option listing, "
            "and lifecycle alerts; prices, orderbooks, and trading are not part of the oracle protocol."
        ),
        capabilities=REALITY_ETH_CAPABILITIES,
    ),
    MarketMetadata(
        market_id="sportstrade",
        display_name="SportsTrade",
        homepage_url="https://sportstrade.com",
        description="Verified blocked: Sporttrade officially ceased all wagering on 2026-05-25, so no production market or order integration is available.",
    ),
    MarketMetadata(
        market_id="prophet_exchange",
        display_name="Prophet Exchange",
        homepage_url="https://docs.prophetx.co/docs/integration",
        description=(
            "Official ProphetX Market Data and Trading API adapter for tournament/event/market discovery, "
            "available-quantity quotes, local paper orders, and guarded authenticated market-maker orders."
        ),
        capabilities=PROPHET_EXCHANGE_CAPABILITIES,
    ),
    MarketMetadata(
        market_id="sporttrade_products",
        display_name="Sporttrade Prediction / Exchange Products",
        homepage_url="https://sporttrade.com",
        description="Verified blocked: Sporttrade officially ceased all wagering on 2026-05-25, so its prediction/exchange products are not an active production integration target.",
    ),
    MarketMetadata(
        market_id="matchbook",
        display_name="Matchbook",
        homepage_url="https://developers.matchbook.com/",
        description=(
            "Official Matchbook exchange API adapter for event/market discovery, decimal-odds prices, "
            "orderbooks, paper orders, and guarded session-authenticated offers."
        ),
        capabilities=MATCHBOOK_CAPABILITIES,
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
    "kalshi_via_robinhood": _blocker(
        "Robinhood's distribution surface does not publish a separate public automation contract for Kalshi-through-Robinhood accounts; private brokerage endpoints are not supported.",
        "https://robinhood.com/us/en/prediction-markets",
        "https://kalshi.com/",
    ),
    "fanduel_predicts": _blocker(
        "FanDuel Predicts is a consumer product without a public documented prediction-market API or third-party automation contract.",
        "https://www.fanduel.com/",
    ),
    "synstation": _blocker(
        "No stable official market-data and order API contract has been validated for SynStation.",
        "https://synstation.ai",
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
    "sportstrade": _blocker(
        "Sporttrade officially ceased all wagering on 2026-05-25; no active production market or order integration is available.",
        "https://getsporttrade.com/",
        "https://new.getsporttrade.com/",
    ),
    "sporttrade_products": _blocker(
        "Sporttrade officially ceased all wagering on 2026-05-25; its prediction/exchange products are not an active production integration target.",
        "https://getsporttrade.com/",
        "https://new.getsporttrade.com/",
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

