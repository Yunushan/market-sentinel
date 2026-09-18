GAMMA_API = "https://gamma-api.polymarket.com"
DATA_API = "https://data-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"
BRIDGE_API = "https://bridge.polymarket.com"
RELAYER_API = "https://relayer-v2.polymarket.com"

# Polymarket's CLOB V2 migration is not backward compatible with legacy
# py-clob-client/V1-signed mutation flows. Normal product mutations remain
# disabled. A separate reviewed capability permits only the bounded funded
# order/immediate-cancel audit used to produce exact-revision evidence.
POLYMARKET_CLOB_V2_MIGRATION_URL = "https://docs.polymarket.com/v2-migration"
POLYMARKET_CLOB_V2_CLIENT_IMPLEMENTED = True
POLYMARKET_LIVE_MUTATIONS_SUPPORTED = False
POLYMARKET_BOUNDED_AUDIT_MUTATIONS_SUPPORTED = True
POLYMARKET_LIVE_MUTATION_BLOCKER = (
    "Polymarket CLOB V2 mutations are disabled until exact-revision credentialed "
    "and funded order/cancel verification is reviewed and promoted; legacy "
    "py-clob-client/V1 order flows must not be used in production."
)
POLYMARKET_BOUNDED_AUDIT_MUTATION_BLOCKER = (
    "The bounded Polymarket CLOB V2 funded audit is available only through its one-shot, "
    "allow-listed, capped post-only GTC order/immediate-cancel capability with durable recovery; "
    "normal product mutation support remains independently disabled."
)

# WebSocket base (append /ws/market or /ws/user)
CLOB_WSS_BASE = "wss://ws-subscriptions-clob.polymarket.com"
SPORTS_WSS_BASE = "wss://sports-api.polymarket.com"
