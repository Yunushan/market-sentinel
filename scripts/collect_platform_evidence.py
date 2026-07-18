from __future__ import annotations

import argparse
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 compatibility.
    import tomli as tomllib


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = 1

CommandRunner = Callable[[list[str], Path, float], subprocess.CompletedProcess[object]]


def _run_command(args: list[str], cwd: Path, timeout: float) -> subprocess.CompletedProcess[object]:
    """Run a verification command without retaining potentially sensitive output."""
    return subprocess.run(
        args,
        cwd=cwd,
        check=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=timeout,
    )


def source_identity() -> dict[str, str | None]:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    commit: str | None = None
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        candidate = result.stdout.decode("ascii", errors="ignore").strip().lower()
        if re.fullmatch(r"[0-9a-f]{40}", candidate):
            commit = candidate
    except OSError:
        pass
    return {"project_version": str(project["version"]), "git_commit": commit}


def _check(
    name: str,
    args: list[str],
    cwd: Path,
    timeout: float,
    runner: CommandRunner,
) -> dict[str, Any]:
    started = time.monotonic()
    try:
        result = runner(args, cwd, timeout)
        return {
            "name": name,
            "status": "pass" if result.returncode == 0 else "fail",
            "returncode": result.returncode,
            "duration_seconds": round(time.monotonic() - started, 3),
        }
    except subprocess.TimeoutExpired:
        return {
            "name": name,
            "status": "fail",
            "error": "timeout",
            "duration_seconds": round(time.monotonic() - started, 3),
        }
    except OSError as exc:
        return {
            "name": name,
            "status": "fail",
            "error": f"os_error_{exc.errno or 'unknown'}",
            "duration_seconds": round(time.monotonic() - started, 3),
        }


def collect_evidence(
    platform_label: str,
    python_executable: str,
    timeout: float,
    include_frontend_build: bool,
    runner: CommandRunner = _run_command,
) -> dict[str, Any]:
    checks = [
        _check("python_version", [python_executable, "--version"], ROOT, timeout, runner),
        _check("python_dependency_check", [python_executable, "-m", "pip", "check"], ROOT, timeout, runner),
        _check("tkinter_smoke", [python_executable, "app.py", "--smoke-test"], ROOT, timeout, runner),
        _check("project_verification", [python_executable, "verify.py"], ROOT, timeout, runner),
    ]
    if include_frontend_build:
        checks.append(_check("frontend_build", ["npm", "run", "build"], ROOT / "frontend", timeout, runner))

    return {
        "schema_version": SCHEMA_VERSION,
        "collected_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "platform_label": platform_label,
        "source": source_identity(),
        "host": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python_version": platform.python_version(),
        },
        "checks": checks,
        "status": "ok" if all(check["status"] == "pass" for check in checks) else "failed",
    }


def write_evidence(path: Path, payload: dict[str, Any]) -> None:
    if not path.parent.is_dir():
        raise ValueError(f"evidence parent directory does not exist: {path.parent}")
    # macOS exposes its normal temporary directory beneath /var, a system symlink.
    # Allow only symlinks that are ancestors of the platform-selected temp root; all
    # user-selected symlink components in an evidence destination remain rejected.
    temp_root = Path(tempfile.gettempdir()).absolute()
    trusted_symlinks = {item for item in (temp_root, *temp_root.parents) if item.is_symlink()}
    output_parent = path.parent.absolute()
    symlinked_component = next(
        (item for item in (output_parent, *output_parent.parents) if item.is_symlink() and item not in trusted_symlinks),
        None,
    )
    if symlinked_component is not None:
        raise ValueError(f"evidence output path contains symbolic-link component: {symlinked_component}")
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            if os.name == "posix":
                os.fchmod(handle.fileno(), 0o600)
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run real host checks and write a redacted MarketSentinel platform-evidence record."
    )
    parser.add_argument("--platform", required=True, help="Human-readable host target, for example 'FreeBSD 14.2'.")
    parser.add_argument("--output", type=Path, required=True, help="Existing-directory path for the JSON evidence record.")
    parser.add_argument("--python", default=sys.executable, help="Python executable used for the checks.")
    parser.add_argument("--timeout", type=float, default=900.0, help="Per-check timeout in seconds.")
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
    evidence = collect_evidence(
        args.platform,
        args.python,
        args.timeout,
        args.include_frontend_build,
    )
    try:
        write_evidence(args.output, evidence)
    except (OSError, ValueError) as exc:
        print(f"Unable to write platform evidence: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"output": str(args.output), "status": evidence["status"]}, sort_keys=True))
    return 0 if evidence["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
