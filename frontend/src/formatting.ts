export function formatNumber(value: number | null | undefined, digits = 4): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "-";
  }
  return value.toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits
  });
}

// Preserve the declared asset. A stablecoin amount is never a USD conversion.
export function formatAssetAmount(value: unknown, currency: unknown): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "Unavailable";
  if (currency === "USD") {
    return new Intl.NumberFormat(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 2 }).format(value);
  }
  if (currency === "USDC") return `${formatNumber(value, 2)} USDC`;
  return "Unavailable";
}

export function formatAuditValue(value: unknown): string {
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
