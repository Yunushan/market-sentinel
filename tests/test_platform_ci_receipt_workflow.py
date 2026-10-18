from __future__ import annotations

import itertools
import os
import re
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch


ACTION = Path(__file__).resolve().parents[1] / ".github/actions/platform-ci-receipt/action.yml"


def action_step(name: str) -> str:
    text = ACTION.read_text(encoding="utf-8")
    return text.split(f"    - name: {name}\n", 1)[1].split("\n    - name: ", 1)[0]


def condition_matches(step: str, outcomes: dict[str, str], *, cancelled: bool = False, success: bool = True) -> bool:
    """Evaluate the action's small boolean condition subset, without a YAML dependency."""
    expression = re.search(r"^      if: \$\{\{ (.+) \}\}$", step, re.MULTILINE)
    if expression is None:
        raise AssertionError("step has no explicit condition")
    value = expression[1].replace("!cancelled()", "not cancelled").replace("success()", "success")
    value = value.replace("&&", "and")
    value = re.sub(r"steps\.([a-z_0-9]+)\.outcome", r'outcomes["\1"]', value)
    return bool(eval(value, {"__builtins__": {}}, {"outcomes": outcomes, "cancelled": cancelled, "success": success}))


class PlatformCiReceiptWorkflowTests(unittest.TestCase):
    def test_gate_accepts_only_a_successful_bounded_attestation_sequence(self) -> None:
        step = action_step("Require successful receipt attestation")
        script = textwrap.dedent(step.split("python - <<'PY'\n", 1)[1].rsplit("        PY", 1)[0])
        code = compile(script, str(ACTION), "exec")
        accepted = {
            ("success", "skipped", "skipped"),
            ("failure", "success", "skipped"),
            ("failure", "failure", "success"),
        }
        for outcomes in itertools.product(("success", "failure", "cancelled", "skipped", ""), repeat=3):
            with self.subTest(outcomes=outcomes):
                environment = {f"ATTESTATION_OUTCOME_{attempt}": value for attempt, value in enumerate(outcomes, 1)}
                with patch.dict(os.environ, environment, clear=True):
                    if outcomes in accepted:
                        exec(code, {})
                    else:
                        with self.assertRaisesRegex(SystemExit, "Receipt attestation did not succeed"):
                            exec(code, {})

        for attempt in range(1, 4):
            self.assertIn(f"ATTESTATION_OUTCOME_{attempt}: ${{{{ steps.attest_{attempt}.outcome }}}}", step)
        self.assertNotIn("continue-on-error", step)

    def test_retries_only_follow_a_real_failure_and_successful_backoff(self) -> None:
        pin = "actions/attest-build-provenance@4d101475d8b20a2381f78447822ac1eab6504dd8"
        for attempt in range(1, 4):
            step = action_step(f"Attest canonical hosted-job receipt (attempt {attempt})")
            self.assertIn(f"uses: {pin}", step)
            self.assertIn("subject-path: ${{ steps.generate.outputs.receipt-path }}", step)
            self.assertIn("continue-on-error: true", step)
            if attempt == 1:
                continue
            outcomes = {f"attest_{previous}": "failure" for previous in range(1, attempt)}
            outcomes[f"retry_delay_{attempt - 1}"] = "success"
            self.assertTrue(condition_matches(step, outcomes))
            self.assertFalse(condition_matches(step, outcomes, cancelled=True))
            for previous in range(1, attempt):
                for value in ("success", "cancelled", "skipped", ""):
                    with self.subTest(attempt=attempt, previous=previous, outcome=value):
                        self.assertFalse(condition_matches(step, {**outcomes, f"attest_{previous}": value}))
            for value in ("failure", "cancelled", "skipped", ""):
                self.assertFalse(condition_matches(step, {**outcomes, f"retry_delay_{attempt - 1}": value}))

        self.assertIn("run: sleep 15", action_step("Wait before receipt attestation retry 2"))
        self.assertIn("run: sleep 30", action_step("Wait before receipt attestation retry 3"))

    def test_cancellation_or_failed_gate_or_failed_reverification_cannot_upload(self) -> None:
        guard = action_step("Require successful receipt attestation")
        self.assertTrue(condition_matches(guard, {}))
        self.assertFalse(condition_matches(guard, {}, cancelled=True))
        upload = action_step("Upload canonical hosted-job receipt")
        passed = {"require_attestation": "success", "reverify": "success"}
        self.assertTrue(condition_matches(upload, passed))
        self.assertFalse(condition_matches(upload, passed, success=False))
        for step_id in passed:
            for outcome in ("failure", "cancelled", "skipped", ""):
                with self.subTest(step=step_id, outcome=outcome):
                    self.assertFalse(condition_matches(upload, {**passed, step_id: outcome}))
        self.assertNotIn("continue-on-error", upload)


if __name__ == "__main__":
    unittest.main()
