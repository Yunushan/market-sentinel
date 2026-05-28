from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

from .constants import BRIDGE_API, CLOB_API, DATA_API, GAMMA_API, RELAYER_API


@dataclass(frozen=True)
class PolymarketEndpoint:
    service: str
    method: str
    path: str
    base_url: str
    auth: str = "none"
    max_items: Optional[int] = None
    doc_url: str = ""


DOCS_API_REFERENCE = "https://docs.polymarket.com/api-reference/introduction"

GAMMA_ENDPOINTS: Dict[str, PolymarketEndpoint] = {
    "events_keyset": PolymarketEndpoint("gamma", "GET", "/events/keyset", GAMMA_API, doc_url=DOCS_API_REFERENCE),
    "events": PolymarketEndpoint("gamma", "GET", "/events", GAMMA_API, doc_url=DOCS_API_REFERENCE),
    "event_by_id": PolymarketEndpoint("gamma", "GET", "/events/{id}", GAMMA_API, doc_url=DOCS_API_REFERENCE),
    "event_by_slug": PolymarketEndpoint("gamma", "GET", "/events/slug/{slug}", GAMMA_API, doc_url=DOCS_API_REFERENCE),
    "event_tags": PolymarketEndpoint("gamma", "GET", "/events/{id}/tags", GAMMA_API, doc_url=DOCS_API_REFERENCE),
    "markets_keyset": PolymarketEndpoint("gamma", "GET", "/markets/keyset", GAMMA_API, doc_url=DOCS_API_REFERENCE),
    "markets": PolymarketEndpoint("gamma", "GET", "/markets", GAMMA_API, doc_url=DOCS_API_REFERENCE),
    "market_by_id": PolymarketEndpoint("gamma", "GET", "/markets/{id}", GAMMA_API, doc_url=DOCS_API_REFERENCE),
    "market_by_slug": PolymarketEndpoint("gamma", "GET", "/markets/slug/{slug}", GAMMA_API, doc_url=DOCS_API_REFERENCE),
    "market_tags": PolymarketEndpoint("gamma", "GET", "/markets/{id}/tags", GAMMA_API, doc_url=DOCS_API_REFERENCE),
    "public_profile": PolymarketEndpoint("gamma", "GET", "/public-profile", GAMMA_API, doc_url=DOCS_API_REFERENCE),
    "public_search": PolymarketEndpoint("gamma", "GET", "/public-search", GAMMA_API, doc_url=DOCS_API_REFERENCE),
    "tags": PolymarketEndpoint("gamma", "GET", "/tags", GAMMA_API, doc_url=DOCS_API_REFERENCE),
    "tag_by_id": PolymarketEndpoint("gamma", "GET", "/tags/{id}", GAMMA_API, doc_url=DOCS_API_REFERENCE),
    "tag_by_slug": PolymarketEndpoint("gamma", "GET", "/tags/slug/{slug}", GAMMA_API, doc_url=DOCS_API_REFERENCE),
    "tag_relationships_by_id": PolymarketEndpoint("gamma", "GET", "/tags/{id}/related-tags", GAMMA_API, doc_url=DOCS_API_REFERENCE),
    "tag_relationships_by_slug": PolymarketEndpoint("gamma", "GET", "/tags/slug/{slug}/related-tags", GAMMA_API, doc_url=DOCS_API_REFERENCE),
    "tags_related_to_id": PolymarketEndpoint("gamma", "GET", "/tags/{id}/related-tags/tags", GAMMA_API, doc_url=DOCS_API_REFERENCE),
    "tags_related_to_slug": PolymarketEndpoint("gamma", "GET", "/tags/slug/{slug}/related-tags/tags", GAMMA_API, doc_url=DOCS_API_REFERENCE),
    "series": PolymarketEndpoint("gamma", "GET", "/series", GAMMA_API, doc_url=DOCS_API_REFERENCE),
    "series_by_id": PolymarketEndpoint("gamma", "GET", "/series/{id}", GAMMA_API, doc_url=DOCS_API_REFERENCE),
    "comments": PolymarketEndpoint("gamma", "GET", "/comments", GAMMA_API, doc_url=DOCS_API_REFERENCE),
    "comment_by_id": PolymarketEndpoint("gamma", "GET", "/comments/{id}", GAMMA_API, doc_url=DOCS_API_REFERENCE),
    "comments_by_user": PolymarketEndpoint("gamma", "GET", "/comments/user_address/{address}", GAMMA_API, doc_url=DOCS_API_REFERENCE),
    "sports": PolymarketEndpoint("gamma", "GET", "/sports", GAMMA_API, doc_url=DOCS_API_REFERENCE),
    "sports_market_types": PolymarketEndpoint("gamma", "GET", "/sports/market-types", GAMMA_API, doc_url=DOCS_API_REFERENCE),
    "teams": PolymarketEndpoint("gamma", "GET", "/teams", GAMMA_API, doc_url=DOCS_API_REFERENCE),
}

