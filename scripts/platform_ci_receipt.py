from __future__ import annotations

"""Generate and review per-job GitHub-hosted platform provenance receipts."""

import argparse
import hashlib
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

try:
    from scripts.trusted_readiness_evidence import (
        PLATFORM_RECEIPT_SUBJECT_NAME,
        PLATFORM_SOURCE_WORKFLOW,
        MAX_AGE_HOURS,
        MAX_FUTURE_SKEW_SECONDS,
        REPOSITORY,
        TRUSTED_REF,
        TrustedEvidenceError,
        _COMMIT_RE,
        _HASH_RE,
        build_platform_job_receipt,
        canonical_json_bytes,
        load_strict_json,
        write_manifest,
    )
except ModuleNotFoundError:
    from trusted_readiness_evidence import (  # type: ignore[no-redef]
        PLATFORM_RECEIPT_SUBJECT_NAME,
        PLATFORM_SOURCE_WORKFLOW,
        MAX_AGE_HOURS,
        MAX_FUTURE_SKEW_SECONDS,
        REPOSITORY,
        TRUSTED_REF,
        TrustedEvidenceError,
        _COMMIT_RE,
        _HASH_RE,
        build_platform_job_receipt,
        canonical_json_bytes,
        load_strict_json,
        write_manifest,
    )


def _matrix_from_args(args: argparse.Namespace) -> dict[str, str]:
    supplied = {
        "os": args.matrix_os,
        "python-version": args.matrix_python_version,
        "name": args.matrix_name,
        "target": args.matrix_target,
    }
    expected_keys = {
        "python": {"os", "python-version"},
        "future-python": {"os", "python-version"},
        "enterprise-linux": {"name"},
        "mobile-web": {"target"},
        "package": set(),
        "windows-11": set(),
        "frontend": set(),
        "tkinter-gui-lifecycle": set(),
    }.get(args.job_key)
    if expected_keys is None:
        raise TrustedEvidenceError("platform receipt job key is not recognized")
    observed_keys = {key for key, value in supplied.items() if value not in {None, ""}}
    if observed_keys != expected_keys:
        raise TrustedEvidenceError("platform receipt matrix arguments do not match the job key")
    return {key: str(value) for key, value in supplied.items() if key in expected_keys}


def _write_github_output(path: Path, receipt: Mapping[str, Any], output_path: Path) -> None:
    if path.is_symlink() or not path.is_file():
        raise TrustedEvidenceError("GITHUB_OUTPUT must be a regular non-symbolic-link file")
    values = {
        "artifact-name": receipt["artifact_name"],
        "identity-sha256": receipt["identity_sha256"],
        "receipt-sha256": hashlib.sha256(canonical_json_bytes(dict(receipt))).hexdigest(),
        "receipt-path": str(output_path),
    }
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        for key, value in values.items():
            text = str(value)
            if not text or "\n" in text or "\r" in text:
                raise TrustedEvidenceError("platform receipt GitHub output is not a safe single-line value")
            handle.write(f"{key}={text}\n")


def generate_receipt(args: argparse.Namespace) -> dict[str, Any]:
    output = args.output.resolve()
    if output.name != PLATFORM_RECEIPT_SUBJECT_NAME:
        raise TrustedEvidenceError(f"platform receipt output must be named {PLATFORM_RECEIPT_SUBJECT_NAME}")
    receipt = build_platform_job_receipt(
        repository=args.repository,
        source_revision=args.source_revision,
        run_id=args.run_id,
        run_attempt=args.run_attempt,
        workflow_ref=args.workflow_ref,
        event=args.event,
        job_key=args.job_key,
        matrix=_matrix_from_args(args),
        runner_environment=args.runner_environment,
        runner_os=args.runner_os,
        runner_arch=args.runner_arch,
    )
    write_manifest(output, receipt)
    if args.github_output is not None:
        _write_github_output(args.github_output.resolve(), receipt, output)
    return receipt


def _receipt_files(root: Path) -> list[Path]:
    if root.is_symlink() or not root.is_dir():
        raise TrustedEvidenceError("platform receipt root must be a regular directory")
    paths = sorted(root.rglob(PLATFORM_RECEIPT_SUBJECT_NAME))
    if not paths:
        raise TrustedEvidenceError("no platform source-job receipts were downloaded")
    for path in paths:
        if path.is_symlink() or not path.is_file():
            raise TrustedEvidenceError("platform source-job receipt must be a regular non-symbolic-link file")
        if path.parent.is_symlink():
            raise TrustedEvidenceError("platform source-job receipt parent must not be a symbolic link")
    return paths


def _receipt_identity(receipt: Any) -> str:
    if not isinstance(receipt, Mapping):
        raise TrustedEvidenceError("platform source-job receipt must be an object")
    identity = receipt.get("identity_sha256")
    if not isinstance(identity, str) or not _HASH_RE.fullmatch(identity):
        raise TrustedEvidenceError("platform source-job receipt identity digest is invalid")
    return identity


