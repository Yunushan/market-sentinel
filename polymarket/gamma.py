from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional

from .endpoints import GAMMA_ENDPOINTS
from .http_client import PolymarketError, as_dict, request_json


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


def _get_json(endpoint_name: str, *, path: Optional[str] = None, params: Optional[Mapping[str, Any]] = None, timeout: float = 15.0) -> Any:
    return request_json(GAMMA_ENDPOINTS[endpoint_name], path=path, params=params, timeout=timeout)


def _list_response(data: Any) -> List[Dict[str, Any]]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        for key in ("data", "events", "markets", "tags", "series", "comments", "teams"):
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


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
    return as_dict(_get_json("public_search", params=params, timeout=timeout), endpoint_name="gamma.public-search")


def list_events(
    *,
    limit: int = 100,
    offset: int = 0,
    order: Optional[str] = None,
    ascending: Optional[bool] = None,
    timeout: float = 15.0,
    **filters: Any,
) -> List[Dict[str, Any]]:
    params: Dict[str, Any] = {
        "limit": max(0, min(int(limit), 500)),
        "offset": max(0, int(offset)),
        "order": order,
        "ascending": ascending,
    }
    params.update(filters)
    return _list_response(_get_json("events", params=params, timeout=timeout))


def list_events_keyset(
    *,
    limit: int = 100,
    after_cursor: Optional[str] = None,
    order: Optional[str] = None,
    ascending: Optional[bool] = None,
    timeout: float = 15.0,
    **filters: Any,
) -> Dict[str, Any]:
    params: Dict[str, Any] = {
        "limit": max(1, min(int(limit), 500)),
        "after_cursor": after_cursor,
        "order": order,
        "ascending": ascending,
    }
    params.update(filters)
    return as_dict(_get_json("events_keyset", params=params, timeout=timeout), endpoint_name="gamma.events-keyset")


def get_event_tags(event_id: str, timeout: float = 15.0) -> List[Dict[str, Any]]:
    return _list_response(_get_json("event_tags", path=f"/events/{event_id}/tags", timeout=timeout))


def list_markets(
    *,
    limit: int = 100,
    offset: int = 0,
    order: Optional[str] = None,
    ascending: Optional[bool] = None,
    timeout: float = 15.0,
    **filters: Any,
) -> List[Dict[str, Any]]:
    params: Dict[str, Any] = {
        "limit": max(0, min(int(limit), 500)),
        "offset": max(0, int(offset)),
        "order": order,
        "ascending": ascending,
    }
    params.update(filters)
    return _list_response(_get_json("markets", params=params, timeout=timeout))


def list_markets_keyset(
    *,
    limit: int = 100,
    after_cursor: Optional[str] = None,
    order: Optional[str] = None,
    ascending: Optional[bool] = None,
    timeout: float = 15.0,
    **filters: Any,
) -> Dict[str, Any]:
    params: Dict[str, Any] = {
        "limit": max(1, min(int(limit), 500)),
        "after_cursor": after_cursor,
        "order": order,
        "ascending": ascending,
    }
    params.update(filters)
    return as_dict(_get_json("markets_keyset", params=params, timeout=timeout), endpoint_name="gamma.markets-keyset")


def get_market_tags(market_id: str, timeout: float = 15.0) -> List[Dict[str, Any]]:
    return _list_response(_get_json("market_tags", path=f"/markets/{market_id}/tags", timeout=timeout))


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


def get_public_profile(address: str, timeout: float = 15.0) -> Dict[str, Any]:
    data = _get_json("public_profile", params={"address": address}, timeout=timeout)
    return data if isinstance(data, dict) else {}


