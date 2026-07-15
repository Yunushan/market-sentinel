from __future__ import annotations

import unittest

import verify


class ReleaseVersionGateTests(unittest.TestCase):
    def test_next_version_must_be_newer_than_latest_tag(self) -> None:
        expected = verify.validate_release_version_history("1.0.9", ["v1.0.7", "v1.0.8"], [])

        self.assertEqual(expected, "v1.0.9")

        with self.assertRaisesRegex(SystemExit, "must be newer"):
            verify.validate_release_version_history("1.0.7", ["v1.0.8"], [])

    def test_existing_version_tag_must_point_at_head(self) -> None:
        with self.assertRaisesRegex(SystemExit, "reuses existing tag"):
            verify.validate_release_version_history("1.0.8", ["v1.0.8"], [])

        expected = verify.validate_release_version_history("1.0.8", ["v1.0.8"], ["v1.0.8"])
        self.assertEqual(expected, "v1.0.8")

    def test_release_version_rejects_local_suffixes_and_invalid_values(self) -> None:
        with self.assertRaisesRegex(SystemExit, "without a local suffix"):
            verify.validate_release_version_history("1.0.9+local", [], [])
        with self.assertRaisesRegex(SystemExit, "not a valid release version"):
            verify.validate_release_version_history("not-a-version", [], [])


if __name__ == "__main__":
    unittest.main()
