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
    MarketMetadata(market_id="predictit", display_name="PredictIt", homepage_url="https://www.predictit.org"),
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
    ),
    MarketMetadata(market_id="nadex", display_name="Nadex", homepage_url="https://www.nadex.com"),
    MarketMetadata(
        market_id="crypto_com_predict",
        display_name="Crypto.com Predict / CDNA",
        homepage_url="https://crypto.com",
    ),
    MarketMetadata(market_id="hyperliquid", display_name="Hyperliquid", homepage_url="https://hyperliquid.xyz"),
    MarketMetadata(market_id="myriad_markets", display_name="Myriad Markets", homepage_url="https://myriad.markets"),
    MarketMetadata(market_id="context_v2", display_name="Context V2", homepage_url="https://context.app"),
    MarketMetadata(market_id="frenzy_finance", display_name="Frenzy Finance", homepage_url="https://frenzy.finance"),
    MarketMetadata(market_id="xo_market", display_name="XO Market", homepage_url="https://xomarket.com"),
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
    ),
    MarketMetadata(
        market_id="good_judgment_open",
        display_name="Good Judgment Open",
        homepage_url="https://www.gjopen.com",
    ),
    MarketMetadata(market_id="hypermind", display_name="Hypermind", homepage_url="https://www.hypermind.com"),
    MarketMetadata(
        market_id="iowa_electronic_markets",
        display_name="Iowa Electronic Markets",
        homepage_url="https://iem.uiowa.edu",
    ),
    MarketMetadata(market_id="infer", display_name="INFER / INFER-pub", homepage_url="https://www.infer-pub.com"),
    MarketMetadata(market_id="fact_machine", display_name="Fact Machine", homepage_url="https://factmachine.io"),
    MarketMetadata(market_id="opinion_labs", display_name="Opinion Labs", homepage_url="https://opinionlabs.xyz"),
    MarketMetadata(market_id="gemini_titan", display_name="Gemini Titan / Gemini Predictions", homepage_url="https://www.gemini.com"),
    MarketMetadata(market_id="augur", display_name="Augur", homepage_url="https://augur.net"),
    MarketMetadata(market_id="betmgm", display_name="BetMGM", homepage_url="https://www.betmgm.com"),
    MarketMetadata(market_id="prizepicks", display_name="PrizePicks", homepage_url="https://www.prizepicks.com"),
    MarketMetadata(market_id="underdog_sports", display_name="Underdog Sports", homepage_url="https://underdogfantasy.com"),
    MarketMetadata(market_id="drift_bet", display_name="Drift BET", homepage_url="https://www.drift.trade"),
    MarketMetadata(market_id="thales_market", display_name="Thales Market", homepage_url="https://thalesmarket.io"),
    MarketMetadata(market_id="hedgehog_markets", display_name="Hedgehog Markets", homepage_url="https://hedgehog.markets"),
    MarketMetadata(market_id="omen", display_name="Omen", homepage_url="https://omen.eth.limo"),
    MarketMetadata(market_id="zeitgeist", display_name="Zeitgeist", homepage_url="https://zeitgeist.pm"),
    MarketMetadata(market_id="azuro", display_name="Azuro", homepage_url="https://azuro.org"),
    MarketMetadata(
        market_id="sx_bet",
        display_name="SX Bet / SX Network",
        homepage_url="https://sx.bet",
    ),
    MarketMetadata(
        market_id="limitless_exchange",
        display_name="Limitless Exchange",
        homepage_url="https://limitless.exchange",
    ),
    MarketMetadata(market_id="predict_fun", display_name="Predict.fun", homepage_url="https://predict.fun"),
    MarketMetadata(market_id="smarkets", display_name="Smarkets", homepage_url="https://smarkets.com"),
    MarketMetadata(market_id="betfair_exchange", display_name="Betfair Exchange", homepage_url="https://www.betfair.com/exchange"),
    MarketMetadata(market_id="probo", display_name="Probo", homepage_url="https://probo.in"),
)

MARKET_IDS = tuple(m.market_id for m in MARKET_CATALOG)
_MARKET_BY_ID: Dict[str, MarketMetadata] = {m.market_id: m for m in MARKET_CATALOG}


def get_market_metadata(market_id: str) -> MarketMetadata:
    normalized = str(market_id or "").strip().lower()
    return _MARKET_BY_ID[normalized]
