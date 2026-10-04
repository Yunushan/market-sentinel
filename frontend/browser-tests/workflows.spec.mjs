import { expect, test } from "@playwright/test";
import { createHash } from "node:crypto";

const views = ["Overview", "Markets", "Analytics", "Live Safety", "Alerts", "Wallets", "Paper", "Settings"];
const headings = { Markets: "Market Operations", Analytics: "Polymarket Analytics", Wallets: "Wallets & Copy", Paper: "Paper Trading" };

async function navigate(page, name) {
  const button = page.getByRole("navigation", { name: "Primary" }).getByRole("button", { name, exact: true });
  await button.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("heading", { name: headings[name] || name, level: 1, exact: true })).toBeVisible();
}

test.beforeEach(async ({ page, context, baseURL }, testInfo) => {
  const errors = [];
  const blocked = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await context.route("**/*", (route) => {
    if (new URL(route.request().url()).origin === baseURL) return route.continue();
    blocked.push(route.request().url());
    return route.abort();
  });
  testInfo.errorsFromPage = errors;
  testInfo.blockedRequests = blocked;
  await page.goto("/");
  await expect(page.getByText("API ok", { exact: true })).toBeVisible();
  await navigate(page, "Settings");
  await page.getByRole("combobox", { name: "Theme", exact: true }).selectOption(testInfo.project.use.colorScheme);
  await expect(page.locator("html")).toHaveAttribute("data-theme", testInfo.project.use.colorScheme);
  await expect(page.locator(".app-shell")).toHaveCSS("background-color",
    testInfo.project.use.colorScheme === "dark" ? "rgb(20, 25, 27)" : "rgb(241, 244, 243)");
});

test.afterEach(async ({}, testInfo) => {
  expect(testInfo.errorsFromPage).toEqual([]);
  expect(testInfo.blockedRequests).toEqual([]);
});

test("leaderboard category selection is sent without changing its meaning", async ({ page }, testInfo) => {
  await navigate(page, "Analytics");
  const form = page.locator(".leaderboard-form");
  const category = form.getByRole("combobox", { name: "Category", exact: true });
  await expect(form.getByRole("checkbox", { name: "Accounting snapshot", exact: true })).toBeVisible();
  await expect(category).toHaveValue("OVERALL");
  await expect(category.locator("option")).toHaveCount(11);
  await category.selectOption("ESPORTS");
  await page.route("**/api/polymarket/users/leaderboard?*", async (route) => {
    expect(new URL(route.request().url()).searchParams.get("category")).toBe("ESPORTS");
    await route.fulfill({ json: {
      category: "ESPORTS", rows: [], warnings: [],
      counts: { returned: 0, filtered: 0, scanned: 0, mdd_computed: 0 },
      mdd_available: false, mdd_note: "No history requested.", rate_limit: { limited: false },
      analytics_cache: { enabled: false, entries: 0 },
      completion_reason: "end_of_results", source_enumeration_complete: true,
      source_scope_note: "Fixture category results."
    } });
  });
  const request = page.waitForRequest((request) => new URL(request.url()).pathname === "/api/polymarket/users/leaderboard");
  await form.locator('button[type="submit"]').click();
  expect(new URL((await request).url()).searchParams.get("category")).toBe("ESPORTS");
  await expect(page.getByText("No leaderboard rows matched the filters.")).toBeVisible();
  await expect(category).toHaveValue("ESPORTS");
  await form.screenshot({ path: testInfo.outputPath("leaderboard-category.png") });
  await navigate(page, "Overview");
  await navigate(page, "Analytics");
  await expect(category).toHaveValue("ESPORTS");
});

