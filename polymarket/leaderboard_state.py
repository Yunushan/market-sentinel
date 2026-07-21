from __future__ import annotations

import json
import hashlib
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, Iterator, Mapping, Optional


_SORT_COLUMNS = {
    "roi_pct": "roi_pct",
    "pnl_usd": "pnl_usd",
    "volume_usd": "volume_usd",
    "mdd_pct": "mdd_pct",
    "mdd_usd": "mdd_usd",
}


class LeaderboardStateStore:
    """Durable local state for large leaderboard scans and MDD enrichment."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self._create_schema()

    def close(self) -> None:
        self.connection.close()

    def _create_schema(self) -> None:
        self.connection.executescript(
            """
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=NORMAL;
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS pages (
                page_offset INTEGER PRIMARY KEY,
                page_limit INTEGER NOT NULL,
                row_count INTEGER NOT NULL,
                fingerprint TEXT NOT NULL DEFAULT '',
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
        if "fingerprint" not in page_columns:
            self.connection.execute("ALTER TABLE pages ADD COLUMN fingerprint TEXT NOT NULL DEFAULT ''")
        self.connection.execute("CREATE INDEX IF NOT EXISTS pages_fingerprint_idx ON pages(fingerprint)")
        self.connection.commit()

    def prepare(self, signature: Mapping[str, Any], *, resume: bool) -> None:
        serialized = json.dumps(dict(signature), sort_keys=True, separators=(",", ":"))
        existing = self._metadata("signature")
        now = str(int(time.time()))
        if resume:
            if existing and existing != serialized:
                raise ValueError("State database was created with different leaderboard scan settings.")
            if not existing:
                self._set_metadata("signature", serialized)
            if not self._metadata("started_at"):
                self._set_metadata("started_at", now)
            self._set_metadata("last_updated_at", now)
            self.connection.commit()
            return

        self.connection.executescript("DELETE FROM pages; DELETE FROM rows; DELETE FROM metadata;")
        self._set_metadata("signature", serialized)
        self._set_metadata("scan_complete", "0")
        self._set_metadata("started_at", now)
        self._set_metadata("last_updated_at", now)
        self.connection.commit()

    def _metadata(self, key: str) -> str:
        row = self.connection.execute("SELECT value FROM metadata WHERE key = ?", (key,)).fetchone()
        return str(row["value"]) if row is not None else ""

    def _set_metadata(self, key: str, value: str) -> None:
        self.connection.execute(
            "INSERT INTO metadata(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    def progress(self) -> Dict[str, Any]:
        row_count = int(self.connection.execute("SELECT COUNT(*) AS count FROM rows").fetchone()["count"])
        page_count = int(self.connection.execute("SELECT COUNT(*) AS count FROM pages").fetchone()["count"])
        done = int(
            self.connection.execute("SELECT COUNT(*) AS count FROM rows WHERE mdd_status = 'done'").fetchone()["count"]
        )
        failed = int(
            self.connection.execute("SELECT COUNT(*) AS count FROM rows WHERE mdd_status = 'error'").fetchone()["count"]
        )
        last_page = self.connection.execute(
            "SELECT page_offset, page_limit, row_count FROM pages ORDER BY page_offset DESC LIMIT 1"
        ).fetchone()
        page_times = self.connection.execute("SELECT MIN(saved_at) AS started_at, MAX(saved_at) AS updated_at FROM pages").fetchone()
        next_offset = 0
        if last_page is not None:
            next_offset = int(last_page["page_offset"]) + int(last_page["row_count"])
        page_started_at = str(page_times["started_at"] or "") if page_times is not None else ""
        page_updated_at = str(page_times["updated_at"] or "") if page_times is not None else ""
        started_at = self._metadata("started_at") or page_started_at
        last_updated_at = self._metadata("last_updated_at") or page_updated_at
        return {
            "rows": row_count,
            "pages": page_count,
            "mdd_done": done,
            "mdd_errors": failed,
            "mdd_pending": max(0, row_count - done - failed),
            "next_offset": next_offset,
            "scan_complete": self._metadata("scan_complete") == "1",
            "stop_reason": self._metadata("stop_reason"),
            "started_at": started_at,
            "last_updated_at": last_updated_at,
        }

    def status(self) -> Dict[str, Any]:
        signature_text = self._metadata("signature")
        try:
            signature = json.loads(signature_text) if signature_text else {}
        except json.JSONDecodeError:
            signature = {"invalid": True}
        progress = self.progress()
        return {
            "state_db": str(self.path),
            "database_bytes": self.path.stat().st_size if self.path.exists() else 0,
            "signature": signature if isinstance(signature, Mapping) else {},
            **progress,
        }

    def record_page(self, offset: int, limit: int, rows: list[Mapping[str, Any]]) -> bool:
        clean_offset = max(0, int(offset))
        clean_limit = max(1, int(limit))
        fingerprint = self._page_fingerprint(rows)
        with self.connection:
            duplicate = self.connection.execute(
                "SELECT page_offset FROM pages WHERE fingerprint = ? AND page_offset != ? LIMIT 1",
                (fingerprint, clean_offset),
            ).fetchone()
            if rows and duplicate is not None:
                self._set_metadata("scan_complete", "1")
                self._set_metadata("stop_reason", "repeated_page")
                self._set_metadata("stop_offset", str(clean_offset))
                self._set_metadata("repeated_page_offset", str(int(duplicate["page_offset"])))
                self._set_metadata("last_updated_at", str(int(time.time())))
                return False
            self.connection.execute("DELETE FROM rows WHERE page_offset = ?", (clean_offset,))
            self.connection.execute(
                "INSERT OR REPLACE INTO pages(page_offset, page_limit, row_count, fingerprint, saved_at) VALUES (?, ?, ?, ?, ?)",
                (clean_offset, clean_limit, len(rows), fingerprint, int(time.time())),
            )
            self.connection.executemany(
                """
                INSERT INTO rows(page_offset, page_index, rank, display_name, wallet, pnl_usd, volume_usd, roi_pct, trade_count, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        clean_offset,
                        index,
                        row.get("rank"),
                        str(row.get("display_name") or "-"),
                        str(row.get("wallet") or ""),
                        row.get("pnl_usd"),
                        row.get("volume_usd"),
                        row.get("roi_pct"),
                        row.get("trade_count"),
                        json.dumps(dict(row.get("raw") or {}), separators=(",", ":"), sort_keys=True),
                    )
                    for index, row in enumerate(rows)
                ],
            )
            if len(rows) < clean_limit:
                self._set_metadata("scan_complete", "1")
                self._set_metadata("stop_reason", "end_of_results")
            else:
                self._set_metadata("stop_reason", "")
            self._set_metadata("last_updated_at", str(int(time.time())))
        return True

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
            "mdd_usd",
            "mdd_pct",
            "mdd_available",
            "mdd_method",
            "mdd_pct_basis",
            "equity_base_usd",
            "equity_base_source",
            "public_capital_basis_usd",
            "peak_value",
            "trough_value",
            "peak_timestamp",
            "trough_timestamp",
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
                    for item_key in ("status", "source", "available", "warning_count", "warnings", "limitations")
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
        column = _SORT_COLUMNS.get(sort, "roi_pct")
        if candidate and column in {"mdd_pct", "mdd_usd"}:
            return "ORDER BY rank ASC, id ASC"
        clean_direction = "ASC" if str(direction).upper() == "ASC" else "DESC"
        return f"ORDER BY ({column} IS NULL) ASC, {column} {clean_direction}, id ASC"

    @staticmethod
    def _decode_row(row: sqlite3.Row) -> Dict[str, Any]:
        result = {
            "id": int(row["id"]),
            "rank": row["rank"],
            "display_name": row["display_name"],
            "wallet": row["wallet"],
            "pnl_usd": row["pnl_usd"],
            "volume_usd": row["volume_usd"],
            "roi_pct": row["roi_pct"],
            "trade_count": row["trade_count"],
            "mdd_usd": row["mdd_usd"],
            "mdd_pct": row["mdd_pct"],
            "mdd_available": row["mdd_status"] == "done",
            "mdd_method": row["mdd_method"] or "",
            "mdd_source": row["mdd_source"] or "",
            "mdd_status": row["mdd_status"],
            "mdd_error": row["mdd_error"] or "",
            "raw": json.loads(str(row["raw_json"] or "{}")),
        }
        if row["mdd_json"]:
            try:
                result.update(json.loads(str(row["mdd_json"])))
            except json.JSONDecodeError:
                pass
        result["id"] = int(row["id"])
        return result
