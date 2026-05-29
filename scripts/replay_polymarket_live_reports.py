from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from polymarket.live_report_replay import replay_live_validation_report_paths


def _print_summary(result: Mapping[str, Any]) -> None:
    counts = result.get("counts", {}) if isinstance(result.get("counts"), Mapping) else {}
    status = "[ok]" if result.get("ok") else "[failed]"
    print(f"{status} Polymarket live report replay ({result.get('mode')})")
    print("funded_execution_exposed: false")
    print(
        "files: {files} valid: {valid} invalid: {invalid} warnings: {warnings} imported: {imported} duplicates: {duplicates} skipped_duplicates: {skipped_duplicates}".format(
            files=counts.get("files", 0),
            valid=counts.get("valid", 0),
            invalid=counts.get("invalid", 0),
            warnings=counts.get("warnings", 0),
            imported=counts.get("imported", 0),
            duplicates=counts.get("duplicates", 0),
            skipped_duplicates=counts.get("skipped_duplicates", 0),
        )
    )
    if result.get("store_path"):
        print(f"store_path: {result.get('store_path')}")
    for entry in result.get("entries") or []:
        if not isinstance(entry, Mapping):
            continue
        validation = entry.get("schema_validation") if isinstance(entry.get("schema_validation"), Mapping) else {}
        summary = entry.get("summary") if isinstance(entry.get("summary"), Mapping) else {}
        prefix = "[ok]" if entry.get("ok") else "[failed]"
        print("")
        print(f"{prefix} {entry.get('path')}")
        print(
            "schema: {state} mode={mode} type={report_type} errors={errors} warnings={warnings}".format(
                state="accepted" if validation.get("ok") else "rejected",
                mode=validation.get("mode") or "missing",
                report_type=validation.get("report_type") or "unknown",
                errors=len(validation.get("errors") or []),
                warnings=len(validation.get("warnings") or []),
            )
        )
        for error in validation.get("errors") or []:
            print(f"  error: {error}")
        for warning in validation.get("warnings") or []:
            print(f"  warning: {warning}")
        if summary:
            print(f"credential_live_verified: {summary.get('credential_live_verified')}")
            print(f"funded_live_verified: {summary.get('funded_live_verified')}")
            print(f"credentialed_read_checks: {summary.get('credentialed_read_checks')}")
            print(f"funded_live_order_check: {summary.get('funded_live_order_check')}")
        if entry.get("payload_hash"):
            print(f"payload_hash: {entry.get('payload_hash')}")
        provenance = entry.get("provenance") if isinstance(entry.get("provenance"), Mapping) else {}
        if provenance.get("source_file_name"):
            print(f"source_file: {provenance.get('source_file_name')}")
        promotion = entry.get("promotion") if isinstance(entry.get("promotion"), Mapping) else {}
        for reason in promotion.get("blocked_reasons") or []:
            print(f"  promotion_blocker: {reason}")
        if entry.get("import_error"):
            print(f"  import_error: {entry.get('import_error')}")
        stored = entry.get("stored") if isinstance(entry.get("stored"), Mapping) else {}
        if stored:
            print(f"  imported_key: {stored.get('key')}")
            if stored.get("duplicate"):
                print(f"  duplicate_of: {stored.get('duplicate_key') or stored.get('duplicate_of')}")
            if stored.get("duplicate") and not stored.get("stored"):
                print("  duplicate_skipped: true")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Replay Polymarket live-validation report JSON files locally. "
            "This validates schema and promotion evidence and never performs funded actions."
        )
    )
    parser.add_argument("paths", nargs="+", type=Path, help="One or more live-validation report JSON files.")
    parser.add_argument("--json", action="store_true", help="Print structured replay JSON.")
    parser.add_argument("--import", dest="import_reports", action="store_true", help="Import valid reports into the redacted local report store.")
    parser.add_argument("--store-path", type=Path, help="Override the local report store path for --import.")
    parser.add_argument("--source", default="cli_replay", help="Source label to store on imported reports.")
    parser.add_argument("--label-prefix", default="", help="Optional label prefix for imported reports.")
    parser.add_argument("--max-entries", type=int, default=100, help="Maximum stored reports to retain when importing.")
    parser.add_argument("--fail-on-warning", action="store_true", help="Exit non-zero when any valid report has schema warnings.")
    parser.add_argument("--skip-duplicates", dest="skip_duplicates", action="store_true", default=True, help="Skip duplicate redacted payload hashes during import and record an audit event. This is the default.")
    parser.add_argument("--allow-duplicate", action="store_true", help="Store duplicate redacted payload hashes as separate audit entries.")
    args = parser.parse_args()

    result = replay_live_validation_report_paths(
        args.paths,
        import_reports=args.import_reports,
        store_path=args.store_path,
        source=args.source,
        label_prefix=args.label_prefix,
        max_entries=args.max_entries,
        fail_on_warning=args.fail_on_warning,
        allow_duplicate=args.allow_duplicate,
        skip_duplicates=args.skip_duplicates and not args.allow_duplicate,
    )
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        _print_summary(result)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
