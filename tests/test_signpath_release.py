from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import shutil
import struct
import subprocess
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile, ZipInfo

from scripts import signpath_release as signing
from scripts.windows_signing_policy import signing_configuration_issues


REVISION = "a" * 40
REQUEST_ID = "00000000-0000-4000-8000-000000000002"


def environment() -> dict[str, str]:
    return {
        "GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": "github-hosted", "RUNNER_OS": "Windows",
        "GITHUB_REPOSITORY": signing.REPOSITORY, "GITHUB_SHA": REVISION,
        "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "1", "GITHUB_REF": "refs/heads/main",
        "GITHUB_WORKFLOW_REF": f"{signing.REPOSITORY}/.github/workflows/release.yml@refs/heads/main",
        "GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_TOKEN": "github-read-token",
        "WINDOWS_SIGNING_PROVIDER": "signpath", "SIGNPATH_API_TOKEN": "provider-submitter-token",
        "SIGNPATH_ORGANIZATION_ID": "00000000-0000-4000-8000-000000000001",
        "WINDOWS_SIGNING_CERTIFICATE_SHA256": "b" * 64,
    }


def parameters() -> dict[str, str]:
    return {"nativeVersion": "1.0.1299", "releaseTag": "v1.0.12", "sourceRevision": REVISION}


def request_details() -> dict:
    return {
        "status": "Completed", "workflowStatus": "Completed", "isFinalStatus": True,
        "projectSlug": "market-sentinel", "signingPolicySlug": "release-signing",
        "artifactConfigurationSlug": "market-sentinel-exe-v1", "parameters": parameters(),
        "origin": {
            "repositoryData": {"url": f"https://github.com/{signing.REPOSITORY}", "commitId": REVISION, "branchName": "main", "sourceControlManagementType": "git"},
            "buildData": {"url": f"https://github.com/{signing.REPOSITORY}/actions/runs/123"},
        },
    }