test("analytics edits invalidate pending user, category and wallet scope results", async ({ page }) => {
  await navigate(page, "Analytics");
  async function delayedFixture(pattern, oldRequest, payload) {
    let release;
    let finish;
    const held = new Promise((resolve) => { release = resolve; });
    const finished = new Promise((resolve) => { finish = resolve; });
    await page.route(pattern, async (route) => {
      const parameters = new URL(route.request().url()).searchParams;
      const delayed = oldRequest(parameters);
      if (delayed) await held;
      try { await route.fulfill({ json: payload(parameters) }); }
      finally { if (delayed) finish(); }
    });
    return { release, finished };
  }

  const search = page.locator(".user-search-form");
  const searchFixture = await delayedFixture("**/api/polymarket/users/search?*", (params) => params.get("q") === "earlier-user", (params) => ({
    query: params.get("q"), source: "fixture", counts: { profiles: 1 }, profiles: [{
      pseudonym: params.get("q") === "earlier-user" ? "Earlier user result" : "Current user result",
      proxy_wallet: "0x1234", profile_image: "", display_username_public: true
    }]
  }));
  try {
    await search.getByLabel("Search", { exact: true }).fill("earlier-user");
    const started = page.waitForRequest("**/api/polymarket/users/search?*");
    await search.locator('button[type="submit"]').click(); await started;
    await search.getByLabel("Search", { exact: true }).fill("current-user");
    searchFixture.release(); await searchFixture.finished;
    await expect(page.getByText("Earlier user result", { exact: true })).toHaveCount(0);
    await search.locator('button[type="submit"]').click();
    await expect(page.getByText("Current user result", { exact: true })).toBeVisible();
    await search.getByLabel("Search", { exact: true }).fill("another-user");
    await expect(page.getByText("Current user result", { exact: true })).toHaveCount(0);
  } finally { searchFixture.release(); }

  const leaderboard = page.locator(".leaderboard-form");
  const categoryFixture = await delayedFixture("**/api/polymarket/users/leaderboard?*", (params) => params.get("category") === "POLITICS", (params) => ({
    category: params.get("category"), rows: [], warnings: [], counts: { returned: 0, filtered: 0, scanned: 0, mdd_computed: 0 },
    mdd_available: false, mdd_note: "No history requested.", rate_limit: { limited: false },
    analytics_cache: { enabled: false, entries: 0 }, completion_reason: "end_of_results", source_enumeration_complete: true,
    source_scope_note: params.get("category") === "POLITICS" ? "Earlier category scope" : "Current category scope"
  }));
  try {
    await leaderboard.getByRole("combobox", { name: "Category", exact: true }).selectOption("POLITICS");
    const started = page.waitForRequest("**/api/polymarket/users/leaderboard?*");
    await leaderboard.locator('button[type="submit"]').click(); await started;
    await leaderboard.getByRole("combobox", { name: "Category", exact: true }).selectOption("SPORTS");
    categoryFixture.release(); await categoryFixture.finished;
    await expect(page.getByText(/Earlier category scope/)).toHaveCount(0);
    await leaderboard.locator('button[type="submit"]').click();
    await expect(page.getByText(/Current category scope/).first()).toBeVisible();
    await leaderboard.getByRole("combobox", { name: "Category", exact: true }).selectOption("CRYPTO");
    await expect(page.getByText(/Current category scope/)).toHaveCount(0);
  } finally { categoryFixture.release(); }

  const mdd = page.locator(".direct-mdd-form");
  const walletFixture = await delayedFixture("**/api/polymarket/users/mdd?*", (params) => params.get("wallet") === "0x1111", (params) => ({
    wallet: params.get("wallet"), mdd_usd: null, mdd_pct: null, mdd_available: false, mdd_pct_basis: "unverified", points: [],
    mdd_method: params.get("wallet") === "0x1111" ? "Earlier wallet scope" : "Current wallet scope"
  }));
  try {
    await mdd.getByLabel("Wallet", { exact: true }).fill("0x1111");
    const started = page.waitForRequest("**/api/polymarket/users/mdd?*");
    await mdd.locator('button[type="submit"]').click(); await started;
    await mdd.getByLabel("Wallet", { exact: true }).fill("0x2222");
    await mdd.getByRole("combobox", { name: "MDD mode", exact: true }).selectOption("mark_replay");
    walletFixture.release(); await walletFixture.finished;
    await expect(page.getByText("Earlier wallet scope", { exact: true })).toHaveCount(0);
    await mdd.locator('button[type="submit"]').click();
    await expect(page.getByText("Current wallet scope", { exact: true })).toBeVisible();
    await mdd.getByRole("combobox", { name: "MDD mode", exact: true }).selectOption("fast");
    await expect(page.getByText("Current wallet scope", { exact: true })).toHaveCount(0);
  } finally { walletFixture.release(); }
  await expect(page.getByRole("alert")).toHaveCount(0);
});

