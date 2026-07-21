from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional

from .live_report_schema import (
    ACCEPTED_LIVE_VALIDATION_REPORT_MODES,
    LIVE_VALIDATION_REPORT_SCHEMA_VERSION,
    LiveValidationReportSchemaError,
    compact_schema_validation,
    parse_live_validation_report_json,
    validate_live_validation_report,
)
from .live_reports import (
    DEFAULT_LIVE_VALIDATION_REPORTS_MAX_ENTRIES,
    find_live_validation_report_duplicate,
    live_validation_report_payload_hash,
    live_validation_report_summary,
    store_live_validation_report,
)


def replay_live_validation_report_paths(
    paths: Iterable[Path | str],
    *,
    import_reports: bool = False,
    store_path: Optional[Path | str] = None,
    source: str = "cli_replay",
    label_prefix: str = "",
    max_entries: int = DEFAULT_LIVE_VALIDATION_REPORTS_MAX_ENTRIES,
    fail_on_warning: bool = False,
    allow_duplicate: bool = False,
    skip_duplicates: bool = True,
) -> Dict[str, Any]:
    entries = [
        replay_live_validation_report_path(
            path,
            import_report=import_reports,
            store_path=store_path,
            source=source,
            label_prefix=label_prefix,
            max_entries=max_entries,
            allow_duplicate=allow_duplicate,
            skip_duplicates=skip_duplicates,
        )
        for path in paths
    ]
    seen_hashes: Dict[str, str] = {}
    for entry in entries:
        payload_hash = str(entry.get("payload_hash") or "").strip()
        if not payload_hash:
            continue
        seen_ref = seen_hashes.get(payload_hash)
        if seen_ref and not entry.get("duplicate"):
            entry["duplicate"] = True
            entry["duplicate_key"] = seen_ref
            entry["duplicate_source"] = "input"
        stored = entry.get("stored") if isinstance(entry.get("stored"), Mapping) else {}
        seen_hashes.setdefault(payload_hash, str(stored.get("key") or entry.get("path") or "input"))
    warning_count = sum(len((entry.get("schema_validation") or {}).get("warnings") or []) for entry in entries)
    invalid_count = sum(1 for entry in entries if not entry.get("ok"))
    import_error_count = sum(1 for entry in entries if entry.get("import_error"))
    imported_count = sum(1 for entry in entries if entry.get("imported"))
    duplicate_count = sum(1 for entry in entries if entry.get("duplicate"))
    skipped_duplicate_count = sum(1 for entry in entries if entry.get("duplicate_skipped"))
    valid_count = sum(1 for entry in entries if entry.get("schema_validation", {}).get("ok"))
    ok = invalid_count == 0 and import_error_count == 0 and (warning_count == 0 or not fail_on_warning)
    return {
        "ok": ok,
        "mode": "import" if import_reports else "dry_run",
        "funded_execution_exposed": False,
        "store_path": str(store_path) if store_path is not None else None,
        "counts": {
            "files": len(entries),
            "valid": valid_count,
            "invalid": invalid_count,
            "warnings": warning_count,
            "warning_reports": sum(1 for entry in entries if (entry.get("schema_validation") or {}).get("warnings")),
            "imported": imported_count,
            "import_errors": import_error_count,
            "duplicates": duplicate_count,
            "skipped_duplicates": skipped_duplicate_count,
        },
        "entries": entries,
    }


