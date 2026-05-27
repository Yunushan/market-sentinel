from __future__ import annotations

from typing import Dict, Tuple

from .types import MarketCapabilities, MarketMetadata


POLYMARKET_CAPABILITIES = MarketCapabilities(
    market_discovery=True,
    event_listing=True,
    price_reading=True,
    orderbook_reading=True,
    alerts=True,
    paper_trading=True,
    live_trading=True,
    copy_trading=True,
    api_required=True,
    credentials_required=True,
    kyc_required=True,
    region_limited=True,
)

KALSHI_CAPABILITIES = MarketCapabilities(
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

MANIFOLD_CAPABILITIES = MarketCapabilities(
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
    region_limited=False,
)

METACULUS_CAPABILITIES = MarketCapabilities(
    market_discovery=True,
    event_listing=True,
    price_reading=True,
    orderbook_reading=False,
    alerts=True,
    paper_trading=False,
    live_trading=False,
    copy_trading=False,
    api_required=True,
    credentials_required=True,
    kyc_required=False,
    region_limited=False,
)

PREDICTIT_CAPABILITIES = MarketCapabilities(
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
    kyc_required=True,
    region_limited=True,
)

LIMITLESS_CAPABILITIES = MarketCapabilities(
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

SX_BET_CAPABILITIES = MarketCapabilities(
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

AZURO_CAPABILITIES = MarketCapabilities(
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

AUGUR_CAPABILITIES = MarketCapabilities(
    market_discovery=True,
    event_listing=True,
    price_reading=False,
    orderbook_reading=False,
    alerts=False,
    paper_trading=False,
    live_trading=False,
    copy_trading=False,
    api_required=True,
    credentials_required=True,
    kyc_required=False,
    region_limited=True,
)

OMEN_CAPABILITIES = MarketCapabilities(
    market_discovery=True,
    event_listing=True,
    price_reading=True,
    orderbook_reading=False,
    alerts=True,
    paper_trading=True,
    live_trading=False,
    copy_trading=False,
    api_required=True,
    credentials_required=True,
    kyc_required=False,
    region_limited=True,
)

ZEITGEIST_CAPABILITIES = MarketCapabilities(
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

GEMINI_PREDICTION_CAPABILITIES = MarketCapabilities(
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

MYRIAD_CAPABILITIES = MarketCapabilities(
    market_discovery=True,
    event_listing=True,
    price_reading=True,
    orderbook_reading=True,
    alerts=True,
    paper_trading=True,
    live_trading=True,
    copy_trading=False,
    api_required=True,
    credentials_required=False,
    kyc_required=False,
    region_limited=True,
)

OPINION_CAPABILITIES = MarketCapabilities(
    market_discovery=True,
    event_listing=True,
    price_reading=True,
    orderbook_reading=True,
    alerts=True,
    paper_trading=True,
    live_trading=False,
    copy_trading=False,
    api_required=True,
    credentials_required=True,
    kyc_required=False,
    region_limited=True,
)

PREDICT_FUN_CAPABILITIES = MarketCapabilities(
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

XO_MARKET_CAPABILITIES = MarketCapabilities(
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

BETFAIR_CAPABILITIES = MarketCapabilities(
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

STUB_CAPABILITIES = MarketCapabilities()

MARKET_CATALOG: Tuple[MarketMetadata, ...] = (
    MarketMetadata(
        market_id="polymarket",
        display_name="Polymarket",
        default_enabled=True,
        homepage_url="https://polymarket.com",
        description="Existing Polymarket alert, wallet tracking, and guarded copy-trading support.",
        capabilities=POLYMARKET_CAPABILITIES,
    ),
    MarketMetadata(
        market_id="kalshi",
        display_name="Kalshi",
        homepage_url="https://kalshi.com",
        description="Official Kalshi REST market-data adapter with dry-run orders and guarded live-order support.",
        capabilities=KALSHI_CAPABILITIES,
    ),
    MarketMetadata(
        market_id="predictit",
        display_name="PredictIt",
        homepage_url="https://www.predictit.org",
        description="Official public market-data API adapter for read-only political market data and dry-run orders.",
        capabilities=PREDICTIT_CAPABILITIES,
    ),
    MarketMetadata(
        market_id="robinhood_prediction_markets",
        display_name="Robinhood Prediction Markets",
        homepage_url="https://robinhood.com",
    ),
    MarketMetadata(
        market_id="fanatics_markets",
        display_name="Fanatics Markets",
        homepage_url="https://www.fanatics.com",
    ),
    MarketMetadata(
        market_id="draftkings_predictions",
        display_name="DraftKings Predictions",
        homepage_url="https://www.draftkings.com",
    ),
    MarketMetadata(
        market_id="ibkr_forecasttrader",
        display_name="Interactive Brokers ForecastTrader / IBKR Prediction Markets",
        homepage_url="https://www.interactivebrokers.com",
    ),
    MarketMetadata(market_id="forecastex", display_name="ForecastEx", homepage_url="https://www.forecastex.com"),
    MarketMetadata(
        market_id="cme_prediction_markets",
        display_name="CME Group Prediction Markets",
        homepage_url="https://www.cmegroup.com",
        description="Verified blocked: CME event-contract support requires licensed data entitlements and a documented broker/order route.",
    ),
    MarketMetadata(market_id="nadex", display_name="Nadex", homepage_url="https://www.nadex.com"),
    MarketMetadata(
        market_id="crypto_com_predict",
        display_name="Crypto.com Predict / CDNA",
        homepage_url="https://crypto.com",
    ),
    MarketMetadata(
        market_id="hyperliquid",
        display_name="Hyperliquid",
        homepage_url="https://hyperliquid.xyz",
        description="Verified blocked: outcome docs exist, but production-safe outcome metadata, fixtures, and wallet/order safeguards are not implemented.",
    ),
    MarketMetadata(
        market_id="myriad_markets",
        display_name="Myriad Markets",
        homepage_url="https://myriad.markets",
        description="Official Myriad Protocol API adapter for public question/market discovery, outcome prices, orderbooks, dry-run quote payloads, and guarded signed order submission.",
        capabilities=MYRIAD_CAPABILITIES,
    ),
    MarketMetadata(
        market_id="context_v2",
        display_name="Context V2",
        homepage_url="https://context.markets",
        description="Verified blocked: Context Markets is sunset, so historical SDK/API docs are not a supported production target.",
    ),
    MarketMetadata(market_id="frenzy_finance", display_name="Frenzy Finance", homepage_url="https://frenzy.finance"),
    MarketMetadata(
        market_id="xo_market",
        display_name="XO Market",
        homepage_url="https://xotrade.co",
        description="Official XO Markets HMAC REST adapter for authenticated market data, orderbooks, dry-run orders, and guarded live order posting.",
        capabilities=XO_MARKET_CAPABILITIES,
    ),
    MarketMetadata(
        market_id="manifold",
        display_name="Manifold Markets",
        homepage_url="https://manifold.markets",
        description="Official Manifold REST API adapter for market discovery, probabilities, dry-run orders, and guarded MANA betting.",
        capabilities=MANIFOLD_CAPABILITIES,
    ),
    MarketMetadata(
        market_id="metaculus",
        display_name="Metaculus",
        homepage_url="https://www.metaculus.com",
        description="Official Metaculus API adapter for authenticated read-only forecasting questions and probabilities.",
        capabilities=METACULUS_CAPABILITIES,
    ),
    MarketMetadata(
        market_id="good_judgment_open",
        display_name="Good Judgment Open",
        homepage_url="https://www.gjopen.com",
        description="Verified blocked: no public documented API/export contract for app integration.",
    ),
    MarketMetadata(
        market_id="hypermind",
        display_name="Hypermind",
        homepage_url="https://www.hypermind.com",
        description="Verified blocked: API feeds are described as managed service deliverables, not public docs.",
    ),
    MarketMetadata(
        market_id="iowa_electronic_markets",
        display_name="Iowa Electronic Markets",
        homepage_url="https://iem.uiowa.edu",
        description="Verified blocked: public quote/history pages exist, but no stable documented API is published.",
    ),
    MarketMetadata(
        market_id="infer",
        display_name="INFER / INFER-pub",
        homepage_url="https://www.infer-pub.com",
        description="Verified blocked: RFI public pages exist, but no public documented API/export contract is published.",
    ),
    MarketMetadata(market_id="fact_machine", display_name="Fact Machine", homepage_url="https://factmachine.io"),
    MarketMetadata(
        market_id="opinion_labs",
        display_name="Opinion Labs",
        homepage_url="https://opinion.trade",
        description="Official Opinion OpenAPI adapter for authenticated read-only market data, orderbooks, prices, and dry-run orders.",
        capabilities=OPINION_CAPABILITIES,
    ),
    MarketMetadata(
        market_id="gemini_titan",
        display_name="Gemini Titan / Gemini Predictions",
        homepage_url="https://www.gemini.com",
        description="Official Gemini Prediction Markets API adapter for public event discovery, contracts, orderbooks, prices, dry-run orders, and guarded authenticated limit orders.",
        capabilities=GEMINI_PREDICTION_CAPABILITIES,
    ),
    MarketMetadata(
        market_id="augur",
        display_name="Augur",
        homepage_url="https://augur.net",
        description="Legacy Augur v2 read-only market/outcome adapter using the documented subgraph schema with a user-configured GraphQL endpoint.",
        capabilities=AUGUR_CAPABILITIES,
    ),
    MarketMetadata(market_id="betmgm", display_name="BetMGM", homepage_url="https://www.betmgm.com"),
    MarketMetadata(market_id="prizepicks", display_name="PrizePicks", homepage_url="https://www.prizepicks.com"),
    MarketMetadata(market_id="underdog_sports", display_name="Underdog Sports", homepage_url="https://underdogfantasy.com"),
    MarketMetadata(market_id="drift_bet", display_name="Drift BET", homepage_url="https://www.drift.trade"),
    MarketMetadata(
        market_id="thales_market",
        display_name="Thales Market",
        homepage_url="https://thalesmarket.io",
        description="Verified blocked: Thales API/contract docs exist, but chain-specific AMM, collateral, wallet, and fixture coverage is required.",
    ),
    MarketMetadata(market_id="hedgehog_markets", display_name="Hedgehog Markets", homepage_url="https://hedgehog.markets"),
    MarketMetadata(
        market_id="omen",
        display_name="Omen",
        homepage_url="https://omen.eth.limo",
        description="Legacy Omen/Gnosis FixedProductMarketMaker subgraph adapter for read-only markets, AMM marginal prices, alerts, and paper orders.",
        capabilities=OMEN_CAPABILITIES,
    ),
    MarketMetadata(
        market_id="zeitgeist",
        display_name="Zeitgeist",
        homepage_url="https://zeitgeist.pm",
        description="Official Zeitgeist Subsquid/indexer adapter for market discovery, outcome asset prices, alerts, and paper orders.",
        capabilities=ZEITGEIST_CAPABILITIES,
    ),
    MarketMetadata(
        market_id="azuro",
        display_name="Azuro",
        homepage_url="https://azuro.org",
        description="Official Azuro V3 backend/feed API adapter for games, conditions, odds, WebSocket subscriptions, dry-run bets, and guarded pre-signed live order posting.",
        capabilities=AZURO_CAPABILITIES,
    ),
    MarketMetadata(
        market_id="sx_bet",
        display_name="SX Bet / SX Network",
        homepage_url="https://sx.bet",
        description="Official SX Bet REST/WebSocket adapter for sports market data, orderbooks, dry-run orders, and guarded signed live orders.",
        capabilities=SX_BET_CAPABILITIES,
    ),
    MarketMetadata(
        market_id="limitless_exchange",
        display_name="Limitless Exchange",
        homepage_url="https://limitless.exchange",
        description="Official Limitless REST adapter for market data, orderbooks, dry-run orders, and guarded HMAC live orders.",
        capabilities=LIMITLESS_CAPABILITIES,
    ),
    MarketMetadata(
        market_id="predict_fun",
        display_name="Predict.fun",
        homepage_url="https://predict.fun",
        description="Official Predict.fun REST API adapter for market discovery, orderbooks, prices, dry-run orders, and guarded signed order submission.",
        capabilities=PREDICT_FUN_CAPABILITIES,
    ),
    MarketMetadata(
        market_id="smarkets",
        display_name="Smarkets",
        homepage_url="https://smarkets.com",
        description="Verified blocked: API access requires Smarkets account approval and written permission for platform data redistribution.",
    ),
    MarketMetadata(
        market_id="betfair_exchange",
        display_name="Betfair Exchange",
        homepage_url="https://www.betfair.com/exchange",
        description="Official Betfair Exchange API adapter for authenticated market discovery, best-offer orderbooks, prices, dry-run orders, and guarded placeOrders support.",
        capabilities=BETFAIR_CAPABILITIES,
    ),
    MarketMetadata(market_id="probo", display_name="Probo", homepage_url="https://probo.in"),
)

MARKET_IDS = tuple(m.market_id for m in MARKET_CATALOG)
_MARKET_BY_ID: Dict[str, MarketMetadata] = {m.market_id: m for m in MARKET_CATALOG}


def get_market_metadata(market_id: str) -> MarketMetadata:
    normalized = str(market_id or "").strip().lower()
    return _MARKET_BY_ID[normalized]
