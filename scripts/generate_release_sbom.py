from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 compatibility.
    import tomli as tomllib


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.atomic_files import atomic_write_text

LOCK_RE = re.compile(r"^([A-Za-z0-9_.-]+)==([^\s;]+)")


def spdx_id(name: str, version: str) -> str:
    digest = hashlib.sha256(f"{name}@{version}".encode("utf-8")).hexdigest()[:16]
    return f"SPDXRef-Package-{digest}"


def created_at() -> str:
    source_date_epoch = os.environ.get("SOURCE_DATE_EPOCH")
    if source_date_epoch and source_date_epoch.isdigit():
        return datetime.fromtimestamp(int(source_date_epoch), tz=timezone.utc).isoformat().replace("+00:00", "Z")
    return datetime.now(tz=timezone.utc).isoformat().replace("+00:00", "Z")


def locked_python_packages(lock_path: Path) -> list[tuple[str, str]]:
    packages: dict[str, str] = {}
    for line in lock_path.read_text(encoding="utf-8").splitlines():
        match = LOCK_RE.match(line)
        if match:
            packages[match.group(1).lower()] = match.group(2)
    return sorted(packages.items())


def locked_node_packages(lock_path: Path) -> list[tuple[str, str]]:
    data = json.loads(lock_path.read_text(encoding="utf-8"))
    packages: dict[str, str] = {}
    for path, metadata in dict(data.get("packages") or {}).items():
        if not path.startswith("node_modules/") or not isinstance(metadata, dict):
            continue
        name = path.removeprefix("node_modules/")
        version = str(metadata.get("version") or "").strip()
        if name and version:
            packages[name] = version
    return sorted(packages.items())


def package_entry(name: str, version: str, *, license_expression: str = "NOASSERTION") -> dict[str, Any]:
    return {
        "SPDXID": spdx_id(name, version),
        "name": name,
        "versionInfo": version,
        "downloadLocation": "NOASSERTION",
        "filesAnalyzed": False,
        "licenseConcluded": "NOASSERTION",
        "licenseDeclared": license_expression,
        "copyrightText": "NOASSERTION",
    }


def build_sbom(version: str) -> dict[str, Any]:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    project_name = str(project["name"])
    if str(project["version"]) != version:
        raise SystemExit(f"Requested SBOM version {version} does not match pyproject.toml ({project['version']}).")
    root_package = package_entry(project_name, version, license_expression="0BSD")
    dependencies = [
        package_entry(name, package_version)
        for name, package_version in locked_python_packages(ROOT / "requirements.lock")
    ] + [
        package_entry(name, package_version)
        for name, package_version in locked_node_packages(ROOT / "frontend" / "package-lock.json")
    ]
    relationships = [
        {
            "spdxElementId": "SPDXRef-DOCUMENT",
            "relationshipType": "DESCRIBES",
            "relatedSpdxElement": root_package["SPDXID"],
        }
    ]
    relationships.extend(
        {
            "spdxElementId": root_package["SPDXID"],
            "relationshipType": "DEPENDS_ON",
            "relatedSpdxElement": dependency["SPDXID"],
        }
        for dependency in dependencies
    )
    namespace_hash = hashlib.sha256(f"{project_name}:{version}".encode("utf-8")).hexdigest()
    return {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": f"{project_name}-{version}-sbom",
        "documentNamespace": f"https://github.com/Yunushan/market-sentinel/sbom/{namespace_hash}",
        "creationInfo": {"created": created_at(), "creators": ["Tool: market-sentinel generate_release_sbom.py"]},
        "packages": [root_package, *dependencies],
        "relationships": relationships,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate an SPDX JSON SBOM from the committed dependency locks.")
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = build_sbom(str(args.version))
    atomic_write_text(args.output, json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(f"[ok] SBOM ({args.output})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