test("native leaderboard share volume never acquires a USD turnover or percentage basis", async ({ page }) => {
  await navigate(page, "Analytics");
  const form = page.locator(".leaderboard-form");
  const sort = form.getByRole("combobox", { name: "Sort", exact: true });
  await expect(sort).toHaveValue("pnl_usd");
  await expect(sort.locator('option[value="volume_usd"]')).toHaveAttribute("disabled", "");
  await expect(sort.locator('option[value="roi_pct"]')).toHaveAttribute("disabled", "");
  for (const name of ["Min volume USD", "Max volume USD", "Min PnL / USD volume %", "Max PnL / USD volume %"]) {
    await expect(form.getByLabel(name, { exact: true })).toBeDisabled();
  }
  let version = 2;
  await page.route("**/api/polymarket/users/leaderboard?*", async (route) => {
    expect(new URL(route.request().url()).searchParams.get("sort")).toBe(version === 2 ? "volume_shares" : "pnl_usd");
    await route.fulfill({ json: {
      source_api_version: version, financial_basis: { volume: version === 2 ? "shares" : "USD" },
      rows: [{ source_api_version: version, rank: 1, wallet: "fixture-wallet", display_name: "Financial basis fixture",
        profile_image: "", display_username_public: true, pnl_usd: 50, volume_shares: version === 2 ? 1234.5 : null,
        // Contradictory v2 monetary fields must still fail closed in the view.
        volume_usd: 100, roi_pct: 50, pnl_volume_pct: 50, roi_pct_basis: version === 2 ? "Unavailable: native volume is shares" : "declared USD turnover",
        trade_count: 1, mdd_usd: null, mdd_pct: null, mdd_available: false, raw: {} }],
      warnings: [], counts: { returned: 1, filtered: 0, scanned: 1, mdd_computed: 0 }, analytics_cache: { enabled: false, entries: 0 },
      rate_limit: { limited: false }, completion_reason: "end_of_results", source_enumeration_complete: true,
      source_scope_note: "Fixture financial units", mdd_available: false, mdd_note: "No history requested."
    } });
  });
  await sort.selectOption("volume_shares");
  await form.locator('button[type="submit"]').click();
  const row = page.getByRole("row").filter({ hasText: "Financial basis fixture" });
  await expect(page.getByRole("columnheader", { name: "PnL (USDC)", exact: true })).toBeVisible();
  await expect(row.getByRole("cell").nth(2)).toHaveText("50.00 USDC");
  await expect(row.getByRole("cell").nth(3)).toHaveText("1,234.5000");
  await expect(row.getByRole("cell").nth(4)).toHaveText("Unavailable");
  await expect(row.getByRole("cell").nth(5)).toHaveText("Unavailable");
  version = 1;
  await sort.selectOption("pnl_usd");
  await form.locator('button[type="submit"]').click();
  await expect(page.getByRole("columnheader", { name: "PnL (USD)", exact: true })).toBeVisible();
  await expect(row.getByRole("cell").nth(2)).toHaveText("$50.00");
  await expect(row.getByRole("cell").nth(4)).toHaveText("$100.00");
  await expect(row.getByRole("cell").nth(5)).toHaveText("50.00%");

  const mdd = page.locator(".direct-mdd-form");
  const mddPanel = page.locator("section.panel").filter({ has: mdd });
  const auditPanel = page.locator("section.panel").filter({ has: page.getByRole("heading", { name: "MDD Audit Detail", exact: true }) });
  let currency = "USDC";
  await page.route("**/api/polymarket/users/mdd?*", async (route) => {
    const params = new URL(route.request().url()).searchParams;
    expect(params.get("equity_base_currency")).toBe("USDC");
    expect(params.get("equity_base_usd")).toBe("500");
    await route.fulfill({ json: {
      wallet: params.get("wallet"), mdd_usd: 25, mdd_pct: currency ? 5 : null, mdd_available: true,
      quote_currency: currency, equity_base_currency: currency, equity_base_usd: 500,
      // Position basis alone must never relabel an unknown whole curve.
      position_capital_basis: { unit: "USDC" }, peak_value: 50, trough_value: 25,
      mdd_method: "Explicit currency fixture", mdd_pct_basis: "fixture", points: [{ timestamp: 100, value: 50 }],
      audit_cache: { key: "currency-fixture" }
    } });
  });
  await mdd.getByLabel("Wallet", { exact: true }).fill("0x1111");
  await mdd.getByLabel("Equity base (USDC)", { exact: true }).fill("500");
  for (const [unit, amount, base, point] of [["USDC", "25.00 USDC", "500.00 USDC", "50.00 USDC"],
    ["USD", "$25.00", "$500.00", "$50.00"], [null, "Unavailable", "Unavailable", "Unavailable"]]) {
    currency = unit;
    await mdd.locator('button[type="submit"]').click();
    await expect(mddPanel.locator(".metric").filter({ hasText: "Observed MDD amount" }).locator("strong")).toHaveText(amount);
    await expect(auditPanel.locator(".metric").filter({ hasText: "Equity base" }).locator("strong")).toHaveText(base);
    await expect(auditPanel.locator(".audit-points").getByRole("row").nth(1).getByRole("cell").nth(1)).toHaveText(point);
  }
  await expect(page.getByRole("alert")).toHaveCount(0);
});

test("all views have named controls, current navigation and responsive layout", async ({ page }, testInfo) => {
  for (const name of views) {
    await navigate(page, name);
    const navigation = page.getByRole("navigation", { name: "Primary" });
    await expect(navigation.locator('[aria-current="page"]')).toHaveCount(1);
    await expect(navigation.getByRole("button", { name, exact: true })).toHaveAttribute("aria-current", "page");
    for (const role of ["textbox", "combobox", "checkbox", "button"]) {
      for (const control of await page.getByRole(role).all()) {
        await expect(control).toHaveAccessibleName(/\S/);
      }
    }
    const width = await page.evaluate(() => ({ document: document.documentElement.scrollWidth, viewport: innerWidth }));
    expect(width.document).toBeLessThanOrEqual(width.viewport);
    await page.screenshot({ path: testInfo.outputPath(`${name.toLowerCase().replaceAll(" ", "-")}.png`), fullPage: true });
  }
  await page.reload();
  await expect(page.getByRole("heading", { name: "Settings", level: 1 })).toBeVisible();
  await expect(page.getByRole("combobox", { name: "Theme", exact: true })).toHaveValue(testInfo.project.use.colorScheme);
  await expect(page.locator("html")).toHaveAttribute("data-theme", testInfo.project.use.colorScheme);
  await navigate(page, "Markets");
  await page.goBack();
  await expect(page.getByRole("heading", { name: "Settings", level: 1 })).toBeVisible();
  await page.goForward();
  await expect(page.getByRole("heading", { name: "Market Operations", level: 1 })).toBeVisible();
  await page.getByRole("navigation", { name: "Primary" }).getByRole("button", { name: "Overview", exact: true }).focus();
  await page.keyboard.press("Tab");
  await expect(page.getByRole("navigation", { name: "Primary" }).getByRole("button", { name: "Markets", exact: true })).toBeFocused();
  await page.getByRole("textbox", { name: "Search markets" }).fill("polymarket");
  await expect(page.getByRole("combobox", { name: "Selected market" })).toHaveValue("polymarket");
});

