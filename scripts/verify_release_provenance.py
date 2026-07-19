from __future__ import annotations

import argparse
import re
import subprocess
from collections.abc import Callable, Sequence


GitRunner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]
COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")


def _run_git(arguments: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *arguments],
        check=False,
        capture_output=True,
        text=True,
    )


def _resolved_commit(tag: str, run_git: GitRunner) -> str:
    result = run_git(("rev-parse", "--verify", "--end-of-options", f"{tag}^{{commit}}"))
    candidate = result.stdout.strip().lower()
    if result.returncode != 0 or not COMMIT_SHA.fullmatch(candidate):
        raise SystemExit(f"Release tag {tag} does not exist or does not resolve to a commit.")
    return candidate


def verify_release_provenance(
    tag: str,
    workflow_commit: str,
    main_ref: str,
    *,
    run_git: GitRunner = _run_git,
) -> None:
    """Require an existing release tag to identify the workflow commit on protected main."""
    tag = tag.strip()
    workflow_commit = workflow_commit.strip().lower()
    main_ref = main_ref.strip()
    if not tag:
        raise SystemExit("Release tag must not be empty.")
    if not COMMIT_SHA.fullmatch(workflow_commit):
        raise SystemExit("Workflow commit must be a full 40-character Git SHA.")
    if not main_ref:
        raise SystemExit("Protected main reference must not be empty.")

    tag_commit = _resolved_commit(tag, run_git)
    if tag_commit != workflow_commit:
        raise SystemExit(
            f"Release tag {tag} resolves to {tag_commit}, not workflow commit {workflow_commit}. "
            "Run a manual release from the existing release tag ref, not an arbitrary branch."
        )

    result = run_git(("merge-base", "--is-ancestor", tag_commit, main_ref))
    if result.returncode != 0:
        raise SystemExit(
            f"Release tag {tag} commit {tag_commit} is not reachable from {main_ref}. "
            "Publish releases only from a commit already merged to protected main."
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify that a release tag resolves to the workflow commit on protected main."
    )
    parser.add_argument("--tag", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--main-ref", default="origin/main")
    args = parser.parse_args()
    verify_release_provenance(args.tag, args.commit, args.main_ref)
    print(f"[ok] Release provenance ({args.tag} -> {args.commit})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
