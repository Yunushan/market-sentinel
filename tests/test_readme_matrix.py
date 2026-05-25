from __future__ import annotations

import unittest
from pathlib import Path

from market_adapters import MARKET_IDS


ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"
REQUIRED_HEADERS = (
    "Market",
    "Adapter",
    "Alerts",
    "Read-only data",
    "Paper trading",
    "Live trading",
    "Copy trading",
    "API required",
    "Credentials required",
    "Region/KYC limitation",
)


class ReadmeCapabilityMatrixTests(unittest.TestCase):
    def test_readme_capability_matrix_has_required_headers(self) -> None:
        text = README.read_text(encoding="utf-8")

        self.assertIn("## Market Capability Matrix", text)
        for header in REQUIRED_HEADERS:
            self.assertIn(header, text)

    def test_readme_capability_matrix_covers_all_catalog_markets(self) -> None:
        text = README.read_text(encoding="utf-8")

        for market_id in MARKET_IDS:
            self.assertIn(f"`{market_id}`", text)

    def test_readme_marks_non_polymarket_markets_as_stubs(self) -> None:
        text = README.read_text(encoding="utf-8")

        for line in text.splitlines():
            if "(`polymarket`)" in line or not line.startswith("| "):
                continue
            if any(f"`{market_id}`" in line for market_id in MARKET_IDS):
                self.assertIn("| Stub |", line)


if __name__ == "__main__":
    unittest.main()
