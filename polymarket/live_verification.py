from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Mapping, Optional, Sequence

from . import clob_rest
from .auth_readiness import build_clob_auth_readiness, parse_signature_type, redacted_address
from .http_client import PolymarketValidationError
from .trader import PolymarketTrader, TraderConfig


CONFIRM_LIVE_ORDER_CANCEL = "I_UNDERSTAND_THIS_PLACES_A_REAL_POLYMARKET_ORDER"
ABSOLUTE_MAX_VERIFY_SIZE = 5.0
ABSOLUTE_MAX_VERIFY_NOTIONAL = 1.0
DEFAULT_MAKER_PRICE_BUFFER = 0.005


@dataclass(frozen=True)
class LiveOrderCancelRequest:
    token_id: str = ""
    side: str = ""
    price: Any = None
    size: Any = None
    tif: str = "GTC"
    allow_token_ids: Sequence[str] = ()
    private_key: str = ""
    funder_address: Optional[str] = None
    signature_type: Any = 0
    execute: bool = False
    cancel_immediately: bool = False
    confirmation: str = ""
    max_size: Any = ABSOLUTE_MAX_VERIFY_SIZE
    max_notional: Any = ABSOLUTE_MAX_VERIFY_NOTIONAL
    maker_price_buffer: Any = DEFAULT_MAKER_PRICE_BUFFER


def load_allow_token_ids(values: Iterable[str] = (), *, file_path: Optional[str] = None) -> list[str]:
    tokens = [str(value).strip() for value in values if str(value).strip()]
    if file_path:
        path = Path(file_path)
        for line in path.read_text(encoding="utf-8").splitlines():
            clean = line.split("#", 1)[0].strip()
            if clean:
                tokens.append(clean)
    out: list[str] = []
    for token in tokens:
        if token not in out:
            out.append(token)
    return out


