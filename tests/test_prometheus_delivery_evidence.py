from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import socket
import ssl
import stat
import tempfile
import threading
import time
import unittest
import urllib.request
from unittest import mock
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from scripts.collect_prometheus_delivery_evidence import (
    CollectorConfig,
    DEFAULT_ONCALL_RECEIVER_NAME,
    DeliveryEvidenceError,
    ONCALL_RECEIPT_TYPE,
    _PinnedHTTPSConnection,
    _resolve_public_https_origin,
    _validate_private_config_metadata,
    canonical_public_https_origin,
    collect_evidence,
    evidence_transcript_sha256,
    loaded_alertmanager_config_contract,
    oncall_receipt_observation,
    oncall_webhook_event_sha256,
    receiver_url,
    utc_now,
    validate_oncall_receipt,
)
from scripts.review_prometheus_delivery_evidence import (
    DeliveryEvidenceReviewError,
    review_payload,
)
from scripts.generate_deployment_evidence import _reviewed_alert_delivery_summary


ROOT = Path(__file__).resolve().parent.parent
REVISION = "a" * 40
DEPLOYMENT_IDENTITY = "b" * 64
NONCE = "c" * 64
RUN_ID = 7319
RUN_ATTEMPT = 2
FINGERPRINT = "0123456789abcdef"
ONCALL_ORIGIN = "https://oncall.market-sentinel.com"
ONCALL_TOKEN = "production-attestation-token-abcdefghijklmnopqrstuvwxyz"
CHANNEL_SHA256 = "d" * 64
ACKNOWLEDGER_SHA256 = "e" * 64


def _public_resolver(host: str, port: int, **_: Any) -> list[tuple[Any, ...]]:
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", port))]


def _reserved_loopback_socket() -> socket.socket:
    candidate = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    candidate.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    candidate.bind(("127.0.0.1", 0))
    candidate.listen()
    return candidate