def get_market_by_slug(slug: str, timeout: float = 15.0) -> Optional[Dict[str, Any]]:
    """Fetch a single market by slug."""
    slug = slug.strip().strip("/")
    if not slug:
        return None

    # Strategy 1: /markets/slug/{slug}
    try:
        data = _get_json("market_by_slug", path=f"/markets/slug/{slug}", timeout=timeout)
        return data if isinstance(data, dict) else None
    except PolymarketError:
        pass

    # Strategy 2: /markets?slug={slug}
    try:
        data = _get_json("markets", params={"slug": slug}, timeout=timeout)
        if isinstance(data, list):
            return data[0] if data else None
        if isinstance(data, dict) and "data" in data:
            arr = data.get("data") or []
            return arr[0] if arr else None
        return data if isinstance(data, dict) else None
    except PolymarketError:
        pass

    return None


def get_market_by_id(market_id: str, timeout: float = 15.0) -> Optional[Dict[str, Any]]:
    """Fetch a single market by numeric id."""
    market_id = str(market_id or "").strip()
    if not market_id:
        return None
    try:
        data = _get_json("market_by_id", path=f"/markets/{market_id}", timeout=timeout)
        return data if isinstance(data, dict) else None
    except PolymarketError:
        pass
    return None


def get_event_by_slug(slug: str, timeout: float = 15.0) -> Optional[Dict[str, Any]]:
    """Fetch a single event by slug."""
    slug = slug.strip().strip("/")
    if not slug:
        return None

    # Strategy 1: /events/slug/{slug}
    try:
        data = _get_json("event_by_slug", path=f"/events/slug/{slug}", timeout=timeout)
        return data if isinstance(data, dict) else None
    except PolymarketError:
        pass

    # Strategy 2: /events?slug={slug}
    try:
        data = _get_json("events", params={"slug": slug}, timeout=timeout)
        if isinstance(data, list):
            return data[0] if data else None
        if isinstance(data, dict) and "data" in data:
            arr = data.get("data") or []
            return arr[0] if arr else None
        return data if isinstance(data, dict) else None
    except PolymarketError:
        pass

    return None


def get_event_by_id(event_id: str, timeout: float = 15.0) -> Optional[Dict[str, Any]]:
    """Fetch a single event by numeric id."""
    event_id = str(event_id or "").strip()
    if not event_id:
        return None
    try:
        data = _get_json("event_by_id", path=f"/events/{event_id}", timeout=timeout)
        return data if isinstance(data, dict) else None
    except PolymarketError:
        pass
    return None


def list_tags(
    *,
    limit: int = 100,
    offset: int = 0,
    order: Optional[str] = None,
    ascending: Optional[bool] = None,
    timeout: float = 15.0,
    **filters: Any,
) -> List[Dict[str, Any]]:
    params: Dict[str, Any] = {
        "limit": max(0, int(limit)),
        "offset": max(0, int(offset)),
        "order": order,
        "ascending": ascending,
    }
    params.update(filters)
    return _list_response(_get_json("tags", params=params, timeout=timeout))


def get_tag_by_id(tag_id: str, timeout: float = 15.0) -> Dict[str, Any]:
    data = _get_json("tag_by_id", path=f"/tags/{tag_id}", timeout=timeout)
    return data if isinstance(data, dict) else {}


def get_tag_by_slug(slug: str, timeout: float = 15.0) -> Dict[str, Any]:
    data = _get_json("tag_by_slug", path=f"/tags/slug/{slug}", timeout=timeout)
    return data if isinstance(data, dict) else {}


def get_related_tag_relationships_by_id(
    tag_id: str,
    *,
    omit_empty: Optional[bool] = None,
    status: Optional[str] = None,
    timeout: float = 15.0,
) -> List[Dict[str, Any]]:
    return _list_response(
        _get_json(
            "tag_relationships_by_id",
            path=f"/tags/{tag_id}/related-tags",
            params={"omit_empty": omit_empty, "status": status},
            timeout=timeout,
        )
    )


def get_related_tag_relationships_by_slug(
    slug: str,
    *,
    omit_empty: Optional[bool] = None,
    status: Optional[str] = None,
    timeout: float = 15.0,
) -> List[Dict[str, Any]]:
    return _list_response(
        _get_json(
            "tag_relationships_by_slug",
            path=f"/tags/slug/{slug}/related-tags",
            params={"omit_empty": omit_empty, "status": status},
            timeout=timeout,
        )
    )


