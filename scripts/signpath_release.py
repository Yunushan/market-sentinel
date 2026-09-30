from __future__ import annotations

"""Sign uploaded release artifacts through SignPath's official GitHub connector.

The upstream action only submits the GitHub artifact ID and waits. We suppress
its potentially secret-bearing output, independently review its completed
request's origin, download from a fixed API origin, and verify the exact signer
and timestamp before atomically replacing the staged file. No PFX fallback or
generic artifact-upload API is available here.
"""

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import UUID
from zipfile import BadZipFile, ZipFile

try:
    from scripts.build_windows_release import msi_product_version
    from scripts.release_version import normalize_release_tag, normalize_release_version
    from scripts.windows_signing_policy import (
        SIGNPATH_ARTIFACT_CONFIGURATIONS,
        SIGNPATH_POLICY_SLUG,
        SIGNPATH_PROJECT_SLUG,
        signing_configuration_issues,
    )
except ModuleNotFoundError:
    from build_windows_release import msi_product_version
    from release_version import normalize_release_tag, normalize_release_version
    from windows_signing_policy import (
        SIGNPATH_ARTIFACT_CONFIGURATIONS,
        SIGNPATH_POLICY_SLUG,
        SIGNPATH_PROJECT_SLUG,
        signing_configuration_issues,
    )


REPOSITORY = "Yunushan/market-sentinel"
ACTION_COMMIT = "f6d04783b4569d051e0c80105fe66e82819d0092"
ACTION_SHA256 = "40af2e2581648d7357de3c13ca081ce87ac97f0aa66fdd2c5e3f1468d3232751"
ACTION_URL = f"https://raw.githubusercontent.com/SignPath/github-action-submit-signing-request/{ACTION_COMMIT}/index.js"
CONNECTOR_URL = "https://pipelineconnector.connectors.signpath.io/GitHubActions/GitHubCom"
MAX_ARTIFACT_BYTES = 256 * 1024 * 1024
MAX_JSON_BYTES = 1024 * 1024
PROVIDER_TIMEOUT_SECONDS = 900
ROOT = Path(__file__).resolve().parents[1]


