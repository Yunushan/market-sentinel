from __future__ import annotations

import unittest
from pathlib import Path

from market_adapters import MARKET_IDS


ROOT = Path(__file__).resolve().parent.parent
BLOCKERS = ROOT / "docs" / "BLOCKERS.md"
REQUIRED_HEADERS = (
    "# Blockers",
    "## Summary",
    "## Market Blockers",
    "## Implementation Rules For Clearing A Blocker",
)
REQUIRED_COLUMNS = (
    "Market id",
    "Current adapter",
    "Blocker",
    "Required before full support",
    "Reference",
)


class BlockersDocTests(unittest.TestCase):
    def test_blockers_doc_has_required_sections_and_columns(self) -> None:
        text = BLOCKERS.read_text(encoding="utf-8")

        for header in REQUIRED_HEADERS:
            self.assertIn(header, text)
        for column in REQUIRED_COLUMNS:
            self.assertIn(column, text)

    def test_blockers_doc_covers_every_catalog_market(self) -> None:
        text = BLOCKERS.read_text(encoding="utf-8")

        for market_id in MARKET_IDS:
            self.assertIn(f"`{market_id}`", text)

    def test_blockers_doc_marks_non_polymarket_markets_as_stubs(self) -> None:
        text = BLOCKERS.read_text(encoding="utf-8")

        for line in text.splitlines():
            if " `polymarket` " in line or not line.startswith("| `"):
                continue
            if any(f"`{market_id}`" in line for market_id in MARKET_IDS):
                self.assertIn("| Stub |", line)

    def test_blockers_doc_keeps_live_trading_disabled_rule(self) -> None:
        text = BLOCKERS.read_text(encoding="utf-8")

        self.assertIn("Live trading remains disabled by default", text)
        self.assertIn("Keep paper/dry-run mode as default", text)
        self.assertIn("Never commit credentials", text)


if __name__ == "__main__":
    unittest.main()