CLOB_ENDPOINTS: Dict[str, PolymarketEndpoint] = {
    "book": PolymarketEndpoint("clob", "GET", "/book", CLOB_API, doc_url=DOCS_API_REFERENCE),
    "books": PolymarketEndpoint("clob", "POST", "/books", CLOB_API, doc_url="https://docs.polymarket.com/api-reference/market-data/get-order-books-request-body"),
    "price": PolymarketEndpoint("clob", "GET", "/price", CLOB_API, doc_url=DOCS_API_REFERENCE),
    "prices": PolymarketEndpoint("clob", "GET", "/prices", CLOB_API, doc_url=DOCS_API_REFERENCE),
    "prices_body": PolymarketEndpoint("clob", "POST", "/prices", CLOB_API, doc_url="https://docs.polymarket.com/api-reference/market-data/get-market-prices-request-body"),
    "midpoint": PolymarketEndpoint("clob", "GET", "/midpoint", CLOB_API, doc_url=DOCS_API_REFERENCE),
    "midpoints": PolymarketEndpoint("clob", "GET", "/midpoints", CLOB_API, doc_url=DOCS_API_REFERENCE),
    "midpoints_body": PolymarketEndpoint("clob", "POST", "/midpoints", CLOB_API, doc_url="https://docs.polymarket.com/api-reference/market-data/get-midpoint-prices-request-body"),
    "spread": PolymarketEndpoint("clob", "GET", "/spread", CLOB_API, doc_url=DOCS_API_REFERENCE),
    "spreads": PolymarketEndpoint("clob", "POST", "/spreads", CLOB_API, doc_url="https://docs.polymarket.com/api-reference/market-data/get-spreads"),
    "last_trade_price": PolymarketEndpoint("clob", "GET", "/last-trade-price", CLOB_API, doc_url=DOCS_API_REFERENCE),
    "last_trade_prices": PolymarketEndpoint("clob", "GET", "/last-trades-prices", CLOB_API, doc_url=DOCS_API_REFERENCE),
    "last_trade_prices_body": PolymarketEndpoint("clob", "POST", "/last-trades-prices", CLOB_API, doc_url="https://docs.polymarket.com/api-reference/market-data/get-last-trade-prices-request-body"),
    "prices_history": PolymarketEndpoint("clob", "GET", "/prices-history", CLOB_API, doc_url=DOCS_API_REFERENCE),
    "batch_prices_history": PolymarketEndpoint("clob", "POST", "/batch-prices-history", CLOB_API, max_items=20, doc_url="https://docs.polymarket.com/api-reference/markets/get-batch-prices-history"),
    "fee_rate": PolymarketEndpoint("clob", "GET", "/fee-rate", CLOB_API, doc_url=DOCS_API_REFERENCE),
    "fee_rate_token": PolymarketEndpoint("clob", "GET", "/fee-rate/{token_id}", CLOB_API, doc_url=DOCS_API_REFERENCE),
    "tick_size": PolymarketEndpoint("clob", "GET", "/tick-size", CLOB_API, doc_url=DOCS_API_REFERENCE),
    "tick_size_token": PolymarketEndpoint("clob", "GET", "/tick-size/{token_id}", CLOB_API, doc_url=DOCS_API_REFERENCE),
    "clob_market": PolymarketEndpoint("clob", "GET", "/clob-markets/{condition_id}", CLOB_API, doc_url=DOCS_API_REFERENCE),
    "market_by_token": PolymarketEndpoint("clob", "GET", "/markets-by-token/{token_id}", CLOB_API, doc_url=DOCS_API_REFERENCE),
    "time": PolymarketEndpoint("clob", "GET", "/time", CLOB_API, doc_url=DOCS_API_REFERENCE),
    "simplified_markets": PolymarketEndpoint("clob", "GET", "/simplified-markets", CLOB_API, doc_url=DOCS_API_REFERENCE),
    "sampling_markets": PolymarketEndpoint("clob", "GET", "/sampling-markets", CLOB_API, doc_url=DOCS_API_REFERENCE),
    "sampling_simplified_markets": PolymarketEndpoint("clob", "GET", "/sampling-simplified-markets", CLOB_API, doc_url=DOCS_API_REFERENCE),
    "rebates_current": PolymarketEndpoint("clob", "GET", "/rebates/current", CLOB_API, doc_url=DOCS_API_REFERENCE),
    "rewards_current": PolymarketEndpoint("clob", "GET", "/rewards/markets/current", CLOB_API, doc_url=DOCS_API_REFERENCE),
    "rewards_market": PolymarketEndpoint("clob", "GET", "/rewards/markets/{condition_id}", CLOB_API, doc_url=DOCS_API_REFERENCE),
    "rewards_markets": PolymarketEndpoint("clob", "GET", "/rewards/markets", CLOB_API, doc_url=DOCS_API_REFERENCE),
    "builder_trades": PolymarketEndpoint("clob", "GET", "/builder/trades", CLOB_API, doc_url=DOCS_API_REFERENCE),
    "post_order": PolymarketEndpoint("clob", "POST", "/order", CLOB_API, auth="l2", doc_url=DOCS_API_REFERENCE),
    "post_orders": PolymarketEndpoint("clob", "POST", "/orders", CLOB_API, auth="l2", max_items=15, doc_url="https://docs.polymarket.com/api-reference/trade/post-multiple-orders"),
    "cancel_order": PolymarketEndpoint("clob", "DELETE", "/order", CLOB_API, auth="l2", doc_url=DOCS_API_REFERENCE),
    "get_order": PolymarketEndpoint("clob", "GET", "/order/{order_id}", CLOB_API, auth="l2", doc_url=DOCS_API_REFERENCE),
    "get_orders": PolymarketEndpoint("clob", "GET", "/data/orders", CLOB_API, auth="l2", doc_url=DOCS_API_REFERENCE),
    "cancel_orders": PolymarketEndpoint("clob", "DELETE", "/orders", CLOB_API, auth="l2", max_items=3000, doc_url="https://docs.polymarket.com/api-reference/trade/cancel-multiple-orders"),
    "cancel_all": PolymarketEndpoint("clob", "DELETE", "/cancel-all", CLOB_API, auth="l2", doc_url=DOCS_API_REFERENCE),
    "cancel_market_orders": PolymarketEndpoint("clob", "DELETE", "/cancel-market-orders", CLOB_API, auth="l2", doc_url=DOCS_API_REFERENCE),
    "trades": PolymarketEndpoint("clob", "GET", "/trades", CLOB_API, auth="l2", doc_url=DOCS_API_REFERENCE),
    "order_scoring": PolymarketEndpoint("clob", "GET", "/order-scoring", CLOB_API, auth="l2", doc_url=DOCS_API_REFERENCE),
    "heartbeats": PolymarketEndpoint("clob", "POST", "/heartbeats", CLOB_API, auth="l2", doc_url=DOCS_API_REFERENCE),
    "user_rewards": PolymarketEndpoint("clob", "GET", "/rewards/user", CLOB_API, auth="l2", doc_url=DOCS_API_REFERENCE),
    "user_reward_total": PolymarketEndpoint("clob", "GET", "/rewards/user/total", CLOB_API, auth="l2", doc_url=DOCS_API_REFERENCE),
    "user_reward_percentages": PolymarketEndpoint("clob", "GET", "/rewards/user/percentages", CLOB_API, auth="l2", doc_url=DOCS_API_REFERENCE),
    "user_reward_markets": PolymarketEndpoint("clob", "GET", "/rewards/user/markets", CLOB_API, auth="l2", doc_url=DOCS_API_REFERENCE),
}

