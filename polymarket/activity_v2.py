"""Reviewed Data API v2 activity wire contract, without cursor fabrication.

Sources: https://docs.polymarket.com/api-reference/feeds/list-account-activity
and https://docs.polymarket.com/quickstart/reference/data-api-v2 (checked 2026-10-03).
"""
from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Optional

from .data_v2 import identity_row, page_payload
from .http_client import PolymarketResponseError, PolymarketValidationError


MAX_ACTIVITY_PAGE_SIZE = 1000
_WALLET = re.compile(r"0x[0-9a-fA-F]{40}\Z")
_CONDITION = re.compile(r"0x[0-9a-fA-F]{64}\Z")
_ACTIVITY_TYPE = re.compile(r"[A-Z][A-Z0-9_]*\Z")
_ROW_ALIASES = {
    "condition_id": "conditionId",
    "event_slug": "eventSlug",
    "outcome_index": "outcomeIndex",
    "profile_image": "profileImage",
    "profile_image_optimized": "profileImageOptimized",
    "proxy_wallet": "proxyWallet",
    "token_id": "asset",
    "transaction_hash": "transactionHash",
    "usdc_size": "usdcSize",
    "is_combo": "isCombo",
    "market_id": "marketId",
}


def _opaque_cursor(value: Any) -> bool:
    return (isinstance(value, str) and bool(value) and value.strip() == value
            and not any(ord(char) < 32 or ord(char) == 127 for char in value))


def activity_v2_params(
    user: str, *, limit: int, cursor: Optional[str], types: Optional[List[str]],
    side: Optional[str], market: Optional[List[str]], event_id: Optional[List[str]],
    start: int, end: Optional[int], sort_by: str, sort_direction: str,
    exclude_deposits_withdrawals: bool,
) -> Dict[str, Any]:
    if not isinstance(user, str) or not _WALLET.fullmatch(user):
        raise PolymarketValidationError("Activity v2 user must be a 0x-prefixed wallet address.")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_ACTIVITY_PAGE_SIZE:
        raise PolymarketValidationError("Activity v2 limit must be between 1 and 1000.")
    if cursor is not None and not _opaque_cursor(cursor):
        raise PolymarketValidationError("Activity v2 cursor must be a nonempty opaque string.")
    if not isinstance(sort_by, str) or sort_by.upper() != "TIMESTAMP":
        raise PolymarketValidationError("Activity v2 supports only TIMESTAMP sorting.")
    if not isinstance(sort_direction, str) or sort_direction.upper() not in {"ASC", "DESC"}:
        raise PolymarketValidationError("Activity v2 sort direction must be ASC or DESC.")
    if start is None:
        raise PolymarketValidationError("Activity v2 start must be an explicit nonnegative int64 timestamp.")
    for label, value in (("start", start), ("end", end)):
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < 2 ** 63):
            raise PolymarketValidationError(f"Activity v2 {label} must be a nonnegative int64 timestamp.")
    if end is not None and end > 0 and start > end:
        raise PolymarketValidationError("Activity v2 start must not exceed end.")
    if not isinstance(exclude_deposits_withdrawals, bool):
        raise PolymarketValidationError("Activity v2 exclude_deposits_withdrawals must be a boolean.")
    params: Dict[str, Any] = {
        "user": user, "start": start, "sort_by": "TIMESTAMP", "sort_direction": sort_direction.upper(),
        "exclude_deposits_withdrawals": str(exclude_deposits_withdrawals).lower(),
    }
    # Feeds require identical filters on every page. Unlike boards, the cursor
    # carries the seek anchor only; its page size overrides an added limit.
    params["limit" if cursor is None else "cursor"] = limit if cursor is None else cursor
    if end is not None:
        params["end"] = end
    if types is not None:
        if (not isinstance(types, list) or not types
                or any(not isinstance(value, str) or not _ACTIVITY_TYPE.fullmatch(value) for value in types)):
            raise PolymarketValidationError("Activity v2 types must be a nonempty list of activity names.")
        params["type"] = ",".join(types)
    if side is not None:
        if not isinstance(side, str) or side.upper() not in {"BUY", "SELL"}:
            raise PolymarketValidationError("Activity v2 side must be BUY or SELL.")
        params["side"] = side.upper()
    if market is not None and event_id is not None:
        raise PolymarketValidationError("Activity v2 condition and event_id filters are mutually exclusive.")
    for label, values in (("condition", market), ("event_id", event_id)):
        if values is None:
            continue
        if (not isinstance(values, list) or not values or any(not isinstance(value, str) for value in values)
                or len(set(values)) > 20):
            raise PolymarketValidationError(f"Activity v2 {label} needs 1 to 20 distinct identifiers.")
        for value in values:
            valid = isinstance(value, str) and (
                bool(_CONDITION.fullmatch(value)) if label == "condition"
                else value.isascii() and value.isdecimal() and bool(value.lstrip("0"))
            )
            if not valid:
                raise PolymarketValidationError(f"Activity v2 {label} contains an invalid identifier.")
        params[label] = ",".join(values)
    return params


