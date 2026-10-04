from __future__ import annotations

import base64
import binascii
import os
import re
from typing import Any, Dict, Mapping, Optional

from .clob_auth import REQUIRED_L2_HEADERS
from .constants import CLOB_API
from .http_client import PolymarketValidationError


POLYGON_CHAIN_ID = 137
PRIVATE_KEY_KEYS = ("private_key", "polymarket_private_key")
PRIVATE_KEY_ENV_VARS = ("PRIVATE_KEY", "POLYMARKET_PRIVATE_KEY")
FUNDER_KEYS = ("funder_address", "polymarket_funder_address", "deposit_wallet_address")
FUNDER_ENV_VARS = ("FUNDER_ADDRESS", "POLYMARKET_FUNDER_ADDRESS", "DEPOSIT_WALLET_ADDRESS")
SIGNATURE_TYPE_KEYS = ("signature_type", "polymarket_signature_type")
SIGNATURE_TYPE_ENV_VARS = ("SIGNATURE_TYPE", "POLYMARKET_SIGNATURE_TYPE")
L1_HEADER_NAMES = ("POLY_ADDRESS", "POLY_SIGNATURE", "POLY_TIMESTAMP", "POLY_NONCE")
HEX_PRIVATE_KEY_RE = re.compile(r"^0x[0-9a-fA-F]{64}$")
EVM_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")

SIGNATURE_TYPE_INFO: Dict[int, Dict[str, Any]] = {
    0: {
        "name": "EOA",
        "description": "Standard Ethereum wallet. Funder is the EOA address and needs POL for gas.",
        "requires_funder": False,
    },
    1: {
        "name": "POLY_PROXY",
        "description": "Existing Polymarket proxy wallet flow.",
        "requires_funder": True,
    },
    2: {
        "name": "GNOSIS_SAFE",
        "description": "Existing Gnosis Safe wallet flow.",
        "requires_funder": True,
    },
    3: {
        "name": "POLY_1271",
        "description": "Deposit-wallet flow for new API users.",
        "requires_funder": True,
    },
}


# The non-mutating SDK probe selects these aliases in this exact order.
# Keep this separate from the broader config/legacy signing inventory below.
AUTHENTICATED_READ_ENVIRONMENT = {
    "private_key": ("POLYMARKET_PRIVATE_KEY", "PRIVATE_KEY"),
    "funder_address": ("POLYMARKET_FUNDER_ADDRESS", "FUNDER_ADDRESS"),
    "signature_type": ("POLYMARKET_SIGNATURE_TYPE", "SIGNATURE_TYPE"),
    "api_key": ("POLY_API_KEY",),
    "api_secret": ("POLY_API_SECRET", "POLY_SECRET"),
    "api_passphrase": ("POLY_PASSPHRASE",),
}
SDK_AUTH_HEADER_RE = re.compile(rb"[^\x00\s]+(?:[ \t]+[^\x00\s]+)*")
SECP256K1_ORDER = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141


def resolve_authenticated_read_environment(
    *, environ: Optional[Mapping[str, str]] = None,
) -> Dict[str, str]:
    """Resolve raw credentials for execution callers; never include this in reports."""
    env = os.environ if environ is None else environ
    selected = {
        field: next((str(env[name]) for name in names if env.get(name)), "")
        for field, names in AUTHENTICATED_READ_ENVIRONMENT.items()
    }
    selected["signature_type"] = selected["signature_type"] or "0"
    return selected


