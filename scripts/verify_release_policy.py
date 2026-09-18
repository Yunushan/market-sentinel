#!/usr/bin/env python3
"""Resolve and verify fail-closed release publication policy."""

from __future__ import annotations

import argparse

try:
    from scripts.release_version import parse_release_version
except ModuleNotFoundError:  # Direct execution adds scripts/, rather than the repository root, to sys.path.
    from release_version import parse_release_version


def parse_boolean(value: str, label: str) -> bool:
    normalized = str(value).strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise ValueError(f"{label} must be exactly true or false; got {value!r}")


def windows_signing_required(
    tag: str,
    *,
    draft: bool,
    configured_required: bool,
) -> bool:
    """Require signing for configured runs and every stable release artifact set."""

    parsed = parse_release_version(tag, require_tag_prefix=True)
    # Stable drafts can be published manually after this workflow finishes, so
    # draft state is not a safe boundary for unsigned stable artifacts.
    return configured_required or not parsed.is_prerelease


def verify_publication_policy(
    tag: str,
    *,
    draft: bool,
    prerelease: bool,
    windows_signed: bool,
) -> None:
    """Reject a release state that could publicly expose unsigned stable assets."""

    parsed = parse_release_version(tag, require_tag_prefix=True)
    if prerelease != parsed.is_prerelease:
        raise ValueError(
            f"release prerelease state ({str(prerelease).lower()}) conflicts with tag {tag} "
            f"({str(parsed.is_prerelease).lower()})"
        )
    if not windows_signed and not prerelease:
        raise ValueError(
            "unsigned Windows artifacts are permitted only for prerelease tags; "
            f"refusing stable release artifacts for {tag}, including draft state"
        )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    signing = commands.add_parser(
        "signing-required",
        help="Print whether Windows signing is mandatory for the requested release state.",
    )
    signing.add_argument("--tag", required=True)
    signing.add_argument("--draft", required=True)
    signing.add_argument("--configured-required", required=True)

    verify = commands.add_parser(
        "verify-publication",
        help="Fail unless the requested publication state is safe for the Windows signing status.",
    )
    verify.add_argument("--tag", required=True)
    verify.add_argument("--draft", required=True)
    verify.add_argument("--prerelease", required=True)
    verify.add_argument("--windows-signed", required=True)
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    try:
        draft = parse_boolean(args.draft, "draft")
        if args.command == "signing-required":
            required = windows_signing_required(
                args.tag,
                draft=draft,
                configured_required=parse_boolean(
                    args.configured_required,
                    "configured-required",
                ),
            )
            print("true" if required else "false")
        else:
            verify_publication_policy(
                args.tag,
                draft=draft,
                prerelease=parse_boolean(args.prerelease, "prerelease"),
                windows_signed=parse_boolean(args.windows_signed, "windows-signed"),
            )
            print("[ok] release publication policy")
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
