export type Theme = "light" | "dark";

export interface MarketCapabilities {
  market_discovery: boolean;
  event_listing: boolean;
  price_reading: boolean;
  orderbook_reading: boolean;
  alerts: boolean;
  paper_trading: boolean;
  live_trading: boolean;
  copy_trading: boolean;
  api_required: boolean;
  credentials_required: boolean;
  kyc_required: boolean;
  region_limited: boolean;
}

export interface Market {
  market_id: string;
  display_name: string;
  enabled: boolean;
  default_enabled: boolean;
  homepage_url: string;
  description: string;
  capabilities: MarketCapabilities;
  enabled_capabilities: string[];
  settings: Record<string, unknown>;
  safety: {
    enabled: boolean;
    live_trading_enabled: boolean;
    live_trading_confirmed: boolean;
    live_trading_kill_switch: boolean;
    live_trading_max_size: number | string | null;
    live_trading_max_notional: number | string | null;
  };
  health: {
    market_id: string;
    ok: boolean;
    message: string;
    adapter: string;
    capabilities: MarketCapabilities;
    runtime?: Record<string, unknown>;
    verified_blocker?: boolean;
    credential_requirement?: string;
  };
  status_text: string;
  credential_env_vars: string[];
  credential_sources: Array<{
    name: string;
    source: string;
  }>;
  credential_summary: string;
}

export interface MarketsPayload {
  selected_market_id: string;
  markets: Market[];
  counts: {
    total: number;
    enabled: number;
    implemented: number;
  };
}

export type AlertDirection = "above" | "below";
export type AlertSource = "last_trade" | "midpoint" | "best_bid" | "best_ask";

export interface AlertSourceOption {
  id: AlertSource;
  label: string;
}

export interface AlertPriceValues {
  last_trade: number | null;
  midpoint: number | null;
  best_bid: number | null;
  best_ask: number | null;
}

export interface PriceAlert {
  id: string;
  created_at: number;
  market_id: string;
  token_id: string;
  contract_id: string;
  label: string;
  direction: AlertDirection;
  threshold: number;
  source: AlertSource;
  once: boolean;
  enabled: boolean;
  last_value: number | null;
  triggered: boolean;
  values: AlertPriceValues;
  current_value: number | null;
  status: {
    label: string;
    tone: "good" | "warn" | "neutral";
  };
}

export interface AlertsPayload {
  alerts: PriceAlert[];
  source_options: AlertSourceOption[];
  counts: {
    total: number;
    enabled: number;
    triggered: number;
  };
}

export interface AlertForm {
  market_id: string;
  contract_id: string;
  label: string;
  direction: AlertDirection;
  threshold: string;
  source: AlertSource;
  once: boolean;
  enabled: boolean;
}

export interface AlertRefreshResponse {
  alerts: AlertsPayload;
  message: string;
  refreshed: Array<{
    market_id: string;
    contract_id: string;
    values: AlertPriceValues;
    messages: string[];
    source: string;
  }>;
  problems: string[];
}

export interface WalletWatch {
  id: string;
  wallet: string;
  display_name: string;
  enabled: boolean;
  last_seen_ts: number;
  last_seen_tx: string;
  seen_activity_keys: string[];
  seen_count: number;
  only_market_slug: string;
}

export interface CopyTradePreview {
  status: string;
  reason?: string;
  live?: boolean;
  would_place_order?: boolean;
  blocked?: boolean;
  message?: string;
  order?: {
    market_id: string;
    contract_id: string;
    side: string;
    size: number;
    limit_price: number | null;
    approx_notional: number;
  };
  pricing?: {
    raw_price: number | null;
    best_bid: number | null;
    best_ask: number | null;
    slippage: number;
    capped_by_max_usdc: boolean;
  };
  preflight?: Record<string, unknown>;
}

export interface WalletActivity {
  id: string;
  wallet_id: string;
  wallet: string;
  display_name: string;
  timestamp: number;
  transaction_hash: string;
  proxy_wallet: string;
  side: string;
  asset: string;
  price: number | null;
  size: number | null;
  slug: string;
  outcome: string;
  pseudonym: string;
  raw: Record<string, unknown>;
  copy_preview?: CopyTradePreview;
}

