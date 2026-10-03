from __future__ import annotations

import math
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional

from .activity_v2 import activity_v2_page, activity_v2_params
from .data_v2 import (
    POSITION_SORTS, POSITION_STATUSES, amount_filter, finite_number, identifier_filter, identity_row,
    invalid, native_position_row, native_trade_row, opaque_cursor, page_payload, reject_error_envelope,
    validate_page_options, validate_user, window_params,
)
from .endpoints import DATA_ENDPOINTS
from .http_client import PolymarketResponseError, PolymarketValidationError, comma_join, request_bytes, request_json
from .leaderboard import LEADERBOARD_MAX_OFFSET, normalize_leaderboard_category
from .leaderboard_validation import source_leaderboard_fields


def _get_json(endpoint_name: str, *, params: Optional[Mapping[str, Any]] = None, timeout: float = 15.0) -> Any:
    return request_json(DATA_ENDPOINTS[endpoint_name], params=params, timeout=timeout)


def _list_payload(data: Any, keys: List[str]) -> List[Dict[str, Any]]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        for key in keys:
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _history_payload(data: Any, endpoint: str) -> List[Dict[str, Any]]:
    if not isinstance(data, list) or any(not isinstance(row, dict) for row in data):
        raise PolymarketResponseError(f"{endpoint} must return an array of history objects; completeness is unknown.")
    return data


def _reject_leaderboard_error_envelope(data: Mapping[str, Any]) -> None:
    for key in ("error", "errors", "errorMessage", "error_message"):
        if key in data and data[key] is not None and data[key] != "" and not (key == "errors" and data[key] == []):
            raise PolymarketResponseError("Leaderboard returned an error envelope; board coverage is unknown.")
    failed = {"error", "failed", "failure", "fail", "unavailable", "invalid_request", "internal_error",
              "unauthorized", "forbidden", "not_found", "rate_limited", "timeout"}
    for key in ("status", "status_code", "statusCode", "code"):
        value = data.get(key)
        if value is False:
            raise PolymarketResponseError("Leaderboard returned a failed status; board coverage is unknown.")
        if isinstance(value, str):
            clean = value.strip().lower()
            if clean in failed or (clean.isdigit() and int(clean) >= 400):
                raise PolymarketResponseError("Leaderboard returned a failed status; board coverage is unknown.")
        elif isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 400:
            raise PolymarketResponseError("Leaderboard returned a failed status; board coverage is unknown.")
    for key in ("success", "ok"):
        value = data.get(key)
        if value is False or (isinstance(value, (int, float)) and value == 0) or (
            isinstance(value, str) and value.strip().lower() in {"false", "0", "failed", "failure"}
        ):
            raise PolymarketResponseError("Leaderboard returned a failed status; board coverage is unknown.")


def _leaderboard_payload(data: Any) -> List[Dict[str, Any]]:
    """Do not turn upstream errors or discarded rows into source exhaustion."""
    rows = data
    if isinstance(data, dict):
        _reject_leaderboard_error_envelope(data)
        candidates = [data[key] for key in ("data", "leaderboard", "users", "results") if key in data]
        if not candidates or any(candidate != candidates[0] for candidate in candidates[1:]):
            raise PolymarketResponseError("Leaderboard row envelope is missing or contradictory; board coverage is unknown.")
        rows = candidates[0]
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise PolymarketResponseError("Leaderboard must return an array of row objects; board coverage is unknown.")
    for row in rows:
        source_leaderboard_fields(row)
    return rows


