from __future__ import annotations

import argparse
import importlib.metadata
import re
import subprocess
import sys
from pathlib import Path

from packaging.requirements import Requirement
from packaging.version import Version

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 compatibility for unit tests.
    import tomli as tomllib


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
LOCK_BLOCK = re.compile(r"(?m)^[A-Za-z0-9_.-]+==[^\n]*(?:\n[ \t]+[^\n]*)*(?:\n|$)")


def _name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _blocks(text: str) -> dict[str, tuple[Requirement, str]]:
    result: dict[str, tuple[Requirement, str]] = {}
    for match in LOCK_BLOCK.finditer(text):
        block = match.group()
        requirement = Requirement(block.splitlines()[0].rstrip("\\").strip())
        name = _name(requirement.name)
        if name in result:
            raise RuntimeError("Lock regeneration requires unique reviewed package records")
        result[name] = (requirement, block)
    return result


def source_requirements(path: Path) -> list[Requirement]:
    if path.suffix == ".toml":
        project = tomllib.loads(path.read_text(encoding="utf-8"))
        return [Requirement(value) for value in project["project"]["dependencies"]]
    requirements: list[Requirement] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("-r "):
            requirements.extend(source_requirements(path.parent / line[3:].strip()))
        else:
            requirements.append(Requirement(line))
    return requirements


def preserve_reviewed_markers(compiled: str, previous: str, direct: list[Requirement]) -> str:
    """Retain inactive target records only while their reviewed dependency holds.

    pip-compile resolves this host, so it drops Python 3.10 records and can erase
    a Windows marker. A changed source, parent pin or active conditional pin
    requires a new target-platform resolution instead of guessing its metadata.
    """
    old_blocks, new_blocks = _blocks(previous), _blocks(compiled)
    for name, (old, block) in old_blocks.items():
        if old.marker is None:
            continue
        pins = tuple(old.specifier)
        if len(pins) != 1 or pins[0].operator != "==":
            raise RuntimeError(f"Conditional {name} needs an exact reviewed pin")
        pin = str(Version(pins[0].version))
        sources = [requirement for requirement in direct if _name(requirement.name) == name]
        if sources:
            supported = all(
                requirement.url is None
                and requirement.extras == old.extras
                and str(requirement.marker) == str(old.marker)
                and requirement.specifier.contains(pin, prereleases=True)
                for requirement in sources
            )
        else:
            parents = []
            for line in block.splitlines()[1:]:
                comment = line.strip().removeprefix("#").strip()
                candidate = comment.removeprefix("via ")
                if re.fullmatch(r"[A-Za-z0-9_.-]+", candidate) and candidate != "via":
                    parents.append(_name(candidate))
            supported = bool(parents) and all(
                parent in old_blocks and parent in new_blocks
                and str(old_blocks[parent][0]) == str(new_blocks[parent][0])
                for parent in parents
            )
            supported = supported and all(
                requirement.url is None and not requirement.extras
                for requirement in direct if _name(requirement.name) in parents
            )
        current = new_blocks.get(name)
        if not supported or (current and (
            current[0].specifier != old.specifier
            or (current[0].marker is not None and str(current[0].marker) != str(old.marker))
        )):
            raise RuntimeError(f"Conditional {name} needs a new reviewed target-platform resolution")
        if current:
            # Keep the fresh hash record, but restore the exact reviewed marker.
            lines = current[1].splitlines(keepends=True)
            lines[0] = block.splitlines(keepends=True)[0]
            compiled = compiled.replace(current[1], "".join(lines), 1)
        else:
            following = next((value[1] for key, value in new_blocks.items() if key > name), None)
            compiled = compiled.replace(following, block + following, 1) if following else compiled.rstrip() + "\n" + block
    return compiled


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
        help="select newer allowed versions; inactive conditional pins stay reviewed, and changed parents require target-platform resolution",
    )
    args = parser.parse_args(argv)
    validate_toolchain()
    for lock_name, source_name in LOCK_INPUTS:
        lock = ROOT / lock_name
        previous = lock.read_text(encoding="utf-8")
        subprocess.run(
            compile_command(lock_name, source_name, upgrade=args.upgrade),
            cwd=ROOT,
            check=True,
        )
        lock.write_text(
            preserve_reviewed_markers(
                lock.read_text(encoding="utf-8"), previous,
                source_requirements(ROOT / source_name),
            ),
            encoding="utf-8",
        )
    subprocess.run(
        [sys.executable, "scripts/verify_dependency_lock.py"],
        cwd=ROOT,
        check=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