test("alert validation, creation, edit, toggle, delete confirmation and persistence", async ({ page }, testInfo) => {
  await navigate(page, "Alerts");
  const label = `Browser alert ${testInfo.project.name}`;
  const updated = `${label} edited`;
  await page.getByLabel("Contract/token ID", { exact: true }).fill("123456789");
  await page.getByLabel("Label", { exact: true }).fill(label);
  await page.getByLabel("Threshold", { exact: true }).fill("not-a-number");
  await page.getByRole("button", { name: "Add Alert", exact: true }).click();
  await expect(page.getByRole("alert")).toBeVisible();
  await expect(page.getByLabel("Label", { exact: true })).toHaveValue(label);
  await page.getByLabel("Threshold", { exact: true }).fill("0.5");
  await page.getByRole("button", { name: "Add Alert", exact: true }).click();
  await expect(page.getByRole("status")).toHaveText("Alert added.");
  let row = page.getByRole("row").filter({ hasText: label });
  await expect(row).toHaveCount(1);
  await page.screenshot({ path: testInfo.outputPath("alert-created.png"), fullPage: true });
  await page.reload();
  await expect(row).toHaveCount(1);
  await row.getByRole("button", { name: "Edit alert", exact: true }).click();
  await page.getByLabel("Label", { exact: true }).fill(updated);
  await page.getByRole("button", { name: "Save Alert", exact: true }).click();
  row = page.getByRole("row").filter({ hasText: updated });
  await expect(row).toContainText(updated);
  await row.getByRole("button", { name: "Disable alert", exact: true }).click();
  await expect(row.getByRole("button", { name: "Enable alert", exact: true })).toBeVisible();
  await page.reload();
  await expect(row.getByRole("button", { name: "Enable alert", exact: true })).toBeVisible();
  page.once("dialog", (dialog) => dialog.dismiss());
  await row.getByRole("button", { name: "Delete alert", exact: true }).click();
  await expect(row).toHaveCount(1);
  await row.getByRole("button", { name: "Edit alert", exact: true }).click();
  page.once("dialog", (dialog) => dialog.accept());
  await row.getByRole("button", { name: "Delete alert", exact: true }).click();
  await expect(row).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Add Alert", exact: true })).toBeVisible();
  await page.reload();
  await expect(row).toHaveCount(0);
});

test("wallet edit and delete persist, and failed saves preserve form input", async ({ page }, testInfo) => {
  await navigate(page, "Wallets");
  const name = `Browser wallet ${testInfo.project.name}`;
  const updated = `${name} edited`;
  const walletForm = page.locator("form.wallet-form");
  const identity = `0x${createHash("sha256").update(testInfo.project.name).digest("hex").slice(0, 40)}`;
  await walletForm.getByLabel("Activity identity").fill(identity);
  await walletForm.getByLabel("Name", { exact: true }).fill(name);
  await walletForm.getByLabel("Enabled", { exact: true }).uncheck();
  const failure = (route) => route.request().method() === "POST"
    ? route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ error: "Acceptance save failure" }) })
    : route.fallback();
  await page.route("**/api/wallets", failure);
  await page.getByRole("button", { name: "Add Wallet", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("Acceptance save failure");
  await expect(walletForm.getByLabel("Name", { exact: true })).toHaveValue(name);
  await expect(page.getByRole("row").filter({ hasText: name })).toHaveCount(0);
  await page.unroute("**/api/wallets", failure);
  await page.getByRole("button", { name: "Add Wallet", exact: true }).click();
  await expect(page.getByRole("status")).toHaveText("Wallet watch added.");
  let row = page.getByRole("row").filter({ hasText: name });
  await expect(row).toHaveCount(1);
  await page.screenshot({ path: testInfo.outputPath("wallet-created.png"), fullPage: true });
  await row.getByRole("button", { name: "Edit wallet", exact: true }).click();
  await walletForm.getByLabel("Name", { exact: true }).fill(updated);
  await page.getByRole("button", { name: "Save Wallet", exact: true }).click();
  row = page.getByRole("row").filter({ hasText: updated });
  await expect(row).toContainText(updated);
  await row.getByRole("button", { name: "Enable wallet", exact: true }).click();
  await expect(row.getByRole("button", { name: "Disable wallet", exact: true })).toBeVisible();
  await page.reload();
  await expect(row.getByRole("button", { name: "Disable wallet", exact: true })).toBeVisible();
  page.once("dialog", (dialog) => dialog.dismiss());
  await row.getByRole("button", { name: "Delete wallet", exact: true }).click();
  await expect(row).toHaveCount(1);
  await row.getByRole("button", { name: "Edit wallet", exact: true }).click();
  page.once("dialog", (dialog) => dialog.accept());
  await row.getByRole("button", { name: "Delete wallet", exact: true }).click();
  await expect(row).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Add Wallet", exact: true })).toBeVisible();
  await expect(walletForm.getByLabel("Name", { exact: true })).toHaveValue("");
  await page.reload();
  await expect(row).toHaveCount(0);
});