def get_activity(
    user: str,
    *,
    limit: int = 50,
    offset: int = 0,
    types: Optional[List[str]] = None,
    side: Optional[str] = None,
    market: Optional[List[str]] = None,  # condition IDs
    start: Optional[int] = None,
    end: Optional[int] = None,
    sort_by: str = "TIMESTAMP",
    sort_direction: str = "DESC",
    timeout: float = 15.0,
) -> List[Dict[str, Any]]:
    """
    Data API: /activity
    Returns trades and activity history for a specified wallet.
    """
    clean_sort = str(sort_by or "TIMESTAMP").strip().upper()
    if clean_sort not in {"TIMESTAMP", "TOKENS", "CASH"}:
        clean_sort = "TIMESTAMP"
    clean_direction = str(sort_direction or "DESC").strip().upper()
    if clean_direction not in {"ASC", "DESC"}:
        clean_direction = "DESC"
    params: Dict[str, Any] = {
        "user": user,
        "limit": max(0, min(int(limit), 500)),
        "offset": max(0, min(int(offset), 10000)),
        "sortDirection": clean_direction,
        "sortBy": clean_sort,
    }
    if types:
        # The docs use enum list; requests will encode repeated params if list passed.
        params["type"] = types
    if side:
        params["side"] = side
    if market:
        params["market"] = market
    if start is not None:
        params["start"] = int(start)
    if end is not None:
        params["end"] = int(end)

    data = _get_json("activity", params=params, timeout=timeout)
    return _history_payload(data, "activity")


def get_activity_page_v2(
    user: str,
    *,
    limit: int = 100,
    cursor: Optional[str] = None,
    types: Optional[List[str]] = None,
    side: Optional[str] = None,
    market: Optional[List[str]] = None,
    event_id: Optional[List[str]] = None,
    start: int = 1,
    end: Optional[int] = None,
    sort_by: str = "TIMESTAMP",
    sort_direction: str = "DESC",
    exclude_deposits_withdrawals: bool = True,
    timeout: float = 15.0,
) -> Dict[str, Any]:
    """Read one strict v2 feed page, preserving raw fields and legacy aliases.

    Re-send identical filters and window bounds on every cursor page. Only
    ``pagination.next_cursor is None`` proves exhaustion; short/empty pages
    may continue. ``start=1`` requests full history; an explicit ``start=0``
    uses the upstream three-year floor. This helper never falls back to v1.
    """
    params = activity_v2_params(
        user, limit=limit, cursor=cursor, types=types, side=side, market=market, event_id=event_id,
        start=start, end=end, sort_by=sort_by, sort_direction=sort_direction,
        exclude_deposits_withdrawals=exclude_deposits_withdrawals,
    )
    data = _get_json("activity_v2", params=params, timeout=timeout)
    return activity_v2_page(
        data, user=user, start=start, end=end, sort_direction=sort_direction, cursor=cursor,
    )


def get_positions_page_v2(
    user: Optional[str] = None,
    *,
    market: Optional[List[str]] = None,
    event_id: Optional[List[str]] = None,
    status: Optional[str] = None,
    limit: int = 100,
    cursor: Optional[str] = None,
    title: Optional[str] = None,
    filter_type: str = "TOKENS",
    filter_amount: float = 0,
    include_archived: bool = False,
    sort_by: Optional[str] = None,
    sort_direction: Optional[str] = None,
    start: Optional[int] = None,
    end: Optional[int] = None,
    timeout: float = 15.0,
) -> Dict[str, Any]:
    """Read v2 lifecycle positions with native economic fields unchanged.

    The cursor adopts its status/order. Keep the user/condition anchor and
    narrowing filters on every page; title and time bounds are not cursor-bound.
    ``total_size`` is shares and is never relabeled as dollars here.
    """
    validate_user(user)
    validate_page_options(limit, cursor)
    condition = identifier_filter(market, "condition")
    events = identifier_filter(event_id, "event_id")
    if user is None and condition is None:
        raise PolymarketValidationError("Positions v2 requires a user or condition anchor.")
    if user is None and market is not None and len(set(market)) != 1:
        raise PolymarketValidationError("Positions v2 market anchor accepts exactly one condition.")
    if user is None and events is not None:
        raise PolymarketValidationError("Positions v2 event_id filtering requires a user.")
    params: Dict[str, Any] = {"user": user, "condition": condition, "event_id": events}
    params.update(amount_filter(filter_type, filter_amount))
    params.update(window_params(start, end))
    if not isinstance(include_archived, bool):
        raise PolymarketValidationError("Positions v2 include_archived must be a boolean.")
    params["include_archived"] = str(include_archived).lower()
    if title is not None:
        if not isinstance(title, str) or not title.strip() or len(title) > 200:
            raise PolymarketValidationError("Positions v2 title must contain 1 to 200 characters.")
        params["title"] = title
    for label, value, allowed in (("status", status, POSITION_STATUSES), ("sort_by", sort_by, POSITION_SORTS),
                                   ("sort_direction", sort_direction, {"ASC", "DESC"})):
        if value is not None:
            if not isinstance(value, str) or value.upper() not in allowed:
                raise PolymarketValidationError(f"Positions v2 {label} is unsupported.")
            params[label] = value.upper()
    if params.get("status") == "CLOSED" and include_archived:
        raise PolymarketValidationError("Positions v2 CLOSED cannot be combined with include_archived.")
    if params.get("status") == "REDEEMABLE_LOST" and user is None:
        raise PolymarketValidationError("Positions v2 REDEEMABLE_LOST requires a user.")
    params["limit" if cursor is None else "cursor"] = limit if cursor is None else cursor
    params = {key: value for key, value in params.items() if value is not None}
    data = page_payload(_get_json("positions_v2", params=params, timeout=timeout), "Positions v2", cursor=cursor)
    rows = [native_position_row(row, user=user, start=start, end=end) for row in data["data"]]
    return {**data, "data": rows, "pagination": dict(data["pagination"])}


