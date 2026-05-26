from __future__ import annotations

import unittest
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 compatibility.
    import tomli as tomllib


ROOT = Path(__file__).resolve().parent.parent
PROJECT_NAME = "prediction-market-alert-and-copy-trade-gui"


class ProjectMetadataTests(unittest.TestCase):
    def test_project_name_uses_dashes_not_underscores(self) -> None:
        data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        name = data["project"]["name"]

        self.assertEqual(name, PROJECT_NAME)
        self.assertNotIn("_", name)

    def test_user_facing_project_title_uses_dashed_name(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        app = (ROOT / "app.py").read_text(encoding="utf-8")

        self.assertIn(f"# {PROJECT_NAME}", readme)
        self.assertIn(f'self.title("{PROJECT_NAME}")', app)
        self.assertIn(f'set_windows_app_id("{PROJECT_NAME}")', app)
        self.assertIn(f'User-Agent": "{PROJECT_NAME}/1.0"', app)

    def test_old_polymarket_project_branding_is_not_used(self) -> None:
        files = [
            ROOT / "README.md",
            ROOT / "app.py",
            ROOT / "pyproject.toml",
            ROOT / "GOAL.md",
        ]
        forbidden = (
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