def _invalid(message: str) -> PolymarketResponseError:
    return PolymarketResponseError(f"Activity v2 {message}; history completeness is unknown.")


def _canonical_row(row: Dict[str, Any], *, user: str, start: int, end: Optional[int]) -> Dict[str, Any]:
    # Condition payouts can omit a token; account rewards can omit both token
    # and condition. Preserve native empty strings without inventing identity.
    nontrade = row.get("type") != "TRADE"
    result = identity_row(row, "Activity v2", user=user,
                          allow_empty_token=nontrade, allow_empty_condition=nontrade)
    timestamp = row.get("timestamp")
    if isinstance(timestamp, bool) or not isinstance(timestamp, int) or not 0 < timestamp < 2 ** 63:
        raise _invalid("row timestamp is missing or invalid")
    if (start > 0 and timestamp < start) or (end is not None and end > 0 and timestamp > end):
        raise _invalid("row lies outside the requested window")
    if not isinstance(row.get("type"), str) or not _ACTIVITY_TYPE.fullmatch(row["type"]):
        raise _invalid("row activity type is missing or invalid")
    for key in ("price", "size", "usdc_size"):
        value = row.get(key)
        if value is None:
            continue  # Documented unavailable sentinel; never manufacture zero.
        try:
            finite = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
        except OverflowError:
            finite = False
        if not finite or value < 0:
            raise _invalid(f"row {key} is not a finite nonnegative JSON number")
    if row.get("proxy_wallet") is not None and row["proxy_wallet"].lower() != user.lower():
        raise _invalid("row belongs to another user")
    if "outcome_index" in row and row["outcome_index"] is not None and (
        isinstance(row["outcome_index"], bool) or not isinstance(row["outcome_index"], int) or row["outcome_index"] < 0
    ):
        raise _invalid("row outcome_index is invalid")
    if "is_combo" in row and row["is_combo"] is not None and not isinstance(row["is_combo"], bool):
        raise _invalid("row is_combo is invalid")
    for source, alias in _ROW_ALIASES.items():
        if source in row:
            if alias in row and (type(row[alias]) is not type(row[source]) or row[alias] != row[source]):
                raise _invalid(f"row {source} and {alias} contradict each other")
            result[alias] = row[source]
    return result


def activity_v2_page(data: Any, *, user: str, start: int, end: Optional[int],
                     sort_direction: str, cursor: Optional[str]) -> Dict[str, Any]:
    data = page_payload(data, "Activity v2", cursor=cursor)
    pagination = data["pagination"]
    rows = []
    for row in data["data"]:
        if not isinstance(row, dict):
            raise _invalid("contains a nonobject row")
        rows.append(_canonical_row(row, user=user, start=start, end=end))
    timestamps = [row["timestamp"] for row in rows]
    if timestamps != sorted(timestamps, reverse=sort_direction.upper() == "DESC"):
        raise _invalid("rows contradict the requested timestamp order")
    return {**data, "data": rows, "pagination": dict(pagination)}
