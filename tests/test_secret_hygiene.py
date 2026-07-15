from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import verify


class SecretHygieneTests(unittest.TestCase):
    def test_application_sources_pass_the_secret_hygiene_check(self) -> None:
        verify.run_secret_hygiene_check()

    def test_secret_hygiene_detects_credentials_and_private_addresses(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "unsafe.py"
            source.write_text(
                'token = "ghp_aaaaaaaaaaaaaaaaaaaa"\nurl = "http://192.168.1.10/service"\n',
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