def _recent_attestation_timestamp(value: Any, *, now: datetime) -> bool:
    if not isinstance(value, str) or not value.strip() or "\n" in value or "\r" in value:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return False
    age = now.astimezone(timezone.utc) - parsed.astimezone(timezone.utc)
    return -timedelta(seconds=MAX_FUTURE_SKEW_SECONDS) <= age <= timedelta(hours=MAX_AGE_HOURS)


def _attestation_matches(receipt: Mapping[str, Any], result: Any, *, now: datetime) -> bool:
    raw = canonical_json_bytes(dict(receipt))
    report_hash = hashlib.sha256(raw).hexdigest()
    revision = receipt.get("source_revision")
    workflow_ref = receipt.get("workflow_ref")
    run_id = receipt.get("run_id")
    run_attempt = receipt.get("run_attempt")
    event = receipt.get("event")
    if (
        not isinstance(result, list)
        or not isinstance(revision, str)
        or _COMMIT_RE.fullmatch(revision) is None
        or workflow_ref != f"{REPOSITORY}/{PLATFORM_SOURCE_WORKFLOW}@{TRUSTED_REF}"
        or type(run_id) is not int
        or run_id <= 0
        or type(run_attempt) is not int
        or run_attempt <= 0
        or event != "push"
    ):
        return False
    ref = TRUSTED_REF
    workflow_uri = f"https://github.com/{workflow_ref}"
    repository_uri = f"https://github.com/{REPOSITORY}"
    invocation_uri = f"{repository_uri}/actions/runs/{run_id}/attempts/{run_attempt}"
    owner = REPOSITORY.split("/", 1)[0]
    matches: list[Any] = []
    for item in result:
        if not isinstance(item, Mapping) or not isinstance(item.get("attestation"), Mapping) or not item["attestation"]:
            continue
        verification = item.get("verificationResult")
        if not isinstance(verification, Mapping) or verification.get("mediaType") != (
            "application/vnd.dev.sigstore.verificationresult+json;version=0.1"
        ):
            continue
        statement = verification.get("statement")
        signature = verification.get("signature")
        if not isinstance(statement, Mapping) or not isinstance(signature, Mapping):
            continue
        subjects = statement.get("subject")
        if (
            statement.get("_type") != "https://in-toto.io/Statement/v1"
            or statement.get("predicateType") != "https://slsa.dev/provenance/v1"
            or not isinstance(subjects, list)
            or len(subjects) != 1
            or not isinstance(subjects[0], Mapping)
            or subjects[0].get("name") != PLATFORM_RECEIPT_SUBJECT_NAME
            or subjects[0].get("digest") != {"sha256": report_hash}
        ):
            continue
        certificate = signature.get("certificate")
        certificate_contract = {
            "subjectAlternativeName": workflow_uri,
            "issuer": "https://token.actions.githubusercontent.com",
            "buildSignerURI": workflow_uri,
            "buildSignerDigest": revision,
            "runnerEnvironment": "github-hosted",
            "sourceRepositoryURI": repository_uri,
            "sourceRepositoryDigest": revision,
            "sourceRepositoryRef": ref,
            "sourceRepositoryOwnerURI": f"https://github.com/{owner}",
            "buildConfigURI": workflow_uri,
            "buildConfigDigest": revision,
            "buildTrigger": event,
            "runInvocationURI": invocation_uri,
            "sourceRepositoryVisibilityAtSigning": "public",
        }
        if not isinstance(certificate, Mapping) or any(
            certificate.get(key) != value for key, value in certificate_contract.items()
        ):
            continue
        timestamps = verification.get("verifiedTimestamps")
        if (
            not isinstance(timestamps, list)
            or not timestamps
            or any(
                not isinstance(timestamp, Mapping)
                or not _recent_attestation_timestamp(timestamp.get("timestamp"), now=now)
                for timestamp in timestamps
            )
        ):
            continue
        predicate = statement.get("predicate")
        build_definition = predicate.get("buildDefinition") if isinstance(predicate, Mapping) else None
        run_details = predicate.get("runDetails") if isinstance(predicate, Mapping) else None
        if not isinstance(build_definition, Mapping) or not isinstance(run_details, Mapping):
            continue
        if build_definition.get("buildType") != "https://actions.github.io/buildtypes/workflow/v1":
            continue
        external = build_definition.get("externalParameters")
        internal = build_definition.get("internalParameters")
        dependencies = build_definition.get("resolvedDependencies")
        workflow = external.get("workflow") if isinstance(external, Mapping) else None
        github = internal.get("github") if isinstance(internal, Mapping) else None
        if (
            workflow != {"path": PLATFORM_SOURCE_WORKFLOW, "ref": ref, "repository": repository_uri}
            or not isinstance(github, Mapping)
            or github.get("event_name") != event
            or github.get("runner_environment") != "github-hosted"
            or not isinstance(dependencies, list)
        ):
            continue
        dependency_uri = f"git+{repository_uri}@{ref}"
        matching_dependencies = [
            dependency
            for dependency in dependencies
            if isinstance(dependency, Mapping)
            and dependency.get("uri") == dependency_uri
            and dependency.get("digest") == {"gitCommit": revision}
        ]
        builder = run_details.get("builder")
        metadata = run_details.get("metadata")
        if (
            len(matching_dependencies) != 1
            or not isinstance(builder, Mapping)
            or builder.get("id") != workflow_uri
            or not isinstance(metadata, Mapping)
            or metadata.get("invocationId") != invocation_uri
        ):
            continue
        matches.append(item)
    return len(matches) == 1


