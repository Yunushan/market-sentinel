from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

from .endpoints import BRIDGE_ENDPOINTS
from .http_client import request_json


def _get_json(endpoint_name: str, *, path: Optional[str] = None, params: Optional[Mapping[str, Any]] = None, timeout: float = 15.0) -> Any:
    return request_json(BRIDGE_ENDPOINTS[endpoint_name], path=path, params=params, timeout=timeout)


def _post_json(endpoint_name: str, payload: Mapping[str, Any], *, timeout: float = 15.0) -> Any:
    return request_json(BRIDGE_ENDPOINTS[endpoint_name], payload=dict(payload), timeout=timeout)


def get_supported_assets(*, timeout: float = 15.0) -> Dict[str, Any]:
    data = _get_json("supported_assets", timeout=timeout)
    return data if isinstance(data, dict) else {}


def create_deposit_addresses(address: str, *, timeout: float = 15.0) -> Dict[str, Any]:
    data = _post_json("deposit", {"address": address}, timeout=timeout)
    return data if isinstance(data, dict) else {}


def get_quote(
    *,
    from_amount_base_unit: str,
    from_chain_id: str,
    from_token_address: str,
    recipient_address: str,
    to_chain_id: str,
    to_token_address: str,
    timeout: float = 15.0,
) -> Dict[str, Any]:
    data = _post_json(
        "quote",
        {
            "fromAmountBaseUnit": from_amount_base_unit,
            "fromChainId": from_chain_id,
            "fromTokenAddress": from_token_address,
            "recipientAddress": recipient_address,
            "toChainId": to_chain_id,
            "toTokenAddress": to_token_address,
        },
        timeout=timeout,
    )
    return data if isinstance(data, dict) else {}


def get_transaction_status(address: str, *, timeout: float = 15.0) -> Dict[str, Any]:
    data = _get_json("status", path=f"/status/{address}", timeout=timeout)
    return data if isinstance(data, dict) else {}


def create_withdrawal_addresses(
    *,
    address: str,
    to_chain_id: str,
    to_token_address: str,
    recipient_addr: str,
    timeout: float = 15.0,
) -> Dict[str, Any]:
    data = _post_json(
        "withdraw",
        {
            "address": address,
            "toChainId": to_chain_id,
            "toTokenAddress": to_token_address,
            "recipientAddr": recipient_addr,
        },
        timeout=timeout,
    )
    return data if isinstance(data, dict) else {}