def build_live_order_cancel_plan(request: LiveOrderCancelRequest) -> Dict[str, Any]:
    blockers: list[str] = []
    warnings: list[str] = []

    token_id = str(request.token_id or "").strip()
    side = str(request.side or "").strip().upper()
    tif = str(request.tif or "GTC").strip().upper()
    allow_token_ids = [str(token).strip() for token in request.allow_token_ids if str(token).strip()]

    price, price_error = _positive_float(request.price, "price")
    size, size_error = _positive_float(request.size, "size")
    max_size, max_size_error = _positive_float(request.max_size, "max size")
    max_notional, max_notional_error = _positive_float(request.max_notional, "max notional")
    maker_price_buffer, buffer_error = _non_negative_float(request.maker_price_buffer, "maker price buffer")

    for error in (price_error, size_error, max_size_error, max_notional_error, buffer_error):
        if error:
            blockers.append(error)

    if not token_id:
        blockers.append("Missing --token-id.")
    if side not in {"BUY", "SELL"}:
        blockers.append("Side must be BUY or SELL.")
    if price is not None and price >= 1:
        blockers.append("Price must be less than 1.")
    if tif != "GTC":
        blockers.append("Safe order/cancel verification requires TIF=GTC so the order can rest and be canceled.")
    if max_size is not None and max_size > ABSOLUTE_MAX_VERIFY_SIZE:
        blockers.append(f"Max size cap cannot exceed hard limit {ABSOLUTE_MAX_VERIFY_SIZE:g}.")
    if max_notional is not None and max_notional > ABSOLUTE_MAX_VERIFY_NOTIONAL:
        blockers.append(f"Max notional cap cannot exceed hard limit {ABSOLUTE_MAX_VERIFY_NOTIONAL:g} USDC.")
    if price is not None and size is not None:
        notional = price * size
        if max_size is not None and size > max_size:
            blockers.append(f"Size {size:g} exceeds max size cap {max_size:g}.")
        if max_notional is not None and notional > max_notional:
            blockers.append(f"Approx notional {notional:g} exceeds max notional cap {max_notional:g} USDC.")
    else:
        notional = None
    if not allow_token_ids:
        blockers.append("Missing token allow-list. Pass --allow-token-id or --allow-token-file.")
    elif token_id and token_id not in allow_token_ids:
        blockers.append("Token id is not present in the explicit allow-list.")
    if not request.cancel_immediately:
        blockers.append("Safe live verification requires --cancel-immediately.")

    try:
        signature_type = parse_signature_type(request.signature_type)
    except PolymarketValidationError as exc:
        blockers.append(str(exc))
        signature_type = 0

    readiness = build_clob_auth_readiness(
        {
            "private_key": request.private_key,
            "funder_address": request.funder_address or "",
            "signature_type": signature_type,
        },
        environ={},
    )
    if request.execute and readiness["blockers"]:
        blockers.extend(f"Auth readiness: {item}" for item in readiness["blockers"])
    if not request.private_key:
        warnings.append("Dry-run transcript only: private key is not present.")
    if not request.execute:
        warnings.append("Default dry-run mode: pass --allow-funded-order with exact confirmation text to execute.")
    if request.execute and request.confirmation != CONFIRM_LIVE_ORDER_CANCEL:
        blockers.append(f"Missing exact --confirm-live-order-cancel {CONFIRM_LIVE_ORDER_CANCEL!r}.")

    status = "blocked" if blockers else ("ready_to_execute" if request.execute else "dry_run")
    return {
        "status": status,
        "live_action": bool(request.execute and not blockers),
        "token_id": token_id,
        "side": side,
        "price": price,
        "size": size,
        "tif": tif,
        "approx_notional": notional,
        "caps": {
            "hard_max_size": ABSOLUTE_MAX_VERIFY_SIZE,
            "hard_max_notional": ABSOLUTE_MAX_VERIFY_NOTIONAL,
            "max_size": max_size,
            "max_notional": max_notional,
            "maker_price_buffer": maker_price_buffer,
        },
        "allow_list": {
            "count": len(allow_token_ids),
            "token_allowed": bool(token_id and token_id in allow_token_ids),
        },
        "auth_readiness": readiness,
        "redacted_credentials": {
            "private_key": "***" if request.private_key else "",
            "funder_address": redacted_address(request.funder_address),
            "signature_type": signature_type,
        },
        "required_execution_flags": [
            "--allow-funded-order",
            "--cancel-immediately",
            "--allow-token-id or --allow-token-file",
            "--confirm-live-order-cancel",
        ],
        "transcript": [
            "Validate token, side, price, size, TIF, caps, and allow-list.",
            "Validate private key, signature type, funder/deposit wallet, official host, and Polygon chain id.",
            "Fetch public orderbook and require a maker-side price before placing a live order.",
            "Place one GTC limit order through official py-clob-client.",
            "Immediately cancel the returned order id.",
            "Fetch the order after cancel and verify it is no longer live.",
        ],
        "blockers": blockers,
        "warnings": warnings,
    }


