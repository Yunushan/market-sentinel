from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List


ROOT = Path(__file__).resolve().parents[1]
DOC_PATH = ROOT / "docs" / "PLATFORM_SUPPORT.md"

PLATFORMS: Dict[str, Dict[str, Any]] = {
    "windows": {
        "label": "Windows",
        "required_for_full_claim": True,
        "fully_tested": True,
        "evidence": ["windows-latest CI", "Tkinter smoke", "python verify.py", "Windows EXE/MSI release build"],
    },
    "ubuntu_linux": {
        "label": "Ubuntu Linux",
        "required_for_full_claim": True,
        "fully_tested": True,
        "evidence": ["ubuntu-latest CI", "Tkinter smoke", "python verify.py"],
    },
    "macos": {
        "label": "macOS",
        "required_for_full_claim": True,
        "fully_tested": True,
        "evidence": ["macos-latest CI", "Tkinter smoke", "python verify.py"],
    },
    "other_linux": {
        "label": "Other Linux distributions",
        "required_for_full_claim": True,
        "fully_tested": False,
        "missing": ["named distro matrix or documented distro certification runner"],
    },
    "bsd": {
        "label": "BSD",
        "required_for_full_claim": True,
        "fully_tested": False,
        "missing": ["BSD runner or VM", "dependency install", "Tkinter smoke", "python verify.py"],
    },
    "generic_unix": {
        "label": "generic Unix",
        "required_for_full_claim": True,
        "fully_tested": False,
        "missing": ["named Unix OS target", "runner", "dependency install", "python verify.py"],
    },
    "solaris": {
        "label": "Solaris",
        "required_for_full_claim": True,
        "fully_tested": False,
        "missing": ["Solaris runner or VM", "Python dependency toolchain", "Tkinter smoke", "python verify.py"],
    },
    "android": {
        "label": "Android",
        "required_for_full_claim": True,
        "fully_tested": False,
        "missing": ["mobile app/web deployment model", "Android emulator CI", "package/install smoke"],
    },
    "ios": {
        "label": "iOS",
        "required_for_full_claim": True,
        "fully_tested": False,
        "missing": ["mobile app/web deployment model", "iOS simulator CI", "signing/package strategy"],
    },
}


def platform_report() -> Dict[str, Any]:
    doc = DOC_PATH.read_text(encoding="utf-8") if DOC_PATH.exists() else ""
    missing_from_docs: List[str] = []
    for item in PLATFORMS.values():
        if item["label"] not in doc:
            missing_from_docs.append(item["label"])

    full_claim_blockers = [
        {
            "platform": item["label"],
            "missing": item.get("missing", []),
        }
        for item in PLATFORMS.values()
        if item["required_for_full_claim"] and not item["fully_tested"]
    ]
    return {
        "ok": not missing_from_docs,
        "doc_path": str(DOC_PATH),
        "platforms": PLATFORMS,
        "missing_from_docs": missing_from_docs,
        "full_claim_ready": not full_claim_blockers,
        "full_claim_blockers": full_claim_blockers,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify MarketSentinel platform support claims.")
    parser.add_argument("--json", action="store_true", help="Print platform support status as JSON.")
    parser.add_argument(
        "--require-full",
        action="store_true",
        help="Fail unless every required platform has complete test evidence.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = platform_report()
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    if not report["ok"]:
        print("Platform support docs are missing: " + ", ".join(report["missing_from_docs"]))
        return 1
    if args.require_full and not report["full_claim_ready"]:
        blockers = "; ".join(
            f"{item['platform']}: {', '.join(item['missing'])}" for item in report["full_claim_blockers"]
        )
        print("Full platform support claim is blocked: " + blockers)
        return 1
    if not args.json:
        print("[ok] Platform support claims are documented.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
