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
        "evidence": ["windows-2025-vs2026 CI", "Tkinter smoke", "python verify.py", "Windows EXE/MSI release build"],
    },
    "windows_11": {
        "label": "Windows 11",
        "required_for_full_claim": True,
        "fully_tested": True,
        "evidence": ["windows-11-arm CI", "Tkinter smoke", "python verify.py"],
    },
    "windows_10": {
        "label": "Windows 10",
        "required_for_full_claim": True,
        "fully_tested": False,
        "evidence": ["opt-in self-hosted Windows 10 CI job"],
        "missing": ["self-hosted Windows 10 runner labelled windows-10", "required CI run with ENABLE_WINDOWS_10_SELF_HOSTED=true"],
    },
    "ubuntu_linux": {
        "label": "Ubuntu Linux",
        "required_for_full_claim": True,
        "fully_tested": True,
        "evidence": ["ubuntu-latest CI", "Tkinter smoke", "python verify.py"],
    },
    "rhel_ubi": {
        "label": "Red Hat Enterprise Linux / UBI",
        "required_for_full_claim": True,
        "fully_tested": False,
        "evidence": ["RHEL UBI 8 CI", "RHEL UBI 9 CI", "RHEL UBI 10 CI", "RHEL 7 ABI manylinux2014 CI"],
        "missing": ["RHEL runner or container with Tkinter", "python app.py --smoke-test", "python verify.py"],
    },
    "rocky_linux": {
        "label": "Rocky Linux",
        "required_for_full_claim": True,
        "fully_tested": False,
        "evidence": ["Rocky Linux 8 CI", "Rocky Linux 9 CI", "Rocky Linux 10 CI"],
        "missing": ["Rocky runner or container with Tkinter", "python app.py --smoke-test", "python verify.py"],
    },
    "macos": {
        "label": "macOS",
        "required_for_full_claim": True,
        "fully_tested": True,
        "evidence": ["macos-14 CI", "macos-15 CI", "macos-26 CI", "Tkinter smoke", "python verify.py"],
    },
    "other_linux": {
        "label": "Other Linux distributions",
        "required_for_full_claim": True,
        "fully_tested": False,
        "missing": ["additional named distro matrix or documented distro certification runner"],
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
        "evidence": ["Android 14 mobile web smoke", "Android 15 mobile web smoke", "Android 16 mobile web smoke"],
        "missing": ["native/mobile app deployment model", "Android emulator CI", "package/install smoke"],
    },
    "ios": {
        "label": "iOS",
        "required_for_full_claim": True,
        "fully_tested": False,
        "evidence": ["iOS 15 mobile web smoke", "iOS 16 mobile web smoke", "iOS 18 mobile web smoke", "iOS 26 mobile web smoke"],
        "missing": ["native/mobile app deployment model", "iOS simulator CI", "signing/package strategy"],
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