def get_trades_page_v2(
    user: Optional[str] = None,
    *,
    limit: int = 100,
    cursor: Optional[str] = None,
    taker_only: bool = True,
    market: Optional[List[str]] = None,
    event_id: Optional[List[str]] = None,
    side: Optional[str] = None,
    filter_type: str = "TOKENS",
    filter_amount: float = 0.01,
    start: int = 1,
    end: Optional[int] = None,
    timeout: float = 15.0,
) -> Dict[str, Any]:
    """Read one v2 keyset trade page; bounds apply only to a user-anchored feed.

    Keep filters on cursor pages. Set taker_only=False to include wallet maker
    fills; default true serves each fill on its taker side only.
    """
    validate_user(user)
    validate_page_options(limit, cursor)
    if start is None:
        raise PolymarketValidationError("Trades v2 start must be an explicit int64 timestamp.")
    if not isinstance(taker_only, bool):
        raise PolymarketValidationError("Trades v2 taker_only must be a boolean.")
    condition = identifier_filter(market, "condition")
    events = identifier_filter(event_id, "event_id")
    if condition is not None and events is not None:
        raise PolymarketValidationError("Trades v2 condition and event_id filters are mutually exclusive.")
    params: Dict[str, Any] = {"user": user, "condition": condition, "event_id": events,
                              "taker_only": str(taker_only).lower()}
    params.update(amount_filter(filter_type, filter_amount))
    params.update(window_params(start, end))
    if side is not None:
        if not isinstance(side, str) or side.upper() not in {"BUY", "SELL"}:
            raise PolymarketValidationError("Trades v2 side must be BUY or SELL.")
        params["side"] = side.upper()
    params["limit" if cursor is None else "cursor"] = limit if cursor is None else cursor
    params = {key: value for key, value in params.items() if value is not None}
    data = page_payload(_get_json("trades_v2", params=params, timeout=timeout), "Trades v2", cursor=cursor)
    rows = [native_trade_row(row, user=user, start=start, end=end) for row in data["data"]]
    if [row["timestamp"] for row in rows] != sorted((row["timestamp"] for row in rows), reverse=True):
        raise invalid("Trades v2", "rows are not in descending timestamp order")
    return {**data, "data": rows, "pagination": dict(data["pagination"])}


