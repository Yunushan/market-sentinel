from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


class CiCdWorkflowTests(unittest.TestCase):
    def test_ci_workflow_covers_python_frontend_and_artifacts(self) -> None:
        text = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

        for fragment in (
            "permissions:",
            "contents: read",
            "concurrency:",
            "cancel-in-progress: true",
            "ubuntu-latest",
            "windows-latest",
            '"3.10"',
            '"3.11"',
            '"3.12"',
            '"3.13"',
            '"3.14"',
            "actions/setup-python@v5",
            "actions/setup-node@v4",
            "actions/upload-artifact@v4",
            "python app.py --smoke-test",
            "python verify.py",
            "npm run build",
            "npm install --no-audit --no-fund",
            "python -m build",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, text)

    def test_release_workflow_publishes_checked_and_checksummed_assets(self) -> None:
        text = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

        for fragment in (
            '"v*.*.*"',
            "workflow_dispatch:",
            "environment: release",
            "contents: write",
            "python app.py --smoke-test",
            "python verify.py",
            "python -m build",
            "npm run build",
            "actions/download-artifact@v4",
            "sha256sum * > SHA256SUMS.txt",
            "gh release create",
            "gh release upload",
            "--target \"${GITHUB_SHA}\"",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, text)

    def test_security_and_dependabot_automation_are_configured(self) -> None:
        security = (ROOT / ".github" / "workflows" / "security.yml").read_text(encoding="utf-8")
        dependabot = (ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")

        for fragment in (
            "actions/dependency-review-action@v4",
            "github/codeql-action/init@v3",
            "github/codeql-action/analyze@v3",
            "security-events: write",
            "fail-on-severity: high",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, security)

        for fragment in (
            "package-ecosystem: github-actions",
            "package-ecosystem: pip",
            "package-ecosystem: npm",
            "directory: /frontend",
            "timezone: Europe/Istanbul",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, dependabot)

    def test_ci_cd_docs_describe_release_operations(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        docs = (ROOT / "docs" / "CI_CD.md").read_text(encoding="utf-8")

        for fragment in (
            "## CI/CD and Releases",
            "ci.yml",
            "security.yml",
            "release.yml",
            "docs/CI_CD.md",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, readme)

        for fragment in (
            "Release Process",
            "python verify.py --frontend-build",
            "git tag v0.1.0",
            "SHA256SUMS.txt",
            "release environment",
            "branch protection",
            "No custom release secrets are required",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, docs)


if __name__ == "__main__":
    unittest.main()
