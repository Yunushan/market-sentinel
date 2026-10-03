"""Bounded Data API v2 reads whose EOF evidence comes from the cursor."""
from __future__ import annotations

import time
from typing import Any, Callable

from .http_client import PolymarketResponseError


class PublicHistoryRows(list[dict[str, Any]]):
    def __init__(self, rows: list[dict[str, Any]], *, history_complete: bool):
        super().__init__(rows)
        self.history_complete = history_complete


def fetch_cursor_history(
    loader: Callable[..., dict[str, Any]], *, limit: int, page_size: int = 500,
    timeout_seconds: float = 60, maximum_pages: int = 200, **query: Any,
) -> PublicHistoryRows:
    """A short page can have a next cursor; a full final page can be complete."""
    if limit <= 0:
        return PublicHistoryRows([], history_complete=False)
    rows: list[dict[str, Any]] = []
    cursor = None
    cursors: set[str] = set()
    deadline = time.monotonic() + timeout_seconds
    for _page in range(maximum_pages):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise PolymarketResponseError("Data API v2 history exceeded its bounded read budget; coverage is unknown.")
        page = loader(limit=min(page_size, limit - len(rows)), cursor=cursor,
                      timeout=min(15.0, remaining), **query)
        if not isinstance(page, dict) or not isinstance(page.get("data"), list):
            raise PolymarketResponseError("Data API v2 history envelope is malformed; coverage is unknown.")
        pagination = page.get("pagination")
        if not isinstance(pagination, dict) or "next_cursor" not in pagination:
            raise PolymarketResponseError("Data API v2 history lacks a next-cursor state; coverage is unknown.")
        next_cursor = pagination["next_cursor"]
        if next_cursor is not None and (not isinstance(next_cursor, str) or not next_cursor.strip()
                                        or next_cursor in cursors):
            raise PolymarketResponseError("Data API v2 history repeated or invalidated its cursor; coverage is unknown.")
        if any(not isinstance(row, dict) for row in page["data"]):
            raise PolymarketResponseError("Data API v2 history contains malformed rows; coverage is unknown.")
        capacity = limit - len(rows)
        rows.extend(page["data"][:capacity])
        truncated = len(page["data"]) > capacity
        if next_cursor is None:
            return PublicHistoryRows(rows, history_complete=not truncated)
        if len(rows) >= limit:
            return PublicHistoryRows(rows, history_complete=False)
        cursors.add(next_cursor)
        cursor = next_cursor
    raise PolymarketResponseError("Data API v2 history exceeded its page budget; coverage is unknown.")
