from __future__ import annotations

import json
import hashlib
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, BinaryIO, Dict, Iterator, Mapping, Optional

from core.json_validation import loads_strict_json
from .leaderboard import LEADERBOARD_MAX_OFFSET, performance_ratio_metadata, wallet_membership_fingerprint
from .leaderboard_validation import validate_stored_leaderboard_row


_SORT_COLUMNS = {
    "roi_pct": "roi_pct",
    "pnl_usd": "pnl_usd",
    "volume_usd": "volume_usd",
    "volume_shares": "volume_shares",
    "mdd_pct": "mdd_pct",
    "mdd_usd": "mdd_usd",
}


class LeaderboardStateBusyError(RuntimeError):
    """Another scan owns the state database's writer lock."""


def leaderboard_writer_lock_path(path: Path) -> Path:
    target = path.expanduser().resolve()
    return target.with_name(f".{target.name}.writer.lock")


def _acquire_writer_lock(path: Path) -> BinaryIO:
    lock_path = leaderboard_writer_lock_path(path)
    if lock_path.is_symlink():
        raise ValueError(f"Leaderboard writer lock must not be a symbolic link: {lock_path}")
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    handle = os.fdopen(descriptor, "r+b")
    try:
        if os.fstat(handle.fileno()).st_size == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise LeaderboardStateBusyError(
                f"Cannot acquire the leaderboard writer lock for {path}. "
                "Another scan may be running; use status/export or wait for it to stop."
            ) from exc
        return handle
    except BaseException:
        handle.close()
        raise