def run_live_order_cancel_verification(
    request: LiveOrderCancelRequest,
    *,
    trader_factory: Callable[[TraderConfig], Any] = PolymarketTrader,
    orderbook_getter: Callable[[str], Mapping[str, Any]] = clob_rest.get_book,
) -> Dict[str, Any]:
    plan = build_live_order_cancel_plan(request)
    if plan["status"] != "ready_to_execute":
        return plan

    book = orderbook_getter(plan["token_id"])
    best_bid, best_ask = clob_rest.best_bid_ask_from_book(dict(book))
    maker_blocker = maker_price_blocker(
        side=str(plan["side"]),
        price=float(plan["price"]),
        best_bid=best_bid,
        best_ask=best_ask,
        buffer=float(plan["caps"]["maker_price_buffer"]),
    )
    plan["orderbook_preflight"] = {"best_bid": best_bid, "best_ask": best_ask}
    if maker_blocker:
        plan["status"] = "blocked"
        plan["live_action"] = False
        plan["blockers"].append(maker_blocker)
        return plan

    trader = trader_factory(
        TraderConfig(
            private_key=request.private_key,
            funder_address=request.funder_address or None,
            signature_type=int(plan["redacted_credentials"]["signature_type"]),
        )
    )
    placed = trader.place_limit_order(
        token_id=str(plan["token_id"]),
        side=str(plan["side"]),
        price=float(plan["price"]),
        size=float(plan["size"]),
        tif=str(plan["tif"]),
    )
    order_id = extract_order_id(placed)
    audit: Dict[str, Any] = {"placed": _safe_payload(placed), "order_id": order_id}
    if not order_id:
        plan.update(
            {
                "status": "failed",
                "live_action": True,
                "audit": audit,
                "failure": "Order placement response did not include an order id; manual account review is required.",
            }
        )
        return plan

    cancelled = trader.cancel_order(order_id)
    post_cancel = trader.get_order(order_id)
    cancel_verified = cancel_response_contains(cancelled, order_id) and order_state_is_cancelled(post_cancel)
    audit.update(
        {
            "cancel": _safe_payload(cancelled),
            "post_cancel_order": _safe_payload(post_cancel),
            "post_cancel_verified": cancel_verified,
        }
    )
    plan.update({"status": "ok" if cancel_verified else "failed", "live_action": True, "audit": audit})
    if not cancel_verified:
        plan["failure"] = "Order cancel was submitted, but post-cancel verification did not prove the order is canceled."
    return plan


def build_live_validation_stage_gates(report: Mapping[str, Any]) -> Dict[str, Any]:
    public_status = _section_status(report.get("public_checks"))
    authenticated_status = _section_status(report.get("authenticated_read_checks"))
    bridge_status = _section_status(report.get("bridge_address_checks"))
    readiness = report.get("clob_auth_readiness") if isinstance(report.get("clob_auth_readiness"), Mapping) else {}
    funded = report.get("funded_live_order_check") if isinstance(report.get("funded_live_order_check"), Mapping) else {}
    funded_status = str(funded.get("status") or "unknown")
    credentialed_read_ok = _section_has_ok(report.get("authenticated_read_checks"))
    credential_readiness_ok = bool(readiness.get("ok"))
    safe_to_attempt_funded_order = (
        credential_readiness_ok
        and credentialed_read_ok
        and funded_status == "ready_to_execute"
        and bool(funded.get("live_action"))
    )
    return {
        "public_live_checks": public_status,
        "credential_readiness": "ok" if credential_readiness_ok else "blocked",
        "credentialed_read_checks": authenticated_status,
        "bridge_address_checks": bridge_status,
        "funded_live_order_check": funded_status,
        "credentialed_read_ok": credentialed_read_ok,
        "safe_to_attempt_funded_order": safe_to_attempt_funded_order,
        "requires_explicit_live_approval": True,
        "next_step": _next_live_validation_step(
            public_status=public_status,
            credential_readiness_ok=credential_readiness_ok,
            credentialed_read_ok=credentialed_read_ok,
            funded_status=funded_status,
        ),
    }


def _section_status(section: Any) -> str:
    if not isinstance(section, Mapping) or not section:
        return "skipped"
    statuses = [str(item.get("status") or "unknown") for item in section.values() if isinstance(item, Mapping)]
    if not statuses:
        return "skipped"
    if any(status == "failed" for status in statuses):
        return "failed"
    ok_count = statuses.count("ok")
    blocked_count = statuses.count("blocked")
    skipped_count = statuses.count("skipped")
    if ok_count and not blocked_count and not skipped_count:
        return "ok"
    if ok_count:
        return "partial"
    if blocked_count:
        return "blocked"
    if skipped_count == len(statuses):
        return "skipped"
    return "unknown"


def _section_has_ok(section: Any) -> bool:
    return bool(
        isinstance(section, Mapping)
        and any(isinstance(item, Mapping) and item.get("status") == "ok" for item in section.values())
    )


