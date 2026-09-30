from __future__ import annotations

"""The reviewed, non-exportable-key Windows release signing contract."""

import re
from typing import Mapping
from uuid import UUID


SIGNPATH_PROJECT_SLUG = "market-sentinel"
SIGNPATH_POLICY_SLUG = "release-signing"
SIGNPATH_ARTIFACT_CONFIGURATIONS = {
    "exe": "market-sentinel-exe-v1",
    "msi": "market-sentinel-msi-v1",
}
REQUIRED_SIGNING_VARIABLES = frozenset(
    {"WINDOWS_SIGNING_PROVIDER", "SIGNPATH_ORGANIZATION_ID", "WINDOWS_SIGNING_CERTIFICATE_SHA256"}
)
REQUIRED_SIGNING_SECRETS = frozenset({"SIGNPATH_API_TOKEN"})


def signing_configuration_issues(values: Mapping[str, str]) -> list[str]:
    """Return names only; configuration values must never enter failure logs."""

    issues: list[str] = []
    if values.get("WINDOWS_SIGNING_PROVIDER") != "signpath":
        issues.append("WINDOWS_SIGNING_PROVIDER (must be signpath)")
    organization = values.get("SIGNPATH_ORGANIZATION_ID", "")
    try:
        parsed = UUID(organization)
        valid_organization = str(parsed) == organization and parsed.int != 0
    except (ValueError, AttributeError, TypeError):
        valid_organization = False
    if not valid_organization:
        issues.append("SIGNPATH_ORGANIZATION_ID")
    certificate = values.get("WINDOWS_SIGNING_CERTIFICATE_SHA256", "")
    if not isinstance(certificate, str) or not re.fullmatch(r"[0-9a-f]{64}", certificate) or certificate == "0" * 64:
        issues.append("WINDOWS_SIGNING_CERTIFICATE_SHA256")
    return issues
