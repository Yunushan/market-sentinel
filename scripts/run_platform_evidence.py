from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.collect_platform_evidence import collect_evidence, write_evidence


CommandRunner = Callable[[list[str], Path, float], subprocess.CompletedProcess[object]]


class BootstrapFailure(RuntimeError):
    """Raised when a disposable, locked evidence environment cannot be prepared."""


def _run_command(args: list[str], cwd: Path, timeout: float) -> subprocess.CompletedProcess[object]:
    """Run bootstrap commands without retaining environment or credential output."""
    return subprocess.run(
        args,
        cwd=cwd,
        check=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=timeout,
    )


def venv_python(environment: Path, *, windows: bool | None = None) -> Path:
    """Return the interpreter path created by Python's standard venv module."""
    if windows is None:
        windows = os.name == "nt"
    return environment / ("Scripts/python.exe" if windows else "bin/python")


def _bootstrap_step(
    name: str,
    args: list[str],
    timeout: float,
    runner: CommandRunner,
) -> None:
    try:
        result = runner(args, ROOT, timeout)
    except subprocess.TimeoutExpired as exc:
        raise BootstrapFailure(f"{name} timed out") from exc
    except OSError as exc:
        raise BootstrapFailure(f"{name} could not start (os_error_{exc.errno or 'unknown'})") from exc
    if result.returncode != 0:
        raise BootstrapFailure(f"{name} failed with exit code {result.returncode}")


def prepare_environment(
    base_python: str,
    environment: Path,
    timeout: float,
    runner: CommandRunner = _run_command,
) -> Path:
    """Create a disposable venv and install only the repository's locked test set."""
    _bootstrap_step(
        "virtual environment creation",
        [base_python, "-m", "venv", str(environment)],
        timeout,
        runner,
    )
    python = venv_python(environment)
    _bootstrap_step(
        "locked dependency installation",
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-input",
            "--no-cache-dir",
            "--require-hashes",
            "-r",
            "requirements-test.lock",
        ],
        timeout,
        runner,
    )
    _bootstrap_step(
        "project installation",
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-input",
            "--no-cache-dir",
            "--no-deps",
            ".",
        ],
        timeout,
        runner,
    )
    return python


def collect_isolated_evidence(
    platform_label: str,
    output: Path,
    base_python: str,
    timeout: float,
    include_frontend_build: bool,
    runner: CommandRunner = _run_command,
) -> dict[str, object]:
    """Collect redacted evidence from a temporary venv, never the caller's environment."""
    with tempfile.TemporaryDirectory(prefix="marketsentinel-platform-evidence-") as temporary_directory:
        temporary = Path(temporary_directory)
        python = prepare_environment(base_python, temporary / "venv", timeout, runner)
        config_path = temporary / "config.json"
        previous_config = os.environ.get("PREDICTION_MARKET_CONFIG_PATH")
        os.environ["PREDICTION_MARKET_CONFIG_PATH"] = str(config_path)
        try:
            evidence = collect_evidence(
                platform_label,
                str(python),
                timeout,
                include_frontend_build,
            )
        finally:
            if previous_config is None:
                os.environ.pop("PREDICTION_MARKET_CONFIG_PATH", None)
            else:
                os.environ["PREDICTION_MARKET_CONFIG_PATH"] = previous_config
        write_evidence(output, evidence)
        return evidence


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create an isolated, hash-locked virtual environment and write a redacted "
            "MarketSentinel platform-evidence record."
        )
    )
    parser.add_argument("--platform", required=True, help="Human-readable host target, for example 'FreeBSD 14.2'.")
    parser.add_argument("--output", type=Path, required=True, help="Existing-directory path for the JSON evidence record.")
    parser.add_argument("--base-python", default=sys.executable, help="Python used to create the disposable virtual environment.")
    parser.add_argument("--timeout", type=float, default=900.0, help="Per-bootstrap and per-check timeout in seconds.")
    parser.add_argument(
        "--include-frontend-build",
        action="store_true",
        help="Also run npm run build from frontend/ when Node.js is installed on the target host.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.timeout <= 0:
        raise SystemExit("--timeout must be greater than zero")
    try:
        evidence = collect_isolated_evidence(
            args.platform,
            args.output,
            args.base_python,
            args.timeout,
            args.include_frontend_build,
        )
    except (BootstrapFailure, OSError, ValueError) as exc:
        print(f"Unable to collect isolated platform evidence: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"output": str(args.output), "status": evidence["status"]}, sort_keys=True))
    return 0 if evidence["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
