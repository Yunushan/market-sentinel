from __future__ import annotations

import re
import sys
from pathlib import Path

from packaging.requirements import Requirement

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 compatibility.
    import tomli as tomllib


ROOT = Path(__file__).resolve().parent.parent
LOCK_PATH = ROOT / "requirements.lock"
PROJECT_PATH = ROOT / "pyproject.toml"
LOCKED_REQUIREMENT_RE = re.compile(r"^([A-Za-z0-9_.-]+)==[^\s;]+(?:\s*;\s*[^\\]+)?\s*\\?$")
HASH_RE = re.compile(r"^\s*--hash=sha256:[0-9a-f]{64}$")


def canonical_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def lock_issues(lock_text: str, project_dependencies: list[str]) -> list[str]:
    locked: dict[str, int] = {}
    active_name: str | None = None
    active_has_hash = False
    issues: list[str] = []

    def finish_active() -> None:
        nonlocal active_name, active_has_hash
        if active_name and not active_has_hash:
            issues.append(f"{active_name} is not hash protected")
        active_name = None
        active_has_hash = False

    for line in lock_text.splitlines():
        match = LOCKED_REQUIREMENT_RE.match(line)
        if match:
            finish_active()
            active_name = canonical_name(match.group(1))
            locked[active_name] = locked.get(active_name, 0) + 1
            continue
        if active_name and HASH_RE.match(line):
            active_has_hash = True
            continue
        if active_name and line and not line.startswith((" ", "#")):
            finish_active()
    finish_active()

    for dependency in project_dependencies:
        requirement = Requirement(dependency)
        if canonical_name(requirement.name) not in locked:
            issues.append(f"direct dependency {requirement.name} is missing from requirements.lock")
    duplicate_names = sorted(name for name, count in locked.items() if count > 1 and name != "tomli")
    if duplicate_names:
        issues.append("duplicate locked packages: " + ", ".join(duplicate_names))
    return issues


def main() -> int:
    if not LOCK_PATH.exists():
        print("requirements.lock is missing", file=sys.stderr)
        return 1
    project = tomllib.loads(PROJECT_PATH.read_text(encoding="utf-8"))
    dependencies = list(project.get("project", {}).get("dependencies", []))
    issues = lock_issues(LOCK_PATH.read_text(encoding="utf-8"), dependencies)
    if issues:
        print("Dependency lock validation failed:", file=sys.stderr)
        for issue in issues:
            print(f"- {issue}", file=sys.stderr)
        return 1
    print("[ok] dependency lock")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
