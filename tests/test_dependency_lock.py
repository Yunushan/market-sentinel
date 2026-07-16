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

    def test_lock_validation_rejects_unhashed_or_missing_direct_dependency(self) -> None:
        lock = "requests==2.0.0\n"
        issues = lock_issues(lock, ["requests>=2", "truststore>=1"])
        self.assertIn("requests is not hash protected", issues)
        self.assertIn("direct dependency truststore is missing from requirements.lock", issues)


if __name__ == "__main__":
    unittest.main()
