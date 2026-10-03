import assert from "node:assert/strict";
import test from "node:test";

const { marketConfigurationPatch } = await import("../.test-dist/src/market-configuration.js");
const market = {
  configuration_revision: "market-revision-1", enabled: true,
  health: { order_management_enabled: true },
  safety: { live_trading_enabled: false, live_trading_confirmed: false, live_trading_kill_switch: true,
    live_trading_max_size: null, live_trading_max_notional: 20 }
};

test("saving a limited gate form sends only changed fields and preserves absent settings", () => {
  assert.deepEqual(marketConfigurationPatch(market, {
    enabled: true, live_trading_enabled: false, live_trading_confirmed: false,
    live_trading_kill_switch: true, live_trading_max_size: "7", live_trading_max_notional: "20"
  }), { live_trading_max_size: "7", expected_revision: "market-revision-1" });
});

test("explicitly disabling a presented feature remains possible while unchanged forms are harmless", () => {
  assert.deepEqual(marketConfigurationPatch(market, { polymarket_order_management_enabled: false }),
    { settings: { polymarket_order_management_enabled: false }, expected_revision: "market-revision-1" });
  assert.equal(marketConfigurationPatch(market, { live_trading_kill_switch: true, live_trading_max_size: "" }), null);
  assert.throws(() => marketConfigurationPatch({ ...market, configuration_revision: undefined }, { enabled: false }), /revision is unavailable/);
});
