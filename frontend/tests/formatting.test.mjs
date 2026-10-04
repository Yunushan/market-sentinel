import assert from "node:assert/strict";
import test from "node:test";
const { formatAssetAmount } = await import("../.test-dist/src/formatting.js");

test("amounts preserve explicit USD and native USDC units without conversion", () => {
  const number = new Intl.NumberFormat(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  assert.equal(formatAssetAmount(50, "USD"), new Intl.NumberFormat(undefined, { style: "currency", currency: "USD" }).format(50));
  assert.equal(formatAssetAmount(50, "USDC"), `${number.format(50)} USDC`);
  assert.equal(formatAssetAmount(-1234.5, "USDC"), `${number.format(-1234.5)} USDC`);
  assert.notEqual(formatAssetAmount(50, "USD"), formatAssetAmount(50, "USDC"));
});

test("unknown historical currencies and invalid amounts remain unavailable", () => {
  for (const currency of [undefined, null, "", "usd", "USDT", "USD/USDC"]) {
    assert.equal(formatAssetAmount(50, currency), "Unavailable");
  }
  for (const amount of [undefined, null, NaN, Infinity, -Infinity, "50", ""]) {
    assert.equal(formatAssetAmount(amount, "USDC"), "Unavailable");
  }
});
