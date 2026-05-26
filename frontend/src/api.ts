import type {
  AlertForm,
  AlertRefreshResponse,
  AlertsPayload,
  AppStatePayload,
  ConfigPayload,
  CopyForm,
  CopyPayload,
  CopyPreviewForm,
  CopyPreviewPayload,
  HealthPayload,
  LivePreflightPayload,
  LiveSafetyPayload,
  MarketsPayload,
  PaperFormFillPayload,
  PaperImpactPayload,
  PaperOrderForm,
  PaperOrderResponse,
  PaperPayload,
  PaperQuotePayload,
  WalletForm,
  WalletPollResponse,
  WalletsPayload
} from "./types";

export interface MarketPatch {
  enabled?: boolean;
  live_trading_enabled?: boolean;
  live_trading_confirmed?: boolean;
  live_trading_kill_switch?: boolean;
  live_trading_max_size?: string | number | null;
  live_trading_max_notional?: string | number | null;
  settings?: Record<string, unknown>;
}

type ApiErrorBody = {
  error?: string | {
    code?: string;
    message?: string;
    status?: number;
    details?: unknown;
  };
};

const vitePorts = new Set(["5173", "4173"]);
const defaultApiBase = vitePorts.has(window.location.port) ? "http://127.0.0.1:8765" : "";
const apiBase = (import.meta.env.VITE_API_BASE_URL ?? defaultApiBase).replace(/\/$/, "");

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`${apiBase}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(options.headers ?? {})
    }
  });
  const payload = (await response.json()) as T & ApiErrorBody;
  if (!response.ok) {
    const error = payload.error;
    const message = typeof error === "string" ? error : error?.message;
    const code = typeof error === "object" && error?.code ? `${error.code}: ` : "";
    throw new Error(`${code}${message ?? `Request failed: ${response.status}`}`);
  }
  return payload;
}

export function fetchHealth(): Promise<HealthPayload> {
  return request<HealthPayload>("/api/health");
}

export function fetchState(): Promise<AppStatePayload> {
  return request<AppStatePayload>("/api/state");
}

export function fetchConfig(): Promise<ConfigPayload> {
  return request<ConfigPayload>("/api/config");
}

export function fetchMarkets(): Promise<MarketsPayload> {
  return request<MarketsPayload>("/api/markets");
}

export function fetchAlerts(): Promise<AlertsPayload> {
  return request<AlertsPayload>("/api/alerts");
}

export function fetchPaper(): Promise<PaperPayload> {
  return request<PaperPayload>("/api/paper");
}

export function fetchWallets(): Promise<WalletsPayload> {
  return request<WalletsPayload>("/api/wallets");
}

export function fetchCopy(): Promise<CopyPayload> {
  return request<CopyPayload>("/api/copy");
}

export function fetchLiveSafety(): Promise<LiveSafetyPayload> {
  return request<LiveSafetyPayload>("/api/live-safety");
}

export function updateMarket(marketId: string, patch: MarketPatch): Promise<MarketsPayload> {
  return request<MarketsPayload>(`/api/markets/${encodeURIComponent(marketId)}`, {
    method: "PATCH",
    body: JSON.stringify(patch)
  });
}

export function updateConfig(payload: Partial<Pick<ConfigPayload, "selected_market_id" | "theme">>): Promise<ConfigPayload> {
  return request<ConfigPayload>("/api/config", {
    method: "PATCH",
    body: JSON.stringify(payload)
  });
}

export function createAlert(form: AlertForm): Promise<AlertsPayload> {
  return request<AlertsPayload>("/api/alerts", {
    method: "POST",
    body: JSON.stringify(form)
  });
}

export function updateAlert(alertId: string, form: Partial<AlertForm>): Promise<AlertsPayload> {
  return request<AlertsPayload>(`/api/alerts/${encodeURIComponent(alertId)}`, {
    method: "PATCH",
    body: JSON.stringify(form)
  });
}

export function deleteAlert(alertId: string): Promise<AlertsPayload> {
  return request<AlertsPayload>(`/api/alerts/${encodeURIComponent(alertId)}`, {
    method: "DELETE"
  });
}

export function refreshAlerts(): Promise<AlertRefreshResponse> {
  return request<AlertRefreshResponse>("/api/alerts/refresh", {
    method: "POST",
    body: JSON.stringify({})
  });
}

export function refreshAlert(alertId: string): Promise<AlertRefreshResponse> {
  return request<AlertRefreshResponse>(`/api/alerts/${encodeURIComponent(alertId)}/refresh`, {
    method: "POST",
    body: JSON.stringify({})
  });
}

export function createWallet(form: WalletForm): Promise<WalletsPayload> {
  return request<WalletsPayload>("/api/wallets", {
    method: "POST",
    body: JSON.stringify(form)
  });
}

export function updateWallet(walletId: string, form: Partial<WalletForm>): Promise<WalletsPayload> {
  return request<WalletsPayload>(`/api/wallets/${encodeURIComponent(walletId)}`, {
    method: "PATCH",
    body: JSON.stringify(form)
  });
}

export function deleteWallet(walletId: string): Promise<WalletsPayload> {
  return request<WalletsPayload>(`/api/wallets/${encodeURIComponent(walletId)}`, {
    method: "DELETE"
  });
}

export function updateWalletPolling(pollIntervalSeconds: string | number): Promise<WalletsPayload> {
  return request<WalletsPayload>("/api/wallets/polling", {
    method: "PATCH",
    body: JSON.stringify({ poll_interval_seconds: pollIntervalSeconds })
  });
}

export function pollWallets(limit = 25): Promise<WalletPollResponse> {
  return request<WalletPollResponse>("/api/wallets/poll", {
    method: "POST",
    body: JSON.stringify({ limit })
  });
}

export function updateCopySettings(form: CopyForm): Promise<CopyPayload> {
  return request<CopyPayload>("/api/copy", {
    method: "PATCH",
    body: JSON.stringify(form)
  });
}

export function previewCopyTrade(form: CopyPreviewForm): Promise<CopyPreviewPayload> {
  return request<CopyPreviewPayload>("/api/copy/preview", {
    method: "POST",
    body: JSON.stringify(form)
  });
}

export function previewLivePreflight(form: PaperOrderForm): Promise<LivePreflightPayload> {
  return request<LivePreflightPayload>("/api/live-safety/preflight", {
    method: "POST",
    body: JSON.stringify(form)
  });
}

export function clearPaperHistory(): Promise<PaperPayload> {
  return request<PaperPayload>("/api/paper/history/clear", {
    method: "POST",
    body: JSON.stringify({})
  });
}

export function refreshPaperQuote(form: PaperOrderForm): Promise<PaperQuotePayload> {
  return request<PaperQuotePayload>("/api/paper/quote", {
    method: "POST",
    body: JSON.stringify(form)
  });
}

export function fillPaperQuoteLimit(form: PaperOrderForm): Promise<{ limit_price: number; message: string }> {
  return request<{ limit_price: number; message: string }>("/api/paper/quote-limit", {
    method: "POST",
    body: JSON.stringify(form)
  });
}

export function previewPaperImpact(form: PaperOrderForm): Promise<PaperImpactPayload> {
  return request<PaperImpactPayload>("/api/paper/preview-impact", {
    method: "POST",
    body: JSON.stringify(form)
  });
}

export function submitPaperOrder(form: PaperOrderForm): Promise<PaperOrderResponse> {
  return request<PaperOrderResponse>("/api/paper/orders", {
    method: "POST",
    body: JSON.stringify(form)
  });
}

export function usePaperHistory(recordId: string): Promise<PaperFormFillPayload> {
  return request<PaperFormFillPayload>("/api/paper/history/use", {
    method: "POST",
    body: JSON.stringify({ record_id: recordId })
  });
}

export function usePaperPosition(marketId: string, contractId: string): Promise<PaperFormFillPayload> {
  return request<PaperFormFillPayload>("/api/paper/positions/use", {
    method: "POST",
    body: JSON.stringify({ market_id: marketId, contract_id: contractId })
  });
}

export function refreshPaperMarks(): Promise<{ paper: PaperPayload; message: string; problems: string[] }> {
  return request<{ paper: PaperPayload; message: string; problems: string[] }>("/api/paper/marks/refresh", {
    method: "POST",
    body: JSON.stringify({})
  });
}

export function refreshSelectedPaperMark(marketId: string, contractId: string): Promise<{ paper: PaperPayload; message: string }> {
  return request<{ paper: PaperPayload; message: string }>("/api/paper/marks/refresh-selected", {
    method: "POST",
    body: JSON.stringify({ market_id: marketId, contract_id: contractId })
  });
}

export function clearPaperMarks(): Promise<{ paper: PaperPayload; message: string }> {
  return request<{ paper: PaperPayload; message: string }>("/api/paper/marks/clear", {
    method: "POST",
    body: JSON.stringify({})
  });
}

export function clearSelectedPaperMark(marketId: string, contractId: string): Promise<{ paper: PaperPayload; message: string }> {
  return request<{ paper: PaperPayload; message: string }>("/api/paper/marks/clear-selected", {
    method: "POST",
    body: JSON.stringify({ market_id: marketId, contract_id: contractId })
  });
}
