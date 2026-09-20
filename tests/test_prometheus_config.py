from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCRAPE_PATH = ROOT / "deploy" / "prometheus" / "market-sentinel-scrape.yml"
ALERTS_PATH = ROOT / "deploy" / "prometheus" / "market-sentinel-alerts.yml"


class PrometheusConfigTests(unittest.TestCase):
    def test_scrape_is_loopback_authenticated_and_bounded(self) -> None:
        text = SCRAPE_PATH.read_text(encoding="utf-8")
        for fragment in (
            "job_name: market-sentinel",
            "metrics_path: /metrics",
            "scrape_interval: 15s",
            "scrape_timeout: 5s",
            "type: Bearer",
            "credentials_file: /etc/prometheus/market-sentinel-observability-token",
            "127.0.0.1:8765",
            "market-sentinel-alerts.yml",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, text)
        self.assertNotIn("localhost", text)
        self.assertNotRegex(text, r"(?i)(?:password|token):\s*[^\s$]")

    def test_alerts_cover_availability_errors_capacity_and_restarts(self) -> None:
        text = ALERTS_PATH.read_text(encoding="utf-8")
        alerts = set(re.findall(r"^\s+- alert: ([A-Za-z0-9]+)$", text, re.MULTILINE))
        self.assertEqual(
            {
                "MarketSentinelDown",
                "MarketSentinelHighServerErrorRatio",
                "MarketSentinelOverloaded",
                "MarketSentinelMutationSaturation",
                "MarketSentinelOversizeResponse",
                "MarketSentinelRestartLoop",
            },
            alerts,
        )
        for severity in ("severity: warning", "severity: critical"):
            self.assertIn(severity, text)
        self.assertEqual(len(alerts), len(re.findall(r"^\s+for: ", text, re.MULTILINE)))

    def test_alert_expressions_reference_exported_metrics(self) -> None:
        alerts = ALERTS_PATH.read_text(encoding="utf-8")
        web_api = (ROOT / "web_api.py").read_text(encoding="utf-8")
        metric_names = set(re.findall(r"market_sentinel_[a-z0-9_]+", alerts))
        self.assertGreaterEqual(len(metric_names), 6)
        for metric_name in metric_names:
            with self.subTest(metric=metric_name):
                self.assertIn(metric_name, web_api)


if __name__ == "__main__":
    unittest.main()
