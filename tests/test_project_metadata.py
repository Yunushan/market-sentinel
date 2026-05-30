from __future__ import annotations

import unittest
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 compatibility.
    import tomli as tomllib


ROOT = Path(__file__).resolve().parent.parent
PROJECT_NAME = "market-sentinel"
APP_TITLE = "MarketSentinel"


class ProjectMetadataTests(unittest.TestCase):
    def test_project_name_uses_dashes_not_underscores(self) -> None:
        data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        frontend_package = (ROOT / "frontend" / "package.json").read_text(encoding="utf-8")
        name = data["project"]["name"]

        self.assertEqual(name, PROJECT_NAME)
        self.assertNotIn("_", name)
        self.assertEqual(data["project"]["requires-python"], ">=3.10")
        self.assertIn("Programming Language :: Python :: 3.15", data["project"]["classifiers"])
        self.assertIn("Programming Language :: Python :: 3.16", data["project"]["classifiers"])
        self.assertIn('"name": "market-sentinel-react-gui"', frontend_package)

    def test_user_facing_project_title_uses_marketsentinel_brand(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        app = (ROOT / "app.py").read_text(encoding="utf-8")

        self.assertIn(f"# {APP_TITLE}", readme)
        self.assertIn('assets/marketsentinel.svg', readme)
        self.assertTrue((ROOT / "assets" / "marketsentinel.svg").exists())
        self.assertTrue((ROOT / "assets" / "marketsentinel.ico").exists())
        self.assertTrue((ROOT / "marketsentinel.png").exists())
        self.assertTrue((ROOT / "frontend" / "public" / "marketsentinel.png").exists())
        self.assertIn(f'APP_TITLE = "{APP_TITLE}"', app)
        self.assertIn(f'APP_ID = "{PROJECT_NAME}"', app)
        self.assertIn('APP_USER_AGENT = f"{APP_ID}/1.0"', app)
        self.assertIn('headers={"User-Agent": APP_USER_AGENT}', app)

    def test_old_polymarket_project_branding_is_not_used(self) -> None:
        files = [
            ROOT / "README.md",
            ROOT / "app.py",
            ROOT / "pyproject.toml",
            ROOT / "GOAL.md",
        ]
        forbidden = (
            "prediction-market-alert-and-copy-trade-gui",
            "polymarket-alert-and-copy-trade-gui",
            "polymarket-sentinel-gui",
            "Polymarket Sentinel GUI",
            "PolymarketSentinelGUI",
        )

        for path in files:
            text = path.read_text(encoding="utf-8")
            for value in forbidden:
                with self.subTest(path=path.name, value=value):
                    self.assertNotIn(value, text)


if __name__ == "__main__":
    unittest.main()
