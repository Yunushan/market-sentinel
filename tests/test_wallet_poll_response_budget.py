from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import http.client
import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from core.models import AppConfig, MAX_MUTATION_JOURNAL_ENTRIES, MAX_UNACKNOWLEDGED_WALLET_POLL_RECEIPTS, WalletWatch
from core.request_control import RequestDeadlineExceeded, current_request
from core.storage import load_config, save_config
from core.wallet_activity import activity_key
import web_api


WALLET_A = "0x" + "a" * 40
WALLET_B = "0x" + "b" * 40


def trade(index: int, *, timestamp: int | None = None, padding: int = 0, slug: str = "wanted") -> dict:
    return {"timestamp": timestamp if timestamp is not None else 100 + index,
            "transactionHash": f"fixture-tx-{index}", "asset": f"fixture-token-{index}",
            "side": "BUY", "price": .4, "size": 1, "slug": slug, "note": "x" * padding}


def fixture_loader(histories):
    """An offline keyset-page fixture that honors each immutable query window."""
    def load(wallet, **kwargs):
        eligible = [row for row in histories[wallet] if kwargs["start"] <= row["timestamp"] <= kwargs["end"]]
        eligible.sort(key=lambda row: row["timestamp"], reverse=True)
        offset = int(kwargs["cursor"].removeprefix("fixture-page-")) if kwargs["cursor"] else 0
        rows = eligible[offset:offset + kwargs["limit"]]
        following = offset + len(rows)
        cursor = f"fixture-page-{following}" if following < len(eligible) else None
        return {"data": rows, "pagination": {"next_cursor": cursor, "has_more": cursor is not None}}
    return load


def registry():
    adapter = SimpleNamespace(display_name="Polymarket", capabilities=SimpleNamespace(copy_trading=True))
    return SimpleNamespace(create=lambda *_args: adapter)