test("saved alert events stay pending when acknowledgement fails", async ({ page }, testInfo) => {
  const event = {
    id: `notification-${testInfo.project.name}`, alert_id: "deleted-alert", market_id: "polymarket", contract_id: "123456789",
    label: "Deleted threshold alert", direction: "above", threshold: 0.5, source: "last_trade", value: 0.75,
    message: "Deleted threshold alert crossed above 0.5 at 0.75", created_at: 100, acknowledged_at: 0
  };
  const history = () => ({ events: [event], counts: { total: 1, unacknowledged: event.acknowledged_at ? 0 : 1 }, capacity: 1000 });
  await page.route("**/api/state", async (route) => {
    const response = await route.fetch();
    const payload = await response.json();
    payload.alerts.event_history = history();
    await route.fulfill({ response, json: payload });
  });
  let failSave = true;
  await page.route(`**/api/alerts/events/${event.id}/acknowledge`, async (route) => {
    if (failSave) {
      await route.fulfill({ status: 503, json: { error: "Acceptance event save failure" } });
    } else {
      event.acknowledged_at = 101;
      await route.fulfill({ json: { acknowledged: event, ...history() } });
    }
  });
  await page.reload();
  await navigate(page, "Alerts");
  const table = page.getByRole("region", { name: "Saved alert events" });
  await expect(table).toContainText(event.message);
  await table.getByRole("button", { name: "Acknowledge event for Deleted threshold alert", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("Acceptance event save failure");
  await expect(table).toContainText("Awaiting acknowledgement");
  failSave = false;
  await table.getByRole("button", { name: "Acknowledge event for Deleted threshold alert", exact: true }).click();
  await expect(page.getByRole("status")).toHaveText("Alert event acknowledged.");
  await expect(table).not.toContainText(event.message);
  await page.getByRole("checkbox", { name: "Include acknowledged events", exact: true }).check();
  await expect(table).toContainText(event.message);
  await expect(table).toContainText("Acknowledged");
  await page.screenshot({ path: testInfo.outputPath("alert-event-acknowledged.png"), fullPage: true });
});

test("invalid paper and analytics input cannot create orders or qualifying risk results", async ({ page }) => {
  await navigate(page, "Paper");
  await page.getByLabel("Metadata JSON (optional)", { exact: true }).fill("[]");
  await page.getByRole("button", { name: "Submit Paper Order", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("Order metadata must be a JSON object.");
  await expect(page.getByText("No paper-order history.", { exact: true })).toBeVisible();
  await navigate(page, "Analytics");
  const mdd = page.locator("form.direct-mdd-form");
  await mdd.getByLabel("Wallet", { exact: true }).fill("not-a-wallet");
  await mdd.getByRole("button", { name: "Compute", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("valid 0x");
  await expect(mdd.getByLabel("Wallet", { exact: true })).toHaveValue("not-a-wallet");
  await expect(page.locator("section.panel").filter({ has: mdd }).getByText("Observed MDD %", { exact: true })).toHaveCount(0);
  await page.reload();
  await expect(page.getByText("No cached MDD audit artifacts.", { exact: true })).toBeVisible();
});

async function readMarket(request, marketId = "polymarket") {
  const response = await request.get("/api/state");
  expect(response.ok()).toBe(true);
  const state = await response.json();
  return state.markets.markets.find((market) => market.market_id === marketId);
}

async function patchMarket(request, baseURL, patch, marketId = "polymarket") {
  const current = await readMarket(request, marketId);
  const response = await request.patch(`/api/markets/${marketId}`, {
    headers: { Origin: baseURL }, data: { ...patch, expected_revision: current.configuration_revision }
  });
  expect(response.ok(), await response.text()).toBe(true);
  return readMarket(request, marketId);
}

function restoreGate(market) {
  return {
    live_trading_kill_switch: market.safety.live_trading_kill_switch,
    live_trading_max_size: market.safety.live_trading_max_size,
    live_trading_max_notional: market.safety.live_trading_max_notional,
    settings: { polymarket_order_management_enabled: market.settings.polymarket_order_management_enabled === true }
  };
}

test("refreshed gates match persisted settings and limited saves preserve unrelated safeguards", async ({ page, request, baseURL }) => {
  const original = await readMarket(request);
  try {
    await patchMarket(request, baseURL, { live_trading_kill_switch: false, live_trading_max_size: null });
    await page.reload();
    await expect(page.getByText("API ok", { exact: true })).toBeVisible();
    await navigate(page, "Live Safety");
    await expect(page.getByRole("checkbox", { name: "Kill switch", exact: true })).not.toBeChecked();
    const updated = await patchMarket(request, baseURL, { live_trading_kill_switch: true, settings: { polymarket_order_management_enabled: true } });
    await page.locator(".topbar").getByRole("button", { name: "Refresh", exact: true }).click();
    await expect(page.getByRole("checkbox", { name: "Kill switch", exact: true })).toBeChecked();
    await page.getByLabel("Max size", { exact: true }).fill("7");
    const mutation = page.waitForRequest((request) => request.method() === "PATCH" && new URL(request.url()).pathname === "/api/markets/polymarket");
    await page.getByRole("button", { name: "Save Gate", exact: true }).click();
    expect((await mutation).postDataJSON()).toEqual({ live_trading_max_size: "7", expected_revision: updated.configuration_revision });
    await expect(page.getByRole("button", { name: "Save Gate", exact: true })).toBeEnabled();
    const persisted = await readMarket(request);
    expect(persisted.safety.live_trading_kill_switch).toBe(true);
    expect(persisted.settings.polymarket_order_management_enabled).toBe(true);
    expect(Number(persisted.safety.live_trading_max_size)).toBe(7);
    await navigate(page, "Markets");
    await expect(page.getByRole("checkbox", { name: "Kill switch", exact: true })).toBeChecked();
    await expect(page.getByLabel("Max size", { exact: true })).toHaveValue("7");
  } finally {
    await patchMarket(request, baseURL, restoreGate(original));
  }
});

test("a concurrent safety change rejects a stale save and reloads current controls", async ({ page, request, baseURL }) => {
  const original = await readMarket(request);
  try {
    await patchMarket(request, baseURL, { live_trading_kill_switch: false, live_trading_max_notional: null });
    await page.reload();
    await expect(page.getByText("API ok", { exact: true })).toBeVisible();
    await navigate(page, "Live Safety");
    await page.getByLabel("Max notional", { exact: true }).fill("25");
    await patchMarket(request, baseURL, { live_trading_kill_switch: true, live_trading_max_notional: 13 });
    await page.getByRole("button", { name: "Save Gate", exact: true }).click();
    await expect(page.getByRole("alert")).toContainText("market_config_conflict");
    await expect(page.getByRole("checkbox", { name: "Kill switch", exact: true })).toBeChecked();
    await expect(page.getByLabel("Max notional", { exact: true })).toHaveValue("13");
    const persisted = await readMarket(request);
    expect(persisted.safety.live_trading_kill_switch).toBe(true);
    expect(Number(persisted.safety.live_trading_max_notional)).toBe(13);
  } finally {
    await patchMarket(request, baseURL, restoreGate(original));
  }
});

test("preflight audits expire on edits, gate changes, failed runs and superseded responses", async ({ page, request }) => {
  await navigate(page, "Live Safety");
  const state = await (await request.get("/api/state")).json();
  let fail = false;
  let hold = false;
  let release;
  let finish;
  const held = new Promise((resolve) => { release = resolve; });
  const finished = new Promise((resolve) => { finish = resolve; });
  await page.route("**/api/live-safety/preflight", async (route) => {
    const body = route.request().postDataJSON();
    const delayed = hold;
    if (delayed) await held;
    try {
      await route.fulfill({ status: fail ? 503 : 200, json: fail ? { error: "Acceptance preflight unavailable" } : {
        ok: true, blocked: false, message: "Fixture audit passed without executing.",
        order: { market_id: body.market_id, contract_id: body.contract_id, side: body.side,
          size: Number(body.size), limit_price: Number(body.limit_price), approx_notional: Number(body.size) * Number(body.limit_price), metadata_keys: [] },
        preflight: { display_name: "Fixture", feature: "live_trading", max_size: 10, max_notional: 20, warnings: [] },
        live_safety: { ...state.live_safety, status: "ready", tone: "good", blockers: [] }
      } });
    } finally {
      if (delayed) finish();
    }
  });
  await page.getByLabel("Contract", { exact: true }).fill("fixture-contract");
  await page.getByLabel("Size", { exact: true }).fill("2.5");
  await page.getByLabel("Limit", { exact: true }).fill("0.4");
  const run = () => page.getByRole("button", { name: "Run Preflight", exact: true }).click();
  const audit = page.locator("[data-preflight-result]");
  await run();
  await expect(audit).toHaveAttribute("data-preflight-result", "passed");
  await page.getByLabel("Size", { exact: true }).fill("9999");
  await expect(audit).toHaveCount(0);
  await run();
  await expect(audit).toHaveAttribute("data-preflight-result", "passed");
  await page.getByRole("checkbox", { name: "Kill switch", exact: true }).check();
  await expect(audit).toHaveCount(0);
  await run();
  await expect(audit).toHaveAttribute("data-preflight-result", "passed");
  fail = true;
  await run();
  await expect(page.getByRole("alert")).toContainText("Acceptance preflight unavailable");
  await expect(audit).toHaveCount(0);
  fail = false;
  hold = true;
  const started = page.waitForRequest("**/api/live-safety/preflight");
  await run(); await started;
  await page.getByLabel("Size", { exact: true }).fill("8");
  hold = false;
  await run();
  await expect(audit).toContainText("BUY 8.0000");
  release(); await finished;
  await expect(audit).toContainText("BUY 8.0000");
  await expect(audit).not.toContainText("9,999.0000");
});

test("responses from a previous market cannot replace the current market data", async ({ page, request, baseURL }) => {
  await navigate(page, "Markets");
  const selected = await page.getByRole("combobox", { name: "Selected market", exact: true }).inputValue();
  const target = selected === "kalshi" ? "polymarket" : "kalshi";
  let release;
  let finish;
  const held = new Promise((resolve) => { release = resolve; });
  const finished = new Promise((resolve) => { finish = resolve; });
  let releaseQuote;
  let finishQuote;
  const heldQuote = new Promise((resolve) => { releaseQuote = resolve; });
  const finishedQuote = new Promise((resolve) => { finishQuote = resolve; });
  await page.route(`**/api/markets/${selected}/events?*`, async (route) => {
    await held;
    try {
      await route.fulfill({ json: { market_id: selected, events: [{ event_id: "old-event", title: "Previous market event", status: "open" }] } });
    } finally { finish(); }
  });
  await page.route(`**/api/markets/${target}/events?*`, (route) => route.fulfill({ json: {
    market_id: target, events: [{ event_id: "new-event", title: "Current market event", status: "open" }]
  } }));
  await page.route(`**/api/markets/${target}/price?*`, async (route) => {
    const contract = new URL(route.request().url()).searchParams.get("contract_id");
    if (contract === "previous-contract") await heldQuote;
    try {
      await route.fulfill({ json: { market_id: target, contract_id: contract,
        price: { last: contract === "previous-contract" ? 0.2 : 0.8, bid: null, ask: null, midpoint: null } } });
    } finally { if (contract === "previous-contract") finishQuote(); }
  });
  try {
    const started = page.waitForRequest(`**/api/markets/${selected}/events?*`);
    await page.getByRole("button", { name: "Discover events", exact: true }).click(); await started;
    await page.getByRole("combobox", { name: "Selected market", exact: true }).selectOption(target);
    await expect(page.getByRole("combobox", { name: "Selected market", exact: true })).toHaveValue(target);
    await page.getByRole("button", { name: "Discover events", exact: true }).click();
    await expect(page.getByText("Current market event", { exact: true })).toBeVisible();
    release(); await finished;
    await expect(page.getByText("Previous market event", { exact: true })).toHaveCount(0);
    await expect(page.getByText("Current market event", { exact: true })).toBeVisible();
    await page.getByLabel("Contract id", { exact: true }).fill("previous-contract");
    const quoteStarted = page.waitForRequest(`**/api/markets/${target}/price?*`);
    await page.getByRole("button", { name: "Read price", exact: true }).click(); await quoteStarted;
    await page.getByLabel("Contract id", { exact: true }).fill("current-contract");
    await page.getByRole("button", { name: "Read price", exact: true }).click();
    await expect(page.getByText("0.8000", { exact: true })).toBeVisible();
    releaseQuote(); await finishedQuote;
    await expect(page.getByText("0.2000", { exact: true })).toHaveCount(0);
    await expect(page.getByText("0.8000", { exact: true })).toBeVisible();
    await page.getByLabel("Contract id", { exact: true }).fill("another-contract");
    await expect(page.getByText("0.8000", { exact: true })).toHaveCount(0);
    await expect(page.getByRole("alert")).toHaveCount(0);
  } finally {
    release();
    releaseQuote();
    await request.patch("/api/config", { headers: { Origin: baseURL }, data: { selected_market_id: selected } });
  }
});

test("unavailable paper economics remain explicit and cannot be used as share quantities", async ({ page }) => {
  await page.route("**/api/state", async (route) => {
    const response = await route.fetch();
    const state = await response.json();
    state.paper.summary = { ...state.paper.summary, positions: 1, gross_size: null, entry_notional: null, net_notional: null,
      marked: 0, unrealized: null, realized: null, quote_currency: null, unavailable_positions: 1 };
    state.paper.accounting = { method: "retained_ledger", status: "incomplete", scope: "retained_ledger_simulation",
      opening_inventory_assumption: "empty", execution_assumptions: ["assumed_full_fill_at_limit"], quote_currency: null,
      closed_positions: 0, incomplete_reasons: ["Unsupported budget quantity model"] };
    state.paper.positions = [{ market_id: "manifold", contract_id: "budget-fixture", net_size: null, average_price: null,
      notional: null, trades: 1, mark_price: null, mark_source: "", marked_at: null, unrealized: null, realized: null,
      accounting_status: "incomplete", quantity_unit: "unavailable", currency: "MANA", incomplete_reasons: ["Budget is not a share count"] }];
    await route.fulfill({ response, json: state });
  });
  await page.reload();
  await expect(page.getByText("API ok", { exact: true })).toBeVisible();
  await navigate(page, "Paper");
  await expect(page.getByText("Retained paper ledger simulation — incomplete", { exact: true })).toBeVisible();
  await expect(page.getByText("Some orders assume a full fill at their limit price.", { exact: true })).toBeVisible();
  const row = page.getByRole("row").filter({ hasText: "budget-fixture" });
  await expect(row).toContainText("Quantity unavailable");
  await expect(row).toContainText("Budget is not a share count");
  await expect(row.getByRole("button", { name: "Use", exact: true })).toBeDisabled();
  await expect(row.getByRole("button", { name: "Mark", exact: true })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Refresh Marks", exact: true })).toBeDisabled();
});

test("a failed render offers recovery instead of leaving an empty interface", async ({ page }) => {
  const corruptState = async (route) => {
    const response = await route.fetch();
    const state = await response.json();
    state.markets.markets = null;
    await route.fulfill({ response, json: state });
  };
  await page.route("**/api/state", corruptState);
  await page.reload();
  await expect(page.getByRole("alert")).toContainText("The interface could not display the current data");
  await page.unroute("**/api/state", corruptState);
  await page.getByRole("button", { name: "Reload interface", exact: true }).click();
  await expect(page.getByText("API ok", { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Settings", level: 1, exact: true })).toBeVisible();
});

test("ambiguous saves retain their safe retry identity through a page reload", async ({ page }, testInfo) => {
  await navigate(page, "Wallets");
  const identity = `0x${createHash("sha256").update(`recovery-${testInfo.project.name}`).digest("hex").slice(0, 40)}`;
  const name = `Reload recovery ${testInfo.project.name}`;
  const form = page.locator("form.wallet-form");
  const fill = async () => {
    await form.getByLabel("Activity identity").fill(identity);
    await form.getByLabel("Name", { exact: true }).fill(name);
    await form.getByLabel("Enabled", { exact: true }).uncheck();
  };
  let first = true;
  const keys = [];
  await page.route("**/api/wallets", async (route) => {
    if (route.request().method() !== "POST") return route.fallback();
    keys.push(route.request().headers()["idempotency-key"]);
    const response = await route.fetch();
    if (first) {
      first = false;
      await route.fulfill({ status: 503, json: { error: "Acceptance response lost after persistence" } });
    } else await route.fulfill({ response });
  });
  await fill();
  await page.getByRole("button", { name: "Add Wallet", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("response lost after persistence");
  const recovery = await page.evaluate(() => sessionStorage.getItem("market-sentinel.pending-mutations.v1"));
  expect(recovery).not.toContain(identity);
  expect(recovery).not.toContain(name);
  expect(Object.keys(JSON.parse(recovery))[0]).toMatch(/^[a-f0-9]{64}$/);
  await page.reload();
  await expect(page.getByText("API ok", { exact: true })).toBeVisible();
  await fill();
  await page.getByRole("button", { name: "Add Wallet", exact: true }).click();
  await expect(page.getByRole("status")).toHaveText("Wallet watch added.");
  expect(keys).toHaveLength(2);
  expect(keys[0]).toBe(keys[1]);
  const row = page.getByRole("row").filter({ hasText: name });
  await expect(row).toHaveCount(1);
  await row.getByRole("button", { name: "Enable wallet", exact: true }).click();
  await expect(row.getByRole("button", { name: "Disable wallet", exact: true })).toBeVisible();
  const stateResponse = await page.request.get("/api/state");
  const state = await stateResponse.json();
  const pollKeys = [];
  const receiptId = "00000000-0000-4000-8000-000000000101";
  const nextReceiptId = "00000000-0000-4000-8000-000000000102";
  await page.route("**/api/wallets/poll", async (route) => {
    pollKeys.push(route.request().headers()["idempotency-key"]);
    expect(route.request().postDataJSON().acknowledge_receipt_id).toBe(pollKeys.length > 2 ? receiptId : undefined);
    if (pollKeys.length === 1) return route.fulfill({ status: 503, json: { error: "Acceptance batch receipt interrupted" } });
    await route.fulfill({ json: { wallets: state.wallets, copy: state.copy, message: "Delivered one bounded receipt.",
      activity: [], problems: [], polled_wallets: 1, has_more: pollKeys.length === 2,
      remaining_activity: pollKeys.length === 2 ? 3 : 0, delivered_activity: 0, consumed_filtered: 0, batch_limit: 100,
      delivery: { mode: "durable_replayable_batch", receipt_id: pollKeys.length > 2 ? nextReceiptId : receiptId, acknowledge_with_next_poll: true } } });
  });
  const poll = page.getByRole("button", { name: "Poll Now", exact: true });
  await poll.click();
  await expect(page.getByRole("alert")).toContainText("batch receipt interrupted");
  await page.reload();
  await expect(page.getByText("API ok", { exact: true })).toBeVisible();
  await poll.click();
  await expect(page.getByRole("status")).toContainText("3 queued event(s) remain. Poll again to request the next batch.");
  expect(pollKeys).toHaveLength(2);
  expect(pollKeys[0]).toBe(pollKeys[1]);
  expect(await page.evaluate(() => sessionStorage.getItem("market-sentinel.wallet-poll-receipt.v1"))).toBe(receiptId);
  await page.reload();
  await expect(page.getByText("API ok", { exact: true })).toBeVisible();
  await poll.click();
  await expect(page.getByRole("status")).toHaveText("Delivered one bounded receipt.");
  expect(pollKeys).toHaveLength(3);
  expect(pollKeys[2]).not.toBe(pollKeys[1]);
  expect(await page.evaluate(() => sessionStorage.getItem("market-sentinel.wallet-poll-receipt.v1"))).toBe(nextReceiptId);
  page.once("dialog", (dialog) => dialog.accept());
  await row.getByRole("button", { name: "Delete wallet", exact: true }).click();
  await expect(row).toHaveCount(0);
});
