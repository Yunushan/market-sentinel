from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from pathlib import Path
from stat import S_IFREG
from unittest.mock import patch
from urllib.error import HTTPError

from scripts import verify_production_deployment as deployment
from scripts.verify_production_deployment import (
    check_evidence_output_directory,
    check_filesystem_permissions,
    check_loopback,
    check_public_proxy,
    check_systemd,
    _fsync_parent_directory,
    build_evidence,
    main,
    write_evidence,
)


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
    def test_evidence_includes_a_versioned_utc_collection_timestamp(self) -> None:
        evidence = build_evidence(
            [{"name": "loopback_health", "status": "pass"}],
            collected_at=datetime(2026, 7, 19, 12, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(evidence["schema_version"], 1)
        self.assertEqual(evidence["collected_at"], "2026-07-19T12:00:00Z")
        self.assertEqual(evidence["status"], "ok")

    def test_verifier_runs_when_invoked_as_a_script_path(self) -> None:
        script = Path(__file__).resolve().parent.parent / "scripts" / "verify_production_deployment.py"
        result = subprocess.run([sys.executable, str(script), "--help"], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("production deployment evidence", result.stdout)

    def test_systemd_checks_require_active_enabled_and_recent_backup(self) -> None:
        def runner(args: list[str]) -> subprocess.CompletedProcess[str]:
            if args[1] == "show":
                return subprocess.CompletedProcess(args, 0, "success\n0\nThu 1970-01-01 00:16:39 UTC\n", "")
            return subprocess.CompletedProcess(args, 0, "active\n", "")

        checks = check_systemd(runner, clock=lambda: 1000.0)
        self.assertEqual(len(checks), 7)
        self.assertTrue(all(check["status"] == "pass" for check in checks))

    def test_filesystem_check_requires_private_paths_and_root_owned_environment(self) -> None:
        paths = {
            "market-sentinel.env": SimpleNamespace(st_mode=0o100600, st_uid=0),
            "market-sentinel": SimpleNamespace(st_mode=0o040700, st_uid=123),
            "market-sentinel-backups": SimpleNamespace(st_mode=0o040700, st_uid=123),
        }
        checks = check_filesystem_permissions(lambda path: paths[path.name])
        self.assertTrue(all(check["status"] == "pass" for check in checks))

        paths["market-sentinel.env"] = SimpleNamespace(st_mode=0o100640, st_uid=123)
        environment = check_filesystem_permissions(lambda path: paths[path.name])[0]
        self.assertEqual(environment["status"], "fail")

    @unittest.skipUnless(os.name == "posix", "symbolic-link safety is verified on POSIX hosts")
    def test_filesystem_check_rejects_a_symlinked_critical_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "target.env"
            target.write_text("token=not-read", encoding="utf-8")
            linked = root / "market-sentinel.env"
            linked.symlink_to(target)

            with patch.object(deployment, "REQUIRED_PRIVATE_PATHS", ((linked, S_IFREG, False),)):
                check = check_filesystem_permissions()[0]

        self.assertEqual(check["status"], "fail")
        self.assertIn("expected=file", check["detail"])

    def test_evidence_output_requires_a_private_root_owned_parent_directory(self) -> None:
        # Do not use /var here: macOS deliberately exposes it as a compatibility
        # symlink, while this unit test supplies its own directory metadata.
        output = Path.cwd().resolve() / "market-sentinel-evidence-test" / "deployment.json"
        metadata = SimpleNamespace(st_mode=0o040700, st_uid=0)
        self.assertEqual(check_evidence_output_directory(output, lambda path: metadata)["status"], "pass")

        untrusted = SimpleNamespace(st_mode=0o040700, st_uid=123)
        self.assertEqual(check_evidence_output_directory(output, lambda path: untrusted)["status"], "fail")

    @unittest.skipUnless(os.name == "posix", "symbolic-link safety is verified on POSIX hosts")
    def test_evidence_output_rejects_a_symlinked_parent_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trusted = root / "trusted"
            trusted.mkdir()
            linked = root / "service-controlled-link"
            linked.symlink_to(trusted, target_is_directory=True)
            metadata = SimpleNamespace(st_mode=0o040700, st_uid=0)

            check = check_evidence_output_directory(linked / "deployment.json", lambda path: metadata)

            self.assertEqual(check["status"], "fail")
            self.assertIn("symbolic-link", check["detail"])

    @unittest.skipUnless(os.name == "posix", "symbolic-link safety is verified on POSIX hosts")
    def test_evidence_output_rejects_a_symlinked_ancestor_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trusted = root / "trusted"
            trusted.mkdir()
            linked = root / "service-controlled-link"
            linked.symlink_to(trusted, target_is_directory=True)
            metadata = SimpleNamespace(st_mode=0o040700, st_uid=0)

            check = check_evidence_output_directory(linked / "nested" / "deployment.json", lambda path: metadata)

            self.assertEqual(check["status"], "fail")
            self.assertIn(str(linked), check["detail"])

    def test_systemd_check_rejects_a_stale_backup(self) -> None:
        def runner(args: list[str]) -> subprocess.CompletedProcess[str]:
            if args[1] == "show":
                return subprocess.CompletedProcess(args, 0, "success\n0\nThu 1970-01-01 00:00:01 UTC\n", "")
            return subprocess.CompletedProcess(args, 0, "active\n", "")

        checks = check_systemd(runner, clock=lambda: 1_000_000.0)
        backup = checks[-1]
        self.assertEqual(backup["status"], "fail")
        self.assertIn("backup_age_seconds=999999", backup["detail"])

    def test_systemd_check_rejects_an_impossibly_future_backup_timestamp(self) -> None:
        def runner(args: list[str]) -> subprocess.CompletedProcess[str]:
            if args[1] == "show":
                return subprocess.CompletedProcess(args, 0, "success\n0\nThu 1970-01-01 00:23:20 UTC\n", "")
            return subprocess.CompletedProcess(args, 0, "active\n", "")

        checks = check_systemd(runner, clock=lambda: 1000.0)
        backup = checks[-1]
        self.assertEqual(backup["status"], "fail")
        self.assertIn("backup_age_seconds=-400", backup["detail"])

    def test_loopback_checks_expected_version(self) -> None:
        with patch("scripts.verify_production_deployment.check_health", return_value={"api_version": "1.0.10"}):
            self.assertEqual(check_loopback("http://127.0.0.1", "", 1.0, "1.0.10")["status"], "pass")
            with self.assertRaisesRegex(RuntimeError, "expected 1.0.11"):
                check_loopback("http://127.0.0.1", "", 1.0, "1.0.11")

    def test_evidence_output_is_atomic_json_with_private_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "evidence" / "deployment.json"
            output.parent.mkdir()
            write_evidence(output, {"status": "ok", "checks": [{"name": "loopback", "status": "pass"}]})

            self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["status"], "ok")
            if os.name == "posix":
                self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            self.assertFalse(list(output.parent.glob("*.tmp")))

    def test_evidence_parent_directory_is_synced_on_posix(self) -> None:
        path = Path("evidence") / "deployment.json"
        with (
            patch("scripts.verify_production_deployment.os.name", "posix"),
            patch("scripts.verify_production_deployment.os.open", return_value=42) as open_directory,
            patch("scripts.verify_production_deployment.os.fsync") as sync,
            patch("scripts.verify_production_deployment.os.close") as close,
        ):
            _fsync_parent_directory(path)

        open_directory.assert_called_once()
        sync.assert_called_once_with(42)
        close.assert_called_once_with(42)

    @unittest.skipUnless(os.name == "posix", "symlink safety is verified on POSIX hosts")
    def test_evidence_output_ignores_a_predictable_temp_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "evidence" / "deployment.json"
            output.parent.mkdir()
            protected = Path(tmp) / "protected.txt"
            protected.write_text("do not overwrite", encoding="utf-8")
            predictable = output.parent / f".{output.name}.{os.getpid()}.tmp"
            predictable.symlink_to(protected)

            write_evidence(output, {"status": "ok", "checks": []})

            self.assertEqual(protected.read_text(encoding="utf-8"), "do not overwrite")
            self.assertTrue(predictable.is_symlink())
            self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["status"], "ok")

    def test_verifier_fails_when_evidence_output_cannot_be_written(self) -> None:
        stdout = io.StringIO()
        with (
            patch.object(
                sys,
                "argv",
                ["verify_production_deployment.py", "--skip-systemd", "--output", "deployment.json"],
            ),
            patch("scripts.verify_production_deployment.check_loopback", return_value={"name": "loopback_health", "status": "pass"}),
            patch(
                "scripts.verify_production_deployment.check_evidence_output_directory",
                return_value={"name": "filesystem_private_evidence", "status": "pass"},
            ),
            patch("scripts.verify_production_deployment.write_evidence", side_effect=OSError("disk unavailable")),
            contextlib.redirect_stdout(stdout),
        ):
            self.assertEqual(main(), 1)

        evidence = json.loads(stdout.getvalue())
        self.assertEqual(evidence["status"], "failed")
        self.assertEqual(evidence["checks"][-1]["name"], "evidence_output")

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
        unauthenticated = HTTPError("https://analytics.example.com/api/health", 401, "Unauthorized", {}, None)
        with patch(
            "scripts.verify_production_deployment.urlopen",
            side_effect=[unauthenticated, _Response(headers, {"status": "ok", "api_version": "1.0.10"})],
        ):
            self.assertEqual(
                check_public_proxy("https://analytics.example.com", "operator", "secret", 1.0, "1.0.10")["status"],
                "pass",
            )
        with self.assertRaisesRegex(ValueError, "Basic Auth credentials"):
            check_public_proxy("https://analytics.example.com", "", "secret", 1.0)
        with patch("scripts.verify_production_deployment.urlopen", return_value=_Response(headers, {"status": "ok", "api_version": "1.0.10"})):
            with self.assertRaisesRegex(RuntimeError, "unauthenticated public proxy request was accepted"):
                check_public_proxy("https://analytics.example.com", "operator", "secret", 1.0)
        with patch(
            "scripts.verify_production_deployment.urlopen",
            side_effect=HTTPError("https://analytics.example.com/api/health", 403, "Forbidden", {}, None),
        ):
            with self.assertRaisesRegex(RuntimeError, "HTTP 403, expected 401"):
                check_public_proxy("https://analytics.example.com", "operator", "secret", 1.0)
        with patch(
            "scripts.verify_production_deployment.urlopen",
            side_effect=[unauthenticated, _Response(headers, {"status": "ok", "api_version": "1.0.10"})],
        ):
            with self.assertRaisesRegex(RuntimeError, "expected 1.0.11"):
                check_public_proxy("https://analytics.example.com", "operator", "secret", 1.0, "1.0.11")
        with self.assertRaisesRegex(ValueError, "absolute https"):
            check_public_proxy("http://analytics.example.com", "", "", 1.0)

    def test_public_proxy_closes_the_unauthenticated_error_response(self) -> None:
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
        body = io.BytesIO(b'{"error":"unauthorized"}')
        unauthenticated = HTTPError("https://analytics.example.com/api/health", 401, "Unauthorized", {}, body)
        with patch(
            "scripts.verify_production_deployment.urlopen",
            side_effect=[unauthenticated, _Response(headers, {"status": "ok", "api_version": "1.0.10"})],
        ):
            self.assertEqual(
                check_public_proxy("https://analytics.example.com", "operator", "secret", 1.0, "1.0.10")["status"],
                "pass",
            )
        self.assertTrue(body.closed)


if __name__ == "__main__":
    unittest.main()
