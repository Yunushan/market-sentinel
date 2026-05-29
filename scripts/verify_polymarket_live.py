from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    load_dotenv = None  # type: ignore

from polymarket import bridge, clob_auth, clob_rest, data_api, gamma, relayer
from polymarket.auth_readiness import build_clob_auth_readiness
from polymarket.credential_runbook import build_polymarket_credential_runbook
from polymarket.live_verification import (
    ABSOLUTE_MAX_VERIFY_NOTIONAL,
    ABSOLUTE_MAX_VERIFY_SIZE,
    CONFIRM_LIVE_ORDER_CANCEL,
    LiveOrderCancelRequest,
    build_live_validation_stage_gates,
    load_allow_token_ids,
    run_live_order_cancel_verification,
)
from polymarket.trader import PolymarketTrader, TraderConfig
from polymarket.ws_user import build_user_subscription, probe_user_websocket


L2_HEADERS = ("POLY_ADDRESS", "POLY_API_KEY", "POLY_PASSPHRASE", "POLY_SIGNATURE", "POLY_TIMESTAMP")
RELAYER_HEADERS = ("RELAYER_API_KEY", "RELAYER_API_KEY_ADDRESS")
BUILDER_HEADERS = (
    "POLY_BUILDER_API_KEY",
    "POLY_BUILDER_TIMESTAMP",
    "POLY_BUILDER_PASSPHRASE",
    "POLY_BUILDER_SIGNATURE",
)


def _load_env() -> None:
    if load_dotenv is not None:
        load_dotenv(ROOT / ".env")


def _present(names: Iterable[str]) -> Dict[str, bool]:
    return {name: bool(os.getenv(name)) for name in names}


def _missing(names: Iterable[str]) -> list[str]:
    return [name for name in names if not os.getenv(name)]


def _headers(names: Iterable[str]) -> Dict[str, str]:
    return {name: os.getenv(name, "") for name in names if os.getenv(name)}


