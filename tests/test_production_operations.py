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
            "StateDirectory=market-sentinel",
            "StateDirectoryMode=0700",
            "UMask=0077",
            "NoNewPrivileges=true",
            "PrivateDevices=true",
            "ProtectClock=true",
            "ProtectHostname=true",
            "ProtectSystem=strict",
            "ProtectHome=true",
            "RestrictRealtime=true",
            "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6",
            "ReadWritePaths=/var/lib/market-sentinel",
            "ExecStartPre=/opt/market-sentinel/.venv/bin/python -m market_sentinel_cli doctor --strict --compact",
            "--config /var/lib/market-sentinel/config.json",
            "--frontend-dir /opt/market-sentinel/frontend/dist",
            "verify_service_health.py",
            "StartLimitIntervalSec=5min",
            "StartLimitBurst=5",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, unit)

    def test_proxy_and_governance_artifacts_require_authenticated_tls_access(self) -> None:
        proxy = (ROOT / "deploy" / "caddy" / "Caddyfile.example").read_text(encoding="utf-8")
        security = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
        repository_settings = (ROOT / "docs" / "REPOSITORY_SETTINGS.md").read_text(encoding="utf-8")
        codeowners = (ROOT / ".github" / "CODEOWNERS").read_text(encoding="utf-8")
        service_environment = (ROOT / "deploy" / "systemd" / "market-sentinel.env.example").read_text(encoding="utf-8")
        self.assertIn("basic_auth", proxy)
        self.assertIn("X-Market-Sentinel-Token", proxy)
        self.assertIn("127.0.0.1:8765", proxy)
        self.assertIn("Content-Security-Policy", proxy)
        self.assertIn("default-src 'self'", proxy)
        self.assertIn("object-src 'none'", proxy)
        self.assertIn("frame-ancestors 'none'", proxy)
        self.assertIn("Permissions-Policy", proxy)
        self.assertIn("Cross-Origin-Opener-Policy", proxy)
        self.assertIn("Cross-Origin-Resource-Policy", proxy)
        self.assertIn("Report a vulnerability", security)
        self.assertIn("Team production policy", repository_settings)
        self.assertIn("secret scanning", repository_settings)
        self.assertIn("Python package build", repository_settings)
        self.assertIn("Frontend dependency audit", repository_settings)
        self.assertIn("Release` workflow", repository_settings)
        self.assertIn("MARKET_SENTINEL_ALLOWED_ORIGINS", service_environment)
        self.assertIn("* @Yunushan", codeowners)

    def test_systemd_health_timer_performs_periodic_loopback_checks(self) -> None:
        health_unit = (ROOT / "deploy" / "systemd" / "market-sentinel-health.service").read_text(encoding="utf-8")
        timer = (ROOT / "deploy" / "systemd" / "market-sentinel-health.timer").read_text(encoding="utf-8")
        for fragment in (
            "User=market-sentinel",
            "verify_service_health.py --retries 2 --retry-delay 5",
            "NoNewPrivileges=true",
            "PrivateDevices=true",
            "ProtectSystem=strict",
            "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6",
            "StartLimitIntervalSec=5min",
            "StartLimitBurst=5",
            "TimeoutStartSec=30",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, health_unit)
        self.assertIn("OnUnitActiveSec=1min", timer)
        self.assertIn("Persistent=true", timer)
        self.assertIn("Unit=market-sentinel-health.service", timer)

    def test_systemd_backup_timer_keeps_offline_backups_hardened(self) -> None:
        backup_unit = (ROOT / "deploy" / "systemd" / "market-sentinel-backup.service").read_text(encoding="utf-8")
        timer = (ROOT / "deploy" / "systemd" / "market-sentinel-backup.timer").read_text(encoding="utf-8")
        for fragment in (
            "User=market-sentinel",
            "StateDirectory=market-sentinel-backups",
            "backup_state.py --source /var/lib/market-sentinel --destination /var/lib/market-sentinel-backups --retain 14",
            "PrivateNetwork=true",
            "NoNewPrivileges=true",
            "ProtectSystem=strict",
            "ReadOnlyPaths=/var/lib/market-sentinel",
            "ReadWritePaths=/var/lib/market-sentinel-backups",
            "RestrictAddressFamilies=AF_UNIX",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, backup_unit)
        self.assertIn("OnCalendar=daily", timer)
        self.assertIn("Persistent=true", timer)
        self.assertIn("Unit=market-sentinel-backup.service", timer)

    def test_operations_document_private_root_owned_deployment_evidence(self) -> None:
        operations = (ROOT / "docs" / "PRODUCTION_OPERATIONS.md").read_text(encoding="utf-8")
        self.assertIn("install -d -o root -g root -m 0700 /var/lib/market-sentinel-deployment-evidence", operations)
        self.assertIn("/var/lib/market-sentinel-deployment-evidence/deployment-evidence-<RELEASE_VERSION>.json", operations)
        self.assertIn("private root-owned parent directory", operations)

    def test_gitignore_excludes_generated_analytics_artifacts(self) -> None:
        gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        for fragment in (
            "data/polymarket_analytics_cache.json",
            "data/*.sqlite*",
            "data/*.jsonl",
            "data/*.csv",
            "data/*.log",
            "data/*.pid",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, gitignore)


if __name__ == "__main__":
    unittest.main()
