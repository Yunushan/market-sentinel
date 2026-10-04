from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import shlex
import unittest
from unittest.mock import Mock, patch

from polymarket.credential_runbook import build_polymarket_credential_runbook
from polymarket.live_verification import LiveOrderCancelRequest, run_live_order_cancel_verification
from scripts import verify_polymarket_credentials as credential_cli
from scripts import verify_polymarket_live as live_probe


def sdk_environment() -> dict[str, str]:
    return {
        "POLYMARKET_PRIVATE_KEY": "0x" + "1" * 64,
        "POLY_API_KEY": "read-key",
        "POLY_API_SECRET": "cmVhZC1zZWNyZXQ=",
        "POLY_PASSPHRASE": "read-passphrase",
    }


def legacy_headers() -> dict[str, str]:
    return {
        "POLY_ADDRESS": "0x" + "a" * 40,
        "POLY_API_KEY": "read-key",
        "POLY_PASSPHRASE": "read-passphrase",
        "POLY_SIGNATURE": "old-signature",
        "POLY_TIMESTAMP": "123",
    }


class CredentialReadinessAlignmentTests(unittest.TestCase):
    def setUp(self) -> None:
        # Inventory must never construct the SDK, sign, derive keys or use transport.
        self.network_guard = patch("socket.socket", side_effect=AssertionError("offline inventory attempted network"))
        self.network_guard.start()
        self.addCleanup(self.network_guard.stop)
        self.sdk_guard = patch("polymarket.trader.ClobClient", side_effect=AssertionError("offline inventory constructed SDK"))
        self.sdk_guard.start()
        self.addCleanup(self.sdk_guard.stop)

    def assert_clob_ready(self, env: dict[str, str], expected: bool, *, settings: dict | None = None) -> dict:
        runbook = build_polymarket_credential_runbook(settings, environ=env)
        candidates = runbook["readiness"]["credentialed_read_candidates"]
        self.assertEqual("clob_l2_orders" in candidates, expected)
        self.assertEqual(runbook["readiness"]["non_destructive_auth_ready"], expected)
        self.assertFalse(runbook["funded_execution_exposed"])
        return runbook

    def cli_result(self, env: dict[str, str], flag: str) -> tuple[int, dict]:
        output = io.StringIO()
        with patch.dict(os.environ, env, clear=True), patch.object(credential_cli, "_load_env"), patch(
            "sys.argv", ["verify_polymarket_credentials.py", "--json", flag]
        ), contextlib.redirect_stdout(output):
            result = credential_cli.main()
        return result, json.loads(output.getvalue())

    def test_complete_sdk_credentials_need_no_static_signed_headers(self) -> None:
        runbook = self.assert_clob_ready(sdk_environment(), True)
        self.assertEqual(runbook["readiness"]["direct_l2_read_headers"]["status"], "blocked")

    def test_static_signed_headers_alone_do_not_prepare_current_clob_probe(self) -> None:
        runbook = self.assert_clob_ready(legacy_headers(), False)
        self.assertEqual(runbook["readiness"]["direct_l2_read_headers"]["status"], "ok")

    def test_websocket_payload_only_is_separate_from_accepted_read_readiness(self) -> None:
        env = sdk_environment()
        del env["POLYMARKET_PRIVATE_KEY"]
        runbook = self.assert_clob_ready(env, False)
        self.assertEqual(runbook["readiness"]["user_websocket_auth_payload"]["status"], "ok")
        self.assertEqual(self.cli_result(env, "--require-user-websocket-ready")[0], 0)
        self.assertEqual(self.cli_result(env, "--require-authenticated-read-ready")[0], 1)

    def test_l2_cli_gate_uses_current_sdk_read_contract(self) -> None:
        self.assertEqual(self.cli_result(sdk_environment(), "--require-l2-read-ready")[0], 0)
        self.assertEqual(self.cli_result(legacy_headers(), "--require-l2-read-ready")[0], 1)

    def test_generic_aliases_remain_supported_when_primary_aliases_absent(self) -> None:
        env = sdk_environment()
        env["PRIVATE_KEY"] = env.pop("POLYMARKET_PRIVATE_KEY")
        env.update(SIGNATURE_TYPE="1", FUNDER_ADDRESS="0x" + "b" * 40)
        self.assert_clob_ready(env, True)

    def test_padded_api_triple_matches_sdk_trim_semantics(self) -> None:
        env = sdk_environment()
        for field in ("POLY_API_KEY", "POLY_API_SECRET", "POLY_PASSPHRASE"):
            env[field] = " " + env[field] + " "
        self.assert_clob_ready(env, True)

    def test_last_valid_private_key_scalar_remains_ready(self) -> None:
        env = sdk_environment()
        env["POLYMARKET_PRIVATE_KEY"] = "0xfffffffffffffffffffffffffffffffebaaedce6af48a03bbfd25e8cd0364140"
        self.assert_clob_ready(env, True)

    def test_missing_or_whitespace_api_field_blocks_sdk_read(self) -> None:
        for name in ("POLY_API_KEY", "POLY_API_SECRET", "POLY_PASSPHRASE"):
            for value in (None, "   "):
                with self.subTest(name=name, value=value):
                    env = sdk_environment()
                    if value is None:
                        del env[name]
                    else:
                        env[name] = value
                    self.assert_clob_ready(env, False)

    def test_api_key_and_passphrase_follow_sdk_transport_header_grammar(self) -> None:
        for field in ("POLY_API_KEY", "POLY_PASSPHRASE"):
            for invalid in ("non-ascii-\u2603", "prefix\x00suffix", "prefix\r\nsuffix", "prefix\vsuffix", "prefix\fsuffix"):
                with self.subTest(field=field, invalid=repr(invalid)):
                    env = sdk_environment()
                    env[field] = invalid
                    runbook = self.assert_clob_ready(env, False)
                    self.assertNotIn(invalid, json.dumps(runbook))
                    with patch.object(live_probe.os, "environ", env), patch.object(live_probe, "PolymarketTrader") as factory:
                        checks = live_probe._authenticated_read_checks(1)
                    self.assertEqual(checks["clob_l2_orders"]["status"], "blocked")
                    factory.assert_not_called()
            for permitted in ("not-a-uuid", "opaque punctuation !?=", "internal\tseparator", "  trimmed-edges  "):
                with self.subTest(field=field, permitted=permitted):
                    env = sdk_environment()
                    env[field] = permitted
                    self.assert_clob_ready(env, True)

    def test_api_secret_decoder_matches_sdk_without_signing_or_length_guesses(self) -> None:
        for secret in ("read-secret", "not-base64-\u2603"):
            with self.subTest(secret=secret):
                env = sdk_environment()
                env["POLY_API_SECRET"] = secret
                env["POLY_SECRET"] = "cmVhZC1zZWNyZXQ="
                runbook = self.assert_clob_ready(env, False)
                self.assertNotIn(secret, json.dumps(runbook))
                with patch.dict(os.environ, env, clear=True), patch.object(live_probe, "PolymarketTrader") as factory:
                    checks = live_probe._authenticated_read_checks(1)
                self.assertEqual(checks["clob_l2_orders"]["status"], "blocked")
                factory.assert_not_called()
        # The SDK decoder accepts this; impose no invented minimum byte length.
        env = sdk_environment()
        env["POLY_API_SECRET"] = "YQ=="
        self.assert_clob_ready(env, True)

    def test_secret_alias_supported_without_derivation(self) -> None:
        env = sdk_environment()
        env["POLY_SECRET"] = env.pop("POLY_API_SECRET")
        self.assert_clob_ready(env, True)

    def test_invalid_higher_priority_secret_does_not_fall_through(self) -> None:
        env = sdk_environment()
        env.update(POLY_API_SECRET="   ", POLY_SECRET="valid-lower-secret")
        self.assert_clob_ready(env, False)

    def test_selected_primary_aliases_override_invalid_unused_generic_aliases(self) -> None:
        env = sdk_environment()
        env.update(
            PRIVATE_KEY="invalid-unused-key",
            SIGNATURE_TYPE="invalid-unused-type",
            FUNDER_ADDRESS="invalid-unused-funder",
            POLYMARKET_SIGNATURE_TYPE="2",
            POLYMARKET_FUNDER_ADDRESS="0x" + "b" * 40,
        )
        self.assert_clob_ready(env, True)

    def test_invalid_primary_alias_does_not_fall_through(self) -> None:
        for primary, fallback, valid in (
            ("POLYMARKET_PRIVATE_KEY", "PRIVATE_KEY", "0x" + "1" * 64),
            ("POLYMARKET_SIGNATURE_TYPE", "SIGNATURE_TYPE", "0"),
            ("POLYMARKET_FUNDER_ADDRESS", "FUNDER_ADDRESS", "0x" + "b" * 40),
        ):
            with self.subTest(primary=primary):
                env = sdk_environment()
                env.update({primary: "   ", fallback: valid})
                self.assert_clob_ready(env, False)

    def test_config_only_signer_is_not_available_to_cli(self) -> None:
        env = sdk_environment()
        del env["POLYMARKET_PRIVATE_KEY"]
        runbook = self.assert_clob_ready(env, False, settings={"private_key": "0x" + "1" * 64})
        self.assertEqual(runbook["readiness"]["sdk_trading_credentials"]["status"], "ok")

    def test_empty_explicit_environment_never_uses_process_credentials(self) -> None:
        with patch.dict(os.environ, sdk_environment(), clear=True):
            self.assert_clob_ready({}, False)

    def test_private_key_must_be_valid_secp256k1_scalar_and_canonical_text(self) -> None:
        for key in (
            "bad-key",
            "0x" + "0" * 64,
            "0xfffffffffffffffffffffffffffffffebaaedce6af48a03bbfd25e8cd0364141",
            "0x" + "f" * 64,
            " " + "0x" + "1" * 64,
        ):
            with self.subTest(key=key):
                env = sdk_environment()
                env["POLYMARKET_PRIVATE_KEY"] = key
                self.assert_clob_ready(env, False)

    def test_funder_required_for_proxy_safe_and_deposit_wallet_types(self) -> None:
        for signature_type in ("1", "2", "3"):
            with self.subTest(signature_type=signature_type):
                env = sdk_environment()
                env["POLYMARKET_SIGNATURE_TYPE"] = signature_type
                self.assert_clob_ready(env, False)
                env["POLYMARKET_FUNDER_ADDRESS"] = "0x" + "b" * 40
                self.assert_clob_ready(env, True)
                env["POLYMARKET_FUNDER_ADDRESS"] = " " + "0x" + "b" * 40
                self.assert_clob_ready(env, False)

    def test_deposit_wallet_alias_is_not_forwarded_by_current_probe(self) -> None:
        env = sdk_environment()
        env.update(POLYMARKET_SIGNATURE_TYPE="3", DEPOSIT_WALLET_ADDRESS="0x" + "b" * 40)
        self.assert_clob_ready(env, False)

    def test_signature_types_match_executed_integer_contract(self) -> None:
        for value in ("4", "no", "1.0"):
            with self.subTest(value=value):
                env = sdk_environment()
                env["POLYMARKET_SIGNATURE_TYPE"] = value
                self.assert_clob_ready(env, False)
        env = sdk_environment()
        env["POLYMARKET_SIGNATURE_TYPE"] = " +0 "
        self.assert_clob_ready(env, True)

    def test_relayer_remains_an_independent_accepted_read_candidate(self) -> None:
        env = {"RELAYER_API_KEY": "relayer-key", "RELAYER_API_KEY_ADDRESS": "0x" + "c" * 40}
        runbook = build_polymarket_credential_runbook(environ=env)
        self.assertEqual(runbook["readiness"]["credentialed_read_candidates"], ["relayer_recent_transactions"])
        self.assertTrue(runbook["readiness"]["non_destructive_auth_ready"])
        self.assertEqual(self.cli_result(env, "--require-authenticated-read-ready")[0], 0)
        self.assertEqual(self.cli_result(env, "--require-l2-read-ready")[0], 1)

    def test_relayer_blank_or_whitespace_header_cannot_satisfy_read_gate(self) -> None:
        for name in ("RELAYER_API_KEY", "RELAYER_API_KEY_ADDRESS"):
            for value in ("", "   ", " leading", "trailing ", "embedded\r\n", "non-latin-\u2603"):
                with self.subTest(name=name, value=value):
                    env = {"RELAYER_API_KEY": "relayer-key", "RELAYER_API_KEY_ADDRESS": "relayer-address", name: value}
                    runbook = build_polymarket_credential_runbook(environ=env)
                    self.assertFalse(runbook["readiness"]["non_destructive_auth_ready"])
                    self.assertEqual(runbook["readiness"]["credentialed_read_candidates"], [])
                    self.assertIn(name, runbook["readiness"]["relayer_headers"]["missing"])
                    self.assertEqual(self.cli_result(env, "--require-authenticated-read-ready")[0], 1)
                    with patch.dict(os.environ, env, clear=True), patch.object(live_probe.relayer, "get_recent_transactions") as read:
                        checks = live_probe._authenticated_read_checks(1)
                    self.assertEqual(checks["relayer_recent_transactions"]["status"], "blocked")
                    read.assert_not_called()

    def test_valid_relayer_probe_preserves_raw_headers_and_does_not_claim_account_validity(self) -> None:
        env = {"RELAYER_API_KEY": "raw-relayer-key", "RELAYER_API_KEY_ADDRESS": "raw-address"}
        runbook = build_polymarket_credential_runbook(environ=env)
        self.assertTrue(runbook["readiness"]["non_destructive_auth_ready"])
        with patch.dict(os.environ, env, clear=True), patch.object(live_probe.relayer, "get_recent_transactions", return_value=[]) as read:
            checks = live_probe._authenticated_read_checks(1)
        self.assertEqual(checks["relayer_recent_transactions"]["status"], "ok")
        read.assert_called_once_with(env, timeout=1)

    def test_malformed_signature_setting_never_echoes_raw_text(self) -> None:
        env = sdk_environment()
        env.update(POLYMARKET_SIGNATURE_TYPE="mistakenly-pasted-secret", SIGNATURE_TYPE="another-pasted-secret")
        serialized = json.dumps(build_polymarket_credential_runbook(environ=env))
        self.assertNotIn(env["POLYMARKET_SIGNATURE_TYPE"], serialized)
        self.assertNotIn(env["SIGNATURE_TYPE"], serialized)
        self.assertIn('"redacted": "***"', serialized)

    def test_inventory_redacts_selected_and_unselected_secrets(self) -> None:
        env = sdk_environment()
        env.update(PRIVATE_KEY="0x" + "2" * 64, POLY_SECRET="unused-secret")
        serialized = json.dumps(build_polymarket_credential_runbook(environ=env))
        for name in ("POLYMARKET_PRIVATE_KEY", "PRIVATE_KEY", "POLY_API_KEY", "POLY_API_SECRET", "POLY_SECRET", "POLY_PASSPHRASE"):
            self.assertNotIn(env[name], serialized)

    def test_generated_nonfunded_command_builds_dry_run_without_transport(self) -> None:
        command = build_polymarket_credential_runbook(environ={})["operator_commands"]["dry_run_order_cancel_no_funded_actions"]
        command = command.replace("<TOKEN>", "123").replace("<PRICE>", "0.1").replace("<SIZE>", "1")
        parser = argparse.ArgumentParser()
        for option in ("token-id", "side", "price", "size", "report-file"):
            parser.add_argument("--" + option)
        parser.add_argument("--allow-token-id", action="append", default=[])
        parser.add_argument("--cancel-immediately", action="store_true")
        parser.add_argument("--allow-funded-order", action="store_true")
        args = parser.parse_args(shlex.split(command)[2:])
        self.assertFalse(args.allow_funded_order)
        trader_factory = Mock(side_effect=AssertionError("dry-run constructed trader"))
        orderbook_getter = Mock(side_effect=AssertionError("dry-run fetched orderbook"))
        geoblock_checker = Mock(side_effect=AssertionError("dry-run fetched eligibility"))
        request = LiveOrderCancelRequest(
            token_id=args.token_id, side=args.side, price=args.price, size=args.size,
            allow_token_ids=args.allow_token_id, cancel_immediately=args.cancel_immediately,
            execute=args.allow_funded_order,
        )
        result = run_live_order_cancel_verification(
            request, trader_factory=trader_factory, orderbook_getter=orderbook_getter,
            geoblock_checker=geoblock_checker,
        )
        self.assertEqual(result["status"], "dry_run")
        self.assertFalse(result["live_action"])
        self.assertEqual(result["blockers"], [])
        trader_factory.assert_not_called()
        orderbook_getter.assert_not_called()
        geoblock_checker.assert_not_called()

    def test_funded_guidance_only_inspects_protected_workflow(self) -> None:
        runbook = build_polymarket_credential_runbook(environ={})
        commands = runbook["operator_commands"]
        for command in commands.values():
            self.assertNotIn("--allow-funded-order", command)
            self.assertNotIn("--evidence-run-id", command)
            self.assertNotIn("--evidence-run-attempt", command)
            self.assertNotIn("--evidence-nonce", command)
            self.assertNotIn("gh workflow run", command)
        self.assertIn("gh workflow view", commands["funded_workflow_inspection"])
        self.assertEqual(runbook["funded_workflow"]["ref"], "main")
        self.assertTrue(runbook["funded_workflow"]["requires_explicit_user_approval"])

    def test_live_probe_and_inventory_use_same_selected_credentials(self) -> None:
        env = sdk_environment()
        env.update(
            PRIVATE_KEY="invalid-unused-key", SIGNATURE_TYPE="invalid-unused-type",
            FUNDER_ADDRESS="invalid-unused-funder", POLYMARKET_SIGNATURE_TYPE="2",
            POLYMARKET_FUNDER_ADDRESS="0x" + "b" * 40,
        )
        self.assert_clob_ready(env, True)
        trader = Mock()
        trader.get_orders.return_value = []
        with patch.dict(os.environ, env, clear=True), patch.object(live_probe, "PolymarketTrader", return_value=trader) as factory:
            checks = live_probe._authenticated_read_checks(1)
        self.assertEqual(checks["clob_l2_orders"]["status"], "ok")
        config = factory.call_args.args[0]
        self.assertEqual(config.private_key, env["POLYMARKET_PRIVATE_KEY"])
        self.assertEqual(config.funder_address, env["POLYMARKET_FUNDER_ADDRESS"])
        self.assertEqual(config.signature_type, 2)
        self.assertFalse(config.allow_api_key_derivation)
        self.assertFalse(config.allow_api_key_creation)
        trader.get_orders.assert_called_once_with(only_first_page=True)

    def test_invalid_selected_credentials_never_construct_sdk_in_live_probe(self) -> None:
        env = sdk_environment()
        env["POLYMARKET_PRIVATE_KEY"] = "0x" + "0" * 64
        with patch.dict(os.environ, env, clear=True), patch.object(live_probe, "PolymarketTrader") as factory:
            checks = live_probe._authenticated_read_checks(1)
        self.assertEqual(checks["clob_l2_orders"]["status"], "blocked")
        factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
