"""Sequential, resumable v2 board walks with explicit cursor provenance."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Callable

from core.request_control import RequestCancelled, cancellation_scope, request_scope
from . import data_api
from .http_client import PolymarketResponseError
from .leaderboard import wallet_membership_fingerprint
from .leaderboard_validation import source_leaderboard_fields


def scan_leaderboard_v2(
    *, scan_limit: int | None, scan_start_offset: int = 0, scan_start_cursor: str | None = None,
    initial_complete: bool = False,
    initial_rows: list[dict[str, Any]] | None = None, initial_scanned: int | None = None,
    retain_rows: bool = True, remote_sort: str, direction: str, period: str, category: str,
    scan_concurrency: int = 1, scan_retry_attempts: int = 1, scan_retry_delay_seconds: float = 0,
    is_cancelled: Callable[[], bool], emit_progress: Callable[..., None], warnings: list[str],
    page_callback: Callable[..., Any] | None = None, cursor_page_callback: Callable[..., Any] | None = None,
    scan_summary: dict[str, Any] | None = None, maximum_pages: int = 100_000,
) -> tuple[list[dict[str, Any]], bool]:
    rows = [dict(row) for row in (initial_rows or [])] if retain_rows else []
    scanned = max(len(initial_rows or []), int(initial_scanned or 0))
    page_index = max(0, int(scan_start_offset))
    cursor = scan_start_cursor
    if initial_complete:
        if cursor is not None:
            raise ValueError("Completed leaderboard checkpoint still has a continuation cursor.")
        if scan_summary is not None:
            scan_summary.update(completion_reason="end_of_results", source_enumeration_complete=True,
                                source_api_version=2, source_max_offset=None, next_cursor=None,
                                next_page_index=page_index, scanned=scanned)
        return rows, False
    if (page_index or scanned) and cursor is None:
        raise ValueError("Saved leaderboard observations have no v2 continuation cursor; use a new state file.")
    if scan_concurrency > 1:
        warnings.append("V2 board pages follow one opaque cursor sequentially; enrichment can still run concurrently.")
    if direction.upper() == "ASC":
        warnings.append("V2 source boards rank descending; ascending order is computed within the observed rows.")
    remote_sort = "VOLUME" if remote_sort in {"VOL", "VOLUME"} else "PNL"
    fingerprints: set[str] = set()
    memberships: set[str] = set()
    observed_wallets = {source_leaderboard_fields(row, version=2)["wallet"] for row in (initial_rows or [])}
    visited_cursors = {cursor} if cursor is not None else set()
    reason = "scan_limit_reached"
    cancelled = False
    for _page_number in range(maximum_pages):
        if scan_limit is not None and scanned >= scan_limit:
            break
        if is_cancelled():
            cancelled, reason = True, "cancelled"
            break
        remaining = None if scan_limit is None else max(1, scan_limit - scanned)
        first_limit = min(50, remaining) if remaining is not None else 50
        payload = None
        for attempt in range(1, max(1, scan_retry_attempts) + 1):
            try:
                with cancellation_scope(is_cancelled):
                    payload = data_api.get_leaderboard_v2_page(cursor=cursor) if cursor is not None else data_api.get_leaderboard_v2_page(
                        limit=first_limit, sort_by=remote_sort, period=period, category=category)
                break
            except RequestCancelled:
                cancelled, reason = True, "cancelled"
                break
            except Exception as exc:
                if attempt >= max(1, scan_retry_attempts):
                    raise
                warnings.append(f"Leaderboard cursor page {page_index} failed attempt {attempt}: {exc}; retrying.")
                emit_progress("leaderboard", scanned=scanned, message=warnings[-1])
                if scan_retry_delay_seconds:
                    try:
                        with cancellation_scope(is_cancelled), request_scope(scan_retry_delay_seconds + 1) as control:
                            control.sleep(scan_retry_delay_seconds)
                    except RequestCancelled:
                        cancelled, reason = True, "cancelled"
                        break
        if cancelled:
            break
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list) or not isinstance(payload.get("pagination"), dict):
            raise PolymarketResponseError("Leaderboard v2 page is malformed; no cursor was committed.")
        page, pagination = payload["data"], payload["pagination"]
        if "next_cursor" not in pagination or type(pagination.get("has_more")) is not bool:
            raise PolymarketResponseError("Leaderboard v2 cursor metadata is incomplete.")
        next_cursor = pagination["next_cursor"]
        if (next_cursor is not None and (not isinstance(next_cursor, str) or not next_cursor or len(next_cursor) > 8192)) or pagination["has_more"] != (next_cursor is not None):
            raise PolymarketResponseError("Leaderboard v2 cursor metadata is contradictory.")
        if next_cursor is not None and next_cursor in visited_cursors:
            raise PolymarketResponseError("Leaderboard v2 repeated a cursor; no page was committed.")
        reported_limit = pagination.get("limit")
        if type(reported_limit) is not int or not 1 <= reported_limit <= 1000 or len(page) > reported_limit:
            raise PolymarketResponseError("Leaderboard v2 page exceeds its declared limit.")
        for row in page:
            source_leaderboard_fields(row, version=2)
        fingerprint = hashlib.sha256(json.dumps(page, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        membership = wallet_membership_fingerprint([{"wallet": row["user_id"]} for row in page])
        page_wallets = {source_leaderboard_fields(row, version=2)["wallet"] for row in page}
        if page and (fingerprint in fingerprints or membership in memberships or page_wallets <= observed_wallets):
            reason = "repeated_page"
            warnings.append("V2 board refreshed or repeated observed wallet membership; coverage remains incomplete.")
            emit_progress("leaderboard", scanned=scanned, message=warnings[-1])
            break
        # The callback must atomically commit these exact rows and both cursors.
        accepted = cursor_page_callback(page_index, reported_limit, page, cursor, next_cursor) if cursor_page_callback else (
            page_callback(page_index, reported_limit, page) if page_callback else True)
        if accepted is False:
            reason = "repeated_page"
            break
        fingerprints.add(fingerprint)
        if membership:
            memberships.add(membership)
        observed_wallets.update(page_wallets)
        if retain_rows:
            rows.extend(dict(row) for row in page)
        scanned += len(page)
        page_index += 1
        cursor = next_cursor
        emit_progress("leaderboard", scanned=scanned, message=f"Observed {scanned} v2 board rows across {page_index} cursor pages.")
        if next_cursor is None:
            reason = "end_of_results"
            break
        visited_cursors.add(next_cursor)
    else:
        raise PolymarketResponseError("Leaderboard v2 reached its bounded page budget before cursor exhaustion.")
    overrun = max(0, scanned - scan_limit) if scan_limit is not None else 0
    if overrun:
        warnings.append(f"Committed {overrun} rows beyond the requested scan threshold to preserve the complete provider cursor page.")
    if cancelled:
        warnings.append("Leaderboard scan cancelled by user; committed cursor pages remain resumable.")
    if scan_summary is not None:
        scan_summary.update(completion_reason=reason, source_enumeration_complete=reason == "end_of_results",
                            source_api_version=2, source_max_offset=None, next_cursor=cursor,
                            next_page_index=page_index, scanned=scanned, scan_limit_overrun=overrun,
                            scan_limit_policy="complete_provider_cursor_pages")
    return rows, cancelled
