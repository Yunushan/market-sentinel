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
        self.assertIn("polymarket_live_validation", payload)
        self.assertIn("polymarket_live_validation_reports", payload)
        self.assertIn("paper", payload)
        self.assertEqual(payload["health"]["tkinter_fallback"], "run_gui.bat or python app.py")
        self.assertIn("/api/alerts", payload["health"]["routes"]["GET"])
        self.assertIn("/api/wallets", payload["health"]["routes"]["GET"])
        self.assertIn("/api/paper", payload["health"]["routes"]["GET"])
        self.assertIn("/api/live-safety/preflight", payload["health"]["routes"]["POST"])

    def test_frontend_package_supports_strict_build_verification(self) -> None:
        package = json.loads((ROOT / "frontend" / "package.json").read_text(encoding="utf-8"))

        self.assertEqual(package["scripts"]["dev"], "vite --host 127.0.0.1 --port 5173")
        self.assertEqual(
            package["scripts"]["build"],
            "tsc -p tsconfig.json --noEmit && tsc -p tsconfig.node.json --noEmit && vite build",
        )
        self.assertEqual(package["scripts"]["preview"], "vite preview --host 127.0.0.1 --port 4173")
        self.assertIn("react", package["dependencies"])
        self.assertIn("typescript", package["devDependencies"])
        self.assertIn("vite", package["devDependencies"])

    def test_status_pill_accepts_formatted_jsx_children(self) -> None:
        source = (ROOT / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")

        self.assertIn("import type { FormEvent, ReactNode } from \"react\";", source)
        self.assertIn("{ children: ReactNode; tone?: \"good\" | \"warn\" | \"neutral\" }", source)

    def test_react_analytics_source_exposes_direct_mdd_lookup_and_cached_detail(self) -> None:
        app_source = (ROOT / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")
        api_source = (ROOT / "frontend" / "src" / "api.ts").read_text(encoding="utf-8")
        type_source = (ROOT / "frontend" / "src" / "types.ts").read_text(encoding="utf-8")

        self.assertIn("Direct Wallet MDD", app_source)
        self.assertIn("MDD Audit Detail", app_source)
        self.assertIn("onAuditDetailLoad", app_source)
        self.assertIn("fetchPolymarketMddAudit", api_source)
        self.assertIn("PolymarketMddAuditExport", type_source)

    def test_react_analytics_source_exposes_mdd_cache_management(self) -> None:
        app_source = (ROOT / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")
        api_source = (ROOT / "frontend" / "src" / "api.ts").read_text(encoding="utf-8")
        type_source = (ROOT / "frontend" / "src" / "types.ts").read_text(encoding="utf-8")

        self.assertIn("MDD Audit Cache", app_source)
        self.assertIn("onMddCachePurge", app_source)
        self.assertIn("fetchPolymarketMddCache", api_source)
        self.assertIn("purgePolymarketMddCache", api_source)
        self.assertIn("PolymarketMddCachePayload", type_source)

    def test_react_tabs_can_be_opened_for_browser_smoke_routes(self) -> None:
        source = (ROOT / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")

        self.assertIn("function initialTabFromUrl()", source)
        self.assertIn('new URLSearchParams(window.location.search).get("tab")', source)
        self.assertIn("useState<Tab>(initialTabFromUrl)", source)

    def test_react_live_safety_exposes_polymarket_live_validation_report(self) -> None:
        app_source = (ROOT / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")
        api_source = (ROOT / "frontend" / "src" / "api.ts").read_text(encoding="utf-8")
        type_source = (ROOT / "frontend" / "src" / "types.ts").read_text(encoding="utf-8")

        self.assertIn("Polymarket Live Validation", app_source)
        self.assertIn("Validation Reports", app_source)
        self.assertIn("Store Snapshot", app_source)
        self.assertIn("stage_gates", app_source)
        self.assertIn("fetchPolymarketLiveValidation", api_source)
        self.assertIn("storePolymarketLiveValidationReport", api_source)
        self.assertIn("deletePolymarketLiveValidationReport", api_source)
        self.assertIn("/api/polymarket/live-validation", api_source)
        self.assertIn("PolymarketLiveValidationPayload", type_source)
        self.assertIn("PolymarketLiveValidationReportsPayload", type_source)

    def test_windows_package_supports_tkinter_and_react_modes(self) -> None:
        app_source = (ROOT / "app.py").read_text(encoding="utf-8")
        build_source = (ROOT / "scripts" / "build_windows_release.py").read_text(encoding="utf-8")

        self.assertIn("--web-gui", app_source)
        self.assertIn("run_server(parsed.host, parsed.port, parsed.config, parsed.frontend_dir)", app_source)
        self.assertIn("PyInstaller", build_source)
        self.assertIn("start_tkinter_gui.bat", build_source)
        self.assertIn("start_web_gui.bat", build_source)
        self.assertIn("PREDICTION_MARKET_CONFIG_PATH", build_source)
        self.assertIn("--config \"%PREDICTION_MARKET_CONFIG_PATH%\"", build_source)
        self.assertIn("StartMenuTkinterShortcut", build_source)
        self.assertIn("Target=\"[INSTALLFOLDER]start_tkinter_gui.bat\"", build_source)
        self.assertIn("wix, \"build\"", build_source)

    def test_readme_documents_final_parity_commands(self) -> None:
        text = (ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("python app.py --smoke-test", text)
        self.assertIn("python verify.py --frontend-build", text)
        self.assertIn("npm run build", text)
        self.assertIn("run_gui.bat", text)

    def test_verify_frontend_build_uses_windows_npm_command(self) -> None:
        source = (ROOT / "verify.py").read_text(encoding="utf-8")

        self.assertIn('"npm.cmd" if sys.platform == "win32" else "npm"', source)
        self.assertIn("[npm_command(), \"run\", \"build\"]", source)


if __name__ == "__main__":
    unittest.main()
