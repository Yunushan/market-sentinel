from __future__ import annotations

import unittest
from unittest.mock import patch
from pathlib import Path

from scripts.regenerate_dependency_locks import (
    LOCK_INPUTS,
    REQUIRED_PIP_TOOLS,
    REQUIRED_PYTHON,
    compile_command,
    validate_toolchain,
)
from scripts.verify_dependency_lock import lock_issues

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 compatibility.
    import tomli as tomllib


ROOT = Path(__file__).resolve().parent.parent


class DependencyLockTests(unittest.TestCase):
    def test_regeneration_covers_every_reviewed_lock_with_hardening_flags(self) -> None:
        expected_locks = {
            "requirements.lock",
            "requirements-live.lock",
            "requirements-test.lock",
            "requirements-build.lock",
            "requirements-bootstrap.lock",
            "requirements-security.lock",
        }
        self.assertEqual(expected_locks, {lock for lock, _source in LOCK_INPUTS})
        for lock_name, source_name in LOCK_INPUTS:
            with self.subTest(lock=lock_name):
                command = compile_command(lock_name, source_name, upgrade=False)
                self.assertIn("--allow-unsafe", command)
                self.assertIn("--generate-hashes", command)
                self.assertIn("--strip-extras", command)
                self.assertIn(f"--output-file={lock_name}", command)
                self.assertEqual(source_name, command[-1])
                self.assertNotIn("--upgrade", command)

    def test_regeneration_upgrade_is_explicit(self) -> None:
        command = compile_command("requirements.lock", "pyproject.toml", upgrade=True)
        self.assertEqual("--upgrade", command[-2])

    def test_regeneration_rejects_unreviewed_toolchain(self) -> None:
        with (
            patch("scripts.regenerate_dependency_locks.sys.version_info", (3, 13)),
            self.assertRaisesRegex(SystemExit, "requires Python 3.14"),
        ):
            validate_toolchain()
        with (
            patch("scripts.regenerate_dependency_locks.sys.version_info", (*REQUIRED_PYTHON, 0)),
            patch(
                "scripts.regenerate_dependency_locks.importlib.metadata.version",
                return_value="0.0.0",
            ),
            self.assertRaisesRegex(SystemExit, REQUIRED_PIP_TOOLS),
        ):
            validate_toolchain()

    def test_repository_lock_covers_direct_dependencies_with_hashes(self) -> None:
        project = ROOT / "pyproject.toml"
        lock = ROOT / "requirements.lock"
        self.assertTrue(lock.exists())
        dependencies = [
            "exceptiongroup>=1.0.2; python_version < '3.11'",
            "requests>=2.31.0",
            "truststore>=0.10.0",
            "websocket-client>=1.7.0",
            "tomli>=2.4.1; python_version < '3.11'",
        ]
        self.assertEqual([], lock_issues(lock.read_text(encoding="utf-8"), dependencies), project.read_text(encoding="utf-8"))

    def test_runtime_lock_excludes_test_tooling(self) -> None:
        lock = (ROOT / "requirements.lock").read_text(encoding="utf-8")
        self.assertNotIn("pytest==", lock)
        self.assertNotIn("coverage==", lock)

    def test_repository_locks_satisfy_requirements_file_specifiers(self) -> None:
        sources = (
            ("requirements.lock", ("requirements.txt",)),
            ("requirements-live.lock", ("requirements.txt", "requirements-live.txt")),
            (
                "requirements-test.lock",
                ("requirements.txt", "requirements-live.txt", "requirements-test.txt"),
            ),
        )
        for lock_name, source_names in sources:
            dependencies = []
            for source_name in source_names:
                dependencies.extend(
                    line.strip()
                    for line in (ROOT / source_name).read_text(encoding="utf-8").splitlines()
                    if line.strip() and not line.lstrip().startswith(("#", "-r"))
                )
            with self.subTest(lock=lock_name):
                self.assertEqual(
                    [],
                    lock_issues((ROOT / lock_name).read_text(encoding="utf-8"), dependencies),
                )

    def test_runtime_derived_locks_include_python_310_requirements_with_hashes(self) -> None:
        for name in ("requirements.lock", "requirements-live.lock", "requirements-test.lock"):
            with self.subTest(name=name):
                lock = (ROOT / name).read_text(encoding="utf-8")
                self.assertIn('exceptiongroup==1.3.1 ; python_version < "3.11"', lock)
                self.assertIn("--hash=sha256:8b412432c6055b0b7d14c310000ae93352ed6754f70fa8f7c34141f91c4e3219", lock)
                self.assertIn("--hash=sha256:a7a39a3bd276781e98394987d3a5701d0c4edffb633bb7a5144577f82c773598", lock)
                self.assertIn('tomli==2.4.1 ; python_version < "3.11"', lock)
                self.assertIn("--hash=sha256:01f520d4f53ef97964a240a035ec2a869fe1a37dde002b57ebc4417a27ccd853", lock)

    def test_build_lock_includes_hash_pinned_distribution_build_toolchain(self) -> None:
        source = (ROOT / "requirements-build.txt").read_text(encoding="utf-8")
        lock = (ROOT / "requirements-build.lock").read_text(encoding="utf-8")
        requirements = [line.strip() for line in source.splitlines() if line.strip() and not line.startswith("#")]
        self.assertGreaterEqual(len(requirements), 2)
        for requirement in requirements:
            self.assertIn(requirement, lock)
        self.assertIn("pyproject-hooks==1.2.0", lock)

    def test_test_lock_covers_runtime_and_test_dependencies(self) -> None:
        lock = (ROOT / "requirements-test.lock").read_text(encoding="utf-8")
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
        dependencies = [
            *project["dependencies"],
            *project["optional-dependencies"]["live"],
            *project["optional-dependencies"]["test"],
        ]
        self.assertEqual(
            [],
            lock_issues(lock, dependencies),
        )

    def test_live_lock_covers_authenticated_clob_sdk_dependencies(self) -> None:
        lock = (ROOT / "requirements-live.lock").read_text(encoding="utf-8")
        self.assertEqual([], lock_issues(lock, ["requests>=2.31.0", "py-clob-client-v2>=1.1.0"]))

    def test_security_audit_lock_is_hash_protected(self) -> None:
        source = (ROOT / "requirements-security.txt").read_text(encoding="utf-8")
        lock = (ROOT / "requirements-security.lock").read_text(encoding="utf-8")
        self.assertEqual("pip-audit==2.10.1\n", source)
        self.assertEqual([], lock_issues(lock, ["pip-audit==2.10.1"]))

    def test_bootstrap_installer_and_build_backend_are_hash_protected(self) -> None:
        source = (ROOT / "requirements-bootstrap.txt").read_text(encoding="utf-8")
        lock = (ROOT / "requirements-bootstrap.lock").read_text(encoding="utf-8")
        self.assertEqual("pip==26.2.1\nsetuptools==84.0.0\n", source)
        self.assertEqual([], lock_issues(lock, ["pip==26.2.1", "setuptools==84.0.0"]))

    def test_standalone_lock_verifier_covers_security_audit_lock(self) -> None:
        verifier = (ROOT / "scripts" / "verify_dependency_lock.py").read_text(encoding="utf-8")
        self.assertIn("SECURITY_LOCK_PATH", verifier)
        self.assertIn("SECURITY_REQUIREMENTS_PATH", verifier)
        self.assertIn("BOOTSTRAP_LOCK_PATH", verifier)
        self.assertIn("BOOTSTRAP_REQUIREMENTS_PATH", verifier)

    def test_lock_validation_rejects_unhashed_or_missing_direct_dependency(self) -> None:
        lock = "requests==2.0.0\n"
        issues = lock_issues(lock, ["requests>=2", "truststore>=1"])
        self.assertIn("requests is not hash protected", issues)
        self.assertIn("direct dependency truststore is missing from requirements.lock", issues)

    def test_lock_validation_rejects_pip_control_directives(self) -> None:
        pinned = "requests==2.32.5 \\\n    --hash=sha256:" + "a" * 64 + "\n"
        for directive in (
            "--no-binary=:all:",
            "--only-binary=:none:",
            "--index-url https://packages.example.invalid/simple",
            "--extra-index-url https://packages.example.invalid/simple",
            "--find-links https://packages.example.invalid/wheels",
            "--trusted-host packages.example.invalid",
            "-r injected-requirements.lock",
        ):
            with self.subTest(directive=directive):
                issues = lock_issues(pinned + directive + "\n", ["requests>=2"])
                self.assertIn(
                    f"line 3: pip control directive is not allowed in a lock file: {directive.split()[0]}",
                    issues,
                )

    def test_lock_validation_rejects_unrecognized_executable_content(self) -> None:
        pinned = "requests==2.32.5 \\\n    --hash=sha256:" + "a" * 64 + "\n"

        issues = lock_issues(pinned + "https://packages.example.invalid/archive.whl\n", ["requests>=2"])

        self.assertIn("line 3: unrecognized lock-file content", issues)

    def test_lock_validation_rejects_ruff_version_outside_source_pin(self) -> None:
        lock = "ruff==0.16.3 \\\n    --hash=sha256:" + "a" * 64 + "\n"

        issues = lock_issues(lock, ["ruff==0.16.4"])

        self.assertIn(
            "locked ruff==0.16.3 does not satisfy source requirement ruff==0.16.4",
            issues,
        )

    def test_lock_validation_rejects_tomli_version_below_source_minimum(self) -> None:
        lock = 'tomli==2.2.1 ; python_version < "3.11" \\\n    --hash=sha256:' + "b" * 64 + "\n"

        issues = lock_issues(lock, ['tomli>=2.4.1; python_version < "3.11"'])

        self.assertIn(
            'locked tomli==2.2.1 does not satisfy source requirement tomli>=2.4.1; python_version < "3.11"',
            issues,
        )


if __name__ == "__main__":
    unittest.main()
