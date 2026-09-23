from __future__ import annotations

import hashlib
import http.client
import json
import socket
import tempfile
import threading
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from http.server import HTTPServer
from pathlib import Path
from unittest import mock

from scripts.collect_prometheus_delivery_evidence import (
    DEFAULT_ONCALL_RECEIPT_PATH_PREFIX,
    DEFAULT_ONCALL_RECEIVER_NAME,
    DEFAULT_ONCALL_WEBHOOK_PATH,
    ONCALL_RECEIPT_FIELDS,
    expected_labels,
    utc_now,
    validate_oncall_receipt,
)
from scripts.oncall_receipt_bridge import ACK_PATH_PREFIX, BridgeConfig, BridgeError, ReceiptBridge, make_handler, smtp_dispatch


REVISION = "a" * 40
DEPLOYMENT = "b" * 64
BINDING = hashlib.sha256(b"unique production challenge").hexdigest()
FINGERPRINT = "0123456789abcdef"
WEBHOOK_TOKEN = "webhook-token-abcdefghijklmnopqrstuvwxyz-123456"
ACK_TOKEN = "operator-token-abcdefghijklmnopqrstuvwxyz-123456"


def _payload(starts_at: str) -> dict[str, object]:
    labels = expected_labels(
        binding=BINDING, source_revision=REVISION, deployment_identity_sha256=DEPLOYMENT,
        run_id=7391, run_attempt=1,
    )
    return {
        "receiver": DEFAULT_ONCALL_RECEIVER_NAME,
        "status": "firing",
        "version": "4",
        "alerts": [{
            "status": "firing",
            "labels": labels,
            "annotations": {"attestation_binding": BINDING},
            "startsAt": starts_at,
            "fingerprint": FINGERPRINT,
        }],
    }


class OncallReceiptBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.sent: list[dict[str, object]] = []
        config = BridgeConfig(
            database=Path(self.temporary.name) / "receipts.sqlite",
            webhook_token=WEBHOOK_TOKEN,
            acknowledgement_token=ACK_TOKEN,
            acknowledger_identity="primary-oncall-operator",
            smtp_host="smtp.provider.tld",
            smtp_port=587,
            smtp_username="service@example.com",
            smtp_password="test-password",
            smtp_from="service@example.com",
            smtp_to="operator@example.com",
        )
        self.bridge = ReceiptBridge(config, dispatch=lambda _config, record: self.sent.append(record))
        self.addCleanup(self.bridge.close)
        self.server = HTTPServer(("127.0.0.1", 0), make_handler(self.bridge))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.thread.join, 2)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def request(self, method: str, path: str, *, token: str = "", body: bytes | None = None) -> tuple[int, dict]:
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def webhook(self, *, starts_at: str | None = None) -> bytes:
        return json.dumps(_payload(starts_at or utc_now()), sort_keys=True).encode("utf-8")

    def test_delivery_requires_distinct_human_acknowledgement_and_matches_collector_contract(self) -> None:
        raw = self.webhook()
        self.assertEqual(self.request("POST", DEFAULT_ONCALL_WEBHOOK_PATH, token=ACK_TOKEN, body=raw)[0], 401)
        status, accepted = self.request("POST", DEFAULT_ONCALL_WEBHOOK_PATH, token=WEBHOOK_TOKEN, body=raw)
        self.assertEqual(status, 202)
        self.assertEqual(accepted["status"], "dispatched")
        self.assertEqual(len(self.sent), 1)

        receipt_path = f"{DEFAULT_ONCALL_RECEIPT_PATH_PREFIX}/{BINDING}"
        self.assertEqual(self.request("GET", receipt_path, token=WEBHOOK_TOKEN)[0], 404)
        self.assertEqual(self.request("GET", receipt_path, token=ACK_TOKEN)[0], 401)
        ack_path = f"{ACK_PATH_PREFIX}/{BINDING}"
        ack_body = json.dumps({"delivery_id": accepted["delivery_id"], "acknowledgement": "human"}).encode()
        self.assertEqual(self.request("POST", ack_path, token=WEBHOOK_TOKEN, body=ack_body)[0], 401)
        self.assertEqual(self.request("POST", ack_path, token=ACK_TOKEN, body=ack_body)[0], 200)
        self.bridge.config = replace(self.bridge.config, smtp_to="replacement@example.com")
        status, receipt = self.request("GET", receipt_path, token=WEBHOOK_TOKEN)
        self.assertEqual(status, 200)
        self.assertEqual(set(receipt), ONCALL_RECEIPT_FIELDS)
        self.assertEqual(receipt["webhook_body_sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(receipt["oncall_provider"], "smtp")
        self.assertEqual(receipt["oncall_channel_sha256"], hashlib.sha256(b"operator@example.com").hexdigest())
        labels = _payload("unused")["alerts"][0]["labels"]
        observed = datetime.now(timezone.utc) + timedelta(seconds=1)
        validate_oncall_receipt(
            receipt, source_revision=REVISION, deployment_identity_sha256=DEPLOYMENT,
            run_id=7391, run_attempt=1, binding=BINDING, alert_fingerprint=FINGERPRINT,
            labels=labels, alert_starts_at=json.loads(raw)["alerts"][0]["startsAt"],
            started_at=observed - timedelta(minutes=1), observed_at=observed,
        )
        self.assertEqual(self.request("POST", DEFAULT_ONCALL_WEBHOOK_PATH, token=WEBHOOK_TOKEN, body=raw)[1]["status"], "acknowledged")
        self.assertEqual(len(self.sent), 1)
        audit = self.bridge.db.execute("SELECT event FROM audit_events ORDER BY id").fetchall()
        self.assertEqual(audit, [("received",), ("dispatched",), ("human_acknowledged",)])

    def test_mismatched_delivery_and_changed_webhook_cannot_claim_receipt(self) -> None:
        raw = self.webhook()
        accepted = self.request("POST", DEFAULT_ONCALL_WEBHOOK_PATH, token=WEBHOOK_TOKEN, body=raw)[1]
        ack_path = f"{ACK_PATH_PREFIX}/{BINDING}"
        forged = json.dumps({"delivery_id": "0" * 32, "acknowledgement": "human"}).encode()
        self.assertEqual(self.request("POST", ack_path, token=ACK_TOKEN, body=forged)[0], 400)
        changed = raw.replace(b'"version": "4"', b'"version":"4"')
        self.assertNotEqual(changed, raw)
        self.assertEqual(self.request("POST", DEFAULT_ONCALL_WEBHOOK_PATH, token=WEBHOOK_TOKEN, body=changed)[0], 400)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.bridge.receipt(BINDING), None)
        self.assertEqual(len(accepted["delivery_id"]), 32)

    def test_stale_and_malformed_alerts_fail_closed(self) -> None:
        stale = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
        self.assertEqual(self.request("POST", DEFAULT_ONCALL_WEBHOOK_PATH, token=WEBHOOK_TOKEN, body=self.webhook(starts_at=stale))[0], 400)
        duplicate_key = b'{"receiver":"x","receiver":"y"}'
        self.assertEqual(self.request("POST", DEFAULT_ONCALL_WEBHOOK_PATH, token=WEBHOOK_TOKEN, body=duplicate_key)[0], 400)
        extra_label = _payload(utc_now())
        extra_label["alerts"][0]["labels"]["unexpected"] = "value"
        self.assertEqual(
            self.request("POST", DEFAULT_ONCALL_WEBHOOK_PATH, token=WEBHOOK_TOKEN,
                         body=json.dumps(extra_label).encode())[0], 400,
        )
        self.assertEqual(self.sent, [])

    def test_failed_dispatch_keeps_pending_record_for_retry_with_same_delivery_id(self) -> None:
        calls = 0

        def fail_once(_config: BridgeConfig, record: dict[str, object]) -> None:
            nonlocal calls
            calls += 1
            self.sent.append(record)
            if calls == 1:
                raise BridgeError("provider unavailable")

        self.bridge.dispatch = fail_once
        raw = self.webhook()
        self.assertEqual(self.request("POST", DEFAULT_ONCALL_WEBHOOK_PATH, token=WEBHOOK_TOKEN, body=raw)[0], 503)
        self.assertIsNone(self.bridge.receipt(BINDING))
        ack = json.dumps({"delivery_id": self.sent[0]["delivery_id"], "acknowledgement": "human"}).encode()
        self.assertEqual(self.request("POST", f"{ACK_PATH_PREFIX}/{BINDING}", token=ACK_TOKEN, body=ack)[0], 400)
        status, accepted = self.request("POST", DEFAULT_ONCALL_WEBHOOK_PATH, token=WEBHOOK_TOKEN, body=raw)
        self.assertEqual(status, 202)
        self.assertEqual(self.sent[0]["delivery_id"], accepted["delivery_id"])
        self.assertEqual(self.sent[1]["delivery_id"], accepted["delivery_id"])

    def test_smtp_dispatch_requires_starttls_and_authenticated_acceptance(self) -> None:
        smtp = mock.MagicMock()
        smtp.__enter__.return_value = smtp
        smtp.has_extn.return_value = True
        smtp.send_message.return_value = {}
        public_dns = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 587))]
        with mock.patch("scripts.oncall_receipt_bridge.socket.getaddrinfo", return_value=public_dns), mock.patch(
            "scripts.oncall_receipt_bridge._PinnedSMTP", return_value=smtp
        ):
            smtp_dispatch(self.bridge.config, {"binding_sha256": BINDING,
                                               "alert_fingerprint": FINGERPRINT,
                                               "delivery_id": "1" * 32})
        smtp.starttls.assert_called_once()
        smtp.login.assert_called_once_with("service@example.com", "test-password")
        self.assertEqual(smtp.send_message.call_count, 1)
        smtp.has_extn.return_value = False
        with mock.patch("scripts.oncall_receipt_bridge.socket.getaddrinfo", return_value=public_dns), mock.patch(
            "scripts.oncall_receipt_bridge._PinnedSMTP", return_value=smtp
        ):
            with self.assertRaisesRegex(BridgeError, "STARTTLS"):
                smtp_dispatch(self.bridge.config, {"binding_sha256": BINDING,
                                                   "alert_fingerprint": FINGERPRINT,
                                                   "delivery_id": "2" * 32})

    def test_smtp_dispatch_rejects_private_provider_address(self) -> None:
        private_dns = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 587))]
        with mock.patch("scripts.oncall_receipt_bridge.socket.getaddrinfo", return_value=private_dns):
            with self.assertRaisesRegex(ValueError, "public"):
                smtp_dispatch(self.bridge.config, {"binding_sha256": BINDING,
                                                   "alert_fingerprint": FINGERPRINT,
                                                   "delivery_id": "3" * 32})

    def test_private_smtp_dns_is_retryable_webhook_failure(self) -> None:
        self.bridge.dispatch = smtp_dispatch
        raw = self.webhook()
        with mock.patch("scripts.oncall_receipt_bridge._resolve_public_https_origin",
                        side_effect=ValueError("SMTP relay must resolve to public addresses")):
            status, response = self.request("POST", DEFAULT_ONCALL_WEBHOOK_PATH, token=WEBHOOK_TOKEN, body=raw)
        self.assertEqual(status, 503)
        self.assertIn("retry", response["error"])
        self.assertEqual(self.bridge.db.execute("SELECT state FROM deliveries").fetchone(), ("pending",))
        self.assertIsNone(self.bridge.receipt(BINDING))


if __name__ == "__main__":
    unittest.main()
