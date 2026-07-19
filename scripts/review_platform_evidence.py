from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.collect_platform_evidence import SCHEMA_VERSION, source_identity


MAX_EVIDENCE_BYTES = 1024 * 1024
REQUIRED_CHECKS = frozenset(
    {
        "python_version",
        "python_dependency_check",
        "tkinter_smoke",
        "project_verification",
    }
)
TOP_LEVEL_FIELDS = frozenset({"schema_version", "collected_at", "platform_label", "source", "host", "checks", "status"})
SOURCE_FIELDS = frozenset({"project_version", "git_commit"})
HOST_FIELDS = frozenset({"system", "release", "machine", "python_version"})
CHECK_FIELDS = frozenset({"name", "status", "returncode", "duration_seconds", "error"})
COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}")


def _as_mapping(value: object, field: str, errors: list[str]) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    errors.append(f"{field} must be an object")
    return {}


def _unexpected_fields(value: Mapping[str, Any], allowed: frozenset[str], field: str, errors: list[str]) -> None:
    unexpected = sorted(set(value) - allowed)
    if unexpected:
        errors.append(f"{field} has unexpected fields: {', '.join(unexpected)}")


def _parse_collected_at(value: object, errors: list[str]) -> None:
    if not isinstance(value, str) or not value or not value.endswith("Z"):
        errors.append("collected_at must be a non-empty UTC timestamp")
        return
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
        if parsed.tzinfo is None:
            raise ValueError
    except ValueError:
        errors.append("collected_at must be an ISO-8601 timestamp")


def review_evidence(
    payload: object,
    expected_version: str = "",
    expected_commit: str | None = None,
) -> dict[str, Any]:
    """Validate a redacted host-evidence record without changing any support claim."""
    errors: list[str] = []
    warnings: list[str] = []
    record = _as_mapping(payload, "evidence", errors)
    _unexpected_fields(record, TOP_LEVEL_FIELDS, "evidence", errors)

    if record.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must equal {SCHEMA_VERSION}")
    _parse_collected_at(record.get("collected_at"), errors)
    if not isinstance(record.get("platform_label"), str) or not record.get("platform_label").strip():
        errors.append("platform_label must be a non-empty string")
    if record.get("status") != "ok":
        errors.append("status must be ok")

    source = _as_mapping(record.get("source"), "source", errors)
    _unexpected_fields(source, SOURCE_FIELDS, "source", errors)
    project_version = source.get("project_version")
    if not isinstance(project_version, str) or not project_version:
        errors.append("source.project_version must be a non-empty string")
    elif expected_version and project_version != expected_version:
        errors.append(f"source.project_version {project_version} does not match expected {expected_version}")
    git_commit = source.get("git_commit")
    if git_commit is not None and (not isinstance(git_commit, str) or not COMMIT_PATTERN.fullmatch(git_commit)):
        errors.append("source.git_commit must be null or a lowercase 40-character Git commit")
    if expected_commit:
        if git_commit != expected_commit:
            errors.append("source.git_commit does not match the reviewer checkout")
    elif git_commit is None:
        warnings.append("evidence has no Git commit; review it only with an independently verified source archive")

    host = _as_mapping(record.get("host"), "host", errors)
    _unexpected_fields(host, HOST_FIELDS, "host", errors)
    for name in HOST_FIELDS:
        if not isinstance(host.get(name), str) or not host.get(name).strip():
            errors.append(f"host.{name} must be a non-empty string")

    checks = record.get("checks")
    if not isinstance(checks, list):
        errors.append("checks must be an array")
        checks = []
    names: list[str] = []
    for index, raw_check in enumerate(checks):
        check = _as_mapping(raw_check, f"checks[{index}]", errors)
        _unexpected_fields(check, CHECK_FIELDS, f"checks[{index}]", errors)
        name = check.get("name")
        if not isinstance(name, str) or not name:
            errors.append(f"checks[{index}].name must be a non-empty string")
            continue
        names.append(name)
        if check.get("status") != "pass":
            errors.append(f"checks[{index}] {name} did not pass")
        if check.get("returncode") != 0:
            errors.append(f"checks[{index}] {name} must have returncode 0")
        duration = check.get("duration_seconds")
        if not isinstance(duration, (int, float)) or isinstance(duration, bool) or not math.isfinite(duration) or duration < 0:
            errors.append(f"checks[{index}] {name} has an invalid duration_seconds")
        if "error" in check:
            errors.append(f"checks[{index}] {name} must not contain an error")

    duplicate_names = sorted(name for name in set(names) if names.count(name) > 1)
    if duplicate_names:
        errors.append("checks contain duplicate names: " + ", ".join(duplicate_names))
    missing_checks = sorted(REQUIRED_CHECKS - set(names))
    if missing_checks:
        errors.append("checks are missing: " + ", ".join(missing_checks))

    canonical = json.dumps(record, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {
        "ok": not errors,
        "promotion_permitted": False,
        "payload_sha256": hashlib.sha256(canonical).hexdigest(),
        "platform_label": record.get("platform_label") if isinstance(record.get("platform_label"), str) else "",
        "source": {"project_version": project_version, "git_commit": git_commit},
        "errors": errors,
        "warnings": warnings,
    }


def _load_json(path: Path) -> object:
    if path.is_symlink():
        raise ValueError("refusing symbolic-link evidence file")
    if not path.is_file():
        raise ValueError("evidence path is not a regular file")
    if path.stat().st_size > MAX_EVIDENCE_BYTES:
        raise ValueError(f"evidence file exceeds {MAX_EVIDENCE_BYTES} bytes")
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    identity = source_identity()
    parser = argparse.ArgumentParser(
        description=(
            "Review redacted platform-evidence JSON against this checkout. "
            "A passing review never promotes a platform support claim."
        )
    )
    parser.add_argument("paths", nargs="+", type=Path, help="One or more collector-generated JSON evidence files.")
    parser.add_argument("--expected-version", default=identity["project_version"], help="Required project version.")
    parser.add_argument("--expected-commit", default=identity["git_commit"] or "", help="Required Git commit when available.")
    parser.add_argument("--json", action="store_true", help="Print the structured review result.")
    args = parser.parse_args()

    entries: list[dict[str, Any]] = []
    for path in args.paths:
        try:
            entry = review_evidence(
                _load_json(path),
                expected_version=args.expected_version,
                expected_commit=args.expected_commit or None,
            )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            entry = {
                "ok": False,
                "promotion_permitted": False,
                "payload_sha256": "",
                "platform_label": "",
                "source": {},
                "errors": [str(exc)],
                "warnings": [],
            }
        entry["path"] = str(path)
        entries.append(entry)

    result = {"ok": all(entry["ok"] for entry in entries), "promotion_permitted": False, "entries": entries}
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        for entry in entries:
            prefix = "[ok]" if entry["ok"] else "[failed]"
            print(f"{prefix} {entry['path']}")
            for error in entry["errors"]:
                print(f"  error: {error}")
            for warning in entry["warnings"]:
                print(f"  warning: {warning}")
        print("promotion_permitted: false")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
