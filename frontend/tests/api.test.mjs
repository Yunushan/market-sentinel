import assert from "node:assert/strict";
import { after, afterEach, test } from "node:test";
import { webcrypto } from "node:crypto";

const originalWindowDescriptor = Object.getOwnPropertyDescriptor(globalThis, "window");
const originalCryptoDescriptor = Object.getOwnPropertyDescriptor(globalThis, "crypto");
const originalFetchDescriptor = Object.getOwnPropertyDescriptor(globalThis, "fetch");

Object.defineProperty(globalThis, "window", {
  configurable: true,
  value: { location: { port: "5173" } }
});

const api = await import("../.test-dist/src/api.js");

function restoreProperty(name, descriptor) {
  if (descriptor) {
    Object.defineProperty(globalThis, name, descriptor);
  } else {
    delete globalThis[name];
  }
}

function useUuidSequence(...values) {
  let index = 0;
  Object.defineProperty(globalThis, "crypto", {
    configurable: true,
    value: {
      randomUUID() {
        const value = values[index];
        index += 1;
        assert.ok(value, "test exhausted its deterministic UUID sequence");
        return value;
      }
    }
  });
}

function jsonResponse(status, payload) {
  return {
    ok: status >= 200 && status < 300,
    status,
    async json() {
      return payload;
    }
  };
}

afterEach(() => {
  restoreProperty("crypto", originalCryptoDescriptor);
  restoreProperty("fetch", originalFetchDescriptor);
});

after(() => {
  restoreProperty("window", originalWindowDescriptor);
});

test("alert event acknowledgement encodes its identity and preserves failed-save errors", async () => {
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options });
    return jsonResponse(503, { error: { code: "SAVE_FAILED", message: "Event remains unacknowledged." } });
  };
  await assert.rejects(api.acknowledgeAlertEvent("event/identity"), /Event remains unacknowledged/);
  assert.equal(calls[0].url, "http://127.0.0.1:8765/api/alerts/events/event%2Fidentity/acknowledge");
  assert.equal(calls[0].options.method, "POST");
  assert.equal(calls[0].options.body, "{}");
});

test("ambiguous mutation failures reuse a canonical idempotency key until success", async () => {
  useUuidSequence("00000000-0000-4000-8000-000000000001", "00000000-0000-4000-8000-000000000002");
  const calls = [];
  let attempt = 0;
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options });
    attempt += 1;
    if (attempt === 1) {
      throw new TypeError("connection reset after request transmission");
    }
    return jsonResponse(200, { stored: true });
  };

  const firstPayload = { label: "reconcile-me", report: { beta: 2, alpha: 1 } };
  const reorderedPayload = { report: { alpha: 1, beta: 2 }, label: "reconcile-me" };
  await assert.rejects(api.storePolymarketLiveValidationReport(firstPayload), TypeError);
  await api.storePolymarketLiveValidationReport(reorderedPayload);
  await api.storePolymarketLiveValidationReport(firstPayload);

  assert.equal(calls[0].url, "http://127.0.0.1:8765/api/polymarket/live-validation/reports");
  assert.equal(calls[0].options.headers["Idempotency-Key"], calls[1].options.headers["Idempotency-Key"]);
  assert.notEqual(calls[1].options.headers["Idempotency-Key"], calls[2].options.headers["Idempotency-Key"]);
});

test("terminal structured 4xx errors fall back to HTTP status and rotate the next key", async () => {
  useUuidSequence("00000000-0000-4000-8000-000000000003", "00000000-0000-4000-8000-000000000004");
  const keys = [];
  let attempt = 0;
  globalThis.fetch = async (_url, options) => {
    keys.push(options.headers["Idempotency-Key"]);
    attempt += 1;
    return attempt === 1
      ? jsonResponse(422, { error: { code: "INVALID_REPORT", message: "schema rejected", details: { field: "mode" } } })
      : jsonResponse(200, { stored: true });
  };

  await assert.rejects(
    api.storePolymarketLiveValidationReport({ label: "invalid-terminal", report: { mode: "bad" } }),
    (error) => {
      assert.ok(error instanceof api.ApiRequestError);
      assert.equal(error.code, "INVALID_REPORT");
      assert.equal(error.status, 422);
      assert.deepEqual(error.details, { field: "mode" });
      return true;
    }
  );
  await api.storePolymarketLiveValidationReport({ label: "invalid-terminal", report: { mode: "bad" } });

  assert.notEqual(keys[0], keys[1]);
});