def build_authenticated_read_readiness(
    *, environ: Optional[Mapping[str, str]] = None,
) -> Dict[str, Any]:
    """Check the current SDK read's selected environment inputs without SDK/network use.

    This checks local input validity only, never successful authentication or account
    eligibility. Config settings and pre-signed legacy headers cannot satisfy it.
    """
    env = os.environ if environ is None else environ
    selected = resolve_authenticated_read_environment(environ=env)
    sources = {
        field: next((name for name in names if env.get(name)), "")
        for field, names in AUTHENTICATED_READ_ENVIRONMENT.items()
    }
    blockers: list[str] = []
    missing: list[str] = []
    field_ready: Dict[str, bool] = {}

    private_key = selected["private_key"]
    private_key_valid = bool(HEX_PRIVATE_KEY_RE.fullmatch(private_key))
    if private_key_valid:
        private_key_valid = 1 <= int(private_key[2:], 16) < SECP256K1_ORDER
    field_ready["private_key"] = private_key_valid
    if not private_key:
        missing.append("POLYMARKET_PRIVATE_KEY or PRIVATE_KEY")
        blockers.append("Fresh SDK reads require an explicit environment private key.")
    elif not private_key_valid:
        blockers.append("The selected private key must be an unpadded 0x-prefixed valid secp256k1 scalar.")

    signature_type, signature_error = _parse_signature_type(selected["signature_type"])
    signature_valid = signature_error is None and signature_type in SIGNATURE_TYPE_INFO
    field_ready["signature_type"] = signature_valid
    if not signature_valid:
        blockers.append("The selected environment signature type must be a supported integer (0, 1, 2, or 3).")
    requires_funder = bool(SIGNATURE_TYPE_INFO.get(signature_type, {}).get("requires_funder")) if signature_valid else False
    funder = selected["funder_address"]
    funder_valid = bool(EVM_ADDRESS_RE.fullmatch(funder)) if funder else not requires_funder
    field_ready["funder_address"] = funder_valid
    if requires_funder and not funder:
        missing.append("POLYMARKET_FUNDER_ADDRESS or FUNDER_ADDRESS")
        blockers.append("The selected signature type requires an explicit environment funder address.")
    elif funder and not funder_valid:
        blockers.append("The selected funder must be an unpadded 0x-prefixed EVM address.")

    for field in ("api_key", "api_secret", "api_passphrase"):
        # Match Trader._explicit_api_creds; a whitespace primary alias must not
        # fall through to a valid lower-priority alias before this validation.
        field_ready[field] = bool(selected[field].strip())
        if not field_ready[field]:
            names = " or ".join(AUTHENTICATED_READ_ENVIRONMENT[field])
            missing.append(names)
            blockers.append(f"Fresh SDK reads require explicit nonempty {names}.")

    for field in ("api_key", "api_passphrase"):
        if not field_ready[field]:
            continue
        try:
            encoded_header = selected[field].strip().encode("ascii")
        except UnicodeEncodeError:
            encoded_header = b""
        if not SDK_AUTH_HEADER_RE.fullmatch(encoded_header):
            field_ready[field] = False
            name = " or ".join(AUTHENTICATED_READ_ENVIRONMENT[field])
            blockers.append(f"The selected {name} is not compatible with the SDK's HTTP header encoding.")

    if field_ready["api_secret"]:
        try:
            # Match the locked SDK's local HMAC input decoder exactly. Decoder
            # success is syntax compatibility, not proof of valid API credentials.
            base64.urlsafe_b64decode(selected["api_secret"].strip())
        except (binascii.Error, ValueError):
            field_ready["api_secret"] = False
            blockers.append("The selected API secret is not decodable by the SDK's URL-safe base64 decoder.")

    return {
        "status": "blocked" if blockers else "ok",
        "detail": "Environment inputs are ready for a fresh SDK read; authentication remains unverified." if not blockers else "Environment inputs are not ready for the current SDK read.",
        "ok": not blockers,
        "blockers": blockers,
        "missing": missing,
        "sources": sources,
        "field_ready": field_ready,
        "requires_funder": requires_funder,
        "signature_type": signature_type if signature_valid else None,
    }


RELAYER_READ_HEADERS = ("RELAYER_API_KEY", "RELAYER_API_KEY_ADDRESS")


