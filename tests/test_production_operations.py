from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


class ProductionOperationsTests(unittest.TestCase):
    def test_systemd_unit_uses_loopback_and_service_hardening(self) -> None:
        unit = (ROOT / "deploy" / "systemd" / "market-sentinel-web.service").read_text(encoding="utf-8")
        for fragment in (
            "--host 127.0.0.1",
            "User=market-sentinel",
            "UMask=0077",
            "NoNewPrivileges=true",
            "ProtectSystem=strict",
            "ProtectHome=true",
            "ReadWritePaths=/var/lib/market-sentinel",
            "verify_service_health.py",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, unit)

    def test_proxy_and_governance_artifacts_require_authenticated_tls_access(self) -> None:
        proxy = (ROOT / "deploy" / "caddy" / "Caddyfile.example").read_text(encoding="utf-8")
        security = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
        repository_settings = (ROOT / "docs" / "REPOSITORY_SETTINGS.md").read_text(encoding="utf-8")
        self.assertIn("basic_auth", proxy)
        self.assertIn("X-Market-Sentinel-Token", proxy)
        self.assertIn("127.0.0.1:8765", proxy)
        self.assertIn("Report a vulnerability", security)
        self.assertIn("Required review from Code Owners", repository_settings)
        self.assertIn("secret scanning", repository_settings)


if __name__ == "__main__":
    unittest.main()
