from __future__ import annotations

import unittest
from pathlib import Path

from scripts.verify_dependency_lock import lock_issues


ROOT = Path(__file__).resolve().parent.parent


class DependencyLockTests(unittest.TestCase):
    def test_repository_lock_covers_direct_dependencies_with_hashes(self) -> None:
        project = ROOT / "pyproject.toml"
        lock = ROOT / "requirements.lock"
        self.assertTrue(lock.exists())
        dependencies = [
            "requests>=2.31.0",
            "truststore>=0.10.0",
            "websocket-client>=1.7.0",
            "tomli>=2.0.0; python_version < '3.11'",
        ]
        self.assertEqual([], lock_issues(lock.read_text(encoding="utf-8"), dependencies), project.read_text(encoding="utf-8"))

    def test_runtime_lock_excludes_test_tooling(self) -> None:
        lock = (ROOT / "requirements.lock").read_text(encoding="utf-8")
        self.assertNotIn("pytest==", lock)
        self.assertNotIn("coverage==", lock)

    def test_lock_includes_python_310_requirement_with_hashes(self) -> None:
        lock = (ROOT / "requirements.lock").read_text(encoding="utf-8")
        self.assertIn('tomli==2.2.1 ; python_version < "3.11"', lock)
        self.assertIn("--hash=sha256:cb55c73c5f4408779d0cf3eef9f762b9c9f147a77de7b258bef0a5628adc85cc", lock)

    def test_build_lock_includes_hash_pinned_distribution_build_toolchain(self) -> None:
        lock = (ROOT / "requirements-build.lock").read_text(encoding="utf-8")
        self.assertIn("build==1.5.0", lock)
        self.assertIn("pyproject-hooks==1.2.0", lock)
        self.assertIn("pyinstaller==6.21.0", lock)

    def test_test_lock_covers_runtime_and_test_dependencies(self) -> None:
        lock = (ROOT / "requirements-test.lock").read_text(encoding="utf-8")
        self.assertEqual(
            [],
            lock_issues(lock, ["requests>=2.31.0", "py-clob-client>=0.34.0", "pytest>=8.0", "coverage[toml]>=7.6"]),
        )

    def test_live_lock_covers_authenticated_clob_sdk_dependencies(self) -> None:
        lock = (ROOT / "requirements-live.lock").read_text(encoding="utf-8")
        self.assertEqual([], lock_issues(lock, ["requests>=2.31.0", "py-clob-client>=0.34.0"]))

    def test_lock_validation_rejects_unhashed_or_missing_direct_dependency(self) -> None:
        lock = "requests==2.0.0\n"
        issues = lock_issues(lock, ["requests>=2", "truststore>=1"])
        self.assertIn("requests is not hash protected", issues)
        self.assertIn("direct dependency truststore is missing from requirements.lock", issues)


if __name__ == "__main__":
    unittest.main()
