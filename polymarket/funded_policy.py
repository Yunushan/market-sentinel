from __future__ import annotations

"""Strict, independently configured policy for the bounded funded audit."""

import hashlib
import json
import re
from typing import Any, Sequence

from core.json_validation import loads_strict_json


FUNDED_TOKEN_ALLOWLIST_VARIABLE = "POLYMARKET_FUNDED_TOKEN_ALLOWLIST"
MAX_FUNDED_TOKEN_ALLOWLIST_BYTES = 8 * 1024
MAX_FUNDED_TOKEN_IDS = 32
_TOKEN_ID_RE = re.compile(r"[A-Za-z0-9._:-]{1,256}")


def canonical_funded_token_allowlist(token_ids: Sequence[Any]) -> tuple[str, ...]:
    """Validate and normalize a bounded token-id inventory.

    The inventory is sorted so its digest has one representation. Duplicate
    entries are rejected instead of silently collapsed.
    """

    if isinstance(token_ids, (str, bytes)) or not isinstance(token_ids, (list, tuple)):
        raise ValueError("funded token allowlist must be a JSON array")
    if not 1 <= len(token_ids) <= MAX_FUNDED_TOKEN_IDS:
        raise ValueError(f"funded token allowlist must contain 1-{MAX_FUNDED_TOKEN_IDS} token ids")
    normalized: list[str] = []
    for token_id in token_ids:
        if not isinstance(token_id, str) or not _TOKEN_ID_RE.fullmatch(token_id):
            raise ValueError("funded token allowlist contains an invalid token id")
        normalized.append(token_id)
    if len(normalized) != len(set(normalized)):
        raise ValueError("funded token allowlist contains duplicate token ids")
    return tuple(sorted(normalized))


def funded_token_allowlist_json(token_ids: Sequence[Any]) -> str:
    normalized = canonical_funded_token_allowlist(token_ids)
    return json.dumps(list(normalized), ensure_ascii=True, separators=(",", ":"))


def funded_token_allowlist_sha256(token_ids: Sequence[Any]) -> str:
    return hashlib.sha256(funded_token_allowlist_json(token_ids).encode("utf-8")).hexdigest()


def parse_funded_token_allowlist(raw: Any) -> tuple[str, ...]:
    """Parse the production environment variable's canonical JSON value."""

    if not isinstance(raw, str):
        raise ValueError("funded token allowlist environment variable must be a string")
    try:
        encoded = raw.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("funded token allowlist must be valid UTF-8") from exc
    if not encoded or len(encoded) > MAX_FUNDED_TOKEN_ALLOWLIST_BYTES:
        raise ValueError("funded token allowlist is empty or exceeds its byte limit")
    try:
        parsed = loads_strict_json(raw)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("funded token allowlist must be strict JSON") from exc
    normalized = canonical_funded_token_allowlist(parsed)
    if raw != funded_token_allowlist_json(normalized):
        raise ValueError("funded token allowlist must use compact, sorted canonical JSON")
    return normalized
