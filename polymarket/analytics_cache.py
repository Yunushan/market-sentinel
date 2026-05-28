from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import time
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ANALYTICS_CACHE_PATH = PROJECT_ROOT / "data" / "polymarket_analytics_cache.json"
DEFAULT_ANALYTICS_CACHE_TTL_SECONDS = 24 * 60 * 60
DEFAULT_ANALYTICS_CACHE_MAX_ENTRIES = 100
ANALYTICS_CACHE_VERSION = 1
POLYMARKET_MDD_AUDIT_KIND = "polymarket_mdd_audit"


def analytics_cache_path(path: Optional[Path | str] = None) -> Path:
    if path is not None:
        return Path(path)
    configured = os.environ.get("POLYMARKET_ANALYTICS_CACHE_PATH")
    if configured:
        return Path(configured)
    return DEFAULT_ANALYTICS_CACHE_PATH


def make_cache_key(kind: str, params: Mapping[str, Any]) -> str:
    body = json.dumps(_jsonable(params), sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(f"{kind}:{body}".encode("utf-8")).hexdigest()
    return digest[:32]


def load_analytics_cache(path: Optional[Path | str] = None) -> Dict[str, Any]:
    target = analytics_cache_path(path)
    if not target.exists():
        return _empty_cache()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except Exception:
        return _empty_cache()
    if not isinstance(raw, dict):
        return _empty_cache()
    entries = raw.get("entries")
    if not isinstance(entries, dict):
        raw["entries"] = {}
    raw.setdefault("version", ANALYTICS_CACHE_VERSION)
    raw.setdefault("created_at", _now())
    raw.setdefault("updated_at", raw.get("created_at", _now()))
    return raw


def save_analytics_cache(cache: Mapping[str, Any], path: Optional[Path | str] = None) -> Path:
    target = analytics_cache_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f"{target.name}.tmp")
    tmp.write_text(json.dumps(cache, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(target)
    return target


def analytics_cache_summary(path: Optional[Path | str] = None, *, enabled: bool = False) -> Dict[str, Any]:
    target = analytics_cache_path(path)
    cache = load_analytics_cache(target)
    entries = cache.get("entries") or {}
    now = _now()
    active_entries = 0
    expired_entries = 0
    newest_stored_at: Optional[int] = None
    oldest_stored_at: Optional[int] = None
    kinds = set()
    for entry in entries.values():
        if not isinstance(entry, Mapping):
            continue
        kinds.add(str(entry.get("kind") or "unknown"))
        stored_at = _safe_int(entry.get("stored_at"))
        if stored_at is not None:
            newest_stored_at = stored_at if newest_stored_at is None else max(newest_stored_at, stored_at)
            oldest_stored_at = stored_at if oldest_stored_at is None else min(oldest_stored_at, stored_at)
        if _is_expired(entry, now=now):
            expired_entries += 1
        else:
            active_entries += 1
    size_bytes = target.stat().st_size if target.exists() else 0
    return {
        "enabled": bool(enabled),
        "path": str(target),
        "exists": target.exists(),
        "entries": len(entries),
        "active_entries": active_entries,
        "expired_entries": expired_entries,
        "max_entries": DEFAULT_ANALYTICS_CACHE_MAX_ENTRIES,
        "ttl_seconds": DEFAULT_ANALYTICS_CACHE_TTL_SECONDS,
        "size_bytes": size_bytes,
        "newest_stored_at": newest_stored_at,
        "oldest_stored_at": oldest_stored_at,
        "kinds": sorted(kinds),
    }


def analytics_cache_health(path: Optional[Path | str] = None, *, kind: Optional[str] = None) -> Dict[str, Any]:
    target = analytics_cache_path(path)
    cache = load_analytics_cache(target)
    entries = cache.get("entries") or {}
    summary = analytics_cache_summary(target, enabled=True)
    if kind:
        matching = [entry for entry in entries.values() if isinstance(entry, Mapping) and entry.get("kind") == kind]
        now = _now()
        summary.update(
            {
                "entries": len(matching),
                "active_entries": sum(1 for entry in matching if not _is_expired(entry, now=now)),
                "expired_entries": sum(1 for entry in matching if _is_expired(entry, now=now)),
            }
        )
    summary.update(
        {
            "version": cache.get("version", ANALYTICS_CACHE_VERSION),
            "created_at": cache.get("created_at"),
            "updated_at": cache.get("updated_at"),
            "kind": kind,
        }
    )
    return summary


def list_analytics_artifacts(
    *,
    kind: Optional[str] = None,
    include_expired: bool = True,
    include_payload: bool = False,
    path: Optional[Path | str] = None,
) -> Dict[str, Any]:
    target = analytics_cache_path(path)
    cache = load_analytics_cache(target)
    now = _now()
    rows: List[Dict[str, Any]] = []
    for entry in (cache.get("entries") or {}).values():
        if not isinstance(entry, Mapping):
            continue
        if kind is not None and entry.get("kind") != kind:
            continue
        expired = _is_expired(entry, now=now)
        if expired and not include_expired:
            continue
        row = _entry_metadata(entry, target, now=now)
        row["params"] = _jsonable(entry.get("params") or {})
        payload = entry.get("payload")
        if isinstance(payload, Mapping):
            row.update(_payload_summary(payload))
            row["payload_bytes"] = len(json.dumps(_jsonable(payload), sort_keys=True, separators=(",", ":")).encode("utf-8"))
            if include_payload:
                row["payload"] = deepcopy(payload)
        rows.append(row)

    rows.sort(key=lambda item: float(item.get("stored_at") or 0), reverse=True)
    return {
        "source": kind or "analytics_cache",
        "cache": analytics_cache_health(target, kind=kind),
        "entries": rows,
        "counts": {
            "entries": len(rows),
            "active_entries": sum(1 for row in rows if not row.get("expired")),
            "expired_entries": sum(1 for row in rows if row.get("expired")),
        },
    }


def purge_analytics_artifacts(
    *,
    keys: Optional[Iterable[str]] = None,
    kind: Optional[str] = None,
    expired_only: bool = False,
    all_entries: bool = False,
    path: Optional[Path | str] = None,
) -> Dict[str, Any]:
    target = analytics_cache_path(path)
    cache = load_analytics_cache(target)
    entries = cache.setdefault("entries", {})
    now = _now()
    requested_keys = [str(key or "").strip() for key in (keys or []) if str(key or "").strip()]
    if not requested_keys and not expired_only and not all_entries:
        raise ValueError("Cache purge requires a key, expired_only=true, or all=true.")

    deleted_keys: List[str] = []
    missing_keys: List[str] = []
    if requested_keys:
        for key in requested_keys:
            entry = entries.get(key)
            if not isinstance(entry, Mapping):
                missing_keys.append(key)
                continue
            if kind is not None and entry.get("kind") != kind:
                missing_keys.append(key)
                continue
            entries.pop(key, None)
            deleted_keys.append(key)

    if expired_only or all_entries:
        for key, entry in list(entries.items()):
            if not isinstance(entry, Mapping):
                entries.pop(key, None)
                deleted_keys.append(key)
                continue
            if kind is not None and entry.get("kind") != kind:
                continue
            if all_entries or _is_expired(entry, now=now):
                entries.pop(key, None)
                deleted_keys.append(key)

    cache["updated_at"] = now
    save_analytics_cache(cache, target)
    inventory = list_analytics_artifacts(kind=kind, include_expired=True, path=target)
    inventory.update(
        {
            "deleted": len(deleted_keys),
            "deleted_keys": deleted_keys,
            "missing_keys": missing_keys,
            "requested": len(requested_keys),
            "message": f"Purged {len(deleted_keys)} analytics cache artifact(s).",
        }
    )
    return inventory


def store_analytics_artifact(
    kind: str,
    params: Mapping[str, Any],
    payload: Mapping[str, Any],
    *,
    ttl_seconds: int = DEFAULT_ANALYTICS_CACHE_TTL_SECONDS,
    max_entries: int = DEFAULT_ANALYTICS_CACHE_MAX_ENTRIES,
    path: Optional[Path | str] = None,
) -> Dict[str, Any]:
    target = analytics_cache_path(path)
    cache = load_analytics_cache(target)
    entries = cache.setdefault("entries", {})
    key = make_cache_key(kind, params)
    now = _now()
    expires_at = now + max(1, int(ttl_seconds)) if int(ttl_seconds) > 0 else None
    entry = {
        "key": key,
        "kind": str(kind),
        "params": _jsonable(params),
        "payload": _jsonable(payload),
        "stored_at": now,
        "expires_at": expires_at,
    }
    entries[key] = entry
    _prune_entries(entries, max_entries=max_entries, now=now)
    cache["updated_at"] = now
    cache["max_entries"] = int(max_entries)
    save_analytics_cache(cache, target)
    metadata = _entry_metadata(entry, target)
    metadata.update({"enabled": True, "hit": False, "stored": True, "entries": len(entries), "max_entries": int(max_entries)})
    return metadata


def load_analytics_artifact(
    key: str,
    *,
    kind: Optional[str] = None,
    allow_expired: bool = False,
    path: Optional[Path | str] = None,
) -> Optional[Tuple[Dict[str, Any], Dict[str, Any]]]:
    clean_key = str(key or "").strip()
    if not clean_key:
        return None
    target = analytics_cache_path(path)
    cache = load_analytics_cache(target)
    entry = (cache.get("entries") or {}).get(clean_key)
    if not isinstance(entry, dict):
        return None
    if kind is not None and entry.get("kind") != kind:
        return None
    if not allow_expired and _is_expired(entry):
        return None
    payload = entry.get("payload")
    if not isinstance(payload, dict):
        return None
    metadata = _entry_metadata(entry, target)
    metadata.update({"enabled": True, "hit": True})
    return deepcopy(payload), metadata


def mdd_payload_to_csv(payload: Mapping[str, Any]) -> str:
    fields = [
        "section",
        "wallet",
        "mdd_method",
        "timestamp",
        "value",
        "mdd_usd",
        "mdd_pct",
        "equity_base_usd",
        "peak_value",
        "trough_value",
        "source",
        "status",
    ]
    rows = [
        {
            "section": "summary",
            "wallet": payload.get("wallet"),
            "mdd_method": payload.get("mdd_method"),
            "timestamp": "",
            "value": "",
            "mdd_usd": payload.get("mdd_usd"),
            "mdd_pct": payload.get("mdd_pct"),
            "equity_base_usd": payload.get("equity_base_usd"),
            "peak_value": payload.get("peak_value"),
            "trough_value": payload.get("trough_value"),
            "source": payload.get("mdd_pct_basis"),
            "status": "ok" if payload.get("mdd_available") else "unavailable",
        }
    ]
    for point in payload.get("points") or []:
        if not isinstance(point, Mapping):
            continue
        rows.append(
            {
                "section": "point",
                "wallet": payload.get("wallet"),
                "mdd_method": payload.get("mdd_method"),
                "timestamp": point.get("timestamp"),
                "value": point.get("value"),
                "mdd_usd": "",
                "mdd_pct": "",
                "equity_base_usd": payload.get("equity_base_usd"),
                "peak_value": "",
                "trough_value": "",
                "source": point.get("source") or point.get("kind") or "",
                "status": "",
            }
        )
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def _empty_cache() -> Dict[str, Any]:
    now = _now()
    return {"version": ANALYTICS_CACHE_VERSION, "created_at": now, "updated_at": now, "entries": {}}


def _now() -> int:
    return int(time.time())


def _jsonable(value: Any) -> Any:
    try:
        json.dumps(value)
    except TypeError:
        if isinstance(value, Mapping):
            return {str(key): _jsonable(item) for key, item in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [_jsonable(item) for item in value]
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return deepcopy(value)


def _is_expired(entry: Mapping[str, Any], *, now: Optional[int] = None) -> bool:
    expires_at = entry.get("expires_at")
    return isinstance(expires_at, (int, float)) and expires_at <= (now if now is not None else _now())


def _prune_entries(entries: Dict[str, Any], *, max_entries: int, now: int) -> None:
    for key, entry in list(entries.items()):
        if not isinstance(entry, Mapping):
            entries.pop(key, None)
            continue
        expires_at = entry.get("expires_at")
        if isinstance(expires_at, (int, float)) and expires_at <= now:
            entries.pop(key, None)
    while len(entries) > max(1, int(max_entries)):
        oldest_key = min(entries, key=lambda item: float((entries[item] or {}).get("stored_at") or 0))
        entries.pop(oldest_key, None)


def _entry_metadata(entry: Mapping[str, Any], path: Path, *, now: Optional[int] = None) -> Dict[str, Any]:
    current = now if now is not None else _now()
    stored_at = _safe_int(entry.get("stored_at"))
    expires_at = _safe_int(entry.get("expires_at"))
    ttl_remaining = None if expires_at is None else max(0, int(expires_at - current))
    age_seconds = None if stored_at is None else max(0, int(current - stored_at))
    return {
        "key": entry.get("key"),
        "kind": entry.get("kind"),
        "stored_at": stored_at,
        "expires_at": expires_at,
        "expired": _is_expired(entry, now=current),
        "ttl_remaining_seconds": ttl_remaining,
        "age_seconds": age_seconds,
        "path": str(path),
    }


def _payload_summary(payload: Mapping[str, Any]) -> Dict[str, Any]:
    summary: Dict[str, Any] = {}
    for key in (
        "wallet",
        "mdd_method",
        "mdd_available",
        "mdd_usd",
        "mdd_pct",
        "equity_base_usd",
        "peak_value",
        "trough_value",
        "peak_timestamp",
        "trough_timestamp",
        "points_total",
    ):
        if key in payload:
            summary[key] = deepcopy(payload.get(key))
    points = payload.get("points")
    if "points_total" not in summary and isinstance(points, list):
        summary["points_total"] = len(points)
    return summary


def _safe_int(value: Any) -> Optional[int]:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None
