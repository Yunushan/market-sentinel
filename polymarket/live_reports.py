from __future__ import annotations

import hashlib
import json
import os
import time
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LIVE_VALIDATION_REPORTS_PATH = PROJECT_ROOT / "data" / "polymarket_live_validation_reports.json"
DEFAULT_LIVE_VALIDATION_REPORTS_MAX_ENTRIES = 100
LIVE_VALIDATION_REPORTS_VERSION = 1
POLYMARKET_LIVE_VALIDATION_REPORT_KIND = "polymarket_live_validation_report"

SENSITIVE_REPORT_KEY_FRAGMENTS = (
    "api_key",
    "apikey",
    "secret",
    "signature",
    "passphrase",
    "private",
    "password",
    "cookie",
    "session",
    "bearer",
)


def live_validation_reports_path(path: Optional[Path | str] = None) -> Path:
    if path is not None:
        return Path(path)
    configured = os.environ.get("POLYMARKET_LIVE_VALIDATION_REPORTS_PATH")
    if configured:
        return Path(configured)
    return DEFAULT_LIVE_VALIDATION_REPORTS_PATH


def load_live_validation_reports(path: Optional[Path | str] = None) -> Dict[str, Any]:
    target = live_validation_reports_path(path)
    if not target.exists():
        return _empty_store()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except Exception:
        return _empty_store()
    if not isinstance(raw, dict):
        return _empty_store()
    reports = raw.get("reports")
    if not isinstance(reports, dict):
        raw["reports"] = {}
    raw.setdefault("version", LIVE_VALIDATION_REPORTS_VERSION)
    raw.setdefault("created_at", _now())
    raw.setdefault("updated_at", raw.get("created_at", _now()))
    raw.setdefault("max_entries", DEFAULT_LIVE_VALIDATION_REPORTS_MAX_ENTRIES)
    return raw


