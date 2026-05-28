import { useEffect, useMemo, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import {
  Activity,
  BarChart3,
  Bell,
  BellRing,
  Copy,
  Database,
  Download,
  Edit3,
  Power,
  Radio,
  RefreshCw,
  Save,
  Search,
  Settings,
  ShieldCheck,
  SlidersHorizontal,
  Trophy,
  Trash2,
  Upload,
  Wallet,
  XCircle
} from "lucide-react";
import {
  createAlert,
  createWallet,
  clearPaperMarks,
  clearSelectedPaperMark,
  clearPaperHistory,
  deleteAlert,
  deletePolymarketLiveValidationReport,
  deleteWallet,
  fillPaperQuoteLimit,
  fetchLiveSafety,
  fetchPolymarketLeaderboard,
  fetchPolymarketLiveValidation,
  fetchPolymarketLiveValidationReports,
  fetchPolymarketMdd,
  fetchPolymarketMddAudit,
  fetchPolymarketMddCache,
  fetchState,
  polymarketMddExportUrl,
  pollWallets,
  previewLivePreflight,
  previewPaperImpact,
  previewCopyTrade,
  purgePolymarketMddCache,
  refreshAlert,
  refreshAlerts,
  refreshPaperMarks,
  refreshPaperQuote,
  refreshSelectedPaperMark,
  searchPolymarketUsers,
  submitPaperOrder,
  storePolymarketLiveValidationReport,
  updateAlert,
  updateCopySettings,
  updateConfig,
  updateMarket,
  updateWallet,
  updateWalletPolling,
  usePaperHistory,
  usePaperPosition
} from "./api";
import type { MarketPatch } from "./api";
import type {
  AlertForm,
  AlertsPayload,
  ConfigPayload,
  CopyForm,
  CopyPayload,
  CopyPreviewForm,
  CopyTradePreview,
  HealthPayload,
  LivePreflightPayload,
  LiveSafetyPayload,
  Market,
  MarketsPayload,
  PaperOrderForm,
  PaperPayload,
  PolymarketLeaderboardFilters,
  PolymarketLeaderboardPayload,
  PolymarketLeaderboardSort,
  PolymarketLiveValidationReportsPayload,
  PolymarketLiveValidationPayload,
  PolymarketMddAuditExport,
  PolymarketMddCachePayload,
  PolymarketMddCachePurgeRequest,
  PolymarketMddForm,
  PolymarketMddPayload,
  PolymarketUserSearchPayload,
  PriceAlert,
  Theme,
  WalletActivity,
  WalletForm,
  WalletsPayload,
  WalletWatch
} from "./types";
import "./styles.css";

type Tab = "overview" | "markets" | "analytics" | "live" | "alerts" | "wallets" | "paper" | "settings";
const TABS: Tab[] = ["overview", "markets", "analytics", "live", "alerts", "wallets", "paper", "settings"];

function isTab(value: string | null): value is Tab {
  return TABS.includes(value as Tab);
}

function initialTabFromUrl(): Tab {
  const value = new URLSearchParams(window.location.search).get("tab");
  return isTab(value) ? value : "overview";
}

function formatNumber(value: number | null | undefined, digits = 4): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "-";
  }
  return value.toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits
  });
}

function formatUsd(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "-";
  }
  return value.toLocaleString(undefined, {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2
  });
}

function formatPercent(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "-";
  }
  return `${value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}%`;
}

function formatTime(seconds: number): string {
  if (!seconds) {
    return "-";
  }
  return new Date(seconds * 1000).toLocaleString();
}

function formatUnknownTime(value: unknown): string {
  const numeric = typeof value === "number" ? value : Number(value);
  return Number.isFinite(numeric) && numeric > 0 ? formatTime(numeric) : "-";
}

function formatUnknownNumber(value: unknown, digits = 4): string {
  const numeric = typeof value === "number" ? value : Number(value);
  return formatNumber(numeric, digits);
}

function formatBytes(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "-";
  }
  if (value < 1024) {
    return `${value} B`;
  }
  if (value < 1024 * 1024) {
    return `${(value / 1024).toLocaleString(undefined, { maximumFractionDigits: 1 })} KB`;
  }
  return `${(value / (1024 * 1024)).toLocaleString(undefined, { maximumFractionDigits: 1 })} MB`;
}

function formatAuditValue(value: unknown): string {
  if (Array.isArray(value)) {
    return value.length ? value.map((item) => String(item)).join(", ") : "-";
  }
  if (value === null || value === undefined || value === "") {
    return "-";
  }
  if (typeof value === "object") {
    return JSON.stringify(value);
  }
  return String(value);
}

function enabledCapabilities(market: Market): string[] {
  return Object.entries(market.capabilities)
    .filter(([, enabled]) => enabled)
    .map(([name]) => name);
}

function credentialRequirementText(market: Market): string {
  if (market.health?.credential_requirement === "live_trading_only") {
    return "credentials: live only";
  }
  return market.capabilities.credentials_required ? "credentials required" : "no credential flag";
}

function StatusPill({ children, tone = "neutral" }: { children: ReactNode; tone?: "good" | "warn" | "neutral" }) {
  return <span className={`status-pill ${tone}`}>{children}</span>;
}