def get_tags_related_to_id(
    tag_id: str,
    *,
    omit_empty: Optional[bool] = None,
    status: Optional[str] = None,
    timeout: float = 15.0,
) -> List[Dict[str, Any]]:
    return _list_response(
        _get_json(
            "tags_related_to_id",
            path=f"/tags/{tag_id}/related-tags/tags",
            params={"omit_empty": omit_empty, "status": status},
            timeout=timeout,
        )
    )


def get_tags_related_to_slug(
    slug: str,
    *,
    omit_empty: Optional[bool] = None,
    status: Optional[str] = None,
    timeout: float = 15.0,
) -> List[Dict[str, Any]]:
    return _list_response(
        _get_json(
            "tags_related_to_slug",
            path=f"/tags/slug/{slug}/related-tags/tags",
            params={"omit_empty": omit_empty, "status": status},
            timeout=timeout,
        )
    )


def list_series(
    *,
    limit: int = 100,
    offset: int = 0,
    order: Optional[str] = None,
    ascending: Optional[bool] = None,
    timeout: float = 15.0,
    **filters: Any,
) -> List[Dict[str, Any]]:
    params: Dict[str, Any] = {
        "limit": max(0, int(limit)),
        "offset": max(0, int(offset)),
        "order": order,
        "ascending": ascending,
    }
    params.update(filters)
    return _list_response(_get_json("series", params=params, timeout=timeout))


def get_series_by_id(series_id: str, timeout: float = 15.0) -> Dict[str, Any]:
    data = _get_json("series_by_id", path=f"/series/{series_id}", timeout=timeout)
    return data if isinstance(data, dict) else {}


def list_comments(
    *,
    limit: int = 100,
    offset: int = 0,
    order: Optional[str] = None,
    ascending: Optional[bool] = None,
    timeout: float = 15.0,
    **filters: Any,
) -> List[Dict[str, Any]]:
    params: Dict[str, Any] = {
        "limit": max(0, int(limit)),
        "offset": max(0, int(offset)),
        "order": order,
        "ascending": ascending,
    }
    params.update(filters)
    return _list_response(_get_json("comments", params=params, timeout=timeout))


def get_comment_by_id(comment_id: str, timeout: float = 15.0) -> Dict[str, Any]:
    data = _get_json("comment_by_id", path=f"/comments/{comment_id}", timeout=timeout)
    return data if isinstance(data, dict) else {}


def get_comments_by_user_address(
    user_address: str,
    *,
    limit: int = 100,
    offset: int = 0,
    order: Optional[str] = None,
    ascending: Optional[bool] = None,
    timeout: float = 15.0,
) -> List[Dict[str, Any]]:
    return _list_response(
        _get_json(
            "comments_by_user",
            path=f"/comments/user_address/{user_address}",
            params={"limit": max(0, int(limit)), "offset": max(0, int(offset)), "order": order, "ascending": ascending},
            timeout=timeout,
        )
    )


def get_sports_metadata(timeout: float = 15.0) -> List[Dict[str, Any]]:
    return _list_response(_get_json("sports", timeout=timeout))


def get_sports_market_types(timeout: float = 15.0) -> Dict[str, Any]:
    data = _get_json("sports_market_types", timeout=timeout)
    return data if isinstance(data, dict) else {"marketTypes": data}


def list_teams(
    *,
    limit: int = 100,
    offset: int = 0,
    order: Optional[str] = None,
    ascending: Optional[bool] = None,
    timeout: float = 15.0,
    **filters: Any,
) -> List[Dict[str, Any]]:
    params: Dict[str, Any] = {
        "limit": max(0, int(limit)),
        "offset": max(0, int(offset)),
        "order": order,
        "ascending": ascending,
    }
    params.update(filters)
    return _list_response(_get_json("teams", params=params, timeout=timeout))


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