def _result(status: str, detail: str, **extra: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {"status": status, "detail": detail}
    out.update(extra)
    return out


def _skipped(detail: str) -> Dict[str, Any]:
    return _result("skipped", detail)


def _probe(fn: Callable[[], Any], success_detail: str) -> Dict[str, Any]:
    try:
        value = fn()
        return _result("ok", success_detail, sample_type=type(value).__name__)
    except Exception as exc:
        return _result("failed", f"{type(exc).__name__}: {exc}")


def _public_checks(timeout: float) -> Dict[str, Any]:
    return {
        "clob_time": _probe(lambda: clob_rest.get_server_time(timeout=timeout), "CLOB /time responded."),
        "gamma_markets": _probe(lambda: gamma.list_markets(limit=1, timeout=timeout), "Gamma /markets responded."),
        "data_leaderboard": _probe(
            lambda: data_api.get_leaderboard(limit=1, timeout=timeout),
            "Data /v1/leaderboard responded.",
        ),
        "bridge_supported_assets": _probe(
            lambda: bridge.get_supported_assets(timeout=timeout),
            "Bridge /supported-assets responded.",
        ),
    }


def _skipped_public_checks() -> Dict[str, Any]:
    return {
        "clob_time": _skipped("Skipped by --skip-public-checks."),
        "gamma_markets": _skipped("Skipped by --skip-public-checks."),
        "data_leaderboard": _skipped("Skipped by --skip-public-checks."),
        "bridge_supported_assets": _skipped("Skipped by --skip-public-checks."),
    }


def _authenticated_read_checks(
    timeout: float,
    *,
    include_user_websocket_connect: bool = False,
    user_ws_markets: Iterable[str] = (),
) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    l2_missing = _missing(L2_HEADERS)
    if l2_missing:
        out["clob_l2_orders"] = _result("blocked", "Missing explicit CLOB L2 headers.", missing=l2_missing)
    else:
        out["clob_l2_orders"] = _probe(
            lambda: clob_auth.get_orders(_headers(L2_HEADERS), timeout=timeout),
            "Authenticated CLOB order list responded.",
        )

    private_key = os.getenv("POLYMARKET_PRIVATE_KEY") or os.getenv("PRIVATE_KEY")
    if not private_key:
        out["py_clob_client_credentials"] = _result(
            "blocked",
            "Missing POLYMARKET_PRIVATE_KEY or PRIVATE_KEY for official py-clob-client credential derivation.",
            missing=["POLYMARKET_PRIVATE_KEY", "PRIVATE_KEY"],
        )
    else:
        def derive() -> str:
            readiness = build_clob_auth_readiness()
            if readiness["blockers"]:
                raise ValueError("; ".join(readiness["blockers"]))
            PolymarketTrader(
                TraderConfig(
                    private_key=private_key,
                    funder_address=os.getenv("POLYMARKET_FUNDER_ADDRESS") or os.getenv("FUNDER_ADDRESS") or None,
                    signature_type=int(os.getenv("POLYMARKET_SIGNATURE_TYPE") or os.getenv("SIGNATURE_TYPE") or "0"),
                )
            )
            return "derived"

        out["py_clob_client_credentials"] = _probe(derive, "py-clob-client initialized and derived API credentials.")

    if _missing(RELAYER_HEADERS):
        out["relayer_recent_transactions"] = _result(
            "blocked",
            "Missing relayer API key headers.",
            missing=_missing(RELAYER_HEADERS),
        )
    else:
        out["relayer_recent_transactions"] = _probe(
            lambda: relayer.get_recent_transactions(_headers(RELAYER_HEADERS), timeout=timeout),
            "Authenticated relayer recent transactions responded.",
        )

    user_ws_auth = {
        "apiKey": os.getenv("POLY_API_KEY") or "",
        "secret": os.getenv("POLY_API_SECRET") or os.getenv("POLY_SECRET") or "",
        "passphrase": os.getenv("POLY_PASSPHRASE") or "",
    }
    user_ws_ready = False
    try:
        build_user_subscription(user_ws_auth)
        out["user_websocket_auth_payload"] = _result("ok", "User WebSocket auth payload can be built.")
        user_ws_ready = True
    except ValueError as exc:
        out["user_websocket_auth_payload"] = _result("blocked", str(exc))
    if include_user_websocket_connect and user_ws_ready:
        out["user_websocket_connect"] = _probe(
            lambda: probe_user_websocket(user_ws_auth, user_ws_markets, timeout=timeout),
            "Authenticated user WebSocket connected and subscription payload was sent.",
        )
    elif include_user_websocket_connect:
        out["user_websocket_connect"] = _result(
            "blocked",
            "Cannot open user WebSocket until apiKey, secret, and passphrase are present.",
        )
    else:
        out["user_websocket_connect"] = _skipped(
            "Not run. Pass --include-user-websocket-connect to open the authenticated user WebSocket.",
        )
    return out


def _skipped_authenticated_read_checks() -> Dict[str, Any]:
    return {
        "clob_l2_orders": _skipped("Skipped by --skip-authenticated-read-checks."),
        "py_clob_client_credentials": _skipped("Skipped by --skip-authenticated-read-checks."),
        "relayer_recent_transactions": _skipped("Skipped by --skip-authenticated-read-checks."),
        "user_websocket_auth_payload": _skipped("Skipped by --skip-authenticated-read-checks."),
        "user_websocket_connect": _skipped("Skipped by --skip-authenticated-read-checks."),
    }


def _bridge_address_checks(args: argparse.Namespace) -> Dict[str, Any]:
    if not args.include_bridge_address_creation:
        return {
            "deposit_address_creation": _result(
                "blocked",
                "Not run. Pass --include-bridge-address-creation with --bridge-address for explicit address-creation verification.",
            ),
            "withdrawal_address_creation": _result(
                "blocked",
                "Not run. Pass --include-bridge-address-creation with withdrawal args for explicit address-creation verification.",
            ),
        }
    if not args.bridge_address:
        return {
            "deposit_address_creation": _result("blocked", "Missing --bridge-address."),
            "withdrawal_address_creation": _result("blocked", "Missing --bridge-address."),
        }
    out = {
        "deposit_address_creation": _probe(
            lambda: bridge.create_deposit_addresses(args.bridge_address, timeout=args.timeout),
            "Bridge deposit address creation responded.",
        )
    }
    required = (args.to_chain_id, args.to_token_address, args.recipient_addr)
    if all(required):
        out["withdrawal_address_creation"] = _probe(
            lambda: bridge.create_withdrawal_addresses(
                address=args.bridge_address,
                to_chain_id=args.to_chain_id,
                to_token_address=args.to_token_address,
                recipient_addr=args.recipient_addr,
                timeout=args.timeout,
            ),
            "Bridge withdrawal address creation responded.",
        )
    else:
        out["withdrawal_address_creation"] = _result(
            "blocked",
            "Missing --to-chain-id, --to-token-address, or --recipient-addr.",
        )
    return out


def _funded_order_check(args: argparse.Namespace) -> Dict[str, Any]:
    if not any((args.token_id, args.side, args.price, args.size, args.allow_funded_order)):
        return _result(
            "blocked",
            "Not run. Pass token, side, price, and size for a dry-run transcript; add explicit execution flags for a real order/cancel check.",
        )
    try:
        allow_tokens = load_allow_token_ids(args.allow_token_id or (), file_path=args.allow_token_file)
        return run_live_order_cancel_verification(
            LiveOrderCancelRequest(
                token_id=args.token_id or "",
                side=args.side or "",
                price=args.price,
                size=args.size,
                tif=args.tif,
                allow_token_ids=allow_tokens,
                private_key=os.getenv("POLYMARKET_PRIVATE_KEY") or os.getenv("PRIVATE_KEY") or "",
                funder_address=os.getenv("POLYMARKET_FUNDER_ADDRESS") or os.getenv("FUNDER_ADDRESS") or None,
                signature_type=os.getenv("POLYMARKET_SIGNATURE_TYPE") or os.getenv("SIGNATURE_TYPE") or "0",
                execute=bool(args.allow_funded_order),
                cancel_immediately=bool(args.cancel_immediately),
                confirmation=args.confirm_live_order_cancel or "",
                max_size=args.max_verify_size,
                max_notional=args.max_verify_notional,
                maker_price_buffer=args.maker_price_buffer,
            )
        )
    except Exception as exc:
        return _result("failed", f"{type(exc).__name__}: {exc}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify Polymarket public, credentialed, and optional live flows.")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--skip-public-checks", action="store_true")
    parser.add_argument("--skip-authenticated-read-checks", action="store_true")
    parser.add_argument("--require-authenticated-read-ok", action="store_true")
    parser.add_argument("--include-user-websocket-connect", action="store_true")
    parser.add_argument("--user-ws-market", action="append", default=[])
    parser.add_argument("--report-file")
    parser.add_argument("--include-bridge-address-creation", action="store_true")
    parser.add_argument("--bridge-address")
    parser.add_argument("--to-chain-id")
    parser.add_argument("--to-token-address")
    parser.add_argument("--recipient-addr")
    parser.add_argument("--allow-funded-order", action="store_true")
    parser.add_argument("--cancel-immediately", action="store_true")
    parser.add_argument("--confirm-live-order-cancel")
    parser.add_argument("--allow-token-id", action="append", default=[])
    parser.add_argument("--allow-token-file")
    parser.add_argument("--token-id")
    parser.add_argument("--side", choices=["BUY", "SELL"])
    parser.add_argument("--price")
    parser.add_argument("--size")
    parser.add_argument("--tif", default="GTC")
    parser.add_argument("--max-verify-size", type=float, default=ABSOLUTE_MAX_VERIFY_SIZE)
    parser.add_argument("--max-verify-notional", type=float, default=ABSOLUTE_MAX_VERIFY_NOTIONAL)
    parser.add_argument("--maker-price-buffer", type=float, default=0.005)
    args = parser.parse_args()

    _load_env()
    public_checks = _skipped_public_checks() if args.skip_public_checks else _public_checks(args.timeout)
    authenticated_checks = (
        _skipped_authenticated_read_checks()
        if args.skip_authenticated_read_checks
        else _authenticated_read_checks(
            args.timeout,
            include_user_websocket_connect=args.include_user_websocket_connect,
            user_ws_markets=args.user_ws_market,
        )
    )
    report = {
        "ok": True,
        "generated_at": time.time(),
        "mode": "strict_cli",
        "market_id": "polymarket",
        "credential_presence": {
            "clob_l2_headers": _present(L2_HEADERS),
            "py_clob_client": _present(("POLYMARKET_PRIVATE_KEY", "PRIVATE_KEY", "POLYMARKET_FUNDER_ADDRESS", "FUNDER_ADDRESS", "POLYMARKET_SIGNATURE_TYPE", "SIGNATURE_TYPE")),
            "relayer_headers": _present(RELAYER_HEADERS),
            "builder_headers": _present(BUILDER_HEADERS),
            "user_ws": _present(("POLY_API_KEY", "POLY_API_SECRET", "POLY_SECRET", "POLY_PASSPHRASE")),
        },
        "clob_auth_readiness": build_clob_auth_readiness(),
        "credential_runbook": build_polymarket_credential_runbook(),
        "live_order_cancel_harness": {
            "default_mode": "dry_run_transcript",
            "execute_flag": "--allow-funded-order",
            "confirmation_required": CONFIRM_LIVE_ORDER_CANCEL,
            "hard_max_size": ABSOLUTE_MAX_VERIFY_SIZE,
            "hard_max_notional": ABSOLUTE_MAX_VERIFY_NOTIONAL,
        },
        "public_checks": public_checks,
        "authenticated_read_checks": authenticated_checks,
        "bridge_address_checks": _bridge_address_checks(args),
        "funded_live_order_check": _funded_order_check(args),
    }
    report["stage_gates"] = build_live_validation_stage_gates(report)
    report["ok"] = not any(
        item.get("status") == "failed"
        for section in (
            report["public_checks"],
            report["authenticated_read_checks"],
            report["bridge_address_checks"],
        )
        for item in section.values()
    ) and report["funded_live_order_check"].get("status") != "failed"
    if args.require_authenticated_read_ok and not report["stage_gates"]["credentialed_read_ok"]:
        report["ok"] = False
        report["stage_gates"]["required_authenticated_read"] = "failed"
    if args.report_file:
        path = Path(args.report_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