export interface WalletsPayload {
  wallets: WalletWatch[];
  counts: {
    total: number;
    enabled: number;
  };
  polling: {
    mode: string;
    poll_interval_seconds: number;
    last_polled_at: number | null;
    last_message: string;
  };
  recent_activity: WalletActivity[];
}

export interface WalletForm {
  wallet: string;
  display_name: string;
  enabled: boolean;
  only_market_slug: string;
}

export interface WalletPollResponse {
  wallets: WalletsPayload;
  copy: CopyPayload;
  message: string;
  activity: WalletActivity[];
  problems: string[];
  polled_wallets: number;
}

export interface PolymarketUserProfile {
  pseudonym: string;
  proxy_wallet: string;
  profile_image: string;
  display_username_public: boolean;
}

export interface PolymarketUserSearchPayload {
  query: string;
  profiles: PolymarketUserProfile[];
  counts: {
    profiles: number;
  };
  source: string;
}

export type PolymarketLeaderboardSort = "roi_pct" | "pnl_usd" | "volume_usd";

export interface PolymarketLeaderboardFilters {
  sort: PolymarketLeaderboardSort;
  limit: string;
  scan_limit: string;
  min_pnl_usd: string;
  max_pnl_usd: string;
  min_volume_usd: string;
  max_volume_usd: string;
  min_roi_pct: string;
  max_roi_pct: string;
  min_mdd_usd: string;
  max_mdd_usd: string;
  min_mdd_pct: string;
  max_mdd_pct: string;
}

export interface PolymarketLeaderboardRow {
  rank: number;
  wallet: string;
  display_name: string;
  profile_image: string;
  display_username_public: boolean;
  pnl_usd: number | null;
  volume_usd: number | null;
  roi_pct: number | null;
  trade_count: number;
  mdd_usd: number | null;
  mdd_pct: number | null;
  mdd_available: boolean;
  raw: Record<string, unknown>;
}

export interface PolymarketLeaderboardPayload {
  rows: PolymarketLeaderboardRow[];
  counts: {
    returned: number;
    filtered: number;
    scanned: number;
  };
  sort: PolymarketLeaderboardSort;
  direction: "ASC" | "DESC";
  period: string;
  limit: number;
  scan_limit: number;
  source: string;
  source_sort: string;
  ranking_scope: string;
  mdd_available: boolean;
  mdd_note: string;
  warnings: string[];
}

export interface CopySettings {
  enabled: boolean;
  live: boolean;
  follow_wallet: string;
  follow_wallets: string[];
  scale: number;
  copy_percentage: number;
  max_usdc_per_trade: number;
  slippage: number;
  allow_sells: boolean;
  conflict_guard: boolean;
  conflict_window_seconds: number;
}

export interface CopyPayload {
  settings: CopySettings;
  wallet_choices: string[];
  follow_wallet_tracked: boolean;
  follow_wallets_tracked: number;
  follow_wallets_untracked: string[];
  status: string;
  simulation_first: boolean;
  copy_trading_supported: boolean;
  adapter: string;
  live_gate: {
    market_enabled: boolean;
    live_trading_enabled: boolean;
    live_trading_confirmed: boolean;
    live_trading_kill_switch: boolean;
    max_size: number | null;
    max_notional: number | null;
  };
}

export interface CopyForm {
  enabled: boolean;
  live: boolean;
  follow_wallets: string;
  copy_percentage: string;
  max_usdc_per_trade: string;
  slippage: string;
  allow_sells: boolean;
  conflict_guard: boolean;
}

export interface CopyPreviewForm {
  proxyWallet: string;
  asset: string;
  side: string;
  size: string;
  price: string;
  slug: string;
  outcome: string;
}

export interface CopyPreviewPayload {
  preview: CopyTradePreview;
  copy: CopyPayload;
}