def get_value_v2(user: str, *, market: Optional[List[str]] = None, timeout: float = 15.0) -> Dict[str, Any]:
    """Read native portfolio value; condition-scoped reads exclude combos."""
    validate_user(user, required=True)
    condition = identifier_filter(market, "condition")
    params = {"user": user}
    if condition is not None:
        params["condition"] = condition
    data = _get_json("value_v2", params=params, timeout=timeout)
    if not isinstance(data, dict) or not isinstance(data.get("data"), dict) or "pagination" in data:
        raise invalid("Value v2", "must return a single object in data without pagination", scope="portfolio value")
    reject_error_envelope(data, "Value v2", scope="portfolio value")
    row = data["data"]
    if row.get("proxy_wallet") is None or not finite_number(row.get("value"), nonnegative=True):
        raise invalid("Value v2", "wallet or portfolio value is missing or invalid", scope="portfolio value")
    return {**data, "data": identity_row(row, "Value v2", user=user)}


def get_positions(
    user: str,
    *,
    limit: int = 100,
    offset: int = 0,
    size_threshold: float = 1.0,
    include_archived: bool = False,
    timeout: float = 15.0,
) -> List[Dict[str, Any]]:
    if (isinstance(size_threshold, bool) or not isinstance(size_threshold, (int, float))
            or not math.isfinite(size_threshold) or size_threshold < 0):
        raise ValueError("Position size threshold must be finite and nonnegative.")
    if not isinstance(include_archived, bool):
        raise ValueError("Include archived positions must be a boolean.")
    params = {
        "user": user,
        "limit": max(0, min(int(limit), 500)),
        "offset": max(0, min(int(offset), 10000)),
        "sizeThreshold": size_threshold,
        "includeArchived": str(include_archived).lower(),
    }
    data = _get_json("positions", params=params, timeout=timeout)
    return _history_payload(data, "positions")


def get_closed_positions(
    user: str,
    *,
    limit: int = 50,
    offset: int = 0,
    sort_by: str = "TIMESTAMP",
    sort_direction: str = "ASC",
    timeout: float = 15.0,
) -> List[Dict[str, Any]]:
    clean_sort = str(sort_by or "TIMESTAMP").strip().upper()
    if clean_sort not in {"REALIZEDPNL", "TITLE", "PRICE", "AVGPRICE", "TIMESTAMP"}:
        clean_sort = "TIMESTAMP"
    clean_direction = str(sort_direction or "ASC").strip().upper()
    if clean_direction not in {"ASC", "DESC"}:
        clean_direction = "ASC"
    params = {
        "user": user,
        "limit": max(0, min(int(limit), 50)),
        "offset": max(0, min(int(offset), 100000)),
        "sortBy": clean_sort,
        "sortDirection": clean_direction,
    }
    data = _get_json("closed_positions", params=params, timeout=timeout)
    return _history_payload(data, "closed_positions")


def get_trades(
    user: str,
    *,
    limit: int = 100,
    offset: int = 0,
    timeout: float = 15.0,
) -> List[Dict[str, Any]]:
    params = {
        "user": user,
        "limit": max(0, min(int(limit), 500)),
        "offset": max(0, min(int(offset), 10000)),
    }
    data = _get_json("trades", params=params, timeout=timeout)
    return _history_payload(data, "trades")


def get_leaderboard(
    *,
    limit: int = 50,
    offset: int = 0,
    sort_by: str = "PNL",
    sort_direction: str = "DESC",
    period: str = "all",
    category: str = "OVERALL",
    timeout: float = 15.0,
) -> List[Dict[str, Any]]:
    """
    Data API: /v1/leaderboard
    Returns public trader leaderboard rows.
    """
    clean_offset = max(0, int(offset))
    if clean_offset > LEADERBOARD_MAX_OFFSET:
        raise ValueError(f"Public leaderboard offset must not exceed {LEADERBOARD_MAX_OFFSET}; this endpoint cannot enumerate all accounts.")
    clean_sort = str(sort_by or "PNL").strip().upper()
    if clean_sort not in {"PNL", "VOL"}:
        clean_sort = "PNL"
    clean_direction = str(sort_direction or "DESC").strip().upper()
    if clean_direction not in {"ASC", "DESC"}:
        clean_direction = "DESC"
    clean_period = str(period or "ALL").strip().upper()
    if clean_period not in {"DAY", "WEEK", "MONTH", "ALL"}:
        clean_period = "ALL"
    clean_category = normalize_leaderboard_category(category)
    params = {
        "limit": max(1, min(int(limit), 50)),
        "offset": clean_offset,
        "orderBy": clean_sort,
        "sortDirection": clean_direction,
        "timePeriod": clean_period,
        "category": clean_category,
    }
    return _leaderboard_payload(_get_json("leaderboard", params=params, timeout=timeout))


