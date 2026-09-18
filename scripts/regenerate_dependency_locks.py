from __future__ import annotations

import argparse
import importlib.metadata
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
REQUIRED_PYTHON = (3, 14)
REQUIRED_PIP_TOOLS = "7.6.1"
LOCK_INPUTS = (
    ("requirements.lock", "pyproject.toml"),
    ("requirements-live.lock", "requirements-live.txt"),
    ("requirements-test.lock", "requirements-test.txt"),
    ("requirements-build.lock", "requirements-build.txt"),
    ("requirements-bootstrap.lock", "requirements-bootstrap.txt"),
    ("requirements-security.lock", "requirements-security.txt"),
)


def compile_command(lock_name: str, source_name: str, *, upgrade: bool) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "piptools",
        "compile",
        "--allow-unsafe",
        "--generate-hashes",
        "--strip-extras",
        f"--output-file={lock_name}",
    ]
    if upgrade:
        command.append("--upgrade")
    command.append(source_name)
    return command


def validate_toolchain() -> None:
    current_python = sys.version_info[:2]
    if current_python != REQUIRED_PYTHON:
        expected = ".".join(map(str, REQUIRED_PYTHON))
        actual = ".".join(map(str, current_python))
        raise SystemExit(f"lock regeneration requires Python {expected}; found {actual}")
    try:
        actual_pip_tools = importlib.metadata.version("pip-tools")
    except importlib.metadata.PackageNotFoundError as exc:
        raise SystemExit(
            "pip-tools is missing; install the hash-locked requirements-build.lock first"
        ) from exc
    if actual_pip_tools != REQUIRED_PIP_TOOLS:
        raise SystemExit(
            f"lock regeneration requires pip-tools {REQUIRED_PIP_TOOLS}; "
            f"found {actual_pip_tools}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Regenerate every reviewed Python dependency lock with one pinned toolchain."
    )
    parser.add_argument(
        "--upgrade",
        action="store_true",
        help="permit pip-compile to select newer versions allowed by the source constraints",
    )
    args = parser.parse_args(argv)
    validate_toolchain()
    for lock_name, source_name in LOCK_INPUTS:
        subprocess.run(
            compile_command(lock_name, source_name, upgrade=args.upgrade),
            cwd=ROOT,
            check=True,
        )
    subprocess.run(
        [sys.executable, "scripts/verify_dependency_lock.py"],
        cwd=ROOT,
        check=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
