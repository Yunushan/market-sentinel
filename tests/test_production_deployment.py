from __future__ import annotations

import json
import subprocess
import unittest
from unittest.mock import patch

from scripts.verify_production_deployment import check_loopback, check_public_proxy, check_systemd


class _Response:
    status = 200

    def __init__(self, headers: dict[str, str], payload: dict[str, str]) -> None:
        self.headers = headers
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


class ProductionDeploymentTests(unittest.TestCase):
    def test_systemd_checks_require_active_and_enabled_units(self) -> None:
        checks = check_systemd(lambda args: subprocess.CompletedProcess(args, 0, "active\n", ""))
        self.assertEqual(len(checks), 6)
        self.assertTrue(all(check["status"] == "pass" for check in checks))

    def test_loopback_checks_expected_version(self) -> None:
        with patch("scripts.verify_production_deployment.check_health", return_value={"api_version": "1.0.10"}):
            self.assertEqual(check_loopback("http://127.0.0.1", "", 1.0, "1.0.10")["status"], "pass")
            with self.assertRaisesRegex(RuntimeError, "expected 1.0.11"):
                check_loopback("http://127.0.0.1", "", 1.0, "1.0.11")

    def test_public_proxy_requires_https_security_headers_and_no_store(self) -> None:
        headers = {
            "Strict-Transport-Security": "max-age=31536000",
            "Content-Security-Policy": "default-src 'self'",
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "Referrer-Policy": "no-referrer",
            "Permissions-Policy": "camera=()",
            "Cross-Origin-Opener-Policy": "same-origin",
            "Cross-Origin-Resource-Policy": "same-origin",
            "Cache-Control": "no-store",
        }
        with patch("scripts.verify_production_deployment.urlopen", return_value=_Response(headers, {"status": "ok", "api_version": "1.0.10"})):
            self.assertEqual(
                check_public_proxy("https://analytics.example.com", "operator", "secret", 1.0, "1.0.10")["status"],
                "pass",
            )
            with self.assertRaisesRegex(RuntimeError, "expected 1.0.11"):
                check_public_proxy("https://analytics.example.com", "operator", "secret", 1.0, "1.0.11")
        with self.assertRaisesRegex(ValueError, "absolute https"):
            check_public_proxy("http://analytics.example.com", "", "", 1.0)


if __name__ == "__main__":
    unittest.main()