def get_leaderboard_v2_page(
    *,
    limit: int = 1000,
    cursor: Optional[str] = None,
    sort_by: str = "PNL",
    period: str = "all",
    category: str = "OVERALL",
    timeout: float = 15.0,
) -> Dict[str, Any]:
    """Read one ranked Data API v2 board page without changing its financial units.

    V2 ``volume`` is outcome shares, not USD. The cursor traverses a ranked
    board, not every Polymarket account, and its offset-shaped walk may skip or
    repeat rows if the board changes between pages. Existing v1 USD analytics
    must not consume these raw rows as v1 leaderboard rows.
    """
    if cursor is not None:
        if not opaque_cursor(cursor):
            raise PolymarketValidationError("Leaderboard v2 cursor must be a nonempty opaque string.")
        if limit != 1000 or sort_by != "PNL" or period != "all" or category != "OVERALL":
            raise PolymarketValidationError("Leaderboard v2 cursor binds the board; resume with cursor only.")
        params: Dict[str, Any] = {"cursor": cursor}
    else:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise PolymarketValidationError("Leaderboard v2 first-page limit must be between 1 and 1000.")
        clean_sort = str(sort_by).strip().upper()
        if clean_sort not in {"PNL", "VOLUME"}:
            raise PolymarketValidationError("Leaderboard v2 sort must be PNL or VOLUME.")
        clean_period = str(period).strip().lower()
        if clean_period not in {"day", "week", "month", "all"}:
            raise PolymarketValidationError("Leaderboard v2 period must be day, week, month, or all.")
        clean_category = str(category).strip().upper()
        if clean_category == "COMBOS":
            if clean_sort == "VOLUME":
                raise PolymarketValidationError("Leaderboard v2 combos has no volume board.")
        else:
            try:
                clean_category = normalize_leaderboard_category(category)
            except ValueError as exc:
                raise PolymarketValidationError(str(exc)) from exc
        params = {"limit": limit, "sort_by": clean_sort, "time_period": clean_period, "category": clean_category.lower()}

    data = _get_json("leaderboard_v2", params=params, timeout=timeout)
    if isinstance(data, dict):
        _reject_leaderboard_error_envelope(data)
    if not isinstance(data, dict) or not isinstance(data.get("data"), list) or any(
        not isinstance(row, dict) for row in data["data"]
    ):
        raise PolymarketResponseError("Leaderboard v2 must return an object with an array of rows; board coverage is unknown.")
    for row in data["data"]:
        source_leaderboard_fields(row, version=2)
    return page_payload(data, "Leaderboard v2", cursor=cursor, scope="board coverage")


def iter_leaderboard_v2_pages(
    *,
    limit: int = 1000,
    cursor: Optional[str] = None,
    sort_by: str = "PNL",
    period: str = "all",
    category: str = "OVERALL",
    max_pages: Optional[int] = 10000,
    timeout: float = 15.0,
) -> Iterator[Dict[str, Any]]:
    """Walk v2 board cursors; exhaustion is not a consistent all-account snapshot."""
    if max_pages is not None and (isinstance(max_pages, bool) or not isinstance(max_pages, int) or max_pages < 1):
        raise PolymarketValidationError("Leaderboard v2 max_pages must be positive or None.")
    if cursor is not None and (limit != 1000 or sort_by != "PNL" or period != "all" or category != "OVERALL"):
        raise PolymarketValidationError("Leaderboard v2 cursor binds the board; resume with cursor only.")
    seen_cursors: set[str] = {cursor} if isinstance(cursor, str) and cursor else set()
    page_count = 0
    while True:
        if max_pages is not None and page_count >= max_pages:
            raise PolymarketResponseError("Leaderboard v2 page budget reached before cursor exhaustion; board coverage is unknown.")
        if cursor is None:
            page = get_leaderboard_v2_page(
                limit=limit, sort_by=sort_by, period=period, category=category, timeout=timeout,
            )
        else:
            page = get_leaderboard_v2_page(cursor=cursor, timeout=timeout)
        page_count += 1
        next_cursor = page["pagination"]["next_cursor"]
        if next_cursor is not None:
            if next_cursor in seen_cursors:
                raise PolymarketResponseError("Leaderboard v2 repeated a cursor; board coverage is unknown.")
            seen_cursors.add(next_cursor)
        yield page
        if next_cursor is None:
            return
        cursor = next_cursor