def _next_live_validation_step(
    *,
    public_status: str,
    credential_readiness_ok: bool,
    credentialed_read_ok: bool,
    funded_status: str,
) -> str:
    if public_status == "failed":
        return "Fix public Polymarket connectivity before using credentials or live-order checks."
    if not credential_readiness_ok:
        return "Provide valid local CLOB trading credentials or explicit signed L2 headers, then rerun readiness."
    if not credentialed_read_ok:
        return "Run at least one non-destructive authenticated read check before any funded verification."
    if funded_status in {"blocked", "skipped"}:
        return "Run a dry-run order/cancel transcript with token, side, price, size, and an explicit token allow-list."
    if funded_status == "dry_run":
        return "Review the dry-run transcript; real order/cancel still requires explicit funded flags and confirmation."
    if funded_status == "ready_to_execute":
        return "All local gates are ready; execute only if the operator explicitly approves the funded live check."
    if funded_status == "ok":
        return "Funded order/cancel verification completed and post-cancel verification passed."
    return "Review the report before taking any live action."


def maker_price_blocker(
    *,
    side: str,
    price: float,
    best_bid: Optional[float],
    best_ask: Optional[float],
    buffer: float = DEFAULT_MAKER_PRICE_BUFFER,
) -> str:
    side = str(side or "").upper()
    if side == "BUY":
        if best_ask is None:
            return "Cannot prove BUY order is maker-side because best ask is unavailable."
        if price > best_ask - buffer:
            return f"BUY price {price:g} is too close to/takes best ask {best_ask:g}; lower price or increase safety buffer."
    elif side == "SELL":
        if best_bid is None:
            return "Cannot prove SELL order is maker-side because best bid is unavailable."
        if price < best_bid + buffer:
            return f"SELL price {price:g} is too close to/takes best bid {best_bid:g}; raise price or increase safety buffer."
    else:
        return "Side must be BUY or SELL."
    return ""


def extract_order_id(payload: Any) -> str:
    if not isinstance(payload, Mapping):
        return ""
    for key in ("orderID", "orderId", "order_id", "id"):
        value = payload.get(key)
        if value:
            return str(value)
    return ""


def cancel_response_contains(payload: Any, order_id: str) -> bool:
    if not isinstance(payload, Mapping):
        return False
    canceled = payload.get("canceled")
    if isinstance(canceled, list) and str(order_id) in {str(item) for item in canceled}:
        return True
    return bool(payload.get("success") is True or payload.get("cancelled") is True or payload.get("canceled") is True)


def order_state_is_cancelled(payload: Any) -> bool:
    if not isinstance(payload, Mapping):
        return False
    status = str(payload.get("status") or payload.get("orderStatus") or "").upper()
    if "CANCEL" in status or status in {"DONE", "DEAD", "EXPIRED"}:
        return True
    if payload.get("live") is False or payload.get("open") is False:
        return True
    return False


def _safe_payload(payload: Any) -> Any:
    if isinstance(payload, Mapping):
        out: Dict[str, Any] = {}
        for key, value in payload.items():
            normalized = str(key).lower()
            if any(fragment in normalized for fragment in ("key", "secret", "passphrase", "signature", "private")):
                out[str(key)] = "***" if value not in (None, "") else ""
            else:
                out[str(key)] = _safe_payload(value)
        return out
    if isinstance(payload, list):
        return [_safe_payload(item) for item in payload]
    return payload


def _positive_float(value: Any, label: str) -> tuple[Optional[float], str]:
    number, error = _finite_float(value, label)
    if error:
        return None, error
    if number is None or number <= 0:
        return None, f"{label.capitalize()} must be greater than 0."
    return number, ""


def _non_negative_float(value: Any, label: str) -> tuple[Optional[float], str]:
    number, error = _finite_float(value, label)
    if error:
        return None, error
    if number is None or number < 0:
        return None, f"{label.capitalize()} must be greater than or equal to 0."
    return number, ""


def _finite_float(value: Any, label: str) -> tuple[Optional[float], str]:
    if value in (None, ""):
        return None, f"Missing {label}."
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None, f"{label.capitalize()} must be numeric."
    if not math.isfinite(number):
        return None, f"{label.capitalize()} must be finite."
    return number, ""
