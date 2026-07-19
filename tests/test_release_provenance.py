from __future__ import annotations

import subprocess
import unittest
from collections.abc import Sequence

from scripts.verify_release_provenance import verify_release_provenance


TAG = "v1.2.3"
COMMIT = "a" * 40


def git_runner(
    outcomes: dict[tuple[str, ...], subprocess.CompletedProcess[str]],
):
    def run(arguments: Sequence[str]) -> subprocess.CompletedProcess[str]:
        key = tuple(arguments)
        if key not in outcomes:
            raise AssertionError(f"Unexpected git command: {key!r}")
        return outcomes[key]

    return run


def git_result(*arguments: str, returncode: int = 0, stdout: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["git", *arguments], returncode, stdout=stdout, stderr="")


class ReleaseProvenanceTests(unittest.TestCase):
    def test_accepts_tag_on_workflow_commit_reachable_from_main(self) -> None:
        runner = git_runner(
            {
                ("rev-parse", "--verify", "--end-of-options", f"{TAG}^{{commit}}"):
                    git_result("rev-parse", stdout=f"{COMMIT}\n"),
                ("merge-base", "--is-ancestor", COMMIT, "origin/main"):
                    git_result("merge-base"),
            }
        )

        verify_release_provenance(TAG, COMMIT, "origin/main", run_git=runner)

    def test_rejects_missing_release_tag(self) -> None:
        runner = git_runner(
            {
                ("rev-parse", "--verify", "--end-of-options", f"{TAG}^{{commit}}"):
                    git_result("rev-parse", returncode=128),
            }
        )

        with self.assertRaisesRegex(SystemExit, "does not exist"):
            verify_release_provenance(TAG, COMMIT, "origin/main", run_git=runner)

    def test_rejects_tag_for_a_different_commit(self) -> None:
        other_commit = "b" * 40
        runner = git_runner(
            {
                ("rev-parse", "--verify", "--end-of-options", f"{TAG}^{{commit}}"):
                    git_result("rev-parse", stdout=f"{other_commit}\n"),
            }
        )

        with self.assertRaisesRegex(SystemExit, "not workflow commit"):
            verify_release_provenance(TAG, COMMIT, "origin/main", run_git=runner)

    def test_rejects_tag_not_reachable_from_protected_main(self) -> None:
        runner = git_runner(
            {
                ("rev-parse", "--verify", "--end-of-options", f"{TAG}^{{commit}}"):
                    git_result("rev-parse", stdout=f"{COMMIT}\n"),
                ("merge-base", "--is-ancestor", COMMIT, "origin/main"):
                    git_result("merge-base", returncode=1),
            }
        )

        with self.assertRaisesRegex(SystemExit, "not reachable"):
            verify_release_provenance(TAG, COMMIT, "origin/main", run_git=runner)


if __name__ == "__main__":
    unittest.main()