def replay_live_validation_report_path(
    path: Path | str,
    *,
    import_report: bool = False,
    store_path: Optional[Path | str] = None,
    source: str = "cli_replay",
    label_prefix: str = "",
    max_entries: int = DEFAULT_LIVE_VALIDATION_REPORTS_MAX_ENTRIES,
    allow_duplicate: bool = False,
    skip_duplicates: bool = True,
) -> Dict[str, Any]:
    target = Path(path)
    entry: Dict[str, Any] = {
        "path": str(target),
        "filename": target.name,
        "ok": False,
        "imported": False,
    }
    try:
        text = target.read_text(encoding="utf-8")
    except Exception as exc:
        entry["schema_validation"] = _schema_error(
            f"Could not read live validation report file: {exc}",
            report_type="file_read_error",
        )
        return entry

    try:
        report = parse_live_validation_report_json(text)
    except LiveValidationReportSchemaError as exc:
        entry["schema_validation"] = compact_schema_validation(exc.validation)
        return entry

    validation = validate_live_validation_report(report)
    entry["schema_validation"] = compact_schema_validation(validation)
    if not validation["ok"]:
        return entry

    summary = live_validation_report_summary(report)
    payload_hash = live_validation_report_payload_hash(report)
    entry["ok"] = True
    entry["payload_hash"] = payload_hash
    entry["provenance"] = _source_file_provenance(target, payload_hash)
    entry["summary"] = _compact_summary(summary)
    entry["promotion"] = summary.get("verification_promotion", {})
    duplicate = find_live_validation_report_duplicate(payload_hash, path=store_path)
    if duplicate:
        entry["duplicate"] = True
        entry["duplicate_key"] = duplicate.get("key")
        entry["duplicate_of"] = duplicate.get("key")

    if import_report:
        try:
            stored = store_live_validation_report(
                report,
                source=source,
                label=_label_for_path(target, label_prefix),
                path=store_path,
                max_entries=max_entries,
                source_file=target,
                allow_duplicate=allow_duplicate,
                skip_duplicate=skip_duplicates,
            )
        except Exception as exc:
            entry["ok"] = False
            entry["import_error"] = str(exc)
        else:
            entry["imported"] = bool(stored.get("stored"))
            entry["duplicate"] = bool(stored.get("duplicate"))
            if stored.get("duplicate_key"):
                entry["duplicate_key"] = stored.get("duplicate_key")
                entry["duplicate_of"] = stored.get("duplicate_key")
            entry["duplicate_skipped"] = bool(stored.get("duplicate") and not stored.get("stored"))
            entry["stored"] = _compact_stored_metadata(stored)
    return entry


def _compact_summary(summary: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "generated_at": summary.get("generated_at"),
        "market_id": summary.get("market_id"),
        "mode": summary.get("mode"),
        "credential_live_verified": summary.get("credential_live_verified"),
        "funded_live_verified": summary.get("funded_live_verified"),
        "can_promote_credential_live_verified": bool(summary.get("can_promote_credential_live_verified")),
        "can_promote_funded_live_verified": bool(summary.get("can_promote_funded_live_verified")),
        "credentialed_read_checks": summary.get("credentialed_read_checks"),
        "funded_live_order_check": summary.get("funded_live_order_check"),
        "next_step": summary.get("next_step"),
    }


def _compact_stored_metadata(stored: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "key": stored.get("key"),
        "source": stored.get("source"),
        "label": stored.get("label"),
        "stored_at": stored.get("stored_at"),
        "path": stored.get("path"),
        "stored": stored.get("stored"),
        "duplicate": stored.get("duplicate"),
        "duplicate_key": stored.get("duplicate_key"),
        "duplicate_of": stored.get("duplicate_of"),
        "duplicate_policy": stored.get("duplicate_policy"),
        "duplicate_import_count": stored.get("duplicate_import_count"),
        "payload_hash": stored.get("payload_hash"),
        "provenance": stored.get("provenance"),
        "schema_validation": stored.get("schema_validation"),
        "summary": stored.get("summary"),
    }


def _source_file_provenance(path: Path, payload_hash: str) -> Dict[str, Any]:
    return {
        "source_file": str(path),
        "source_file_name": path.name,
        "payload_hash": payload_hash,
        "redacted_payload_hash": payload_hash,
    }


def _schema_error(message: str, *, report_type: str) -> Dict[str, Any]:
    return {
        "schema_version": LIVE_VALIDATION_REPORT_SCHEMA_VERSION,
        "ok": False,
        "mode": None,
        "report_type": report_type,
        "errors": [message],
        "warnings": [],
        "accepted_modes": sorted(ACCEPTED_LIVE_VALIDATION_REPORT_MODES),
    }


def _label_for_path(path: Path, label_prefix: str) -> str:
    prefix = str(label_prefix or "").strip()
    if not prefix:
        return path.stem
    return f"{prefix} {path.stem}".strip()
