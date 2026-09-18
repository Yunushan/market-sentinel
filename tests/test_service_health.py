from __future__ import annotations

import io
import json
import os
import sys
import unittest
from unittest.mock import patch

from scripts.verify_service_health import check_health, main as service_health_main


class _Response:
    def __init__(self, status: int, payload: object) -> None:
        self.status = status
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        return self._body if size < 0 else self._body[:size]


class ServiceHealthTests(unittest.TestCase):
    def test_check_health_accepts_a_versioned_ok_response(self) -> None:
        response = _Response(200, {"status": "ok", "api_version": "1.0.10"})
        with patch("scripts.verify_service_health.open_probe", return_value=response):
            payload = check_health("http://127.0.0.1:8765/api/health", "", 1.0)

        self.assertEqual(payload["api_version"], "1.0.10")

    def test_check_health_rejects_missing_or_unknown_version(self) -> None:
        for version in (None, "", "unknown"):
            with self.subTest(version=version):
                response = _Response(200, {"status": "ok", "api_version": version})
                with patch("scripts.verify_service_health.open_probe", return_value=response):
                    with self.assertRaisesRegex(RuntimeError, "usable api_version"):
                        check_health("http://127.0.0.1:8765/api/health", "", 1.0)

    def test_check_health_rejects_a_versioned_but_degraded_service(self) -> None:
        response = _Response(
            200,
            {
                "status": "ok",
                "api_version": "1.0.11",
                "readiness": {"ready": False, "status": "degraded"},
            },
        )
        with patch("scripts.verify_service_health.open_probe", return_value=response):
            with self.assertRaisesRegex(RuntimeError, "readiness=degraded"):
                check_health("http://127.0.0.1:8765/api/health", "", 1.0)

    def test_check_health_accepts_explicit_ready_state(self) -> None:
        response = _Response(
            200,
            {
                "status": "ok",
                "api_version": "1.0.11",
                "readiness": {"ready": True, "status": "ready"},
            },
        )
        with patch("scripts.verify_service_health.open_probe", return_value=response):
            payload = check_health("http://127.0.0.1:8765/api/health", "", 1.0)

        self.assertTrue(payload["readiness"]["ready"])

    def test_cli_prefers_least_privilege_observability_token_with_admin_fallback(self) -> None:
        payload = {"status": "ok", "api_version": "1.0.12", "readiness": {"ready": True}}
        cases = (
            ("observer-token", "admin-token", "observer-token"),
            ("", "admin-token", "admin-token"),
        )
        for observability_token, admin_token, expected in cases:
            with self.subTest(observability_token=bool(observability_token)), patch.dict(
                os.environ,
                {
                    "MARKET_SENTINEL_OBSERVABILITY_TOKEN": observability_token,
                    "MARKET_SENTINEL_API_TOKEN": admin_token,
                },
                clear=False,
            ), patch.object(sys, "argv", ["verify_service_health.py", "--retries", "1"]), patch(
                "scripts.verify_service_health.check_health",
                return_value=payload,
            ) as check, patch("builtins.print"):
                self.assertEqual(service_health_main(), 0)

            self.assertEqual(check.call_args.args[1], expected)

    def test_observability_only_mode_rejects_admin_or_missing_observer_credentials(self) -> None:
        payload = {"status": "ok", "api_version": "1.0.12", "readiness": {"ready": True}}
        with patch.dict(
            os.environ,
            {
                "MARKET_SENTINEL_OBSERVABILITY_TOKEN": "observer-token",
            },
            clear=True,
        ), patch.object(
            sys,
            "argv",
            ["verify_service_health.py", "--require-observability-token", "--retries", "1"],
        ), patch(
            "scripts.verify_service_health.check_health",
            return_value=payload,
        ) as check, patch("builtins.print"):
            self.assertEqual(service_health_main(), 0)

        self.assertEqual(check.call_args.args[1], "observer-token")

        rejected_environments = (
            {"MARKET_SENTINEL_OBSERVABILITY_TOKEN": "", "MARKET_SENTINEL_API_TOKEN": ""},
            {"MARKET_SENTINEL_OBSERVABILITY_TOKEN": "observer-token", "MARKET_SENTINEL_API_TOKEN": "admin-token"},
            {
                "MARKET_SENTINEL_OBSERVABILITY_TOKEN": "observer-token",
                "MARKET_SENTINEL_API_TOKEN": "",
                "POLYMARKET_PRIVATE_KEY": "venue-secret",
            },
        )
        for environment in rejected_environments:
            with self.subTest(environment=environment), patch.dict(
                os.environ,
                environment,
                clear=True,
            ), patch.object(
                sys,
                "argv",
                ["verify_service_health.py", "--require-observability-token"],
            ), patch("sys.stderr", new_callable=io.StringIO), self.assertRaises(SystemExit) as ctx:
                service_health_main()
            self.assertEqual(ctx.exception.code, 2)

    def test_observability_only_mode_rejects_command_line_token_override(self) -> None:
        with patch.dict(
            os.environ,
            {
                "MARKET_SENTINEL_OBSERVABILITY_TOKEN": "observer-token",
            },
            clear=True,
        ), patch.object(
            sys,
            "argv",
            [
                "verify_service_health.py",
                "--require-observability-token",
                "--token",
                "command-line-secret",
            ],
        ), patch("sys.stderr", new_callable=io.StringIO), self.assertRaises(SystemExit) as ctx:
            service_health_main()

        self.assertEqual(ctx.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
