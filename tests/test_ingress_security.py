from __future__ import annotations

import http.client
import json
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import web_api


class IngressSecurityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        frontend = self.root / "frontend"
        frontend.mkdir()
        (frontend / "index.html").write_text("<html></html>", encoding="utf-8")
        with patch("web_api.DEFAULT_FRONTEND_DIR", frontend):
            self.server = web_api.ReactGuiServer(
                ("127.0.0.1", 0), web_api.ReactGuiHandler,
                config_path=self.root / "config.json", frontend_dir=frontend,
                allowed_origins=["https://console.example.test"], max_http_workers=2,
            )
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.worker.join(timeout=2)
        self.temporary.cleanup()

    def request(self, host: str) -> tuple[int, dict]:
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=2)
        try:
            connection.request("GET", "/api/config", headers={"Host": host})
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_foreign_host_is_rejected_without_origin_or_token(self) -> None:
        status, payload = self.request("attacker.example.test:8765")
        self.assertEqual(status, 403)
        self.assertEqual(payload["error"]["code"], "host_forbidden")
        self.assertEqual(self.request(f"127.0.0.1:{self.server.server_port}")[0], 200)

    def test_configured_public_proxy_authority_is_allowed(self) -> None:
        self.assertEqual(self.request("console.example.test")[0], 200)
        self.assertEqual(self.request("console.example.test:9999")[0], 403)

    def test_unauthenticated_dripped_headers_release_http_slots(self) -> None:
        self.server.api_token = "test-admin"
        with patch("web_api.HTTP_HEADER_DEADLINE_SECONDS", 0.2):
            connection = socket.create_connection(self.server.server_address, timeout=2)
            try:
                connection.sendall(b"GET /api/config HTTP/1.1\r\nX-Drip: ")
                started = time.monotonic()
                for _ in range(12):
                    time.sleep(0.03)
                    try:
                        connection.sendall(b"a")
                    except OSError:
                        break
                deadline = time.monotonic() + 1
                while self.server.http_metrics.snapshot()["requests_in_flight"]:
                    self.assertLess(time.monotonic(), deadline, "Dripped headers retained an HTTP worker")
                    time.sleep(0.01)
                self.assertLess(time.monotonic() - started, 0.65)
                self.assertIn('status="408"', self.server.http_metrics.prometheus_text())
            finally:
                connection.close()
        self.server.api_token = ""
        self.assertEqual(self.request("localhost")[0], 200)

    def test_dripped_json_body_has_absolute_deadline_and_does_not_commit(self) -> None:
        with patch("web_api.HTTP_BODY_DEADLINE_SECONDS", 0.2):
            connection = socket.create_connection(self.server.server_address, timeout=2)
            try:
                connection.sendall(
                    b"PATCH /api/config HTTP/1.1\r\nHost: localhost\r\nContent-Length: 100\r\n\r\n{"
                )
                for _ in range(5):
                    time.sleep(0.03)
                    try:
                        connection.sendall(b" ")
                    except OSError:
                        break
                response = bytearray()
                try:
                    while chunk := connection.recv(4096):
                        response.extend(chunk)
                except (ConnectionResetError, ConnectionAbortedError):
                    pass  # Windows may reset a connection with unread incoming bytes.
                self.assertIn(b"408 Request Timeout", response)
                self.assertIn(b"request_receive_timeout", response)
                self.assertFalse((self.root / "config.json").exists())
            finally:
                connection.close()
        self.assertEqual(self.request("localhost")[0], 200)


if __name__ == "__main__":
    unittest.main()