class LeaderboardStateStore:
    """Durable local state for large leaderboard scans and MDD enrichment."""

    def __init__(self, path: Path | str, *, read_only: bool = False) -> None:
        self.path = Path(path).expanduser().resolve()
        self._writer_lock: Optional[BinaryIO] = None
        self.read_only = read_only
        try:
            if read_only:
                self.connection = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)
                self.connection.execute("PRAGMA query_only=ON")
            else:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self._writer_lock = _acquire_writer_lock(self.path)
                self.connection = sqlite3.connect(self.path)
            self.connection.row_factory = sqlite3.Row
            self._validate_durable_rows()
            self._validate_cursor_chain()
            if read_only:
                index = self.connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'index' AND name = 'rows_wallet_unique_idx'"
                ).fetchone()
                if index is None:
                    raise ValueError("Legacy leaderboard state requires migration; resume the scan once before status/export.")
            else:
                self._create_schema()
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        try:
            connection = getattr(self, "connection", None)
            if connection is not None:
                connection.close()
        finally:
            if self._writer_lock is not None:
                # The OS releases ownership even after an ungraceful process exit.
                self._writer_lock.close()
                self._writer_lock = None

    def _validate_durable_rows(self) -> None:
        exists = self.connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='rows'").fetchone()
        if exists is None:
            return
        for row in self.connection.execute("SELECT * FROM rows"):
            fields = dict(row)
            try:
                fields["raw"] = loads_strict_json(fields.pop("raw_json"))
            except (TypeError, ValueError) as exc:
                raise ValueError("Leaderboard state has invalid source JSON; start a fresh scan in a separate state file.") from exc
            validate_stored_leaderboard_row(fields)

    @contextmanager
    def snapshot(self) -> Iterator[None]:
        """Keep counts, provenance and streamed rows on one SQLite read snapshot."""
        started = not self.connection.in_transaction
        if started:
            self.connection.execute("BEGIN")
        try:
            yield
        finally:
            if started:
                self.connection.rollback()

    def _validate_cursor_chain(self) -> None:
        """Reject durable v2 provenance that cannot reproduce its continuation."""
        if self.connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='pages'").fetchone() is None:
            return
        columns = {str(row["name"]) for row in self.connection.execute("PRAGMA table_info(pages)")}
        if "source_version" not in columns:
            return  # Historical offset databases are validated as legacy observations.
        pages = self.connection.execute("SELECT * FROM pages ORDER BY page_offset")
        expected_cursor = None
        consumed: set[str] = set()
        count = 0
        seen_legacy = False
        for page in pages:
            if page["source_version"] == 1:
                seen_legacy = True
                if count:
                    raise ValueError("Leaderboard state mixes offset and cursor provenance.")
                continue
            if page["source_version"] != 2 or seen_legacy or int(page["page_offset"]) != count:
                raise ValueError("Leaderboard state has an invalid v2 cursor page sequence.")
            if count and expected_cursor is None:
                raise ValueError("Leaderboard state contains pages after cursor exhaustion.")
            for cursor in (page["source_cursor"], page["next_cursor"]):
                if cursor is not None and (not isinstance(cursor, str) or not cursor or len(cursor) > 8192 or
                                           cursor.strip() != cursor or any(ord(char) < 32 or ord(char) == 127 for char in cursor)):
                    raise ValueError("Leaderboard state has an invalid saved cursor.")
            if page["source_cursor"] != expected_cursor:
                raise ValueError("Leaderboard state has a broken saved cursor chain.")
            if expected_cursor is not None:
                consumed.add(expected_cursor)
            expected_cursor = page["next_cursor"]
            if expected_cursor is not None and expected_cursor in consumed:
                raise ValueError("Leaderboard state has a repeated saved cursor.")
            if not 1 <= int(page["page_limit"]) <= 1000 or not 0 <= int(page["row_count"]) <= int(page["page_limit"]):
                raise ValueError("Leaderboard state has invalid cursor page bounds.")
            count += 1
        if count or self._metadata("source_api_version") == "2":
            invalid_row = self.connection.execute(
                """SELECT rows.id FROM rows LEFT JOIN pages ON rows.page_offset=pages.page_offset
                   WHERE pages.page_offset IS NULL OR rows.page_index < 0 OR rows.page_index >= pages.row_count
                      OR pages.source_version != 2 LIMIT 1"""
            ).fetchone()
            excessive_retention = self.connection.execute(
                """SELECT pages.page_offset FROM pages LEFT JOIN rows ON rows.page_offset=pages.page_offset
                   GROUP BY pages.page_offset HAVING COUNT(rows.id) > pages.row_count LIMIT 1"""
            ).fetchone()
            if invalid_row is not None or excessive_retention is not None:
                raise ValueError("Leaderboard retained observations contradict their source cursor pages.")
            try:
                cursor = loads_strict_json(self._metadata("v2_next_cursor") or "null")
                ordinal = int(self._metadata("v2_next_page") or "0")
            except (TypeError, ValueError) as exc:
                raise ValueError("Leaderboard state has invalid cursor progress metadata.") from exc
            if seen_legacy or self._metadata("source_api_version") != "2" or cursor != expected_cursor or ordinal != count:
                raise ValueError("Leaderboard cursor progress metadata contradicts its saved pages.")
            reason = self._metadata("stop_reason")
            complete = self._metadata("scan_complete")
            if complete not in {"0", "1"} or (complete == "1" and reason not in {"end_of_results", "repeated_page"}) or (complete == "0" and reason):
                raise ValueError("Leaderboard cursor completion metadata has no consistent terminal reason.")
            if count and ((expected_cursor is None) != (reason == "end_of_results")):
                raise ValueError("Leaderboard cursor exhaustion metadata contradicts its saved pages.")
            if count and expected_cursor is None and complete != "1":
                raise ValueError("Leaderboard terminal cursor is not marked complete.")

    def _create_schema(self) -> None:
        # A successful page/MDD commit must request a storage sync, not wait
        # until a later WAL checkpoint. Verify settings before any migration.
        for setting, value, expected in (
            ("journal_mode", "WAL", "wal"),
            ("synchronous", "FULL", 2),
            ("fullfsync", "ON", 1),
        ):
            self.connection.execute(f"PRAGMA {setting}={value}")
            row = self.connection.execute(f"PRAGMA {setting}").fetchone()
            if row is None or row[0] != expected:
                raise RuntimeError(f"Leaderboard state requires SQLite {setting}={value}; refusing to write with weaker durability.")
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS pages (
                page_offset INTEGER PRIMARY KEY,
                page_limit INTEGER NOT NULL,
                row_count INTEGER NOT NULL,
                fingerprint TEXT NOT NULL DEFAULT '',
                wallet_fingerprint TEXT NOT NULL DEFAULT '',
                saved_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS rows (
                id INTEGER PRIMARY KEY,
                page_offset INTEGER NOT NULL,
                page_index INTEGER NOT NULL,
                rank INTEGER,
                display_name TEXT NOT NULL,
                wallet TEXT NOT NULL,
                pnl_usd REAL,
                volume_usd REAL,
                roi_pct REAL,
                trade_count INTEGER,
                raw_json TEXT NOT NULL,
                mdd_status TEXT NOT NULL DEFAULT 'pending',
                mdd_attempts INTEGER NOT NULL DEFAULT 0,
                mdd_usd REAL,
                mdd_pct REAL,
                mdd_method TEXT,
                mdd_source TEXT,
                mdd_json TEXT,
                mdd_error TEXT,
                UNIQUE(page_offset, page_index)
            );
            CREATE INDEX IF NOT EXISTS rows_roi_idx ON rows(roi_pct);
            CREATE INDEX IF NOT EXISTS rows_pnl_idx ON rows(pnl_usd);
            CREATE INDEX IF NOT EXISTS rows_volume_idx ON rows(volume_usd);
            CREATE INDEX IF NOT EXISTS rows_mdd_pct_idx ON rows(mdd_pct);
            CREATE INDEX IF NOT EXISTS rows_mdd_status_idx ON rows(mdd_status);
            """
        )
        page_columns = {
            str(row["name"])
            for row in self.connection.execute("PRAGMA table_info(pages)")
        }
        row_columns = {str(row["name"]) for row in self.connection.execute("PRAGMA table_info(rows)")}
        if "volume_shares" not in row_columns:
            self.connection.execute("ALTER TABLE rows ADD COLUMN volume_shares REAL")
        self.connection.execute("CREATE INDEX IF NOT EXISTS rows_volume_shares_idx ON rows(volume_shares)")
        if "fingerprint" not in page_columns:
            self.connection.execute("ALTER TABLE pages ADD COLUMN fingerprint TEXT NOT NULL DEFAULT ''")
        for column, declaration in (("source_version", "INTEGER NOT NULL DEFAULT 1"),
                                    ("source_cursor", "TEXT"), ("next_cursor", "TEXT")):
            if column not in page_columns:
                self.connection.execute(f"ALTER TABLE pages ADD COLUMN {column} {declaration}")  # noqa: S608 -- fixed schema allowlist
        migrate_memberships = "wallet_fingerprint" not in page_columns
        if migrate_memberships:
            self.connection.execute("ALTER TABLE pages ADD COLUMN wallet_fingerprint TEXT NOT NULL DEFAULT ''")
        self.connection.execute("CREATE INDEX IF NOT EXISTS pages_fingerprint_idx ON pages(fingerprint)")
        self.connection.execute("CREATE INDEX IF NOT EXISTS pages_wallet_fingerprint_idx ON pages(wallet_fingerprint)")
        wallet_index = self.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'index' AND name = 'rows_wallet_unique_idx'"
        ).fetchone()
        if wallet_index is None:
            # Migrate old scans atomically, keeping the earliest observed row per wallet.
            with self.connection:
                self.connection.execute("UPDATE rows SET wallet = LOWER(TRIM(wallet))")
                self.connection.execute(
                    """
                    DELETE FROM rows WHERE id IN (
                        SELECT id FROM (
                            SELECT id, ROW_NUMBER() OVER (
                                PARTITION BY wallet ORDER BY page_offset, page_index, id
                            ) AS occurrence FROM rows WHERE wallet != ''
                        ) WHERE occurrence > 1
                    )
                    """
                )
                self.connection.execute(
                    "CREATE UNIQUE INDEX rows_wallet_unique_idx ON rows(wallet) WHERE wallet != ''"
                )
        if migrate_memberships:
            # Only reconstruct complete retained pages within the documented source window.
            for page in self.connection.execute("SELECT page_offset, row_count FROM pages WHERE page_offset <= ?", (LEADERBOARD_MAX_OFFSET,)):
                rows = [dict(row) for row in self.connection.execute("SELECT wallet FROM rows WHERE page_offset = ?", (page["page_offset"],))]
                if len(rows) == page["row_count"]:
                    self.connection.execute("UPDATE pages SET wallet_fingerprint = ? WHERE page_offset = ?", (
                        wallet_membership_fingerprint(rows), page["page_offset"],
                    ))
        self.connection.commit()

    def prepare(self, signature: Mapping[str, Any], *, resume: bool) -> None:
        serialized = json.dumps(dict(signature), sort_keys=True, separators=(",", ":"))
        existing = self._metadata("signature")
        now = str(int(time.time()))
        with self.connection:
            if resume:
                if existing and existing != serialized:
                    raise ValueError("State database was created with different leaderboard scan settings.")
                if not existing:
                    self._set_metadata("signature", serialized)
                if not self._metadata("started_at"):
                    self._set_metadata("started_at", now)
                self._set_metadata("last_updated_at", now)
            else:
                self.connection.execute("DELETE FROM pages")
                self.connection.execute("DELETE FROM rows")
                self.connection.execute("DELETE FROM metadata")
                self._set_metadata("signature", serialized)
                self._set_metadata("scan_complete", "0")
                self._set_metadata("started_at", now)
                self._set_metadata("last_updated_at", now)
            if signature.get("source_api_version") == 2 and not self._metadata("source_api_version"):
                self._set_metadata("source_api_version", "2")
                self._set_metadata("v2_next_cursor", "null")
                self._set_metadata("v2_next_page", "0")

    def prepare_mdd(self, signature: Mapping[str, Any]) -> int:
        """Invalidate enrichment, not fetched pages, when calculation inputs change."""
        serialized = json.dumps(dict(signature), sort_keys=True, separators=(",", ":"), allow_nan=False)
        if self._metadata("mdd_signature") == serialized:
            return 0
        with self.connection:
            invalidated = self.connection.execute(
                """
                UPDATE rows SET mdd_status = 'pending', mdd_attempts = 0,
                    mdd_usd = NULL, mdd_pct = NULL, mdd_method = NULL,
                    mdd_source = NULL, mdd_json = NULL, mdd_error = NULL
                WHERE mdd_status != 'pending' OR mdd_json IS NOT NULL
                """
            ).rowcount
            self._set_metadata("mdd_signature", serialized)
            self._set_metadata("last_updated_at", str(int(time.time())))
        return invalidated

    def _metadata(self, key: str) -> str:
        row = self.connection.execute("SELECT value FROM metadata WHERE key = ?", (key,)).fetchone()
        return str(row["value"]) if row is not None else ""

    def _set_metadata(self, key: str, value: str) -> None:
        self.connection.execute(
            "INSERT INTO metadata(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    def progress(self) -> Dict[str, Any]:
        with self.snapshot():
            return self._snapshot_progress()

    def _snapshot_progress(self) -> Dict[str, Any]:
        row_count = int(self.connection.execute("SELECT COUNT(*) AS count FROM rows").fetchone()["count"])
        wallet_count = int(self.connection.execute("SELECT COUNT(*) FROM rows WHERE wallet != ''").fetchone()[0])
        page_stats = self.connection.execute(
            "SELECT COUNT(*) AS count, COALESCE(SUM(row_count), 0) AS scanned, "
            "MIN(saved_at) AS started_at, MAX(saved_at) AS updated_at FROM pages"
        ).fetchone()
        page_count = int(page_stats["count"])
        scanned_count = int(page_stats["scanned"])
        done = int(
            self.connection.execute("SELECT COUNT(*) AS count FROM rows WHERE mdd_status = 'done'").fetchone()["count"]
        )
        available = int(self.connection.execute(
            "SELECT COUNT(*) FROM rows WHERE mdd_status = 'done' AND (mdd_usd IS NOT NULL OR mdd_pct IS NOT NULL)"
        ).fetchone()[0])
        failed = int(
            self.connection.execute("SELECT COUNT(*) AS count FROM rows WHERE mdd_status = 'error'").fetchone()["count"]
        )
        last_page = self.connection.execute(
            "SELECT page_offset, page_limit, row_count FROM pages ORDER BY page_offset DESC LIMIT 1"
        ).fetchone()
        next_offset = 0
        if last_page is not None:
            next_offset = int(last_page["page_offset"]) + int(last_page["row_count"])
        if self._metadata("source_api_version") == "2":
            next_offset = int(self._metadata("v2_next_page") or "0")
        page_started_at = str(page_stats["started_at"] or "")
        page_updated_at = str(page_stats["updated_at"] or "")
        started_at = self._metadata("started_at") or page_started_at
        last_updated_at = self._metadata("last_updated_at") or page_updated_at
        return {
            "rows": row_count,
            "scanned": scanned_count,
            "unique_wallets": wallet_count,
            "duplicate_rows": max(0, scanned_count - row_count),
            "pages": page_count,
            "mdd_done": done,
            "mdd_available": available,
            "mdd_unavailable": done - available,
            "mdd_errors": failed,
            "mdd_pending": max(0, row_count - done - failed),
            "next_offset": next_offset,
            "source_api_version": int(self._metadata("source_api_version") or "1"),
            "next_cursor": json.loads(self._metadata("v2_next_cursor")) if self._metadata("v2_next_cursor") else None,
            "scan_complete": self._metadata("scan_complete") == "1",
            "stop_reason": self._metadata("stop_reason"),
            "started_at": started_at,
            "last_updated_at": last_updated_at,
        }

    def status(self) -> Dict[str, Any]:
        with self.snapshot():
            return self._snapshot_status()

    def _snapshot_status(self) -> Dict[str, Any]:
        signature_text = self._metadata("signature")
        try:
            signature = json.loads(signature_text) if signature_text else {}
        except json.JSONDecodeError:
            signature = {"invalid": True}
        progress = self.progress()
        mdd_signature_text = self._metadata("mdd_signature")
        mdd_signature = json.loads(mdd_signature_text) if mdd_signature_text else None
        return {
            "state_db": str(self.path),
            "database_bytes": self.path.stat().st_size if self.path.exists() else 0,
            "signature": signature if isinstance(signature, Mapping) else {},
            "mdd_signature": mdd_signature,
            **progress,
        }

    def record_page(self, offset: int, limit: int, rows: list[Mapping[str, Any]], *,
                    source_version: int = 1, source_cursor: Optional[str] = None,
                    next_cursor: Optional[str] = None) -> bool:
        if source_version == 2 and (type(offset) is not int or offset < 0 or type(limit) is not int or
                                    not 1 <= limit <= 1000 or len(rows) > limit):
            raise ValueError("Leaderboard v2 page bounds are invalid.")
        for row in rows:
            validate_stored_leaderboard_row(row)
        clean_offset = max(0, int(offset))
        clean_limit = max(1, int(limit))
        fingerprint = self._page_fingerprint(rows)
        wallet_fingerprint = wallet_membership_fingerprint(rows)
        with self.connection:
            if source_version not in (1, 2):
                raise ValueError("Unsupported leaderboard source version.")
            saved_page = self.connection.execute(
                "SELECT fingerprint, page_limit, source_version, source_cursor, next_cursor FROM pages WHERE page_offset = ?", (clean_offset,)
            ).fetchone()
            if saved_page is not None:
                if saved_page["fingerprint"] != fingerprint or (source_version == 2 and (
                    saved_page["page_limit"] != clean_limit or saved_page["source_version"] != 2 or
                    saved_page["source_cursor"] != source_cursor or saved_page["next_cursor"] != next_cursor
                )):
                    raise ValueError("Cannot overwrite an already saved leaderboard page with different observations or cursors.")
                return True
            if source_version == 2:
                for cursor in (source_cursor, next_cursor):
                    if cursor is not None and (not isinstance(cursor, str) or not cursor or len(cursor) > 8192 or
                                               cursor.strip() != cursor or any(ord(char) < 32 or ord(char) == 127 for char in cursor)):
                        raise ValueError("Leaderboard cursor is invalid.")
                if clean_offset != int(self._metadata("v2_next_page") or "0"):
                    raise ValueError("Leaderboard page ordinal is not contiguous with the durable cursor.")
                if any(not isinstance(row.get("raw"), Mapping) or "user_id" not in row["raw"] for row in rows):
                    raise ValueError("V2 cursor pages require native v2 source rows.")
                existing_version = self._metadata("source_api_version")
                if existing_version == "1":
                    raise ValueError("Cannot append v2 cursor pages to legacy offset observations; use a new state file.")
                previous = self._metadata("v2_next_cursor")
                if previous and json.loads(previous) != source_cursor:
                    raise ValueError("Leaderboard page does not continue the durable cursor.")
                if not previous and (source_cursor is not None or clean_offset != 0):
                    raise ValueError("Leaderboard v2 cannot resume from a synthesized offset.")
                if previous and self._metadata("scan_complete") == "1":
                    raise ValueError("Leaderboard cursor already reached a terminal state.")
                if source_cursor is not None and self.connection.execute(
                    "SELECT 1 FROM pages WHERE source_version=2 AND source_cursor=?", (source_cursor,)
                ).fetchone():
                    raise ValueError("Leaderboard cursor was already consumed.")
                if next_cursor is not None and (next_cursor == source_cursor or self.connection.execute(
                    "SELECT 1 FROM pages WHERE source_version=2 AND source_cursor=?", (next_cursor,)
                ).fetchone()):
                    raise ValueError("Leaderboard next cursor repeated.")
            elif self._metadata("source_api_version") == "2":
                raise ValueError("Cannot append legacy offset observations to a v2 cursor scan.")
            elif any(isinstance(row.get("raw"), Mapping) and "user_id" in row["raw"] for row in rows):
                raise ValueError("Native v2 rows require cursor provenance rather than a legacy page version.")
            duplicate = self.connection.execute(
                "SELECT page_offset FROM pages WHERE (fingerprint = ? OR (? != '' AND wallet_fingerprint = ?)) AND page_offset != ? LIMIT 1",
                (fingerprint, wallet_fingerprint, wallet_fingerprint, clean_offset),
            ).fetchone()
            if rows and duplicate is not None:
                self._set_metadata("scan_complete", "1")
                self._set_metadata("stop_reason", "repeated_page")
                self._set_metadata("stop_offset", str(clean_offset))
                self._set_metadata("repeated_page_offset", str(int(duplicate["page_offset"])))
                self._set_metadata("last_updated_at", str(int(time.time())))
                return False
            self.connection.execute(
                "INSERT INTO pages(page_offset, page_limit, row_count, fingerprint, wallet_fingerprint, saved_at, source_version, source_cursor, next_cursor) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (clean_offset, clean_limit, len(rows), fingerprint, wallet_fingerprint, int(time.time()), source_version, source_cursor, next_cursor),
            )
            self.connection.executemany(
                """
                INSERT INTO rows(page_offset, page_index, rank, display_name, wallet, pnl_usd, volume_usd, volume_shares, roi_pct, trade_count, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(wallet) WHERE wallet != '' DO NOTHING
                """,
                [
                    (
                        clean_offset,
                        index,
                        row.get("rank"),
                        str(row.get("display_name") or "-"),
                        str(row.get("wallet") or "").strip().lower(),
                        row.get("pnl_usd"),
                        row.get("volume_usd"),
                        row.get("volume_shares"),
                        row.get("roi_pct"),
                        row.get("trade_count"),
                        json.dumps(dict(row.get("raw") or {}), separators=(",", ":"), sort_keys=True, allow_nan=False),
                    )
                    for index, row in enumerate(rows)
                ],
            )
            exhausted = next_cursor is None if source_version == 2 else len(rows) < clean_limit
            if exhausted:
                self._set_metadata("scan_complete", "1")
                self._set_metadata("stop_reason", "end_of_results")
            else:
                self._set_metadata("scan_complete", "0")
                self._set_metadata("stop_reason", "")
            self._set_metadata("source_api_version", str(source_version))
            if source_version == 2:
                self._set_metadata("v2_next_cursor", json.dumps(next_cursor))
                self._set_metadata("v2_next_page", str(clean_offset + 1))
            self._set_metadata("last_updated_at", str(int(time.time())))
        return True

    def stop_at_upstream_limit(self) -> None:
        with self.connection:
            self._set_metadata("scan_complete", "1")
            self._set_metadata("stop_reason", "upstream_offset_limit")
            self._set_metadata("last_updated_at", str(int(time.time())))

    @staticmethod
    def _page_fingerprint(rows: list[Mapping[str, Any]]) -> str:
        canonical = json.dumps(list(rows), default=str, separators=(",", ":"), sort_keys=True)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def candidate_count(self, filters: Mapping[str, Optional[float]]) -> int:
        where, values = self._where(filters, require_mdd=False)
        # `where` is built only from the fixed column tuple in `_where`; values remain bound parameters.
        row = self.connection.execute(f"SELECT COUNT(*) AS count FROM rows {where}", values).fetchone()  # noqa: S608
        return int(row["count"])

    def iter_mdd_candidates(
        self,
        filters: Mapping[str, Optional[float]],
        *,
        sort: str,
        direction: str,
        limit: Optional[int],
    ) -> Iterator[Dict[str, Any]]:
        where, values = self._where(filters, require_mdd=False)
        order = self._order_clause(sort, direction, candidate=True)
        # `where` and `order` are both generated from fixed allowlists; user input is never interpolated.
        query = f"SELECT * FROM rows {where} {order}"  # noqa: S608
        if limit is not None:
            query += " LIMIT ?"
            values.append(int(limit))
        for row in self.connection.execute(query, values):
            yield self._decode_row(row)

    def set_mdd(self, row_id: int, payload: Optional[Mapping[str, Any]], error: Optional[BaseException] = None) -> None:
        if payload is None:
            with self.connection:
                self.connection.execute(
                    "UPDATE rows SET mdd_status = 'error', mdd_attempts = mdd_attempts + 1, mdd_error = ? WHERE id = ?",
                    (str(error or "MDD unavailable")[:512], int(row_id)),
                )
                self._set_metadata("last_updated_at", str(int(time.time())))
            return

        summary = self._mdd_summary(payload)
        if summary.get("mdd_available") is False:
            summary.update(mdd_usd=None, mdd_pct=None)
        mark_replay = summary.get("mark_replay") or {}
        accounting = summary.get("accounting_snapshot") or {}
        source = str(
            (accounting.get("status") if isinstance(accounting, Mapping) else "")
            or (mark_replay.get("status") if isinstance(mark_replay, Mapping) else "")
            or summary.get("mdd_method")
            or ""
        )
        with self.connection:
            self.connection.execute(
                """
                UPDATE rows
                SET mdd_status = 'done', mdd_attempts = mdd_attempts + 1, mdd_usd = ?, mdd_pct = ?,
                    mdd_method = ?, mdd_source = ?, mdd_json = ?, mdd_error = NULL
                WHERE id = ?
                """,
                (
                    summary.get("mdd_usd"),
                    summary.get("mdd_pct"),
                    summary.get("mdd_method"),
                    source,
                    json.dumps(summary, separators=(",", ":"), sort_keys=True),
                    int(row_id),
                ),
            )
            self._set_metadata("last_updated_at", str(int(time.time())))

    @staticmethod
    def _mdd_summary(payload: Mapping[str, Any]) -> Dict[str, Any]:
        """Keep result/provenance fields required for resume and export, not full point history."""
        keys = (
            "version",
            "calculation_version",
            "mdd_usd",
            "mdd_pct",
            "mdd_available",
            "mdd_method",
            "mdd_pct_basis",
            "mdd_scope",
            "mdd_account_equity_verified",
            "mdd_history_status",
            "mdd_history_coverage",
            "mdd_history_capped_sources",
            "mdd_history_excluded_sources",
            "mdd_source_quality",
            "mdd_unavailable_reasons",
            "equity_base_usd",
            "source_economics_currency",
            "quote_currency",
            "equity_base_currency",
            "mdd_currency_status",
            "mdd_percentage_available",
            "equity_base_source",
            "public_capital_basis_usd",
            "position_capital_basis",
            "peak_value",
            "trough_value",
            "peak_timestamp",
            "trough_timestamp",
            "pct_drawdown_usd",
            "pct_peak_value",
            "pct_trough_value",
            "pct_peak_timestamp",
            "pct_trough_timestamp",
            "drawdown_baseline",
            "points_total",
            "data_counts",
            "assumptions",
            "limitations",
        )
        summary = {key: payload.get(key) for key in keys if key in payload}
        for key in ("mark_replay", "accounting_snapshot"):
            value = payload.get(key)
            if isinstance(value, Mapping):
                summary[key] = {
                    item_key: value.get(item_key)
                    for item_key in (
                        "status", "source", "available", "warning_count", "warnings", "limitations",
                        "incomplete_reasons", "trade_events_replayed", "trades_without_timestamp",
                        "trades_without_size_or_price", "negative_inventory_events", "timeline_truncated",
                        "display_points_truncated", "complete",
                        "unsupported_activity", "current_snapshot_reconciliation",
                    )
                    if item_key in value
                }
        return summary

    def result_count(self, filters: Mapping[str, Optional[float]], *, require_mdd: bool) -> int:
        where, values = self._where(filters, require_mdd=require_mdd)
        # `where` is built only from the fixed column tuple in `_where`; values remain bound parameters.
        row = self.connection.execute(f"SELECT COUNT(*) AS count FROM rows {where}", values).fetchone()  # noqa: S608
        return int(row["count"])

    def iter_results(
        self,
        filters: Mapping[str, Optional[float]],
        *,
        require_mdd: bool,
        sort: str,
        direction: str,
        limit: Optional[int],
    ) -> Iterator[Dict[str, Any]]:
        where, values = self._where(filters, require_mdd=require_mdd)
        # `_where` and `_order_clause` produce fixed SQL fragments; user values are passed separately.
        query = f"SELECT * FROM rows {where} {self._order_clause(sort, direction)}"  # noqa: S608
        if limit is not None:
            query += " LIMIT ?"
            values.append(int(limit))
        for row in self.connection.execute(query, values):
            yield self._decode_row(row)

    def _where(self, filters: Mapping[str, Optional[float]], *, require_mdd: bool) -> tuple[str, list[Any]]:
        clauses: list[str] = []
        values: list[Any] = []
        for column, minimum_key, maximum_key in (
            ("pnl_usd", "min_pnl_usd", "max_pnl_usd"),
            ("volume_usd", "min_volume_usd", "max_volume_usd"),
            ("volume_shares", "min_volume_shares", "max_volume_shares"),
            ("roi_pct", "min_roi_pct", "max_roi_pct"),
        ):
            minimum = filters.get(minimum_key)
            maximum = filters.get(maximum_key)
            if minimum is not None:
                clauses.append(f"{column} >= ?")
                values.append(minimum)
            if maximum is not None:
                clauses.append(f"{column} <= ?")
                values.append(maximum)
        if require_mdd:
            clauses.append("mdd_status = 'done'")
            clauses.append("(mdd_usd IS NOT NULL OR mdd_pct IS NOT NULL)")
            for column, minimum_key, maximum_key in (
                ("mdd_usd", "min_mdd_usd", "max_mdd_usd"),
                ("mdd_pct", "min_mdd_pct", "max_mdd_pct"),
            ):
                minimum = filters.get(minimum_key)
                maximum = filters.get(maximum_key)
                if minimum is not None:
                    clauses.append(f"{column} >= ?")
                    values.append(minimum)
                if maximum is not None:
                    clauses.append(f"{column} <= ?")
                    values.append(maximum)
        return ("WHERE " + " AND ".join(clauses)) if clauses else "", values

    @staticmethod
    def _order_clause(sort: str, direction: str, *, candidate: bool = False) -> str:
        column = _SORT_COLUMNS.get(sort, "pnl_usd")
        if candidate and column in {"mdd_pct", "mdd_usd"}:
            return "ORDER BY rank ASC, id ASC"
        clean_direction = "ASC" if str(direction).upper() == "ASC" else "DESC"
        return f"ORDER BY ({column} IS NULL) ASC, {column} {clean_direction}, id ASC"

    @staticmethod
    def _decode_row(row: sqlite3.Row) -> Dict[str, Any]:
        raw = json.loads(str(row["raw_json"] or "{}"))
        version = 2 if "user_id" in raw else 1
        result = {
            "id": int(row["id"]),
            "rank": row["rank"],
            "display_name": row["display_name"],
            "wallet": row["wallet"],
            "pnl_usd": row["pnl_usd"],
            "volume_usd": row["volume_usd"],
            "volume_shares": row["volume_shares"] if version == 2 and "volume_shares" in row.keys() else None,
            "source_api_version": version,
            "quote_currency": "USDC" if version == 2 else "USD",
            "volume_unit": "shares" if version == 2 else "USD",
            "roi_pct": row["roi_pct"],
            **(performance_ratio_metadata(row["roi_pct"]) if version == 1 else {
                "pnl_volume_pct": None, "pnl_volume_pct_basis": "unavailable", "roi_pct_basis": "unavailable",
            }),
            "trade_count": row["trade_count"],
            "mdd_usd": row["mdd_usd"],
            "mdd_pct": row["mdd_pct"],
            "mdd_available": row["mdd_status"] == "done",
            "mdd_method": row["mdd_method"] or "",
            "mdd_source": row["mdd_source"] or "",
            "mdd_status": row["mdd_status"],
            "mdd_error": row["mdd_error"] or "",
            "raw": raw,
        }
        if row["mdd_json"]:
            try:
                summary = json.loads(str(row["mdd_json"]))
                result["mdd_quote_currency"] = summary.pop("quote_currency", None)
                result["mdd_source_economics_currency"] = summary.pop("source_economics_currency", None)
                result["mdd_equity_base_currency"] = summary.pop("equity_base_currency", None)
                result.update(summary)
            except json.JSONDecodeError:
                pass
        result["id"] = int(row["id"])
        return result
