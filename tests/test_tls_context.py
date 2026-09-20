from __future__ import annotations

from pathlib import Path
import ssl
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import core.tls as tls
from test_polymarket_http_transport import local_tls_server


class TLSContextTests(unittest.TestCase):
    @staticmethod
    def context_mock() -> Mock:
        context = Mock()
        context.verify_flags = 0
        return context

    def test_platform_context_is_verified(self) -> None:
        context = self.context_mock()
        platform_context = Mock(return_value=context)

        with patch.dict(sys.modules, {"truststore": SimpleNamespace(SSLContext=platform_context)}):
            result = tls.create_verified_client_context()

        self.assertIs(result, context)
        platform_context.assert_called_once_with(ssl.PROTOCOL_TLS_CLIENT)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertIs(context.check_hostname, True)
        context.load_default_certs.assert_not_called()

    def test_platform_trust_installation_is_explicit_and_fail_closed(self) -> None:
        truststore = SimpleNamespace(inject_into_ssl=Mock())
        with patch.dict(sys.modules, {"truststore": truststore}):
            self.assertIs(tls.install_platform_trust_store(), True)
        truststore.inject_into_ssl.assert_called_once_with()

        unavailable = SimpleNamespace(inject_into_ssl=Mock(side_effect=RuntimeError("unavailable")))
        with patch.dict(sys.modules, {"truststore": unavailable}):
            self.assertIs(tls.install_platform_trust_store(), False)

    def test_explicit_ca_uses_hardened_stdlib_context_only(self) -> None:
        context = self.context_mock()
        stdlib_context = Mock(return_value=context)

        with patch.object(tls, "_STDLIB_SSL_CONTEXT", stdlib_context):
            result = tls.create_verified_client_context(cafile="operator-ca.pem", capath="operator-ca-dir")

        self.assertIs(result, context)
        stdlib_context.assert_called_once_with(ssl.PROTOCOL_TLS_CLIENT)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertIs(context.check_hostname, True)
        context.load_verify_locations.assert_called_once_with(
            cafile="operator-ca.pem", capath="operator-ca-dir"
        )
        context.load_default_certs.assert_not_called()

    def test_missing_truststore_falls_back_to_verified_system_context(self) -> None:
        context = self.context_mock()
        stdlib_context = Mock(return_value=context)

        with (
            patch.object(tls, "_STDLIB_SSL_CONTEXT", stdlib_context),
            patch.dict(sys.modules, {"truststore": None}),
        ):
            result = tls.create_verified_client_context()

        self.assertIs(result, context)
        context.load_default_certs.assert_called_once_with(ssl.Purpose.SERVER_AUTH)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertIs(context.check_hostname, True)

    @staticmethod
    def complete_handshake(client_context, server_context, hostname: str) -> None:
        client_incoming, client_outgoing = ssl.MemoryBIO(), ssl.MemoryBIO()
        server_incoming, server_outgoing = ssl.MemoryBIO(), ssl.MemoryBIO()
        client = client_context.wrap_bio(
            client_incoming, client_outgoing, server_hostname=hostname
        )
        server = server_context.wrap_bio(server_incoming, server_outgoing, server_side=True)
        client_done = server_done = False
        for _ in range(20):
            if not client_done:
                try:
                    client.do_handshake()
                    client_done = True
                except ssl.SSLWantReadError:
                    pass
            client_bytes = client_outgoing.read()
            if client_bytes:
                server_incoming.write(client_bytes)
            if not server_done:
                try:
                    server.do_handshake()
                    server_done = True
                except ssl.SSLWantReadError:
                    pass
            server_bytes = server_outgoing.read()
            if server_bytes:
                client_incoming.write(server_bytes)
            if client_done and server_done:
                return
        raise AssertionError("in-memory TLS handshake did not complete")

    def test_explicit_ca_and_hostname_are_verified_in_memory(self) -> None:
        with tempfile.TemporaryDirectory() as directory, local_tls_server(directory) as (_, ca, _):
            server_context = tls._STDLIB_SSL_CONTEXT(ssl.PROTOCOL_TLS_SERVER)
            server_context.load_cert_chain(
                Path(directory) / "server.pem", Path(directory) / "server.key"
            )
            trusted = tls.create_verified_client_context(cafile=ca)
            self.complete_handshake(trusted, server_context, "venue.example.test")

            wrong_hostname = tls.create_verified_client_context(cafile=ca)
            with self.assertRaises(ssl.SSLCertVerificationError):
                self.complete_handshake(wrong_hostname, server_context, "wrong.example.test")


if __name__ == "__main__":
    unittest.main()
