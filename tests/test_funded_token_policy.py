from __future__ import annotations

import io
import json
import os
import unittest
from contextlib import redirect_stderr
from types import SimpleNamespace
from unittest.mock import patch

from polymarket.funded_policy import (
    FUNDED_TOKEN_ALLOWLIST_VARIABLE,
    MAX_FUNDED_TOKEN_IDS,
    funded_token_allowlist_json,
    funded_token_allowlist_sha256,
    parse_funded_token_allowlist,
)
from scripts import verify_polymarket_live as live_probe


class FundedTokenPolicyTests(unittest.TestCase):
    def test_canonical_policy_is_sorted_bounded_and_digestible(self) -> None:
        raw = '["123","456"]'
        token_ids = parse_funded_token_allowlist(raw)
        self.assertEqual(token_ids, ("123", "456"))
        self.assertEqual(funded_token_allowlist_json(token_ids), raw)
        self.assertEqual(len(funded_token_allowlist_sha256(token_ids)), 64)

    def test_policy_rejects_ambiguous_or_unbounded_values(self) -> None:
        cases = (
            None,
            "",
            "{}",
            "[]",
            '["123","123"]',
            '["456","123"]',
            '[ "123" ]',
            '["bad token"]',
            '["$(not-shell)"]',
            json.dumps(
                [str(index) for index in range(MAX_FUNDED_TOKEN_IDS + 1)],
                separators=(",", ":"),
            ),
        )
        for value in cases:
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_funded_token_allowlist(value)

    def test_verifier_rejects_dispatcher_token_outside_protected_policy_before_network(self) -> None:
        with (
            patch.dict(
                os.environ,
                {FUNDED_TOKEN_ALLOWLIST_VARIABLE: '["approved-token"]'},
                clear=False,
            ),
            patch.object(live_probe, "_load_env") as load_env,
            patch.object(live_probe, "_public_checks") as public_checks,
            redirect_stderr(io.StringIO()),
        ):
            with self.assertRaises(SystemExit) as raised:
                live_probe.main(
                    [
                        "--allow-funded-order",
                        "--token-id",
                        "dispatcher-token",
                        "--allow-token-environment",
                        FUNDED_TOKEN_ALLOWLIST_VARIABLE,
                    ]
                )
        self.assertEqual(raised.exception.code, 2)
        load_env.assert_not_called()
        public_checks.assert_not_called()

    def test_verifier_rejects_tautological_allowlist_combined_with_protected_policy(self) -> None:
        with (
            patch.dict(
                os.environ,
                {FUNDED_TOKEN_ALLOWLIST_VARIABLE: '["approved-token"]'},
                clear=False,
            ),
            patch.object(live_probe, "_load_env") as load_env,
            redirect_stderr(io.StringIO()),
        ):
            with self.assertRaises(SystemExit) as raised:
                live_probe.main(
                    [
                        "--allow-funded-order",
                        "--token-id",
                        "approved-token",
                        "--allow-token-environment",
                        FUNDED_TOKEN_ALLOWLIST_VARIABLE,
                        "--allow-token-id",
                        "approved-token",
                    ]
                )
        self.assertEqual(raised.exception.code, 2)
        load_env.assert_not_called()

    def test_verifier_report_receipt_binds_the_policy_seen_by_collector(self) -> None:
        args = SimpleNamespace(
            token_id="approved-token",
            side="BUY",
            price="0.01",
            size="1",
            allow_funded_order=False,
            allow_token_id=[],
            allow_token_file=None,
            funded_environment_allow_tokens=("approved-token", "second-token"),
            tif="GTC",
            cancel_immediately=True,
            confirm_live_order_cancel="",
            max_verify_size=5.0,
            max_verify_notional=1.0,
            maker_price_buffer=0.005,
        )
        with patch.object(
            live_probe,
            "run_live_order_cancel_verification",
            return_value={"status": "dry_run", "live_action": False},
        ):
            result = live_probe._funded_order_check(args)
        receipt = result["funded_token_policy_receipt"]
        self.assertEqual(receipt["token_ids"], ["approved-token", "second-token"])
        self.assertEqual(receipt["selected_token_id"], "approved-token")
        self.assertEqual(
            receipt["allowlist_sha256"],
            funded_token_allowlist_sha256(("approved-token", "second-token")),
        )


if __name__ == "__main__":
    unittest.main()
