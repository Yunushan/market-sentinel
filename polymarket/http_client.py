from __future__ import annotations

from dataclasses import dataclass
import json
import time
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, TypeVar

import requests

from .endpoints import PolymarketEndpoint


TRANSIENT_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}
SAFE_RETRY_METHODS = {"GET", "HEAD", "OPTIONS"}


class PolymarketError(RuntimeError):
    """Base exception for Polymarket API wrapper failures."""


class PolymarketValidationError(PolymarketError, ValueError):
    """Raised before sending requests when local input violates documented contracts."""


class PolymarketHTTPError(PolymarketError):
    def __init__(
        self,
        message: str,
        *,
        service: str,
        method: str,
        url: str,
        status_code: Optional[int] = None,
        response_body: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.service = service
        self.method = method
        self.url = url
        self.status_code = status_code
        self.response_body = response_body


class PolymarketRateLimitError(PolymarketHTTPError):
    """Raised after retry handling cannot recover from a 429 response."""


class PolymarketResponseError(PolymarketError):
    """Raised when an endpoint returns malformed or unexpected JSON."""


T = TypeVar("T")


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 2
    backoff_seconds: float = 0.25
    max_sleep_seconds: float = 2.0

    def attempts_for(self, method: str) -> int:
        method = str(method).upper()
        if method in SAFE_RETRY_METHODS:
            return max(1, int(self.max_attempts))
        return 1


DEFAULT_RETRY_POLICY = RetryPolicy()


def compact_params(params: Mapping[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in params.items() if value is not None}


def comma_join(values: Optional[Iterable[str]]) -> Optional[str]:
    if values is None:
        return None
    joined = ",".join(str(value) for value in values if str(value))
    return joined or None


def build_batch(items: Iterable[Any], *, max_items: Optional[int], name: str) -> List[Any]:
    cleaned = [item for item in items if item is not None and str(item)]
    if not cleaned:
        raise PolymarketValidationError(f"{name} requires at least one item.")
    if max_items is not None and len(cleaned) > max_items:
        raise PolymarketValidationError(f"{name} accepts at most {max_items} items; got {len(cleaned)}.")
    return cleaned


def endpoint_url(endpoint: PolymarketEndpoint, path: Optional[str] = None) -> str:
    return f"{endpoint.base_url}{path or endpoint.path}"


def request_json(
    endpoint: PolymarketEndpoint,
    *,
    path: Optional[str] = None,
    params: Optional[Mapping[str, Any]] = None,
    payload: Optional[Any] = None,
    headers: Optional[Mapping[str, str]] = None,
    timeout: float = 15.0,
    retry_policy: RetryPolicy = DEFAULT_RETRY_POLICY,
) -> Any:
    response = _request(endpoint, path=path, params=params, payload=payload, headers=headers, timeout=timeout, retry_policy=retry_policy)
    try:
        return response.json()
    except ValueError as exc:
        raise PolymarketResponseError(
            f"{endpoint.service} {endpoint.method} {path or endpoint.path} returned non-JSON response."
        ) from exc


def request_bytes(
    endpoint: PolymarketEndpoint,
    *,
    path: Optional[str] = None,
    params: Optional[Mapping[str, Any]] = None,
    headers: Optional[Mapping[str, str]] = None,
    timeout: float = 30.0,
    retry_policy: RetryPolicy = DEFAULT_RETRY_POLICY,
) -> bytes:
    response = _request(endpoint, path=path, params=params, headers=headers, timeout=timeout, retry_policy=retry_policy)
    return bytes(response.content)


def as_dict(data: Any, *, endpoint_name: str) -> Dict[str, Any]:
    if isinstance(data, dict):
        return data
    raise PolymarketResponseError(f"{endpoint_name} expected an object response, got {type(data).__name__}.")


def as_list(data: Any, *, endpoint_name: str) -> List[Any]:
    if isinstance(data, list):
        return data
    raise PolymarketResponseError(f"{endpoint_name} expected an array response, got {type(data).__name__}.")


def as_list_of_dicts(data: Any, *, endpoint_name: str, wrapper_keys: Sequence[str] = ()) -> List[Dict[str, Any]]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        for key in wrapper_keys:
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    raise PolymarketResponseError(f"{endpoint_name} expected an array response, got {type(data).__name__}.")


def optional_price(data: Any, keys: Sequence[str]) -> Optional[float]:
    try:
        if isinstance(data, dict):
            for key in keys:
                if key in data:
                    return float(data[key])
        if isinstance(data, (int, float, str)):
            return float(data)
    except Exception:
        return None
    return None


def _request(
    endpoint: PolymarketEndpoint,
    *,
    path: Optional[str] = None,
    params: Optional[Mapping[str, Any]] = None,
    payload: Optional[Any] = None,
    headers: Optional[Mapping[str, str]] = None,
    timeout: float,
    retry_policy: RetryPolicy,
) -> requests.Response:
    method = endpoint.method.upper()
    url = endpoint_url(endpoint, path)
    request_headers = dict(headers or {})
    if payload is not None:
        request_headers.setdefault("Content-Type", "application/json")
    attempts = retry_policy.attempts_for(method)
    last_exc: Optional[BaseException] = None

    for attempt in range(1, attempts + 1):
        try:
            response = requests.request(
                method,
                url,
                params=compact_params(params or {}),
                json=payload,
                headers=request_headers or None,
                timeout=timeout,
            )
        except requests.RequestException as exc:
            last_exc = exc
            if attempt < attempts:
                _sleep_before_retry(attempt, retry_policy)
                continue
            raise PolymarketHTTPError(
                f"{endpoint.service} {method} {url} failed: {exc}",
                service=endpoint.service,
                method=method,
                url=url,
            ) from exc

        if response.status_code < 400:
            return response

        if response.status_code in TRANSIENT_STATUS_CODES and attempt < attempts:
            _sleep_before_retry(attempt, retry_policy, response=response)
            continue

        error_cls = PolymarketRateLimitError if response.status_code == 429 else PolymarketHTTPError
        raise error_cls(
            f"{endpoint.service} {method} {url} returned HTTP {response.status_code}.",
            service=endpoint.service,
            method=method,
            url=url,
            status_code=response.status_code,
            response_body=_response_preview(response),
        )

    if last_exc is not None:
        raise PolymarketHTTPError(
            f"{endpoint.service} {method} {url} failed: {last_exc}",
            service=endpoint.service,
            method=method,
            url=url,
        ) from last_exc
    raise PolymarketHTTPError(f"{endpoint.service} {method} {url} failed.", service=endpoint.service, method=method, url=url)


def _sleep_before_retry(attempt: int, retry_policy: RetryPolicy, *, response: Optional[requests.Response] = None) -> None:
    retry_after = _retry_after_seconds(response)
    delay = retry_after if retry_after is not None else retry_policy.backoff_seconds * (2 ** max(0, attempt - 1))
    time.sleep(min(max(0.0, delay), retry_policy.max_sleep_seconds))


def _retry_after_seconds(response: Optional[requests.Response]) -> Optional[float]:
    if response is None:
        return None
    value = response.headers.get("Retry-After") if hasattr(response, "headers") else None
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _response_preview(response: requests.Response) -> str:
    try:
        text = response.text
    except Exception:
        try:
            return json.dumps(response.json())[:500]
        except Exception:
            return ""
    return str(text)[:500]