export interface LiveSafetyPayload {
  selected_market_id: string;
  market: Market;
  status: string;
  tone: "good" | "warn" | "neutral";
  blockers: string[];
  can_preflight: boolean;
  controls: Market["safety"];
  redaction: {
    sensitive_key_fragments: string[];
    audit_payloads_redacted: boolean;
  };
}

export interface LiveOrderAudit {
  market_id: string;
  contract_id: string;
  side: string;
  size: number;
  limit_price: number | null;
  approx_notional: number;
  metadata_keys: string[];
}

export interface LivePreflightPayload {
  ok: boolean;
  blocked: boolean;
  order: LiveOrderAudit;
  preflight: Record<string, unknown> | null;
  message: string;
  error?: string;
  live_safety: LiveSafetyPayload;
}

export interface ConfigPayload {
  selected_market_id: string;
  theme: Theme;
  alerts: unknown[];
  wallets: unknown[];
  copytrading: {
    enabled: boolean;
    live: boolean;
    follow_wallet: string;
    follow_wallets: string[];
    scale: number;
    copy_percentage: number;
    max_usdc_per_trade: number;
    slippage: number;
    allow_sells: boolean;
    conflict_guard: boolean;
    conflict_window_seconds: number;
  };
}

export interface HealthPayload {
  status: string;
  api_version: string;
  mode: string;
  python_gui_available: boolean;
  python_gui_command: string;
  python_gui_script: string;
  tkinter_fallback: string;
  react_gui: string;
  react_dev_command: string;
  react_dev_manual_command: string;
  react_build_command: string;
  react_prod_command: string;
  config_path: string;
  frontend_dist: string;
  frontend_build_available: boolean;
  routes: Record<string, string[]>;
}

export interface PaperPosition {
  market_id: string;
  contract_id: string;
  net_size: number;
  average_price: number | null;
  notional: number | null;
  trades: number;
  mark_price: number | null;
  mark_source: string;
  marked_at: number | null;
  unrealized: number | null;
}

export interface PaperTrade {
  id: string;
  created_at: number;
  market_id: string;
  contract_id: string;
  side: string;
  size: number;
  limit_price: number | null;
  accepted: boolean;
  message: string;
  filled_size: number;
  average_price: number | null;
  raw: Record<string, unknown>;
}

export interface PaperPayload {
  summary: {
    positions: number;
    gross_size: number;
    entry_notional: number;
    net_notional: number;
    marked: number;
    unrealized: number | null;
    mark_sources: Record<string, number>;
    last_marked_at: number | null;
  };
  positions: PaperPosition[];
  history: PaperTrade[];
  counts: {
    history: number;
    accepted: number;
    rejected: number;
  };
}

export interface PaperOrderForm {
  market_id: string;
  contract_id: string;
  side: string;
  size: string;
  limit_price: string;
}

export interface PaperQuotePayload {
  market_id: string;
  contract_id: string;
  display_name: string;
  best_bid: number | null;
  best_ask: number | null;
  suggested_limits: Record<string, number | null>;
  message: string;
  price: {
    last: number | null;
    bid: number | null;
    ask: number | null;
    midpoint: number | null;
    source: string;
  } | null;
  orderbook: {
    best_bid: number | null;
    best_ask: number | null;
    bids: Array<{ price: number; size: number }>;
    asks: Array<{ price: number; size: number }>;
  } | null;
}

export interface PaperImpactPayload {
  impact: Record<string, number | string | null>;
  message: string;
}

export interface PaperOrderResponse {
  record: PaperTrade;
  result: {
    accepted: boolean;
    message: string;
    filled_size: number;
    average_price: number | null;
  };
  paper: PaperPayload;
}

export interface PaperFormFillPayload {
  market_id: string;
  contract_id: string;
  side: string;
  size: number;
  limit_price: number | null;
  message: string;
}

export interface AppStatePayload {
  health: HealthPayload;
  config: ConfigPayload;
  markets: MarketsPayload;
  alerts: AlertsPayload;
  wallets: WalletsPayload;
  copy: CopyPayload;
  live_safety: LiveSafetyPayload;
  paper: PaperPayload;
}
