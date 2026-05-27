from __future__ import annotations

from typing import Any, Dict, List, Optional
import requests

from .constants import DATA_API


def _list_payload(data: Any, keys: List[str]) -> List[Dict[str, Any]]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        for key in keys:
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


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


def get_leaderboard(
    *,
    limit: int = 50,
    offset: int = 0,
    sort_by: str = "PNL",
    sort_direction: str = "DESC",
    period: str = "all",
    timeout: float = 15.0,
) -> List[Dict[str, Any]]:
    """
    Data API: /v1/leaderboard
    Returns public trader leaderboard rows.
    """
    clean_sort = str(sort_by or "PNL").strip().upper()
    if clean_sort not in {"PNL", "VOL"}:
        clean_sort = "PNL"
    clean_direction = str(sort_direction or "DESC").strip().upper()
    if clean_direction not in {"ASC", "DESC"}:
        clean_direction = "DESC"
    clean_period = str(period or "all").strip().lower()
    params = {
        "limit": max(1, min(int(limit), 50)),
        "offset": max(0, min(int(offset), 10000)),
        "sortBy": clean_sort,
        "sortDirection": clean_direction,
        "period": clean_period,
    }
    r = requests.get(f"{DATA_API}/v1/leaderboard", params=params, timeout=timeout)
    r.raise_for_status()
    return _list_payload(r.json(), ["data", "leaderboard", "users", "results"])
