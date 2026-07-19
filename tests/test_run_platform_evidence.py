from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.run_platform_evidence import BootstrapFailure, collect_isolated_evidence, prepare_environment, venv_python


class IsolatedPlatformEvidenceTests(unittest.TestCase):
    def test_cli_is_directly_executable(self) -> None:
        result = subprocess.run(
            [sys.executable, "scripts/run_platform_evidence.py", "--help"],
            cwd=Path(__file__).resolve().parent.parent,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("--base-python", result.stdout)

    def test_uses_native_virtual_environment_interpreter_path(self) -> None:
        environment = Path("evidence-venv")
        self.assertEqual(environment / "Scripts" / "python.exe", venv_python(environment, windows=True))
        self.assertEqual(environment / "bin" / "python", venv_python(environment, windows=False))

    def test_prepares_hash_locked_environment(self) -> None:
        calls: list[list[str]] = []

        def runner(args: list[str], cwd: Path, timeout: float) -> subprocess.CompletedProcess[object]:
            calls.append(args)
            return subprocess.CompletedProcess(args, 0)

        python = prepare_environment("python3", Path("temporary-venv"), 30.0, runner)

        self.assertEqual(Path("temporary-venv") / ("Scripts/python.exe" if os.name == "nt" else "bin/python"), python)
        self.assertEqual(["python3", "-m", "venv", "temporary-venv"], calls[0])
        self.assertIn("--require-hashes", calls[1])
        self.assertIn("requirements-test.lock", calls[1])
        self.assertIn("--no-deps", calls[2])

    def test_bootstrap_failure_does_not_include_command_output(self) -> None:
        def runner(args: list[str], cwd: Path, timeout: float) -> subprocess.CompletedProcess[object]:
            return subprocess.CompletedProcess(args, 19, stdout="secret", stderr="secret")

        with self.assertRaisesRegex(BootstrapFailure, "virtual environment creation failed with exit code 19") as failure:
            prepare_environment("python3", Path("temporary-venv"), 30.0, runner)
        self.assertNotIn("secret", str(failure.exception))

    def test_collects_with_temporary_config_and_restores_callers_environment(self) -> None:
        observed: dict[str, object] = {}

        def fake_collect(platform: str, python: str, timeout: float, frontend: bool) -> dict[str, object]:
            observed["config"] = os.environ.get("PREDICTION_MARKET_CONFIG_PATH")
            observed["python"] = python
            return {"status": "ok", "checks": []}

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "evidence.json"
            with (
                patch("scripts.run_platform_evidence.prepare_environment", return_value=Path("isolated-python")),
                patch("scripts.run_platform_evidence.collect_evidence", side_effect=fake_collect),
                patch("scripts.run_platform_evidence.write_evidence") as write,
                patch.dict(os.environ, {"PREDICTION_MARKET_CONFIG_PATH": "caller-config.json"}),
            ):
                evidence = collect_isolated_evidence("FreeBSD 14.2", output, "python3", 30.0, False)
                self.assertEqual("caller-config.json", os.environ.get("PREDICTION_MARKET_CONFIG_PATH"))

        self.assertEqual({"status": "ok", "checks": []}, evidence)
        self.assertNotEqual("caller-config.json", observed["config"])
        self.assertEqual("isolated-python", observed["python"])
        write.assert_called_once_with(output, evidence)


if __name__ == "__main__":
    unittest.main()
