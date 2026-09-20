from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts.platform_ci_receipt import _attestation_matches, collect_receipts, generate_receipt
from scripts.trusted_readiness_evidence import (
    PLATFORM_RECEIPT_SUBJECT_NAME,
    PLATFORM_SOURCE_WORKFLOW,
    REPOSITORY,
    TRUSTED_REF,
    TrustedEvidenceError,
    canonical_json_bytes,
)


REVISION = "a" * 40
RUN_ID = 123
RUN_ATTEMPT = 2


def generate_args(directory: Path, *, runner_environment: str = "github-hosted") -> SimpleNamespace:
    github_output = directory / "github-output.txt"
    github_output.write_text("", encoding="utf-8")
    return SimpleNamespace(
        repository=REPOSITORY,
        source_revision=REVISION,
        run_id=RUN_ID,
        run_attempt=RUN_ATTEMPT,
        workflow_ref=f"{REPOSITORY}/.github/workflows/ci.yml@{TRUSTED_REF}",
        event="push",
        job_key="package",
        matrix_os="",
        matrix_python_version="",
        matrix_name="",
        matrix_target="",
        runner_environment=runner_environment,
        runner_os="Linux",
        runner_arch="X64",
        output=directory / PLATFORM_RECEIPT_SUBJECT_NAME,
        github_output=github_output,
    )


def attestation_for(receipt: dict[str, object], *, now: datetime) -> list[dict[str, object]]:
    workflow_ref = str(receipt["workflow_ref"])
    workflow_uri = f"https://github.com/{workflow_ref}"
    repository_uri = f"https://github.com/{REPOSITORY}"
    invocation_uri = (
        f"{repository_uri}/actions/runs/{receipt['run_id']}/attempts/{receipt['run_attempt']}"
    )
    return [
        {
            "attestation": {"bundle": "verified"},
            "verificationResult": {
                "mediaType": "application/vnd.dev.sigstore.verificationresult+json;version=0.1",
                "verifiedTimestamps": [
                    {"timestamp": (now - timedelta(minutes=1)).isoformat().replace("+00:00", "Z")}
                ],
                "signature": {
                    "certificate": {
                        "subjectAlternativeName": workflow_uri,
                        "issuer": "https://token.actions.githubusercontent.com",
                        "buildSignerURI": workflow_uri,
                        "buildSignerDigest": REVISION,
                        "runnerEnvironment": "github-hosted",
                        "sourceRepositoryURI": repository_uri,
                        "sourceRepositoryDigest": REVISION,
                        "sourceRepositoryRef": TRUSTED_REF,
                        "sourceRepositoryOwnerURI": "https://github.com/Yunushan",
                        "buildConfigURI": workflow_uri,
                        "buildConfigDigest": REVISION,
                        "buildTrigger": "push",
                        "runInvocationURI": invocation_uri,
                        "sourceRepositoryVisibilityAtSigning": "public",
                    }
                },
                "statement": {
                    "_type": "https://in-toto.io/Statement/v1",
                    "predicateType": "https://slsa.dev/provenance/v1",
                    "subject": [
                        {
                            "name": PLATFORM_RECEIPT_SUBJECT_NAME,
                            "digest": {
                                "sha256": hashlib.sha256(canonical_json_bytes(receipt)).hexdigest()
                            },
                        }
                    ],
                    "predicate": {
                        "buildDefinition": {
                            "buildType": "https://actions.github.io/buildtypes/workflow/v1",
                            "externalParameters": {
                                "workflow": {
                                    "path": PLATFORM_SOURCE_WORKFLOW,
                                    "ref": TRUSTED_REF,
                                    "repository": repository_uri,
                                }
                            },
                            "internalParameters": {
                                "github": {
                                    "event_name": "push",
                                    "runner_environment": "github-hosted",
                                }
                            },
                            "resolvedDependencies": [
                                {
                                    "uri": f"git+{repository_uri}@{TRUSTED_REF}",
                                    "digest": {"gitCommit": REVISION},
                                }
                            ],
                        },
                        "runDetails": {
                            "builder": {"id": workflow_uri},
                            "metadata": {"invocationId": invocation_uri},
                        },
                    },
                },
            },
        }
    ]


