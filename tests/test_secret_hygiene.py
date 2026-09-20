from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import verify


class SecretHygieneTests(unittest.TestCase):
    def test_application_sources_pass_the_secret_hygiene_check(self) -> None:
        verify.run_secret_hygiene_check()

    def test_secret_hygiene_detects_credentials_and_private_addresses(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "unsafe.py"
            source.write_text(
                'token = "ghp_aaaaaaaaaaaaaaaaaaaa"\nurl = "http://192.168.1.10/service"\n',  # gitleaks:allow -- synthetic detector input
                encoding="utf-8",
            )
            violations = verify._secret_hygiene_violations([source])

        self.assertTrue(any("common access token" in item for item in violations))
        self.assertTrue(any("private network address" in item for item in violations))

    def test_secret_hygiene_allows_local_loopback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "loopback.py"
            source.write_text('host = "http://127.0.0.1:8765"\n', encoding="utf-8")
            violations = verify._secret_hygiene_violations([source])

        self.assertEqual(violations, [])

    def test_secret_hygiene_discovers_the_full_repository_text_surface(self) -> None:
        discovered = set(verify._secret_hygiene_source_paths())

        for relative in (
            ".github/workflows/security.yml",
            "docs/PRODUCTION_OPERATIONS.md",
            "deploy/systemd/market-sentinel-web.service",
            "tests/test_core_models.py",
            "frontend/package-lock.json",
        ):
            with self.subTest(path=relative):
                self.assertIn(verify.ROOT / relative, discovered)
        self.assertNotIn(verify.ROOT / "frontend/node_modules", discovered)

    def test_secret_hygiene_requires_an_explicit_fixture_allow_marker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "fixture.py"
            source.write_text(
                'token = "ghp_aaaaaaaaaaaaaaaaaaaa"  # secret-scan: allow -- synthetic fixture\n',  # gitleaks:allow
                encoding="utf-8",
            )
            violations = verify._secret_hygiene_violations([source])

        self.assertEqual(violations, [])

    def test_secret_hygiene_skips_virtual_environments_by_marker_not_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.py"
            source.write_text("safe = True\n", encoding="utf-8")
            generated = root / ".arbitrary-build-environment"
            generated.mkdir()
            (generated / "pyvenv.cfg").write_text("home = python\n", encoding="utf-8")
            (generated / "vendored.py").write_text(
                'token = "ghp_aaaaaaaaaaaaaaaaaaaa"\n',  # gitleaks:allow -- synthetic detector input
                encoding="utf-8",
            )
            with patch.object(verify, "ROOT", root):
                discovered = verify._secret_hygiene_source_paths()

        self.assertEqual([source], discovered)

    def test_secret_hygiene_discovers_common_secret_file_names_and_pem_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_file = root / ".env"
            key_file = root / "service.pem"
            env_file.write_text("SAFE_PLACEHOLDER=\n", encoding="utf-8")
            key_file.write_text("public fixture only\n", encoding="utf-8")
            with patch.object(verify, "ROOT", root):
                discovered = set(verify._secret_hygiene_source_paths())

        self.assertEqual({env_file, key_file}, discovered)

    def test_secret_hygiene_fails_closed_for_binary_or_oversized_secret_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            binary_key = root / "secret.key"
            oversized_env = root / ".env"
            binary_key.write_bytes(b"\xff\xfe\x00")
            oversized_env.write_text("SAFE=placeholder\n", encoding="utf-8")
            with patch.object(verify, "SECRET_HYGIENE_MAX_FILE_BYTES", 4):
                violations = verify._secret_hygiene_violations([binary_key, oversized_env])

        self.assertTrue(any("not valid UTF-8" in item for item in violations))
        self.assertTrue(any("exceeds 4 bytes" in item for item in violations))

    def test_secret_hygiene_rejects_binary_credential_containers_by_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            credential = Path(tmp) / "release-signing.pfx"
            credential.write_bytes(b"synthetic fixture")
            violations = verify._secret_hygiene_violations([credential])

        self.assertEqual(1, len(violations))
        self.assertIn("credential container", violations[0])