test("durable creates and live order management send generated idempotency keys", async () => {
  useUuidSequence(
    "00000000-0000-4000-8000-000000000011",
    "00000000-0000-4000-8000-000000000012",
    "00000000-0000-4000-8000-000000000013",
    "00000000-0000-4000-8000-000000000014"
  );
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options });
    return jsonResponse(200, { ok: true });
  };

  await api.createAlert({ token_id: "token-1", label: "Watch", direction: "above", threshold: 0.7 });
  await api.createWallet({ wallet: "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb" });
  await api.submitPaperOrder({
    market_id: "kalshi",
    contract_id: "KXTEST:YES",
    side: "BUY",
    size: "2",
    limit_price: "0.4",
    metadata_json: ""
  });
  await api.manageMarketOrders("kalshi", "cancel_order", { order_id: "order-1" });

  assert.deepEqual(
    calls.map((call) => new URL(call.url).pathname),
    [
      "/api/alerts",
      "/api/wallets",
      "/api/paper/orders",
      "/api/markets/kalshi/orders/cancel_order"
    ]
  );
  assert.ok(calls.every((call) => call.options.method === "POST"));
  const keys = calls.map((call) => call.options.headers["Idempotency-Key"]);
  assert.equal(new Set(keys).size, 4);
  assert.ok(keys.every((key) => key.startsWith("market-sentinel-")));
});

test("ambiguous live order responses retain the same key for reconciliation", async () => {
  useUuidSequence("00000000-0000-4000-8000-000000000021");
  const keys = [];
  globalThis.fetch = async (_url, options) => {
    keys.push(options.headers["Idempotency-Key"]);
    return jsonResponse(503, {
      error: {
        code: "live_mutation_reconciliation_required",
        message: "reconcile venue history",
        status: 503
      }
    });
  };

  const request = { order_id: "order-ambiguous" };
  await assert.rejects(api.manageMarketOrders("kalshi", "cancel_order", request), api.ApiRequestError);
  await assert.rejects(api.manageMarketOrders("kalshi", "cancel_order", request), api.ApiRequestError);

  assert.equal(keys.length, 2);
  assert.equal(keys[0], keys[1]);
});

test("paper metadata is parsed as an object before a live preflight request", async () => {
  let request;
  globalThis.fetch = async (url, options) => {
    request = { url, options };
    return jsonResponse(200, { ok: true });
  };
  const form = {
    market_id: "polymarket",
    contract_id: "contract-1",
    side: "BUY",
    size: "2",
    limit_price: "0.4",
    metadata_json: '{"forecast":{"yes":0.61}}'
  };

  await api.previewLivePreflight(form);
  const body = JSON.parse(request.options.body);
  assert.equal(request.url, "http://127.0.0.1:8765/api/live-safety/preflight");
  assert.deepEqual(body.metadata, { forecast: { yes: 0.61 } });
  assert.equal("metadata_json" in body, false);

  let fetchCalled = false;
  globalThis.fetch = async () => {
    fetchCalled = true;
    return jsonResponse(200, {});
  };
  assert.throws(() => api.previewLivePreflight({ ...form, metadata_json: "[]" }), /must be a JSON object/);
  assert.equal(fetchCalled, false);
});

test("schema diagnostics reject malformed values and normalize safe arrays", () => {
  assert.equal(api.apiSchemaValidation({ schema_validation: { ok: "yes" } }), null);
  assert.deepEqual(
    api.apiSchemaValidation({
      schema_validation: {
        schema_version: "2",
        ok: false,
        mode: "funded_audit",
        report_type: null,
        errors: ["bad mode", 42],
        warnings: ["review"],
        accepted_modes: ["dry_run"]
      }
    }),
    {
      schema_version: 2,
      ok: false,
      mode: "funded_audit",
      report_type: null,
      errors: ["bad mode", "42"],
      warnings: ["review"],
      accepted_modes: ["dry_run"]
    }
  );
});

