"""Strict public Data API v2 envelope and native financial field validation."""
from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Optional

from .http_client import PolymarketResponseError, PolymarketValidationError


MAX_V2_PAGE_SIZE = 1000
POSITION_STATUSES = {"OPEN", "REDEEMABLE", "REDEEMABLE_LOST", "MERGEABLE", "CLOSED"}
POSITION_SORTS = {"CURRENT_VALUE", "PRICE", "TOKENS", "UNREALIZED_PNL", "REALIZED_PNL", "TOTAL_PNL", "TIMESTAMP"}
WALLET_PATTERN = re.compile(r"0x[0-9a-fA-F]{40}\Z")
CONDITION_PATTERN = re.compile(r"0x[0-9a-fA-F]{64}\Z")
IDENTITY_ALIASES = {
    "condition_id": "conditionId", "event_id": "eventId", "event_slug": "eventSlug",
    "outcome_index": "outcomeIndex", "profile_image": "profileImage",
    "profile_image_optimized": "profileImageOptimized", "proxy_wallet": "proxyWallet",
    "token_id": "asset", "transaction_hash": "transactionHash", "market_id": "marketId",
    "opposite_token_id": "oppositeAsset", "opposite_outcome": "oppositeOutcome",
}


def invalid(endpoint: str, reason: str, *, scope: str = "history completeness") -> PolymarketResponseError:
    return PolymarketResponseError(f"{endpoint} {reason}; {scope} is unknown.")


def opaque_cursor(value: Any) -> bool:
    return (isinstance(value, str) and bool(value) and value.strip() == value
            and not any(ord(char) < 32 or ord(char) == 127 for char in value))


def validate_user(user: Optional[str], *, required: bool = False) -> None:
    if user is None and not required:
        return
    if not isinstance(user, str) or not WALLET_PATTERN.fullmatch(user):
        raise PolymarketValidationError("Data API v2 user must be a 0x-prefixed wallet address.")


def validate_page_options(limit: int, cursor: Optional[str]) -> None:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_V2_PAGE_SIZE:
        raise PolymarketValidationError("Data API v2 limit must be between 1 and 1000.")
    if cursor is not None and not opaque_cursor(cursor):
        raise PolymarketValidationError("Data API v2 cursor must be a nonempty opaque string.")


def identifier_filter(values: Optional[List[str]], label: str) -> Optional[str]:
    if values is None:
        return None
    if (not isinstance(values, list) or not values or any(not isinstance(value, str) for value in values)
            or len(set(values)) > 20):
        raise PolymarketValidationError(f"Data API v2 {label} needs 1 to 20 distinct identifiers.")
    for value in values:
        valid = (bool(CONDITION_PATTERN.fullmatch(value)) if label == "condition"
                 else value.isascii() and value.isdecimal() and bool(value.lstrip("0")))
        if not valid:
            raise PolymarketValidationError(f"Data API v2 {label} contains an invalid identifier.")
    return ",".join(values)


def window_params(start: Optional[int], end: Optional[int]) -> Dict[str, int]:
    params = {}
    for label, value in (("start", start), ("end", end)):
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < 2 ** 63:
            raise PolymarketValidationError(f"Data API v2 {label} must be a nonnegative int64 timestamp.")
        params[label] = value
    if start is not None and end is not None and end > 0 and start > end:
        raise PolymarketValidationError("Data API v2 start must not exceed end.")
    return params


def finite_number(value: Any, *, nonnegative: bool = False) -> bool:
    try:
        return (isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
                and (not nonnegative or value >= 0))
    except OverflowError:
        return False


def amount_filter(filter_type: str, filter_amount: float) -> Dict[str, Any]:
    if not isinstance(filter_type, str) or filter_type.upper() not in {"CASH", "TOKENS"}:
        raise PolymarketValidationError("Data API v2 filter_type must be CASH or TOKENS.")
    if not finite_number(filter_amount, nonnegative=True):
        raise PolymarketValidationError("Data API v2 filter_amount must be finite and nonnegative.")
    return {"filter_type": filter_type.upper(), "filter_amount": filter_amount}


def reject_error_envelope(data: Dict[str, Any], endpoint: str, *, scope: str = "history completeness") -> None:
    for key in ("error", "errors", "errorMessage", "error_message"):
        value = data.get(key)
        if value is not None and value != "" and not (key == "errors" and value == []):
            raise invalid(endpoint, "returned an error envelope", scope=scope)
    failed = {"error", "failed", "failure", "fail", "unavailable", "invalid_request", "internal_error",
              "unauthorized", "forbidden", "not_found", "rate_limited", "timeout"}
    for key in ("status", "status_code", "statusCode", "code"):
        value = data.get(key)
        if value is False or (
            isinstance(value, str) and (value.strip().lower() in failed or (value.isdigit() and int(value) >= 400))
        ) or (isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 400):
            raise invalid(endpoint, "returned a failed status", scope=scope)
    for key in ("success", "ok"):
        value = data.get(key)
        if value is False or (isinstance(value, (int, float)) and value == 0) or (
            isinstance(value, str) and value.strip().lower() in {"false", "0", "failed", "failure"}
        ):
            raise invalid(endpoint, "returned a failed status", scope=scope)


