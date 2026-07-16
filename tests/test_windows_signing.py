from __future__ import annotations

import unittest

from scripts.sign_windows_release import decode_certificate


class WindowsSigningTests(unittest.TestCase):
    def test_certificate_decoder_rejects_invalid_base64(self) -> None:
        with self.assertRaises(SystemExit):
            decode_certificate("not base64!")

    def test_certificate_decoder_decodes_a_valid_payload(self) -> None:
        self.assertEqual(decode_certificate("Y2VydGlmaWNhdGU="), b"certificate")


if __name__ == "__main__":
    unittest.main()