test("bounded requests time out while consuming the response body", async () => {
  globalThis.fetch = async (_url, options) => ({
    ok: true, status: 200,
    json: () => new Promise((_resolve, reject) => options.signal.addEventListener("abort", () => reject(options.signal.reason), { once: true }))
  });
  await assert.rejects(api.fetchHealth({ timeoutMs: 5 }), (error) => error.name === "TimeoutError" && /timed out/.test(error.message));
});

test("caller cancellation reaches the underlying read and is distinguishable from failures", async () => {
  let observedSignal;
  globalThis.fetch = (_url, options) => {
    observedSignal = options.signal;
    return new Promise((_resolve, reject) => options.signal.addEventListener("abort", () => reject(options.signal.reason), { once: true }));
  };
  const controller = new AbortController();
  const request = api.fetchMarketEvents("polymarket", "", 50, { signal: controller.signal });
  controller.abort();
  await assert.rejects(request, (error) => error.name === "AbortError");
  assert.equal(observedSignal.aborted, true);
});

test("unreadable proxy failures retain HTTP status and ambiguous mutation identity", async () => {
  useUuidSequence("00000000-0000-4000-8000-000000000099");
  const keys = [];
  globalThis.fetch = async (_url, options) => {
    keys.push(options.headers["Idempotency-Key"]);
    return { ok: false, status: 503, async json() { throw new SyntaxError("upstream HTML response"); } };
  };
  const payload = { order_id: "proxy-ambiguous" };
  await assert.rejects(api.manageMarketOrders("kalshi", "cancel_order", payload),
    (error) => error instanceof api.ApiRequestError && error.code === "invalid_api_response" && error.status === 503);
  await assert.rejects(api.manageMarketOrders("kalshi", "cancel_order", payload), api.ApiRequestError);
  assert.equal(keys[0], keys[1]);
});

test("an overlapping older response cannot erase a newer ambiguous mutation key", async () => {
  useUuidSequence("00000000-0000-4000-8000-000000000081", "00000000-0000-4000-8000-000000000082");
  const calls = [];
  const replies = [];
  globalThis.fetch = async (_url, options) => {
    calls.push(options.headers["Idempotency-Key"]);
    return new Promise((resolve) => replies.push(resolve));
  };
  const payload = { order_id: "overlap-recovery" };
  const first = api.manageMarketOrders("kalshi", "cancel_order", payload);
  const overlap = api.manageMarketOrders("kalshi", "cancel_order", payload);
  replies[0](jsonResponse(200, { ok: true })); await first;
  const newer = api.manageMarketOrders("kalshi", "cancel_order", payload);
  replies[2](jsonResponse(503, { error: "ambiguous newer operation" }));
  await assert.rejects(newer, api.ApiRequestError);
  replies[1](jsonResponse(200, { ok: true })); await overlap;
  const retry = api.manageMarketOrders("kalshi", "cancel_order", payload);
  replies[3](jsonResponse(200, { ok: true })); await retry;
  assert.equal(calls[0], calls[1]);
  assert.notEqual(calls[0], calls[2]);
  assert.equal(calls[2], calls[3]);
});

test("blocked browser recovery storage prevents transmitting a durable operation", async () => {
  let transmitted = false;
  globalThis.fetch = async () => { transmitted = true; return jsonResponse(200, {}); };
  Object.defineProperty(window, "sessionStorage", { configurable: true, get() { throw new Error("storage blocked"); } });
  try {
    await assert.rejects(api.createWallet({ wallet: "storage-blocked" }), /cannot be preserved/);
    assert.equal(transmitted, false);
  } finally {
    delete window.sessionStorage;
  }
});