function Metric({ label, value, tone }: { label: string; value: string | number; tone?: "good" | "warn" | "neutral" }) {
  return (
    <div className={`metric ${tone ?? ""}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function validationTone(status: string | undefined): "good" | "warn" | "neutral" {
  if (status === "ok" || status === "passed" || status === "partial") {
    return "good";
  }
  if (status === "blocked" || status === "failed") {
    return "warn";
  }
  return "neutral";
}

function emptyAlertForm(marketId = "polymarket"): AlertForm {
  return {
    market_id: marketId,
    contract_id: "",
    label: "",
    direction: "above",
    threshold: "",
    source: "last_trade",
    once: true,
    enabled: true
  };
}

function alertToForm(alert: PriceAlert): AlertForm {
  return {
    market_id: alert.market_id,
    contract_id: alert.contract_id || alert.token_id,
    label: alert.label,
    direction: alert.direction,
    threshold: String(alert.threshold),
    source: alert.source,
    once: alert.once,
    enabled: alert.enabled
  };
}

function emptyWalletForm(): WalletForm {
  return {
    wallet: "",
    display_name: "",
    enabled: true,
    only_market_slug: ""
  };
}

function walletToForm(wallet: WalletWatch): WalletForm {
  return {
    wallet: wallet.wallet,
    display_name: wallet.display_name,
    enabled: wallet.enabled,
    only_market_slug: wallet.only_market_slug
  };
}

function copyToForm(copy: CopyPayload | null): CopyForm {
  const settings = copy?.settings;
  return {
    enabled: settings?.enabled ?? false,
    live: settings?.live ?? false,
    follow_wallets: (settings?.follow_wallets?.length ? settings.follow_wallets : settings?.follow_wallet ? [settings.follow_wallet] : []).join(", "),
    copy_percentage: String(settings?.copy_percentage ?? (settings?.scale ?? 1) * 100),
    max_usdc_per_trade: String(settings?.max_usdc_per_trade ?? 25),
    slippage: String(settings?.slippage ?? 0.02),
    allow_sells: settings?.allow_sells ?? false,
    conflict_guard: settings?.conflict_guard ?? true
  };
}

function emptyCopyPreviewForm(followWallet = ""): CopyPreviewForm {
  return {
    proxyWallet: followWallet,
    asset: "",
    side: "BUY",
    size: "",
    price: "",
    slug: "",
    outcome: ""
  };
}

function defaultLeaderboardFilters(): PolymarketLeaderboardFilters {
  return {
    sort: "roi_pct",
    direction: "DESC",
    limit: "100",
    scan_limit: "500",
    compute_mdd: false,
    mdd_scan_limit: "100",
    mdd_history_limit: "500",
    mdd_activity_limit: "1000",
    mdd_trade_limit: "1000",
    mdd_open_limit: "500",
    mdd_mode: "fast",
    mdd_mark_replay_token_limit: "10",
    mdd_mark_replay_point_limit: "5000",
    mdd_mark_replay_interval: "1h",
    mdd_mark_replay_fidelity: "60",
    mdd_include_accounting: false,
    mdd_accounting_timeout: "30",
    mdd_persist_cache: false,
    mdd_cache_ttl_seconds: "60",
    equity_base_usd: "",
    min_pnl_usd: "",
    max_pnl_usd: "",
    min_volume_usd: "",
    max_volume_usd: "",
    min_roi_pct: "",
    max_roi_pct: "",
    min_mdd_usd: "",
    max_mdd_usd: "",
    min_mdd_pct: "",
    max_mdd_pct: ""
  };
}

function defaultMddForm(): PolymarketMddForm {
  return {
    wallet: "",
    mode: "fast",
    closed_limit: "500",
    activity_limit: "1000",
    trade_limit: "1000",
    open_limit: "500",
    max_points: "100",
    equity_base_usd: "",
    mark_replay_token_limit: "10",
    mark_replay_interval: "1h",
    mark_replay_fidelity: "60",
    include_accounting_snapshot: false,
    persist_cache: true
  };
}

export default function App() {
  const [tab, setTab] = useState<Tab>(initialTabFromUrl);
  const [health, setHealth] = useState<HealthPayload | null>(null);
  const [config, setConfig] = useState<ConfigPayload | null>(null);
  const [markets, setMarkets] = useState<MarketsPayload | null>(null);
  const [alerts, setAlerts] = useState<AlertsPayload | null>(null);
  const [alertForm, setAlertForm] = useState<AlertForm>({
    market_id: "polymarket",
    contract_id: "",
    label: "",
    direction: "above",
    threshold: "",
    source: "last_trade",
    once: true,
    enabled: true
  });
  const [editingAlertId, setEditingAlertId] = useState<string | null>(null);
  const [alertMessage, setAlertMessage] = useState("");
  const [wallets, setWallets] = useState<WalletsPayload | null>(null);
  const [walletForm, setWalletForm] = useState<WalletForm>(emptyWalletForm());
  const [editingWalletId, setEditingWalletId] = useState<string | null>(null);
  const [walletMessage, setWalletMessage] = useState("");
  const [copyState, setCopyState] = useState<CopyPayload | null>(null);
  const [copyForm, setCopyForm] = useState<CopyForm>(copyToForm(null));
  const [copyPreviewForm, setCopyPreviewForm] = useState<CopyPreviewForm>(emptyCopyPreviewForm());
  const [copyPreview, setCopyPreview] = useState<CopyTradePreview | null>(null);
  const [liveSafety, setLiveSafety] = useState<LiveSafetyPayload | null>(null);
  const [liveValidation, setLiveValidation] = useState<PolymarketLiveValidationPayload | null>(null);
  const [liveValidationReports, setLiveValidationReports] = useState<PolymarketLiveValidationReportsPayload | null>(null);
  const [liveValidationImport, setLiveValidationImport] = useState("");
  const [liveValidationReportMessage, setLiveValidationReportMessage] = useState("");
  const [liveValidationReportBusyKey, setLiveValidationReportBusyKey] = useState<string | null>(null);
  const [livePreflight, setLivePreflight] = useState<LivePreflightPayload | null>(null);
  const [liveMessage, setLiveMessage] = useState("");
  const [paper, setPaper] = useState<PaperPayload | null>(null);
  const [paperForm, setPaperForm] = useState<PaperOrderForm>({
    market_id: "polymarket",
    contract_id: "",
    side: "BUY",
    size: "",
    limit_price: ""
  });
  const [paperMessage, setPaperMessage] = useState("");
  const [marketQuery, setMarketQuery] = useState("");
  const [analyticsLoading, setAnalyticsLoading] = useState(false);
  const [analyticsMessage, setAnalyticsMessage] = useState("");
  const [userSearchQuery, setUserSearchQuery] = useState("");
  const [userSearch, setUserSearch] = useState<PolymarketUserSearchPayload | null>(null);
  const [leaderboardFilters, setLeaderboardFilters] = useState<PolymarketLeaderboardFilters>(defaultLeaderboardFilters());
  const [leaderboard, setLeaderboard] = useState<PolymarketLeaderboardPayload | null>(null);
  const [mddForm, setMddForm] = useState<PolymarketMddForm>(defaultMddForm());
  const [walletMdd, setWalletMdd] = useState<PolymarketMddPayload | null>(null);
  const [mddAuditDetail, setMddAuditDetail] = useState<PolymarketMddAuditExport | null>(null);
  const [mddCache, setMddCache] = useState<PolymarketMddCachePayload | null>(null);
  const [mddCacheBusyKey, setMddCacheBusyKey] = useState<string | null>(null);
  const [busyMarket, setBusyMarket] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  async function loadAll() {
    setLoading(true);
    setError(null);
    try {
      const state = await fetchState();
      setHealth(state.health);
      setConfig(state.config);
      setMarkets(state.markets);
      setAlerts(state.alerts);
      setWallets(state.wallets);
      setCopyState(state.copy);
      setCopyForm(copyToForm(state.copy));
      setCopyPreviewForm((current) => ({ ...current, proxyWallet: current.proxyWallet || state.copy.settings.follow_wallet }));
      setLiveSafety(state.live_safety);
      setLiveValidation(state.polymarket_live_validation);
      setLiveValidationReports(state.polymarket_live_validation_reports);
      setPaper(state.paper);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadAll();
  }, []);

  useEffect(() => {
    if (config?.selected_market_id) {
      setPaperForm((current) => ({ ...current, market_id: current.market_id || config.selected_market_id }));
      setAlertForm((current) => ({ ...current, market_id: current.market_id || config.selected_market_id }));
    }
  }, [config?.selected_market_id]);

  const filteredMarkets = useMemo(() => {
    const all = markets?.markets ?? [];
    const query = marketQuery.trim().toLowerCase();
    if (!query) {
      return all;
    }
    return all.filter((market) => {
      const capabilityText = enabledCapabilities(market).join(" ");
      return `${market.market_id} ${market.display_name} ${market.description} ${capabilityText}`
        .toLowerCase()
        .includes(query);
    });
  }, [marketQuery, markets]);

  const selectedMarket = useMemo(() => {
    if (!markets || !config) {
      return null;
    }
    return markets.markets.find((market) => market.market_id === config.selected_market_id) ?? null;
  }, [config, markets]);

  async function handleMarketToggle(market: Market) {
    setBusyMarket(market.market_id);
    setError(null);
    try {
      const payload = await updateMarket(market.market_id, { enabled: !market.enabled });
      setMarkets(payload);
      if (market.market_id === config?.selected_market_id) {
        setLiveSafety(await fetchLiveSafety());
      }
      if (market.market_id === "polymarket") {
        setLiveValidation(await fetchPolymarketLiveValidation());
      }
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusyMarket(null);
    }
  }

  async function handleMarketSettingsSave(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedMarket) {
      return;
    }
    const form = new FormData(event.currentTarget);
    const patch: MarketPatch = {
      enabled: form.get("enabled") === "on",
      live_trading_enabled: form.get("live_trading_enabled") === "on",
      live_trading_confirmed: form.get("live_trading_confirmed") === "on",
      live_trading_kill_switch: form.get("live_trading_kill_switch") === "on",
      live_trading_max_size: String(form.get("live_trading_max_size") ?? "").trim(),
      live_trading_max_notional: String(form.get("live_trading_max_notional") ?? "").trim()
    };
    setBusyMarket(selectedMarket.market_id);
    setError(null);
    try {
      const payload = await updateMarket(selectedMarket.market_id, patch);
      setMarkets(payload);
      setLiveSafety(await fetchLiveSafety());
      if (selectedMarket.market_id === "polymarket") {
        setLiveValidation(await fetchPolymarketLiveValidation());
      }
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusyMarket(null);
    }
  }

  async function handleSelectedMarketChange(marketId: string) {
    setError(null);
    try {
      const payload = await updateConfig({ selected_market_id: marketId });
      setConfig(payload);
      setPaperForm((current) => ({ ...current, market_id: marketId }));
      setAlertForm((current) => ({ ...current, market_id: marketId }));
      setLivePreflight(null);
      setLiveMessage("");
      setLiveSafety(await fetchLiveSafety());
      setLiveValidation(await fetchPolymarketLiveValidation());
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    }
  }

  async function handleThemeChange(theme: Theme) {
    setError(null);
    try {
      const payload = await updateConfig({ theme });
      setConfig(payload);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    }
  }

  async function handleUserSearch(event?: FormEvent<HTMLFormElement>) {
    event?.preventDefault();
    setError(null);
    setAnalyticsMessage("");
    setAnalyticsLoading(true);
    try {
      const payload = await searchPolymarketUsers(userSearchQuery, 10);
      setUserSearch(payload);
      setAnalyticsMessage(payload.counts.profiles ? `Found ${payload.counts.profiles} profile(s).` : "No matching profiles found.");
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setAnalyticsLoading(false);
    }
  }

  async function handleLeaderboardRefresh(event?: FormEvent<HTMLFormElement>) {
    event?.preventDefault();
    setError(null);
    setAnalyticsMessage("");
    setAnalyticsLoading(true);
    try {
      const payload = await fetchPolymarketLeaderboard(leaderboardFilters);
      setLeaderboard(payload);
      const warning = payload.warnings.length ? ` ${payload.warnings[0]}` : "";
      const cache = payload.analytics_cache.enabled ? ` Audit cache entries: ${payload.analytics_cache.entries}.` : "";
      setAnalyticsMessage(
        `Loaded ${payload.counts.returned} trader row(s) from ${payload.counts.scanned} scanned rows; computed MDD for ${payload.counts.mdd_computed}.${cache}${warning}`
      );
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setAnalyticsLoading(false);
    }
  }

  async function handleMddLookup(event?: FormEvent<HTMLFormElement>) {
    event?.preventDefault();
    setError(null);
    setAnalyticsMessage("");
    setAnalyticsLoading(true);
    try {
      const payload = await fetchPolymarketMdd(mddForm);
      setWalletMdd(payload);
      if (payload.audit_cache?.key) {
        setMddAuditDetail({
          cache: payload.audit_cache,
          payload,
          export: { format: "json", source: "direct_wallet_mdd" }
        });
      }
      const cache = payload.audit_cache?.key ? ` Audit cache key ${payload.audit_cache.key.slice(0, 8)}.` : "";
      setAnalyticsMessage(`Computed wallet MDD ${formatUsd(payload.mdd_usd)} / ${formatPercent(payload.mdd_pct)}.${cache}`);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setAnalyticsLoading(false);
    }
  }

  async function handleAuditDetailLoad(cacheKey: string) {
    setError(null);
    setAnalyticsMessage("");
    setAnalyticsLoading(true);
    try {
      const payload = await fetchPolymarketMddAudit(cacheKey);
      setMddAuditDetail(payload);
      setAnalyticsMessage(`Loaded cached MDD audit ${cacheKey.slice(0, 8)}.`);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setAnalyticsLoading(false);
    }
  }

  async function handleMddCacheRefresh() {
    setError(null);
    setAnalyticsMessage("");
    setAnalyticsLoading(true);
    try {
      const payload = await fetchPolymarketMddCache(true);
      setMddCache(payload);
      setAnalyticsMessage(
        `MDD audit cache has ${payload.counts.entries} artifact(s), ${payload.counts.expired_entries} expired.`
      );
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setAnalyticsLoading(false);
    }
  }

  async function handleMddCachePurge(request: PolymarketMddCachePurgeRequest) {
    if (request.all && !window.confirm("Clear all cached Polymarket MDD audit artifacts?")) {
      return;
    }
    setError(null);
    setAnalyticsMessage("");
    setMddCacheBusyKey(request.key ?? (request.expired_only ? "__expired__" : "__all__"));
    try {
      const payload = await purgePolymarketMddCache(request);
      setMddCache(payload);
      if (payload.deleted_keys?.includes(mddAuditDetail?.cache?.key ?? "")) {
        setMddAuditDetail(null);
      }
      setAnalyticsMessage(payload.message ?? `Purged ${payload.deleted ?? 0} MDD audit artifact(s).`);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setMddCacheBusyKey(null);
    }
  }

  async function handleAlertSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setAlertMessage("");
    try {
      const payload = editingAlertId ? await updateAlert(editingAlertId, alertForm) : await createAlert(alertForm);
      setAlerts(payload);
      setAlertMessage(editingAlertId ? "Alert updated." : "Alert added.");
      setEditingAlertId(null);
      setAlertForm(emptyAlertForm(alertForm.market_id));
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    }
  }

  async function handleAlertAction(action: string, alert?: PriceAlert) {
    setError(null);
    setAlertMessage("");
    try {
      if (action === "edit" && alert) {
        setEditingAlertId(alert.id);
        setAlertForm(alertToForm(alert));
        setAlertMessage(`Editing ${alert.label}.`);
      } else if (action === "cancel-edit") {
        setEditingAlertId(null);
        setAlertForm(emptyAlertForm(config?.selected_market_id ?? "polymarket"));
      } else if (action === "toggle" && alert) {
        const payload = await updateAlert(alert.id, { enabled: !alert.enabled });
        setAlerts(payload);
        setAlertMessage(`${alert.label} ${alert.enabled ? "disabled" : "enabled"}.`);
      } else if (action === "refresh" && alert) {
        const payload = await refreshAlert(alert.id);
        setAlerts(payload.alerts);
        setAlertMessage(payload.message);
      } else if (action === "refresh-all") {
        const payload = await refreshAlerts();
        setAlerts(payload.alerts);
        setAlertMessage(payload.problems.length ? `${payload.message} ${payload.problems.length} problem(s).` : payload.message);
      } else if (action === "delete" && alert) {
        if (!window.confirm(`Delete alert "${alert.label}"?`)) {
          return;
        }
        const payload = await deleteAlert(alert.id);
        setAlerts(payload);
        setAlertMessage(`Deleted ${alert.label}.`);
        if (editingAlertId === alert.id) {
          setEditingAlertId(null);
          setAlertForm(emptyAlertForm(config?.selected_market_id ?? "polymarket"));
        }
      }
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    }
  }

  async function handleWalletSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setWalletMessage("");
    try {
      const payload = editingWalletId ? await updateWallet(editingWalletId, walletForm) : await createWallet(walletForm);
      setWallets(payload);
      setWalletMessage(editingWalletId ? "Wallet watch updated." : "Wallet watch added.");
      setEditingWalletId(null);
      setWalletForm(emptyWalletForm());
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    }
  }

  async function handleWalletAction(action: string, wallet?: WalletWatch) {
    setError(null);
    setWalletMessage("");
    try {
      if (action === "edit" && wallet) {
        setEditingWalletId(wallet.id);
        setWalletForm(walletToForm(wallet));
        setWalletMessage(`Editing ${wallet.display_name || wallet.wallet}.`);
      } else if (action === "cancel-edit") {
        setEditingWalletId(null);
        setWalletForm(emptyWalletForm());
      } else if (action === "toggle" && wallet) {
        const payload = await updateWallet(wallet.id, { wallet: wallet.wallet, enabled: !wallet.enabled });
        setWallets(payload);
        setWalletMessage(`${wallet.display_name || wallet.wallet} ${wallet.enabled ? "disabled" : "enabled"}.`);
      } else if (action === "delete" && wallet) {
        if (!window.confirm(`Delete wallet watch "${wallet.display_name || wallet.wallet}"?`)) {
          return;
        }
        const payload = await deleteWallet(wallet.id);
        setWallets(payload);
        setWalletMessage("Wallet watch deleted.");
      } else if (action === "poll") {
        const payload = await pollWallets();
        setWallets(payload.wallets);
        setCopyState(payload.copy);
        setCopyForm(copyToForm(payload.copy));
        setWalletMessage(payload.problems.length ? `${payload.message} ${payload.problems.length} problem(s).` : payload.message);
      } else if (action === "save-polling") {
        const payload = await updateWalletPolling(wallets?.polling.poll_interval_seconds ?? 10);
        setWallets(payload);
        setWalletMessage(payload.polling.last_message);
      }
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    }
  }

  async function handleCopySubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setWalletMessage("");
    try {
      const payload = await updateCopySettings(copyForm);
      setCopyState(payload);
      setCopyForm(copyToForm(payload));
      setCopyPreviewForm((current) => ({ ...current, proxyWallet: current.proxyWallet || payload.settings.follow_wallet }));
      setWalletMessage("Copy settings saved.");
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    }
  }

  async function handleCopyPreview() {
    setError(null);
    setWalletMessage("");
    try {
      const payload = await previewCopyTrade(copyPreviewForm);
      setCopyState(payload.copy);
      setCopyForm(copyToForm(payload.copy));
      setCopyPreview(payload.preview);
      setWalletMessage(payload.preview.message ?? payload.preview.reason ?? "Copy preview updated.");
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    }
  }

  async function handleClearHistory() {
    if (!window.confirm("Clear all local paper-order history?")) {
      return;
    }
    setError(null);
    try {
      const payload = await clearPaperHistory();
      setPaper(payload);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    }
  }

  async function handleLivePreflight() {
    setError(null);
    setLiveMessage("");
    try {
      const payload = await previewLivePreflight(paperForm);
      setLivePreflight(payload);
      setLiveSafety(payload.live_safety);
      setLiveMessage(payload.message);
      setPaperMessage(payload.message);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    }
  }

  async function handleLiveValidationRefresh() {
    setError(null);
    try {
      setLiveValidation(await fetchPolymarketLiveValidation());
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    }
  }

  async function handleLiveValidationReportsRefresh() {
    setError(null);
    setLiveValidationReportMessage("");
    try {
      setLiveValidationReports(await fetchPolymarketLiveValidationReports());
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    }
  }

  async function handleLiveValidationSnapshotStore() {
    setError(null);
    setLiveValidationReportMessage("");
    setLiveValidationReportBusyKey("store-current");
    try {
      const payload = await storePolymarketLiveValidationReport({ source: "gui_snapshot", label: "GUI readiness snapshot" });
      setLiveValidationReports(payload);
      setLiveValidationReportMessage(payload.message ?? "Stored GUI readiness snapshot.");
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setLiveValidationReportBusyKey(null);
    }
  }

  async function handleLiveValidationReportImport() {
    const reportJson = liveValidationImport.trim();
    if (!reportJson) {
      setLiveValidationReportMessage("Paste a CLI JSON report before importing.");
      return;
    }
    setError(null);
    setLiveValidationReportMessage("");
    setLiveValidationReportBusyKey("import-json");
    try {
      const payload = await storePolymarketLiveValidationReport({
        source: "cli_import",
        label: "CLI import",
        report_json: reportJson
      });
      setLiveValidationReports(payload);
      setLiveValidationImport("");
      setLiveValidationReportMessage(payload.message ?? "Imported CLI validation report.");
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setLiveValidationReportBusyKey(null);
    }
  }

  async function handleLiveValidationReportDelete(key: string) {
    if (!key || !window.confirm("Delete this stored live validation report?")) {
      return;
    }
    setError(null);
    setLiveValidationReportMessage("");
    setLiveValidationReportBusyKey(key);
    try {
      const payload = await deletePolymarketLiveValidationReport(key);
      setLiveValidationReports(payload);
      setLiveValidationReportMessage(payload.message ?? "Deleted live validation report.");
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setLiveValidationReportBusyKey(null);
    }
  }

  async function handlePaperAction(action: string, target?: { market_id: string; contract_id: string; id?: string }) {
    setError(null);
    setPaperMessage("");
    try {
      if (action === "quote") {
        const quote = await refreshPaperQuote(paperForm);
        setPaperMessage(quote.message);
      } else if (action === "quote-limit") {
        const result = await fillPaperQuoteLimit(paperForm);
        setPaperForm((current) => ({ ...current, limit_price: String(result.limit_price) }));
        setPaperMessage(result.message);
      } else if (action === "impact") {
        const result = await previewPaperImpact(paperForm);
        setPaperMessage(result.message);
      } else if (action === "live-preflight") {
        await handleLivePreflight();
      } else if (action === "submit") {
        const result = await submitPaperOrder(paperForm);
        setPaper(result.paper);
        setPaperMessage(result.result.message);
      } else if (action === "refresh-marks") {
        const result = await refreshPaperMarks();
        setPaper(result.paper);
        setPaperMessage(result.message);
      } else if (action === "clear-marks") {
        const result = await clearPaperMarks();
        setPaper(result.paper);
        setPaperMessage(result.message);
      } else if (action === "refresh-selected-mark" && target) {
        const result = await refreshSelectedPaperMark(target.market_id, target.contract_id);
        setPaper(result.paper);
        setPaperMessage(result.message);
      } else if (action === "clear-selected-mark" && target) {
        const result = await clearSelectedPaperMark(target.market_id, target.contract_id);
        setPaper(result.paper);
        setPaperMessage(result.message);
      } else if (action === "use-position" && target) {
        const result = await usePaperPosition(target.market_id, target.contract_id);
        setPaperForm({
          market_id: result.market_id,
          contract_id: result.contract_id,
          side: result.side,
          size: String(result.size),
          limit_price: ""
        });
        setPaperMessage(result.message);
      } else if (action === "use-history" && target?.id) {
        const result = await usePaperHistory(target.id);
        setPaperForm({
          market_id: result.market_id,
          contract_id: result.contract_id,
          side: result.side,
          size: String(result.size),
          limit_price: result.limit_price === null ? "" : String(result.limit_price)
        });
        setPaperMessage(result.message);
      }
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    }
  }

  return (
    <main className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <img src="/polymarket.png" alt="" />
          <div>
            <strong>Market Console</strong>
            <span>Local app</span>
          </div>
        </div>
        <nav className="nav-list" aria-label="Primary">
          <button className={tab === "overview" ? "active" : ""} onClick={() => setTab("overview")}>
            <BarChart3 size={18} /> Overview
          </button>
          <button className={tab === "markets" ? "active" : ""} onClick={() => setTab("markets")}>
            <SlidersHorizontal size={18} /> Markets
          </button>
          <button className={tab === "analytics" ? "active" : ""} onClick={() => setTab("analytics")}>
            <Trophy size={18} /> Analytics
          </button>
          <button className={tab === "live" ? "active" : ""} onClick={() => setTab("live")}>
            <ShieldCheck size={18} /> Live Safety
          </button>
          <button className={tab === "alerts" ? "active" : ""} onClick={() => setTab("alerts")}>
            <Bell size={18} /> Alerts
          </button>
          <button className={tab === "wallets" ? "active" : ""} onClick={() => setTab("wallets")}>
            <Wallet size={18} /> Wallets
          </button>
          <button className={tab === "paper" ? "active" : ""} onClick={() => setTab("paper")}>
            <Activity size={18} /> Paper
          </button>
          <button className={tab === "settings" ? "active" : ""} onClick={() => setTab("settings")}>
            <Settings size={18} /> Settings
          </button>
        </nav>
      </aside>

      <section className="workspace">
        <header className="topbar">
          <div>
            <h1>{tabLabel(tab)}</h1>
            <div className="status-row">
              <StatusPill tone={health?.status === "ok" ? "good" : "warn"}>
                API {health?.status ?? "loading"}
              </StatusPill>
              <StatusPill tone={health?.python_gui_available ? "good" : "neutral"}>
                Python GUI {health?.python_gui_available ? "available" : "unknown"}
              </StatusPill>
              {selectedMarket ? <StatusPill>{selectedMarket.display_name}</StatusPill> : null}
            </div>
          </div>
          <button className="icon-button" onClick={() => void loadAll()} disabled={loading} title="Refresh data">
            <RefreshCw size={18} /> Refresh
          </button>
        </header>

        {error ? <div className="error-banner">{error}</div> : null}

        {tab === "overview" ? (
          <OverviewView alerts={alerts} config={config} copy={copyState} health={health} markets={markets} paper={paper} wallets={wallets} loading={loading} />
        ) : null}
        {tab === "markets" ? (
          <MarketsView
            busyMarket={busyMarket}
            filteredMarkets={filteredMarkets}
            marketQuery={marketQuery}
            markets={markets}
            onQueryChange={setMarketQuery}
            onSelectedMarketChange={(marketId) => void handleSelectedMarketChange(marketId)}
            onSettingsSave={(event) => void handleMarketSettingsSave(event)}
            onToggle={(market) => void handleMarketToggle(market)}
            selectedMarket={selectedMarket}
            selectedMarketId={config?.selected_market_id ?? ""}
          />
        ) : null}
        {tab === "analytics" ? (
          <PolymarketAnalyticsView
            filters={leaderboardFilters}
            loading={analyticsLoading}
            message={analyticsMessage}
            mddAuditDetail={mddAuditDetail}
            mddCache={mddCache}
            mddCacheBusyKey={mddCacheBusyKey}
            mddForm={mddForm}
            mddPayload={walletMdd}
            onAuditDetailLoad={(cacheKey) => void handleAuditDetailLoad(cacheKey)}
            onMddCachePurge={(request) => void handleMddCachePurge(request)}
            onMddCacheRefresh={() => void handleMddCacheRefresh()}
            onFiltersChange={setLeaderboardFilters}
            onLeaderboardRefresh={(event) => void handleLeaderboardRefresh(event)}
            onMddFormChange={setMddForm}
            onMddLookup={(event) => void handleMddLookup(event)}
            onUserSearch={(event) => void handleUserSearch(event)}
            onUserSearchQueryChange={setUserSearchQuery}
            searchQuery={userSearchQuery}
            searchResults={userSearch}
            leaderboard={leaderboard}
          />
        ) : null}
        {tab === "live" ? (
          <LiveSafetyView
            busyMarket={busyMarket}
            form={paperForm}
            livePreflight={livePreflight}
            liveSafety={liveSafety}
            liveValidation={liveValidation}
            liveValidationImport={liveValidationImport}
            liveValidationReportBusyKey={liveValidationReportBusyKey}
            liveValidationReportMessage={liveValidationReportMessage}
            liveValidationReports={liveValidationReports}
            markets={markets}
            message={liveMessage}
            onFormChange={setPaperForm}
            onPreview={() => void handleLivePreflight()}
            onValidationImport={() => void handleLiveValidationReportImport()}
            onValidationImportChange={setLiveValidationImport}
            onValidationReportDelete={(key) => void handleLiveValidationReportDelete(key)}
            onValidationReportsRefresh={() => void handleLiveValidationReportsRefresh()}
            onValidationRefresh={() => void handleLiveValidationRefresh()}
            onValidationSnapshotStore={() => void handleLiveValidationSnapshotStore()}
            onSelectedMarketChange={(marketId) => void handleSelectedMarketChange(marketId)}
            onSettingsSave={(event) => void handleMarketSettingsSave(event)}
            selectedMarket={selectedMarket}
            selectedMarketId={config?.selected_market_id ?? ""}
          />
        ) : null}
        {tab === "paper" ? (
          <PaperView
            form={paperForm}
            markets={markets}
            message={paperMessage}
            onAction={(action, target) => void handlePaperAction(action, target)}
            onClearHistory={() => void handleClearHistory()}
            onFormChange={setPaperForm}
            paper={paper}
          />
        ) : null}
        {tab === "alerts" ? (
          <AlertsView
            alerts={alerts}
            editingAlertId={editingAlertId}
            form={alertForm}
            markets={markets}
            message={alertMessage}
            onAction={(action, alert) => void handleAlertAction(action, alert)}
            onFormChange={setAlertForm}
            onSubmit={(event) => void handleAlertSubmit(event)}
          />
        ) : null}
        {tab === "wallets" ? (
          <WalletsCopyView
            copy={copyState}
            copyForm={copyForm}
            copyPreview={copyPreview}
            copyPreviewForm={copyPreviewForm}
            editingWalletId={editingWalletId}
            form={walletForm}
            message={walletMessage}
            onCopyFormChange={setCopyForm}
            onCopyPreview={() => void handleCopyPreview()}
            onCopyPreviewFormChange={setCopyPreviewForm}
            onCopySubmit={(event) => void handleCopySubmit(event)}
            onPollingIntervalChange={(value) =>
              setWallets((current) =>
                current
                  ? { ...current, polling: { ...current.polling, poll_interval_seconds: value } }
                  : current
              )
            }
            onWalletAction={(action, wallet) => void handleWalletAction(action, wallet)}
            onWalletFormChange={setWalletForm}
            onWalletSubmit={(event) => void handleWalletSubmit(event)}
            wallets={wallets}
          />
        ) : null}
        {tab === "settings" ? (
          <SettingsView
            config={config}
            health={health}
            markets={markets}
            onSelectedMarketChange={(marketId) => void handleSelectedMarketChange(marketId)}
            onThemeChange={(theme) => void handleThemeChange(theme)}
          />
        ) : null}
      </section>
    </main>
  );
}

function tabLabel(tab: Tab): string {
  if (tab === "markets") {
    return "Market Operations";
  }
  if (tab === "analytics") {
    return "Polymarket Analytics";
  }
  if (tab === "live") {
    return "Live Safety";
  }
  if (tab === "alerts") {
    return "Alerts";
  }
  if (tab === "wallets") {
    return "Wallets & Copy";
  }
  if (tab === "paper") {
    return "Paper Trading";
  }
  if (tab === "settings") {
    return "Settings";
  }
  return "Overview";
}

function OverviewView({
  alerts,
  config,
  copy,
  health,
  markets,
  paper,
  wallets,
  loading
}: {
  alerts: AlertsPayload | null;
  config: ConfigPayload | null;
  copy: CopyPayload | null;
  health: HealthPayload | null;
  markets: MarketsPayload | null;
  paper: PaperPayload | null;
  wallets: WalletsPayload | null;
  loading: boolean;
}) {
  return (
    <div className="content-grid">
      <section className="panel span-2">
        <div className="panel-title">
          <ShieldCheck size={18} />
          <h2>Runtime</h2>
        </div>
        <div className="metrics-grid">
          <Metric label="API" value={health?.status ?? (loading ? "loading" : "offline")} tone={health?.status === "ok" ? "good" : "warn"} />
          <Metric label="Selected" value={config?.selected_market_id ?? "-"} />
          <Metric label="Theme" value={config?.theme ?? "-"} />
          <Metric label="API version" value={health?.api_version ?? "-"} />
          <Metric label="Mode" value={health?.mode ?? "parallel"} />
        </div>
      </section>

      <section className="panel">
        <div className="panel-title">
          <SlidersHorizontal size={18} />
          <h2>Markets</h2>
        </div>
        <div className="metrics-grid two">
          <Metric label="Total" value={markets?.counts.total ?? 0} />
          <Metric label="Enabled" value={markets?.counts.enabled ?? 0} tone="good" />
          <Metric label="Implemented" value={markets?.counts.implemented ?? 0} />
        </div>
      </section>

      <section className="panel">
        <div className="panel-title">
          <Activity size={18} />
          <h2>Paper</h2>
        </div>
        <div className="metrics-grid two">
          <Metric label="Positions" value={paper?.summary.positions ?? 0} />
          <Metric label="History" value={paper?.counts.history ?? 0} />
          <Metric label="Accepted" value={paper?.counts.accepted ?? 0} tone="good" />
          <Metric label="Rejected" value={paper?.counts.rejected ?? 0} tone={paper?.counts.rejected ? "warn" : undefined} />
        </div>
      </section>

      <section className="panel">
        <div className="panel-title">
          <BellRing size={18} />
          <h2>Alerts</h2>
        </div>
        <div className="metrics-grid two">
          <Metric label="Total" value={alerts?.counts.total ?? 0} />
          <Metric label="Enabled" value={alerts?.counts.enabled ?? 0} tone="good" />
          <Metric label="Triggered" value={alerts?.counts.triggered ?? 0} tone={alerts?.counts.triggered ? "warn" : undefined} />
        </div>
      </section>

      <section className="panel">
        <div className="panel-title">
          <Wallet size={18} />
          <h2>Wallets</h2>
        </div>
        <div className="metrics-grid two">
          <Metric label="Tracked" value={wallets?.counts.total ?? 0} />
          <Metric label="Enabled" value={wallets?.counts.enabled ?? 0} tone="good" />
          <Metric label="Recent" value={wallets?.recent_activity.length ?? 0} />
          <Metric label="Copy" value={copy?.status ?? "disabled"} tone={copy?.settings.enabled ? "good" : undefined} />
        </div>
      </section>
    </div>
  );
}

function MarketsView({
  busyMarket,
  filteredMarkets,
  marketQuery,
  markets,
  onQueryChange,
  onSelectedMarketChange,
  onSettingsSave,
  onToggle,
  selectedMarket,
  selectedMarketId
}: {
  busyMarket: string | null;
  filteredMarkets: Market[];
  marketQuery: string;
  markets: MarketsPayload | null;
  onQueryChange: (value: string) => void;
  onSelectedMarketChange: (marketId: string) => void;
  onSettingsSave: (event: FormEvent<HTMLFormElement>) => void;
  onToggle: (market: Market) => void;
  selectedMarket: Market | null;
  selectedMarketId: string;
}) {
  return (
    <section className="panel full">
      <div className="toolbar">
        <label className="search-box">
          <Search size={17} />
          <input
            value={marketQuery}
            onChange={(event) => onQueryChange(event.target.value)}
            placeholder="Search markets, ids, capabilities"
          />
        </label>
        <select value={selectedMarketId} onChange={(event) => onSelectedMarketChange(event.target.value)}>
          {(markets?.markets ?? []).map((market) => (
            <option key={market.market_id} value={market.market_id}>
              {market.display_name}
            </option>
          ))}
        </select>
      </div>
      {selectedMarket ? (
        <form className="market-detail" key={selectedMarket.market_id} onSubmit={onSettingsSave}>
          <div className="market-detail-main">
            <div>
              <h2>{selectedMarket.display_name}</h2>
              <p>{selectedMarket.status_text}</p>
            </div>
            <StatusPill tone={selectedMarket.health.ok ? "good" : "warn"}>
              {selectedMarket.health.ok ? "health ok" : "health issue"}
            </StatusPill>
          </div>
          <div className="diagnostic-grid">
            <div>
              <span>Adapter</span>
              <strong>{selectedMarket.health.adapter || "-"}</strong>
            </div>
            <div>
              <span>Message</span>
              <strong>{selectedMarket.health.message || "-"}</strong>
            </div>
            <div>
              <span>Credential sources</span>
              <strong>{selectedMarket.credential_summary}</strong>
            </div>
            <div>
              <span>Credential env vars</span>
              <strong>{selectedMarket.credential_env_vars.length ? selectedMarket.credential_env_vars.join(", ") : "none listed"}</strong>
            </div>
          </div>
          <div className="safety-grid">
            <label className="check-row">
              <input name="enabled" type="checkbox" defaultChecked={selectedMarket.enabled} />
              <span>Market enabled</span>
            </label>
            <label className="check-row">
              <input name="live_trading_enabled" type="checkbox" defaultChecked={selectedMarket.safety.live_trading_enabled} />
              <span>Live enabled</span>
            </label>
            <label className="check-row">
              <input name="live_trading_confirmed" type="checkbox" defaultChecked={selectedMarket.safety.live_trading_confirmed} />
              <span>Live acknowledged</span>
            </label>
            <label className="check-row">
              <input name="live_trading_kill_switch" type="checkbox" defaultChecked={selectedMarket.safety.live_trading_kill_switch} />
              <span>Kill switch</span>
            </label>
            <label>
              <span>Max size</span>
              <input name="live_trading_max_size" defaultValue={selectedMarket.safety.live_trading_max_size ?? ""} />
            </label>
            <label>
              <span>Max notional</span>
              <input name="live_trading_max_notional" defaultValue={selectedMarket.safety.live_trading_max_notional ?? ""} />
            </label>
            <button className="icon-button" type="submit" disabled={busyMarket === selectedMarket.market_id}>
              <Settings size={17} /> Save Market Settings
            </button>
          </div>
        </form>
      ) : null}
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Enabled</th>
              <th>Market</th>
              <th>Capabilities</th>
              <th>Access</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {filteredMarkets.map((market) => {
              const capabilities = enabledCapabilities(market);
              const implemented = capabilities.length > 0;
              return (
                <tr key={market.market_id}>
                  <td>
                    <button
                      className={`switch ${market.enabled ? "on" : ""}`}
                      disabled={busyMarket === market.market_id}
                      onClick={() => onToggle(market)}
                      title={market.enabled ? "Disable market" : "Enable market"}
                    >
                      <span />
                    </button>
                  </td>
                  <td>
                    <strong>{market.display_name}</strong>
                    <small>{market.market_id}</small>
                  </td>
                  <td>
                    <div className="chip-row">
                      {capabilities.slice(0, 5).map((capability) => (
                        <span className="chip" key={capability}>
                          {capability.replace(/_/g, " ")}
                        </span>
                      ))}
                      {capabilities.length > 5 ? <span className="chip muted">+{capabilities.length - 5}</span> : null}
                      {!implemented ? <span className="chip muted">blocked</span> : null}
                    </div>
                  </td>
                  <td>
                    <div className="stacked">
                      <span>{market.capabilities.api_required ? "API required" : "No API flag"}</span>
                      <small>{credentialRequirementText(market)}</small>
                    </div>
                  </td>
                  <td>
                    <StatusPill tone={market.enabled ? "good" : implemented ? "neutral" : "warn"}>
                      {market.enabled ? "enabled" : implemented ? "disabled" : "blocked"}
                    </StatusPill>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function PolymarketAnalyticsView({
  filters,
  loading,
  message,
  mddAuditDetail,
  mddCache,
  mddCacheBusyKey,
  mddForm,
  mddPayload,
  onAuditDetailLoad,
  onMddCachePurge,
  onMddCacheRefresh,
  onFiltersChange,
  onLeaderboardRefresh,
  onMddFormChange,
  onMddLookup,
  onUserSearch,
  onUserSearchQueryChange,
  searchQuery,
  searchResults,
  leaderboard
}: {
  filters: PolymarketLeaderboardFilters;
  loading: boolean;
  message: string;
  mddAuditDetail: PolymarketMddAuditExport | null;
  mddCache: PolymarketMddCachePayload | null;
  mddCacheBusyKey: string | null;
  mddForm: PolymarketMddForm;
  mddPayload: PolymarketMddPayload | null;
  onAuditDetailLoad: (cacheKey: string) => void;
  onMddCachePurge: (request: PolymarketMddCachePurgeRequest) => void;
  onMddCacheRefresh: () => void;
  onFiltersChange: (filters: PolymarketLeaderboardFilters) => void;
  onLeaderboardRefresh: (event: FormEvent<HTMLFormElement>) => void;
  onMddFormChange: (form: PolymarketMddForm) => void;
  onMddLookup: (event: FormEvent<HTMLFormElement>) => void;
  onUserSearch: (event: FormEvent<HTMLFormElement>) => void;
  onUserSearchQueryChange: (value: string) => void;
  searchQuery: string;
  searchResults: PolymarketUserSearchPayload | null;
  leaderboard: PolymarketLeaderboardPayload | null;
}) {
  const updateFilter = <K extends keyof PolymarketLeaderboardFilters>(field: K, value: PolymarketLeaderboardFilters[K]) => {
    onFiltersChange({ ...filters, [field]: value });
  };
  const updateMddForm = <K extends keyof PolymarketMddForm>(field: K, value: PolymarketMddForm[K]) => {
    onMddFormChange({ ...mddForm, [field]: value });
  };
  const auditPayload = mddAuditDetail?.payload ?? null;
  const auditCacheKey = mddAuditDetail?.cache?.key ?? auditPayload?.audit_cache?.key ?? null;
  const auditPoints = auditPayload?.points?.slice(0, 12) ?? [];
  const walletMddCacheKey = mddPayload?.audit_cache?.key ?? null;
  const cacheEntries = mddCache?.entries ?? [];
  const cacheStatus = mddCache?.cache ?? null;

  return (
    <div className="content-grid">
      <section className="panel span-2">
        <div className="panel-title">
          <Search size={18} />
          <h2>User Search</h2>
        </div>
        <form className="analytics-form user-search-form" onSubmit={onUserSearch}>
          <label>
            <span>Search</span>
            <input value={searchQuery} onChange={(event) => onUserSearchQueryChange(event.target.value)} placeholder="Profile, name, wallet" />
          </label>
          <button className="icon-button" type="submit" disabled={loading || !searchQuery.trim()}>
            <Search size={17} /> Search
          </button>
        </form>
        {searchResults ? (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Profile</th>
                  <th>Wallet</th>
                  <th>Public</th>
                  <th>Action</th>
                </tr>
              </thead>
              <tbody>
                {searchResults.profiles.map((profile) => (
                  <tr key={profile.proxy_wallet}>
                    <td>
                      <strong>{profile.pseudonym || "-"}</strong>
                      <small>{profile.profile_image || "-"}</small>
                    </td>
                    <td>{profile.proxy_wallet}</td>
                    <td>
                      <StatusPill tone={profile.display_username_public ? "good" : "neutral"}>
                        {profile.display_username_public ? "yes" : "hidden"}
                      </StatusPill>
                    </td>
                    <td>
                      <button className="icon-button compact" type="button" onClick={() => updateMddForm("wallet", profile.proxy_wallet)}>
                        <Wallet size={14} /> Use
                      </button>
                    </td>
                  </tr>
                ))}
                {!searchResults.profiles.length ? (
                  <tr>
                    <td colSpan={4} className="empty-cell">
                      No matching profiles.
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>
        ) : null}
      </section>

      <section className="panel span-2">
        <div className="panel-title">
          <Wallet size={18} />
          <h2>Direct Wallet MDD</h2>
        </div>
        <form className="analytics-form direct-mdd-form" onSubmit={onMddLookup}>
          <label>
            <span>Wallet</span>
            <input value={mddForm.wallet} onChange={(event) => updateMddForm("wallet", event.target.value)} placeholder="0x..." />
          </label>
          <label>
            <span>MDD mode</span>
            <select value={mddForm.mode} onChange={(event) => updateMddForm("mode", event.target.value as PolymarketMddForm["mode"])}>
              <option value="fast">Fast public curve</option>
              <option value="mark_replay">CLOB mark replay</option>
            </select>
          </label>
          <label>
            <span>Closed</span>
            <input inputMode="numeric" min="1" max="1000" type="number" value={mddForm.closed_limit} onChange={(event) => updateMddForm("closed_limit", event.target.value)} />
          </label>
          <label>
            <span>Activity</span>
            <input inputMode="numeric" min="0" max="5000" type="number" value={mddForm.activity_limit} onChange={(event) => updateMddForm("activity_limit", event.target.value)} />
          </label>
          <label>
            <span>Trades</span>
            <input inputMode="numeric" min="0" max="5000" type="number" value={mddForm.trade_limit} onChange={(event) => updateMddForm("trade_limit", event.target.value)} />
          </label>
          <label>
            <span>Open</span>
            <input inputMode="numeric" min="0" max="1000" type="number" value={mddForm.open_limit} onChange={(event) => updateMddForm("open_limit", event.target.value)} />
          </label>
          <label>
            <span>Points</span>
            <input inputMode="numeric" min="1" max="1000" type="number" value={mddForm.max_points} onChange={(event) => updateMddForm("max_points", event.target.value)} />
          </label>
          <label>
            <span>Equity base</span>
            <input inputMode="decimal" value={mddForm.equity_base_usd} onChange={(event) => updateMddForm("equity_base_usd", event.target.value)} />
          </label>
          <label>
            <span>Replay tokens</span>
            <input
              inputMode="numeric"
              min="1"
              max="20"
              type="number"
              value={mddForm.mark_replay_token_limit}
              onChange={(event) => updateMddForm("mark_replay_token_limit", event.target.value)}
            />
          </label>
          <label>
            <span>Replay interval</span>
            <select value={mddForm.mark_replay_interval} onChange={(event) => updateMddForm("mark_replay_interval", event.target.value)}>
              <option value="1h">1h</option>
              <option value="6h">6h</option>
              <option value="1d">1d</option>
              <option value="1w">1w</option>
              <option value="all">All</option>
              <option value="max">Max</option>
            </select>
          </label>
          <label>
            <span>Replay fidelity</span>
            <input
              inputMode="numeric"
              min="1"
              max="1440"
              type="number"
              value={mddForm.mark_replay_fidelity}
              onChange={(event) => updateMddForm("mark_replay_fidelity", event.target.value)}
            />
          </label>
          <label className="check-row">
            <input
              type="checkbox"
              checked={mddForm.include_accounting_snapshot}
              onChange={(event) => updateMddForm("include_accounting_snapshot", event.target.checked)}
            />
            <span>Accounting base</span>
          </label>
          <label className="check-row">
            <input type="checkbox" checked={mddForm.persist_cache} onChange={(event) => updateMddForm("persist_cache", event.target.checked)} />
            <span>Audit cache</span>
          </label>
          <button className="icon-button" type="submit" disabled={loading || !mddForm.wallet.trim()}>
            <RefreshCw size={17} /> Compute
          </button>
        </form>
        {mddPayload ? (
          <>
            <div className="metrics-grid four">
              <Metric label="MDD USD" value={formatUsd(mddPayload.mdd_usd)} tone={mddPayload.mdd_available ? "warn" : "neutral"} />
              <Metric label="MDD %" value={formatPercent(mddPayload.mdd_pct)} />
              <Metric label="Peak" value={formatUsd(mddPayload.peak_value)} />
              <Metric label="Trough" value={formatUsd(mddPayload.trough_value)} />
            </div>
            <div className="audit-summary">
              <div>
                <span>Method</span>
                <strong>{mddPayload.mdd_method}</strong>
              </div>
              <div>
                <span>Equity base</span>
                <strong>{formatUsd(mddPayload.equity_base_usd)}</strong>
              </div>
              <div>
                <span>Positions</span>
                <strong>
                  {formatAuditValue([`closed ${mddPayload.closed_positions ?? 0}`, `open ${mddPayload.open_positions ?? 0}`])}
                </strong>
              </div>
              <div>
                <span>Cache</span>
                <strong>{walletMddCacheKey || "not stored"}</strong>
              </div>
            </div>
            {mddPayload.rate_limit?.limited ? <div className="info-banner warn">Polymarket rate limit reached; retry after the upstream backoff window.</div> : null}
            {walletMddCacheKey ? (
              <div className="audit-actions panel-actions">
                <button className="icon-button compact" type="button" onClick={() => onAuditDetailLoad(walletMddCacheKey)}>
                  <Database size={14} /> Detail
                </button>
                <a className="icon-button compact" href={polymarketMddExportUrl(walletMddCacheKey, "json")}>
                  <Download size={14} /> JSON
                </a>
                <a className="icon-button compact" href={polymarketMddExportUrl(walletMddCacheKey, "csv")}>
                  <Download size={14} /> CSV
                </a>
              </div>
            ) : null}
          </>
        ) : null}
      </section>

      <section className="panel span-2">
        <div className="panel-header">
          <div className="panel-title">
            <Database size={18} />
            <h2>MDD Audit Cache</h2>
          </div>
          <StatusPill tone={cacheStatus?.exists ? "good" : "neutral"}>{cacheStatus?.exists ? "on disk" : "empty"}</StatusPill>
        </div>
        <div className="metrics-grid four">
          <Metric label="Entries" value={mddCache?.counts.entries ?? cacheStatus?.entries ?? 0} />
          <Metric label="Active" value={mddCache?.counts.active_entries ?? cacheStatus?.active_entries ?? 0} tone="good" />
          <Metric label="Expired" value={mddCache?.counts.expired_entries ?? cacheStatus?.expired_entries ?? 0} tone={(mddCache?.counts.expired_entries ?? 0) ? "warn" : "neutral"} />
          <Metric label="Size" value={formatBytes(cacheStatus?.size_bytes)} />
        </div>
        <div className="audit-summary cache-health">
          <div>
            <span>Path</span>
            <strong>{cacheStatus?.path ?? "-"}</strong>
          </div>
          <div>
            <span>TTL</span>
            <strong>{cacheStatus?.ttl_seconds ? `${cacheStatus.ttl_seconds}s` : "-"}</strong>
          </div>
          <div>
            <span>Max entries</span>
            <strong>{cacheStatus?.max_entries ?? "-"}</strong>
          </div>
          <div>
            <span>Newest</span>
            <strong>{formatUnknownTime(cacheStatus?.newest_stored_at)}</strong>
          </div>
        </div>
        <div className="audit-actions panel-actions cache-actions">
          <button className="icon-button compact" type="button" onClick={onMddCacheRefresh} disabled={loading}>
            <RefreshCw size={14} /> Refresh
          </button>
          <button
            className="icon-button compact"
            type="button"
            onClick={() => onMddCachePurge({ expired_only: true })}
            disabled={mddCacheBusyKey === "__expired__"}
          >
            <Trash2 size={14} /> Purge expired
          </button>
          <button
            className="icon-button compact danger"
            type="button"
            onClick={() => onMddCachePurge({ all: true })}
            disabled={!cacheEntries.length || mddCacheBusyKey === "__all__"}
          >
            <Trash2 size={14} /> Clear all
          </button>
        </div>
        <div className="table-wrap audit-points cache-table">
          <table>
            <thead>
              <tr>
                <th>Stored</th>
                <th>Wallet</th>
                <th className="numeric">MDD USD</th>
                <th className="numeric">MDD %</th>
                <th>Retention</th>
                <th>Status</th>
                <th>Key</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {cacheEntries.map((entry) => {
                const cacheKey = entry.key ?? "";
                return (
                  <tr key={cacheKey || `${entry.wallet}-${entry.stored_at}`}>
                    <td>{formatUnknownTime(entry.stored_at)}</td>
                    <td>{entry.wallet || "-"}</td>
                    <td className="numeric">{formatUsd(entry.mdd_usd)}</td>
                    <td className="numeric">{formatPercent(entry.mdd_pct)}</td>
                    <td>
                      <strong>{entry.ttl_remaining_seconds === null || entry.ttl_remaining_seconds === undefined ? "-" : `${entry.ttl_remaining_seconds}s`}</strong>
                      <small>age {entry.age_seconds === null || entry.age_seconds === undefined ? "-" : `${entry.age_seconds}s`}</small>
                    </td>
                    <td>
                      <StatusPill tone={entry.expired ? "warn" : "good"}>{entry.expired ? "expired" : "active"}</StatusPill>
                    </td>
                    <td className="cache-key">{cacheKey || "-"}</td>
                    <td>
                      {cacheKey ? (
                        <span className="audit-actions">
                          <button className="icon-button compact" type="button" onClick={() => onAuditDetailLoad(cacheKey)}>
                            <Database size={14} /> Detail
                          </button>
                          <a className="icon-button compact" href={polymarketMddExportUrl(cacheKey, "json")}>
                            <Download size={14} /> JSON
                          </a>
                          <a className="icon-button compact" href={polymarketMddExportUrl(cacheKey, "csv")}>
                            <Download size={14} /> CSV
                          </a>
                          <button
                            className="icon-button compact danger"
                            type="button"
                            onClick={() => onMddCachePurge({ key: cacheKey })}
                            disabled={mddCacheBusyKey === cacheKey}
                          >
                            <Trash2 size={14} /> Delete
                          </button>
                        </span>
                      ) : (
                        "-"
                      )}
                    </td>
                  </tr>
                );
              })}
              {!cacheEntries.length ? (
                <tr>
                  <td colSpan={8} className="empty-cell">
                    No cached MDD audit artifacts.
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
      </section>

      <section className="panel span-2">
        <div className="panel-title">
          <Trophy size={18} />
          <h2>Leaderboard</h2>
        </div>
        <form className="analytics-form leaderboard-form" onSubmit={onLeaderboardRefresh}>
          <label>
            <span>Sort</span>
            <select value={filters.sort} onChange={(event) => updateFilter("sort", event.target.value as PolymarketLeaderboardSort)}>
              <option value="roi_pct">ROI %</option>
              <option value="pnl_usd">PnL USD</option>
              <option value="volume_usd">Volume USD</option>
              <option value="mdd_pct">MDD %</option>
              <option value="mdd_usd">MDD USD</option>
            </select>
          </label>
          <label>
            <span>Direction</span>
            <select value={filters.direction} onChange={(event) => updateFilter("direction", event.target.value as PolymarketLeaderboardFilters["direction"])}>
              <option value="DESC">High to low</option>
              <option value="ASC">Low to high</option>
            </select>
          </label>
          <label>
            <span>Returned</span>
            <input
              inputMode="numeric"
              min="1"
              max="100"
              type="number"
              value={filters.limit}
              onChange={(event) => updateFilter("limit", event.target.value)}
            />
          </label>
          <label>
            <span>Scanned</span>
            <input
              inputMode="numeric"
              min="1"
              max="500"
              type="number"
              value={filters.scan_limit}
              onChange={(event) => updateFilter("scan_limit", event.target.value)}
            />
          </label>
          <label className="check-row">
            <input
              type="checkbox"
              checked={filters.compute_mdd}
              onChange={(event) => updateFilter("compute_mdd", event.target.checked)}
            />
            <span>Compute MDD</span>
          </label>
          <label>
            <span>MDD mode</span>
            <select value={filters.mdd_mode} onChange={(event) => updateFilter("mdd_mode", event.target.value as PolymarketLeaderboardFilters["mdd_mode"])}>
              <option value="fast">Fast public curve</option>
              <option value="mark_replay">CLOB mark replay</option>
            </select>
          </label>
          <label>
            <span>MDD scan</span>
            <input
              inputMode="numeric"
              min="1"
              max="100"
              type="number"
              value={filters.mdd_scan_limit}
              onChange={(event) => updateFilter("mdd_scan_limit", event.target.value)}
            />
          </label>
          <label>
            <span>MDD history</span>
            <input
              inputMode="numeric"
              min="1"
              max="1000"
              type="number"
              value={filters.mdd_history_limit}
              onChange={(event) => updateFilter("mdd_history_limit", event.target.value)}
            />
          </label>
          <label>
            <span>MDD activity</span>
            <input
              inputMode="numeric"
              min="0"
              max="5000"
              type="number"
              value={filters.mdd_activity_limit}
              onChange={(event) => updateFilter("mdd_activity_limit", event.target.value)}
            />
          </label>
          <label>
            <span>MDD trades</span>
            <input
              inputMode="numeric"
              min="0"
              max="5000"
              type="number"
              value={filters.mdd_trade_limit}
              onChange={(event) => updateFilter("mdd_trade_limit", event.target.value)}
            />
          </label>
          <label>
            <span>MDD open</span>
            <input
              inputMode="numeric"
              min="0"
              max="1000"
              type="number"
              value={filters.mdd_open_limit}
              onChange={(event) => updateFilter("mdd_open_limit", event.target.value)}
            />
          </label>
          <label>
            <span>Replay tokens</span>
            <input
              inputMode="numeric"
              min="1"
              max="20"
              type="number"
              value={filters.mdd_mark_replay_token_limit}
              onChange={(event) => updateFilter("mdd_mark_replay_token_limit", event.target.value)}
            />
          </label>
          <label>
            <span>Replay fidelity</span>
            <input
              inputMode="numeric"
              min="1"
              max="1440"
              type="number"
              value={filters.mdd_mark_replay_fidelity}
              onChange={(event) => updateFilter("mdd_mark_replay_fidelity", event.target.value)}
            />
          </label>
          <label>
            <span>Replay interval</span>
            <select value={filters.mdd_mark_replay_interval} onChange={(event) => updateFilter("mdd_mark_replay_interval", event.target.value)}>
              <option value="1h">1h</option>
              <option value="6h">6h</option>
              <option value="1d">1d</option>
              <option value="1w">1w</option>
              <option value="all">All</option>
              <option value="max">Max</option>
            </select>
          </label>
          <label className="check-row">
            <input
              type="checkbox"
              checked={filters.mdd_include_accounting}
              onChange={(event) => updateFilter("mdd_include_accounting", event.target.checked)}
            />
            <span>Accounting base</span>
          </label>
          <label className="check-row">
            <input
              type="checkbox"
              checked={filters.mdd_persist_cache}
              onChange={(event) => updateFilter("mdd_persist_cache", event.target.checked)}
            />
            <span>Audit cache</span>
          </label>
          <label>
            <span>Equity base</span>
            <input inputMode="decimal" value={filters.equity_base_usd} onChange={(event) => updateFilter("equity_base_usd", event.target.value)} />
          </label>
          <label>
            <span>Min PnL</span>
            <input inputMode="decimal" value={filters.min_pnl_usd} onChange={(event) => updateFilter("min_pnl_usd", event.target.value)} />
          </label>
          <label>
            <span>Max PnL</span>
            <input inputMode="decimal" value={filters.max_pnl_usd} onChange={(event) => updateFilter("max_pnl_usd", event.target.value)} />
          </label>
          <label>
            <span>Min Volume</span>
            <input inputMode="decimal" value={filters.min_volume_usd} onChange={(event) => updateFilter("min_volume_usd", event.target.value)} />
          </label>
          <label>
            <span>Max Volume</span>
            <input inputMode="decimal" value={filters.max_volume_usd} onChange={(event) => updateFilter("max_volume_usd", event.target.value)} />
          </label>
          <label>
            <span>Min ROI %</span>
            <input inputMode="decimal" value={filters.min_roi_pct} onChange={(event) => updateFilter("min_roi_pct", event.target.value)} />
          </label>
          <label>
            <span>Max ROI %</span>
            <input inputMode="decimal" value={filters.max_roi_pct} onChange={(event) => updateFilter("max_roi_pct", event.target.value)} />
          </label>
          <label>
            <span>Min MDD USD</span>
            <input inputMode="decimal" value={filters.min_mdd_usd} onChange={(event) => updateFilter("min_mdd_usd", event.target.value)} />
          </label>
          <label>
            <span>Max MDD USD</span>
            <input inputMode="decimal" value={filters.max_mdd_usd} onChange={(event) => updateFilter("max_mdd_usd", event.target.value)} />
          </label>
          <label>
            <span>Min MDD %</span>
            <input inputMode="decimal" value={filters.min_mdd_pct} onChange={(event) => updateFilter("min_mdd_pct", event.target.value)} />
          </label>
          <label>
            <span>Max MDD %</span>
            <input inputMode="decimal" value={filters.max_mdd_pct} onChange={(event) => updateFilter("max_mdd_pct", event.target.value)} />
          </label>
          <button className="icon-button" type="submit" disabled={loading}>
            <RefreshCw size={17} /> Load
          </button>
        </form>

        {message ? <div className={`info-banner ${leaderboard?.warnings.length ? "warn" : ""}`}>{message}</div> : null}
        {leaderboard ? (
          <>
            <div className="metrics-grid four">
              <Metric label="Returned" value={leaderboard.counts.returned} tone="good" />
              <Metric label="Filtered" value={leaderboard.counts.filtered} />
              <Metric label="Scanned" value={leaderboard.counts.scanned} />
              <Metric label="MDD computed" value={leaderboard.counts.mdd_computed} />
            </div>
            <div className={`info-banner ${leaderboard.mdd_available ? "" : "warn"}`}>{leaderboard.mdd_note}</div>
            {leaderboard.rate_limit.limited ? <div className="info-banner warn">Polymarket rate limit reached; retry after the upstream backoff window.</div> : null}
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Rank</th>
                    <th>User</th>
                    <th className="numeric">PnL</th>
                    <th className="numeric">Volume</th>
                    <th className="numeric">ROI</th>
                    <th className="numeric">Trades</th>
                    <th className="numeric">MDD USD</th>
                    <th className="numeric">MDD %</th>
                    <th>MDD source</th>
                    <th>Audit</th>
                  </tr>
                </thead>
                <tbody>
                  {leaderboard.rows.map((row) => (
                    <tr key={`${row.rank}-${row.wallet || row.display_name}`}>
                      <td>{row.rank}</td>
                      <td>
                        <strong>{row.display_name}</strong>
                        <small>{row.wallet || "-"}</small>
                      </td>
                      <td className="numeric">{formatUsd(row.pnl_usd)}</td>
                      <td className="numeric">{formatUsd(row.volume_usd)}</td>
                      <td className="numeric">{formatPercent(row.roi_pct)}</td>
                      <td className="numeric">{row.trade_count || "-"}</td>
                      <td className="numeric">{row.mdd_available ? formatUsd(row.mdd_usd) : "-"}</td>
                      <td className="numeric">{row.mdd_available ? formatPercent(row.mdd_pct) : "-"}</td>
                      <td>
                        {row.mdd_available
                          ? row.mdd_accounting_status ?? row.mdd_mark_replay_status ?? (row.mdd_method?.includes("mark") ? "mark replay" : "fast")
                          : "-"}
                      </td>
                      <td>
                        {row.mdd_audit_cache_key ? (
                          <span className="audit-actions">
                            <button className="icon-button compact" type="button" onClick={() => onAuditDetailLoad(row.mdd_audit_cache_key ?? "")}>
                              <Database size={14} /> Detail
                            </button>
                            <a className="icon-button compact" href={polymarketMddExportUrl(row.mdd_audit_cache_key, "json")}>
                              <Download size={14} /> JSON
                            </a>
                            <a className="icon-button compact" href={polymarketMddExportUrl(row.mdd_audit_cache_key, "csv")}>
                              <Download size={14} /> CSV
                            </a>
                          </span>
                        ) : (
                          "-"
                        )}
                      </td>
                    </tr>
                  ))}
                  {!leaderboard.rows.length ? (
                    <tr>
                      <td colSpan={10} className="empty-cell">
                        No leaderboard rows matched the filters.
                      </td>
                    </tr>
                  ) : null}
                </tbody>
              </table>
            </div>
          </>
        ) : null}
      </section>

      {auditPayload ? (
        <section className="panel span-2">
          <div className="panel-header">
            <div className="panel-title">
              <Database size={18} />
              <h2>MDD Audit Detail</h2>
            </div>
            {auditCacheKey ? <StatusPill tone="good">cached</StatusPill> : <StatusPill>direct</StatusPill>}
          </div>
          <div className="metrics-grid four">
            <Metric label="MDD USD" value={formatUsd(auditPayload.mdd_usd)} tone={auditPayload.mdd_available ? "warn" : "neutral"} />
            <Metric label="MDD %" value={formatPercent(auditPayload.mdd_pct)} />
            <Metric label="Equity base" value={formatUsd(auditPayload.equity_base_usd)} />
            <Metric label="Points" value={auditPayload.points_total ?? auditPayload.points?.length ?? 0} />
          </div>
          <div className="audit-summary">
            <div>
              <span>Wallet</span>
              <strong>{auditPayload.wallet || "-"}</strong>
            </div>
            <div>
              <span>Method</span>
              <strong>{auditPayload.mdd_method}</strong>
            </div>
            <div>
              <span>Peak</span>
              <strong>
                {formatUsd(auditPayload.peak_value)} at {formatUnknownTime(auditPayload.peak_timestamp)}
              </strong>
            </div>
            <div>
              <span>Trough</span>
              <strong>
                {formatUsd(auditPayload.trough_value)} at {formatUnknownTime(auditPayload.trough_timestamp)}
              </strong>
            </div>
            <div>
              <span>Closed/open</span>
              <strong>
                {formatAuditValue([`closed ${auditPayload.closed_positions ?? 0}`, `open ${auditPayload.open_positions ?? 0}`])}
              </strong>
            </div>
            <div>
              <span>Activity/trades</span>
              <strong>
                {formatAuditValue([`activity ${auditPayload.activity_events ?? 0}`, `trades ${auditPayload.trade_events ?? 0}`])}
              </strong>
            </div>
            <div>
              <span>Cache key</span>
              <strong>{auditCacheKey || "-"}</strong>
            </div>
            <div>
              <span>Cache path</span>
              <strong>{mddAuditDetail?.cache?.path || auditPayload.audit_cache?.path || "-"}</strong>
            </div>
          </div>
          {auditCacheKey ? (
            <div className="audit-actions panel-actions">
              <a className="icon-button compact" href={polymarketMddExportUrl(auditCacheKey, "json")}>
                <Download size={14} /> JSON
              </a>
              <a className="icon-button compact" href={polymarketMddExportUrl(auditCacheKey, "csv")}>
                <Download size={14} /> CSV
              </a>
            </div>
          ) : null}
          <div className="table-wrap audit-points">
            <table>
              <thead>
                <tr>
                  <th>Time</th>
                  <th className="numeric">Value</th>
                  <th>Source</th>
                </tr>
              </thead>
              <tbody>
                {auditPoints.map((point, index) => (
                  <tr key={`${point.timestamp ?? index}-${index}`}>
                    <td>{formatUnknownTime(point.timestamp)}</td>
                    <td className="numeric">{formatUnknownNumber(point.value, 4)}</td>
                    <td>{point.source ?? point.kind ?? "-"}</td>
                  </tr>
                ))}
                {!auditPoints.length ? (
                  <tr>
                    <td colSpan={3} className="empty-cell">
                      No audit points in this payload.
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>
          <div className="audit-detail-grid">
            <div>
              <span>Assumptions</span>
              <strong>{formatAuditValue(auditPayload.assumptions)}</strong>
            </div>
            <div>
              <span>Limitations</span>
              <strong>{formatAuditValue(auditPayload.limitations)}</strong>
            </div>
          </div>
        </section>
      ) : null}
    </div>
  );
}

function LiveSafetyView({
  busyMarket,
  form,
  livePreflight,
  liveSafety,
  liveValidation,
  liveValidationImport,
  liveValidationReportBusyKey,
  liveValidationReportMessage,
  liveValidationReports,
  markets,
  message,
  onFormChange,
  onPreview,
  onValidationImport,
  onValidationImportChange,
  onValidationReportDelete,
  onValidationReportsRefresh,
  onValidationRefresh,
  onValidationSnapshotStore,
  onSelectedMarketChange,
  onSettingsSave,
  selectedMarket,
  selectedMarketId
}: {
  busyMarket: string | null;
  form: PaperOrderForm;
  livePreflight: LivePreflightPayload | null;
  liveSafety: LiveSafetyPayload | null;
  liveValidation: PolymarketLiveValidationPayload | null;
  liveValidationImport: string;
  liveValidationReportBusyKey: string | null;
  liveValidationReportMessage: string;
  liveValidationReports: PolymarketLiveValidationReportsPayload | null;
  markets: MarketsPayload | null;
  message: string;
  onFormChange: (form: PaperOrderForm) => void;
  onPreview: () => void;
  onValidationImport: () => void;
  onValidationImportChange: (value: string) => void;
  onValidationReportDelete: (key: string) => void;
  onValidationReportsRefresh: () => void;
  onValidationRefresh: () => void;
  onValidationSnapshotStore: () => void;
  onSelectedMarketChange: (marketId: string) => void;
  onSettingsSave: (event: FormEvent<HTMLFormElement>) => void;
  selectedMarket: Market | null;
  selectedMarketId: string;
}) {
  const updateField = (field: keyof PaperOrderForm, value: string) => onFormChange({ ...form, [field]: value });
  const blockers = liveSafety?.blockers ?? [];

  return (
    <div className="content-grid">
      <section className="panel full">
        <div className="toolbar">
          <div className="panel-title">
            <ShieldCheck size={18} />
            <h2>Gate Controls</h2>
          </div>
          <select value={selectedMarketId} onChange={(event) => onSelectedMarketChange(event.target.value)}>
            {(markets?.markets ?? []).map((market) => (
              <option key={market.market_id} value={market.market_id}>
                {market.display_name}
              </option>
            ))}
          </select>
        </div>

        {selectedMarket ? (
          <form className="market-detail" key={`live-${selectedMarket.market_id}`} onSubmit={onSettingsSave}>
            <div className="market-detail-main">
              <div>
                <h2>{selectedMarket.display_name}</h2>
                <p>{selectedMarket.status_text}</p>
              </div>
              <StatusPill tone={liveSafety?.tone ?? "neutral"}>{liveSafety?.status ?? "unknown"}</StatusPill>
            </div>
            <div className="metrics-grid">
              <Metric label="Market" value={selectedMarket.enabled ? "enabled" : "disabled"} tone={selectedMarket.enabled ? "good" : "warn"} />
              <Metric label="Live" value={selectedMarket.safety.live_trading_enabled ? "enabled" : "off"} tone={selectedMarket.safety.live_trading_enabled ? "good" : undefined} />
              <Metric label="Max size" value={String(selectedMarket.safety.live_trading_max_size ?? "-")} />
              <Metric label="Max notional" value={String(selectedMarket.safety.live_trading_max_notional ?? "-")} />
            </div>
            {blockers.length ? (
              <div className="chip-row">
                {blockers.map((blocker) => (
                  <span className="chip muted" key={blocker}>
                    {blocker}
                  </span>
                ))}
              </div>
            ) : null}
            <div className="safety-grid">
              <label className="check-row">
                <input name="enabled" type="checkbox" defaultChecked={selectedMarket.enabled} />
                <span>Market enabled</span>
              </label>
              <label className="check-row">
                <input name="live_trading_enabled" type="checkbox" defaultChecked={selectedMarket.safety.live_trading_enabled} />
                <span>Live enabled</span>
              </label>
              <label className="check-row">
                <input name="live_trading_confirmed" type="checkbox" defaultChecked={selectedMarket.safety.live_trading_confirmed} />
                <span>Live acknowledged</span>
              </label>
              <label className="check-row">
                <input name="live_trading_kill_switch" type="checkbox" defaultChecked={selectedMarket.safety.live_trading_kill_switch} />
                <span>Kill switch</span>
              </label>
              <label>
                <span>Max size</span>
                <input name="live_trading_max_size" defaultValue={selectedMarket.safety.live_trading_max_size ?? ""} />
              </label>
              <label>
                <span>Max notional</span>
                <input name="live_trading_max_notional" defaultValue={selectedMarket.safety.live_trading_max_notional ?? ""} />
              </label>
              <button className="icon-button" type="submit" disabled={busyMarket === selectedMarket.market_id}>
                <Save size={17} /> Save Gate
              </button>
            </div>
          </form>
        ) : null}
      </section>

      <PolymarketLiveValidationPanel
        busyKey={liveValidationReportBusyKey}
        importText={liveValidationImport}
        message={liveValidationReportMessage}
        onDeleteReport={onValidationReportDelete}
        onImport={onValidationImport}
        onImportTextChange={onValidationImportChange}
        onRefresh={onValidationRefresh}
        onReportsRefresh={onValidationReportsRefresh}
        onStoreSnapshot={onValidationSnapshotStore}
        payload={liveValidation}
        reports={liveValidationReports}
      />

      <section className="panel full">
        <div className="panel-title">
          <Database size={18} />
          <h2>Preflight Audit</h2>
        </div>
        <div className="paper-form">
          <label>
            <span>Market</span>
            <select
              value={form.market_id}
              onChange={(event) => {
                updateField("market_id", event.target.value);
                onSelectedMarketChange(event.target.value);
              }}
            >
              {(markets?.markets ?? []).map((market) => (
                <option key={market.market_id} value={market.market_id}>
                  {market.display_name}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>Contract</span>
            <input value={form.contract_id} onChange={(event) => updateField("contract_id", event.target.value)} />
          </label>
          <label>
            <span>Side</span>
            <select value={form.side} onChange={(event) => updateField("side", event.target.value)}>
              <option value="BUY">BUY</option>
              <option value="SELL">SELL</option>
              <option value="BACK">BACK</option>
              <option value="LAY">LAY</option>
            </select>
          </label>
          <label>
            <span>Size</span>
            <input value={form.size} onChange={(event) => updateField("size", event.target.value)} />
          </label>
          <label>
            <span>Limit</span>
            <input value={form.limit_price} onChange={(event) => updateField("limit_price", event.target.value)} />
          </label>
        </div>
        <div className="button-row">
          <button className="icon-button" onClick={onPreview}>
            <ShieldCheck size={17} /> Run Preflight
          </button>
        </div>
        {message ? <div className={`info-banner ${livePreflight?.blocked ? "warn" : ""}`}>{message}</div> : null}
        {livePreflight ? <LivePreflightAudit payload={livePreflight} /> : null}
      </section>
    </div>
  );
}

function PolymarketLiveValidationPanel({
  busyKey,
  importText,
  message,
  onDeleteReport,
  onImport,
  onImportTextChange,
  payload,
  reports,
  onRefresh,
  onReportsRefresh,
  onStoreSnapshot
}: {
  busyKey: string | null;
  importText: string;
  message: string;
  onDeleteReport: (key: string) => void;
  onImport: () => void;
  onImportTextChange: (value: string) => void;
  payload: PolymarketLiveValidationPayload | null;
  reports: PolymarketLiveValidationReportsPayload | null;
  onRefresh: () => void;
  onReportsRefresh: () => void;
  onStoreSnapshot: () => void;
}) {
  const gates = payload?.stage_gates;
  const authChecks = Object.entries(payload?.authenticated_read_checks ?? {});
  const publicChecks = Object.entries(payload?.public_checks ?? {});
  const commands = Object.entries(payload?.operator_commands ?? {});
  const reportEntries = reports?.entries ?? [];
  const comparison = reports?.comparison ?? null;
  return (
    <section className="panel full">
      <div className="toolbar">
        <div className="panel-title">
          <Radio size={18} />
          <h2>Polymarket Live Validation</h2>
        </div>
        <div className="button-row compact">
          <button className="icon-button" onClick={onRefresh}>
            <RefreshCw size={17} /> Refresh
          </button>
          <button className="icon-button" onClick={onStoreSnapshot} disabled={busyKey === "store-current"}>
            <Save size={17} /> Store Snapshot
          </button>
        </div>
      </div>
      {payload ? (
        <>
          <div className="metrics-grid">
            <Metric label="Report" value={formatTime(payload.generated_at)} />
            <Metric label="Mode" value={payload.mode} />
            <Metric
              label="Credential readiness"
              value={gates?.credential_readiness ?? "unknown"}
              tone={validationTone(gates?.credential_readiness)}
            />
            <Metric
              label="Credentialed reads"
              value={gates?.credentialed_read_checks ?? "unknown"}
              tone={validationTone(gates?.credentialed_read_checks)}
            />
            <Metric
              label="Funded exposed"
              value={payload.funded_execution_exposed ? "yes" : "no"}
              tone={payload.funded_execution_exposed ? "warn" : "good"}
            />
            <Metric
              label="Funded gate"
              value={gates?.funded_live_order_check ?? "unknown"}
              tone={validationTone(gates?.funded_live_order_check)}
            />
          </div>
          {gates?.next_step ? <div className="info-banner warn">{gates.next_step}</div> : null}
          <div className="diagnostic-grid">
            <div>
              <span>Public probes</span>
              <strong>{gates?.public_live_checks ?? "unknown"}</strong>
            </div>
            <div>
              <span>Bridge checks</span>
              <strong>{gates?.bridge_address_checks ?? "unknown"}</strong>
            </div>
            <div>
              <span>Authenticated read OK</span>
              <strong>{gates?.credentialed_read_ok ? "yes" : "no"}</strong>
            </div>
            <div>
              <span>Safe funded attempt</span>
              <strong>{gates?.safe_to_attempt_funded_order ? "yes" : "no"}</strong>
            </div>
          </div>
          <div className="split-grid">
            <div className="audit-box">
              <h3>Authenticated Checks</h3>
              <div className="chip-row">
                {authChecks.map(([name, item]) => (
                  <span className={`chip ${item.status === "blocked" || item.status === "failed" ? "muted" : ""}`} key={name}>
                    {name}: {item.status}
                  </span>
                ))}
              </div>
            </div>
            <div className="audit-box">
              <h3>Public Checks</h3>
              <div className="chip-row">
                {publicChecks.map(([name, item]) => (
                  <span className={`chip ${item.status === "blocked" || item.status === "failed" ? "muted" : ""}`} key={name}>
                    {name}: {item.status}
                  </span>
                ))}
              </div>
            </div>
          </div>
          <div className="audit-box">
            <h3>CLI Audit Commands</h3>
            <div className="diagnostic-grid">
              {commands.map(([name, command]) => (
                <div key={name}>
                  <span>{name.replaceAll("_", " ")}</span>
                  <strong>{command}</strong>
                </div>
              ))}
            </div>
          </div>
          <div className="audit-box">
            <div className="toolbar">
              <div className="panel-title">
                <Database size={18} />
                <h2>Validation Reports</h2>
              </div>
              <button className="icon-button" onClick={onReportsRefresh}>
                <RefreshCw size={17} /> Refresh Reports
              </button>
            </div>
            <div className="diagnostic-grid cache-health">
              <div>
                <span>Store</span>
                <strong>{reports?.cache.path ?? "-"}</strong>
              </div>
              <div>
                <span>Reports</span>
                <strong>{reports?.counts.entries ?? 0}</strong>
              </div>
              <div>
                <span>Newest</span>
                <strong>{formatUnknownTime(reports?.cache.newest_stored_at)}</strong>
              </div>
              <div>
                <span>Size</span>
                <strong>{formatBytes(reports?.cache.size_bytes ?? 0)}</strong>
              </div>
            </div>
            {message ? <div className="info-banner">{message}</div> : null}
            <div className="report-import-form">
              <label>
                <span>CLI JSON report import</span>
                <textarea
                  value={importText}
                  onChange={(event) => onImportTextChange(event.target.value)}
                  placeholder='{"stage_gates": {...}}'
                  rows={4}
                />
              </label>
              <button className="icon-button" onClick={onImport} disabled={busyKey === "import-json"}>
                <Upload size={17} /> Import JSON
              </button>
            </div>
            {comparison ? (
              <div className="audit-box">
                <h3>Latest vs Previous</h3>
                {comparison.changed ? (
                  <div className="comparison-list">
                    {comparison.changes.map((change) => (
                      <div key={change.field}>
                        <span>{change.field.replaceAll("_", " ")}</span>
                        <strong>
                          {formatAuditValue(change.previous)} {"->"} {formatAuditValue(change.latest)}
                        </strong>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="empty-state compact">No stage-gate changes between the latest two reports.</div>
                )}
              </div>
            ) : null}
            <div className="report-list">
              {reportEntries.length ? (
                reportEntries.map((entry) => (
                  <div className="report-row" key={entry.key ?? `${entry.source}-${entry.stored_at}`}>
                    <div>
                      <strong>{entry.label || entry.source}</strong>
                      <span>
                        {entry.source} | stored {formatUnknownTime(entry.stored_at)} | generated{" "}
                        {formatUnknownTime(entry.summary?.generated_at)}
                      </span>
                    </div>
                    <div className="chip-row">
                      <span className={`chip ${entry.summary?.credential_readiness === "passed" ? "" : "muted"}`}>
                        credentials: {entry.summary?.credential_readiness ?? "unknown"}
                      </span>
                      <span className={`chip ${entry.summary?.credentialed_read_checks === "passed" ? "" : "muted"}`}>
                        reads: {entry.summary?.credentialed_read_checks ?? "unknown"}
                      </span>
                      <span className={`chip ${entry.summary?.funded_live_order_check === "passed" ? "" : "muted"}`}>
                        funded: {entry.summary?.funded_live_order_check ?? "unknown"}
                      </span>
                    </div>
                    <button
                      className="icon-button"
                      onClick={() => entry.key && onDeleteReport(entry.key)}
                      disabled={!entry.key || busyKey === entry.key}
                    >
                      <Trash2 size={16} /> Delete
                    </button>
                  </div>
                ))
              ) : (
                <div className="empty-state compact">No stored validation reports yet.</div>
              )}
            </div>
          </div>
        </>
      ) : (
        <div className="empty-state">No live validation report loaded.</div>
      )}
    </section>
  );
}

function LivePreflightAudit({ payload }: { payload: LivePreflightPayload }) {
  const preflight = payload.preflight;
  return (
    <div className="audit-box">
      <div className="diagnostic-grid">
        <div>
          <span>Result</span>
          <strong>{payload.blocked ? "blocked" : "passed"}</strong>
        </div>
        <div>
          <span>Market</span>
          <strong>{payload.order.market_id}</strong>
        </div>
        <div>
          <span>Contract</span>
          <strong>{payload.order.contract_id}</strong>
        </div>
        <div>
          <span>Order</span>
          <strong>
            {payload.order.side} {formatNumber(payload.order.size, 4)} @ {formatNumber(payload.order.limit_price, 4)}
          </strong>
        </div>
        <div>
          <span>Notional</span>
          <strong>{formatNumber(payload.order.approx_notional, 4)}</strong>
        </div>
        <div>
          <span>Gate</span>
          <strong>{payload.live_safety.status}</strong>
        </div>
        <div>
          <span>Metadata keys</span>
          <strong>{payload.order.metadata_keys.length ? payload.order.metadata_keys.join(", ") : "-"}</strong>
        </div>
        <div>
          <span>Redaction</span>
          <strong>{payload.live_safety.redaction.audit_payloads_redacted ? "enabled" : "unknown"}</strong>
        </div>
      </div>
      {preflight ? (
        <div className="diagnostic-grid">
          <div>
            <span>Adapter</span>
            <strong>{formatAuditValue(preflight.display_name)}</strong>
          </div>
          <div>
            <span>Feature</span>
            <strong>{formatAuditValue(preflight.feature)}</strong>
          </div>
          <div>
            <span>Max size</span>
            <strong>{formatAuditValue(preflight.max_size)}</strong>
          </div>
          <div>
            <span>Max notional</span>
            <strong>{formatAuditValue(preflight.max_notional)}</strong>
          </div>
          <div>
            <span>Warnings</span>
            <strong>{formatAuditValue(preflight.warnings)}</strong>
          </div>
          <div>
            <span>Credentials</span>
            <strong>{formatAuditValue(preflight.requires_credentials)}</strong>
          </div>
          <div>
            <span>KYC</span>
            <strong>{formatAuditValue(preflight.requires_kyc)}</strong>
          </div>
          <div>
            <span>Region limited</span>
            <strong>{formatAuditValue(preflight.region_limited)}</strong>
          </div>
        </div>
      ) : null}
    </div>
  );
}

function AlertsView({
  alerts,
  editingAlertId,
  form,
  markets,
  message,
  onAction,
  onFormChange,
  onSubmit
}: {
  alerts: AlertsPayload | null;
  editingAlertId: string | null;
  form: AlertForm;
  markets: MarketsPayload | null;
  message: string;
  onAction: (action: string, alert?: PriceAlert) => void;
  onFormChange: (form: AlertForm) => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
}) {
  const alertMarkets = (markets?.markets ?? []).filter((market) => market.capabilities.alerts);
  const sourceOptions = alerts?.source_options ?? [
    { id: "last_trade" as const, label: "Last trade" },
    { id: "midpoint" as const, label: "Midpoint" },
    { id: "best_bid" as const, label: "Best bid" },
    { id: "best_ask" as const, label: "Best ask" }
  ];
  return (
    <section className="panel full">
      <form className="alert-form" onSubmit={onSubmit}>
        <label>
          <span>Market</span>
          <select value={form.market_id} onChange={(event) => onFormChange({ ...form, market_id: event.target.value })}>
            {alertMarkets.map((market) => (
              <option key={market.market_id} value={market.market_id}>
                {market.display_name}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>Contract/token ID</span>
          <input
            value={form.contract_id}
            onChange={(event) => onFormChange({ ...form, contract_id: event.target.value })}
            placeholder="Contract or token id"
          />
        </label>
        <label>
          <span>Label</span>
          <input value={form.label} onChange={(event) => onFormChange({ ...form, label: event.target.value })} placeholder="Alert label" />
        </label>
        <label>
          <span>Direction</span>
          <select value={form.direction} onChange={(event) => onFormChange({ ...form, direction: event.target.value as AlertForm["direction"] })}>
            <option value="above">Above</option>
            <option value="below">Below</option>
          </select>
        </label>
        <label>
          <span>Source</span>
          <select value={form.source} onChange={(event) => onFormChange({ ...form, source: event.target.value as AlertForm["source"] })}>
            {sourceOptions.map((source) => (
              <option key={source.id} value={source.id}>
                {source.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>Threshold</span>
          <input
            inputMode="decimal"
            value={form.threshold}
            onChange={(event) => onFormChange({ ...form, threshold: event.target.value })}
            placeholder="0.50"
          />
        </label>
        <label className="check-row">
          <input type="checkbox" checked={form.once} onChange={(event) => onFormChange({ ...form, once: event.target.checked })} />
          <span>Trigger once</span>
        </label>
        <label className="check-row">
          <input type="checkbox" checked={form.enabled} onChange={(event) => onFormChange({ ...form, enabled: event.target.checked })} />
          <span>Enabled</span>
        </label>
        <div className="button-row">
          <button className="icon-button" type="submit">
            <Save size={17} /> {editingAlertId ? "Save Alert" : "Add Alert"}
          </button>
          {editingAlertId ? (
            <button className="secondary-button" type="button" onClick={() => onAction("cancel-edit")}>
              <XCircle size={17} /> Cancel
            </button>
          ) : null}
          <button className="secondary-button" type="button" onClick={() => onAction("refresh-all")} disabled={!alerts?.alerts.length}>
            <RefreshCw size={17} /> Refresh Prices
          </button>
        </div>
      </form>
      {message ? <div className="info-banner">{message}</div> : null}
      <div className="metrics-grid four">
        <Metric label="Alerts" value={alerts?.counts.total ?? 0} />
        <Metric label="Enabled" value={alerts?.counts.enabled ?? 0} tone="good" />
        <Metric label="Triggered" value={alerts?.counts.triggered ?? 0} tone={alerts?.counts.triggered ? "warn" : undefined} />
        <Metric label="Alert markets" value={alertMarkets.length} />
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Status</th>
              <th>Alert</th>
              <th>Trigger</th>
              <th>Current Price State</th>
              <th>Created</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {(alerts?.alerts ?? []).map((alert) => (
              <tr key={alert.id}>
                <td>
                  <StatusPill tone={alert.status.tone}>{alert.status.label}</StatusPill>
                </td>
                <td>
                  <strong>{alert.label}</strong>
                  <small>
                    {alert.market_id}:{alert.contract_id}
                  </small>
                </td>
                <td>
                  <div className="stacked">
                    <span>
                      {alert.source.replace(/_/g, " ")} {alert.direction} {formatNumber(alert.threshold, 4)}
                    </span>
                    <small>{alert.once ? "once" : "repeat"} / {alert.enabled ? "enabled" : "disabled"}</small>
                  </div>
                </td>
                <td>
                  <div className="stacked">
                    <span>{formatNumber(alert.current_value, 4)}</span>
                    <small>
                      last {formatNumber(alert.values.last_trade, 4)} / mid {formatNumber(alert.values.midpoint, 4)} / bid{" "}
                      {formatNumber(alert.values.best_bid, 4)} / ask {formatNumber(alert.values.best_ask, 4)}
                    </small>
                  </div>
                </td>
                <td>{formatTime(alert.created_at)}</td>
                <td>
                  <div className="row-actions">
                    <button className="secondary-button" onClick={() => onAction("refresh", alert)} title="Refresh current price">
                      <RefreshCw size={16} />
                    </button>
                    <button className="secondary-button" onClick={() => onAction("edit", alert)} title="Edit alert">
                      <Edit3 size={16} />
                    </button>
                    <button className="secondary-button" onClick={() => onAction("toggle", alert)} title={alert.enabled ? "Disable alert" : "Enable alert"}>
                      <Power size={16} />
                    </button>
                    <button className="danger-button" onClick={() => onAction("delete", alert)} title="Delete alert">
                      <Trash2 size={16} />
                    </button>
                  </div>
                </td>
              </tr>
            ))}
            {!alerts?.alerts.length ? (
              <tr>
                <td colSpan={6}>
                  <span className="muted-text">No price alerts configured.</span>
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function WalletsCopyView({
  copy,
  copyForm,
  copyPreview,
  copyPreviewForm,
  editingWalletId,
  form,
  message,
  onCopyFormChange,
  onCopyPreview,
  onCopyPreviewFormChange,
  onCopySubmit,
  onPollingIntervalChange,
  onWalletAction,
  onWalletFormChange,
  onWalletSubmit,
  wallets
}: {
  copy: CopyPayload | null;
  copyForm: CopyForm;
  copyPreview: CopyTradePreview | null;
  copyPreviewForm: CopyPreviewForm;
  editingWalletId: string | null;
  form: WalletForm;
  message: string;
  onCopyFormChange: (form: CopyForm) => void;
  onCopyPreview: () => void;
  onCopyPreviewFormChange: (form: CopyPreviewForm) => void;
  onCopySubmit: (event: FormEvent<HTMLFormElement>) => void;
  onPollingIntervalChange: (value: number) => void;
  onWalletAction: (action: string, wallet?: WalletWatch) => void;
  onWalletFormChange: (form: WalletForm) => void;
  onWalletSubmit: (event: FormEvent<HTMLFormElement>) => void;
  wallets: WalletsPayload | null;
}) {
  return (
    <div className="content-grid">
      <section className="panel span-2">
        <div className="panel-title">
          <Wallet size={18} />
          <h2>Wallet Tracking</h2>
        </div>
        <form className="wallet-form" onSubmit={onWalletSubmit}>
          <label>
            <span>Wallet</span>
            <input value={form.wallet} onChange={(event) => onWalletFormChange({ ...form, wallet: event.target.value })} placeholder="0x..." />
          </label>
          <label>
            <span>Name</span>
            <input
              value={form.display_name}
              onChange={(event) => onWalletFormChange({ ...form, display_name: event.target.value })}
              placeholder="Display name"
            />
          </label>
          <label>
            <span>Market slug filter</span>
            <input
              value={form.only_market_slug}
              onChange={(event) => onWalletFormChange({ ...form, only_market_slug: event.target.value })}
              placeholder="Optional"
            />
          </label>
          <label className="check-row">
            <input type="checkbox" checked={form.enabled} onChange={(event) => onWalletFormChange({ ...form, enabled: event.target.checked })} />
            <span>Enabled</span>
          </label>
          <div className="button-row">
            <button className="icon-button" type="submit">
              <Save size={17} /> {editingWalletId ? "Save Wallet" : "Add Wallet"}
            </button>
            {editingWalletId ? (
              <button className="secondary-button" type="button" onClick={() => onWalletAction("cancel-edit")}>
                <XCircle size={17} /> Cancel
              </button>
            ) : null}
          </div>
        </form>
        <div className="toolbar">
          <div className="metrics-grid two compact-metrics">
            <Metric label="Tracked" value={wallets?.counts.total ?? 0} />
            <Metric label="Enabled" value={wallets?.counts.enabled ?? 0} tone="good" />
          </div>
          <div className="button-row compact">
            <label className="inline-field">
              <span>Interval</span>
              <input
                inputMode="numeric"
                value={wallets?.polling.poll_interval_seconds ?? 10}
                onChange={(event) => onPollingIntervalChange(Number(event.target.value) || 10)}
              />
            </label>
            <button className="secondary-button" onClick={() => onWalletAction("save-polling")}>
              <Save size={16} /> Save
            </button>
            <button className="icon-button" onClick={() => onWalletAction("poll")} disabled={!wallets?.counts.enabled}>
              <Radio size={17} /> Poll Now
            </button>
          </div>
        </div>
        {message ? <div className="info-banner">{message}</div> : null}
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Status</th>
                <th>Wallet</th>
                <th>Last Seen</th>
                <th>Filter</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {(wallets?.wallets ?? []).map((wallet) => (
                <tr key={wallet.id}>
                  <td>
                    <StatusPill tone={wallet.enabled ? "good" : "neutral"}>{wallet.enabled ? "enabled" : "disabled"}</StatusPill>
                  </td>
                  <td>
                    <strong>{wallet.display_name || wallet.wallet}</strong>
                    <small>{wallet.wallet}</small>
                  </td>
                  <td>
                    <div className="stacked">
                      <span>{formatTime(wallet.last_seen_ts)}</span>
                      <small>{wallet.seen_count} seen</small>
                    </div>
                  </td>
                  <td>{wallet.only_market_slug || "-"}</td>
                  <td>
                    <div className="row-actions">
                      <button className="secondary-button" onClick={() => onWalletAction("edit", wallet)} title="Edit wallet">
                        <Edit3 size={16} />
                      </button>
                      <button className="secondary-button" onClick={() => onWalletAction("toggle", wallet)} title={wallet.enabled ? "Disable wallet" : "Enable wallet"}>
                        <Power size={16} />
                      </button>
                      <button className="danger-button" onClick={() => onWalletAction("delete", wallet)} title="Delete wallet">
                        <Trash2 size={16} />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
              {!wallets?.wallets.length ? (
                <tr>
                  <td colSpan={5}>
                    <span className="muted-text">No wallet watches configured.</span>
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
      </section>

      <section className="panel">
        <div className="panel-title">
          <Copy size={18} />
          <h2>Copy Settings</h2>
        </div>
        <form className="copy-form" onSubmit={onCopySubmit}>
          <label className="check-row">
            <input type="checkbox" checked={copyForm.enabled} onChange={(event) => onCopyFormChange({ ...copyForm, enabled: event.target.checked })} />
            <span>Enabled</span>
          </label>
          <label className="check-row">
            <input type="checkbox" checked={copyForm.live} onChange={(event) => onCopyFormChange({ ...copyForm, live: event.target.checked })} />
            <span>Live mode</span>
          </label>
          <label>
            <span>Follow wallets</span>
            <input
              list="wallet-choices"
              value={copyForm.follow_wallets}
              onChange={(event) => onCopyFormChange({ ...copyForm, follow_wallets: event.target.value })}
              placeholder="0x..., 0x..."
            />
            <datalist id="wallet-choices">
              {(copy?.wallet_choices ?? []).map((wallet) => (
                <option key={wallet} value={wallet} />
              ))}
            </datalist>
          </label>
          <label>
            <span>Copy %</span>
            <input
              type="number"
              inputMode="decimal"
              min="0"
              max="100"
              step="0.01"
              value={copyForm.copy_percentage}
              onChange={(event) => onCopyFormChange({ ...copyForm, copy_percentage: event.target.value })}
            />
          </label>
          <label>
            <span>Max USDC</span>
            <input
              inputMode="decimal"
              value={copyForm.max_usdc_per_trade}
              onChange={(event) => onCopyFormChange({ ...copyForm, max_usdc_per_trade: event.target.value })}
            />
          </label>
          <label>
            <span>Slippage</span>
            <input inputMode="decimal" value={copyForm.slippage} onChange={(event) => onCopyFormChange({ ...copyForm, slippage: event.target.value })} />
          </label>
          <label className="check-row">
            <input
              type="checkbox"
              checked={copyForm.allow_sells}
              onChange={(event) => onCopyFormChange({ ...copyForm, allow_sells: event.target.checked })}
            />
            <span>Allow sells</span>
          </label>
          <label className="check-row">
            <input
              type="checkbox"
              checked={copyForm.conflict_guard}
              onChange={(event) => onCopyFormChange({ ...copyForm, conflict_guard: event.target.checked })}
            />
            <span>Conflict guard</span>
          </label>
          <button className="icon-button" type="submit">
            <Save size={17} /> Save Copy Settings
          </button>
        </form>
        <div className="metrics-grid two">
          <Metric label="Mode" value={copy?.status ?? "disabled"} tone={copy?.settings.enabled ? "good" : undefined} />
          <Metric label="Supported" value={copy?.copy_trading_supported ? "yes" : "no"} tone={copy?.copy_trading_supported ? "good" : "warn"} />
          <Metric label="Tracked follows" value={copy?.follow_wallets_tracked ?? 0} tone={copy?.follow_wallet_tracked ? "good" : undefined} />
          <Metric label="Kill switch" value={copy?.live_gate.live_trading_kill_switch ? "on" : "off"} tone={copy?.live_gate.live_trading_kill_switch ? "warn" : undefined} />
        </div>
      </section>

      <section className="panel">
        <div className="panel-title">
          <ShieldCheck size={18} />
          <h2>Live Preflight</h2>
        </div>
        <div className="copy-form">
          <label>
            <span>Proxy wallet</span>
            <input value={copyPreviewForm.proxyWallet} onChange={(event) => onCopyPreviewFormChange({ ...copyPreviewForm, proxyWallet: event.target.value })} />
          </label>
          <label>
            <span>Token</span>
            <input value={copyPreviewForm.asset} onChange={(event) => onCopyPreviewFormChange({ ...copyPreviewForm, asset: event.target.value })} />
          </label>
          <label>
            <span>Side</span>
            <select value={copyPreviewForm.side} onChange={(event) => onCopyPreviewFormChange({ ...copyPreviewForm, side: event.target.value })}>
              <option value="BUY">BUY</option>
              <option value="SELL">SELL</option>
            </select>
          </label>
          <label>
            <span>Size</span>
            <input inputMode="decimal" value={copyPreviewForm.size} onChange={(event) => onCopyPreviewFormChange({ ...copyPreviewForm, size: event.target.value })} />
          </label>
          <label>
            <span>Price</span>
            <input inputMode="decimal" value={copyPreviewForm.price} onChange={(event) => onCopyPreviewFormChange({ ...copyPreviewForm, price: event.target.value })} />
          </label>
          <button className="icon-button" type="button" onClick={onCopyPreview}>
            <ShieldCheck size={17} /> Preview
          </button>
        </div>
        <div className="diagnostic-grid">
          <div>
            <span>Live enabled</span>
            <strong>{copy?.live_gate.live_trading_enabled ? "yes" : "no"}</strong>
          </div>
          <div>
            <span>Confirmed</span>
            <strong>{copy?.live_gate.live_trading_confirmed ? "yes" : "no"}</strong>
          </div>
          <div>
            <span>Max size</span>
            <strong>{formatNumber(copy?.live_gate.max_size, 4)}</strong>
          </div>
          <div>
            <span>Max notional</span>
            <strong>{formatNumber(copy?.live_gate.max_notional, 4)}</strong>
          </div>
        </div>
        {copyPreview ? (
          <div className={`info-banner ${copyPreview.blocked ? "warn" : ""}`}>
            <strong>{copyPreview.status}</strong>
            <span>{copyPreview.message ?? copyPreview.reason ?? ""}</span>
            {copyPreview.order ? (
              <small>
                {copyPreview.order.side} {formatNumber(copyPreview.order.size, 4)} @ {formatNumber(copyPreview.order.limit_price, 4)} notional{" "}
                {formatNumber(copyPreview.order.approx_notional, 4)}
              </small>
            ) : null}
          </div>
        ) : null}
      </section>

      <section className="panel span-2">
        <div className="panel-title">
          <Activity size={18} />
          <h2>Recent Activity</h2>
        </div>
        <ActivityTable activity={wallets?.recent_activity ?? []} />
      </section>
    </div>
  );
}

function ActivityTable({ activity }: { activity: WalletActivity[] }) {
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Time</th>
            <th>Wallet</th>
            <th>Trade</th>
            <th>Market</th>
            <th>Copy Preview</th>
          </tr>
        </thead>
        <tbody>
          {activity.map((item) => (
            <tr key={item.id}>
              <td>{formatTime(item.timestamp)}</td>
              <td>
                <strong>{item.display_name || item.wallet}</strong>
                <small>{item.transaction_hash || item.wallet}</small>
              </td>
              <td>
                <div className="stacked">
                  <span>
                    {item.side} {item.outcome || item.asset}
                  </span>
                  <small>
                    price {formatNumber(item.price, 4)} / size {formatNumber(item.size, 4)}
                  </small>
                </div>
              </td>
              <td>{item.slug || "-"}</td>
              <td>{item.copy_preview?.message ?? item.copy_preview?.reason ?? item.copy_preview?.status ?? "-"}</td>
            </tr>
          ))}
          {!activity.length ? (
            <tr>
              <td colSpan={5}>
                <span className="muted-text">No recent wallet activity in this API session.</span>
              </td>
            </tr>
          ) : null}
        </tbody>
      </table>
    </div>
  );
}

function PaperView({
  form,
  markets,
  message,
  onAction,
  onClearHistory,
  onFormChange,
  paper
}: {
  form: PaperOrderForm;
  markets: MarketsPayload | null;
  message: string;
  onAction: (action: string, target?: { market_id: string; contract_id: string; id?: string }) => void;
  onClearHistory: () => void;
  onFormChange: (form: PaperOrderForm) => void;
  paper: PaperPayload | null;
}) {
  const updateField = (field: keyof PaperOrderForm, value: string) => onFormChange({ ...form, [field]: value });
  return (
    <div className="content-grid">
      <section className="panel full">
        <div className="panel-title">
          <Activity size={18} />
          <h2>Order Form</h2>
        </div>
        <div className="paper-form">
          <label>
            <span>Market</span>
            <select value={form.market_id} onChange={(event) => updateField("market_id", event.target.value)}>
              {(markets?.markets ?? []).map((market) => (
                <option key={market.market_id} value={market.market_id}>
                  {market.display_name}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>Contract</span>
            <input value={form.contract_id} onChange={(event) => updateField("contract_id", event.target.value)} />
          </label>
          <label>
            <span>Side</span>
            <select value={form.side} onChange={(event) => updateField("side", event.target.value)}>
              <option value="BUY">BUY</option>
              <option value="SELL">SELL</option>
              <option value="BACK">BACK</option>
              <option value="LAY">LAY</option>
            </select>
          </label>
          <label>
            <span>Size</span>
            <input value={form.size} onChange={(event) => updateField("size", event.target.value)} />
          </label>
          <label>
            <span>Limit</span>
            <input value={form.limit_price} onChange={(event) => updateField("limit_price", event.target.value)} />
          </label>
        </div>
        <div className="button-row">
          <button className="icon-button" onClick={() => onAction("quote")}>
            <RefreshCw size={17} /> Refresh Quote
          </button>
          <button className="icon-button" onClick={() => onAction("quote-limit")}>
            <SlidersHorizontal size={17} /> Use Quote Limit
          </button>
          <button className="icon-button" onClick={() => onAction("impact")}>
            <BarChart3 size={17} /> Preview Impact
          </button>
          <button className="icon-button" onClick={() => onAction("live-preflight")}>
            <ShieldCheck size={17} /> Live Preflight
          </button>
          <button className="icon-button" onClick={() => onAction("submit")}>
            <Database size={17} /> Submit Paper Order
          </button>
        </div>
        {message ? <div className="info-banner">{message}</div> : null}
      </section>

      <section className="panel span-2">
        <div className="panel-title">
          <BarChart3 size={18} />
          <h2>Exposure</h2>
        </div>
        <div className="metrics-grid">
          <Metric label="Positions" value={paper?.summary.positions ?? 0} />
          <Metric label="Gross size" value={formatNumber(paper?.summary.gross_size, 4)} />
          <Metric label="Entry notional" value={formatNumber(paper?.summary.entry_notional, 4)} />
          <Metric label="Net notional" value={formatNumber(paper?.summary.net_notional, 4)} />
          <Metric label="Marked" value={`${paper?.summary.marked ?? 0}/${paper?.summary.positions ?? 0}`} />
          <Metric label="Unrealized" value={formatNumber(paper?.summary.unrealized, 4)} />
        </div>
      </section>

      <section className="panel full">
        <div className="panel-header">
          <div className="panel-title">
            <Activity size={18} />
            <h2>Open Positions</h2>
          </div>
          <div className="button-row compact">
            <button className="icon-button" onClick={() => onAction("refresh-marks")}>
              <RefreshCw size={17} /> Refresh Marks
            </button>
            <button className="icon-button" onClick={() => onAction("clear-marks")}>
              <Trash2 size={17} /> Clear Marks
            </button>
          </div>
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Market</th>
                <th>Contract</th>
                <th className="numeric">Net size</th>
                <th className="numeric">Avg price</th>
                <th className="numeric">Notional</th>
                <th className="numeric">Mark</th>
                <th>Source</th>
                <th className="numeric">Unrealized</th>
                <th className="numeric">Trades</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {(paper?.positions ?? []).map((position) => (
                <tr key={`${position.market_id}:${position.contract_id}`}>
                  <td>{position.market_id}</td>
                  <td>{position.contract_id}</td>
                  <td className="numeric">{formatNumber(position.net_size)}</td>
                  <td className="numeric">{formatNumber(position.average_price)}</td>
                  <td className="numeric">{formatNumber(position.notional)}</td>
                  <td className="numeric">{formatNumber(position.mark_price)}</td>
                  <td>{position.mark_source || "-"}</td>
                  <td className="numeric">{formatNumber(position.unrealized)}</td>
                  <td className="numeric">{position.trades}</td>
                  <td>
                    <div className="row-actions">
                      <button onClick={() => onAction("use-position", position)}>Use</button>
                      <button onClick={() => onAction("refresh-selected-mark", position)}>Mark</button>
                      <button onClick={() => onAction("clear-selected-mark", position)}>Clear</button>
                    </div>
                  </td>
                </tr>
              ))}
              {!paper?.positions.length ? (
                <tr>
                  <td colSpan={10} className="empty-cell">
                    No open paper exposure.
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
      </section>

      <section className="panel full">
        <div className="panel-header">
          <div className="panel-title">
            <Database size={18} />
            <h2>History</h2>
          </div>
          <button className="danger-button" onClick={onClearHistory} disabled={!paper?.history.length}>
            <Trash2 size={17} /> Clear History
          </button>
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Time</th>
                <th>Market</th>
                <th>Contract</th>
                <th>Side</th>
                <th className="numeric">Size</th>
                <th className="numeric">Limit</th>
                <th>Accepted</th>
                <th>Message</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {(paper?.history ?? []).map((trade) => (
                <tr key={trade.id}>
                  <td>{formatTime(trade.created_at)}</td>
                  <td>{trade.market_id}</td>
                  <td>{trade.contract_id}</td>
                  <td>{trade.side}</td>
                  <td className="numeric">{formatNumber(trade.size)}</td>
                  <td className="numeric">{formatNumber(trade.limit_price)}</td>
                  <td>
                    <StatusPill tone={trade.accepted ? "good" : "warn"}>{trade.accepted ? "yes" : "no"}</StatusPill>
                  </td>
                  <td>{trade.message}</td>
                  <td>
                    <div className="row-actions">
                      <button onClick={() => onAction("use-history", { ...trade, market_id: trade.market_id, contract_id: trade.contract_id })}>
                        Use
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
              {!paper?.history.length ? (
                <tr>
                  <td colSpan={9} className="empty-cell">
                    No paper-order history.
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}

function SettingsView({
  config,
  health,
  markets,
  onSelectedMarketChange,
  onThemeChange
}: {
  config: ConfigPayload | null;
  health: HealthPayload | null;
  markets: MarketsPayload | null;
  onSelectedMarketChange: (marketId: string) => void;
  onThemeChange: (theme: Theme) => void;
}) {
  return (
    <section className="panel full">
      <div className="settings-grid">
        <label>
          <span>Selected market</span>
          <select value={config?.selected_market_id ?? ""} onChange={(event) => onSelectedMarketChange(event.target.value)}>
            {(markets?.markets ?? []).map((market) => (
              <option key={market.market_id} value={market.market_id}>
                {market.display_name}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>Theme</span>
          <select value={config?.theme ?? "light"} onChange={(event) => onThemeChange(event.target.value as Theme)}>
            <option value="light">Light</option>
            <option value="dark">Dark</option>
          </select>
        </label>
        <label>
          <span>Config path</span>
          <input value={health?.config_path ?? ""} readOnly />
        </label>
        <label>
          <span>React build path</span>
          <input value={health?.frontend_dist ?? ""} readOnly />
        </label>
        <label>
          <span>React build available</span>
          <input value={health?.frontend_build_available ? "Yes" : "No"} readOnly />
        </label>
        <label>
          <span>Python GUI command</span>
          <input value={health?.python_gui_command ?? ""} readOnly />
        </label>
        <label>
          <span>Tkinter fallback</span>
          <input value={health?.tkinter_fallback ?? ""} readOnly />
        </label>
        <label>
          <span>React dev command</span>
          <input value={health?.react_dev_command ?? ""} readOnly />
        </label>
        <label>
          <span>React build command</span>
          <input value={health?.react_build_command ?? ""} readOnly />
        </label>
        <label>
          <span>React prod command</span>
          <input value={health?.react_prod_command ?? ""} readOnly />
        </label>
      </div>
    </section>
  );
}