def collect_receipts(args: argparse.Namespace) -> list[dict[str, Any]]:
    receipts: list[dict[str, Any]] = []
    identities: set[str] = set()
    names: set[str] = set()
    current = datetime.now(timezone.utc)
    for path in _receipt_files(args.root.resolve()):
        raw = path.read_bytes()
        receipt = load_strict_json(path)
        if not isinstance(receipt, Mapping) or raw != canonical_json_bytes(dict(receipt)):
            raise TrustedEvidenceError("platform source-job receipt is not canonical JSON")
        identity = _receipt_identity(receipt)
        name = receipt.get("job_name")
        if identity in identities or not isinstance(name, str) or name in names:
            raise TrustedEvidenceError("platform source-job receipt identity was duplicated or reused")
        if args.attestations is not None:
            attestation_path = args.attestations.resolve() / f"{identity}.json"
            result = load_strict_json(attestation_path)
            if not _attestation_matches(receipt, result, now=current):
                raise TrustedEvidenceError(f"platform source-job receipt attestation was rejected for {name}")
        identities.add(identity)
        names.add(name)
        receipts.append(dict(receipt))
    receipts.sort(key=lambda item: str(item.get("job_name") or ""))
    raw = (json.dumps(receipts, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
    _atomic_write(args.output.resolve(), raw)
    return receipts


def _atomic_write(path: Path, raw: bytes) -> None:
    if not path.parent.is_dir() or path.is_symlink():
        raise TrustedEvidenceError("output parent must exist and output must not be a symbolic link")
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            if os.name == "posix":
                os.fchmod(handle.fileno(), 0o600)
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    generate = commands.add_parser("generate")
    generate.add_argument("--repository", required=True)
    generate.add_argument("--source-revision", required=True)
    generate.add_argument("--run-id", required=True, type=int)
    generate.add_argument("--run-attempt", required=True, type=int)
    generate.add_argument("--workflow-ref", required=True)
    generate.add_argument("--event", required=True)
    generate.add_argument("--job-key", required=True)
    generate.add_argument("--matrix-os")
    generate.add_argument("--matrix-python-version")
    generate.add_argument("--matrix-name")
    generate.add_argument("--matrix-target")
    generate.add_argument("--runner-environment", required=True)
    generate.add_argument("--runner-os", required=True)
    generate.add_argument("--runner-arch", required=True)
    generate.add_argument("--output", required=True, type=Path)
    generate.add_argument("--github-output", type=Path)

    identity = commands.add_parser("identity")
    identity.add_argument("--input", required=True, type=Path)
    identity.add_argument("--expected-identity")
    identity.add_argument("--expected-sha256")

    collect = commands.add_parser("collect")
    collect.add_argument("--root", required=True, type=Path)
    collect.add_argument("--attestations", type=Path)
    collect.add_argument("--output", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "generate":
            receipt = generate_receipt(args)
            result: dict[str, Any] = {
                "ok": True,
                "artifact_name": receipt["artifact_name"],
                "identity_sha256": receipt["identity_sha256"],
            }
        elif args.command == "identity":
            receipt = load_strict_json(args.input)
            if not isinstance(receipt, Mapping) or args.input.read_bytes() != canonical_json_bytes(dict(receipt)):
                raise TrustedEvidenceError("platform source-job receipt is not canonical JSON")
            receipt_identity = _receipt_identity(receipt)
            if args.expected_identity is not None and args.expected_identity != receipt_identity:
                raise TrustedEvidenceError("platform source-job receipt identity changed after generation")
            receipt_sha256 = hashlib.sha256(args.input.read_bytes()).hexdigest()
            if args.expected_sha256 is not None and args.expected_sha256 != receipt_sha256:
                raise TrustedEvidenceError("platform source-job receipt bytes changed after generation")
            result = {
                "ok": True,
                "identity_sha256": receipt_identity,
                "receipt_sha256": receipt_sha256,
            }
        else:
            receipts = collect_receipts(args)
            result = {"ok": True, "receipt_count": len(receipts)}
    except (OSError, ValueError, TrustedEvidenceError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True, separators=(",", ":")))
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