DATA_ENDPOINTS: Dict[str, PolymarketEndpoint] = {
    "activity": PolymarketEndpoint("data", "GET", "/activity", DATA_API, doc_url=DOCS_API_REFERENCE),
    "positions": PolymarketEndpoint("data", "GET", "/positions", DATA_API, doc_url=DOCS_API_REFERENCE),
    "closed_positions": PolymarketEndpoint("data", "GET", "/closed-positions", DATA_API, doc_url=DOCS_API_REFERENCE),
    "trades": PolymarketEndpoint("data", "GET", "/trades", DATA_API, doc_url=DOCS_API_REFERENCE),
    "leaderboard": PolymarketEndpoint("data", "GET", "/v1/leaderboard", DATA_API, doc_url=DOCS_API_REFERENCE),
    "value": PolymarketEndpoint("data", "GET", "/value", DATA_API, doc_url=DOCS_API_REFERENCE),
    "traded": PolymarketEndpoint("data", "GET", "/traded", DATA_API, doc_url=DOCS_API_REFERENCE),
    "market_positions": PolymarketEndpoint("data", "GET", "/v1/market-positions", DATA_API, doc_url=DOCS_API_REFERENCE),
    "holders": PolymarketEndpoint("data", "GET", "/holders", DATA_API, doc_url=DOCS_API_REFERENCE),
    "oi": PolymarketEndpoint("data", "GET", "/oi", DATA_API, doc_url=DOCS_API_REFERENCE),
    "live_volume": PolymarketEndpoint("data", "GET", "/live-volume", DATA_API, doc_url=DOCS_API_REFERENCE),
    "accounting_snapshot": PolymarketEndpoint("data", "GET", "/v1/accounting/snapshot", DATA_API, doc_url=DOCS_API_REFERENCE),
    "builder_leaderboard": PolymarketEndpoint("data", "GET", "/v1/builders/leaderboard", DATA_API, doc_url=DOCS_API_REFERENCE),
    "builder_volume": PolymarketEndpoint("data", "GET", "/v1/builders/volume", DATA_API, doc_url=DOCS_API_REFERENCE),
}