def page_payload(data: Any, endpoint: str, *, cursor: Optional[str] = None,
                 scope: str = "history completeness") -> Dict[str, Any]:
    if not isinstance(data, dict) or not isinstance(data.get("data"), list):
        raise invalid(endpoint, "must return an object with an array of rows", scope=scope)
    reject_error_envelope(data, endpoint, scope=scope)
    if any(not isinstance(row, dict) for row in data["data"]):
        raise invalid(endpoint, "contains a nonobject row", scope=scope)
    pagination = data.get("pagination")
    if not isinstance(pagination, dict) or not {"has_more", "next_cursor", "limit", "offset"}.issubset(pagination):
        raise invalid(endpoint, "pagination metadata is missing", scope=scope)
    has_more, next_cursor = pagination["has_more"], pagination["next_cursor"]
    limit, offset = pagination["limit"], pagination["offset"]
    if (not isinstance(has_more, bool) or isinstance(limit, bool) or not isinstance(limit, int)
            or not 1 <= limit <= MAX_V2_PAGE_SIZE or isinstance(offset, bool)
            or not isinstance(offset, int) or offset < 0):
        raise invalid(endpoint, "pagination metadata is invalid", scope=scope)
    if (next_cursor is not None and not opaque_cursor(next_cursor)) or has_more != (next_cursor is not None):
        raise invalid(endpoint, "next_cursor and has_more disagree", scope=scope)
    if next_cursor is not None and next_cursor == cursor:
        raise invalid(endpoint, "repeated a cursor instead of advancing", scope=scope)
    if len(data["data"]) > limit:
        raise invalid(endpoint, "row count exceeds the reported page limit", scope=scope)
    return data


def identity_row(row: Dict[str, Any], endpoint: str, *, user: Optional[str],
                 allow_empty_token: bool = False, allow_empty_condition: bool = False) -> Dict[str, Any]:
    for key in ("proxy_wallet", "token_id", "condition_id", "transaction_hash"):
        if key in row and row[key] is not None and (
            not isinstance(row[key], str) or (not row[key] and not (
                (key == "token_id" and allow_empty_token) or (key == "condition_id" and allow_empty_condition)
            ))
        ):
            raise invalid(endpoint, f"row {key} is invalid")
    wallet = row.get("proxy_wallet")
    if wallet is not None and (not WALLET_PATTERN.fullmatch(wallet) or (user is not None and wallet.lower() != user.lower())):
        raise invalid(endpoint, "row wallet identity is invalid or belongs to another user")
    condition = row.get("condition_id")
    if condition is not None and not (condition == "" and allow_empty_condition) and not CONDITION_PATTERN.fullmatch(condition):
        raise invalid(endpoint, "row condition_id is invalid")
    result = dict(row)
    for source, alias in IDENTITY_ALIASES.items():
        if source in row:
            if alias in row and (type(row[alias]) is not type(row[source]) or row[alias] != row[source]):
                raise invalid(endpoint, f"row {source} and {alias} contradict each other")
            result[alias] = row[source]
    return result


def native_position_row(row: Dict[str, Any], *, user: Optional[str],
                        start: Optional[int], end: Optional[int]) -> Dict[str, Any]:
    endpoint = "Positions v2"
    result = identity_row(row, endpoint, user=user)
    if not isinstance(row.get("token_id"), str) or not row["token_id"]:
        raise invalid(endpoint, "row token identity is missing")
    if row.get("status") not in POSITION_STATUSES:
        raise invalid(endpoint, "row status is missing or invalid")
    nonnegative = {"avg_price", "current_price", "current_size", "current_value", "entry_cost_usdc",
                   "entry_fees_usdc", "total_cost_usdc", "total_size"}
    for key in nonnegative | {"realized_pnl", "unrealized_pnl", "total_pnl", "percent_pnl", "percent_realized_pnl"}:
        if key in row and row[key] is not None and not finite_number(row[key], nonnegative=key in nonnegative):
            raise invalid(endpoint, f"row {key} is not a valid JSON number")
    for key in ("first_entry_at", "last_event_at", "outcome_index"):
        value = row.get(key)
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < 2 ** 63):
            raise invalid(endpoint, f"row {key} is invalid")
    for key in ("archived", "mergeable", "negative_risk", "redeemable", "verified"):
        if key in row and row[key] is not None and not isinstance(row[key], bool):
            raise invalid(endpoint, f"row {key} is not a boolean")
    timestamp = row.get("last_event_at")
    if ((start is not None and start > 0) or (end is not None and end > 0)) and (
        timestamp is None or (start is not None and timestamp < start) or (end is not None and end > 0 and timestamp > end)
    ):
        raise invalid(endpoint, "row lies outside the requested last_event_at window")
    return result


def native_trade_row(row: Dict[str, Any], *, user: Optional[str], start: int, end: Optional[int]) -> Dict[str, Any]:
    endpoint = "Trades v2"
    result = identity_row(row, endpoint, user=user)
    timestamp = row.get("timestamp")
    if isinstance(timestamp, bool) or not isinstance(timestamp, int) or not 0 < timestamp < 2 ** 63:
        raise invalid(endpoint, "row timestamp is missing or invalid")
    if user is not None and ((start > 0 and timestamp < start) or (end is not None and end > 0 and timestamp > end)):
        raise invalid(endpoint, "row lies outside the requested window")
    if row.get("side") not in {"BUY", "SELL"}:
        raise invalid(endpoint, "row side is missing or invalid")
    for key in ("price", "size"):
        if key in row and row[key] is not None and not finite_number(row[key], nonnegative=True):
            raise invalid(endpoint, f"row {key} is not a valid nonnegative JSON number")
    return result
