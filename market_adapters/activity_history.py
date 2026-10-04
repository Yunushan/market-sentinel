"""Exhaustion metadata based on upstream pages before local filtering."""
from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

from core.wallet_activity import ActivitySnapshot


def raw_activity_rows(payload: Any, *keys: str) -> list[Any] | None:
    """Keep malformed/non-trade rows in the count; unknown shapes prove no EOF."""
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, Mapping):
        return None
    candidates = [payload[key] for key in keys if isinstance(payload.get(key), list)]
    if len(candidates) > 1 and any(candidate != candidates[0] for candidate in candidates[1:]):
        return None
    if candidates:
        return candidates[0]
    for key in ("data", "result"):
        if isinstance(payload.get(key), Mapping):
            rows = raw_activity_rows(payload[key], *keys)
            if rows is not None:
                return rows
    return None


def _has_following_page(payload: Any, row_count: int) -> bool:
    if not isinstance(payload, Mapping):
        return False
    for key in ("nextCursor", "next_cursor", "nextPage", "next_page", "hasMore", "has_more", "hasNextPage"):
        if payload.get(key) not in (None, "", False, 0):
            return True
    for key in ("total", "totalCount", "total_count"):
        declared = payload.get(key)
        if isinstance(declared, int) and not isinstance(declared, bool) and declared > row_count:
            return True
    for key in ("data", "result", "pagination", "meta", "pageInfo"):
        if isinstance(payload.get(key), Mapping) and _has_following_page(payload[key], row_count):
            return True
    return False


def activity_snapshot(
    items: Iterable[dict[str, Any]], payload: Any, *, effective_limit: int,
    row_keys: Sequence[str], complete_window: bool = True, history_contiguous: bool | None = None,
) -> ActivitySnapshot:
    """A short normalized result is insufficient when the raw page was full.

    Bounded recent/global feeds and configured subset filters pass
    ``complete_window=False`` even if their source arrays are short.
    """
    normalized = list(items)
    raw = raw_activity_rows(payload, *row_keys)
    complete = (
        complete_window and raw is not None and len(raw) < effective_limit
        and len(normalized) <= effective_limit and not _has_following_page(payload, len(raw))
    )
    return ActivitySnapshot(
        normalized, history_complete=complete,
        history_contiguous=complete_window if history_contiguous is None else history_contiguous,
    )
