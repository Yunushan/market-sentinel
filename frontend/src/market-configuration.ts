import type { MarketPatch } from "./api.js";
import type { Market } from "./types.js";

const booleanFields = ["enabled", "live_trading_enabled", "live_trading_confirmed", "live_trading_kill_switch"] as const;
const limitFields = ["live_trading_max_size", "live_trading_max_notional"] as const;

// The form may expose only a subset of settings. Missing controls must never
// become an instruction to disable a feature on the server.
export function marketConfigurationPatch(market: Market, values: Record<string, string | boolean>): MarketPatch | null {
  const patch: MarketPatch = {};
  for (const field of booleanFields) {
    const previous = field === "enabled" ? market.enabled : market.safety[field];
    if (typeof values[field] === "boolean" && values[field] !== previous) {
      patch[field] = values[field];
    }
  }
  for (const field of limitFields) {
    const value = values[field];
    if (typeof value === "string" && value.trim() !== String(market.safety[field] ?? "")) {
      patch[field] = value.trim();
    }
  }
  for (const [field, value] of Object.entries(values)) {
    if (field.endsWith("_order_management_enabled") && typeof value === "boolean" && value !== (market.health.order_management_enabled === true)) {
      patch.settings = { ...patch.settings, [field]: value };
    }
  }
  if (!Object.keys(patch).length) return null;
  if (!market.configuration_revision) {
    throw new Error("Refresh market settings before saving: the configuration revision is unavailable.");
  }
  return { ...patch, expected_revision: market.configuration_revision };
}

export function marketConfigurationFormValues(form: HTMLFormElement): Record<string, string | boolean> {
  const values: Record<string, string | boolean> = {};
  for (const control of Array.from(form.elements)) {
    if (control instanceof HTMLInputElement && control.name) {
      values[control.name] = control.type === "checkbox" ? control.checked : control.value;
    }
  }
  return values;
}