def build_relayer_read_readiness(
    *, environ: Optional[Mapping[str, str]] = None,
) -> Dict[str, Any]:
    """Check raw relayer header transport compatibility; never claim API/account eligibility."""
    env = os.environ if environ is None else environ
    def usable_header(value: Any) -> bool:
        if not isinstance(value, str) or not value.strip() or value != value.strip():
            return False
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            return False
        try:
            value.encode("latin-1")  # The raw request header must be encodable by HTTP transport.
        except UnicodeEncodeError:
            return False
        return True

    missing = [name for name in RELAYER_READ_HEADERS if not usable_header(env.get(name))]
    return {
        "status": "blocked" if missing else "ok",
        "ok": not missing,
        "detail": "Relayer header inputs are present; authentication remains unverified." if not missing else "Relayer read requires nonblank, unpadded HTTP-compatible environment headers.",
        "missing": missing,
        "blockers": [f"Relayer reads require a nonblank, unpadded HTTP-compatible {name}." for name in missing],
        "sources": {name: f"env:{name}" if env.get(name) else "" for name in RELAYER_READ_HEADERS},
    }


def build_clob_auth_readiness(
    settings: Optional[Mapping[str, Any]] = None,
    *,
    environ: Optional[Mapping[str, str]] = None,
) -> Dict[str, Any]:
    settings = dict(settings or {})
    env = environ if environ is not None else os.environ
    private_key = _first_config_or_env(settings, PRIVATE_KEY_KEYS, env, PRIVATE_KEY_ENV_VARS)
    funder = _first_config_or_env(settings, FUNDER_KEYS, env, FUNDER_ENV_VARS)
    signature_type_raw = _first_config_or_env(settings, SIGNATURE_TYPE_KEYS, env, SIGNATURE_TYPE_ENV_VARS)
    signature_type_value, signature_type_error = _parse_signature_type(signature_type_raw["value"])
    signature_info = SIGNATURE_TYPE_INFO.get(signature_type_value, {}) if signature_type_error is None else {}

    blockers = []
    warnings = []
    if not private_key["present"]:
        blockers.append("Missing private key for L1 API credential derivation and local order signing.")
    elif not is_private_key_like(private_key["value"]):
        blockers.append("Private key must be a 0x-prefixed 32-byte hex string.")

    if signature_type_error:
        blockers.append(signature_type_error)
    elif signature_type_value not in SIGNATURE_TYPE_INFO:
        blockers.append(f"Unsupported Polymarket signature type: {signature_type_value}.")

    if signature_info.get("requires_funder") and not funder["present"]:
        blockers.append(f"{signature_info['name']} signature type requires an explicit funder/deposit wallet address.")
    if funder["present"] and not is_evm_address_like(funder["value"]):
        blockers.append("Funder/deposit wallet address must be a 0x-prefixed EVM address.")
    if signature_type_value == 0 and not funder["present"]:
        warnings.append("EOA signature type selected without explicit funder; py-clob-client-v2 will use the signing wallet flow.")
    if signature_type_value == 3:
        warnings.append("POLY_1271 is the deposit-wallet flow; verify the funder matches the Polymarket deposit wallet.")

    l2_headers = _header_presence(env, REQUIRED_L2_HEADERS)
    direct_l2_read_ready = all(item["present"] for item in l2_headers.values())
    if not direct_l2_read_ready:
        warnings.append("Direct authenticated REST read checks need all five POLY_* L2 headers.")

    l1_headers = _header_presence(env, L1_HEADER_NAMES)
    l1_rest_ready = all(item["present"] for item in l1_headers.values())

    return {
        "ok": not blockers,
        "sdk_trading_ready": not blockers,
        "can_derive_or_create_api_key": private_key["present"] and is_private_key_like(private_key["value"]),
        "can_sign_orders": private_key["present"] and is_private_key_like(private_key["value"]),
        "direct_l2_read_ready": direct_l2_read_ready,
        "l1_rest_api_key_ready": l1_rest_ready,
        "host": CLOB_API,
        "chain_id": POLYGON_CHAIN_ID,
        "private_key": _redacted_secret_presence(private_key),
        "funder_address": _redacted_address_presence(funder),
        "signature_type": {
            "value": signature_type_value,
            "name": signature_info.get("name", "UNKNOWN"),
            "requires_funder": bool(signature_info.get("requires_funder", False)),
            "description": signature_info.get("description", ""),
            "source": signature_type_raw["source"] if signature_type_raw["present"] else "default:0",
        },
        "l2_headers": {
            "required": list(REQUIRED_L2_HEADERS),
            "missing": [name for name, item in l2_headers.items() if not item["present"]],
            "present": {name: item["present"] for name, item in l2_headers.items()},
            "sources": {name: item["source"] for name, item in l2_headers.items() if item["present"]},
        },
        "l1_headers": {
            "required": list(L1_HEADER_NAMES),
            "missing": [name for name, item in l1_headers.items() if not item["present"]],
            "present": {name: item["present"] for name, item in l1_headers.items()},
            "sources": {name: item["source"] for name, item in l1_headers.items() if item["present"]},
        },
        "blockers": blockers,
        "warnings": warnings,
        "docs": {
            "authentication": "https://docs.polymarket.com/api-reference/authentication",
            "clients_and_sdks": "https://docs.polymarket.com/api-reference/clients-and-sdks",
        },
    }