def get_total_value(
    user: str,
    *,
    market: Optional[Iterable[str]] = None,
    timeout: float = 15.0,
) -> List[Dict[str, Any]]:
    data = _get_json("value", params={"user": user, "market": comma_join(market)}, timeout=timeout)
    return data if isinstance(data, list) else []


def get_total_markets_traded(user: str, *, timeout: float = 15.0) -> Dict[str, Any]:
    data = _get_json("traded", params={"user": user}, timeout=timeout)
    return data if isinstance(data, dict) else {}


def get_market_positions(
    market: str,
    *,
    user: Optional[str] = None,
    status: str = "ALL",
    sort_by: str = "TOTAL_PNL",
    sort_direction: str = "DESC",
    limit: int = 50,
    offset: int = 0,
    timeout: float = 15.0,
) -> List[Dict[str, Any]]:
    data = _get_json(
        "market_positions",
        params={
            "market": market,
            "user": user,
            "status": status,
            "sortBy": sort_by,
            "sortDirection": sort_direction,
            "limit": max(0, min(int(limit), 500)),
            "offset": max(0, min(int(offset), 10000)),
        },
        timeout=timeout,
    )
    return data if isinstance(data, list) else []


def get_top_holders(
    markets: Iterable[str],
    *,
    limit: int = 20,
    min_balance: int = 1,
    timeout: float = 15.0,
) -> List[Dict[str, Any]]:
    data = _get_json(
        "holders",
        params={
            "market": comma_join(markets),
            "limit": max(0, min(int(limit), 20)),
            "minBalance": max(0, min(int(min_balance), 999999)),
        },
        timeout=timeout,
    )
    return data if isinstance(data, list) else []


def get_open_interest(markets: Optional[Iterable[str]] = None, *, timeout: float = 15.0) -> List[Dict[str, Any]]:
    data = _get_json("oi", params={"market": comma_join(markets)}, timeout=timeout)
    return data if isinstance(data, list) else []


def get_live_volume(event_id: int, *, timeout: float = 15.0) -> List[Dict[str, Any]]:
    data = _get_json("live_volume", params={"id": int(event_id)}, timeout=timeout)
    return data if isinstance(data, list) else []


def download_accounting_snapshot(user: str, *, timeout: float = 30.0) -> bytes:
    return request_bytes(DATA_ENDPOINTS["accounting_snapshot"], params={"user": user}, timeout=timeout)


def get_builder_leaderboard(
    *,
    time_period: str = "DAY",
    limit: int = 25,
    offset: int = 0,
    timeout: float = 15.0,
) -> List[Dict[str, Any]]:
    data = _get_json(
        "builder_leaderboard",
        params={
            "timePeriod": str(time_period or "DAY").upper(),
            "limit": max(0, min(int(limit), 50)),
            "offset": max(0, min(int(offset), 1000)),
        },
        timeout=timeout,
    )
    return data if isinstance(data, list) else []


def get_builder_volume(*, time_period: str = "DAY", timeout: float = 15.0) -> List[Dict[str, Any]]:
    data = _get_json("builder_volume", params={"timePeriod": str(time_period or "DAY").upper()}, timeout=timeout)
    return data if isinstance(data, list) else []
