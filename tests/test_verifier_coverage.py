from __future__ import annotations

import unittest
from unittest.mock import patch

import verify
from market_adapters import MARKET_IDS, VERIFIED_BLOCKERS


class VerifierCoverageTests(unittest.TestCase):
    def test_implemented_adapter_fixture_mapping_matches_the_catalog(self) -> None:
        implemented = set(MARKET_IDS) - set(VERIFIED_BLOCKERS)
        self.assertEqual(set(verify.IMPLEMENTED_ADAPTER_FIXTURE_TESTS), implemented)
        verify.run_adapter_fixture_coverage_check()

    def test_implemented_adapter_fixture_check_rejects_an_incomplete_mapping(self) -> None:
        broken = dict(verify.IMPLEMENTED_ADAPTER_FIXTURE_TESTS)
        broken.pop("polymarket")

        with patch.object(verify, "IMPLEMENTED_ADAPTER_FIXTURE_TESTS", broken):
            with self.assertRaises(SystemExit) as ctx:
                verify.run_adapter_fixture_coverage_check()

        self.assertIn("missing mappings: polymarket", str(ctx.exception))
