from __future__ import annotations

from typing import Any, Dict, Optional, Tuple
import requests

from .constants import CLOB_API


def get_book(token_id: str, timeout: float = 10.0) -> Dict[str, Any]:
    r = requests.get(f"{CLOB_API}/book", params={"token_id": token_id}, timeout=timeout)
    r.raise_for_status()
    return r.json()


def best_bid_ask_from_book(book: Dict[str, Any]) -> Tuple[Optional[float], Optional[float]]:
    # Docs/examples sometimes use bids/asks; elsewhere buys/sells.
    bids = book.get("bids") or book.get("buys") or []
    asks = book.get("asks") or book.get("sells") or []
    best_bid = None
    best_ask = None
    if bids:
        try:
            best_bid = float(bids[0]["price"])
        except Exception:
            pass
    if asks:
        try:
            best_ask = float(asks[0]["price"])
        except Exception:
            pass
    return best_bid, best_ask


def get_midpoint(token_id: str, timeout: float = 10.0) -> Optional[float]:
    r = requests.get(f"{CLOB_API}/midpoint", params={"token_id": token_id}, timeout=timeout)
    if r.status_code != 200:
        return None
    try:
        data = r.json()
        # docs are inconsistent; handle numbers or dicts
        if isinstance(data, dict):
            # possible {"mid": "..."} or {"midpoint": "..."}
            for k in ("mid", "midpoint", "price"):
                if k in data:
                    return float(data[k])
        if isinstance(data, (int, float, str)):
            return float(data)
    except Exception:
        return None
    return None
