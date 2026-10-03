import assert from "node:assert/strict";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

const { AlertEventHistory } = await import("../.test-dist/src/alert-event-history.js");

test("saved events remain visible independently of deleted alert configuration", () => {
  const html = renderToStaticMarkup(createElement(AlertEventHistory, {
    history: {
      events: [
        { id: "pending", alert_id: "deleted-alert", market_id: "polymarket", contract_id: "123", label: "Lost alert", message: "<script>crossing</script>", created_at: 100, acknowledged_at: 0 },
        { id: "acknowledged", message: "already acknowledged", created_at: 99, acknowledged_at: 101 }
      ],
      counts: { total: 2, unacknowledged: 1 }, capacity: 1000
    }, busyEventId: null, onAcknowledge() {}, onRefresh() {}
  }));
  assert.match(html, /1 awaiting acknowledgement/);
  assert.match(html, /Acknowledge event for Lost alert/);
  assert.match(html, /polymarket:123/);
  assert.match(html, /&lt;script&gt;crossing&lt;\/script&gt;/);
  assert.equal(html.includes("<script>"), false);
  assert.equal(html.includes("already acknowledged"), false);
});

test("busy acknowledgement keeps the pending event visible and prevents duplicate submission", () => {
  const html = renderToStaticMarkup(createElement(AlertEventHistory, {
    history: { events: [{ id: "pending", label: "Threshold", message: "Price crossing", created_at: 100, acknowledged_at: 0 }], counts: { total: 1, unacknowledged: 1 }, capacity: 1000 },
    busyEventId: "pending", onAcknowledge() {}, onRefresh() {}
  }));
  assert.match(html, /Price crossing/);
  assert.match(html, /disabled=""/);
  assert.match(html, /Saving/);
});