def unsigned_pe() -> bytes:
    raw = bytearray(513)
    raw[:2] = b"MZ"
    struct.pack_into("<I", raw, 0x3C, 0x80)
    raw[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<H", raw, 0x80 + 20, 240)
    struct.pack_into("<H", raw, 0x80 + 24, 0x20B)
    raw[-20:] = b"bundled-overlay-code"
    return bytes(raw)


def signed_pe(raw: bytes) -> bytes:
    result = bytearray(raw + b"\0" * ((-len(raw)) % 8))
    certificate_start = len(result)
    struct.pack_into("<II", result, 0x80 + 24 + 112 + 32, certificate_start, 16)
    result.extend(b"signature-bytes!")
    return bytes(result)


def archive(name: str, contents: bytes) -> bytes:
    stream = io.BytesIO()
    with ZipFile(stream, "w") as output:
        output.writestr(name, contents)
    return stream.getvalue()


def compound_storage(*, signature: bool = False, modified: bool = False) -> bytes:
    """Construct native CFB streams; no installer code runs in this test."""
    free, end, fat_sector = 0xFFFFFFFF, 0xFFFFFFFE, 0xFFFFFFFD
    header = bytearray(512)
    header[:8] = bytes.fromhex("d0cf11e0a1b11ae1")
    struct.pack_into("<HHHHH", header, 24, 0x3E, 3, 0xFFFE, 9, 6)
    struct.pack_into("<IIIIIIIII", header, 40, 0, 1, 1, 0, 4096, end, 0, end, 0)
    struct.pack_into("<109I", header, 76, 0, *([free] * 108))
    fat = [free] * 128
    fat[0], fat[1] = fat_sector, end
    streams = [b"database-table" + bytes([int(modified)]), b"embedded-cabinet"]
    if signature:
        streams.append(b"authenticode-signature")
    for index in range(len(streams)):
        first = 2 + index * 8
        for sector in range(first, first + 8):
            fat[sector] = sector + 1 if sector < first + 7 else end

    def entry(name: str, kind: int, color: int, left: int = free, right: int = free, child: int = free, start: int = end, size: int = 0) -> bytes:
        result = bytearray(128)
        encoded = (name + "\0").encode("utf-16-le")
        result[:len(encoded)] = encoded
        struct.pack_into("<HBBIII", result, 64, len(encoded), kind, color, left, right, child)
        struct.pack_into("<IQ", result, 116, start, size)
        return bytes(result)

    directory = entry("Root Entry", 5, 1, child=2)
    directory += entry("Table", 2, 0, start=2, size=4096)
    directory += entry("Cabinet", 2, 1, left=1, right=3 if signature else free, start=10, size=4096)
    directory += entry("\u0005DigitalSignature", 2, 0, start=18, size=4096) if signature else bytes(128)
    return bytes(header) + struct.pack("<128I", *fat) + directory + b"".join(stream.ljust(4096, b"\0") for stream in streams)


class SignPathReleaseTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt" and shutil.which("pwsh"), "Windows structured-storage integration")
    def test_native_msi_stream_equivalence_accepts_signature_only_and_rejects_payload_change(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            unsigned, signed = Path(directory) / "unsigned.msi", Path(directory) / "signed.msi"
            unsigned.write_bytes(compound_storage())
            for modified in (False, True):
                signed.write_bytes(compound_storage(signature=True, modified=modified))
                result = subprocess.run(
                    [str(shutil.which("pwsh")), "-NoProfile", "-NonInteractive", "-File", str(signing.ROOT / "scripts/verify_msi_signing_payload.ps1"), "-UnsignedPath", str(unsigned), "-SignedPath", str(signed)],
                    capture_output=True, text=True, timeout=30, check=False,
                )
                self.assertEqual(result.returncode == 0, not modified, result.stderr)

    def test_preflight_rejects_pfx_missing_config_and_untrusted_execution(self) -> None:
        self.assertEqual(signing.preflight(environment())["revision"], REVISION)
        for field, value in (
            ("WINDOWS_SIGNING_PROVIDER", "pfx"), ("SIGNPATH_ORGANIZATION_ID", "bad"),
            ("WINDOWS_SIGNING_CERTIFICATE_SHA256", "0" * 64), ("SIGNPATH_API_TOKEN", ""),
            ("RUNNER_ENVIRONMENT", "self-hosted"), ("GITHUB_REF", "refs/heads/feature"),
            ("GITHUB_WORKFLOW_REF", "untrusted/workflow.yml@refs/heads/main"),
        ):
            values = {**environment(), field: value}
            with self.subTest(field=field), self.assertRaises(signing.SigningError):
                signing.preflight(values)
        self.assertEqual(len(signing_configuration_issues({})), 3)

    def test_origin_rejects_missing_other_revision_other_build_and_test_policy(self) -> None:
        context = signing.github_context(environment())
        signing.verify_request_origin(request_details(), context, configuration="market-sentinel-exe-v1", parameters=parameters())
        for mutate in (
            lambda value: value.pop("origin"),
            lambda value: value["origin"]["repositoryData"].update(commitId="c" * 40),
            lambda value: value["origin"]["repositoryData"].update(branchName="feature"),
            lambda value: value["origin"]["buildData"].update(url=f"https://github.com/{signing.REPOSITORY}/actions/runs/1234"),
            lambda value: value.update(signingPolicySlug="test-signing"),
            lambda value: value.update(isFinalStatus=1),
            lambda value: value["parameters"].update(nativeVersion="1.0.1"),
        ):
            value = deepcopy(request_details())
            mutate(value)
            with self.assertRaises(signing.SigningError):
                signing.verify_request_origin(value, context, configuration="market-sentinel-exe-v1", parameters=parameters())

    def test_provider_archive_rejects_traversal_extra_files_symlinks_and_bombs(self) -> None:
        self.assertEqual(signing.signed_archive_member(archive("app.exe", b"signed"), "app.exe"), b"signed")
        for name in ("../app.exe", "APP.exe", "directory/app.exe"):
            with self.subTest(name=name), self.assertRaises(signing.SigningError):
                signing.signed_archive_member(archive(name, b"signed"), "app.exe")
        stream = io.BytesIO()
        with ZipFile(stream, "w") as output:
            info = ZipInfo("app.exe")
            info.external_attr = 0o120777 << 16
            output.writestr(info, b"../other")
        with self.assertRaises(signing.SigningError):
            signing.signed_archive_member(stream.getvalue(), "app.exe")
        with patch.object(signing, "MAX_ARTIFACT_BYTES", 4), self.assertRaises(signing.SigningError):
            signing.signed_archive_member(archive("app.exe", b"large"), "app.exe")

    def test_pe_comparison_retains_pyinstaller_overlay_and_rejects_nonterminal_signature(self) -> None:
        original = unsigned_pe()
        signed = signed_pe(original)
        self.assertEqual(signing.pe_unsigned_payload(signed), original + b"\0" * ((-len(original)) % 8))
        with self.assertRaises(signing.SigningError):
            signing.pe_unsigned_payload(signed + b"appended")
        modified = bytearray(signed)
        modified[500] ^= 1
        self.assertNotEqual(signing.pe_unsigned_payload(bytes(modified)), signing.pe_unsigned_payload(signed))

    def test_action_output_parser_rejects_duplicate_multiline_or_invalid_request_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            output.write_text(f"signing-request-id<<delimiter\n{REQUEST_ID}\ndelimiter\n", encoding="utf-8")
            self.assertEqual(signing.signing_request_id(output), REQUEST_ID)
            for content in (
                f"signing-request-id={REQUEST_ID}\nsigning-request-id={REQUEST_ID}\n",
                "signing-request-id=not-an-id\n", "unknown=value\n",
                f"signing-request-id<<delimiter\n{REQUEST_ID}\nextra\ndelimiter\n",
            ):
                output.write_text(content, encoding="utf-8")
                with self.assertRaises(signing.SigningError):
                    signing.signing_request_id(output)

    def test_official_action_is_pinned_bounded_and_does_not_echo_provider_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            action = Path(directory) / "index.js"
            action.write_bytes(b"reviewed-action")
            output = Path(directory) / "output"
            def run(command, **kwargs):
                if command[-1] == "--version":
                    return subprocess.CompletedProcess(command, 0, stdout=b"v24.1.0\n")
                self.assertEqual(kwargs["stdout"], subprocess.DEVNULL)
                self.assertEqual(kwargs["stderr"], subprocess.DEVNULL)
                self.assertEqual(kwargs["timeout"], signing.PROVIDER_TIMEOUT_SECONDS)
                self.assertNotIn("NODE_OPTIONS", kwargs["env"])
                self.assertNotIn("provider-submitter-token", command)
                self.assertEqual(kwargs["env"]["INPUT_OUTPUT-ARTIFACT-DIRECTORY"], "")
                output.write_text(f"signing-request-id={REQUEST_ID}\n", encoding="utf-8")
                return subprocess.CompletedProcess(command, 0)
            with (
                patch.object(signing, "ACTION_SHA256", hashlib.sha256(action.read_bytes()).hexdigest()),
                patch.object(signing.shutil, "which", return_value="node.exe"),
                patch.object(signing.subprocess, "run", side_effect=run),
            ):
                identity = signing.invoke_official_action(action, output, {**environment(), "NODE_OPTIONS": "unreviewed-module"}, artifact_id=456, configuration="market-sentinel-exe-v1", parameters=parameters())
                self.assertEqual(identity, REQUEST_ID)
            with self.assertRaisesRegex(signing.SigningError, "digest"):
                signing.invoke_official_action(action, output, environment(), artifact_id=456, configuration="market-sentinel-exe-v1", parameters=parameters())

    def test_provider_failure_preserves_original_and_success_publishes_only_verified_bytes(self) -> None:
        context = signing.github_context(environment())
        artifact_name = f"unsigned-exe-{REVISION}-123-1"
        artifact = {"id": 456, "name": artifact_name, "expired": False, "digest": "sha256:" + "d" * 64, "size_in_bytes": 1000, "workflow_run": {"id": 123, "head_sha": REVISION}}
        run = {"id": 123, "run_attempt": 1, "head_sha": REVISION, "path": ".github/workflows/release.yml", "status": "in_progress", "repository": {"full_name": signing.REPOSITORY}}
        signing.verify_github_artifact(artifact, run, context, artifact_id=456, artifact_name=artifact_name, artifact_digest="d" * 64)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "market-sentinel.exe"
            original = unsigned_pe()
            signed = signed_pe(original)
            args = argparse.Namespace(kind="exe", path=target, version="1.0.12", tag="v1.0.12", artifact_id=456, artifact_name=artifact_name, artifact_digest="d" * 64)
            def read(url, **kwargs):
                if url == signing.ACTION_URL:
                    return b"reviewed-action"
                if url.endswith("/actions/artifacts/456"):
                    return json.dumps(artifact).encode()
                if url.endswith("/actions/runs/123"):
                    return json.dumps(run).encode()
                if url.endswith("/SignedArtifact"):
                    return archive(target.name, signed)
                return json.dumps(request_details()).encode()
            def git(command, **kwargs):
                return subprocess.CompletedProcess(command, 0, stdout=(REVISION + "\n").encode() if "rev-parse" in command else b"")
            for fail in (True, False):
                target.write_bytes(original)
                with (
                    patch.object(signing, "_read_url", side_effect=read),
                    patch.object(signing, "invoke_official_action", return_value=REQUEST_ID),
                    patch.object(signing.shutil, "which", return_value="git.exe"),
                    patch.object(signing.subprocess, "run", side_effect=git),
                    patch.object(signing, "verify_windows_signature", side_effect=signing.SigningError("Untrusted signer.") if fail else None) as verify,
                ):
                    if fail:
                        with self.assertRaises(signing.SigningError):
                            signing.sign_uploaded_artifact(args, environment())
                        self.assertEqual(target.read_bytes(), original)
                    else:
                        signing.sign_uploaded_artifact(args, environment())
                        self.assertEqual(target.read_bytes(), signed)
                        self.assertEqual(verify.call_count, 1)

    def test_signature_verification_is_bounded_secret_free_and_fail_closed(self) -> None:
        with (
            patch.dict(os.environ, {"SIGNPATH_API_TOKEN": "provider-secret", "WINDOWS_CODE_SIGNING_CERTIFICATE_PASSWORD": "pfx-secret"}),
            patch.object(signing.shutil, "which", return_value="pwsh.exe"),
            patch.object(signing.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, stdout="provider-secret")) as run,
            self.assertRaises(signing.SigningError) as caught,
        ):
            signing.verify_windows_signature(Path("application.exe"), "1.0.1299", environment())
        self.assertNotIn("provider-secret", str(caught.exception))
        self.assertEqual(run.call_args.kwargs["timeout"], 120)
        self.assertNotIn("SIGNPATH_API_TOKEN", run.call_args.kwargs["env"])
        self.assertNotIn("WINDOWS_CODE_SIGNING_CERTIFICATE_PASSWORD", run.call_args.kwargs["env"])
        self.assertIn("TimeStamperCertificate", signing.VERIFY_SIGNATURE_SCRIPT)
        self.assertIn("/pa /all /tw", signing.VERIFY_SIGNATURE_SCRIPT)

    def test_json_rejects_duplicate_keys_and_nonfinite_values(self) -> None:
        for raw in (b'{"status": "Completed", "status": "Failed"}', b'{"value": NaN}'):
            with self.assertRaises(signing.SigningError):
                signing._json(raw)


if __name__ == "__main__":
    unittest.main()