class PlatformCiReceiptTests(unittest.TestCase):
    def test_generate_writes_canonical_run_attempt_and_job_bound_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            args = generate_args(directory)
            receipt = generate_receipt(args)

            self.assertEqual(args.output.read_bytes(), canonical_json_bytes(receipt))
            self.assertEqual(receipt["run_id"], RUN_ID)
            self.assertEqual(receipt["run_attempt"], RUN_ATTEMPT)
            self.assertEqual(receipt["job_key"], "package")
            self.assertEqual(receipt["job_name"], "Python package build")
            self.assertEqual(receipt["matrix"], {})
            self.assertEqual(receipt["runner_environment"], "github-hosted")
            self.assertIn(f"platform-ci-receipt-{RUN_ID}-{RUN_ATTEMPT}-", receipt["artifact_name"])
            outputs = args.github_output.read_text(encoding="utf-8")
            self.assertIn(f"artifact-name={receipt['artifact_name']}", outputs)
            self.assertIn(f"identity-sha256={receipt['identity_sha256']}", outputs)
            self.assertIn("receipt-sha256=", outputs)

    def test_generate_rejects_self_hosted_and_incomplete_matrix_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            with self.assertRaisesRegex(TrustedEvidenceError, "GitHub-hosted"):
                generate_receipt(generate_args(directory, runner_environment="self-hosted"))

            args = generate_args(directory)
            args.job_key = "python"
            args.matrix_os = "ubuntu-latest"
            with self.assertRaisesRegex(TrustedEvidenceError, "matrix arguments"):
                generate_receipt(args)

            args = generate_args(directory)
            args.event = "workflow_dispatch"
            with self.assertRaisesRegex(TrustedEvidenceError, "protected-main push"):
                generate_receipt(args)

    def test_collect_verifies_attestations_and_rejects_replayed_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            source = directory / "source"
            artifact = source / "artifact-1"
            attestations = directory / "attestations"
            artifact.mkdir(parents=True)
            attestations.mkdir()
            args = generate_args(artifact)
            receipt = generate_receipt(args)
            identity = str(receipt["identity_sha256"])
            (attestations / f"{identity}.json").write_text("[]\n", encoding="utf-8")
            output = directory / "receipts.json"
            collect_args = SimpleNamespace(root=source, attestations=attestations, output=output)

            with patch("scripts.platform_ci_receipt._attestation_matches", return_value=True):
                receipts = collect_receipts(collect_args)
            self.assertEqual(receipts, [receipt])
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), [receipt])

            replay = source / "artifact-2"
            replay.mkdir()
            (replay / PLATFORM_RECEIPT_SUBJECT_NAME).write_bytes(canonical_json_bytes(receipt))
            with (
                patch("scripts.platform_ci_receipt._attestation_matches", return_value=True),
                self.assertRaisesRegex(TrustedEvidenceError, "duplicated or reused"),
            ):
                collect_receipts(collect_args)

    def test_collect_fails_closed_when_attestation_is_not_exactly_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            source = directory / "source" / "artifact"
            attestations = directory / "attestations"
            source.mkdir(parents=True)
            attestations.mkdir()
            args = generate_args(source)
            receipt = generate_receipt(args)
            identity = str(receipt["identity_sha256"])
            (attestations / f"{identity}.json").write_text("[]\n", encoding="utf-8")
            with (
                patch("scripts.platform_ci_receipt._attestation_matches", return_value=False),
                self.assertRaisesRegex(TrustedEvidenceError, "attestation was rejected"),
            ):
                collect_receipts(
                    SimpleNamespace(
                        root=directory / "source",
                        attestations=attestations,
                        output=directory / "receipts.json",
                    )
                )

    def test_attestation_matcher_requires_one_exact_hosted_source_invocation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            receipt = generate_receipt(generate_args(Path(temporary)))
        now = datetime.now(timezone.utc)
        result = attestation_for(receipt, now=now)
        self.assertTrue(_attestation_matches(receipt, result, now=now))

        spoofed = deepcopy(result)
        spoofed[0]["verificationResult"]["signature"]["certificate"][  # type: ignore[index]
            "runnerEnvironment"
        ] = "self-hosted"
        self.assertFalse(_attestation_matches(receipt, spoofed, now=now))
        self.assertFalse(_attestation_matches(receipt, [*result, *result], now=now))


if __name__ == "__main__":
    unittest.main()