BRIDGE_ENDPOINTS: Dict[str, PolymarketEndpoint] = {
    "supported_assets": PolymarketEndpoint("bridge", "GET", "/supported-assets", BRIDGE_API, doc_url=DOCS_API_REFERENCE),
    "deposit": PolymarketEndpoint("bridge", "POST", "/deposit", BRIDGE_API, doc_url=DOCS_API_REFERENCE),
    "quote": PolymarketEndpoint("bridge", "POST", "/quote", BRIDGE_API, doc_url=DOCS_API_REFERENCE),
    "status": PolymarketEndpoint("bridge", "GET", "/status/{address}", BRIDGE_API, doc_url=DOCS_API_REFERENCE),
    "withdraw": PolymarketEndpoint("bridge", "POST", "/withdraw", BRIDGE_API, doc_url=DOCS_API_REFERENCE),
}

RELAYER_ENDPOINTS: Dict[str, PolymarketEndpoint] = {
    "submit": PolymarketEndpoint("relayer", "POST", "/submit", RELAYER_API, auth="relayer", doc_url=DOCS_API_REFERENCE),
    "transaction": PolymarketEndpoint("relayer", "GET", "/transaction", RELAYER_API, doc_url=DOCS_API_REFERENCE),
    "transactions": PolymarketEndpoint("relayer", "GET", "/transactions", RELAYER_API, auth="relayer", doc_url=DOCS_API_REFERENCE),
    "nonce": PolymarketEndpoint("relayer", "GET", "/nonce", RELAYER_API, doc_url=DOCS_API_REFERENCE),
    "relay_payload": PolymarketEndpoint("relayer", "GET", "/relay-payload", RELAYER_API, doc_url=DOCS_API_REFERENCE),
    "deployed": PolymarketEndpoint("relayer", "GET", "/deployed", RELAYER_API, doc_url=DOCS_API_REFERENCE),
    "api_keys": PolymarketEndpoint("relayer", "GET", "/relayer/api/keys", RELAYER_API, auth="relayer", doc_url=DOCS_API_REFERENCE),
}

ALL_POLYMARKET_ENDPOINTS: Dict[str, PolymarketEndpoint] = {
    **{f"gamma.{key}": value for key, value in GAMMA_ENDPOINTS.items()},
    **{f"clob.{key}": value for key, value in CLOB_ENDPOINTS.items()},
    **{f"data.{key}": value for key, value in DATA_ENDPOINTS.items()},
    **{f"bridge.{key}": value for key, value in BRIDGE_ENDPOINTS.items()},
    **{f"relayer.{key}": value for key, value in RELAYER_ENDPOINTS.items()},
}