class _FakeMonitoringState:
    def __init__(
        self,
        rule_directory: Path,
        callback_url: str,
        *,
        source_revision: str = REVISION,
        deployment_identity: str = DEPLOYMENT_IDENTITY,
        run_id: int = RUN_ID,
        run_attempt: int = RUN_ATTEMPT,
        oncall_origin: str = ONCALL_ORIGIN,
        oncall_token: str = ONCALL_TOKEN,
    ) -> None:
        self.rule_directory = rule_directory
        self.callback_url = callback_url
        self.source_revision = source_revision
        self.deployment_identity = deployment_identity
        self.run_id = run_id
        self.run_attempt = run_attempt
        self.oncall_origin = oncall_origin
        self.oncall_token = oncall_token
        self.callback_started = False
        self.callback_delivered = threading.Event()
        self.callback_error: BaseException | None = None
        self.lock = threading.Lock()
        self.active_at: str | None = None

    def oncall_receipt(
        self,
        *,
        url: str,
        bearer_token: str,
        timeout: float,
        pinned_addresses: tuple[str, ...],
    ) -> dict[str, Any]:
        del timeout
        if bearer_token != self.oncall_token:
            raise AssertionError("collector did not use the protected on-call credential")
        if pinned_addresses != ("8.8.8.8",):
            raise AssertionError("collector did not retain the validated public DNS answer")
        binding = url.rsplit("/", 1)[-1]
        if url != f"{self.oncall_origin}/v1/market-sentinel/alert-receipts/{binding}":
            raise AssertionError("collector used an unexpected on-call receipt endpoint")
        if self.active_at is None:
            raise AssertionError("receipt was requested before the synthetic alert fired")
        received = datetime.fromisoformat(self.active_at.replace("Z", "+00:00"))
        time.sleep(0.01)
        dispatched = received + timedelta(milliseconds=1)
        acknowledged = received + timedelta(milliseconds=2)
        observed = datetime.now(timezone.utc)
        if observed <= acknowledged:
            time.sleep((acknowledged - observed).total_seconds() + 0.002)
            observed = datetime.now(timezone.utc)
        labels = self.rule()["labels"]
        webhook_event_sha256 = oncall_webhook_event_sha256(
            labels=labels,
            alert_fingerprint=FINGERPRINT,
            starts_at=self.active_at,
        )
        external_webhook_body = json.dumps(
            {
                "alerts": [{"fingerprint": FINGERPRINT, "labels": labels, "startsAt": self.active_at}],
                "receiver": DEFAULT_ONCALL_RECEIVER_NAME,
                "status": "firing",
                "version": "4",
            },
            separators=(",", ":"),
        )
        body = json.dumps(
            {
                "acknowledged_at": acknowledged.isoformat().replace("+00:00", "Z"),
                "acknowledgement_kind": "human",
                "acknowledger_sha256": ACKNOWLEDGER_SHA256,
                "alert_fingerprint": FINGERPRINT,
                "binding_sha256": binding,
                "delivery_id": f"delivery-{self.run_id}-{self.run_attempt}",
                "deployment_identity_sha256": self.deployment_identity,
                "dispatched_at": dispatched.isoformat().replace("+00:00", "Z"),
                "oncall_channel_sha256": CHANNEL_SHA256,
                "oncall_provider": "pagerduty",
                "receipt_type": ONCALL_RECEIPT_TYPE,
                "received_at": received.isoformat().replace("+00:00", "Z"),
                "run_attempt": self.run_attempt,
                "run_id": self.run_id,
                "schema_version": 1,
                "source_revision": self.source_revision,
                "status": "acknowledged",
                "webhook_body_sha256": hashlib.sha256(external_webhook_body.encode("utf-8")).hexdigest(),
                "webhook_event_sha256": webhook_event_sha256,
                "webhook_receiver": DEFAULT_ONCALL_RECEIVER_NAME,
            },
            separators=(",", ":"),
        )
        return {
            "body": body,
            "body_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
            "content_type": "application/json",
            "observed_at": observed.isoformat().replace("+00:00", "Z"),
            "status": 200,
        }

    @staticmethod
    def loaded_config() -> str:
        return """route:
  receiver: default
  routes:
    - receiver: market-sentinel-oncall-attestation
      matchers:
        - market_sentinel_attestation=\"true\"
      group_by:
        - alertname
        - market_sentinel_binding
      group_wait: 0s
      group_interval: 1m
      repeat_interval: 24h
      continue: true
    - receiver: market-sentinel-attestation
      matchers:
        - market_sentinel_attestation=\"true\"
      group_by:
        - alertname
        - market_sentinel_binding
      group_wait: 0s
      group_interval: 1m
      repeat_interval: 24h
      continue: false
receivers:
  - name: default
  - name: market-sentinel-oncall-attestation
    webhook_configs:
      - url_file: /etc/market-sentinel/alertmanager-oncall-webhook-url
        send_resolved: false
        max_alerts: 1
        http_config:
          follow_redirects: false
          proxy_from_environment: false
          tls_config:
            insecure_skip_verify: false
          authorization:
            type: Bearer
            credentials_file: /etc/market-sentinel/alertmanager-oncall-bearer-token
  - name: market-sentinel-attestation
    webhook_configs:
      - send_resolved: false
        url: http://127.0.0.1:19094/market-sentinel-alertmanager
        max_alerts: 1
"""

    def rule(self) -> dict[str, Any]:
        files = list(self.rule_directory.glob("market-sentinel-attestation-*.yml"))
        if len(files) != 1:
            raise AssertionError("expected one generated attestation rule")
        path = files[0]
        content = path.read_text(encoding="utf-8")
        if self.active_at is None:
            self.active_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        def captured(pattern: str) -> str:
            match = re.search(pattern, content, re.MULTILINE)
            if match is None:
                raise AssertionError(f"generated rule did not match {pattern}")
            return match.group(1)

        binding = captured(r'^\s+market_sentinel_binding: "([0-9a-f]{64})"$')
        labels = {
            "alertname": captured(r"^\s+- alert: ([A-Za-z0-9_]+)$"),
            "market_sentinel_attestation": "true",
            "market_sentinel_binding": binding,
            "market_sentinel_deployment": captured(r'^\s+market_sentinel_deployment: "([0-9a-f]{64})"$'),
            "market_sentinel_revision": captured(r'^\s+market_sentinel_revision: "([0-9a-f]{40})"$'),
            "market_sentinel_run": captured(r'^\s+market_sentinel_run: "([0-9]+\.[0-9]+)"$'),
            "severity": "none",
        }
        return {
            "binding": binding,
            "group": captured(r"^\s+- name: ([a-z0-9-]+)$"),
            "labels": labels,
            "path": str(path),
        }

    def alert(self) -> dict[str, Any]:
        rule = self.rule()
        assert self.active_at is not None
        return {
            "activeAt": self.active_at,
            "annotations": {"attestation_binding": rule["binding"]},
            "labels": rule["labels"],
            "state": "firing",
            "value": "1e+00",
        }

    def alertmanager_alert(self) -> dict[str, Any]:
        rule = self.rule()
        assert self.active_at is not None
        return {
            "annotations": {"attestation_binding": rule["binding"]},
            "endsAt": "0001-01-01T00:00:00Z",
            "fingerprint": FINGERPRINT,
            "labels": rule["labels"],
            "receivers": [
                {"name": DEFAULT_ONCALL_RECEIVER_NAME},
                {"name": "market-sentinel-attestation"},
            ],
            "startsAt": self.active_at,
            "status": {"inhibitedBy": [], "silencedBy": [], "state": "active"},
            "updatedAt": self.active_at,
        }

    def send_callback_once(self) -> None:
        with self.lock:
            if self.callback_started:
                return
            self.callback_started = True

        def send() -> None:
            try:
                alert = self.alertmanager_alert()
                webhook_alert = {
                    "annotations": alert["annotations"],
                    "endsAt": alert["endsAt"],
                    "fingerprint": alert["fingerprint"],
                    "generatorURL": "http://127.0.0.1:9090/graph?g0.expr=vector%281%29",
                    "labels": alert["labels"],
                    "startsAt": alert["startsAt"],
                    "status": "firing",
                }
                body = json.dumps(
                    {
                        "alerts": [webhook_alert],
                        "commonAnnotations": alert["annotations"],
                        "commonLabels": alert["labels"],
                        "externalURL": "http://127.0.0.1:9093",
                        "groupKey": "attestation",
                        "groupLabels": {"alertname": alert["labels"]["alertname"]},
                        "receiver": "market-sentinel-attestation",
                        "status": "firing",
                        "truncatedAlerts": 0,
                        "version": "4",
                    },
                    separators=(",", ":"),
                ).encode("utf-8")
                request = urllib.request.Request(
                    self.callback_url,
                    data=body,
                    method="POST",
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(request, timeout=3) as response:  # noqa: S310 - fixed loopback test URL.
                    if response.status != 204:
                        raise AssertionError(f"receiver returned {response.status}")
                self.callback_delivered.set()
            except BaseException as exc:  # Preserve the callback failure for the test assertion.
                self.callback_error = exc

        threading.Thread(target=send, daemon=True).start()


def _handler(state: _FakeMonitoringState) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            return

        def _json(self, payload: Any) -> None:
            body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API.
            if self.path != "/-/reload":
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API.
            if self.path.startswith("/api/v1/rules?"):
                if not list(state.rule_directory.glob("market-sentinel-attestation-*.yml")):
                    self._json({"data": {"groups": []}, "status": "success"})
                    return
                rule = state.rule()
                self._json(
                    {
                        "data": {
                            "groups": [
                                {
                                    "file": rule["path"],
                                    "name": rule["group"],
                                    "rules": [
                                        {
                                            "health": "ok",
                                            "labels": {
                                                key: value
                                                for key, value in rule["labels"].items()
                                                if key != "alertname"
                                            },
                                            "name": rule["labels"]["alertname"],
                                            "query": "vector(1)",
                                            "state": "firing",
                                            "type": "alerting",
                                        }
                                    ],
                                }
                            ]
                        },
                        "status": "success",
                    }
                )
                return
            if self.path == "/api/v1/alerts":
                self._json({"data": {"alerts": [state.alert()]}, "status": "success"})
                return
            if self.path.startswith("/api/v2/alerts?"):
                self._json([state.alertmanager_alert()])
                state.send_callback_once()
                return
            if self.path == "/api/v2/status":
                if not state.callback_delivered.wait(timeout=2):
                    raise AssertionError("controlled callback was not delivered before config observation")
                self._json(
                    {
                        "cluster": {"peers": [], "status": "disabled"},
                        "config": {"original": state.loaded_config()},
                        "uptime": "2026-09-17T00:00:00Z",
                        "versionInfo": {"version": "0.31.0"},
                    }
                )
                return
            self.send_error(404)

    return Handler


class PrometheusDeliveryEvidenceTests(unittest.TestCase):
    def test_utc_now_is_strictly_monotonic(self) -> None:
        values = [utc_now() for _ in range(100)]

        self.assertEqual(values, sorted(values))
        self.assertEqual(len(values), len(set(values)))

    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory(prefix="market-sentinel-prometheus-evidence-")
        try:
            cls.base = Path(cls.temporary.name)
            cls.rule_directory = cls.base / "rules"
            cls.rule_directory.mkdir()
            cls.output = cls.base / "raw.json"
            cls.oncall_url_file = cls.base / "oncall-url"
            cls.oncall_credentials_file = cls.base / "oncall-token"
            cls.oncall_url_file.write_text(f"{ONCALL_ORIGIN}/v1/market-sentinel/alertmanager", encoding="utf-8")
            cls.oncall_credentials_file.write_text(ONCALL_TOKEN, encoding="utf-8")
            cls.oncall_url_file.chmod(0o600)
            cls.oncall_credentials_file.chmod(0o600)
            cls.receiver_socket = _reserved_loopback_socket()
            cls.receiver_port = int(cls.receiver_socket.getsockname()[1])
            state = _FakeMonitoringState(cls.rule_directory, receiver_url(cls.receiver_port))
            cls.state = state
            cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(state))
            cls.server.daemon_threads = True
            cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
            cls.server_thread.start()
            cls.origin = f"http://127.0.0.1:{cls.server.server_port}"
            cls.payload = collect_evidence(
                CollectorConfig(
                    source_revision=REVISION,
                    deployment_identity_sha256=DEPLOYMENT_IDENTITY,
                    run_id=RUN_ID,
                    run_attempt=RUN_ATTEMPT,
                    nonce=NONCE,
                    rule_directory=cls.rule_directory,
                    prometheus_origin=cls.origin,
                    alertmanager_origin=cls.origin,
                    oncall_receipt_origin=ONCALL_ORIGIN,
                    oncall_receipt_token=ONCALL_TOKEN,
                    oncall_url_file=cls.oncall_url_file,
                    oncall_credentials_file=cls.oncall_credentials_file,
                    expected_alertmanager_gid=123,
                    receiver_name="market-sentinel-attestation",
                    receiver_port=cls.receiver_port,
                    output=cls.output,
                    timeout_seconds=5,
                    poll_interval_seconds=0.01,
                    request_timeout_seconds=1,
                    require_root_owned_oncall_files=False,
                ),
                origin_resolver=_public_resolver,
                receipt_fetcher=state.oncall_receipt,
                receiver_socket=cls.receiver_socket,
            )
            cls.raw_sha256 = hashlib.sha256(cls.output.read_bytes()).hexdigest()
            cls.callback_error = state.callback_error
        except BaseException:
            server = getattr(cls, "server", None)
            server_thread = getattr(cls, "server_thread", None)
            if server is not None and server_thread is not None:
                server.shutdown()
            if server is not None:
                server.server_close()
            if server_thread is not None:
                server_thread.join(timeout=3)
            receiver_socket = getattr(cls, "receiver_socket", None)
            if receiver_socket is not None:
                receiver_socket.close()
            cls.temporary.cleanup()
            raise

    @classmethod
    def tearDownClass(cls) -> None:
        try:
            cls.server.shutdown()
            cls.server.server_close()
            cls.server_thread.join(timeout=3)
        finally:
            cls.receiver_socket.close()
            cls.temporary.cleanup()

    def review(self, payload: dict[str, Any] | None = None, **overrides: Any) -> dict[str, Any]:
        values: dict[str, Any] = {
            "expected_source_revision": REVISION,
            "expected_deployment_identity_sha256": DEPLOYMENT_IDENTITY,
            "expected_run_id": RUN_ID,
            "expected_run_attempt": RUN_ATTEMPT,
            "expected_nonce": NONCE,
            "expected_rule_directory": self.rule_directory,
            "expected_oncall_receipt_origin": ONCALL_ORIGIN,
            "expected_prometheus_origin": self.origin,
            "expected_alertmanager_origin": self.origin,
            "expected_receiver_name": "market-sentinel-attestation",
            "expected_receiver_port": self.receiver_port,
            "now": datetime.now(timezone.utc),
            "origin_resolver": _public_resolver,
        }
        values.update(overrides)
        return review_payload(
            payload or copy.deepcopy(self.payload),
            raw_report_sha256=self.raw_sha256,
            **values,
        )

    def mutated_receipt(self, mutate: Any) -> dict[str, Any]:
        payload = copy.deepcopy(self.payload)
        observation = payload["observations"]["oncall_receipt"]
        body = json.loads(observation["body"])
        mutate(body)
        observation["body"] = json.dumps(body, separators=(",", ":"))
        observation["body_sha256"] = hashlib.sha256(observation["body"].encode("utf-8")).hexdigest()
        transcript = dict(payload)
        transcript.pop("transcript_sha256")
        payload["transcript_sha256"] = evidence_transcript_sha256(transcript)
        return payload

    def test_end_to_end_collector_captures_and_reviewer_proves_every_hop(self) -> None:
        self.assertIsNone(self.callback_error)
        reviewed = self.review()
        self.assertEqual(reviewed["status"], "ok")
        self.assertEqual(reviewed["source_revision"], REVISION)
        self.assertEqual(reviewed["nonce"], NONCE)
        self.assertEqual(reviewed["alert_fingerprint"], FINGERPRINT)
        self.assertEqual(reviewed["oncall_provider"], "pagerduty")
        self.assertEqual(reviewed["oncall_channel_sha256"], CHANNEL_SHA256)
        self.assertEqual(reviewed["acknowledger_sha256"], ACKNOWLEDGER_SHA256)
        self.assertRegex(reviewed["receipt_sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(reviewed["delivery_id_sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(reviewed["alertmanager_config_sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(reviewed["alertmanager_status_sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(reviewed["webhook_body_sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(reviewed["webhook_event_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(
            reviewed["receipt_origin_sha256"],
            hashlib.sha256(ONCALL_ORIGIN.encode("utf-8")).hexdigest(),
        )
        timeline = reviewed["timeline"]
        self.assertLess(timeline["receiver_observed_at"], timeline["alertmanager_config_observed_at"])
        self.assertLess(timeline["oncall_received_at"], timeline["prometheus_alert_observed_at"])
        generated = _reviewed_alert_delivery_summary(
            reviewed,
            expected_revision=REVISION,
            expected_identity_sha256=DEPLOYMENT_IDENTITY,
            expected_run_id=RUN_ID,
            expected_run_attempt=RUN_ATTEMPT,
            expected_nonce=NONCE,
            expected_receipt_origin_sha256=hashlib.sha256(ONCALL_ORIGIN.encode("utf-8")).hexdigest(),
            expected_raw_sha256=self.raw_sha256,
            expected_review_sha256=hashlib.sha256(
                json.dumps(reviewed, sort_keys=True).encode("utf-8")
            ).hexdigest(),
        )
        self.assertEqual(generated["status"], "ok")
        self.assertEqual(list(self.rule_directory.iterdir()), [])

    def test_public_receipt_origin_rejects_local_private_and_placeholder_targets(self) -> None:
        for origin in (
            "https://127.0.0.1",
            "https://10.0.0.5",
            "https://localhost",
            "https://alerts.example.com",
            "https://alerts.internal",
            "http://oncall.market-sentinel.com",
            "https://operator:" + ("x" * 32) + "@oncall.market-sentinel.com",
            "https://oncall.market-sentinel.com/path",
            "https://oncall.market-sentinel.com?token=secret",
        ):
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                canonical_public_https_origin(origin, "receipt origin", resolver=_public_resolver)

        def private_resolver(host: str, port: int, **_: Any) -> list[tuple[Any, ...]]:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.20", port))]

        with self.assertRaisesRegex(ValueError, "public global"):
            canonical_public_https_origin(
                "https://oncall.market-sentinel.com",
                "receipt origin",
                resolver=private_resolver,
            )

    def test_receipt_transport_pins_validated_dns_and_ignores_ambient_proxy(self) -> None:
        resolver_calls = 0

        def rebinding_resolver(host: str, port: int, **_: Any) -> list[tuple[Any, ...]]:
            nonlocal resolver_calls
            resolver_calls += 1
            address = "8.8.8.8" if resolver_calls == 1 else "127.0.0.1"
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port))]

        target = _resolve_public_https_origin(
            ONCALL_ORIGIN,
            "receipt origin",
            resolver=rebinding_resolver,
        )
        calls: list[dict[str, Any]] = []

        class FakeResponse:
            status = 200
            headers = {"Content-Length": "2", "Content-Type": "application/json"}

            @staticmethod
            def read(_: int) -> bytes:
                return b"{}"

        class FakeConnection:
            def __init__(
                self,
                hostname: str,
                *,
                port: int,
                pinned_address: str,
                timeout: float,
            ) -> None:
                calls.append(
                    {
                        "hostname": hostname,
                        "pinned_address": pinned_address,
                        "port": port,
                        "timeout": timeout,
                    }
                )

            def request(self, method: str, target_path: str, *, headers: dict[str, str]) -> None:
                calls[-1].update({"headers": headers, "method": method, "target_path": target_path})

            @staticmethod
            def getresponse() -> FakeResponse:
                return FakeResponse()

            @staticmethod
            def close() -> None:
                return None

        receipt_url = f"{target.origin}/v1/market-sentinel/alert-receipts/{'f' * 64}"
        with (
            mock.patch.dict(os.environ, {"HTTPS_PROXY": "http://127.0.0.1:7777"}),
            mock.patch("urllib.request.getproxies", side_effect=AssertionError("proxy lookup")),
        ):
            observation = oncall_receipt_observation(
                url=receipt_url,
                bearer_token=ONCALL_TOKEN,
                timeout=1.0,
                pinned_addresses=target.pinned_addresses,
                connection_factory=FakeConnection,
            )

        self.assertEqual(resolver_calls, 1)
        self.assertEqual(observation["status"], 200)
        self.assertEqual(calls[0]["hostname"], "oncall.market-sentinel.com")
        self.assertEqual(calls[0]["pinned_address"], "8.8.8.8")
        self.assertEqual(
            calls[0]["target_path"],
            f"/v1/market-sentinel/alert-receipts/{'f' * 64}",
        )
        self.assertEqual(calls[0]["headers"]["Authorization"], f"Bearer {ONCALL_TOKEN}")

    def test_pinned_tls_connection_uses_numeric_socket_and_original_hostname_for_sni(self) -> None:
        connection = _PinnedHTTPSConnection(
            "oncall.market-sentinel.com",
            port=443,
            pinned_address="8.8.8.8",
            timeout=1.0,
        )
        self.assertTrue(connection._context.check_hostname)
        self.assertEqual(connection._context.verify_mode, ssl.CERT_REQUIRED)
        raw_socket = mock.Mock()
        tls_socket = mock.Mock()
        context = mock.Mock()
        context.wrap_socket.return_value = tls_socket
        connection._context = context
        with mock.patch(
            "scripts.collect_prometheus_delivery_evidence.socket.socket",
            return_value=raw_socket,
        ) as socket_factory:
            connection.connect()

        socket_factory.assert_called_once_with(socket.AF_INET, socket.SOCK_STREAM)
        raw_socket.connect.assert_called_once_with(("8.8.8.8", 443))
        context.wrap_socket.assert_called_once_with(
            raw_socket,
            server_hostname="oncall.market-sentinel.com",
        )
        self.assertIs(connection.sock, tls_socket)

    def test_receipt_transport_rejects_nonpublic_pins_before_sending_bearer(self) -> None:
        factory = mock.Mock(side_effect=AssertionError("connection attempted"))
        with self.assertRaisesRegex(DeliveryEvidenceError, "public pinned addresses"):
            oncall_receipt_observation(
                url=f"{ONCALL_ORIGIN}/v1/market-sentinel/alert-receipts/{'f' * 64}",
                bearer_token=ONCALL_TOKEN,
                timeout=1.0,
                pinned_addresses=("127.0.0.1",),
                connection_factory=factory,
            )
        factory.assert_not_called()

    def test_review_rejects_missing_or_mismatched_receipt_binding_and_fingerprint(self) -> None:
        missing = self.mutated_receipt(lambda body: body.pop("binding_sha256"))
        with self.assertRaisesRegex(DeliveryEvidenceReviewError, "fields are not exact"):
            self.review(missing)
        wrong_binding = self.mutated_receipt(lambda body: body.__setitem__("binding_sha256", "f" * 64))
        with self.assertRaisesRegex(DeliveryEvidenceReviewError, "exact deployment challenge"):
            self.review(wrong_binding)
        wrong_fingerprint = self.mutated_receipt(
            lambda body: body.__setitem__("alert_fingerprint", "fedcba9876543210")
        )
        with self.assertRaisesRegex(DeliveryEvidenceReviewError, "fingerprint does not match"):
            self.review(wrong_fingerprint)

    def test_review_requires_later_human_acknowledgement_in_challenge_window(self) -> None:
        no_human = self.mutated_receipt(lambda body: body.__setitem__("acknowledgement_kind", "automatic"))
        with self.assertRaisesRegex(DeliveryEvidenceReviewError, "human acknowledgement"):
            self.review(no_human)
        bad_order = self.mutated_receipt(
            lambda body: body.__setitem__("acknowledged_at", body["dispatched_at"])
        )
        with self.assertRaisesRegex(DeliveryEvidenceReviewError, "timestamps are out of order"):
            self.review(bad_order)
        stale_receipt = self.mutated_receipt(
            lambda body: body.__setitem__("received_at", "2000-01-01T00:00:00Z")
        )
        with self.assertRaisesRegex(DeliveryEvidenceReviewError, "activeAt beyond allowed clock skew"):
            self.review(stale_receipt)

    def test_receipt_active_at_clock_skew_boundary_is_enforced_by_collector_and_reviewer(self) -> None:
        alert = json.loads(self.payload["observations"]["alertmanager_alerts"]["body"])[0]
        active_at = datetime.fromisoformat(alert["startsAt"].replace("Z", "+00:00"))
        observation = self.payload["observations"]["oncall_receipt"]
        observed_at = datetime.fromisoformat(observation["observed_at"].replace("Z", "+00:00"))
        started_at = datetime.fromisoformat(self.payload["started_at"].replace("Z", "+00:00"))

        for offset, accepted in ((60.0, True), (60.001, False)):
            with self.subTest(offset=offset):
                received = (active_at - timedelta(seconds=offset)).isoformat().replace("+00:00", "Z")
                payload = self.mutated_receipt(
                    lambda body, value=received: body.__setitem__("received_at", value)
                )
                receipt = json.loads(payload["observations"]["oncall_receipt"]["body"])
                if accepted:
                    validate_oncall_receipt(
                        receipt,
                        source_revision=REVISION,
                        deployment_identity_sha256=DEPLOYMENT_IDENTITY,
                        run_id=RUN_ID,
                        run_attempt=RUN_ATTEMPT,
                        binding=self.payload["challenge"]["binding_sha256"],
                        alert_fingerprint=FINGERPRINT,
                        labels=alert["labels"],
                        alert_starts_at=alert["startsAt"],
                        started_at=started_at,
                        observed_at=observed_at,
                    )
                    self.assertEqual(self.review(payload)["status"], "ok")
                else:
                    with self.assertRaisesRegex(DeliveryEvidenceError, "activeAt beyond allowed clock skew"):
                        validate_oncall_receipt(
                            receipt,
                            source_revision=REVISION,
                            deployment_identity_sha256=DEPLOYMENT_IDENTITY,
                            run_id=RUN_ID,
                            run_attempt=RUN_ATTEMPT,
                            binding=self.payload["challenge"]["binding_sha256"],
                            alert_fingerprint=FINGERPRINT,
                            labels=alert["labels"],
                            alert_starts_at=alert["startsAt"],
                            started_at=started_at,
                            observed_at=observed_at,
                        )
                    with self.assertRaisesRegex(
                        DeliveryEvidenceReviewError,
                        "activeAt beyond allowed clock skew",
                    ):
                        self.review(payload)

    def test_review_requires_loaded_external_route_and_exact_webhook_binding(self) -> None:
        wrong_event = self.mutated_receipt(
            lambda body: body.__setitem__("webhook_event_sha256", "0" * 64)
        )
        with self.assertRaisesRegex(DeliveryEvidenceReviewError, "exact external Alertmanager webhook"):
            self.review(wrong_event)

        payload = copy.deepcopy(self.payload)
        config = payload["observations"]["alertmanager_config"]
        config["contract"]["external_continue"] = False
        transcript = dict(payload)
        transcript.pop("transcript_sha256")
        payload["transcript_sha256"] = evidence_transcript_sha256(transcript)
        with self.assertRaisesRegex(DeliveryEvidenceReviewError, "loaded config contract"):
            self.review(payload)

        extra_field = copy.deepcopy(self.payload)
        config = extra_field["observations"]["alertmanager_config"]
        config["external_receiver_excerpt"] = config["external_receiver_excerpt"].replace(
            "        authorization:\n",
            "        unexpected_option: false\n        authorization:\n",
        )
        transcript = dict(extra_field)
        transcript.pop("transcript_sha256")
        extra_field["transcript_sha256"] = evidence_transcript_sha256(transcript)
        with self.assertRaisesRegex(DeliveryEvidenceReviewError, "unknown, missing, reordered"):
            self.review(extra_field)

    def test_loaded_config_rejects_unsafe_or_unknown_external_receiver_fields(self) -> None:
        mutations = (
            self.state.loaded_config().replace(
                "          proxy_from_environment: false",
                "          proxy_from_environment: true",
            ),
            self.state.loaded_config().replace(
                "          insecure_skip_verify: false",
                "          insecure_skip_verify: true",
            ),
            self.state.loaded_config().replace(
                "          authorization:",
                "          unknown_option: false\n          authorization:",
            ),
            self.state.loaded_config().replace(
                "      group_wait: 0s",
                "       group_wait: 0s",
                1,
            ),
        )
        for index, loaded_config in enumerate(mutations):
            with self.subTest(case=index), self.assertRaisesRegex(
                DeliveryEvidenceError,
                "unknown, missing, reordered, or misindented",
            ):
                loaded_alertmanager_config_contract(
                    {"config": {"original": loaded_config}},
                    bearer_token=ONCALL_TOKEN,
                    oncall_origin=ONCALL_ORIGIN,
                )

    def test_private_mode_0640_requires_expected_alertmanager_gid(self) -> None:
        mode_0640 = SimpleNamespace(st_mode=stat.S_IFREG | 0o640, st_uid=0, st_gid=123)
        mode_0600 = SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_uid=0, st_gid=999)
        with mock.patch("scripts.collect_prometheus_delivery_evidence.os.name", "posix"):
            _validate_private_config_metadata(
                mode_0640,
                "config",
                require_root_owned=True,
                expected_group_id=123,
            )
            _validate_private_config_metadata(
                mode_0600,
                "config",
                require_root_owned=True,
                expected_group_id=123,
            )
            with self.assertRaisesRegex(ValueError, "expected Alertmanager GID"):
                _validate_private_config_metadata(
                    SimpleNamespace(st_mode=stat.S_IFREG | 0o640, st_uid=0, st_gid=124),
                    "config",
                    require_root_owned=True,
                    expected_group_id=123,
                )

    def test_evidence_is_secret_free_and_rejects_receipt_secret_leakage(self) -> None:
        raw = self.output.read_text(encoding="utf-8")
        self.assertNotIn(ONCALL_TOKEN, raw)
        self.assertNotIn(ONCALL_ORIGIN, raw)
        leaked = self.mutated_receipt(lambda body: body.__setitem__("authorization", ONCALL_TOKEN))
        with self.assertRaisesRegex(DeliveryEvidenceReviewError, "fields are not exact"):
            self.review(leaked)
        with self.assertRaisesRegex(DeliveryEvidenceError, "exposed an on-call secret"):
            loaded_alertmanager_config_contract(
                {"config": {"original": f"{self.state.loaded_config()}\n# {ONCALL_TOKEN}\n"}},
                bearer_token=ONCALL_TOKEN,
                oncall_origin=ONCALL_ORIGIN,
            )

    def test_review_rejects_loopback_only_evidence(self) -> None:
        payload = copy.deepcopy(self.payload)
        payload["observations"].pop("oncall_receipt")
        payload["observations"].pop("alertmanager_config")
        payload["endpoints"].pop("oncall_receipt_origin_sha256")
        transcript = dict(payload)
        transcript.pop("transcript_sha256")
        payload["transcript_sha256"] = evidence_transcript_sha256(transcript)
        with self.assertRaisesRegex(DeliveryEvidenceReviewError, "fields are not exact"):
            self.review(payload)

        local_only = copy.deepcopy(self.payload)
        observation = local_only["observations"]["alertmanager_alerts"]
        body = json.loads(observation["body"])
        body[0]["receivers"] = [{"name": "market-sentinel-attestation"}]
        observation["body"] = json.dumps(body, separators=(",", ":"))
        observation["body_sha256"] = hashlib.sha256(observation["body"].encode("utf-8")).hexdigest()
        transcript = dict(local_only)
        transcript.pop("transcript_sha256")
        local_only["transcript_sha256"] = evidence_transcript_sha256(transcript)
        with self.assertRaisesRegex(DeliveryEvidenceReviewError, "both the external on-call"):
            self.review(local_only)

    def test_review_rejects_replayed_nonce_or_run_identity(self) -> None:
        with self.assertRaisesRegex(DeliveryEvidenceReviewError, "expected nonce"):
            self.review(expected_nonce="d" * 64)
        with self.assertRaisesRegex(DeliveryEvidenceReviewError, "expected revision and deployment run"):
            self.review(expected_run_attempt=RUN_ATTEMPT + 1)

    def test_review_rejects_semantic_api_tampering_even_with_recomputed_digests(self) -> None:
        payload = copy.deepcopy(self.payload)
        observation = payload["observations"]["prometheus_rules"]
        body = json.loads(observation["body"])
        body["data"]["groups"][0]["rules"][0]["health"] = "err"
        observation["body"] = json.dumps(body, separators=(",", ":"))
        observation["body_sha256"] = hashlib.sha256(observation["body"].encode("utf-8")).hexdigest()
        transcript = dict(payload)
        transcript.pop("transcript_sha256")
        payload["transcript_sha256"] = evidence_transcript_sha256(transcript)
        with self.assertRaisesRegex(DeliveryEvidenceReviewError, "exactly one"):
            self.review(payload)

    def test_review_rejects_cross_hop_fingerprint_substitution(self) -> None:
        payload = copy.deepcopy(self.payload)
        observation = payload["observations"]["receiver_webhook"]
        body = json.loads(observation["body"])
        body["alerts"][0]["fingerprint"] = "fedcba9876543210"
        observation["body"] = json.dumps(body, separators=(",", ":"))
        observation["body_sha256"] = hashlib.sha256(observation["body"].encode("utf-8")).hexdigest()
        transcript = dict(payload)
        transcript.pop("transcript_sha256")
        payload["transcript_sha256"] = evidence_transcript_sha256(transcript)
        with self.assertRaisesRegex(DeliveryEvidenceReviewError, "fingerprint does not match"):
            self.review(payload)

    def test_review_requires_post_cleanup_api_proof_that_rule_is_absent(self) -> None:
        payload = copy.deepcopy(self.payload)
        loaded = payload["observations"]["prometheus_rules"]["body"]
        observation = payload["observations"]["cleanup_rules"]
        observation["body"] = loaded
        observation["body_sha256"] = hashlib.sha256(loaded.encode("utf-8")).hexdigest()
        transcript = dict(payload)
        transcript.pop("transcript_sha256")
        payload["transcript_sha256"] = evidence_transcript_sha256(transcript)
        with self.assertRaisesRegex(DeliveryEvidenceReviewError, "still reports the synthetic rule"):
            self.review(payload)

    def test_review_rejects_stale_evidence(self) -> None:
        future = datetime.now(timezone.utc) + timedelta(hours=1)
        with self.assertRaisesRegex(DeliveryEvidenceReviewError, "stale"):
            self.review(now=future, max_age_seconds=60)

    def test_required_host_fragments_are_loopback_and_attestation_scoped(self) -> None:
        prometheus = (
            ROOT / "deploy" / "prometheus" / "market-sentinel-attestation-prometheus.yml.example"
        ).read_text(encoding="utf-8")
        alertmanager = (
            ROOT / "deploy" / "prometheus" / "market-sentinel-attestation-alertmanager.yml.example"
        ).read_text(encoding="utf-8")
        self.assertIn("/var/lib/prometheus/market-sentinel-attestation/*.yml", prometheus)
        self.assertIn("127.0.0.1:9093", prometheus)
        self.assertIn('market_sentinel_attestation="true"', alertmanager)
        self.assertIn("http://127.0.0.1:19094/market-sentinel-alertmanager", alertmanager)
        self.assertIn("market-sentinel-oncall-attestation", alertmanager)
        self.assertIn("url_file: /etc/market-sentinel/alertmanager-oncall-webhook-url", alertmanager)
        self.assertIn("credentials_file: /etc/market-sentinel/alertmanager-oncall-bearer-token", alertmanager)
        self.assertIn("follow_redirects: false", alertmanager)
        self.assertIn("proxy_from_environment: false", alertmanager)
        self.assertIn("insecure_skip_verify: false", alertmanager)
        self.assertIn("continue: true", alertmanager)
        self.assertIn("send_resolved: false", alertmanager)


if __name__ == "__main__":
    unittest.main()
