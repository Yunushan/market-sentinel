from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    load_dotenv = None  # type: ignore

from polymarket.credential_runbook import build_polymarket_credential_runbook


def _load_env() -> None:
    if load_dotenv is not None:
        load_dotenv(ROOT / ".env")


def _write_report(path_text: str, payload: dict) -> None:
    path = Path(path_text)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _print_summary(runbook: dict) -> None:
    readiness = runbook.get("readiness", {})
    print("[ok] Polymarket credential runbook generated (no funded actions)")
    print(f"mode: {runbook.get('mode')}")
    print(f"network_calls: {runbook.get('network_calls')}")
    print(f"funded_execution_exposed: {runbook.get('funded_execution_exposed')}")
    for key in (
        "direct_l2_read_headers",
        "user_websocket_auth_payload",
        "relayer_headers",
        "sdk_trading_credentials",
    ):
        item = readiness.get(key, {}) if isinstance(readiness, dict) else {}
        status = item.get("status", "unknown")
        detail = item.get("detail", "")
        print(f"{key}: {status} - {detail}")
    candidates = readiness.get("credentialed_read_candidates", []) if isinstance(readiness, dict) else []
    print("credentialed_read_candidates: " + (", ".join(candidates) if candidates else "none"))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a local redacted Polymarket credential runbook without network or funded actions."
    )
    parser.add_argument("--json", action="store_true", help="Print the full redacted JSON runbook.")
    parser.add_argument("--report-file", help="Write the redacted JSON runbook to this path.")
    parser.add_argument(
        "--require-authenticated-read-ready",
        action="store_true",
        help="Exit non-zero unless at least one non-destructive authenticated read or stream candidate is locally ready.",
    )
    parser.add_argument(
        "--require-l2-read-ready",
        action="store_true",
        help="Exit non-zero unless all explicit CLOB L2 read headers are present.",
    )
    parser.add_argument(
        "--require-user-websocket-ready",
        action="store_true",
        help="Exit non-zero unless the authenticated user WebSocket payload can be built.",
    )
    args = parser.parse_args()

    _load_env()
    runbook = build_polymarket_credential_runbook()
    if args.report_file:
        _write_report(args.report_file, runbook)
    if args.json:
        print(json.dumps(runbook, indent=2, sort_keys=True))
    else:
        _print_summary(runbook)

    readiness = runbook["readiness"]
    if args.require_authenticated_read_ready and not readiness["non_destructive_auth_ready"]:
        return 1
    if args.require_l2_read_ready and readiness["direct_l2_read_headers"]["status"] != "ok":
        return 1
    if args.require_user_websocket_ready and readiness["user_websocket_auth_payload"]["status"] != "ok":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
