from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
import requests

from .constants import GAMMA_API


@dataclass
class ProfileResult:
    pseudonym: str
    proxy_wallet: str
    profile_image: str = ""
    display_username_public: bool = True

@dataclass
class MarketOutcome:
    outcome: str
    token_id: str
    price: Optional[float] = None


def public_search(
    q: str,
    *,
    search_profiles: bool = True,
    search_tags: bool = False,
    limit_per_type: int = 5,
    page: int = 1,
    optimized: bool = True,
    timeout: float = 15.0,
) -> Dict[str, Any]:
    """Gamma public search across events/markets/profiles."""
    params = {
        "q": q,
        "search_profiles": str(search_profiles).lower(),
        "search_tags": str(search_tags).lower(),
        "limit_per_type": limit_per_type,
        "page": page,
        "optimized": str(optimized).lower(),
    }
    r = requests.get(f"{GAMMA_API}/public-search", params=params, timeout=timeout)
    r.raise_for_status()
    return r.json()


def search_profiles(q: str, limit: int = 10) -> List[ProfileResult]:
    data = public_search(q, search_profiles=True, search_tags=False, limit_per_type=limit)
    profiles = data.get("profiles") or []
    out: List[ProfileResult] = []
    for p in profiles:
        proxy = p.get("proxyWallet") or ""
        pseudo = p.get("pseudonym") or p.get("name") or ""
        out.append(
            ProfileResult(
                pseudonym=str(pseudo),
                proxy_wallet=str(proxy),
                profile_image=str(p.get("profileImage") or ""),
                display_username_public=bool(p.get("displayUsernamePublic", True)),
            )
        )
    return [x for x in out if x.proxy_wallet]


def get_market_by_slug(slug: str, timeout: float = 15.0) -> Optional[Dict[str, Any]]:
    """Fetch a single market by slug."""
    slug = slug.strip().strip("/")
    if not slug:
        return None

    # Strategy 1: /markets/slug/{slug}
    try:
        r = requests.get(f"{GAMMA_API}/markets/slug/{slug}", timeout=timeout)
        if r.status_code == 200:
            return r.json()
    except requests.RequestException:
        pass

    # Strategy 2: /markets?slug={slug}
    try:
        r = requests.get(f"{GAMMA_API}/markets", params={"slug": slug}, timeout=timeout)
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, list):
                return data[0] if data else None
            if isinstance(data, dict) and "data" in data:
                # Some APIs return {data:[...]}
                arr = data.get("data") or []
                return arr[0] if arr else None
            return data
    except requests.RequestException:
        pass

    return None


def get_market_by_id(market_id: str, timeout: float = 15.0) -> Optional[Dict[str, Any]]:
    """Fetch a single market by numeric id."""
    market_id = str(market_id or "").strip()
    if not market_id:
        return None
    try:
        r = requests.get(f"{GAMMA_API}/markets/{market_id}", timeout=timeout)
        if r.status_code == 200:
            return r.json()
    except requests.RequestException:
        pass
    return None


def get_event_by_slug(slug: str, timeout: float = 15.0) -> Optional[Dict[str, Any]]:
    """Fetch a single event by slug."""
    slug = slug.strip().strip("/")
    if not slug:
        return None

    # Strategy 1: /events/slug/{slug}
    try:
        r = requests.get(f"{GAMMA_API}/events/slug/{slug}", timeout=timeout)
        if r.status_code == 200:
            return r.json()
    except requests.RequestException:
        pass

    # Strategy 2: /events?slug={slug}
    try:
        r = requests.get(f"{GAMMA_API}/events", params={"slug": slug}, timeout=timeout)
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, list):
                return data[0] if data else None
            if isinstance(data, dict) and "data" in data:
                arr = data.get("data") or []
                return arr[0] if arr else None
            return data
    except requests.RequestException:
        pass

    return None


def get_event_by_id(event_id: str, timeout: float = 15.0) -> Optional[Dict[str, Any]]:
    """Fetch a single event by numeric id."""
    event_id = str(event_id or "").strip()
    if not event_id:
        return None
    try:
        r = requests.get(f"{GAMMA_API}/events/{event_id}", timeout=timeout)
        if r.status_code == 200:
            return r.json()
    except requests.RequestException:
        pass
    return None


def parse_market_outcomes(market: Dict[str, Any]) -> List[MarketOutcome]:
    """Extract outcomes + token ids (+ optional current prices) from a Gamma market object."""
    token_ids = market.get("clobTokenIds") or []
    outcomes = market.get("outcomes") or []
    prices = market.get("outcomePrices") or []

    # Normalize to strings (Gamma sometimes returns JSON-encoded lists)
    if isinstance(token_ids, str):
        # attempt to parse like '["1","2"]'
        try:
            import json
            token_ids = json.loads(token_ids)
        except Exception:
            token_ids = [token_ids]
    if isinstance(outcomes, str):
        try:
            import json
            outcomes = json.loads(outcomes)
        except Exception:
            outcomes = [outcomes]
    if isinstance(prices, str):
        try:
            import json
            prices = json.loads(prices)
        except Exception:
            prices = []

    out: List[MarketOutcome] = []
    for i, token in enumerate(token_ids):
        name = outcomes[i] if i < len(outcomes) else f"Outcome {i}"
        price = None
        if i < len(prices):
            try:
                price = float(prices[i])
            except Exception:
                price = None
        out.append(MarketOutcome(outcome=str(name), token_id=str(token), price=price))
    return out