def save_live_validation_reports(store: Mapping[str, Any], path: Optional[Path | str] = None) -> Path:
    target = live_validation_reports_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f"{target.name}.tmp")
    tmp.write_text(json.dumps(_jsonable(store), indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(target)
    return target


def redact_live_validation_report(report: Mapping[str, Any]) -> Dict[str, Any]:
    redacted = _redact_value(report)
    if not isinstance(redacted, dict):
        return {}
    return redacted


def store_live_validation_report(
    report: Mapping[str, Any],
    *,
    source: str = "gui_snapshot",
    label: str = "",
    path: Optional[Path | str] = None,
    max_entries: int = DEFAULT_LIVE_VALIDATION_REPORTS_MAX_ENTRIES,
) -> Dict[str, Any]:
    if not isinstance(report, Mapping):
        raise ValueError("Live validation report must be an object.")
    target = live_validation_reports_path(path)
    store = load_live_validation_reports(target)
    reports = store.setdefault("reports", {})
    if not isinstance(reports, dict):
        reports = {}
        store["reports"] = reports
    now = _now()
    stored_at_ns = time.time_ns()
    clean_report = redact_live_validation_report(report)
    clean_source = str(source or "gui_snapshot").strip() or "gui_snapshot"
    clean_label = str(label or "").strip()
    key = make_live_validation_report_key(
        clean_report,
        source=clean_source,
        label=clean_label,
        stored_at=now,
        stored_at_ns=stored_at_ns,
    )
    summary = live_validation_report_summary(clean_report)
    entry = {
        "key": key,
        "kind": POLYMARKET_LIVE_VALIDATION_REPORT_KIND,
        "source": clean_source,
        "label": clean_label,
        "stored_at": now,
        "stored_at_ns": stored_at_ns,
        "payload": clean_report,
        "summary": summary,
    }
    reports[key] = entry
    _prune_reports(reports, max_entries=max_entries)
    store["updated_at"] = now
    store["max_entries"] = int(max_entries)
    save_live_validation_reports(store, target)
    metadata = live_validation_report_metadata(entry, target)
    metadata.update({"stored": True, "entries": len(reports), "max_entries": int(max_entries), "summary": summary})
    return metadata


def list_live_validation_reports(
    *,
    include_payload: bool = False,
    path: Optional[Path | str] = None,
) -> Dict[str, Any]:
    target = live_validation_reports_path(path)
    store = load_live_validation_reports(target)
    rows: List[Dict[str, Any]] = []
    for entry in (store.get("reports") or {}).values():
        if not isinstance(entry, Mapping):
            continue
        row = live_validation_report_metadata(entry, target)
        payload = entry.get("payload")
        if isinstance(payload, Mapping):
            row["summary"] = live_validation_report_summary(payload)
            row["payload_bytes"] = len(json.dumps(_jsonable(payload), sort_keys=True, separators=(",", ":")).encode("utf-8"))
            if include_payload:
                row["payload"] = deepcopy(payload)
        rows.append(row)
    rows.sort(key=lambda item: (float(item.get("stored_at") or 0), float(item.get("stored_at_ns") or 0)), reverse=True)
    return {
        "source": "polymarket_live_validation_reports",
        "cache": live_validation_reports_health(target),
        "entries": rows,
        "counts": {"entries": len(rows)},
        "comparison": compare_live_validation_report_entries(rows[0], rows[1]) if len(rows) >= 2 else None,
    }


def load_live_validation_report(
    key: str,
    *,
    path: Optional[Path | str] = None,
) -> Optional[Dict[str, Any]]:
    clean_key = str(key or "").strip()
    if not clean_key:
        return None
    target = live_validation_reports_path(path)
    store = load_live_validation_reports(target)
    entry = (store.get("reports") or {}).get(clean_key)
    if not isinstance(entry, Mapping):
        return None
    payload = entry.get("payload")
    if not isinstance(payload, Mapping):
        return None
    metadata = live_validation_report_metadata(entry, target)
    metadata["summary"] = live_validation_report_summary(payload)
    metadata["payload"] = deepcopy(payload)
    return metadata


def purge_live_validation_reports(
    *,
    keys: Optional[Iterable[str]] = None,
    all_entries: bool = False,
    path: Optional[Path | str] = None,
) -> Dict[str, Any]:
    requested_keys = [str(key or "").strip() for key in (keys or []) if str(key or "").strip()]
    if not requested_keys and not all_entries:
        raise ValueError("Report purge requires a key or all=true.")

    target = live_validation_reports_path(path)
    store = load_live_validation_reports(target)
    reports = store.setdefault("reports", {})
    if not isinstance(reports, dict):
        reports = {}
        store["reports"] = reports

    deleted_keys: List[str] = []
    missing_keys: List[str] = []
    if all_entries:
        deleted_keys = [str(key) for key in reports]
        reports.clear()
    else:
        for key in requested_keys:
            if key in reports:
                reports.pop(key, None)
                deleted_keys.append(key)
            else:
                missing_keys.append(key)

    store["updated_at"] = _now()
    save_live_validation_reports(store, target)
    inventory = list_live_validation_reports(path=target)
    inventory.update(
        {
            "deleted": len(deleted_keys),
            "deleted_keys": deleted_keys,
            "missing_keys": missing_keys,
            "requested": len(requested_keys),
            "message": f"Deleted {len(deleted_keys)} live validation report(s).",
        }
    )
    return inventory


def live_validation_reports_health(path: Optional[Path | str] = None) -> Dict[str, Any]:
    target = live_validation_reports_path(path)
    store = load_live_validation_reports(target)
    reports = store.get("reports") or {}
    entries = reports if isinstance(reports, Mapping) else {}
    newest_stored_at: Optional[int] = None
    oldest_stored_at: Optional[int] = None
    for entry in entries.values():
        if not isinstance(entry, Mapping):
            continue
        stored_at = _safe_int(entry.get("stored_at"))
        if stored_at is None:
            continue
        newest_stored_at = stored_at if newest_stored_at is None else max(newest_stored_at, stored_at)
        oldest_stored_at = stored_at if oldest_stored_at is None else min(oldest_stored_at, stored_at)
    return {
        "path": str(target),
        "exists": target.exists(),
        "entries": len(entries),
        "max_entries": int(store.get("max_entries") or DEFAULT_LIVE_VALIDATION_REPORTS_MAX_ENTRIES),
        "size_bytes": target.stat().st_size if target.exists() else 0,
        "version": int(store.get("version") or LIVE_VALIDATION_REPORTS_VERSION),
        "created_at": store.get("created_at"),
        "updated_at": store.get("updated_at"),
        "newest_stored_at": newest_stored_at,
        "oldest_stored_at": oldest_stored_at,
    }


def make_live_validation_report_key(
    report: Mapping[str, Any],
    *,
    source: str,
    label: str,
    stored_at: int,
    stored_at_ns: int = 0,
) -> str:
    body = json.dumps(
        {
            "report": _jsonable(report),
            "source": str(source or ""),
            "label": str(label or ""),
            "stored_at": int(stored_at),
            "stored_at_ns": int(stored_at_ns or 0),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(f"{POLYMARKET_LIVE_VALIDATION_REPORT_KIND}:{body}".encode("utf-8")).hexdigest()[:32]


def live_validation_report_metadata(entry: Mapping[str, Any], path: Path) -> Dict[str, Any]:
    stored_at = _safe_int(entry.get("stored_at"))
    stored_at_ns = _safe_int(entry.get("stored_at_ns"))
    payload = entry.get("payload")
    payload_bytes = None
    if isinstance(payload, Mapping):
        payload_bytes = len(json.dumps(_jsonable(payload), sort_keys=True, separators=(",", ":")).encode("utf-8"))
    return {
        "key": entry.get("key"),
        "kind": entry.get("kind") or POLYMARKET_LIVE_VALIDATION_REPORT_KIND,
        "source": entry.get("source") or "unknown",
        "label": entry.get("label") or "",
        "stored_at": stored_at,
        "stored_at_ns": stored_at_ns,
        "age_seconds": None if stored_at is None else max(0, int(_now() - stored_at)),
        "path": str(path),
        "payload_bytes": payload_bytes,
    }


def live_validation_report_summary(report: Mapping[str, Any]) -> Dict[str, Any]:
    stage_gates = report.get("stage_gates") if isinstance(report.get("stage_gates"), Mapping) else {}
    readiness = report.get("clob_auth_readiness") if isinstance(report.get("clob_auth_readiness"), Mapping) else {}
    funded_check = report.get("funded_live_order_check") if isinstance(report.get("funded_live_order_check"), Mapping) else {}
    return {
        "generated_at": _safe_float(report.get("generated_at")),
        "market_id": report.get("market_id"),
        "mode": report.get("mode"),
        "selected": bool(report.get("selected")),
        "enabled": bool(report.get("enabled")),
        "public_live_checks": stage_gates.get("public_live_checks"),
        "credential_readiness": stage_gates.get("credential_readiness"),
        "credentialed_read_checks": stage_gates.get("credentialed_read_checks"),
        "bridge_address_checks": stage_gates.get("bridge_address_checks"),
        "funded_live_order_check": stage_gates.get("funded_live_order_check") or funded_check.get("status"),
        "credentialed_read_ok": bool(stage_gates.get("credentialed_read_ok")),
        "safe_to_attempt_funded_order": bool(stage_gates.get("safe_to_attempt_funded_order")),
        "requires_explicit_live_approval": bool(stage_gates.get("requires_explicit_live_approval")),
        "next_step": stage_gates.get("next_step"),
        "funded_execution_exposed": bool(report.get("funded_execution_exposed")),
        "direct_l2_read_ready": bool(readiness.get("direct_l2_read_ready")),
        "sdk_trading_ready": bool(readiness.get("sdk_trading_ready")),
    }


def compare_live_validation_report_entries(latest: Mapping[str, Any], previous: Mapping[str, Any]) -> Dict[str, Any]:
    latest_summary = latest.get("summary") if isinstance(latest.get("summary"), Mapping) else {}
    previous_summary = previous.get("summary") if isinstance(previous.get("summary"), Mapping) else {}
    fields = (
        "public_live_checks",
        "credential_readiness",
        "credentialed_read_checks",
        "bridge_address_checks",
        "funded_live_order_check",
        "credentialed_read_ok",
        "safe_to_attempt_funded_order",
        "next_step",
    )
    changes = []
    for field in fields:
        latest_value = latest_summary.get(field)
        previous_value = previous_summary.get(field)
        if latest_value != previous_value:
            changes.append({"field": field, "previous": previous_value, "latest": latest_value})
    return {
        "latest_key": latest.get("key"),
        "previous_key": previous.get("key"),
        "changed": bool(changes),
        "changes": changes,
    }


def _empty_store() -> Dict[str, Any]:
    now = _now()
    return {
        "version": LIVE_VALIDATION_REPORTS_VERSION,
        "created_at": now,
        "updated_at": now,
        "max_entries": DEFAULT_LIVE_VALIDATION_REPORTS_MAX_ENTRIES,
        "reports": {},
    }


def _now() -> int:
    return int(time.time())


def _redact_value(value: Any, key: str = "") -> Any:
    normalized = str(key or "").strip().lower()
    sensitive_key = any(fragment in normalized for fragment in SENSITIVE_REPORT_KEY_FRAGMENTS)
    if sensitive_key and not isinstance(value, (bool, int, float)) and value not in (None, ""):
        return "***"
    if isinstance(value, Mapping):
        return {str(child_key): _redact_value(child_value, str(child_key)) for child_key, child_value in value.items()}
    if isinstance(value, list):
        return [_redact_value(item, key) for item in value]
    if isinstance(value, tuple):
        return [_redact_value(item, key) for item in value]
    return _jsonable(value)


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


def _prune_reports(reports: Dict[str, Any], *, max_entries: int) -> None:
    for key, entry in list(reports.items()):
        if not isinstance(entry, Mapping):
            reports.pop(key, None)
    while len(reports) > max(1, int(max_entries)):
        oldest_key = min(
            reports,
            key=lambda item: (
                float((reports[item] or {}).get("stored_at") or 0),
                float((reports[item] or {}).get("stored_at_ns") or 0),
            ),
        )
        reports.pop(oldest_key, None)


def _safe_int(value: Any) -> Optional[int]:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
