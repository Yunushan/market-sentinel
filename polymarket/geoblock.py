from __future__ import annotations

from typing import Any, Dict
import requests


def check_geoblock(timeout: float = 10.0) -> Dict[str, Any]:
    """
    Polymarket geoblock endpoint:
    GET https://polymarket.com/api/geoblock
    Returns {blocked:boolean, ip:string, country:string, region:string}
    """
    r = requests.get("https://polymarket.com/api/geoblock", timeout=timeout)
    r.raise_for_status()
    data = r.json()
    return data if isinstance(data, dict) else {}
