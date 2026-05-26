from __future__ import annotations

import json
import unittest
from pathlib import Path

from app import tkinter_smoke_payload
from core.models import AppConfig
from market_adapters import MARKET_IDS
from web_api import app_state_payload


ROOT = Path(__file__).resolve().parent.parent


class FinalParityTests(unittest.TestCase):
    def test_tkinter_smoke_payload_proves_fallback_gui_contract(self) -> None:
        payload = tkinter_smoke_payload()

        self.assertTrue(payload["ok"])
        self.assertTrue(payload["tkinter_base"])
        self.assertEqual(payload["app_class"], "App")
        self.assertEqual(payload["window_title"], "prediction-market-alert-and-copy-trade-gui")
        self.assertEqual(payload["fallback_command"], "python app.py")
        self.assertEqual(payload["market_count"], len(MARKET_IDS))
        self.assertEqual(payload["choice_count"], len(MARKET_IDS))
        self.assertTrue(payload["all_markets_configured"])

    def test_react_state_payload_covers_final_workflow_surfaces(self) -> None:
        payload = app_state_payload(AppConfig())

        self.assertIn("health", payload)
        self.assertIn("config", payload)
        self.assertIn("markets", payload)
        self.assertIn("alerts", payload)
        self.assertIn("wallets", payload)
        self.assertIn("copy", payload)
        self.assertIn("live_safety", payload)
        self.assertIn("paper", payload)
        self.assertEqual(payload["health"]["tkinter_fallback"], "run_gui.bat or python app.py")
        self.assertIn("/api/alerts", payload["health"]["routes"]["GET"])
        self.assertIn("/api/wallets", payload["health"]["routes"]["GET"])
        self.assertIn("/api/paper", payload["health"]["routes"]["GET"])
        self.assertIn("/api/live-safety/preflight", payload["health"]["routes"]["POST"])

    def test_frontend_package_supports_strict_build_verification(self) -> None:
        package = json.loads((ROOT / "frontend" / "package.json").read_text(encoding="utf-8"))

        self.assertEqual(package["scripts"]["dev"], "vite --host 127.0.0.1 --port 5173")
        self.assertEqual(package["scripts"]["build"], "tsc -b && vite build")
        self.assertEqual(package["scripts"]["preview"], "vite preview --host 127.0.0.1 --port 4173")
        self.assertIn("react", package["dependencies"])
        self.assertIn("typescript", package["devDependencies"])
        self.assertIn("vite", package["devDependencies"])

    def test_readme_documents_final_parity_commands(self) -> None:
        text = (ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("python app.py --smoke-test", text)
        self.assertIn("python verify.py --frontend-build", text)
        self.assertIn("npm run build", text)
        self.assertIn("run_gui.bat", text)


if __name__ == "__main__":
    unittest.main()