class WalletPollBatchTests(unittest.TestCase):
    def test_5000_large_events_are_delivered_as_lossless_chronological_byte_bounded_batches(self):
        cfg = AppConfig(wallets=[WalletWatch(wallet=WALLET_A, last_seen_ts=99)])
        histories = {WALLET_A: [trade(index, padding=4096) for index in range(5000)]}
        recent = []
        identities = []
        with patch("web_api.data_api.get_activity_page_v2", side_effect=fixture_loader(histories)):
            for _ in range(2):
                result = web_api.poll_wallet_activity(cfg, registry(), recent, limit=100)
                self.assertTrue(result["has_more"])
                self.assertEqual(result["remaining_activity"], 5000 - len(identities) - len(result["activity"]))
                self.assertGreater(len(result["activity"]), 0)
                self.assertLessEqual(len(result["activity"]), 100)
                self.assertLessEqual(len(web_api._json_bytes(result)), web_api.MAX_MUTATION_RESULT_BYTES)
                chronological = list(reversed(result["activity"]))
                self.assertEqual([item["timestamp"] for item in chronological],
                                 list(range(100 + len(identities), 100 + len(identities) + len(chronological))))
                identities.extend(item["id"] for item in chronological)
                self.assertEqual(cfg.wallets[0].last_seen_ts, 99 + len(identities))
        self.assertEqual(len(identities), len(set(identities)))
        self.assertTrue(all(len(item["raw"]["note"]) == 4096 for item in recent))

    def test_three_same_second_batches_preserve_every_distinct_fill(self):
        cfg = AppConfig(wallets=[WalletWatch(wallet=WALLET_A, last_seen_ts=100)])
        rows = [trade(index, timestamp=100) for index in range(300)]
        ids = []
        with patch("web_api.data_api.get_activity_page_v2", side_effect=fixture_loader({WALLET_A: rows})):
            for expected_remaining in (200, 100, 0):
                result = web_api.poll_wallet_activity(cfg, registry(), [], limit=100)
                self.assertEqual(len(result["activity"]), 100)
                self.assertEqual(result["remaining_activity"], expected_remaining)
                self.assertEqual(result["has_more"], expected_remaining > 0)
                ids.extend(item["id"] for item in result["activity"])
            self.assertEqual(web_api.poll_wallet_activity(cfg, registry(), [], limit=100)["activity"], [])
        self.assertEqual(set(ids), {activity_key(row) for row in rows})
        self.assertEqual(len(ids), 300)
        self.assertEqual(len(cfg.wallets[0].seen_activity_keys), 300)
        self.assertEqual(cfg.wallets[0].last_seen_ts, 100)

    def test_multiple_wallets_share_one_chronological_batch_and_filters_do_not_skip_pending_events(self):
        cfg = AppConfig(wallets=[WalletWatch(wallet=WALLET_A, only_market_slug="wanted"), WalletWatch(wallet=WALLET_B)])
        rows = {WALLET_A: [trade(index, timestamp=100 + index, slug="wanted" if index % 2 else "other")
                           for index in range(5)],
                WALLET_B: [trade(10, timestamp=99), trade(11, timestamp=100)]}
        with patch("web_api.data_api.get_activity_page_v2", side_effect=fixture_loader(rows)):
            first = web_api.poll_wallet_activity(cfg, registry(), [], max_batch_items=2)
            self.assertEqual([item["timestamp"] for item in reversed(first["activity"])], [99, 100])
            self.assertEqual(first["consumed_filtered"], 1)
            self.assertEqual(first["remaining_activity"], 2)
            self.assertEqual([wallet.last_seen_ts for wallet in cfg.wallets], [100, 100])
            second = web_api.poll_wallet_activity(cfg, registry(), [], max_batch_items=1)
            self.assertEqual([item["timestamp"] for item in second["activity"]], [101])
            self.assertEqual(cfg.wallets[0].last_seen_ts, 102)
            self.assertEqual(second["remaining_activity"], 1)
            third = web_api.poll_wallet_activity(cfg, registry(), [])
            self.assertEqual([item["timestamp"] for item in third["activity"]], [103])
            self.assertEqual(cfg.wallets[0].last_seen_ts, 104)
            self.assertFalse(third["has_more"])

    def test_later_wallet_fetch_failure_keeps_all_cursors_and_recent_history(self):
        cfg = AppConfig(wallets=[WalletWatch(wallet=WALLET_A), WalletWatch(wallet=WALLET_B)])
        recent = [{"id": "existing"}]
        before = deepcopy(cfg.to_dict()), deepcopy(recent)

        def load(wallet, **_kwargs):
            if wallet == WALLET_B:
                raise OSError("fixture second wallet failure")
            return {"data": [trade(0)], "pagination": {"next_cursor": None}}

        with patch("web_api.data_api.get_activity_page_v2", side_effect=load), patch("web_api.copy_trade_preview_from_activity") as enrich:
            result = web_api.poll_wallet_activity(cfg, registry(), recent)
        self.assertEqual(result["activity"], [])
        self.assertIsNone(result["remaining_activity"])
        self.assertIn("second wallet failure", result["problems"][0])
        self.assertEqual((cfg.to_dict(), recent), before)
        enrich.assert_not_called()

    def test_copy_failure_and_aggregate_deadline_roll_back_staged_events(self):
        for timeout in (False, True):
            with self.subTest(timeout=timeout):
                cfg = AppConfig(wallets=[WalletWatch(wallet=WALLET_A)])
                recent = [{"id": "existing"}]
                before = deepcopy(cfg.to_dict()), deepcopy(recent)
                calls = []
                clock = [100.0]

                def preview(*_args, calls=calls, timeout=timeout, clock=clock):
                    calls.append(True)
                    if timeout:
                        # Deterministic work cost: the first preview fits and
                        # the second exceeds the same aggregate deadline.
                        clock[0] += .04
                        current_request().check()
                    elif len(calls) == 2:
                        raise OSError("fixture preview failure")
                    return {"status": "simulation"}

                started = time.monotonic()
                with patch("web_api.data_api.get_activity_page_v2", side_effect=fixture_loader({WALLET_A: [trade(0), trade(1)]})), \
                     patch("web_api.copy_trade_preview_from_activity", side_effect=preview), \
                     patch("core.request_control.time", SimpleNamespace(monotonic=lambda clock=clock: clock[0])), \
                     self.assertRaises(RequestDeadlineExceeded if timeout else OSError):
                    web_api.poll_wallet_activity(cfg, registry(), recent, timeout_seconds=.06 if timeout else 1)
                self.assertEqual((cfg.to_dict(), recent), before)
                self.assertEqual(len(calls), 2)
                self.assertLess(time.monotonic() - started, .5)

    def test_a_single_oversized_event_or_full_response_never_advances_or_enriches(self):
        for full_response in (False, True):
            with self.subTest(full_response=full_response):
                cfg = AppConfig(wallets=[WalletWatch(wallet=WALLET_A)])
                recent = [{"id": "existing"}]
                before = deepcopy(cfg.to_dict()), deepcopy(recent)
                rows = [trade(0, padding=0 if full_response else web_api.MAX_MUTATION_RESULT_BYTES)]

                def validate(_cfg, result, _recent):
                    if result["activity"]:
                        raise web_api.HttpResponseTooLargeError("fixture full-response overhead")

                with patch("web_api.data_api.get_activity_page_v2", side_effect=fixture_loader({WALLET_A: rows})), \
                     patch("web_api.copy_trade_preview_from_activity") as enrich, \
                     self.assertRaises(web_api.HttpResponseTooLargeError):
                    web_api.poll_wallet_activity(cfg, registry(), recent, response_validator=validate if full_response else None)
                self.assertEqual((cfg.to_dict(), recent), before)
                enrich.assert_not_called()

    def test_only_the_deliverable_prefix_is_enriched(self):
        cfg = AppConfig(wallets=[WalletWatch(wallet=WALLET_A)])
        rows = [trade(index) for index in range(5000)]
        with patch("web_api.data_api.get_activity_page_v2", side_effect=fixture_loader({WALLET_A: rows})), \
             patch("web_api.copy_trade_preview_from_activity", return_value={"status": "simulation"}) as preview:
            result = web_api.poll_wallet_activity(cfg, registry(), [], limit=100)
        self.assertEqual(len(result["activity"]), 100)
        self.assertEqual(preview.call_count, 100)
        self.assertEqual(result["remaining_activity"], 4900)

    def test_one_deadline_covers_both_source_fetch_and_copy_work(self):
        cfg = AppConfig(wallets=[WalletWatch(wallet=WALLET_A)])
        before = deepcopy(cfg.to_dict())
        controls = []
        clock = [100.0]

        def load(*_args, **_kwargs):
            controls.append(current_request())
            clock[0] += .035
            current_request().check()
            return {"data": [trade(0)], "pagination": {"next_cursor": None}}

        def preview(*_args):
            controls.append(current_request())
            clock[0] += .035
            current_request().check()
            return {"status": "simulation"}

        with patch("web_api.data_api.get_activity_page_v2", side_effect=load), \
             patch("web_api.copy_trade_preview_from_activity", side_effect=preview), \
             patch("core.request_control.time", SimpleNamespace(monotonic=lambda: clock[0])), \
             self.assertRaises(RequestDeadlineExceeded):
            web_api.poll_wallet_activity(cfg, registry(), [], timeout_seconds=.05)
        self.assertEqual(len(controls), 2)
        self.assertIs(controls[0], controls[1])
        self.assertEqual(cfg.to_dict(), before)