def validate_sdk_trading_readiness(
    *,
    private_key: str,
    signature_type: int,
    funder_address: Optional[str],
    chain_id: int = POLYGON_CHAIN_ID,
    host: str = CLOB_API,
) -> Dict[str, Any]:
    if int(chain_id) != POLYGON_CHAIN_ID:
        raise PolymarketValidationError(f"Polymarket CLOB trading expects Polygon chain id {POLYGON_CHAIN_ID}.")
    if str(host).rstrip("/") != CLOB_API:
        raise PolymarketValidationError("Polymarket CLOB trading host must be the official CLOB API.")
    settings = {
        "private_key": private_key,
        "signature_type": signature_type,
        "funder_address": funder_address or "",
    }
    report = build_clob_auth_readiness(settings, environ={})
    if report["blockers"]:
        raise PolymarketValidationError("; ".join(report["blockers"]))
    return report


def parse_signature_type(value: Any) -> int:
    parsed, error = _parse_signature_type(value)
    if error is not None:
        raise PolymarketValidationError(error)
    if parsed not in SIGNATURE_TYPE_INFO:
        raise PolymarketValidationError(f"Unsupported Polymarket signature type: {parsed}.")
    return int(parsed)


def is_private_key_like(value: Any) -> bool:
    return bool(HEX_PRIVATE_KEY_RE.match(str(value or "").strip()))


def is_evm_address_like(value: Any) -> bool:
    return bool(EVM_ADDRESS_RE.match(str(value or "").strip()))


def redacted_address(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) <= 12:
        return "***"
    return f"{text[:6]}...{text[-4:]}"


def _first_config_or_env(
    settings: Mapping[str, Any],
    keys: tuple[str, ...],
    env: Mapping[str, str],
    env_vars: tuple[str, ...],
) -> Dict[str, Any]:
    for key in keys:
        value = settings.get(key)
        if value not in (None, ""):
            return {"present": True, "value": str(value), "source": f"config:{key}"}
    for env_var in env_vars:
        value = env.get(env_var)
        if value:
            return {"present": True, "value": str(value), "source": f"env:{env_var}"}
    return {"present": False, "value": "", "source": ""}


def _parse_signature_type(value: Any) -> tuple[int, Optional[str]]:
    raw = "0" if value in (None, "") else str(value).strip()
    try:
        return int(raw), None
    except (TypeError, ValueError):
        return 0, "Polymarket SIGNATURE_TYPE must be an integer."


def _header_presence(env: Mapping[str, str], names: tuple[str, ...]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for name in names:
        value = env.get(name)
        out[name] = {"present": bool(value), "source": f"env:{name}" if value else ""}
    return out


def _redacted_secret_presence(item: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "present": bool(item.get("present")),
        "source": str(item.get("source") or ""),
        "redacted": "***" if item.get("present") else "",
    }


def _redacted_address_presence(item: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "present": bool(item.get("present")),
        "source": str(item.get("source") or ""),
        "redacted": redacted_address(item.get("value")) if item.get("present") else "",
    }