class SigningError(RuntimeError):
    """A redacted, fail-closed release signing error."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req: Request, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        raise SigningError("Signing transport refused a redirect.")


def _read_url(url: str, *, token: str = "", maximum: int = MAX_JSON_BYTES) -> bytes:
    headers = {"User-Agent": "MarketSentinel-release-signing", "Accept-Encoding": "identity"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        deadline = time.monotonic() + 180
        with build_opener(_NoRedirect()).open(Request(url, headers=headers), timeout=30) as response:
            if response.status != 200:
                raise SigningError("Signing transport returned an unexpected status.")
            chunks: list[bytes] = []
            total = 0
            while chunk := response.read(min(64 * 1024, maximum + 1 - total)):
                if time.monotonic() > deadline:
                    raise SigningError("Signing transport exceeded its total response deadline.")
                total += len(chunk)
                if total > maximum:
                    raise SigningError("Signing transport exceeded the response size limit.")
                chunks.append(chunk)
            return b"".join(chunks)
    except (HTTPError, URLError, OSError, ValueError):
        raise SigningError("Signing transport failed; provider response and credentials were suppressed.") from None


def _json(raw: bytes) -> dict[str, Any]:
    if len(raw) > MAX_JSON_BYTES:
        raise SigningError("Signing JSON exceeded the response size limit.")

    def reject_constant(_value: str) -> None:
        raise SigningError("Signing JSON contains a non-finite value.")

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise SigningError("Signing JSON contains duplicate keys.")
            result[key] = value
        return result

    try:
        result = json.loads(raw, object_pairs_hook=pairs, parse_constant=reject_constant)
    except (UnicodeDecodeError, ValueError):
        raise SigningError("Signing JSON is malformed.") from None
    if not isinstance(result, dict):
        raise SigningError("Signing JSON must be an object.")
    return result


def github_context(environment: Mapping[str, str]) -> dict[str, str]:
    expected = {
        "GITHUB_ACTIONS": "true",
        "RUNNER_ENVIRONMENT": "github-hosted",
        "RUNNER_OS": "Windows",
        "GITHUB_REPOSITORY": REPOSITORY,
    }
    if any(environment.get(key) != value for key, value in expected.items()):
        raise SigningError("Release signing requires this repository's GitHub-hosted Windows workflow.")
    revision = environment.get("GITHUB_SHA", "")
    run_id = environment.get("GITHUB_RUN_ID", "")
    attempt = environment.get("GITHUB_RUN_ATTEMPT", "")
    ref = environment.get("GITHUB_REF", "")
    workflow_ref = environment.get("GITHUB_WORKFLOW_REF", "")
    if (
        not re.fullmatch(r"[0-9a-f]{40}", revision)
        or not re.fullmatch(r"[1-9][0-9]*", run_id)
        or not re.fullmatch(r"[1-9][0-9]*", attempt)
        or environment.get("GITHUB_EVENT_NAME") not in {"push", "workflow_dispatch"}
        or not (ref == "refs/heads/main" or ref.startswith("refs/tags/v"))
        or workflow_ref != f"{REPOSITORY}/.github/workflows/release.yml@{ref}"
    ):
        raise SigningError("Release signing source/run/workflow identity is invalid.")
    return {"revision": revision, "run_id": run_id, "attempt": attempt, "ref": ref}


def preflight(environment: Mapping[str, str]) -> dict[str, str]:
    issues = signing_configuration_issues(environment)
    if not environment.get("SIGNPATH_API_TOKEN", "").strip():
        issues.append("SIGNPATH_API_TOKEN")
    if issues:
        raise SigningError("Protected release signing configuration is missing or invalid: " + ", ".join(issues))
    return github_context(environment)


def verify_github_artifact(
    artifact: Mapping[str, Any], run: Mapping[str, Any], context: Mapping[str, str],
    *, artifact_id: int, artifact_name: str, artifact_digest: str,
) -> None:
    source = artifact.get("workflow_run")
    if (
        type(artifact.get("id")) is not int
        or artifact.get("id") != artifact_id
        or artifact.get("name") != artifact_name
        or artifact.get("expired") is not False
        or artifact.get("digest") != f"sha256:{artifact_digest}"
        or type(artifact.get("size_in_bytes")) is not int
        or not 0 < artifact["size_in_bytes"] <= MAX_ARTIFACT_BYTES
        or not isinstance(source, dict)
        or type(source.get("id")) is not int
        or source.get("id") != int(context["run_id"])
        or source.get("head_sha") != context["revision"]
        or type(run.get("id")) is not int
        or run.get("id") != int(context["run_id"])
        or type(run.get("run_attempt")) is not int
        or run.get("run_attempt") != int(context["attempt"])
        or run.get("head_sha") != context["revision"]
        or run.get("path") != ".github/workflows/release.yml"
        or run.get("status") != "in_progress"
        or not isinstance(run.get("repository"), dict)
        or run["repository"].get("full_name") != REPOSITORY
    ):
        raise SigningError("Uploaded signing artifact does not belong to this exact release invocation.")


def signing_request_id(output: Path) -> str:
    if output.is_symlink() or not output.is_file() or output.stat().st_size > 16 * 1024:
        raise SigningError("SignPath action output is missing or oversized.")
    lines = output.read_text(encoding="utf-8").splitlines()
    values: dict[str, str] = {}
    index = 0
    allowed = {"signing-request-id", "signing-request-web-url", "signed-artifact-download-url", "signpath-api-url"}
    while index < len(lines):
        line = lines[index]
        index += 1
        if not line:
            continue
        if "<<" in line:
            key, delimiter = line.split("<<", 1)
            if not delimiter or index + 1 >= len(lines) or lines[index + 1] != delimiter:
                raise SigningError("SignPath action output must contain only single-line values.")
            value = lines[index]
            index += 2
        elif "=" in line:
            key, value = line.split("=", 1)
        else:
            raise SigningError("SignPath action output format is invalid.")
        if key not in allowed or key in values:
            raise SigningError("SignPath action output contains unexpected or duplicate fields.")
        values[key] = value
    candidate = values.get("signing-request-id", "")
    try:
        parsed = UUID(candidate)
    except (ValueError, AttributeError):
        raise SigningError("SignPath signing request ID is invalid.") from None
    if str(parsed) != candidate or parsed.int == 0:
        raise SigningError("SignPath signing request ID is invalid.")
    return candidate


def verify_request_origin(
    request: Mapping[str, Any], context: Mapping[str, str], *, configuration: str, parameters: Mapping[str, str],
) -> None:
    origin = request.get("origin")
    repository = origin.get("repositoryData") if isinstance(origin, dict) else None
    build = origin.get("buildData") if isinstance(origin, dict) else None
    build_url = f"https://github.com/{REPOSITORY}/actions/runs/{context['run_id']}"
    if (
        request.get("status") != "Completed"
        or request.get("workflowStatus") != "Completed"
        or request.get("isFinalStatus") is not True
        or request.get("projectSlug") != SIGNPATH_PROJECT_SLUG
        or request.get("signingPolicySlug") != SIGNPATH_POLICY_SLUG
        or request.get("artifactConfigurationSlug") != configuration
        or request.get("parameters") != dict(parameters)
        or not isinstance(repository, dict)
        or repository.get("url") != f"https://github.com/{REPOSITORY}"
        or repository.get("commitId") != context["revision"]
        or repository.get("sourceControlManagementType") != "git"
        or repository.get("branchName") not in {context["ref"], context["ref"].removeprefix("refs/heads/").removeprefix("refs/tags/")}
        or not isinstance(build, dict)
        or build.get("url") not in {build_url, f"{build_url}/attempts/{context['attempt']}"}
    ):
        raise SigningError("Completed SignPath request failed exact source/build/policy/parameter verification.")


def signed_archive_member(raw: bytes, expected_name: str) -> bytes:
    if len(raw) > MAX_ARTIFACT_BYTES:
        raise SigningError("Signed artifact exceeds the download budget.")
    try:
        with ZipFile(io.BytesIO(raw)) as archive:
            entries = archive.infolist()
            if (
                len(entries) != 1
                or entries[0].filename != expected_name
                or entries[0].is_dir()
                or entries[0].flag_bits & 1
                or stat.S_ISLNK(entries[0].external_attr >> 16)
                or not 0 < entries[0].file_size <= MAX_ARTIFACT_BYTES
            ):
                raise SigningError("Signed artifact must contain exactly the expected ordinary release file.")
            with archive.open(entries[0]) as member:
                data = member.read(MAX_ARTIFACT_BYTES + 1)
            if len(data) != entries[0].file_size or len(data) > MAX_ARTIFACT_BYTES:
                raise SigningError("Signed artifact decompression exceeded its budget.")
            return data
    except (BadZipFile, OSError, RuntimeError, ValueError):
        raise SigningError("Signed artifact archive is malformed or unsafe.") from None


def pe_unsigned_payload(raw: bytes) -> bytes:
    """Normalize only PE checksum/security-directory/certificate-table bytes.

    Requiring all other bytes to stay identical includes PyInstaller's overlay,
    so a trusted signature cannot disguise changes to the bundled application.
    """
    try:
        pe = struct.unpack_from("<I", raw, 0x3C)[0]
        if raw[:2] != b"MZ" or raw[pe:pe + 4] != b"PE\0\0":
            raise ValueError
        optional = pe + 24
        magic = struct.unpack_from("<H", raw, optional)[0]
        if magic not in {0x10B, 0x20B}:
            raise ValueError
        optional_size = struct.unpack_from("<H", raw, pe + 20)[0]
        directory = optional + (96 if magic == 0x10B else 112) + 8 * 4
        if directory + 8 > optional + optional_size or optional + optional_size > len(raw):
            raise ValueError
        certificate_start, certificate_size = struct.unpack_from("<II", raw, directory)
        if bool(certificate_start) != bool(certificate_size):
            raise ValueError
        if certificate_start and (certificate_start % 8 or certificate_start + certificate_size != len(raw) or certificate_start < optional + optional_size):
            raise ValueError
        normalized = bytearray(raw[:certificate_start] if certificate_start else raw)
        normalized[optional + 64:optional + 68] = b"\0" * 4
        normalized[directory:directory + 8] = b"\0" * 8
        # Authenticode may add up to seven alignment bytes before its table.
        # The caller compares exact original length plus those zero bytes.
        return bytes(normalized)
    except (ValueError, struct.error, IndexError):
        raise SigningError("Release executable has an invalid or unsupported PE signature layout.") from None


VERIFY_SIGNATURE_SCRIPT = r'''
$ErrorActionPreference = "Stop"
$target = $env:MARKET_SENTINEL_SIGNING_VERIFY_TARGET
$signature = Get-AuthenticodeSignature -LiteralPath $target
if ($signature.Status -ne "Valid" -or $null -eq $signature.SignerCertificate -or $null -eq $signature.TimeStamperCertificate) {
    throw "A valid trusted signature and timestamp are required."
}
$signerDigest = [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($signature.SignerCertificate.RawData)).ToLowerInvariant()
if ($signerDigest -cne $env:WINDOWS_SIGNING_CERTIFICATE_SHA256) { throw "Unexpected signing certificate." }
$signTool = (Get-Command signtool.exe -ErrorAction Stop).Source
& $signTool verify /pa /all /tw $target
if ($LASTEXITCODE -ne 0) { throw "Authenticode or timestamp verification failed." }
$nativeVersion = $env:MARKET_SENTINEL_NATIVE_VERSION
if ($target.EndsWith('.exe', [StringComparison]::OrdinalIgnoreCase)) {
    $metadata = [Diagnostics.FileVersionInfo]::GetVersionInfo($target)
    if ($metadata.ProductName -cne 'MarketSentinel' -or $metadata.ProductVersion -cne $nativeVersion -or $metadata.FileVersion -cne $nativeVersion -or $metadata.OriginalFilename -cne 'market-sentinel.exe') {
        throw "Executable metadata does not match the reviewed release."
    }
} else {
    $installer = New-Object -ComObject WindowsInstaller.Installer
    $database = $installer.OpenDatabase($target, 0)
    $view = $database.OpenView('SELECT `Property`, `Value` FROM `Property`')
    $properties = @{}
    try {
        $view.Execute()
        while ($null -ne ($record = $view.Fetch())) {
            $name = $record.StringData(1)
            if ($name -in @('ProductName', 'ProductVersion')) {
                if ($properties.ContainsKey($name)) { throw "Duplicate installer metadata." }
                $properties[$name] = $record.StringData(2)
            }
        }
    } finally { $view.Close() }
    if ($properties['ProductName'] -cne 'MarketSentinel' -or $properties['ProductVersion'] -cne $nativeVersion) {
        throw "Installer metadata does not match the reviewed release."
    }
}
'''


def verify_windows_signature(target: Path, native_version: str, environment: Mapping[str, str]) -> None:
    executable = shutil.which("pwsh.exe") or shutil.which("pwsh")
    if not executable:
        raise SigningError("PowerShell 7 is required for release signature verification.")
    child_environment = os.environ.copy()
    for key in tuple(child_environment):
        if any(secret in key.upper() for secret in ("TOKEN", "PASSWORD", "CERTIFICATE_BASE64", "API_KEY", "PRIVATE_KEY")):
            child_environment.pop(key, None)
    child_environment.update({
        "MARKET_SENTINEL_SIGNING_VERIFY_TARGET": str(target),
        "MARKET_SENTINEL_NATIVE_VERSION": native_version,
        "WINDOWS_SIGNING_CERTIFICATE_SHA256": environment["WINDOWS_SIGNING_CERTIFICATE_SHA256"],
    })
    try:
        result = subprocess.run(
            [executable, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", VERIFY_SIGNATURE_SCRIPT],
            env=child_environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise SigningError("Windows signature verification failed or timed out; child output was suppressed.") from None
    if result.returncode != 0:
        raise SigningError("Windows signer, timestamp, trust, or release metadata verification failed.")


def verify_msi_payload(original: Path, signed: Path) -> None:
    executable = shutil.which("pwsh.exe") or shutil.which("pwsh") or shutil.which("powershell.exe")
    if not executable:
        raise SigningError("PowerShell is required for MSI payload verification.")
    try:
        result = subprocess.run(
            [executable, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(ROOT / "scripts/verify_msi_signing_payload.ps1"), "-UnsignedPath", str(original), "-SignedPath", str(signed)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise SigningError("MSI payload verification failed or timed out; child output was suppressed.") from None
    if result.returncode != 0:
        raise SigningError("MSI database, actions, metadata, or cabinet bytes changed during signing.")


def invoke_official_action(action: Path, output: Path, environment: Mapping[str, str], *, artifact_id: int, configuration: str, parameters: Mapping[str, str]) -> str:
    if hashlib.sha256(action.read_bytes()).hexdigest() != ACTION_SHA256:
        raise SigningError("Official SignPath integration digest does not match the reviewed pin.")
    node = shutil.which("node.exe") or shutil.which("node")
    if not node:
        raise SigningError("Node.js 24 is required for the reviewed SignPath integration.")
    try:
        version = subprocess.run([node, "--version"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        raise SigningError("Node.js runtime could not be verified.") from None
    if not re.fullmatch(rb"v24\.[0-9]+\.[0-9]+\s*", version):
        raise SigningError("Node.js 24 is required for the reviewed SignPath integration.")
    # The exact bundle only submits a stored GitHub artifact; it cannot receive
    # a local artifact path. Keep provider output/URLs away from workflow logs.
    child_environment = {key: value for key, value in environment.items() if not key.startswith("INPUT_")}
    child_environment.pop("NODE_OPTIONS", None)
    child_environment.pop("NODE_DEBUG", None)
    child_environment.pop("NODE_TLS_REJECT_UNAUTHORIZED", None)
    child_environment.pop("NODE_EXTRA_CA_CERTS", None)
    child_environment.update({
        "GITHUB_OUTPUT": str(output),
        "INPUT_CONNECTOR-URL": CONNECTOR_URL,
        "INPUT_API-TOKEN": environment["SIGNPATH_API_TOKEN"],
        "INPUT_ORGANIZATION-ID": environment["SIGNPATH_ORGANIZATION_ID"],
        "INPUT_PROJECT-SLUG": SIGNPATH_PROJECT_SLUG,
        "INPUT_SIGNING-POLICY-SLUG": SIGNPATH_POLICY_SLUG,
        "INPUT_ARTIFACT-CONFIGURATION-SLUG": configuration,
        "INPUT_GITHUB-ARTIFACT-ID": str(artifact_id),
        "INPUT_GITHUB-TOKEN": environment["GITHUB_TOKEN"],
        "INPUT_WAIT-FOR-COMPLETION": "true",
        "INPUT_WAIT-FOR-COMPLETION-TIMEOUT-IN-SECONDS": "600",
        "INPUT_SERVICE-UNAVAILABLE-TIMEOUT-IN-SECONDS": "30",
        "INPUT_DOWNLOAD-SIGNED-ARTIFACT-TIMEOUT-IN-SECONDS": "30",
        "INPUT_OUTPUT-ARTIFACT-DIRECTORY": "",
        "INPUT_PARAMETERS": "\n".join(f"{key}: {json.dumps(value)}" for key, value in parameters.items()),
    })
    output.touch(exist_ok=False)
    try:
        result = subprocess.run([node, str(action)], env=child_environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=PROVIDER_TIMEOUT_SECONDS, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise SigningError("SignPath connector failed or timed out; provider output was suppressed. Reconcile the request before retrying.") from None
    if result.returncode != 0:
        raise SigningError("SignPath connector did not complete; provider output was suppressed. Inspect the provider dashboard before retrying.")
    return signing_request_id(output)


def sign_uploaded_artifact(args: argparse.Namespace, environment: Mapping[str, str]) -> None:
    context = preflight(environment)
    version = normalize_release_version(args.version)
    tag = args.tag
    if normalize_release_tag(tag) != version:
        raise SigningError("Signing release tag and version do not match.")
    if context["ref"].startswith("refs/tags/") and context["ref"] != f"refs/tags/{tag}":
        raise SigningError("Signing release tag does not match the workflow source ref.")
    if not re.fullmatch(r"[0-9a-f]{64}", args.artifact_digest):
        raise SigningError("Uploaded signing artifact digest is invalid.")
    expected_name = f"unsigned-{args.kind}-{context['revision']}-{context['run_id']}-{context['attempt']}"
    if args.artifact_name != expected_name or args.artifact_id <= 0:
        raise SigningError("Uploaded signing artifact identity is invalid.")
    target = args.path
    if target.is_symlink() or not target.is_file() or not 0 < target.stat().st_size <= MAX_ARTIFACT_BYTES:
        raise SigningError("Signing target must be a bounded ordinary release file.")
    if target.suffix.lower() != f".{args.kind}":
        raise SigningError("Signing target kind is invalid.")
    original = target.read_bytes()
    git = shutil.which("git.exe") or shutil.which("git")
    if not git:
        raise SigningError("Git is required to bind release signing to clean committed source.")
    try:
        revision = subprocess.run([git, "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, timeout=10, check=True).stdout.decode().strip()
        dirty = subprocess.run([git, "status", "--porcelain=v1", "--untracked-files=all"], cwd=ROOT, capture_output=True, timeout=10, check=True).stdout
    except (OSError, ValueError, subprocess.SubprocessError):
        raise SigningError("Release source identity could not be verified.") from None
    if revision != context["revision"] or dirty:
        raise SigningError("Release signing requires an unchanged clean exact source revision.")
    github_token = environment.get("GITHUB_TOKEN", "")
    if not github_token:
        raise SigningError("A narrowly scoped GitHub artifact-read token is required.")
    api = f"https://api.github.com/repos/{REPOSITORY}"
    artifact = _json(_read_url(f"{api}/actions/artifacts/{args.artifact_id}", token=github_token))
    run = _json(_read_url(f"{api}/actions/runs/{context['run_id']}", token=github_token))
    verify_github_artifact(artifact, run, context, artifact_id=args.artifact_id, artifact_name=expected_name, artifact_digest=args.artifact_digest)
    configuration = SIGNPATH_ARTIFACT_CONFIGURATIONS[args.kind]
    native_version = msi_product_version(version)
    parameters = {"nativeVersion": native_version, "releaseTag": tag, "sourceRevision": context["revision"]}
    with tempfile.TemporaryDirectory(prefix="market-sentinel-signpath-") as temporary:
        directory = Path(temporary)
        action = directory / "index.js"
        action.write_bytes(_read_url(ACTION_URL, maximum=2 * MAX_JSON_BYTES))
        request_id = invoke_official_action(action, directory / "action-output", environment, artifact_id=args.artifact_id, configuration=configuration, parameters=parameters)
        base = f"https://app.signpath.io/Api/v1/{environment['SIGNPATH_ORGANIZATION_ID']}/SigningRequests/{request_id}"
        provider_request = _json(_read_url(base, token=environment["SIGNPATH_API_TOKEN"]))
        verify_request_origin(provider_request, context, configuration=configuration, parameters=parameters)
        signed = signed_archive_member(_read_url(f"{base}/SignedArtifact", token=environment["SIGNPATH_API_TOKEN"], maximum=MAX_ARTIFACT_BYTES), target.name)
        if args.kind == "exe":
            before = pe_unsigned_payload(original)
            after = pe_unsigned_payload(signed)
            if after != before + b"\0" * ((-len(before)) % 8):
                raise SigningError("Signed executable changed bytes outside its Authenticode fields.")
        candidate = directory / target.name
        candidate.write_bytes(signed)
        verify_windows_signature(candidate, native_version, environment)
        if args.kind == "msi":
            unsigned_candidate = directory / "unsigned.msi"
            unsigned_candidate.write_bytes(original)
            verify_msi_payload(unsigned_candidate, candidate)
        try:
            final_revision = subprocess.run([git, "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, timeout=10, check=True).stdout.decode().strip()
            final_dirty = subprocess.run([git, "status", "--porcelain=v1", "--untracked-files=all"], cwd=ROOT, capture_output=True, timeout=10, check=True).stdout
        except (OSError, ValueError, subprocess.SubprocessError):
            raise SigningError("Release source identity could not be reverified after signing.") from None
        if final_revision != revision or final_dirty:
            raise SigningError("Release source changed while its signing request was pending.")
        if target.read_bytes() != original:
            raise SigningError("Release signing target changed while its request was pending.")
        # Place the replacement on the target's volume for atomic publication.
        with tempfile.NamedTemporaryFile(prefix=f".{target.name}.", suffix=".signed", dir=target.parent, delete=False) as stream:
            replacement = Path(stream.name)
            stream.write(signed)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.replace(replacement, target)
        finally:
            replacement.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("preflight")
    sign = commands.add_parser("sign")
    sign.add_argument("--path", type=Path, required=True)
    sign.add_argument("--kind", choices=tuple(SIGNPATH_ARTIFACT_CONFIGURATIONS), required=True)
    sign.add_argument("--version", required=True)
    sign.add_argument("--tag", required=True)
    sign.add_argument("--artifact-id", type=int, required=True)
    sign.add_argument("--artifact-name", required=True)
    sign.add_argument("--artifact-digest", required=True)
    args = parser.parse_args()
    try:
        if args.command == "preflight":
            preflight(os.environ)
        else:
            sign_uploaded_artifact(args, os.environ)
    except SigningError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except (OSError, ValueError, subprocess.SubprocessError):
        print("Release signing failed; diagnostic values and provider output were suppressed.", file=sys.stderr)
        return 1
    print("[ok] Reviewed SignPath release signing contract passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
