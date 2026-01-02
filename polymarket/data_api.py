from __future__ import annotations

from typing import Any, Dict, List, Optional
import requests

from .constants import DATA_API


def get_activity(
    user: str,
    *,
    limit: int = 50,
    offset: int = 0,
    types: Optional[List[str]] = None,
    side: Optional[str] = None,
    market: Optional[List[str]] = None,  # condition IDs
    start: Optional[int] = None,
    end: Optional[int] = None,
    timeout: float = 15.0,
) -> List[Dict[str, Any]]:
    """
    Data API: /activity
    Returns trades and activity history for a specified wallet.
    """
    params: Dict[str, Any] = {
        "user": user,
        "limit": max(0, min(int(limit), 500)),
        "offset": max(0, min(int(offset), 10000)),
        "sortDirection": "DESC",
        "sortBy": "TIMESTAMP",
    }
    if types:
        # The docs use enum list; requests will encode repeated params if list passed.
        params["type"] = types
    if side:
        params["side"] = side
    if market:
        params["market"] = market
    if start is not None:
        params["start"] = int(start)
    if end is not None:
        params["end"] = int(end)

    r = requests.get(f"{DATA_API}/activity", params=params, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    return data if isinstance(data, list) else []


def get_positions(
    user: str,
    *,
    limit: int = 100,
    offset: int = 0,
    timeout: float = 15.0,
) -> List[Dict[str, Any]]:
    params = {
        "user": user,
        "limit": max(0, min(int(limit), 500)),
        "offset": max(0, min(int(offset), 10000)),
    }
    r = requests.get(f"{DATA_API}/positions", params=params, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    return data if isinstance(data, list) else []


def get_trades(
    user: str,
    *,
    limit: int = 100,
    offset: int = 0,
    timeout: float = 15.0,
) -> List[Dict[str, Any]]:
    params = {
        "user": user,
        "limit": max(0, min(int(limit), 500)),
        "offset": max(0, min(int(offset), 10000)),
    }
    r = requests.get(f"{DATA_API}/trades", params=params, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    return data if isinstance(data, list) else []
