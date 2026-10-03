"""Complete, bounded wallet reads and stable per-fill identities.

A failed or truncated read must never authorize moving a delivery cursor.
"""
from __future__ import annotations

import hashlib
import json
import time
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Mapping


class ActivityHistoryIncompleteError(RuntimeError):
    """The upstream feed could not prove coverage back to the saved cursor."""


# Allow modest provider clock skew without letting a corrupt future event
# suppress legitimate wallet activity indefinitely after cursor persistence.
ACTIVITY_CLOCK_SKEW_SECONDS = 300


def _identity_number(value: Any) -> str:
    text = str(value if value is not None else "").strip()
    try:
        number = Decimal(text)
    except InvalidOperation:
        return text
    if not number.is_finite():
        return text
    sign, digits, exponent = number.as_tuple()
    digits = list(digits)
    while len(digits) > 1 and digits[-1] == 0:
        digits.pop()
        exponent += 1
    if not any(digits):
        return "0"
    return ("-" if sign else "") + "".join(str(digit) for digit in digits) + "e" + str(exponent)


def activity_key(item: Mapping[str, Any]) -> str:
    # An upstream fill/event identity is more specific than its transaction.
    explicit = str(item.get("activityId") or item.get("activity_id") or "").strip()
    if explicit:
        return f"activity-id:{explicit}"
    tx = str(item.get("transactionHash") or item.get("transaction_hash") or "").strip()
    if tx.startswith(("0x", "0X")):
        tx = tx.lower()
    if tx:
        index = next((item.get(field) for field in ("logIndex", "log_index", "fillId", "trade_id")
                      if item.get(field) is not None and str(item.get(field)).strip()), None)
        if index is not None and str(index).strip():
            return f"tx:{tx}:fill:{str(index).strip()}"
        asset = str(item.get("asset") or item.get("contract_id") or "").strip()
        if asset:
            fields = {key: str(item.get(key) if item.get(key) is not None else "").strip().lower()
                      for key in ("timestamp", "proxyWallet", "side", "price", "size", "outcome")}
            fields["asset"] = asset
            for field in ("timestamp", "price", "size"):
                fields[field] = _identity_number(item.get(field))
            digest = hashlib.sha256(json.dumps(fields, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            return f"tx:{tx}:fill:{digest}"
        return f"tx:{tx}"
    fields = ("timestamp", "proxyWallet", "asset", "side", "price", "size", "slug", "outcome")
    return "activity:" + "|".join(str(item.get(key) or "").strip().lower() for key in fields)


def legacy_transaction_key(item: Mapping[str, Any]) -> str:
    tx = str(item.get("transactionHash") or item.get("transaction_hash") or "").strip().lower()
    if tx:
        return f"tx:{tx}"
    explicit = str(item.get("activityId") or item.get("activity_id") or "").strip().lower()
    return f"activity-id:{explicit}" if explicit else ""


def collect_polymarket_activity(
    loader: Callable[..., Any], wallet: str, *, since: int, page_size: int = 25,
    end: int | None = None, maximum_items: int = 50_000, maximum_requests: int = 200,
    timeout_seconds: float = 60.0, offset_ceiling: int = 5_000,
) -> list[dict[str, Any]]:
    """Read a fixed time window, splitting it at the provider's offset ceiling.

    Each inclusive window is completely read before any records are returned.
    Time splitting also handles histories beyond the documented offset cap;
    an overfull single second fails explicitly rather than discarding fills.
    """
    page_size = max(1, min(int(page_size), 500))
    lower = max(1, int(since))
    upper = int(time.time()) if end is None else int(end)
    if upper < lower:
        raise ActivityHistoryIncompleteError("Wallet cursor is ahead of the current clock; cursor unchanged.")
    deadline = time.monotonic() + timeout_seconds
    requests = 0
    results: dict[str, dict[str, Any]] = {}
    windows = [(lower, upper)]
    while windows:
        start, stop = windows.pop()
        offset = 0
        window: dict[str, dict[str, Any]] = {}
        previous_page: tuple[str, ...] | None = None
        preceding_timestamp: int | None = None
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or requests >= maximum_requests:
                raise ActivityHistoryIncompleteError("Wallet history read exceeded its bounded budget; cursor unchanged.")
            requests += 1
            page = loader(wallet, limit=page_size, offset=offset, types=["TRADE"],
                          start=start, end=stop, sort_by="TIMESTAMP", sort_direction="DESC",
                          timeout=min(15.0, remaining))
            if not isinstance(page, list) or len(page) > page_size:
                raise ActivityHistoryIncompleteError("Wallet activity response violated its page contract; cursor unchanged.")
            page_keys: list[str] = []
            reached_cursor = False
            previous_ts: int | None = None
            for item in page:
                if not isinstance(item, Mapping):
                    raise ActivityHistoryIncompleteError("Wallet activity page contains a malformed record; cursor unchanged.")
                raw_ts = item.get("timestamp")
                if isinstance(raw_ts, bool):
                    raise ActivityHistoryIncompleteError("Wallet activity timestamp is invalid; cursor unchanged.")
                try:
                    timestamp = int(raw_ts)
                except (ValueError, TypeError, OverflowError) as exc:
                    raise ActivityHistoryIncompleteError("Wallet activity timestamp is invalid; cursor unchanged.") from exc
                if timestamp < 0 or (previous_ts is not None and timestamp > previous_ts) or (
                    previous_ts is None and preceding_timestamp is not None and timestamp > preceding_timestamp
                ):
                    raise ActivityHistoryIncompleteError("Wallet activity page is not in documented timestamp order; cursor unchanged.")
                previous_ts = timestamp
                key = activity_key(item)
                if key in page_keys:
                    raise ActivityHistoryIncompleteError("Wallet fills have an ambiguous duplicate identity; cursor unchanged.")
                page_keys.append(key)
                if timestamp < start:
                    reached_cursor = True
                elif timestamp <= stop:
                    if key in window:
                        raise ActivityHistoryIncompleteError("Wallet activity endpoint repeated a fill across pages; cursor unchanged.")
                    window[key] = dict(item)
                else:
                    raise ActivityHistoryIncompleteError("Wallet activity escaped the fixed history window; cursor unchanged.")
            signature = tuple(page_keys)
            if page and signature == previous_page:
                raise ActivityHistoryIncompleteError("Wallet activity endpoint repeated a page; cursor unchanged.")
            previous_page = signature
            preceding_timestamp = previous_ts if previous_ts is not None else preceding_timestamp
            if len(window) + len(results) > maximum_items:
                raise ActivityHistoryIncompleteError("Wallet activity backlog exceeds capacity; cursor unchanged.")
            if len(page) < page_size or reached_cursor:
                results.update(window)
                break
            offset += page_size
            if offset > offset_ceiling:
                if start == stop:
                    raise ActivityHistoryIncompleteError("Too many wallet fills share one second for safe pagination; cursor unchanged.")
                middle = start + (stop - start) // 2
                # Discard the incomplete parent and read both disjoint children.
                windows.extend(((middle + 1, stop), (start, middle)))
                break
    return sorted(results.values(), key=lambda item: int(item["timestamp"]), reverse=True)


def collect_polymarket_activity_v2(
    loader: Callable[..., Any], wallet: str, *, since: int, page_size: int = 100,
    end: int | None = None, maximum_items: int = 50_000, maximum_requests: int = 500,
    timeout_seconds: float = 60.0,
) -> list[dict[str, Any]]:
    """Follow the v2 feed's opaque cursors to explicit EOF before delivery.

    The complete query stays identical on every page. Short or empty pages do
    not establish exhaustion; a missing/malformed/repeated cursor fails closed.
    """
    lower = max(1, int(since))
    upper = int(time.time()) if end is None else int(end)
    if upper < lower:
        raise ActivityHistoryIncompleteError("Wallet cursor is ahead of the current clock; cursor unchanged.")
    requested = max(1, min(int(page_size), 1000))
    deadline = time.monotonic() + timeout_seconds
    cursor: str | None = None
    visited: set[str] = set()
    results: list[dict[str, Any]] = []
    identities: set[str] = set()
    previous_timestamp: int | None = None
    for _request in range(maximum_requests):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ActivityHistoryIncompleteError("Wallet history read exceeded its bounded budget; cursor unchanged.")
        payload = loader(wallet, limit=requested, cursor=cursor, types=["TRADE"], start=lower, end=upper,
                         sort_by="TIMESTAMP", sort_direction="DESC", timeout=min(15.0, remaining))
        if not isinstance(payload, Mapping) or not isinstance(payload.get("data"), list) or not isinstance(payload.get("pagination"), Mapping):
            raise ActivityHistoryIncompleteError("Wallet activity v2 envelope is invalid; cursor unchanged.")
        page = payload["data"]
        pagination = payload["pagination"]
        if len(page) > requested or "next_cursor" not in pagination:
            raise ActivityHistoryIncompleteError("Wallet activity v2 pagination is incomplete; cursor unchanged.")
        next_cursor = pagination["next_cursor"]
        if next_cursor is not None and (not isinstance(next_cursor, str) or not next_cursor or len(next_cursor) > 8192):
            raise ActivityHistoryIncompleteError("Wallet activity v2 cursor is invalid; cursor unchanged.")
        if "has_more" in pagination and (type(pagination["has_more"]) is not bool or pagination["has_more"] != (next_cursor is not None)):
            raise ActivityHistoryIncompleteError("Wallet activity v2 pagination is contradictory; cursor unchanged.")
        for row in page:
            if not isinstance(row, Mapping):
                raise ActivityHistoryIncompleteError("Wallet activity v2 record is malformed; cursor unchanged.")
            raw_timestamp = row.get("timestamp")
            if type(raw_timestamp) is not int or not lower <= raw_timestamp <= upper or (
                previous_timestamp is not None and raw_timestamp > previous_timestamp
            ):
                raise ActivityHistoryIncompleteError("Wallet activity v2 record violates the fixed ordered window; cursor unchanged.")
            identity = activity_key(row)
            if identity in identities:
                raise ActivityHistoryIncompleteError("Wallet activity v2 fill identity is repeated or ambiguous; cursor unchanged.")
            previous_timestamp = raw_timestamp
            identities.add(identity)
            results.append(dict(row))
            if len(results) > maximum_items:
                raise ActivityHistoryIncompleteError("Wallet activity backlog exceeds capacity; cursor unchanged.")
        if next_cursor is None:
            return results
        if next_cursor in visited:
            raise ActivityHistoryIncompleteError("Wallet activity v2 cursor repeated; cursor unchanged.")
        visited.add(next_cursor)
        cursor = next_cursor
    raise ActivityHistoryIncompleteError("Wallet history read exceeded its bounded page budget; cursor unchanged.")


def collect_adapter_activity(loader: Callable[..., Any], wallet: str, *, since: int, page_size: int = 25,
                             timeout_seconds: float = 60.0) -> list[dict[str, Any]]:
    """Expand a recent feed until it covers the cursor, or refuse truncation."""
    requested = max(1, min(int(page_size), 5_000))
    previous: tuple[str, ...] | None = None
    deadline = time.monotonic() + timeout_seconds
    maximum_timestamp = int(time.time()) + ACTIVITY_CLOCK_SKEW_SECONDS
    while True:
        if time.monotonic() >= deadline:
            raise ActivityHistoryIncompleteError("Recent wallet feed exceeded its bounded budget; cursor unchanged.")
        page = loader(wallet, limit=requested)
        if time.monotonic() >= deadline:
            raise ActivityHistoryIncompleteError("Recent wallet feed exceeded its bounded budget; cursor unchanged.")
        if not isinstance(page, list) or len(page) > requested or any(not isinstance(row, Mapping) for row in page):
            raise ActivityHistoryIncompleteError("Wallet activity response is malformed; cursor unchanged.")
        items = [dict(row) for row in page]
        timestamps = [row.get("timestamp") for row in items]
        if any(type(ts) is not int or not 0 < ts <= maximum_timestamp for ts in timestamps):
            raise ActivityHistoryIncompleteError("Wallet activity timestamps are invalid; cursor unchanged.")
        signature = tuple(activity_key(row) for row in items)
        if len(set(signature)) != len(signature):
            raise ActivityHistoryIncompleteError("Wallet activity fill identity is repeated or ambiguous; cursor unchanged.")
        # A plain list's bounded limit is part of the adapter contract. Adapters
        # that filter or cap upstream pages report history_complete explicitly.
        complete = getattr(page, "history_complete", len(items) < requested)
        if complete or (
            getattr(page, "history_contiguous", True)
            and since > 0 and timestamps and min(timestamps) < since
        ):
            return sorted(items, key=lambda row: int(row.get("timestamp") or 0), reverse=True)
        if requested >= 5_000 or signature == previous:
            raise ActivityHistoryIncompleteError("Recent wallet feed does not cover the saved cursor; backfill/reconcile before advancing.")
        previous = signature
        requested = min(requested * 2, 5_000)


class ActivitySnapshot(list[dict[str, Any]]):
    """Normalized rows with an explicit statement about upstream exhaustion."""
    def __init__(self, items: Any, *, history_complete: bool, history_contiguous: bool = True):
        super().__init__(items)
        self.history_complete = history_complete
        self.history_contiguous = history_contiguous


def cursor_for_market(watch: Any, market_id: str) -> dict[str, Any]:
    if market_id == watch.activity_cursor_market_id:
        return {"last_seen_ts": watch.last_seen_ts, "last_seen_tx": watch.last_seen_tx,
                "seen_activity_keys": list(watch.seen_activity_keys),
                "activity_key_timestamps": dict(watch.activity_key_timestamps)}
    return dict(watch.activity_cursors.get(market_id, {"last_seen_ts": 0, "last_seen_tx": "",
                "seen_activity_keys": [], "activity_key_timestamps": {}}))


def remember_activity(watch: Any, item: Mapping[str, Any], key: str, *, market_id: str | None = None) -> None:
    """Retain every identity at the cursor timestamp, even beyond 200 fills."""
    timestamp = int(item.get("timestamp") or 0)
    selected = market_id or watch.activity_cursor_market_id
    cursor = cursor_for_market(watch, selected)
    next_timestamp = max(cursor["last_seen_ts"] or 0, timestamp)
    known = dict(cursor["activity_key_timestamps"])
    keys = [value for value in cursor["seen_activity_keys"]
            if value not in known or known[value] >= next_timestamp]
    known = {value: ts for value, ts in known.items() if ts >= next_timestamp}
    if key not in keys:
        keys.append(key)
    if len(keys) > 10_000:
        raise ActivityHistoryIncompleteError("Wallet cursor identity ledger is full; reconcile before advancing.")
    known[key] = timestamp
    if selected != watch.activity_cursor_market_id:
        watch.activity_cursors[watch.activity_cursor_market_id] = cursor_for_market(watch, watch.activity_cursor_market_id)
        watch.activity_cursor_market_id = selected
    watch.last_seen_ts = next_timestamp
    watch.last_seen_tx = str(item.get("transactionHash") or item.get("transaction_hash") or cursor["last_seen_tx"] or "")
    watch.seen_activity_keys = keys
    watch.activity_key_timestamps = known