class WalletPollHttpBudgetTests(unittest.TestCase):
    @contextmanager
    def server(self, root):
        frontend = root / "frontend"
        frontend.mkdir(exist_ok=True)
        (frontend / "index.html").write_text("<html></html>", encoding="utf-8")
        with patch("web_api.DEFAULT_FRONTEND_DIR", frontend):
            server = web_api.ReactGuiServer(("127.0.0.1", 0), web_api.ReactGuiHandler,
                                            frontend_dir=frontend, config_path=root / "config.json",
                                            adapter_registry=registry())
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            yield server
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=2)

    def post(self, server, key="fixture-poll-key", limit=100, acknowledge=None):
        # A 5,000-event fixture performs the complete source-window audit under
        # coverage too. Let the server's real 60-second route budget govern it.
        connection = http.client.HTTPConnection(*server.server_address, timeout=75)
        try:
            headers = {"Content-Type": "application/json", "Idempotency-Key": key}
            body = {"limit": limit}
            if acknowledge is not None:
                body["acknowledge_receipt_id"] = acknowledge
            connection.request("POST", "/api/wallets/poll", json.dumps(body), headers)
            response = connection.getresponse()
            body = response.read()
            return response.status, json.loads(body), len(body)
        finally:
            connection.close()

    def test_large_backlog_replays_exact_batch_after_lost_response_and_restart(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cfg = AppConfig(wallets=[WalletWatch(wallet=WALLET_A)])
            save_config(cfg, root / "config.json")
            rows = [trade(index, padding=4096) for index in range(5000)]
            with self.server(root) as server, patch("web_api.data_api.get_activity_page_v2", side_effect=fixture_loader({WALLET_A: rows})):
                with patch.object(web_api.ReactGuiHandler, "_write_response_body", side_effect=web_api.HttpClientDisconnected("fixture lost response")), \
                     self.assertRaises((http.client.IncompleteRead, http.client.RemoteDisconnected)):
                    self.post(server)
            committed = load_config(root / "config.json")
            receipt = committed.mutation_journal[-1].response
            self.assertNotIn("mutation_result", receipt)
            self.assertTrue(receipt["has_more"])
            self.assertGreater(receipt["delivered_activity"], 0)
            self.assertLess(receipt["delivered_activity"], 100)
            self.assertEqual(receipt["remaining_activity"], 5000 - receipt["delivered_activity"])
            committed_bytes = (root / "config.json").read_bytes()
            with self.server(root) as server, patch("web_api.data_api.get_activity_page_v2") as fetch:
                status, replay, size = self.post(server)
                self.assertEqual(status, 200)
                self.assertEqual(replay["activity"], receipt["activity"])
                self.assertEqual(replay["delivery"], receipt["delivery"])
                self.assertIn("wallets", replay)
                self.assertIn("copy", replay)
                self.assertEqual(replay["wallets"]["recent_activity"], receipt["activity"])
                repeated = self.post(server)[1]
                self.assertEqual(repeated["wallets"]["recent_activity"], receipt["activity"])
                self.assertLess(size, web_api.MAX_HTTP_RESPONSE_BYTES)
                fetch.assert_not_called()
                self.assertEqual((root / "config.json").read_bytes(), committed_bytes)
                self.assertEqual(self.post(server, limit=25)[0], 409)
            with self.server(root) as server, patch("web_api.data_api.get_activity_page_v2", side_effect=fixture_loader({WALLET_A: rows})):
                status, following, _size = self.post(server, key="fixture-next-poll")
                self.assertEqual(status, 200)
                self.assertFalse({item["id"] for item in following["activity"]} & {item["id"] for item in receipt["activity"]})
                self.assertEqual(following["remaining_activity"], 5000 - receipt["delivered_activity"] - following["delivered_activity"])

    def test_save_failure_response_budget_and_deadline_preserve_original_disk_and_runtime(self):
        for failure in ("save", "response", "full_response", "deadline"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                save_config(AppConfig(wallets=[WalletWatch(wallet=WALLET_A)]), root / "config.json")
                disk = (root / "config.json").read_bytes()
                rows = [trade(0, padding=web_api.MAX_MUTATION_RESULT_BYTES if failure == "response"
                              else 4096 if failure == "full_response" else 0)]
                with self.server(root) as server:
                    server.wallet_recent_activity = [{"id": "existing"}]
                    before = deepcopy(server.wallet_recent_activity), deepcopy(server.wallet_polling)

                    def preview(*_args, failure=failure):
                        if failure == "deadline":
                            current_request().sleep(.2)
                        return {"status": "simulation"}

                    with patch("web_api.data_api.get_activity_page_v2", side_effect=fixture_loader({WALLET_A: rows})), \
                         patch("web_api.copy_trade_preview_from_activity", side_effect=preview), \
                         patch("web_api.WALLET_POLL_TIMEOUT_SECONDS", .05 if failure == "deadline" else 60), \
                         patch("web_api.MAX_HTTP_RESPONSE_BYTES", 6500 if failure == "full_response" else web_api.MAX_HTTP_RESPONSE_BYTES):
                        if failure == "save":
                            with patch.object(web_api.ReactGuiHandler, "_save_config", side_effect=OSError("fixture save failed")):
                                status, response, _size = self.post(server)
                        else:
                            status, response, _size = self.post(server)
                    self.assertEqual(status, 500 if failure == "save" else 503)
                    self.assertIn("error", response)
                    self.assertEqual((root / "config.json").read_bytes(), disk)
                    self.assertEqual((server.wallet_recent_activity, server.wallet_polling), before)

    def test_unrelated_journal_pressure_cannot_evict_an_unreceived_poll_batch(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            save_config(AppConfig(wallets=[WalletWatch(wallet=WALLET_A)]), root / "config.json")
            with self.server(root) as server, patch("web_api.data_api.get_activity_page_v2", side_effect=fixture_loader({WALLET_A: [trade(0)]})):
                with patch.object(web_api.ReactGuiHandler, "_write_response_body", side_effect=web_api.HttpClientDisconnected("fixture lost response")), \
                     self.assertRaises((http.client.IncompleteRead, http.client.RemoteDisconnected)):
                    self.post(server)
            cfg = load_config(root / "config.json")
            receipt = deepcopy(cfg.mutation_journal[0])
            for index in range(MAX_MUTATION_JOURNAL_ENTRIES + 20):
                entry = web_api._new_mutation_journal_entry(f"fixture-unrelated-{index}", "POST", "/api/wallets", {}, live=False)
                entry.state = "completed"
                entry.response_status = 200
                entry.response = {"ok": True}
                cfg.append_mutation_journal(entry)
            self.assertEqual(len(cfg.mutation_journal), MAX_MUTATION_JOURNAL_ENTRIES)
            self.assertIn(receipt.id, {entry.id for entry in cfg.mutation_journal})
            save_config(cfg, root / "config.json")
            with self.server(root) as server, patch("web_api.data_api.get_activity_page_v2") as fetch:
                status, response, _size = self.post(server)
                self.assertEqual(status, 200)
                self.assertEqual(response["activity"], receipt.response["activity"])
                fetch.assert_not_called()

    def test_receipt_capacity_fails_before_fetch_and_ack_allows_one_new_batch(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cfg = AppConfig(wallets=[WalletWatch(wallet=WALLET_A)])
            for index in range(MAX_UNACKNOWLEDGED_WALLET_POLL_RECEIPTS):
                entry = web_api._new_mutation_journal_entry(f"fixture-held-{index}", "POST", "/api/wallets/poll", {"limit": 100}, live=False)
                entry.state = "completed"
                entry.response_status = 200
                entry.outcome_code = "wallet_delivery_batch_committed"
                entry.response = {"activity": [], "delivery": {"receipt_id": entry.id}}
                cfg.append_mutation_journal(entry)
            prior_id = cfg.mutation_journal[0].id
            save_config(cfg, root / "config.json")
            before = (root / "config.json").read_bytes()
            with self.server(root) as server, patch("web_api.data_api.get_activity_page_v2") as fetch:
                status, response, _size = self.post(server)
                self.assertEqual(status, 503)
                self.assertEqual(response["error"]["code"], "wallet_poll_receipts_full")
                fetch.assert_not_called()
                self.assertEqual((root / "config.json").read_bytes(), before)
                fetch.return_value = {"data": [], "pagination": {"next_cursor": None}}
                self.assertEqual(self.post(server, key="fixture-ack", acknowledge=prior_id)[0], 200)
                fetch.assert_called_once()
            committed = load_config(root / "config.json")
            self.assertEqual(next(entry for entry in committed.mutation_journal if entry.id == prior_id).outcome_code,
                             "wallet_delivery_batch_acknowledged")
            self.assertEqual(sum(entry.path == "/api/wallets/poll" and entry.outcome_code != "wallet_delivery_batch_acknowledged"
                                 for entry in committed.mutation_journal), MAX_UNACKNOWLEDGED_WALLET_POLL_RECEIPTS)
            with self.server(root) as server, patch("web_api.data_api.get_activity_page_v2") as fetch:
                self.assertEqual(self.post(server, key="fixture-ack", acknowledge=prior_id)[0], 200)
                fetch.assert_not_called()  # An ack retry replays the same new batch after restart.
                self.assertEqual(self.post(server, key="fixture-extra", acknowledge=prior_id)[0], 503)
                fetch.assert_not_called()  # A repeated old ack cannot bypass capacity.

    def test_failed_new_poll_does_not_acknowledge_prior_and_lost_new_response_remains_pinned(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            save_config(AppConfig(wallets=[WalletWatch(wallet=WALLET_A)]), root / "config.json")
            histories = {WALLET_A: [trade(0)]}
            with self.server(root) as server, patch("web_api.data_api.get_activity_page_v2", side_effect=fixture_loader(histories)):
                _status, first, _size = self.post(server)
                prior_id = first["delivery"]["receipt_id"]
            before = (root / "config.json").read_bytes()
            with self.server(root) as server, patch("web_api.data_api.get_activity_page_v2", side_effect=OSError("fixture incomplete source")):
                self.assertEqual(self.post(server, key="fixture-next", acknowledge=prior_id)[0], 503)
                self.assertEqual((root / "config.json").read_bytes(), before)
            histories[WALLET_A].append(trade(1))
            with self.server(root) as server, patch("web_api.data_api.get_activity_page_v2", side_effect=fixture_loader(histories)):
                with patch.object(web_api.ReactGuiHandler, "_write_response_body", side_effect=web_api.HttpClientDisconnected("fixture lost next response")), \
                     self.assertRaises((http.client.IncompleteRead, http.client.RemoteDisconnected)):
                    self.post(server, key="fixture-next", acknowledge=prior_id)
            cfg = load_config(root / "config.json")
            prior = next(entry for entry in cfg.mutation_journal if entry.id == prior_id)
            following = next(entry for entry in cfg.mutation_journal if entry.id != prior_id)
            self.assertEqual(prior.outcome_code, "wallet_delivery_batch_acknowledged")
            self.assertEqual(following.outcome_code, "wallet_delivery_batch_committed")
            self.assertEqual(following.response["activity"][0]["timestamp"], 101)
            with self.server(root) as server, patch("web_api.data_api.get_activity_page_v2") as fetch:
                status, replay, _size = self.post(server, key="fixture-next", acknowledge=prior_id)
                self.assertEqual(status, 200)
                self.assertEqual(replay["delivery"]["receipt_id"], following.id)
                self.assertEqual(replay["activity"], following.response["activity"])
                fetch.assert_not_called()
                self.assertEqual(self.post(server, key="fixture-invalid", acknowledge="unknown-receipt")[0], 400)
                fetch.assert_not_called()

    def test_acknowledgment_is_idempotent_while_prior_receipt_is_retained(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            save_config(AppConfig(wallets=[WalletWatch(wallet=WALLET_A)]), root / "config.json")
            with self.server(root) as server, patch("web_api.data_api.get_activity_page_v2", return_value={"data": [], "pagination": {"next_cursor": None}}):
                first_id = self.post(server)[1]["delivery"]["receipt_id"]
                self.assertEqual(self.post(server, key="fixture-ack-once", acknowledge=first_id)[0], 200)
                self.assertEqual(self.post(server, key="fixture-ack-again", acknowledge=first_id)[0], 200)
            cfg = load_config(root / "config.json")
            self.assertEqual(next(entry for entry in cfg.mutation_journal if entry.id == first_id).outcome_code,
                             "wallet_delivery_batch_acknowledged")


if __name__ == "__main__":
    unittest.main()
