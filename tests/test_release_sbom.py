from __future__ import annotations

import unittest
from pathlib import Path

from scripts.generate_release_sbom import build_sbom


ROOT = Path(__file__).resolve().parent.parent


class ReleaseSbomTests(unittest.TestCase):
    def test_sbom_contains_project_and_locked_dependencies(self) -> None:
        version = next(
            line.split('"', 2)[1]
            for line in (ROOT / "pyproject.toml").read_text(encoding="utf-8").splitlines()
            if line.startswith("version = ")
        )
        payload = build_sbom(version)
        self.assertEqual(payload["spdxVersion"], "SPDX-2.3")
        self.assertEqual(payload["packages"][0]["name"], "market-sentinel")
        names = {package["name"] for package in payload["packages"]}
        self.assertIn("requests", names)
        self.assertIn("react", names)
        self.assertGreater(len(payload["relationships"]), 2)


if __name__ == "__main__":
    unittest.main()
