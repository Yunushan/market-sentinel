from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


class PlatformSupportTests(unittest.TestCase):
    def test_requested_platforms_are_documented(self) -> None:
        text = (ROOT / "docs" / "PLATFORM_SUPPORT.md").read_text(encoding="utf-8")

        for fragment in (
            "Windows",
            "Ubuntu Linux",
            "macOS",
            "Other Linux distributions",
            "BSD",
            "generic Unix",
            "Solaris",
            "Android",
            "iOS",
            "not marked fully supported",
            "Promotion Gates",
            "Collecting Host Evidence",
            "collect_platform_evidence.py",
            "Reviewing Host Evidence",
            "review_platform_evidence.py",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, text)

    def test_platform_support_normal_check_passes(self) -> None:
        result = subprocess.run(
            [sys.executable, "scripts/verify_platform_support.py"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Platform support claims are documented", result.stdout)

    def test_full_platform_claim_remains_blocked_until_real_evidence_exists(self) -> None:
        result = subprocess.run(
            [sys.executable, "scripts/verify_platform_support.py", "--require-full"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Full platform support claim is blocked", result.stdout)
        for fragment in ("BSD", "Solaris", "Android", "iOS"):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, result.stdout)


if __name__ == "__main__":
    unittest.main()