test("interrupted wallet batch delivery retries the same receipt and advances only after success", async () => {
  useUuidSequence("00000000-0000-4000-8000-000000000091", "00000000-0000-4000-8000-000000000092");
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options });
    if (calls.length === 1) throw new TypeError("receipt response was lost");
    return jsonResponse(200, { has_more: true, remaining_activity: 3, consumed_filtered: 1,
      delivery: { mode: "durable_replayable_batch", receipt_id: "00000000-0000-4000-8000-000000000101", acknowledge_with_next_poll: true } });
  };
  await assert.rejects(api.pollWallets(25), TypeError);
  const receipt = await api.pollWallets(25);
  assert.equal(receipt.remaining_activity, 3);
  await api.pollWallets(25);
  assert.equal(calls[0].url, "http://127.0.0.1:8765/api/wallets/poll");
  assert.equal(calls[0].options.body, '{"limit":25}');
  assert.equal(calls[0].options.headers["Idempotency-Key"], calls[1].options.headers["Idempotency-Key"]);
  assert.notEqual(calls[1].options.headers["Idempotency-Key"], calls[2].options.headers["Idempotency-Key"]);
  assert.equal(JSON.parse(calls[2].options.body).acknowledge_receipt_id, "00000000-0000-4000-8000-000000000101");
});

test("analytics requests transmit the explicitly selected equity base asset", async () => {
  const calls = [];
  globalThis.fetch = async (url) => { calls.push(new URL(url)); return jsonResponse(200, {}); };
  await api.fetchPolymarketMdd({ wallet: "0xfixture", equity_base_usd: "500", equity_base_currency: "USDC" });
  await api.fetchPolymarketLeaderboard({ sort: "pnl_usd", equity_base_usd: "500", equity_base_currency: "USDC" });
  for (const url of calls) {
    assert.equal(url.searchParams.get("equity_base_usd"), "500");
    assert.equal(url.searchParams.get("equity_base_currency"), "USDC");
  }
});

test("receipt storage failures and malformed deliveries keep the original batch recovery key", async () => {
  useUuidSequence("00000000-0000-4000-8000-000000000111");
  globalThis.crypto.subtle = webcrypto.subtle;
  const stored = new Map();
  let failReceiptStorage = true;
  const receiptStorageKey = "market-sentinel.wallet-poll-receipt.v1";
  Object.defineProperty(window, "sessionStorage", { configurable: true, value: {
    getItem(key) { return stored.get(key) ?? null; },
    setItem(key, value) {
      if (key === receiptStorageKey && failReceiptStorage) throw new Error("quota unavailable");
      stored.set(key, value);
    }
  } });
  const calls = [];
  globalThis.fetch = async (_url, options) => {
    calls.push(options);
    return jsonResponse(200, { delivery: { mode: "durable_replayable_batch", acknowledge_with_next_poll: true,
      receipt_id: calls.length === 1 ? "malformed" : "00000000-0000-4000-8000-000000000112" } });
  };
  try {
    await assert.rejects(api.pollWallets(31), /delivery receipt is invalid/);
    await assert.rejects(api.pollWallets(31), /could not be preserved/);
    assert.equal(stored.has(receiptStorageKey), false);
    failReceiptStorage = false;
    await api.pollWallets(31);
    assert.equal(stored.get(receiptStorageKey), "00000000-0000-4000-8000-000000000112");
    assert.equal(Object.keys(JSON.parse(stored.get("market-sentinel.pending-mutations.v1"))).length, 0);
    assert.equal(new Set(calls.map((call) => call.headers["Idempotency-Key"])).size, 1);
    assert.equal(new Set(calls.map((call) => call.body)).size, 1);
  } finally { delete window.sessionStorage; }
});

test("concurrent poll clicks share one delivery so late replies cannot orphan a newer pinned receipt", async () => {
  useUuidSequence("00000000-0000-4000-8000-000000000121", "00000000-0000-4000-8000-000000000122");
  const calls = [];
  let finish;
  globalThis.fetch = async (_url, options) => {
    calls.push(options);
    if (calls.length === 1) await new Promise((resolve) => { finish = resolve; });
    return jsonResponse(200, { delivery: { mode: "durable_replayable_batch", acknowledge_with_next_poll: true,
      receipt_id: `00000000-0000-4000-8000-00000000013${calls.length}` } });
  };
  const first = api.pollWallets(37);
  const duplicate = api.pollWallets(37);
  assert.equal(calls.length, 1);
  finish();
  assert.equal(await first, await duplicate);
  await api.pollWallets(37);
  assert.equal(calls.length, 2);
  assert.equal(JSON.parse(calls[1].body).acknowledge_receipt_id, "00000000-0000-4000-8000-000000000131");
  assert.notEqual(calls[0].headers["Idempotency-Key"], calls[1].headers["Idempotency-Key"]);
});
