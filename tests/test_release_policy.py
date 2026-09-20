from __future__ import annotations

import unittest

from scripts.verify_release_policy import (
    parse_boolean,
    verify_publication_policy,
    windows_signing_required,
)


class ReleasePolicyTests(unittest.TestCase):
    def test_every_stable_tag_always_requires_windows_signing(self) -> None:
        for draft in (False, True):
            with self.subTest(draft=draft):
                self.assertTrue(
                    windows_signing_required(
                        "v1.2.3",
                        draft=draft,
                        configured_required=False,
                    )
                )

    def test_repository_policy_can_require_signing_for_drafts_and_prereleases(self) -> None:
        self.assertFalse(
            windows_signing_required(
                "v1.2.3-rc.1",
                draft=False,
                configured_required=False,
            )
        )
        self.assertTrue(
            windows_signing_required(
                "v1.2.3-rc.1",
                draft=False,
                configured_required=True,
            )
        )

    def test_unsigned_stable_artifacts_fail_closed_even_as_a_draft(self) -> None:
        for draft in (False, True):
            with self.subTest(draft=draft), self.assertRaisesRegex(
                ValueError, "unsigned Windows artifacts"
            ):
                verify_publication_policy(
                    "v1.2.3",
                    draft=draft,
                    prerelease=False,
                    windows_signed=False,
                )

    def test_unsigned_artifacts_are_limited_to_prerelease_tags(self) -> None:
        verify_publication_policy(
            "v1.2.3-rc.1",
            draft=False,
            prerelease=True,
            windows_signed=False,
        )
        verify_publication_policy(
            "v1.2.3",
            draft=False,
            prerelease=False,
            windows_signed=True,
        )

    def test_prerelease_state_must_match_the_validated_tag(self) -> None:
        with self.assertRaisesRegex(ValueError, "conflicts with tag"):
            verify_publication_policy(
                "v1.2.3-rc.1",
                draft=True,
                prerelease=False,
                windows_signed=False,
            )

    def test_boolean_parser_rejects_ambiguous_values(self) -> None:
        self.assertTrue(parse_boolean("true", "value"))
        self.assertFalse(parse_boolean("FALSE", "value"))
        with self.assertRaisesRegex(ValueError, "exactly true or false"):
            parse_boolean("1", "value")


if __name__ == "__main__":
    unittest.main()
