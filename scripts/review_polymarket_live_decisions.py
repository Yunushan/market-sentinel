from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from polymarket.live_reports import (
    list_live_validation_report_decisions,
    live_validation_coverage_promotion_proposal,
    live_validation_coverage_promotion_proposal_markdown,
    live_validation_report_decisions_markdown,
    live_validation_report_review_bundle,
    record_live_validation_report_decision,
)


def _print_text_result(result: Mapping[str, Any]) -> None:
    stored = result.get("stored") if isinstance(result.get("stored"), Mapping) else result
    print(
        "[ok] {decision} decision for {target_tier} on report {report_key}".format(
            decision=stored.get("decision"),
            target_tier=stored.get("target_tier"),
            report_key=stored.get("report_key"),
        )
    )
    print(f"decision_key: {stored.get('key')}")
    print("static_coverage_mutated: false")
    print("funded_execution_exposed: false")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Record or export Polymarket live-validation promotion decisions. "
            "Decisions require a report key, redacted payload hash, target tier, decision, reviewer note, "
            "and current review-bundle hash."
        )
    )
    parser.add_argument("--report-key", default="", help="Stored live-validation report key.")
    parser.add_argument("--payload-hash", default="", help="Expected redacted payload hash from the review bundle.")
    parser.add_argument("--target-tier", default="", help="Target tier such as credential_live_verified or funded_live_verified.")
    parser.add_argument("--decision", choices=["accepted", "rejected"], default=None, help="Operator decision.")
    parser.add_argument("--reviewer-note", default="", help="Required operator rationale.")
    parser.add_argument("--review-bundle-hash", default="", help="Expected current review bundle hash.")
    parser.add_argument("--reviewer", default="operator", help="Reviewer name or handle.")
    parser.add_argument("--report-store-path", type=Path, help="Override the stored report path.")
    parser.add_argument("--decision-path", type=Path, help="Override the decision ledger path.")
    parser.add_argument("--print-review-input", action="store_true", help="Print payload hash and review bundle hash for a report.")
    parser.add_argument("--export-ledger", action="store_true", help="Export the decision ledger instead of recording a decision.")
    parser.add_argument("--export-proposal", action="store_true", help="Export a no-automerge coverage promotion proposal.")
    parser.add_argument("--markdown", action="store_true", help="Print Markdown for --export-ledger or --export-proposal.")
    parser.add_argument("--json", action="store_true", help="Print JSON output.")
    args = parser.parse_args()

    if args.print_review_input:
        bundle = live_validation_report_review_bundle(args.report_key, path=args.report_store_path)
        if bundle is None:
            raise SystemExit("Unknown live validation report.")
        payload = {
            "report_key": args.report_key,
            "payload_hash": (bundle.get("report") or {}).get("payload_hash"),
            "review_bundle_hash": bundle.get("review_bundle_hash"),
            "target_tiers": ["public_live_verified", "credential_live_verified", "funded_live_verified"],
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    if args.export_ledger:
        ledger = list_live_validation_report_decisions(report_key=args.report_key, path=args.decision_path)
        if args.markdown:
            print(live_validation_report_decisions_markdown(ledger))
        else:
            print(json.dumps(ledger, indent=2, sort_keys=True))
        return 0

    if args.export_proposal:
        proposal = live_validation_coverage_promotion_proposal(
            report_store_path=args.report_store_path,
            decision_path=args.decision_path,
            target_tier=args.target_tier,
        )
        if args.markdown:
            print(live_validation_coverage_promotion_proposal_markdown(proposal))
        else:
            print(json.dumps(proposal, indent=2, sort_keys=True))
        return 0

    result = record_live_validation_report_decision(
        report_key=args.report_key,
        payload_hash=args.payload_hash,
        target_tier=args.target_tier,
        decision=args.decision or "",
        reviewer_note=args.reviewer_note,
        review_bundle_hash=args.review_bundle_hash,
        reviewer=args.reviewer,
        report_store_path=args.report_store_path,
        decision_path=args.decision_path,
    )
    if args.json:
        print(json.dumps({"source": "polymarket_live_validation_decision_cli", "stored": result}, indent=2, sort_keys=True))
    else:
        _print_text_result({"stored": result})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
