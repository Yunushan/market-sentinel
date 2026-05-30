from __future__ import annotations

import argparse
import json
import mimetypes
import os
import posixpath
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple, cast
from urllib.parse import parse_qs, unquote, urlparse

from core.models import AppConfig, CopyTradeSettings, PaperTradeRecord, PriceAlert, UIDesign, WalletWatch
from core.storage import DEFAULT_CONFIG_PATH, load_config, save_config
from market_adapters import build_default_registry
from market_adapters.registry import AdapterRegistry
from market_adapters.catalog import MARKET_CATALOG, MARKET_IDS
from market_adapters.errors import UnsupportedFeatureError
from market_adapters.types import (
    MarketCapabilities,
    MarketMetadata,
    OrderBookSnapshot,
    PaperOrderRequest,
    PaperOrderResult,
    PriceSnapshot,
)
from polymarket import data_api, gamma
from polymarket.analytics_cache import (
    DEFAULT_ANALYTICS_CACHE_MAX_ENTRIES,
    DEFAULT_ANALYTICS_CACHE_TTL_SECONDS,
    POLYMARKET_MDD_AUDIT_KIND,
    analytics_cache_health,
    analytics_cache_summary,
    list_analytics_artifacts,
    load_analytics_artifact,
    mdd_payload_to_csv,
    purge_analytics_artifacts,
    store_analytics_artifact,
)
from polymarket.auth_readiness import build_clob_auth_readiness
from polymarket.coverage import polymarket_official_api_coverage
from polymarket.credential_runbook import build_polymarket_credential_runbook
from polymarket.http_client import PolymarketHTTPError, PolymarketRateLimitError
from polymarket.live_verification import (
    ABSOLUTE_MAX_VERIFY_NOTIONAL,
    ABSOLUTE_MAX_VERIFY_SIZE,
    CONFIRM_LIVE_ORDER_CANCEL,
    build_live_validation_stage_gates,
)
from polymarket.live_reports import (
    list_live_validation_report_decisions,
    list_live_validation_coverage_promotion_proposal_snapshots,
    load_live_validation_coverage_promotion_proposal_snapshot,
    live_validation_coverage_promotion_proposal,
    live_validation_coverage_promotion_proposal_export_filename,
    live_validation_coverage_promotion_proposal_markdown,
    live_validation_promotion_proposal_snapshot_export_filename,
    live_validation_promotion_proposal_snapshot_markdown,
    live_validation_report_review_bundle,
    live_validation_report_review_export_filename,
    live_validation_report_review_markdown,
    live_validation_report_decisions_markdown,
    live_validation_report_promotion_inventory,
    list_live_validation_reports,
    load_live_validation_report,
    purge_live_validation_coverage_promotion_proposal_snapshots,
    purge_live_validation_reports,
    record_live_validation_report_decision,
    store_live_validation_coverage_promotion_proposal_snapshot,
    store_live_validation_report,
)
from polymarket.live_report_schema import LiveValidationReportSchemaError, parse_live_validation_report_json
from polymarket.mdd import (
    DEFAULT_CACHE_TTL_SECONDS as POLYMARKET_MDD_CACHE_TTL_SECONDS,
    MDD_MARK_REPLAY_ASSUMPTIONS,
    MDD_MARK_REPLAY_LIMITATIONS,
    MDD_ACCOUNTING_ASSUMPTIONS,
    MDD_ACCOUNTING_LIMITATIONS,
    MDD_METHOD_MARK_REPLAY,
    MDD_METHOD_V2,
    MDD_PCT_BASIS_V2,
    MDD_V2_ASSUMPTIONS,
    MDD_V2_LIMITATIONS,
    polymarket_user_mdd_payload_mark_replay,
    polymarket_user_mdd_payload_v2,
)
from polymarket.util import normalize_wallet
from polymarket.ws_user import build_user_subscription


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_FRONTEND_DIR = PROJECT_ROOT / "frontend" / "dist"
API_VERSION = "0.1.0"
MAX_JSON_BODY_BYTES = 1_000_000
PYTHON_GUI_COMMAND = "python app.py"
PYTHON_GUI_SCRIPT = "run_gui.bat"
REACT_DEV_COMMAND = "run_web_gui_dev.bat"
REACT_DEV_MANUAL_COMMAND = "python web_api.py --host 127.0.0.1 --port 8765 + cd frontend && npm run dev"
REACT_BUILD_COMMAND = "cd frontend && npm install && npm run build"
REACT_PROD_COMMAND = "run_web_gui_prod.bat"
API_ROUTES = {
    "GET": [
        "/api/health",
        "/api/state",
        "/api/config",
        "/api/markets",
        "/api/alerts",
        "/api/wallets",
        "/api/copy",
        "/api/live-safety",
        "/api/paper",
        "/api/paper/history",
        "/api/paper/positions",
        "/api/polymarket/users/search",
        "/api/polymarket/users/leaderboard",
        "/api/polymarket/users/mdd",
        "/api/polymarket/users/mdd/cache",
        "/api/polymarket/users/mdd/cache/health",
        "/api/polymarket/users/mdd/export.json",
        "/api/polymarket/users/mdd/export.csv",
        "/api/polymarket/coverage",
        "/api/polymarket/clob-readiness",
        "/api/polymarket/live-validation",
        "/api/polymarket/live-validation/reports",
        "/api/polymarket/live-validation/reports/{key}",
        "/api/polymarket/live-validation/reports/{key}/export.json",
        "/api/polymarket/live-validation/reports/{key}/review.json",
        "/api/polymarket/live-validation/reports/{key}/review.md",
        "/api/polymarket/live-validation/decisions",
        "/api/polymarket/live-validation/decisions/export.json",
        "/api/polymarket/live-validation/decisions/export.md",
        "/api/polymarket/live-validation/promotion-proposal",
        "/api/polymarket/live-validation/promotion-proposal/export.json",
        "/api/polymarket/live-validation/promotion-proposal/export.md",
        "/api/polymarket/live-validation/promotion-proposal/snapshots",
        "/api/polymarket/live-validation/promotion-proposal/snapshots/{key}",
        "/api/polymarket/live-validation/promotion-proposal/snapshots/{key}/export.json",
        "/api/polymarket/live-validation/promotion-proposal/snapshots/{key}/export.md",
    ],
    "PATCH": [
        "/api/config",
        "/api/markets/{market_id}",
        "/api/wallets/{wallet_id}",
        "/api/wallets/polling",
        "/api/copy",
    ],
    "POST": [
        "/api/alerts",
        "/api/alerts/refresh",
        "/api/alerts/{alert_id}/refresh",
        "/api/wallets",
        "/api/wallets/poll",
        "/api/copy/preview",
        "/api/live-safety/preflight",
        "/api/paper/quote",
        "/api/paper/quote-limit",
        "/api/paper/preview-impact",
        "/api/paper/orders",
        "/api/paper/history/use",
        "/api/paper/history/clear",
        "/api/paper/positions/use",
        "/api/paper/marks/refresh",
        "/api/paper/marks/refresh-selected",
        "/api/paper/marks/clear",
        "/api/paper/marks/clear-selected",
        "/api/polymarket/users/mdd/cache/purge",
        "/api/polymarket/live-validation/reports",
        "/api/polymarket/live-validation/decisions",
        "/api/polymarket/live-validation/promotion-proposal/snapshots",
    ],
    "DELETE": [
        "/api/alerts/{alert_id}",
        "/api/wallets/{wallet_id}",
        "/api/polymarket/users/mdd/cache/{key}",
        "/api/polymarket/live-validation/reports/{key}",
        "/api/polymarket/live-validation/promotion-proposal/snapshots/{key}",
    ],
}
SENSITIVE_SETTING_FRAGMENTS = (
    "api_key",
    "apikey",
    "secret",
    "token",
    "password",
    "private",
    "cookie",
    "session",
)
POLYMARKET_L2_HEADERS = ("POLY_ADDRESS", "POLY_API_KEY", "POLY_PASSPHRASE", "POLY_SIGNATURE", "POLY_TIMESTAMP")
POLYMARKET_RELAYER_HEADERS = ("RELAYER_API_KEY", "RELAYER_API_KEY_ADDRESS")
POLYMARKET_USER_WS_KEYS = ("POLY_API_KEY", "POLY_API_SECRET", "POLY_SECRET", "POLY_PASSPHRASE")
LIVE_SETTING_KEYS = {
    "live_trading_enabled",
    "live_trading_confirmed",
    "live_trading_kill_switch",
    "live_trading_max_size",
    "live_trading_max_notional",
}
ALERT_SOURCE_OPTIONS = [
    {"id": "last_trade", "label": "Last trade"},
    {"id": "midpoint", "label": "Midpoint"},
    {"id": "best_bid", "label": "Best bid"},
    {"id": "best_ask", "label": "Best ask"},
]
ALERT_SOURCE_IDS = {str(option["id"]) for option in ALERT_SOURCE_OPTIONS}
ALERT_DIRECTIONS = {"above", "below"}


def _json_bytes(payload: Dict[str, Any]) -> bytes:
    return json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")


def _read_json_body(handler: BaseHTTPRequestHandler) -> Dict[str, Any]:
    try:
        length = int(handler.headers.get("Content-Length") or 0)
    except ValueError as exc:
        raise ValueError("Content-Length must be an integer.") from exc
    if length > MAX_JSON_BODY_BYTES:
        raise ValueError("JSON request body is too large.")
    if length <= 0:
        return {}
    raw = handler.rfile.read(length)
    if not raw:
        return {}
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("JSON request body must be UTF-8.") from exc
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("JSON request body must be an object.")
    return data


def api_error_payload(
    status: int,
    code: str,
    message: str,
    details: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    error: Dict[str, Any] = {
        "code": code,
        "message": message,
        "status": int(status),
    }
    if details:
        error["details"] = sanitize_audit_value(dict(details))
    return {"ok": False, "error": error}


def _paper_record_signed_size(record: PaperTradeRecord) -> float:
    size = float(record.filled_size or record.size or 0.0)
    side = str(record.side or "").upper()
    if side in {"SELL", "LAY"}:
        return -size
    if side in {"BUY", "BACK"}:
        return size
    return 0.0


def _safe_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    number = _safe_float(value, None)
    if number is None:
        return default
    return int(number)


def _clamp_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(_safe_int(value, default), maximum))


def _query_value(params: Mapping[str, List[str]], key: str, default: str = "") -> str:
    values = params.get(key)
    if not values:
        return default
    return str(values[0] if values[0] is not None else default).strip()


def _query_float(params: Mapping[str, List[str]], key: str) -> Optional[float]:
    if key not in params:
        return None
    return _safe_float(_query_value(params, key), None)


def _query_bool(params: Mapping[str, List[str]], key: str, default: bool = False) -> bool:
    if key not in params:
        return default
    value = _query_value(params, key).lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return default


def activity_key(item: Mapping[str, Any]) -> str:
    tx = str(item.get("transactionHash") or item.get("transaction_hash") or "").strip().lower()
    if tx:
        return f"tx:{tx}"
    fields = ("timestamp", "proxyWallet", "asset", "side", "price", "size", "slug", "outcome")
    return "activity:" + "|".join(str(item.get(key) or "").strip().lower() for key in fields)


def _alert_market_id(alert: PriceAlert) -> str:
    return str(getattr(alert, "market_id", "polymarket") or "polymarket").strip().lower()


def _alert_price_state_key(market_id: str, contract_id: str) -> Tuple[str, str]:
    return str(market_id or "polymarket").strip().lower(), str(contract_id or "").strip()


def price_snapshot_values(snapshot: PriceSnapshot) -> Dict[str, Optional[float]]:
    midpoint = snapshot.midpoint
    if midpoint is None and snapshot.bid is not None and snapshot.ask is not None:
        midpoint = (float(snapshot.bid) + float(snapshot.ask)) / 2.0
    return {
        "last_trade": _safe_float(snapshot.last, None),
        "midpoint": _safe_float(midpoint, None),
        "best_bid": _safe_float(snapshot.bid, None),
        "best_ask": _safe_float(snapshot.ask, None),
    }


def _alert_values(
    alert: PriceAlert,
    price_state: Optional[Mapping[Tuple[str, str], Mapping[str, Any]]] = None,
) -> Dict[str, Optional[float]]:
    key = _alert_price_state_key(_alert_market_id(alert), alert.token_id)
    raw_values = dict((price_state or {}).get(key, {}))
    return {source: _safe_float(raw_values.get(source), None) for source in ALERT_SOURCE_IDS}


def _alert_current_value(
    alert: PriceAlert,
    price_state: Optional[Mapping[Tuple[str, str], Mapping[str, Any]]] = None,
) -> Optional[float]:
    values = _alert_values(alert, price_state)
    value = values.get(str(alert.source))
    if value is not None:
        return value
    return _safe_float(alert.last_value, None)


def _alert_condition(alert: PriceAlert, value: float) -> bool:
    return value >= float(alert.threshold) if alert.direction == "above" else value <= float(alert.threshold)


def _paper_order_signed_size(order: PaperOrderRequest) -> float:
    side = str(order.side or "").upper()
    size = float(order.size or 0.0)
    if side in {"SELL", "LAY"}:
        return -size
    if side in {"BUY", "BACK"}:
        return size
    return 0.0


def _paper_order_effect(current_net: float, signed_size: float, projected_net: float) -> str:
    if signed_size == 0:
        return "unchanged"
    if current_net == 0:
        return "opens position"
    if projected_net == 0:
        return "closes position"
    if (current_net > 0 > projected_net) or (current_net < 0 < projected_net):
        return "flips position"
    if (current_net > 0 and signed_size > 0) or (current_net < 0 and signed_size < 0):
        return "adds to position"
    return "reduces position"


def _paper_position_mark_price(snapshot: PriceSnapshot, net_size: float) -> Tuple[float, str]:
    midpoint = snapshot.midpoint
    if midpoint is None and snapshot.bid is not None and snapshot.ask is not None:
        midpoint = (float(snapshot.bid) + float(snapshot.ask)) / 2.0
    values = {
        "best_bid": snapshot.bid,
        "best_ask": snapshot.ask,
        "midpoint": midpoint,
        "last_trade": snapshot.last,
    }
    ordered_sources = (
        (("best_bid", "bid"), ("midpoint", "midpoint"), ("last_trade", "last"), ("best_ask", "ask"))
        if net_size >= 0
        else (("best_ask", "ask"), ("midpoint", "midpoint"), ("last_trade", "last"), ("best_bid", "bid"))
    )
    for key, label in ordered_sources:
        value = values.get(key)
        if value is not None:
            return float(value), label
    raise ValueError("No mark price is available for this position.")


def _paper_position_unrealized(row: Dict[str, Any], mark: Mapping[str, Any]) -> Optional[float]:
    mark_price = _safe_float(mark.get("mark_price"), None)
    notional = row.get("notional")
    if mark_price is None or notional is None:
        return None
    return float(row["net_size"]) * mark_price - float(notional)


def paper_position_rows(records: List[PaperTradeRecord]) -> List[Dict[str, Any]]:
    grouped: Dict[tuple[str, str], Dict[str, Any]] = {}
    for record in records:
        if not record.accepted:
            continue
        signed_size = _paper_record_signed_size(record)
        if signed_size == 0:
            continue
        price = record.average_price if record.average_price is not None else record.limit_price
        key = (record.market_id, record.contract_id)
        row = grouped.setdefault(
            key,
            {
                "market_id": record.market_id,
                "contract_id": record.contract_id,
                "net_size": 0.0,
                "notional": 0.0,
                "priced_size": 0.0,
                "trades": 0,
            },
        )
        row["net_size"] += signed_size
        row["trades"] += 1
        if price is not None:
            row["notional"] += signed_size * float(price)
            row["priced_size"] += abs(signed_size)

    rows: List[Dict[str, Any]] = []
    for row in grouped.values():
        priced_size = float(row.pop("priced_size"))
        notional = float(row["notional"])
        net_size = float(row["net_size"])
        row["average_price"] = abs(notional) / abs(net_size) if priced_size > 0 and net_size != 0 else None
        row["notional"] = notional if priced_size > 0 else None
        rows.append(row)
    return sorted(rows, key=lambda item: (str(item["market_id"]), str(item["contract_id"])))


def _paper_marks_for_rows(
    marks: Mapping[Tuple[str, str], Dict[str, Any]],
    rows: List[Dict[str, Any]],
) -> Dict[Tuple[str, str], Dict[str, Any]]:
    active = {(str(row["market_id"]), str(row["contract_id"])) for row in rows}
    return {key: value for key, value in marks.items() if isinstance(key, tuple) and len(key) == 2 and key in active}


def paper_position_summary(
    rows: List[Dict[str, Any]],
    marks: Optional[Mapping[Tuple[str, str], Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    marks = marks or {}
    priced_rows = [row for row in rows if row.get("notional") is not None]
    unrealized_values = []
    mark_sources: Dict[str, int] = {}
    last_marked_at: Optional[float] = None
    marked_count = 0
    for row in rows:
        mark = marks.get((str(row["market_id"]), str(row["contract_id"])), {})
        if _safe_float(mark.get("mark_price"), None) is not None:
            marked_count += 1
        unrealized = _paper_position_unrealized(row, mark)
        if unrealized is not None:
            unrealized_values.append(float(unrealized))
        source = str(mark.get("source") or "")
        if source:
            mark_sources[source] = mark_sources.get(source, 0) + 1
        marked_at = _safe_float(mark.get("marked_at"), None)
        if marked_at is not None:
            last_marked_at = marked_at if last_marked_at is None else max(last_marked_at, marked_at)
    return {
        "positions": len(rows),
        "gross_size": sum(abs(float(row["net_size"])) for row in rows),
        "entry_notional": sum(abs(float(row["notional"])) for row in priced_rows),
        "net_notional": sum(float(row["notional"]) for row in priced_rows),
        "marked": marked_count,
        "unrealized": sum(unrealized_values) if unrealized_values else None,
        "mark_sources": mark_sources,
        "last_marked_at": last_marked_at,
    }


def bool_from_setting(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value in (None, ""):
        return default
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def optional_positive_float(raw: Any, label: str) -> Optional[float]:
    if raw in (None, ""):
        return None
    value = raw
    if isinstance(raw, str):
        value = raw.strip()
        if value == "":
            return None
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be blank or a positive number.") from exc
    if number <= 0:
        raise ValueError(f"{label} must be blank or a positive number.")
    return float(number)


def sanitize_settings(settings: Mapping[str, Any]) -> Dict[str, Any]:
    sanitized: Dict[str, Any] = {}
    for key, value in settings.items():
        normalized = str(key).strip().lower()
        if any(fragment in normalized for fragment in SENSITIVE_SETTING_FRAGMENTS):
            sanitized[str(key)] = "***" if value not in (None, "") else ""
        else:
            sanitized[str(key)] = sanitize_audit_value(value, str(key))
    return sanitized


def sanitize_audit_value(value: Any, key: str = "") -> Any:
    normalized = str(key).strip().lower()
    if any(fragment in normalized for fragment in SENSITIVE_SETTING_FRAGMENTS):
        return "***" if value not in (None, "") else ""
    if isinstance(value, Mapping):
        return {str(child_key): sanitize_audit_value(child_value, str(child_key)) for child_key, child_value in value.items()}
    if isinstance(value, list):
        return [sanitize_audit_value(item, key) for item in value]
    if isinstance(value, tuple):
        return [sanitize_audit_value(item, key) for item in value]
    return value


def enabled_capabilities(capabilities: MarketCapabilities) -> List[str]:
    return [key for key, value in capabilities.to_dict().items() if value]


def market_live_status(capabilities: MarketCapabilities, settings: Mapping[str, Any]) -> str:
    if not capabilities.live_trading:
        return "no"
    if not bool_from_setting(settings.get("live_trading_enabled"), False):
        return "guarded/off"
    if bool_from_setting(settings.get("live_trading_kill_switch"), False) or bool_from_setting(
        settings.get("live_trading_paused"), False
    ):
        return "kill-switch"
    if not (
        bool_from_setting(settings.get("live_trading_confirmed"), False)
        or bool_from_setting(settings.get("live_trading_acknowledged"), False)
    ):
        return "needs-confirmation"
    return "armed"


def market_status_text(meta: MarketMetadata, enabled: bool, health: Dict[str, Any], settings: Mapping[str, Any]) -> str:
    message = str(health.get("message") or "").strip()
    if health.get("verified_blocker"):
        return f"{meta.display_name}: verified blocked. {message}"

    capabilities = meta.capabilities
    read_supported = (
        capabilities.market_discovery
        or capabilities.event_listing
        or capabilities.price_reading
        or capabilities.orderbook_reading
    )
    return (
        f"{meta.display_name}: adapter loaded. "
        f"Config {'enabled' if enabled else 'disabled'}; "
        f"Alerts {'yes' if capabilities.alerts else 'no'}; "
        f"read-only {'yes' if read_supported else 'no'}; "
        f"paper {'yes' if capabilities.paper_trading else 'no'}; "
        f"live {market_live_status(capabilities, settings)}; "
        f"copy {'yes' if capabilities.copy_trading else 'no'}."
    )


def market_safety_payload(settings: Mapping[str, Any], enabled: bool) -> Dict[str, Any]:
    return {
        "enabled": bool(enabled),
        "live_trading_enabled": bool_from_setting(settings.get("live_trading_enabled"), False),
        "live_trading_confirmed": bool_from_setting(settings.get("live_trading_confirmed"), False)
        or bool_from_setting(settings.get("live_trading_acknowledged"), False),
        "live_trading_kill_switch": bool_from_setting(settings.get("live_trading_kill_switch"), False)
        or bool_from_setting(settings.get("live_trading_paused"), False),
        "live_trading_max_size": settings.get("live_trading_max_size") if settings.get("live_trading_max_size") not in (None, "") else None,
        "live_trading_max_notional": settings.get("live_trading_max_notional")
        if settings.get("live_trading_max_notional") not in (None, "")
        else None,
    }


def market_health_payload(meta: MarketMetadata, cfg: AppConfig, registry: Optional[AdapterRegistry] = None) -> Dict[str, Any]:
    market_cfg = cfg.markets.get(meta.market_id)
    settings = dict(market_cfg.settings) if market_cfg else {}
    registry = registry or build_default_registry()
    try:
        adapter = registry.create(meta.market_id, settings)
        health = adapter.health_check()
    except Exception as exc:
        health = {
            "market_id": meta.market_id,
            "ok": False,
            "message": "Adapter health check failed.",
            "error_type": type(exc).__name__,
            "adapter": "",
            "capabilities": meta.capabilities.to_dict(),
        }
    credential_sources = health.get("credential_sources") if isinstance(health.get("credential_sources"), list) else []
    credential_env_vars = [str(value) for value in settings.get("credential_env_vars") or []]
    return {
        "market_id": meta.market_id,
        "display_name": meta.display_name,
        "enabled": bool(market_cfg and market_cfg.enabled),
        "default_enabled": bool(meta.default_enabled),
        "homepage_url": meta.homepage_url,
        "description": meta.description,
        "capabilities": meta.capabilities.to_dict(),
        "enabled_capabilities": enabled_capabilities(meta.capabilities),
        "settings": sanitize_settings(settings),
        "safety": market_safety_payload(settings, bool(market_cfg and market_cfg.enabled)),
        "health": health,
        "status_text": market_status_text(meta, bool(market_cfg and market_cfg.enabled), health, settings),
        "credential_env_vars": credential_env_vars,
        "credential_sources": credential_sources,
        "credential_summary": ", ".join(
            f"{str(item.get('name') or 'credential')} from {str(item.get('source') or 'configured')}"
            for item in credential_sources
            if isinstance(item, dict)
        )
        or "none detected",
    }


def markets_payload(cfg: AppConfig, registry: Optional[AdapterRegistry] = None) -> Dict[str, Any]:
    registry = registry or build_default_registry()
    markets = [market_health_payload(meta, cfg, registry) for meta in MARKET_CATALOG]
    return {
        "selected_market_id": cfg.selected_market_id,
        "markets": markets,
        "counts": {
            "total": len(markets),
            "enabled": sum(1 for market in markets if market["enabled"]),
            "implemented": sum(1 for market in markets if any(market["capabilities"].values())),
        },
    }


def live_safety_payload(
    cfg: AppConfig,
    registry: Optional[AdapterRegistry] = None,
    market_id: Optional[str] = None,
) -> Dict[str, Any]:
    registry = registry or build_default_registry()
    normalized = str(market_id or cfg.selected_market_id or "").strip().lower()
    meta = next((item for item in MARKET_CATALOG if item.market_id == normalized), None)
    if meta is None:
        raise ValueError(f"Unknown market id: {normalized}")

    market_cfg = cfg.markets.get(normalized)
    settings = dict(market_cfg.settings) if market_cfg else {}
    enabled = bool(market_cfg and market_cfg.enabled)
    safety = market_safety_payload(settings, enabled)
    status = market_live_status(meta.capabilities, settings)
    blockers: List[str] = []
    if not enabled:
        blockers.append("market disabled")
    if not meta.capabilities.live_trading:
        blockers.append("live trading unsupported")
    if meta.capabilities.live_trading and not safety["live_trading_enabled"]:
        blockers.append("live trading disabled")
    if safety["live_trading_kill_switch"]:
        blockers.append("kill switch active")
    if meta.capabilities.live_trading and safety["live_trading_enabled"] and not safety["live_trading_confirmed"]:
        blockers.append("acknowledgement required")

    if enabled and status == "armed":
        tone = "good"
    elif blockers:
        tone = "warn"
    else:
        tone = "neutral"

    return {
        "selected_market_id": normalized,
        "market": market_health_payload(meta, cfg, registry),
        "status": status,
        "tone": tone,
        "blockers": blockers,
        "can_preflight": bool(enabled and meta.capabilities.live_trading),
        "controls": safety,
        "redaction": {
            "sensitive_key_fragments": list(SENSITIVE_SETTING_FRAGMENTS),
            "audit_payloads_redacted": True,
        },
    }


def format_live_preflight(preflight: Mapping[str, Any]) -> str:
    preview = str(preflight.get("dry_run_preview") or "Live order preflight passed.")
    notional = preflight.get("approx_notional")
    max_notional = preflight.get("max_notional")
    warnings = preflight.get("warnings") if isinstance(preflight.get("warnings"), list) else []

    parts = [f"Preflight OK: {preview}"]
    if isinstance(notional, (int, float)):
        parts.append(f"notional~{float(notional):g}")
    if isinstance(max_notional, (int, float)):
        parts.append(f"max_notional={float(max_notional):g}")
    if warnings:
        parts.append("warnings=" + ",".join(str(item) for item in warnings))
    return "; ".join(parts)


def live_order_audit_payload(order: PaperOrderRequest) -> Dict[str, Any]:
    return {
        "market_id": order.market_id,
        "contract_id": order.contract_id,
        "side": order.side,
        "size": order.size,
        "limit_price": order.limit_price,
        "approx_notional": order.size * float(order.limit_price) if order.limit_price is not None else order.size,
        "metadata_keys": sorted(str(key) for key in order.metadata.keys()),
    }


def live_preflight_payload(cfg: AppConfig, registry: AdapterRegistry, payload: Mapping[str, Any]) -> Dict[str, Any]:
    order = paper_order_from_payload(payload)
    response: Dict[str, Any] = {
        "ok": False,
        "blocked": True,
        "order": live_order_audit_payload(order),
        "preflight": None,
        "message": "",
        "live_safety": live_safety_payload(cfg, registry, order.market_id),
    }
    try:
        require_market_enabled(cfg, order.market_id, "live preflight preview")
        adapter = adapter_for_market(cfg, order.market_id, registry)
        if not adapter.capabilities.live_trading:
            raise UnsupportedFeatureError(
                adapter.market_id,
                "live_trading",
                f"{adapter.display_name} does not support live trading in this app.",
            )
        preflight = sanitize_audit_value(adapter.preflight_live_order(order, feature_name="live preflight preview"))
    except Exception as exc:
        response["message"] = f"Live preflight blocked: {exc}"
        response["error"] = str(exc)
        response["live_safety"] = live_safety_payload(cfg, registry, order.market_id)
        return response

    response.update(
        {
            "ok": True,
            "blocked": False,
            "preflight": preflight,
            "message": format_live_preflight(preflight),
            "live_safety": live_safety_payload(cfg, registry, order.market_id),
        }
    )
    return response


def paper_payload(
    cfg: AppConfig,
    marks: Optional[Mapping[Tuple[str, str], Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    positions = paper_position_rows(cfg.paper_trades)
    active_marks = _paper_marks_for_rows(marks or {}, positions)
    marked_positions = []
    for row in positions:
        key = (str(row["market_id"]), str(row["contract_id"]))
        mark = active_marks.get(key, {})
        unrealized = _paper_position_unrealized(row, mark)
        marked_positions.append(
            {
                **row,
                "mark_price": _safe_float(mark.get("mark_price"), None),
                "mark_source": str(mark.get("source") or ""),
                "marked_at": _safe_float(mark.get("marked_at"), None),
                "unrealized": unrealized,
            }
        )
    history = [record.to_dict() for record in cfg.paper_trades]
    return {
        "summary": paper_position_summary(positions, active_marks),
        "positions": marked_positions,
        "history": history,
        "counts": {
            "history": len(history),
            "accepted": sum(1 for record in cfg.paper_trades if record.accepted),
            "rejected": sum(1 for record in cfg.paper_trades if not record.accepted),
        },
    }


def config_payload(cfg: AppConfig) -> Dict[str, Any]:
    return {
        "selected_market_id": cfg.selected_market_id,
        "theme": cfg.theme,
        "ui_design": getattr(cfg, "ui_design", "aurora_2026"),
        "alerts": [alert.to_dict() for alert in cfg.alerts],
        "wallets": [wallet.to_dict() for wallet in cfg.wallets],
        "copytrading": cfg.copytrading.to_dict(),
    }


def health_payload(config_path: Path = DEFAULT_CONFIG_PATH, frontend_dir: Path = DEFAULT_FRONTEND_DIR) -> Dict[str, Any]:
    frontend_index = frontend_dir / "index.html"
    return {
        "status": "ok",
        "api_version": API_VERSION,
        "mode": "parallel",
        "python_gui_available": True,
        "python_gui_command": PYTHON_GUI_COMMAND,
        "python_gui_script": PYTHON_GUI_SCRIPT,
        "tkinter_fallback": f"{PYTHON_GUI_SCRIPT} or {PYTHON_GUI_COMMAND}",
        "react_gui": "parallel",
        "react_dev_command": REACT_DEV_COMMAND,
        "react_dev_manual_command": REACT_DEV_MANUAL_COMMAND,
        "react_build_command": REACT_BUILD_COMMAND,
        "react_prod_command": REACT_PROD_COMMAND,
        "config_path": str(config_path),
        "frontend_dist": str(frontend_dir),
        "frontend_build_available": frontend_index.exists(),
        "routes": API_ROUTES,
    }


def app_state_payload(
    cfg: AppConfig,
    config_path: Path = DEFAULT_CONFIG_PATH,
    frontend_dir: Path = DEFAULT_FRONTEND_DIR,
    paper_marks: Optional[Mapping[Tuple[str, str], Dict[str, Any]]] = None,
    registry: Optional[AdapterRegistry] = None,
    alert_price_state: Optional[Mapping[Tuple[str, str], Mapping[str, Any]]] = None,
    wallet_polling: Optional[Mapping[str, Any]] = None,
    recent_wallet_activity: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    registry = registry or build_default_registry()
    return {
        "health": health_payload(config_path, frontend_dir),
        "config": config_payload(cfg),
        "markets": markets_payload(cfg, registry),
        "alerts": alerts_payload(cfg, registry, alert_price_state),
        "wallets": wallets_payload(cfg, wallet_polling, recent_wallet_activity),
        "copy": copy_payload(cfg, registry),
        "live_safety": live_safety_payload(cfg, registry),
        "polymarket_live_validation": polymarket_live_validation_payload(cfg),
        "polymarket_live_validation_reports": polymarket_live_validation_reports_payload(),
        "paper": paper_payload(cfg, paper_marks),
    }


def apply_config_patch(cfg: AppConfig, payload: Dict[str, Any]) -> AppConfig:
    if "selected_market_id" in payload:
        selected_market_id = str(payload["selected_market_id"] or "").strip().lower()
        if selected_market_id not in MARKET_IDS:
            raise ValueError(f"Unknown market id: {selected_market_id}")
        cfg.selected_market_id = selected_market_id
    if "theme" in payload:
        theme = str(payload["theme"] or "").strip().lower()
        if theme not in {"light", "dark"}:
            raise ValueError("theme must be light or dark.")
        cfg.theme = "dark" if theme == "dark" else "light"
    if "ui_design" in payload:
        ui_design = str(payload["ui_design"] or "").strip().lower().replace("-", "_").replace(" ", "_")
        if ui_design not in {"classic", "aurora_2026", "graphite_2026"}:
            raise ValueError("ui_design must be classic, aurora_2026, or graphite_2026.")
        cfg.ui_design = cast(UIDesign, ui_design)
    return cfg


def apply_market_patch(cfg: AppConfig, market_id: str, payload: Dict[str, Any]) -> AppConfig:
    normalized = str(market_id or "").strip().lower()
    if normalized not in cfg.markets:
        raise ValueError(f"Unknown market id: {normalized}")
    market_cfg = cfg.markets[normalized]
    if "enabled" in payload:
        market_cfg.enabled = bool(payload["enabled"])
    settings = dict(market_cfg.settings)
    for key in ("live_trading_enabled", "live_trading_confirmed", "live_trading_kill_switch"):
        if key in payload:
            settings[key] = bool(payload[key])
    if "live_trading_max_size" in payload:
        max_size = optional_positive_float(payload["live_trading_max_size"], "Max order size")
        if max_size is None:
            settings.pop("live_trading_max_size", None)
        else:
            settings["live_trading_max_size"] = max_size
    if "live_trading_max_notional" in payload:
        max_notional = optional_positive_float(payload["live_trading_max_notional"], "Max notional")
        if max_notional is None:
            settings.pop("live_trading_max_notional", None)
        else:
            settings["live_trading_max_notional"] = max_notional
    if "settings" in payload:
        raw_settings = payload["settings"]
        if not isinstance(raw_settings, dict):
            raise ValueError("settings must be an object.")
        settings.update(raw_settings)
    market_cfg.settings = settings
    return cfg


def require_market_enabled(cfg: AppConfig, market_id: str, feature: str) -> None:
    market_cfg = cfg.markets.get(market_id)
    if not market_cfg or not market_cfg.enabled:
        raise ValueError(f"{market_id} is disabled in local market config. Enable it before using {feature}.")


def adapter_for_market(cfg: AppConfig, market_id: str, registry: AdapterRegistry):
    market_cfg = cfg.markets.get(market_id)
    settings = market_cfg.settings if market_cfg else {}
    return registry.create(market_id, settings)


def find_alert(cfg: AppConfig, alert_id: str) -> PriceAlert:
    normalized = str(alert_id or "").strip()
    for alert in cfg.alerts:
        if alert.id == normalized:
            return alert
    raise ValueError(f"Unknown alert id: {normalized}")


def alert_status(
    cfg: AppConfig,
    registry: AdapterRegistry,
    alert: PriceAlert,
    price_state: Optional[Mapping[Tuple[str, str], Mapping[str, Any]]] = None,
) -> Dict[str, str]:
    market_id = _alert_market_id(alert)
    market_cfg = cfg.markets.get(market_id)
    if not alert.enabled:
        return {"label": "triggered/disabled" if alert.triggered else "disabled", "tone": "neutral"}
    if not market_cfg or not market_cfg.enabled:
        return {"label": "market disabled", "tone": "warn"}
    try:
        adapter = adapter_for_market(cfg, market_id, registry)
    except Exception as exc:
        return {"label": f"adapter unavailable: {exc}", "tone": "warn"}
    if not adapter.capabilities.alerts:
        return {"label": "alerts unsupported", "tone": "warn"}
    if not adapter.capabilities.price_reading:
        return {"label": "price feed unavailable", "tone": "warn"}
    if alert.triggered:
        return {"label": "triggered", "tone": "warn"}
    if _alert_current_value(alert, price_state) is None:
        return {"label": f"waiting for {alert.source}", "tone": "neutral"}
    return {"label": "armed", "tone": "good"}


def alert_payload(
    cfg: AppConfig,
    registry: AdapterRegistry,
    alert: PriceAlert,
    price_state: Optional[Mapping[Tuple[str, str], Mapping[str, Any]]] = None,
) -> Dict[str, Any]:
    market_id = _alert_market_id(alert)
    values = _alert_values(alert, price_state)
    return {
        **alert.to_dict(),
        "market_id": market_id,
        "contract_id": alert.token_id,
        "values": values,
        "current_value": _alert_current_value(alert, price_state),
        "status": alert_status(cfg, registry, alert, price_state),
    }


def alerts_payload(
    cfg: AppConfig,
    registry: Optional[AdapterRegistry] = None,
    price_state: Optional[Mapping[Tuple[str, str], Mapping[str, Any]]] = None,
) -> Dict[str, Any]:
    registry = registry or build_default_registry()
    alerts = [alert_payload(cfg, registry, alert, price_state) for alert in cfg.alerts]
    return {
        "alerts": alerts,
        "source_options": ALERT_SOURCE_OPTIONS,
        "counts": {
            "total": len(alerts),
            "enabled": sum(1 for alert in cfg.alerts if alert.enabled),
            "triggered": sum(1 for alert in cfg.alerts if alert.triggered),
        },
    }


def alert_from_payload(
    cfg: AppConfig,
    registry: AdapterRegistry,
    payload: Mapping[str, Any],
    existing: Optional[PriceAlert] = None,
) -> PriceAlert:
    market_id = str(payload.get("market_id") or (existing.market_id if existing else "") or "").strip().lower()
    token_id = str(
        payload.get("contract_id")
        or payload.get("token_id")
        or (existing.token_id if existing else "")
        or ""
    ).strip()
    label = str(payload.get("label") if "label" in payload else (existing.label if existing else "")).strip()
    direction = str(payload.get("direction") or (existing.direction if existing else "above")).strip().lower()
    source = str(payload.get("source") or (existing.source if existing else "last_trade")).strip().lower()
    threshold = _safe_float(payload.get("threshold") if "threshold" in payload else (existing.threshold if existing else None), None)

    if not market_id:
        raise ValueError("market_id is required.")
    if market_id not in MARKET_IDS:
        raise ValueError(f"Unknown market id: {market_id}")
    if not token_id:
        raise ValueError("contract_id is required.")
    if direction not in ALERT_DIRECTIONS:
        raise ValueError("direction must be above or below.")
    if source not in ALERT_SOURCE_IDS:
        raise ValueError("source must be one of: last_trade, midpoint, best_bid, best_ask.")
    if threshold is None or threshold < 0 or threshold > 1:
        raise ValueError("threshold must be a number between 0 and 1.")

    once = bool_from_setting(payload.get("once"), existing.once if existing else True)
    enabled = bool_from_setting(payload.get("enabled"), existing.enabled if existing else True)
    label = label or f"Alert {token_id[:8]}"
    watched_change_requested = existing is None or any(
        key in payload for key in ("market_id", "contract_id", "token_id", "direction", "threshold", "source")
    )
    if existing is None or enabled or watched_change_requested:
        require_market_enabled(cfg, market_id, "price alerts")
        adapter = adapter_for_market(cfg, market_id, registry)
        if not adapter.capabilities.alerts:
            raise UnsupportedFeatureError(market_id, "alerts", f"{adapter.display_name} does not support price alerts.")
        if not adapter.capabilities.price_reading:
            raise UnsupportedFeatureError(market_id, "price_reading", f"{adapter.display_name} has no price feed for alerts.")

    if existing is None:
        return PriceAlert(
            token_id=token_id,
            label=label,
            direction=direction,  # type: ignore[arg-type]
            threshold=float(threshold),
            source=source,  # type: ignore[arg-type]
            once=once,
            enabled=enabled,
            market_id=market_id,
        )

    watched_before = (existing.market_id, existing.token_id, existing.direction, existing.threshold, existing.source)
    existing.market_id = market_id
    existing.token_id = token_id
    existing.label = label
    existing.direction = direction  # type: ignore[assignment]
    existing.threshold = float(threshold)
    existing.source = source  # type: ignore[assignment]
    existing.once = once
    existing.enabled = enabled
    watched_after = (existing.market_id, existing.token_id, existing.direction, existing.threshold, existing.source)
    if watched_after != watched_before:
        existing.last_value = None
        existing.triggered = False
    return existing


def evaluate_alerts_for_contract(
    cfg: AppConfig,
    market_id: str,
    contract_id: str,
    price_state: Mapping[Tuple[str, str], Mapping[str, Any]],
) -> List[str]:
    normalized_market, normalized_contract = _alert_price_state_key(market_id, contract_id)
    values = dict(price_state.get((normalized_market, normalized_contract), {}))
    messages: List[str] = []
    for alert in cfg.alerts:
        if not alert.enabled:
            continue
        if _alert_market_id(alert) != normalized_market or alert.token_id != normalized_contract:
            continue
        value = _safe_float(values.get(str(alert.source)), None)
        if value is None:
            continue
        previous = alert.last_value
        alert.last_value = float(value)
        condition_now = _alert_condition(alert, float(value))
        condition_previous = None
        if previous is not None:
            condition_previous = _alert_condition(alert, float(previous))
        crossed = condition_now and (condition_previous is False or condition_previous is None)
        if crossed and not alert.triggered:
            alert.triggered = True
            messages.append(
                f"{normalized_market}:{alert.label} {alert.source}={float(value):g} crossed {alert.direction} {float(alert.threshold):g}"
            )
            if alert.once:
                alert.enabled = False
        if not alert.once and alert.triggered and not condition_now:
            alert.triggered = False
    return messages


def refresh_alert_price(
    cfg: AppConfig,
    registry: AdapterRegistry,
    alert: PriceAlert,
    price_state: Dict[Tuple[str, str], Dict[str, Any]],
) -> Dict[str, Any]:
    market_id = _alert_market_id(alert)
    require_market_enabled(cfg, market_id, "alert price refresh")
    adapter = adapter_for_market(cfg, market_id, registry)
    if not adapter.capabilities.alerts:
        raise UnsupportedFeatureError(market_id, "alerts", f"{adapter.display_name} does not support price alerts.")
    if not adapter.capabilities.price_reading:
        raise UnsupportedFeatureError(market_id, "price_reading", f"{adapter.display_name} has no price feed for alerts.")
    snapshot = adapter.get_price(alert.token_id)
    key = _alert_price_state_key(market_id, alert.token_id)
    price_state[key] = price_snapshot_values(snapshot)
    messages = evaluate_alerts_for_contract(cfg, market_id, alert.token_id, price_state)
    return {
        "market_id": key[0],
        "contract_id": key[1],
        "values": price_state[key],
        "messages": messages,
        "source": snapshot.source,
    }


def refresh_all_alert_prices(
    cfg: AppConfig,
    registry: AdapterRegistry,
    price_state: Dict[Tuple[str, str], Dict[str, Any]],
) -> Dict[str, Any]:
    refreshed: List[Dict[str, Any]] = []
    problems: List[str] = []
    seen: set[Tuple[str, str]] = set()
    for alert in cfg.alerts:
        if not alert.enabled:
            continue
        key = _alert_price_state_key(_alert_market_id(alert), alert.token_id)
        if key in seen:
            continue
        seen.add(key)
        try:
            refreshed.append(refresh_alert_price(cfg, registry, alert, price_state))
        except Exception as exc:
            problems.append(f"{key[0]}:{key[1]}: {exc}")
    return {"refreshed": refreshed, "problems": problems}


def delete_alert(cfg: AppConfig, alert_id: str) -> PriceAlert:
    alert = find_alert(cfg, alert_id)
    cfg.alerts = [item for item in cfg.alerts if item.id != alert.id]
    return alert


def require_polymarket_selected(cfg: AppConfig, feature: str) -> None:
    if str(cfg.selected_market_id or "").strip().lower() != "polymarket":
        raise ValueError(f"{feature} is only available when the selected market is polymarket.")
    require_market_enabled(cfg, "polymarket", feature)


def wallet_payload(wallet: WalletWatch) -> Dict[str, Any]:
    return {
        **wallet.to_dict(),
        "seen_count": len(wallet.seen_activity_keys or []),
    }


def wallet_activity_payload(wallet: WalletWatch, item: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "id": activity_key(item),
        "wallet_id": wallet.id,
        "wallet": wallet.wallet,
        "display_name": wallet.display_name,
        "timestamp": int(item.get("timestamp") or 0),
        "transaction_hash": str(item.get("transactionHash") or item.get("transaction_hash") or ""),
        "proxy_wallet": str(item.get("proxyWallet") or ""),
        "side": str(item.get("side") or ""),
        "asset": str(item.get("asset") or ""),
        "price": _safe_float(item.get("price"), None),
        "size": _safe_float(item.get("size"), None),
        "slug": str(item.get("slug") or ""),
        "outcome": str(item.get("outcome") or ""),
        "pseudonym": str(item.get("pseudonym") or item.get("name") or ""),
        "raw": dict(item),
    }


def wallets_payload(
    cfg: AppConfig,
    polling: Optional[Mapping[str, Any]] = None,
    recent_activity: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    enabled_wallets = [wallet for wallet in cfg.wallets if wallet.enabled]
    return {
        "wallets": [wallet_payload(wallet) for wallet in cfg.wallets],
        "counts": {
            "total": len(cfg.wallets),
            "enabled": len(enabled_wallets),
        },
        "polling": {
            "mode": "manual",
            "poll_interval_seconds": float((polling or {}).get("poll_interval_seconds") or 10.0),
            "last_polled_at": _safe_float((polling or {}).get("last_polled_at"), None),
            "last_message": str((polling or {}).get("last_message") or "Not polled yet."),
        },
        "recent_activity": list(recent_activity or []),
    }


LEADERBOARD_SORTS = {
    "roi_pct": "PNL",
    "pnl_usd": "PNL",
    "volume_usd": "VOL",
    "mdd_usd": "PNL",
    "mdd_pct": "PNL",
}
POLYMARKET_LEADERBOARD_MAX_ROWS = 1_000_000


def _leaderboard_lookup(row: Mapping[str, Any], *keys: str) -> Any:
    sources: List[Mapping[str, Any]] = [row]
    for nested_key in ("user", "profile", "trader"):
        nested = row.get(nested_key)
        if isinstance(nested, Mapping):
            sources.append(nested)
    for source in sources:
        for key in keys:
            if key in source and source.get(key) not in (None, ""):
                return source.get(key)
    return None


def _leaderboard_display_name(row: Mapping[str, Any], wallet: str) -> str:
    name = _leaderboard_lookup(
        row,
        "pseudonym",
        "displayName",
        "display_name",
        "username",
        "name",
    )
    if name:
        return str(name)
    return f"{wallet[:8]}...{wallet[-6:]}" if wallet else "-"


def normalize_polymarket_leaderboard_row(raw: Mapping[str, Any], fallback_rank: int) -> Dict[str, Any]:
    wallet = str(
        _leaderboard_lookup(raw, "proxyWallet", "proxy_wallet", "wallet", "address", "userAddress") or ""
    )
    pnl = _safe_float(
        _leaderboard_lookup(raw, "pnl", "pnlUsd", "pnl_usd", "profit", "profitLoss", "realizedPnl", "realizedPnlUsd"),
        None,
    )
    volume = _safe_float(
        _leaderboard_lookup(raw, "volume", "volumeUsd", "volume_usd", "vol", "totalVolume", "totalVolumeUsd"),
        None,
    )
    roi = (float(pnl) / float(volume) * 100.0) if pnl is not None and volume and volume > 0 else None
    mdd_usd = _safe_float(
        _leaderboard_lookup(raw, "mdd", "mddUsd", "mdd_usd", "maxDrawdown", "max_drawdown", "maxDrawdownUsd"),
        None,
    )
    mdd_pct = _safe_float(
        _leaderboard_lookup(raw, "mddPct", "mdd_pct", "maxDrawdownPct", "max_drawdown_pct"),
        None,
    )
    rank = _safe_int(_leaderboard_lookup(raw, "rank", "position"), fallback_rank)
    display_public = _leaderboard_lookup(raw, "displayUsernamePublic", "display_username_public")
    return {
        "rank": rank or fallback_rank,
        "wallet": wallet,
        "display_name": _leaderboard_display_name(raw, wallet),
        "profile_image": str(_leaderboard_lookup(raw, "profileImage", "profile_image", "avatar") or ""),
        "display_username_public": bool(display_public) if display_public is not None else True,
        "pnl_usd": pnl,
        "volume_usd": volume,
        "roi_pct": roi,
        "trade_count": _safe_int(_leaderboard_lookup(raw, "trades", "tradeCount", "trade_count", "totalTrades"), 0),
        "mdd_usd": mdd_usd,
        "mdd_pct": mdd_pct,
        "mdd_available": mdd_usd is not None or mdd_pct is not None,
        "raw": dict(raw),
    }


def _position_total_pnl(row: Mapping[str, Any]) -> Optional[float]:
    total = _safe_float(_leaderboard_lookup(row, "totalPnl", "total_pnl"), None)
    if total is not None:
        return total
    values = [
        _safe_float(_leaderboard_lookup(row, "cashPnl", "cash_pnl"), None),
        _safe_float(_leaderboard_lookup(row, "realizedPnl", "realized_pnl"), None),
    ]
    present = [value for value in values if value is not None]
    return sum(present) if present else None


def _position_capital(row: Mapping[str, Any]) -> float:
    value = _safe_float(
        _leaderboard_lookup(row, "totalBought", "total_bought", "initialValue", "initial_value", "currentValue", "current_value"),
        0.0,
    )
    return max(float(value or 0.0), 0.0)


def _fetch_user_positions_all(wallet: str, limit: int = 500) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    offset = 0
    clean_limit = max(0, min(int(limit), 1000))
    while len(rows) < clean_limit:
        page_limit = min(500, clean_limit - len(rows))
        page = data_api.get_positions(wallet, limit=page_limit, offset=offset)
        if not page:
            break
        rows.extend(page)
        if len(page) < page_limit:
            break
        offset += len(page)
    return rows


def _fetch_user_closed_positions_all(wallet: str, limit: int = 500) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    offset = 0
    clean_limit = max(0, min(int(limit), 1000))
    while len(rows) < clean_limit:
        page_limit = min(50, clean_limit - len(rows))
        page = data_api.get_closed_positions(
            wallet,
            limit=page_limit,
            offset=offset,
            sort_by="TIMESTAMP",
            sort_direction="ASC",
        )
        if not page:
            break
        rows.extend(page)
        if len(page) < page_limit:
            break
        offset += len(page)
    return rows


def _max_drawdown(points: List[Dict[str, Any]], equity_base_usd: Optional[float]) -> Dict[str, Any]:
    if not points:
        return {
            "mdd_usd": 0.0,
            "mdd_pct": 0.0 if equity_base_usd and equity_base_usd > 0 else None,
            "peak_value": 0.0,
            "trough_value": 0.0,
            "peak_timestamp": None,
            "trough_timestamp": None,
        }
    peak_value = float(points[0]["value"])
    peak_ts = points[0].get("timestamp")
    trough_value = peak_value
    trough_ts = peak_ts
    max_dd = 0.0
    max_dd_pct: Optional[float] = 0.0 if equity_base_usd and equity_base_usd > 0 else None
    max_peak = peak_value
    max_peak_ts = peak_ts
    for point in points:
        value = float(point["value"])
        timestamp = point.get("timestamp")
        if value > peak_value:
            peak_value = value
            peak_ts = timestamp
        drawdown = max(0.0, peak_value - value)
        denominator = (float(equity_base_usd) + peak_value) if equity_base_usd and equity_base_usd > 0 else None
        drawdown_pct = (drawdown / denominator * 100.0) if denominator and denominator > 0 else None
        if drawdown > max_dd:
            max_dd = drawdown
            max_dd_pct = drawdown_pct
            max_peak = peak_value
            max_peak_ts = peak_ts
            trough_value = value
            trough_ts = timestamp
    return {
        "mdd_usd": max_dd,
        "mdd_pct": max_dd_pct,
        "peak_value": max_peak,
        "trough_value": trough_value,
        "peak_timestamp": max_peak_ts,
        "trough_timestamp": trough_ts,
    }


def polymarket_user_mdd_payload(
    wallet: str,
    *,
    mode: str = "fast",
    closed_limit: int = 500,
    open_limit: int = 500,
    activity_limit: int = 1000,
    trade_limit: int = 1000,
    include_open: bool = True,
    equity_base_usd: Optional[float] = None,
    max_points: int = 50,
    cache_ttl_seconds: int = 0,
    mark_replay_token_limit: int = 10,
    mark_replay_point_limit: int = 5000,
    mark_replay_interval: Optional[str] = "1h",
    mark_replay_fidelity: Optional[int] = 60,
    mark_replay_start_ts: Optional[int] = None,
    mark_replay_end_ts: Optional[int] = None,
    include_accounting_snapshot: bool = False,
    accounting_timeout: float = 30.0,
) -> Dict[str, Any]:
    clean_mode = str(mode or "fast").strip().lower()
    if clean_mode in {"mark_replay", "mark-replay", "clob_mark_replay", "price_history"}:
        return polymarket_user_mdd_payload_mark_replay(
            wallet,
            closed_limit=closed_limit,
            open_limit=open_limit,
            activity_limit=activity_limit,
            trade_limit=trade_limit,
            include_open=include_open,
            equity_base_usd=equity_base_usd,
            max_points=max_points,
            cache_ttl_seconds=cache_ttl_seconds,
            mark_replay_token_limit=mark_replay_token_limit,
            mark_replay_point_limit=mark_replay_point_limit,
            mark_replay_interval=mark_replay_interval,
            mark_replay_fidelity=mark_replay_fidelity,
            mark_replay_start_ts=mark_replay_start_ts,
            mark_replay_end_ts=mark_replay_end_ts,
            include_accounting_snapshot=include_accounting_snapshot,
            accounting_timeout=accounting_timeout,
        )
    return polymarket_user_mdd_payload_v2(
        wallet,
        closed_limit=closed_limit,
        open_limit=open_limit,
        activity_limit=activity_limit,
        trade_limit=trade_limit,
        include_open=include_open,
        equity_base_usd=equity_base_usd,
        max_points=max_points,
        cache_ttl_seconds=cache_ttl_seconds,
        include_accounting_snapshot=include_accounting_snapshot,
        accounting_timeout=accounting_timeout,
    )


def polymarket_rate_limit_status(exc: Optional[BaseException] = None) -> Dict[str, Any]:
    if exc is None:
        return {"limited": False, "backoff_status": "not_limited", "events": []}
    event: Dict[str, Any] = {
        "message": str(exc),
        "retry_after_seconds": None,
    }
    if isinstance(exc, PolymarketHTTPError):
        event.update(
            {
                "service": exc.service,
                "method": exc.method,
                "status_code": exc.status_code,
                "url": exc.url,
            }
        )
    return {"limited": True, "backoff_status": "retry_later", "events": [event]}


def polymarket_mdd_audit_params(wallet: str, options: Mapping[str, Any]) -> Dict[str, Any]:
    params = dict(options)
    params["wallet"] = normalize_wallet(wallet)
    params["artifact"] = POLYMARKET_MDD_AUDIT_KIND
    return params


def attach_polymarket_mdd_audit_cache(
    payload: Dict[str, Any],
    params: Mapping[str, Any],
    *,
    enabled: bool,
) -> Dict[str, Any]:
    payload["rate_limit"] = polymarket_rate_limit_status()
    if not enabled:
        metadata = analytics_cache_summary(enabled=False)
        metadata.update({"stored": False, "key": None, "kind": POLYMARKET_MDD_AUDIT_KIND})
        payload["audit_cache"] = metadata
        return metadata

    artifact_payload = dict(payload)
    artifact_payload.pop("audit_cache", None)
    artifact_payload.pop("rate_limit", None)
    try:
        metadata = store_analytics_artifact(
            POLYMARKET_MDD_AUDIT_KIND,
            params,
            artifact_payload,
            ttl_seconds=DEFAULT_ANALYTICS_CACHE_TTL_SECONDS,
            max_entries=DEFAULT_ANALYTICS_CACHE_MAX_ENTRIES,
        )
    except Exception as exc:
        metadata = analytics_cache_summary(enabled=True)
        metadata.update({"stored": False, "key": None, "kind": POLYMARKET_MDD_AUDIT_KIND, "error": str(exc)})
        warnings = payload.setdefault("warnings", [])
        if isinstance(warnings, list):
            warnings.append(f"MDD audit cache write failed: {exc}")
    payload["audit_cache"] = metadata
    return metadata


def polymarket_mdd_export_payload(cache_key: str) -> Dict[str, Any]:
    loaded = load_analytics_artifact(cache_key, kind=POLYMARKET_MDD_AUDIT_KIND, allow_expired=True)
    if loaded is None:
        raise ValueError("Unknown MDD audit cache key.")
    payload, metadata = loaded
    return {"cache": metadata, "payload": payload, "export": {"format": "json", "source": POLYMARKET_MDD_AUDIT_KIND}}


def polymarket_mdd_export_csv(cache_key: str) -> Dict[str, Any]:
    export = polymarket_mdd_export_payload(cache_key)
    payload = export["payload"]
    wallet = str(payload.get("wallet") or "wallet").replace("/", "_").replace("\\", "_")
    key = str(export["cache"].get("key") or "audit")
    return {
        "cache": export["cache"],
        "filename": f"polymarket-mdd-{wallet}-{key[:8]}.csv",
        "csv": mdd_payload_to_csv(payload),
    }


def polymarket_mdd_cache_payload(*, include_expired: bool = True) -> Dict[str, Any]:
    return list_analytics_artifacts(
        kind=POLYMARKET_MDD_AUDIT_KIND,
        include_expired=include_expired,
        include_payload=False,
    )


def polymarket_mdd_cache_health_payload() -> Dict[str, Any]:
    return {
        "source": POLYMARKET_MDD_AUDIT_KIND,
        "cache": analytics_cache_health(kind=POLYMARKET_MDD_AUDIT_KIND),
    }


def polymarket_mdd_cache_purge_payload(payload: Mapping[str, Any]) -> Dict[str, Any]:
    raw_keys = payload.get("keys")
    keys: List[str] = []
    if isinstance(raw_keys, list):
        keys.extend(str(key or "").strip() for key in raw_keys if str(key or "").strip())
    single_key = str(payload.get("key") or "").strip()
    if single_key:
        keys.append(single_key)
    result = purge_analytics_artifacts(
        keys=keys,
        kind=POLYMARKET_MDD_AUDIT_KIND,
        expired_only=bool(payload.get("expired_only")),
        all_entries=bool(payload.get("all") or payload.get("all_entries")),
    )
    result["source"] = POLYMARKET_MDD_AUDIT_KIND
    return result


def polymarket_user_search_payload(query: str, limit: int = 10) -> Dict[str, Any]:
    clean_query = str(query or "").strip()
    clean_limit = _clamp_int(limit, 10, 1, 50)
    if not clean_query:
        return {"query": clean_query, "profiles": [], "counts": {"profiles": 0}, "source": "gamma_public_search"}
    profiles = gamma.search_profiles(clean_query, limit=clean_limit)
    return {
        "query": clean_query,
        "profiles": [
            {
                "pseudonym": profile.pseudonym,
                "proxy_wallet": profile.proxy_wallet,
                "profile_image": profile.profile_image,
                "display_username_public": profile.display_username_public,
            }
            for profile in profiles
        ],
        "counts": {"profiles": len(profiles)},
        "source": "gamma_public_search",
    }


def polymarket_clob_readiness_payload(cfg: AppConfig) -> Dict[str, Any]:
    market_cfg = cfg.markets.get("polymarket")
    settings = dict(market_cfg.settings) if market_cfg else {}
    enabled = bool(market_cfg and market_cfg.enabled)
    return {
        "market_id": "polymarket",
        "selected": str(cfg.selected_market_id or "").strip().lower() == "polymarket",
        "enabled": enabled,
        "live_safety": market_safety_payload(settings, enabled),
        "readiness": build_clob_auth_readiness(settings),
    }


def _validation_item(status: str, detail: str, **extra: Any) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"status": status, "detail": detail}
    payload.update(extra)
    return payload


def _env_presence(names: Tuple[str, ...]) -> Dict[str, bool]:
    return {name: bool(os.getenv(name)) for name in names}


def _all_env_present(names: Tuple[str, ...]) -> bool:
    return all(os.getenv(name) for name in names)


def polymarket_live_validation_payload(cfg: AppConfig) -> Dict[str, Any]:
    market_cfg = cfg.markets.get("polymarket")
    settings = dict(market_cfg.settings) if market_cfg else {}
    readiness = build_clob_auth_readiness(settings)
    direct_l2_ready = bool(readiness.get("direct_l2_read_ready"))
    sdk_ready = bool(readiness.get("sdk_trading_ready"))
    relayer_ready = _all_env_present(POLYMARKET_RELAYER_HEADERS)
    user_ws_auth = {
        "apiKey": os.getenv("POLY_API_KEY", ""),
        "secret": os.getenv("POLY_API_SECRET") or os.getenv("POLY_SECRET", ""),
        "passphrase": os.getenv("POLY_PASSPHRASE", ""),
    }
    try:
        build_user_subscription(user_ws_auth)
        user_ws_payload = _validation_item(
            "skipped",
            "User WebSocket auth payload can be built; GUI/API does not open the authenticated stream.",
            next_step="Run scripts/verify_polymarket_live.py --include-user-websocket-connect for a real stream probe.",
        )
    except ValueError as exc:
        user_ws_payload = _validation_item("blocked", str(exc))

    report: Dict[str, Any] = {
        "generated_at": time.time(),
        "market_id": "polymarket",
        "mode": "local_readiness_only",
        "selected": str(cfg.selected_market_id or "").strip().lower() == "polymarket",
        "enabled": bool(market_cfg and market_cfg.enabled),
        "credential_presence": {
            "clob_l2_headers": _env_presence(POLYMARKET_L2_HEADERS),
            "py_clob_client": _env_presence(
                (
                    "POLYMARKET_PRIVATE_KEY",
                    "PRIVATE_KEY",
                    "POLYMARKET_FUNDER_ADDRESS",
                    "FUNDER_ADDRESS",
                    "POLYMARKET_SIGNATURE_TYPE",
                    "SIGNATURE_TYPE",
                )
            ),
            "relayer_headers": _env_presence(POLYMARKET_RELAYER_HEADERS),
            "user_ws": _env_presence(POLYMARKET_USER_WS_KEYS),
        },
        "clob_auth_readiness": readiness,
        "credential_runbook": build_polymarket_credential_runbook(settings),
        "public_checks": {
            "clob_time": _validation_item("skipped", "GUI/API report does not run public network probes."),
            "gamma_markets": _validation_item("skipped", "GUI/API report does not run public network probes."),
            "data_leaderboard": _validation_item("skipped", "GUI/API report does not run public network probes."),
            "bridge_supported_assets": _validation_item("skipped", "GUI/API report does not run public network probes."),
        },
        "authenticated_read_checks": {
            "clob_l2_orders": _validation_item(
                "skipped" if direct_l2_ready else "blocked",
                "Explicit L2 headers are present; run the CLI for a non-destructive order-list read."
                if direct_l2_ready
                else "Missing explicit L2 headers for CLOB order-list reads.",
                missing=readiness.get("l2_headers", {}).get("missing", []),
            ),
            "py_clob_client_credentials": _validation_item(
                "skipped" if sdk_ready else "blocked",
                "SDK trading credentials are locally ready; GUI/API does not derive API credentials."
                if sdk_ready
                else "SDK trading credentials are not locally ready.",
                blockers=readiness.get("blockers", []),
            ),
            "relayer_recent_transactions": _validation_item(
                "skipped" if relayer_ready else "blocked",
                "Relayer headers are present; run the CLI for a non-destructive recent-transactions read."
                if relayer_ready
                else "Missing relayer API key headers.",
                missing=[name for name, present in _env_presence(POLYMARKET_RELAYER_HEADERS).items() if not present],
            ),
            "user_websocket_auth_payload": user_ws_payload,
            "user_websocket_connect": _validation_item(
                "skipped",
                "GUI/API does not open authenticated WebSocket sessions.",
                next_step="Run scripts/verify_polymarket_live.py --include-user-websocket-connect.",
            ),
        },
        "bridge_address_checks": {
            "deposit_address_creation": _validation_item(
                "blocked",
                "Not exposed in GUI/API. Use the CLI with explicit bridge address flags if this check is required.",
            ),
            "withdrawal_address_creation": _validation_item(
                "blocked",
                "Not exposed in GUI/API. Use the CLI with explicit withdrawal arguments if this check is required.",
            ),
        },
        "funded_live_order_check": _validation_item(
            "blocked",
            "Funded order/cancel verification is not exposed in the GUI/API.",
            live_action=False,
        ),
        "live_order_cancel_harness": {
            "default_mode": "dry_run_transcript",
            "execute_flag": "--allow-funded-order",
            "confirmation_required": CONFIRM_LIVE_ORDER_CANCEL,
            "hard_max_size": ABSOLUTE_MAX_VERIFY_SIZE,
            "hard_max_notional": ABSOLUTE_MAX_VERIFY_NOTIONAL,
        },
        "operator_commands": {
            "public_and_readiness": "python scripts/verify_polymarket_live.py --report-file live-report.json",
            "credentialed_read": "python scripts/verify_polymarket_live.py --require-authenticated-read-ok --include-user-websocket-connect --report-file live-auth-report.json",
            "dry_run_order_cancel": "python scripts/verify_polymarket_live.py --token-id <TOKEN> --side BUY --price <PRICE> --size <SIZE> --allow-token-id <TOKEN> --report-file live-dry-run-report.json",
        },
        "funded_execution_exposed": False,
        "notes": [
            "This endpoint is a local readiness view only.",
            "Credentialed reads, user WebSocket connections, and funded order/cancel checks remain CLI-only.",
            "No funded action may run without credentials, safe parameters, and explicit user approval.",
        ],
    }
    report["stage_gates"] = build_live_validation_stage_gates(report)
    return sanitize_audit_value(report)


def polymarket_live_validation_reports_payload(*, include_payload: bool = False) -> Dict[str, Any]:
    return list_live_validation_reports(include_payload=include_payload)


def polymarket_live_validation_report_payload(key: str) -> Optional[Dict[str, Any]]:
    entry = load_live_validation_report(key)
    if entry is None:
        return None
    return {
        "source": "polymarket_live_validation_report",
        "entry": entry,
        "decisions": list_live_validation_report_decisions(report_key=key).get("entries", []),
        "export": {
            "format": "json",
            "filename": polymarket_live_validation_report_export_filename(key),
        },
    }


def polymarket_live_validation_report_export_filename(key: str) -> str:
    clean_key = "".join(char for char in str(key or "") if char.isalnum() or char in {"-", "_"})[:80]
    return f"polymarket-live-validation-{clean_key or 'report'}.json"


def polymarket_live_validation_report_review_payload(key: str) -> Optional[Dict[str, Any]]:
    bundle = live_validation_report_review_bundle(key)
    if bundle is None:
        return None
    return {
        "source": "polymarket_live_validation_report_review_bundle",
        "bundle": bundle,
        "export": {
            "json_filename": live_validation_report_review_export_filename(key, "json"),
            "markdown_filename": live_validation_report_review_export_filename(key, "md"),
        },
    }


def polymarket_live_validation_decisions_payload(params: Optional[Mapping[str, List[str]]] = None) -> Dict[str, Any]:
    report_key = _query_value(params or {}, "report_key", "")
    return list_live_validation_report_decisions(report_key=report_key or "")


def polymarket_live_validation_promotion_proposal_payload(
    params: Optional[Mapping[str, List[str]]] = None,
) -> Dict[str, Any]:
    target_tier = _query_value(params or {}, "target_tier", "")
    return live_validation_coverage_promotion_proposal(target_tier=target_tier or "")


def polymarket_live_validation_promotion_proposal_snapshots_payload() -> Dict[str, Any]:
    return list_live_validation_coverage_promotion_proposal_snapshots()


def polymarket_live_validation_promotion_proposal_snapshot_payload(key: str) -> Optional[Dict[str, Any]]:
    return load_live_validation_coverage_promotion_proposal_snapshot(key)


def polymarket_live_validation_promotion_proposal_snapshot_store_payload(
    payload: Mapping[str, Any],
) -> Dict[str, Any]:
    target_tier = str(payload.get("target_tier") or "")
    label = str(payload.get("label") or "")
    source = str(payload.get("source") or "react_preview")
    proposal = live_validation_coverage_promotion_proposal(target_tier=target_tier)
    stored = store_live_validation_coverage_promotion_proposal_snapshot(
        proposal=proposal,
        target_tier=target_tier,
        label=label,
        source=source,
    )
    inventory = polymarket_live_validation_promotion_proposal_snapshots_payload()
    inventory.update(
        {
            "stored": stored,
            "message": f"Stored promotion proposal snapshot {stored.get('key')}.",
        }
    )
    return inventory


def polymarket_live_validation_promotion_proposal_snapshot_purge_payload(payload: Mapping[str, Any]) -> Dict[str, Any]:
    keys: List[str] = []
    if payload.get("key"):
        keys.append(str(payload.get("key")))
    raw_keys = payload.get("keys")
    if isinstance(raw_keys, list):
        keys.extend(str(key) for key in raw_keys)
    return purge_live_validation_coverage_promotion_proposal_snapshots(
        keys=keys,
        all_entries=bool(payload.get("all") or payload.get("all_entries")),
    )


def polymarket_live_validation_decision_store_payload(payload: Mapping[str, Any]) -> Dict[str, Any]:
    stored = record_live_validation_report_decision(
        report_key=str(payload.get("report_key") or ""),
        payload_hash=str(payload.get("payload_hash") or ""),
        target_tier=str(payload.get("target_tier") or ""),
        decision=str(payload.get("decision") or ""),
        reviewer_note=str(payload.get("reviewer_note") or ""),
        review_bundle_hash=str(payload.get("review_bundle_hash") or ""),
        reviewer=str(payload.get("reviewer") or ""),
    )
    ledger = polymarket_live_validation_decisions_payload()
    ledger.update(
        {
            "stored": stored,
            "message": (
                f"Recorded {stored.get('decision')} decision for {stored.get('target_tier')} "
                f"on report {stored.get('report_key')}."
            ),
        }
    )
    return ledger


def polymarket_live_validation_report_store_payload(cfg: AppConfig, payload: Mapping[str, Any]) -> Dict[str, Any]:
    label = str(payload.get("label") or "").strip()
    source = str(payload.get("source") or "").strip()
    source_file = payload.get("source_file") if str(payload.get("source_file") or "").strip() else None
    allow_duplicate = bool(payload.get("allow_duplicate"))
    skip_duplicate = bool(payload.get("skip_duplicate", True)) and not allow_duplicate
    report: Mapping[str, Any]

    if "report_json" in payload and str(payload.get("report_json") or "").strip():
        report = parse_live_validation_report_json(str(payload.get("report_json") or ""))
        source = source or "cli_import"
        label = label or "CLI import"
    elif isinstance(payload.get("report"), Mapping):
        report = payload["report"]  # type: ignore[assignment]
        source = source or "cli_import"
        label = label or "Imported report"
    else:
        report = polymarket_live_validation_payload(cfg)
        source = source or "gui_snapshot"
        label = label or "GUI readiness snapshot"

    stored = store_live_validation_report(
        report,
        source=source,
        label=label,
        source_file=source_file,
        allow_duplicate=allow_duplicate,
        skip_duplicate=skip_duplicate,
    )
    inventory = polymarket_live_validation_reports_payload()
    if stored.get("duplicate") and not stored.get("stored"):
        message = f"Skipped duplicate live validation report {stored.get('duplicate_key') or stored.get('key')}."
    elif stored.get("duplicate"):
        message = f"Stored duplicate live validation report {stored.get('key')}."
    else:
        message = f"Stored live validation report {stored.get('key')}."
    inventory.update(
        {
            "stored": stored,
            "message": message,
        }
    )
    return inventory


def polymarket_live_validation_report_purge_payload(payload: Mapping[str, Any]) -> Dict[str, Any]:
    keys: List[str] = []
    if payload.get("key"):
        keys.append(str(payload.get("key")))
    raw_keys = payload.get("keys")
    if isinstance(raw_keys, list):
        keys.extend(str(key) for key in raw_keys)
    return purge_live_validation_reports(keys=keys, all_entries=bool(payload.get("all") or payload.get("all_entries")))


def _number_in_range(value: Optional[float], minimum: Optional[float], maximum: Optional[float]) -> bool:
    if value is None:
        return minimum is None and maximum is None
    if minimum is not None and value < minimum:
        return False
    if maximum is not None and value > maximum:
        return False
    return True


def polymarket_leaderboard_payload(params: Optional[Mapping[str, List[str]]] = None) -> Dict[str, Any]:
    query = params or {}
    sort = _query_value(query, "sort", "roi_pct").lower()
    if sort not in LEADERBOARD_SORTS:
        sort = "roi_pct"
    direction = _query_value(query, "direction", "DESC").upper()
    if direction not in {"ASC", "DESC"}:
        direction = "DESC"
    period = _query_value(query, "period", "all") or "all"
    category = _query_value(query, "category", "OVERALL") or "OVERALL"
    limit = _clamp_int(_query_value(query, "limit", "100"), 100, 1, POLYMARKET_LEADERBOARD_MAX_ROWS)
    default_scan = 500 if sort == "roi_pct" else max(100, limit)
    scan_limit = _clamp_int(
        _query_value(query, "scan_limit", str(default_scan)),
        default_scan,
        limit,
        POLYMARKET_LEADERBOARD_MAX_ROWS,
    )
    mdd_history_limit = _clamp_int(_query_value(query, "mdd_history_limit", "500"), 500, 1, 1000)
    mdd_activity_limit = _clamp_int(_query_value(query, "mdd_activity_limit", "1000"), 1000, 0, 5000)
    mdd_trade_limit = _clamp_int(_query_value(query, "mdd_trade_limit", "1000"), 1000, 0, 5000)
    mdd_open_limit = _clamp_int(_query_value(query, "mdd_open_limit", "500"), 500, 0, 1000)
    mdd_scan_limit = _clamp_int(
        _query_value(query, "mdd_scan_limit", str(min(scan_limit, 100))),
        min(scan_limit, 100),
        1,
        min(scan_limit, POLYMARKET_LEADERBOARD_MAX_ROWS),
    )
    raw_mdd_mode = _query_value(query, "mdd_mode", "fast").lower()
    mdd_mode = "mark_replay" if raw_mdd_mode in {"mark_replay", "mark-replay", "clob_mark_replay", "price_history"} else "fast"
    mdd_mark_replay_token_limit = _clamp_int(_query_value(query, "mdd_mark_replay_token_limit", "10"), 10, 1, 20)
    mdd_mark_replay_point_limit = _clamp_int(_query_value(query, "mdd_mark_replay_point_limit", "5000"), 5000, 1, 10000)
    mdd_mark_replay_fidelity = _clamp_int(_query_value(query, "mdd_mark_replay_fidelity", "60"), 60, 1, 1440)
    mdd_mark_replay_interval = _query_value(query, "mdd_mark_replay_interval", "1h") or "1h"
    if mdd_mark_replay_interval not in {"max", "all", "1m", "1w", "1d", "6h", "1h"}:
        mdd_mark_replay_interval = "1h"
    mdd_mark_replay_start_ts = _query_float(query, "mdd_mark_replay_start_ts")
    mdd_mark_replay_end_ts = _query_float(query, "mdd_mark_replay_end_ts")
    mdd_include_accounting = _query_bool(query, "mdd_include_accounting", False)
    mdd_accounting_timeout = _clamp_int(_query_value(query, "mdd_accounting_timeout", "30"), 30, 1, 60)
    mdd_persist_cache = _query_bool(query, "mdd_persist_cache", False)
    mdd_cache_ttl_seconds = _clamp_int(
        _query_value(query, "mdd_cache_ttl_seconds", str(POLYMARKET_MDD_CACHE_TTL_SECONDS)),
        POLYMARKET_MDD_CACHE_TTL_SECONDS,
        0,
        300,
    )
    compute_mdd = _query_bool(query, "compute_mdd", False)
    equity_base_usd = _query_float(query, "equity_base_usd")

    min_pnl = _query_float(query, "min_pnl_usd")
    max_pnl = _query_float(query, "max_pnl_usd")
    min_volume = _query_float(query, "min_volume_usd")
    max_volume = _query_float(query, "max_volume_usd")
    min_roi = _query_float(query, "min_roi_pct")
    max_roi = _query_float(query, "max_roi_pct")
    min_mdd_usd = _query_float(query, "min_mdd_usd")
    max_mdd_usd = _query_float(query, "max_mdd_usd")
    min_mdd_pct = _query_float(query, "min_mdd_pct")
    max_mdd_pct = _query_float(query, "max_mdd_pct")
    mdd_requested = compute_mdd or mdd_mode == "mark_replay" or sort in {"mdd_usd", "mdd_pct"} or any(
        value is not None for value in (min_mdd_usd, max_mdd_usd, min_mdd_pct, max_mdd_pct)
    )

    remote_sort = LEADERBOARD_SORTS[sort]
    raw_rows: List[Dict[str, Any]] = []
    offset = 0
    while len(raw_rows) < scan_limit:
        page_limit = min(50, scan_limit - len(raw_rows))
        page = data_api.get_leaderboard(
            limit=page_limit,
            offset=offset,
            sort_by=remote_sort,
            sort_direction=direction,
            period=period,
            category=category,
        )
        if not page:
            break
        raw_rows.extend(page)
        if len(page) < page_limit:
            break
        offset += len(page)

    warnings: List[str] = []
    rate_limit_events: List[Dict[str, Any]] = []
    rows = [normalize_polymarket_leaderboard_row(row, index + 1) for index, row in enumerate(raw_rows)]
    prefiltered: List[Dict[str, Any]] = []
    for row in rows:
        if not _number_in_range(row["pnl_usd"], min_pnl, max_pnl):
            continue
        if not _number_in_range(row["volume_usd"], min_volume, max_volume):
            continue
        if not _number_in_range(row["roi_pct"], min_roi, max_roi):
            continue
        prefiltered.append(row)

    computed_mdd = 0
    if mdd_requested:
        for row in prefiltered[:mdd_scan_limit]:
            wallet = normalize_wallet(row.get("wallet") or "")
            if not wallet:
                continue
            mdd_options = {
                "mode": mdd_mode,
                "closed_limit": mdd_history_limit,
                "open_limit": mdd_open_limit,
                "activity_limit": mdd_activity_limit,
                "trade_limit": mdd_trade_limit,
                "include_open": True,
                "equity_base_usd": equity_base_usd,
                "cache_ttl_seconds": mdd_cache_ttl_seconds,
                "mark_replay_token_limit": mdd_mark_replay_token_limit,
                "mark_replay_point_limit": mdd_mark_replay_point_limit,
                "mark_replay_interval": mdd_mark_replay_interval,
                "mark_replay_fidelity": mdd_mark_replay_fidelity,
                "mark_replay_start_ts": int(mdd_mark_replay_start_ts) if mdd_mark_replay_start_ts is not None else None,
                "mark_replay_end_ts": int(mdd_mark_replay_end_ts) if mdd_mark_replay_end_ts is not None else None,
                "include_accounting_snapshot": mdd_include_accounting,
                "accounting_timeout": mdd_accounting_timeout,
            }
            try:
                mdd = polymarket_user_mdd_payload(wallet, **mdd_options)
            except PolymarketRateLimitError as exc:
                status = polymarket_rate_limit_status(exc)
                rate_limit_events.extend(status["events"])
                warnings.append(f"MDD rate-limited for {wallet}; retry after the upstream backoff window.")
                break
            except Exception as exc:
                warnings.append(f"MDD unavailable for {wallet}: {exc}")
                continue
            audit_cache = attach_polymarket_mdd_audit_cache(
                mdd,
                polymarket_mdd_audit_params(wallet, mdd_options),
                enabled=mdd_persist_cache,
            )
            row.update(
                {
                    "mdd_usd": mdd["mdd_usd"],
                    "mdd_pct": mdd["mdd_pct"],
                    "mdd_available": True,
                    "mdd_method": mdd["mdd_method"],
                    "mdd_pct_basis": mdd["mdd_pct_basis"],
                    "mdd_points": len(mdd["points"]),
                    "mdd_closed_positions": mdd["closed_positions"],
                    "mdd_open_positions": mdd["open_positions"],
                    "mdd_activity_events": mdd.get("activity_events", 0),
                    "mdd_trade_events": mdd.get("trade_events", 0),
                    "mdd_equity_base_usd": mdd["equity_base_usd"],
                    "mdd_equity_base_source": mdd.get("equity_base_source"),
                    "mdd_public_capital_basis_usd": mdd.get("public_capital_basis_usd"),
                    "mdd_peak_value": mdd["peak_value"],
                    "mdd_trough_value": mdd["trough_value"],
                    "mdd_peak_timestamp": mdd["peak_timestamp"],
                    "mdd_trough_timestamp": mdd["trough_timestamp"],
                    "mdd_mark_replay_status": (mdd.get("mark_replay") or {}).get("status"),
                    "mdd_mark_replay_tokens": (mdd.get("mark_replay") or {}).get("token_count"),
                    "mdd_accounting_status": (mdd.get("accounting_snapshot") or {}).get("status"),
                    "mdd_accounting_equity_base_usd": ((mdd.get("accounting_snapshot") or {}).get("equity") or {}).get("base_equity_usd"),
                    "mdd_accounting_cash_flow_gap_usd": (
                        (((mdd.get("accounting_snapshot") or {}).get("equity") or {}).get("cash_flows") or {}).get("cash_flow_gap_usd")
                    ),
                    "mdd_audit_cache_key": audit_cache.get("key"),
                    "mdd_audit_cache_stored": audit_cache.get("stored", False),
                }
            )
            computed_mdd += 1

    filtered: List[Dict[str, Any]] = []
    for row in prefiltered:
        if mdd_requested:
            if not row["mdd_available"]:
                continue
            if not _number_in_range(row["mdd_usd"], min_mdd_usd, max_mdd_usd):
                continue
            if not _number_in_range(row["mdd_pct"], min_mdd_pct, max_mdd_pct):
                continue
        filtered.append(row)

    reverse = direction != "ASC"
    missing_numeric = float("-inf") if reverse else float("inf")
    if sort == "roi_pct":
        filtered.sort(key=lambda row: row["roi_pct"] if row["roi_pct"] is not None else missing_numeric, reverse=reverse)
    elif sort == "volume_usd":
        filtered.sort(key=lambda row: row["volume_usd"] if row["volume_usd"] is not None else missing_numeric, reverse=reverse)
    elif sort == "mdd_usd":
        filtered.sort(key=lambda row: row["mdd_usd"] if row["mdd_usd"] is not None else missing_numeric, reverse=reverse)
    elif sort == "mdd_pct":
        filtered.sort(key=lambda row: row["mdd_pct"] if row["mdd_pct"] is not None else missing_numeric, reverse=reverse)
    else:
        filtered.sort(key=lambda row: row["pnl_usd"] if row["pnl_usd"] is not None else missing_numeric, reverse=reverse)

    mdd_values_available = any(row["mdd_available"] for row in filtered)
    return {
        "rows": filtered[:limit],
        "counts": {
            "returned": len(filtered[:limit]),
            "filtered": len(filtered),
            "scanned": len(rows),
            "mdd_computed": computed_mdd,
        },
        "sort": sort,
        "direction": direction,
        "period": period,
        "category": category,
        "limit": limit,
        "scan_limit": scan_limit,
        "mdd_scan_limit": mdd_scan_limit,
        "mdd_history_limit": mdd_history_limit,
        "mdd_activity_limit": mdd_activity_limit,
        "mdd_trade_limit": mdd_trade_limit,
        "mdd_open_limit": mdd_open_limit,
        "mdd_mode": mdd_mode,
        "mdd_mark_replay_token_limit": mdd_mark_replay_token_limit,
        "mdd_mark_replay_point_limit": mdd_mark_replay_point_limit,
        "mdd_mark_replay_interval": mdd_mark_replay_interval,
        "mdd_mark_replay_fidelity": mdd_mark_replay_fidelity,
        "mdd_include_accounting": mdd_include_accounting,
        "mdd_accounting_timeout": mdd_accounting_timeout,
        "mdd_persist_cache": mdd_persist_cache,
        "mdd_cache_ttl_seconds": mdd_cache_ttl_seconds,
        "analytics_cache": analytics_cache_summary(enabled=mdd_persist_cache),
        "rate_limit": {
            "limited": bool(rate_limit_events),
            "backoff_status": "retry_later" if rate_limit_events else "not_limited",
            "events": rate_limit_events,
        },
        "source": "polymarket_data_api_leaderboard",
        "source_sort": remote_sort,
        "ranking_scope": "computed_from_scanned_public_leaderboard_rows_with_optional_public_data_mdd_v2",
        "mdd_available": mdd_values_available,
        "mdd_method": MDD_METHOD_MARK_REPLAY if mdd_mode == "mark_replay" else MDD_METHOD_V2,
        "mdd_pct_basis": MDD_PCT_BASIS_V2,
        "mdd_note": (
            "MDD mark replay is opt-in and uses CLOB price history for trade-derived token inventory; rows without reconstructable marks fall back to v2."
            if mdd_mode == "mark_replay"
            else "MDD v2 uses public closed-position realized PnL, public trade/activity capital basis, and the current open-position snapshot; complete account-equity MDD still requires cash-flow ledger and historical mark replay."
        ),
        "mdd_assumptions": list(MDD_MARK_REPLAY_ASSUMPTIONS if mdd_mode == "mark_replay" else MDD_V2_ASSUMPTIONS)
        + (list(MDD_ACCOUNTING_ASSUMPTIONS) if mdd_include_accounting else []),
        "mdd_limitations": list(MDD_MARK_REPLAY_LIMITATIONS if mdd_mode == "mark_replay" else MDD_V2_LIMITATIONS)
        + (list(MDD_ACCOUNTING_LIMITATIONS) if mdd_include_accounting else []),
        "warnings": warnings,
    }


def find_wallet(cfg: AppConfig, wallet_id: str) -> WalletWatch:
    normalized = str(wallet_id or "").strip()
    for wallet in cfg.wallets:
        if wallet.id == normalized:
            return wallet
    raise ValueError(f"Unknown wallet id: {normalized}")


def wallet_from_payload(payload: Mapping[str, Any], existing: Optional[WalletWatch] = None) -> WalletWatch:
    raw_wallet = str(payload.get("wallet") if "wallet" in payload else (existing.wallet if existing else "")).strip()
    wallet = normalize_wallet(raw_wallet)
    if not wallet:
        raise ValueError("wallet must be a valid 0x wallet/proxyWallet address.")
    display_name = str(
        payload.get("display_name") if "display_name" in payload else (existing.display_name if existing else "")
    ).strip()
    enabled = bool_from_setting(payload.get("enabled"), existing.enabled if existing else True)
    only_market_slug = str(
        payload.get("only_market_slug")
        if "only_market_slug" in payload
        else (existing.only_market_slug if existing else "")
    ).strip()
    if existing is None:
        return WalletWatch(wallet=wallet, display_name=display_name or f"{wallet[:10]}...", enabled=enabled, only_market_slug=only_market_slug)
    existing.wallet = wallet
    existing.display_name = display_name
    existing.enabled = enabled
    existing.only_market_slug = only_market_slug
    return existing


def add_wallet_watch(cfg: AppConfig, payload: Mapping[str, Any]) -> WalletWatch:
    require_polymarket_selected(cfg, "Wallet tracking")
    wallet = wallet_from_payload(payload)
    if any(item.wallet == wallet.wallet for item in cfg.wallets):
        raise ValueError("This wallet is already being tracked.")
    cfg.wallets.append(wallet)
    return wallet


def update_wallet_watch(cfg: AppConfig, wallet_id: str, payload: Mapping[str, Any]) -> WalletWatch:
    wallet = find_wallet(cfg, wallet_id)
    wallet_from_payload(payload, existing=wallet)
    duplicates = [item for item in cfg.wallets if item.wallet == wallet.wallet and item.id != wallet.id]
    if duplicates:
        raise ValueError("This wallet is already being tracked.")
    return wallet


def delete_wallet_watch(cfg: AppConfig, wallet_id: str) -> WalletWatch:
    wallet = find_wallet(cfg, wallet_id)
    cfg.wallets = [item for item in cfg.wallets if item.id != wallet.id]
    return wallet


def copy_payload(
    cfg: AppConfig,
    registry: Optional[AdapterRegistry] = None,
) -> Dict[str, Any]:
    registry = registry or build_default_registry()
    market_cfg = cfg.markets.get("polymarket")
    settings = market_cfg.settings if market_cfg else {}
    status = "simulation"
    if not cfg.copytrading.enabled:
        status = "disabled"
    elif cfg.copytrading.live:
        status = "live requested"
    live_gate = {
        "market_enabled": bool(market_cfg.enabled) if market_cfg else False,
        "live_trading_enabled": bool_from_setting(settings.get("live_trading_enabled"), False),
        "live_trading_confirmed": bool_from_setting(settings.get("live_trading_confirmed"), False)
        or bool_from_setting(settings.get("live_trading_acknowledged"), False),
        "live_trading_kill_switch": bool_from_setting(settings.get("live_trading_kill_switch"), False)
        or bool_from_setting(settings.get("live_trading_paused"), False),
        "max_size": _safe_float(settings.get("live_trading_max_size"), None),
        "max_notional": _safe_float(settings.get("live_trading_max_notional"), None),
    }
    try:
        adapter = adapter_for_market(cfg, "polymarket", registry)
        capability = adapter.capabilities.copy_trading
        adapter_name = adapter.display_name
    except Exception:
        capability = False
        adapter_name = "polymarket"
    followed_wallets = cfg.copytrading.normalized_follow_wallets()
    tracked_wallets = {wallet.wallet for wallet in cfg.wallets}
    return {
        "settings": cfg.copytrading.to_dict(),
        "wallet_choices": [wallet.wallet for wallet in cfg.wallets],
        "follow_wallet_tracked": any(wallet in tracked_wallets for wallet in followed_wallets),
        "follow_wallets_tracked": sum(1 for wallet in followed_wallets if wallet in tracked_wallets),
        "follow_wallets_untracked": [wallet for wallet in followed_wallets if wallet not in tracked_wallets],
        "status": status,
        "simulation_first": not cfg.copytrading.live,
        "copy_trading_supported": bool(capability),
        "adapter": adapter_name,
        "live_gate": live_gate,
    }


def _wallets_from_copy_payload(payload: Mapping[str, Any], existing: CopyTradeSettings) -> List[str]:
    raw_values: List[Any] = []
    if "follow_wallets" in payload:
        raw = payload.get("follow_wallets")
        if isinstance(raw, list):
            raw_values.extend(raw)
        elif isinstance(raw, str):
            raw_values.extend(raw.replace(";", ",").split(","))
        elif raw not in (None, ""):
            raise ValueError("follow_wallets must be a list or comma-separated string.")
    elif "follow_wallet" in payload:
        raw_values.append(payload.get("follow_wallet"))
    else:
        raw_values.extend(existing.normalized_follow_wallets())

    wallets: List[str] = []
    for raw in raw_values:
        raw_wallet = str(raw or "").strip().lower()
        if not raw_wallet:
            continue
        normalized = normalize_wallet(raw_wallet)
        if not normalized:
            raise ValueError("follow_wallets must contain only valid 0x wallet/proxyWallet addresses.")
        if normalized not in wallets:
            wallets.append(normalized)
    return wallets


def copy_settings_from_payload(payload: Mapping[str, Any], existing: CopyTradeSettings) -> CopyTradeSettings:
    follow_wallets = _wallets_from_copy_payload(payload, existing)
    percentage_keys = ("copy_percentage", "scale_percent", "percentage")
    percentage_value = next((payload[key] for key in percentage_keys if key in payload), None)
    if percentage_value is not None:
        copy_percentage = _safe_float(percentage_value, None)
        if copy_percentage is None or copy_percentage < 0 or copy_percentage > 100:
            raise ValueError("copy_percentage must be a number between 0 and 100.")
        scale = float(copy_percentage) / 100.0
    elif "scale" in payload:
        scale = _safe_float(payload.get("scale"), None)
    else:
        scale = max(0.0, min(float(existing.scale), 1.0))
    max_usdc = _safe_float(payload.get("max_usdc_per_trade", existing.max_usdc_per_trade), None)
    slippage = _safe_float(payload.get("slippage", existing.slippage), None)
    if scale is None or scale < 0 or scale > 1:
        raise ValueError("scale must be a number between 0 and 1, matching copy_percentage 0..100.")
    if max_usdc is None or max_usdc <= 0:
        raise ValueError("max_usdc_per_trade must be a positive number.")
    if slippage is None or slippage < 0 or slippage > 1:
        raise ValueError("slippage must be a number between 0 and 1.")
    try:
        conflict_window = int(payload.get("conflict_window_seconds", existing.conflict_window_seconds))
    except (TypeError, ValueError) as exc:
        raise ValueError("conflict_window_seconds must be an integer.") from exc
    if conflict_window < 0 or conflict_window > 86400:
        raise ValueError("conflict_window_seconds must be between 0 and 86400.")
    return CopyTradeSettings(
        enabled=bool_from_setting(payload.get("enabled"), existing.enabled),
        live=bool_from_setting(payload.get("live"), existing.live),
        follow_wallet=follow_wallets[0] if follow_wallets else "",
        follow_wallets=follow_wallets,
        scale=float(scale),
        max_usdc_per_trade=float(max_usdc),
        slippage=float(slippage),
        allow_sells=bool_from_setting(payload.get("allow_sells"), existing.allow_sells),
        conflict_guard=bool_from_setting(payload.get("conflict_guard"), existing.conflict_guard),
        conflict_window_seconds=conflict_window,
    )


def apply_copy_settings_patch(cfg: AppConfig, payload: Mapping[str, Any]) -> CopyTradeSettings:
    require_polymarket_selected(cfg, "Copy trading settings")
    cfg.copytrading = copy_settings_from_payload(payload, cfg.copytrading)
    return cfg.copytrading


def copy_trade_guard_key(activity: Mapping[str, Any]) -> str:
    token_id = str(activity.get("asset") or "").strip().lower()
    market_slug = str(activity.get("slug") or "").strip().lower()
    outcome = str(activity.get("outcome") or "").strip().lower()
    return "|".join(part for part in (token_id, market_slug, outcome) if part)


def copy_trade_conflict_reason(
    settings: CopyTradeSettings,
    activity: Mapping[str, Any],
    conflict_state: Optional[Dict[str, Dict[str, Any]]],
) -> Optional[str]:
    if conflict_state is None or not settings.conflict_guard:
        return None
    key = copy_trade_guard_key(activity)
    if not key:
        return None
    side = str(activity.get("side") or "").strip().upper()
    wallet = str(activity.get("proxyWallet") or "").strip().lower()
    timestamp = int(_safe_float(activity.get("timestamp"), time.time()) or time.time())
    window = max(0, int(settings.conflict_window_seconds or 0))
    if window:
        stale = [
            state_key
            for state_key, state in conflict_state.items()
            if timestamp - int(state.get("timestamp") or timestamp) > window
        ]
        for state_key in stale:
            conflict_state.pop(state_key, None)
    existing = conflict_state.get(key)
    if existing:
        previous_side = str(existing.get("side") or "").upper()
        previous_wallet = str(existing.get("wallet") or "")
        if previous_wallet == wallet:
            conflict_state[key] = {
                "side": side,
                "wallet": wallet,
                "activity_key": activity_key(activity),
                "timestamp": timestamp,
            }
            return None
        if previous_side and previous_side != side:
            return f"conflict guard blocked opposite-side copy for the same token from {previous_wallet}"
        return f"conflict guard skipped duplicate same-token copy already accepted from {previous_wallet}"
    conflict_state[key] = {
        "side": side,
        "wallet": wallet,
        "activity_key": activity_key(activity),
        "timestamp": timestamp,
    }
    return None


def copy_trade_preview_from_activity(
    cfg: AppConfig,
    registry: AdapterRegistry,
    activity: Mapping[str, Any],
    conflict_state: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    if str(cfg.selected_market_id or "").strip().lower() != "polymarket":
        return {"status": "skipped", "reason": "selected market is not polymarket"}
    settings = cfg.copytrading
    if not settings.enabled:
        return {"status": "skipped", "reason": "copy trading disabled"}
    followed_wallets = settings.normalized_follow_wallets()
    if not followed_wallets:
        return {"status": "skipped", "reason": "follow wallet is not set"}
    if str(activity.get("proxyWallet") or "").strip().lower() not in followed_wallets:
        return {"status": "skipped", "reason": "activity wallet does not match follow wallet"}
    side = str(activity.get("side") or "").strip().upper()
    if side not in {"BUY", "SELL"}:
        return {"status": "skipped", "reason": "activity side is not BUY or SELL"}
    if side == "SELL" and not settings.allow_sells:
        return {"status": "skipped", "reason": "SELL copying disabled"}
    token_id = str(activity.get("asset") or "").strip()
    if not token_id:
        return {"status": "skipped", "reason": "activity has no asset token"}
    raw_size = _safe_float(activity.get("size"), 0.0) or 0.0
    raw_price = _safe_float(activity.get("price"), None)
    size = max(0.0, raw_size * float(settings.scale))
    adapter = adapter_for_market(cfg, "polymarket", registry)
    best_bid = best_ask = None
    try:
        orderbook = adapter.get_orderbook(token_id)
        best_bid = orderbook.bids[0].price if orderbook.bids else None
        best_ask = orderbook.asks[0].price if orderbook.asks else None
    except Exception:
        pass
    slippage = max(0.0, min(float(settings.slippage), 1.0))
    if side == "BUY":
        reference_price = best_ask if best_ask is not None else raw_price
        limit_price = min(1.0, float(reference_price if reference_price is not None else 0.99) + slippage)
    else:
        reference_price = best_bid if best_bid is not None else raw_price
        limit_price = max(0.0, float(reference_price if reference_price is not None else 0.01) - slippage)
    max_usdc = max(0.01, float(settings.max_usdc_per_trade))
    capped = False
    if limit_price > 0:
        max_shares = max_usdc / limit_price
        if size > max_shares:
            size = max_shares
            capped = True
    if size <= 0:
        return {"status": "skipped", "reason": "computed copy size is zero"}
    conflict_reason = copy_trade_conflict_reason(settings, activity, conflict_state)
    if conflict_reason:
        return {"status": "skipped", "reason": conflict_reason, "conflict_guard": True}
    order = PaperOrderRequest(
        market_id="polymarket",
        contract_id=token_id,
        side=side,
        size=size,
        limit_price=limit_price,
        metadata={"source": "copy_trading", "tif": "FOK", "activity_key": activity_key(activity)},
    )
    result: Dict[str, Any] = {
        "status": "live_preflight" if settings.live else "simulation",
        "live": bool(settings.live),
        "would_place_order": bool(settings.live),
        "order": {
            "market_id": order.market_id,
            "contract_id": order.contract_id,
            "side": order.side,
            "size": order.size,
            "limit_price": order.limit_price,
            "approx_notional": order.size * float(order.limit_price or 0.0),
        },
        "pricing": {
            "raw_price": raw_price,
            "best_bid": best_bid,
            "best_ask": best_ask,
            "slippage": slippage,
            "capped_by_max_usdc": capped,
        },
        "conflict_guard": bool(settings.conflict_guard),
    }
    if settings.live:
        try:
            result["preflight"] = adapter.preflight_live_order(order, feature_name="live copy trading")
            result["blocked"] = False
            result["message"] = "Live copy preflight passed. No order was placed."
        except Exception as exc:
            result["blocked"] = True
            result["message"] = f"Live copy preflight blocked: {exc}"
    else:
        result["blocked"] = False
        result["message"] = (
            f"Simulation: would {side} token={token_id[:10]}... size={size:.4f} "
            f"limit={limit_price:.4f}; no order placed."
        )
    return result


def copy_preview_payload(cfg: AppConfig, registry: AdapterRegistry, payload: Mapping[str, Any]) -> Dict[str, Any]:
    require_polymarket_selected(cfg, "Copy trading preview")
    default_wallets = cfg.copytrading.normalized_follow_wallets()
    activity = {
        "proxyWallet": payload.get("proxyWallet") or payload.get("proxy_wallet") or (default_wallets[0] if default_wallets else ""),
        "asset": payload.get("asset") or payload.get("token_id") or "",
        "side": payload.get("side") or "BUY",
        "size": payload.get("size") if "size" in payload else 0,
        "price": payload.get("price") if "price" in payload else None,
        "timestamp": int(time.time()),
        "slug": payload.get("slug") or "",
        "outcome": payload.get("outcome") or "",
    }
    preview = copy_trade_preview_from_activity(cfg, registry, activity)
    return {"preview": preview, "copy": copy_payload(cfg, registry)}


def poll_wallet_activity(
    cfg: AppConfig,
    registry: AdapterRegistry,
    recent_activity: List[Dict[str, Any]],
    *,
    limit: int = 25,
) -> Dict[str, Any]:
    require_polymarket_selected(cfg, "Wallet polling")
    emitted: List[Dict[str, Any]] = []
    problems: List[str] = []
    copy_conflicts: Dict[str, Dict[str, Any]] = {}
    for wallet in list(cfg.wallets):
        if not wallet.enabled:
            continue
        try:
            items = data_api.get_activity(wallet.wallet, limit=limit, types=["TRADE"])
        except Exception as exc:
            problems.append(f"{wallet.wallet}: {exc}")
            continue
        seen_keys = set(wallet.seen_activity_keys or [])
        new_items: List[Tuple[str, Mapping[str, Any]]] = []
        for item in reversed(items):
            key = activity_key(item)
            if key in seen_keys:
                continue
            timestamp = int(item.get("timestamp") or 0)
            tx = str(item.get("transactionHash") or "")
            if timestamp > (wallet.last_seen_ts or 0):
                new_items.append((key, item))
                seen_keys.add(key)
            elif timestamp == (wallet.last_seen_ts or 0) and (not tx or tx != (wallet.last_seen_tx or "")):
                new_items.append((key, item))
                seen_keys.add(key)
        for key, item in new_items:
            if wallet.only_market_slug and str(item.get("slug") or "") != wallet.only_market_slug:
                continue
            wallet.last_seen_ts = max(wallet.last_seen_ts or 0, int(item.get("timestamp") or 0))
            wallet.last_seen_tx = str(item.get("transactionHash") or wallet.last_seen_tx or "")
            wallet.seen_activity_keys.append(key)
            if len(wallet.seen_activity_keys) > 200:
                wallet.seen_activity_keys = wallet.seen_activity_keys[-200:]
            activity = wallet_activity_payload(wallet, item)
            activity["copy_preview"] = copy_trade_preview_from_activity(cfg, registry, item, copy_conflicts)
            emitted.insert(0, activity)
            recent_activity.insert(0, activity)
    del recent_activity[100:]
    return {"activity": emitted, "problems": problems, "polled_wallets": sum(1 for wallet in cfg.wallets if wallet.enabled)}


def paper_order_from_payload(payload: Mapping[str, Any]) -> PaperOrderRequest:
    market_id = str(payload.get("market_id") or "").strip().lower()
    contract_id = str(payload.get("contract_id") or "").strip()
    side = str(payload.get("side") or "").strip().upper()
    if not market_id:
        raise ValueError("market_id is required.")
    if not contract_id:
        raise ValueError("contract_id is required.")
    if side not in {"BUY", "SELL", "BACK", "LAY"}:
        raise ValueError("side must be BUY, SELL, BACK, or LAY.")
    size = optional_positive_float(payload.get("size"), "Order size")
    if size is None:
        raise ValueError("Order size is required.")
    limit_price = optional_positive_float(payload.get("limit_price"), "Limit price")
    return PaperOrderRequest(
        market_id=market_id,
        contract_id=contract_id,
        side=side,
        size=size,
        limit_price=limit_price,
        metadata=dict(payload.get("metadata") or {}),
    )


def paper_order_impact(records: List[PaperTradeRecord], order: PaperOrderRequest) -> Dict[str, Any]:
    current_row = next(
        (
            row
            for row in paper_position_rows(records)
            if row["market_id"] == order.market_id and row["contract_id"] == order.contract_id
        ),
        None,
    )
    current_net = float(current_row["net_size"]) if current_row else 0.0
    current_notional = current_row.get("notional") if current_row else None
    signed_size = _paper_order_signed_size(order)
    projected_net = current_net + signed_size
    order_notional = signed_size * float(order.limit_price) if order.limit_price is not None else None
    projected_notional = (
        float(current_notional) + float(order_notional)
        if current_notional is not None and order_notional is not None
        else None
    )
    projected_average = (
        abs(projected_notional) / abs(projected_net)
        if projected_notional is not None and projected_net != 0
        else None
    )
    return {
        "market_id": order.market_id,
        "contract_id": order.contract_id,
        "side": order.side,
        "size": order.size,
        "limit_price": order.limit_price,
        "current_net": current_net,
        "signed_size": signed_size,
        "projected_net": projected_net,
        "effect": _paper_order_effect(current_net, signed_size, projected_net),
        "order_notional": order_notional,
        "projected_notional": projected_notional,
        "projected_average": projected_average,
    }


def format_paper_order_impact(impact: Mapping[str, Any]) -> str:
    parts = [
        f"Impact: {impact['market_id']}:{impact['contract_id']}",
        f"{impact['side']} size={float(impact['size']):g}",
        f"current_net={float(impact['current_net']):.4f}",
        f"order_net={float(impact['signed_size']):.4f}",
        f"projected_net={float(impact['projected_net']):.4f}",
        f"effect={impact['effect']}",
    ]
    if impact.get("order_notional") is None:
        parts.append("limit blank")
    else:
        parts.append(f"order_notional={float(impact['order_notional']):.4f}")
    if impact.get("projected_notional") is not None:
        parts.append(f"projected_notional={float(impact['projected_notional']):.4f}")
    if impact.get("projected_average") is not None:
        parts.append(f"projected_avg={float(impact['projected_average']):.4f}")
    return "; ".join(parts)


def serialize_price_snapshot(snapshot: Optional[PriceSnapshot]) -> Optional[Dict[str, Any]]:
    if snapshot is None:
        return None
    midpoint = snapshot.midpoint
    if midpoint is None and snapshot.bid is not None and snapshot.ask is not None:
        midpoint = (float(snapshot.bid) + float(snapshot.ask)) / 2.0
    return {
        "market_id": snapshot.market_id,
        "contract_id": snapshot.contract_id,
        "last": snapshot.last,
        "bid": snapshot.bid,
        "ask": snapshot.ask,
        "midpoint": midpoint,
        "source": snapshot.source,
    }


def serialize_orderbook(orderbook: Optional[OrderBookSnapshot]) -> Optional[Dict[str, Any]]:
    if orderbook is None:
        return None
    return {
        "market_id": orderbook.market_id,
        "contract_id": orderbook.contract_id,
        "bids": [{"price": level.price, "size": level.size} for level in orderbook.bids],
        "asks": [{"price": level.price, "size": level.size} for level in orderbook.asks],
        "best_bid": orderbook.bids[0].price if orderbook.bids else None,
        "best_ask": orderbook.asks[0].price if orderbook.asks else None,
    }


def paper_quote_payload(cfg: AppConfig, registry: AdapterRegistry, payload: Mapping[str, Any]) -> Dict[str, Any]:
    market_id = str(payload.get("market_id") or "").strip().lower()
    contract_id = str(payload.get("contract_id") or "").strip()
    if not market_id:
        raise ValueError("market_id is required.")
    if not contract_id:
        raise ValueError("contract_id is required.")
    require_market_enabled(cfg, market_id, "paper quote")
    adapter = adapter_for_market(cfg, market_id, registry)
    if not (adapter.capabilities.price_reading or adapter.capabilities.orderbook_reading):
        raise UnsupportedFeatureError(market_id, "price_reading", f"{adapter.display_name} does not support quote previews.")
    snapshot = adapter.get_price(contract_id) if adapter.capabilities.price_reading else None
    orderbook = adapter.get_orderbook(contract_id) if adapter.capabilities.orderbook_reading else None
    price = serialize_price_snapshot(snapshot)
    book = serialize_orderbook(orderbook)
    best_bid = (book or {}).get("best_bid")
    best_ask = (book or {}).get("best_ask")
    if price:
        best_bid = best_bid if best_bid is not None else price.get("bid")
        best_ask = best_ask if best_ask is not None else price.get("ask")
    return {
        "market_id": market_id,
        "contract_id": contract_id,
        "display_name": adapter.display_name,
        "price": price,
        "orderbook": book,
        "best_bid": best_bid,
        "best_ask": best_ask,
        "suggested_limits": {
            "BUY": best_ask,
            "BACK": best_ask,
            "SELL": best_bid,
            "LAY": best_bid,
        },
        "message": f"Quote: {adapter.display_name} {contract_id}",
    }


def paper_quote_limit_payload(cfg: AppConfig, registry: AdapterRegistry, payload: Mapping[str, Any]) -> Dict[str, Any]:
    side = str(payload.get("side") or "").strip().upper()
    if side not in {"BUY", "SELL", "BACK", "LAY"}:
        raise ValueError("side must be BUY, SELL, BACK, or LAY.")
    quote = paper_quote_payload(cfg, registry, payload)
    source = "best_ask" if side in {"BUY", "BACK"} else "best_bid"
    limit_price = quote["best_ask"] if source == "best_ask" else quote["best_bid"]
    if limit_price is None:
        raise ValueError(f"No {source} is available for {side}.")
    return {
        "market_id": quote["market_id"],
        "contract_id": quote["contract_id"],
        "side": side,
        "limit_price": float(limit_price),
        "source": source,
        "quote": quote,
        "message": f"Loaded {source} limit {float(limit_price):g} for {side}.",
    }


def record_paper_trade(cfg: AppConfig, order: PaperOrderRequest, result: PaperOrderResult) -> PaperTradeRecord:
    record = PaperTradeRecord(
        market_id=order.market_id,
        contract_id=order.contract_id,
        side=order.side,
        size=order.size,
        limit_price=order.limit_price,
        accepted=result.accepted,
        message=result.message,
        filled_size=result.filled_size,
        average_price=result.average_price,
        raw={"request": order.metadata, "result": result.raw},
    )
    cfg.paper_trades.insert(0, record)
    if len(cfg.paper_trades) > 200:
        cfg.paper_trades = cfg.paper_trades[:200]
    return record


def submit_paper_order(cfg: AppConfig, registry: AdapterRegistry, payload: Mapping[str, Any]) -> Dict[str, Any]:
    order = paper_order_from_payload(payload)
    require_market_enabled(cfg, order.market_id, "paper trading")
    adapter = adapter_for_market(cfg, order.market_id, registry)
    if not adapter.capabilities.paper_trading:
        raise UnsupportedFeatureError(order.market_id, "paper_trading")
    result = adapter.place_paper_order(order)
    record = record_paper_trade(cfg, order, result)
    return {
        "order": {
            "market_id": order.market_id,
            "contract_id": order.contract_id,
            "side": order.side,
            "size": order.size,
            "limit_price": order.limit_price,
        },
        "result": {
            "market_id": result.market_id,
            "contract_id": result.contract_id,
            "accepted": result.accepted,
            "message": result.message,
            "filled_size": result.filled_size,
            "average_price": result.average_price,
        },
        "record": record.to_dict(),
    }


def history_refill_payload(cfg: AppConfig, record_id: str) -> Dict[str, Any]:
    for record in cfg.paper_trades:
        if record.id == record_id:
            return {
                "market_id": record.market_id,
                "contract_id": record.contract_id,
                "side": record.side,
                "size": record.size,
                "limit_price": record.limit_price,
                "message": f"Loaded paper history order: {record.market_id}:{record.contract_id} {record.side} size={record.size:g}",
            }
    raise ValueError("Paper history record was not found.")


def position_close_side(records: List[PaperTradeRecord], market_id: str, contract_id: str, net_size: float) -> str:
    matching_sides = [
        str(record.side or "").upper()
        for record in records
        if record.accepted and record.market_id == market_id and record.contract_id == contract_id
    ]
    uses_back_lay = any(side in {"BACK", "LAY"} for side in matching_sides)
    uses_buy_sell = any(side in {"BUY", "SELL"} for side in matching_sides)
    if uses_back_lay and not uses_buy_sell:
        return "LAY" if net_size > 0 else "BACK"
    return "SELL" if net_size > 0 else "BUY"


def position_refill_payload(cfg: AppConfig, market_id: str, contract_id: str) -> Dict[str, Any]:
    normalized = str(market_id or "").strip().lower()
    contract = str(contract_id or "").strip()
    row = next(
        (
            item
            for item in paper_position_rows(cfg.paper_trades)
            if item["market_id"] == normalized and item["contract_id"] == contract
        ),
        None,
    )
    if not row:
        raise ValueError("Paper position was not found.")
    net_size = float(row["net_size"])
    side = position_close_side(cfg.paper_trades, normalized, contract, net_size)
    return {
        "market_id": normalized,
        "contract_id": contract,
        "side": side,
        "size": abs(net_size),
        "limit_price": None,
        "message": f"Loaded paper position: {normalized}:{contract} {side} size={abs(net_size):g}; limit cleared.",
    }


def refresh_paper_marks(
    cfg: AppConfig,
    registry: AdapterRegistry,
    rows: List[Dict[str, Any]],
    existing_marks: Optional[Mapping[Tuple[str, str], Dict[str, Any]]] = None,
) -> Tuple[Dict[Tuple[str, str], Dict[str, Any]], List[str]]:
    marks: Dict[Tuple[str, str], Dict[str, Any]] = _paper_marks_for_rows(existing_marks or {}, rows)
    problems: List[str] = []
    marked_at = int(time.time())
    for row in rows:
        market_id = str(row["market_id"])
        contract_id = str(row["contract_id"])
        if not cfg.markets.get(market_id) or not cfg.markets[market_id].enabled:
            problems.append(f"{market_id}: disabled")
            continue
        try:
            adapter = adapter_for_market(cfg, market_id, registry)
            if not adapter.capabilities.price_reading:
                problems.append(f"{adapter.display_name}: no price feed")
                continue
            snapshot = adapter.get_price(contract_id)
            mark_price, source = _paper_position_mark_price(snapshot, float(row["net_size"]))
            marks[(market_id, contract_id)] = {
                "mark_price": mark_price,
                "source": source,
                "marked_at": marked_at,
            }
        except Exception as exc:
            problems.append(f"{market_id}:{contract_id}: {exc}")
    return _paper_marks_for_rows(marks, rows), problems


def refresh_selected_paper_mark(
    cfg: AppConfig,
    registry: AdapterRegistry,
    market_id: str,
    contract_id: str,
    marks: Mapping[Tuple[str, str], Dict[str, Any]],
) -> Dict[Tuple[str, str], Dict[str, Any]]:
    normalized = str(market_id or "").strip().lower()
    contract = str(contract_id or "").strip()
    rows = paper_position_rows(cfg.paper_trades)
    row = next((item for item in rows if item["market_id"] == normalized and item["contract_id"] == contract), None)
    if not row:
        raise ValueError("Paper position was not found.")
    require_market_enabled(cfg, normalized, "paper mark refresh")
    adapter = adapter_for_market(cfg, normalized, registry)
    if not adapter.capabilities.price_reading:
        raise UnsupportedFeatureError(normalized, "price_reading", f"{adapter.display_name} does not support paper mark refresh.")
    snapshot = adapter.get_price(contract)
    mark_price, source = _paper_position_mark_price(snapshot, float(row["net_size"]))
    updated = _paper_marks_for_rows(marks, rows)
    updated[(normalized, contract)] = {
        "mark_price": mark_price,
        "source": source,
        "marked_at": int(time.time()),
    }
    return updated


class ReactGuiServer(ThreadingHTTPServer):
    def __init__(
        self,
        server_address: tuple[str, int],
        request_handler_class: type[BaseHTTPRequestHandler],
        *,
        config_path: Path = DEFAULT_CONFIG_PATH,
        frontend_dir: Path = DEFAULT_FRONTEND_DIR,
        adapter_registry: Optional[AdapterRegistry] = None,
    ) -> None:
        super().__init__(server_address, request_handler_class)
        self.config_path = config_path
        self.frontend_dir = frontend_dir
        self.adapter_registry = adapter_registry or build_default_registry()
        self.paper_position_marks: Dict[Tuple[str, str], Dict[str, Any]] = {}
        self.alert_price_state: Dict[Tuple[str, str], Dict[str, Any]] = {}
        self.wallet_recent_activity: List[Dict[str, Any]] = []
        self.wallet_polling: Dict[str, Any] = {
            "poll_interval_seconds": 10.0,
            "last_polled_at": None,
            "last_message": "Not polled yet.",
        }


class ReactGuiHandler(BaseHTTPRequestHandler):
    server_version = "PredictionMarketReactGui/0.1"

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self._send_cors_headers()
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            self._handle_api_get(parsed.path, parsed.query)
            return
        self._serve_static(parsed.path)

    def do_PATCH(self) -> None:
        self._handle_mutation("PATCH")

    def do_POST(self) -> None:
        self._handle_mutation("POST")

    def do_DELETE(self) -> None:
        self._handle_mutation("DELETE")

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[web-gui] {self.address_string()} - {fmt % args}")

    @property
    def app_server(self) -> ReactGuiServer:
        return self.server  # type: ignore[return-value]

    def _load_config(self) -> AppConfig:
        return load_config(self.app_server.config_path)

    def _save_config(self, cfg: AppConfig) -> None:
        save_config(cfg, self.app_server.config_path)

    def _handle_api_get(self, path: str, query: str = "") -> None:
        try:
            cfg = self._load_config()
            query_params = parse_qs(query, keep_blank_values=True)
            if path == "/api/health":
                self._send_json(HTTPStatus.OK, health_payload(self.app_server.config_path, self.app_server.frontend_dir))
                return
            if path == "/api/state":
                self._send_json(
                    HTTPStatus.OK,
                    app_state_payload(
                        cfg,
                        self.app_server.config_path,
                        self.app_server.frontend_dir,
                        self.app_server.paper_position_marks,
                        self.app_server.adapter_registry,
                        self.app_server.alert_price_state,
                        self.app_server.wallet_polling,
                        self.app_server.wallet_recent_activity,
                    ),
                )
                return
            if path == "/api/config":
                self._send_json(HTTPStatus.OK, config_payload(cfg))
                return
            if path == "/api/markets":
                self._send_json(HTTPStatus.OK, markets_payload(cfg, self.app_server.adapter_registry))
                return
            if path == "/api/alerts":
                self._send_json(HTTPStatus.OK, alerts_payload(cfg, self.app_server.adapter_registry, self.app_server.alert_price_state))
                return
            if path == "/api/wallets":
                self._send_json(HTTPStatus.OK, wallets_payload(cfg, self.app_server.wallet_polling, self.app_server.wallet_recent_activity))
                return
            if path == "/api/copy":
                self._send_json(HTTPStatus.OK, copy_payload(cfg, self.app_server.adapter_registry))
                return
            if path == "/api/live-safety":
                self._send_json(HTTPStatus.OK, live_safety_payload(cfg, self.app_server.adapter_registry))
                return
            if path == "/api/paper":
                self._send_json(HTTPStatus.OK, paper_payload(cfg, self.app_server.paper_position_marks))
                return
            if path == "/api/paper/history":
                self._send_json(HTTPStatus.OK, {"history": paper_payload(cfg, self.app_server.paper_position_marks)["history"]})
                return
            if path == "/api/paper/positions":
                paper = paper_payload(cfg, self.app_server.paper_position_marks)
                self._send_json(HTTPStatus.OK, {"summary": paper["summary"], "positions": paper["positions"]})
                return
            if path == "/api/polymarket/users/search":
                self._send_json(
                    HTTPStatus.OK,
                    polymarket_user_search_payload(
                        _query_value(query_params, "q"),
                        _clamp_int(_query_value(query_params, "limit", "10"), 10, 1, 50),
                    ),
                )
                return
            if path == "/api/polymarket/users/leaderboard":
                self._send_json(HTTPStatus.OK, polymarket_leaderboard_payload(query_params))
                return
            if path == "/api/polymarket/users/mdd/cache/health":
                self._send_json(HTTPStatus.OK, polymarket_mdd_cache_health_payload())
                return
            if path == "/api/polymarket/users/mdd/cache":
                self._send_json(
                    HTTPStatus.OK,
                    polymarket_mdd_cache_payload(include_expired=_query_bool(query_params, "include_expired", True)),
                )
                return
            if path == "/api/polymarket/users/mdd/export.json":
                self._send_json(HTTPStatus.OK, polymarket_mdd_export_payload(_query_value(query_params, "key")))
                return
            if path == "/api/polymarket/users/mdd/export.csv":
                export = polymarket_mdd_export_csv(_query_value(query_params, "key"))
                self._send_text(
                    HTTPStatus.OK,
                    export["csv"],
                    content_type="text/csv; charset=utf-8",
                    filename=export["filename"],
                )
                return
            if path == "/api/polymarket/users/mdd":
                wallet = _query_value(query_params, "user") or _query_value(query_params, "wallet")
                mdd_options = {
                    "mode": _query_value(query_params, "mode", _query_value(query_params, "mdd_mode", "fast")),
                    "closed_limit": _clamp_int(_query_value(query_params, "closed_limit", "500"), 500, 1, 1000),
                    "open_limit": _clamp_int(_query_value(query_params, "open_limit", "500"), 500, 0, 1000),
                    "activity_limit": _clamp_int(_query_value(query_params, "activity_limit", "1000"), 1000, 0, 5000),
                    "trade_limit": _clamp_int(_query_value(query_params, "trade_limit", "1000"), 1000, 0, 5000),
                    "include_open": _query_bool(query_params, "include_open", True),
                    "equity_base_usd": _query_float(query_params, "equity_base_usd"),
                    "max_points": _clamp_int(_query_value(query_params, "max_points", "50"), 50, 1, 1000),
                    "cache_ttl_seconds": _clamp_int(_query_value(query_params, "cache_ttl_seconds", "0"), 0, 0, 300),
                    "mark_replay_token_limit": _clamp_int(_query_value(query_params, "mark_replay_token_limit", "10"), 10, 1, 20),
                    "mark_replay_point_limit": _clamp_int(_query_value(query_params, "mark_replay_point_limit", "5000"), 5000, 1, 10000),
                    "mark_replay_interval": _query_value(query_params, "mark_replay_interval", "1h") or "1h",
                    "mark_replay_fidelity": _clamp_int(_query_value(query_params, "mark_replay_fidelity", "60"), 60, 1, 1440),
                    "mark_replay_start_ts": _safe_int(_query_value(query_params, "mark_replay_start_ts"), None)
                    if _query_value(query_params, "mark_replay_start_ts")
                    else None,
                    "mark_replay_end_ts": _safe_int(_query_value(query_params, "mark_replay_end_ts"), None)
                    if _query_value(query_params, "mark_replay_end_ts")
                    else None,
                    "include_accounting_snapshot": _query_bool(query_params, "include_accounting_snapshot", False),
                    "accounting_timeout": float(_clamp_int(_query_value(query_params, "accounting_timeout", "30"), 30, 1, 60)),
                }
                payload = polymarket_user_mdd_payload(wallet, **mdd_options)
                attach_polymarket_mdd_audit_cache(
                    payload,
                    polymarket_mdd_audit_params(wallet, mdd_options),
                    enabled=_query_bool(query_params, "persist_cache", _query_bool(query_params, "mdd_persist_cache", False)),
                )
                self._send_json(HTTPStatus.OK, payload)
                return
            if path == "/api/polymarket/coverage":
                coverage = polymarket_official_api_coverage()
                coverage["stored_live_validation_report_promotion"] = live_validation_report_promotion_inventory()
                self._send_json(HTTPStatus.OK, coverage)
                return
            if path == "/api/polymarket/clob-readiness":
                self._send_json(HTTPStatus.OK, polymarket_clob_readiness_payload(cfg))
                return
            if path == "/api/polymarket/live-validation/reports":
                self._send_json(
                    HTTPStatus.OK,
                    polymarket_live_validation_reports_payload(
                        include_payload=_query_bool(query_params, "include_payload", False)
                    ),
                )
                return
            if path == "/api/polymarket/live-validation/decisions":
                self._send_json(HTTPStatus.OK, polymarket_live_validation_decisions_payload(query_params))
                return
            if path == "/api/polymarket/live-validation/decisions/export.json":
                ledger = polymarket_live_validation_decisions_payload(query_params)
                self._send_text(
                    HTTPStatus.OK,
                    json.dumps(ledger, indent=2, sort_keys=True),
                    content_type="application/json; charset=utf-8",
                    filename="polymarket-live-validation-decision-ledger.json",
                )
                return
            if path == "/api/polymarket/live-validation/decisions/export.md":
                ledger = polymarket_live_validation_decisions_payload(query_params)
                self._send_text(
                    HTTPStatus.OK,
                    live_validation_report_decisions_markdown(ledger),
                    content_type="text/markdown; charset=utf-8",
                    filename="polymarket-live-validation-decision-ledger.md",
                )
                return
            if path == "/api/polymarket/live-validation/promotion-proposal":
                self._send_json(HTTPStatus.OK, polymarket_live_validation_promotion_proposal_payload(query_params))
                return
            if path == "/api/polymarket/live-validation/promotion-proposal/export.json":
                proposal = polymarket_live_validation_promotion_proposal_payload(query_params)
                self._send_text(
                    HTTPStatus.OK,
                    json.dumps(proposal, indent=2, sort_keys=True),
                    content_type="application/json; charset=utf-8",
                    filename=live_validation_coverage_promotion_proposal_export_filename("json"),
                )
                return
            if path == "/api/polymarket/live-validation/promotion-proposal/export.md":
                proposal = polymarket_live_validation_promotion_proposal_payload(query_params)
                self._send_text(
                    HTTPStatus.OK,
                    live_validation_coverage_promotion_proposal_markdown(proposal),
                    content_type="text/markdown; charset=utf-8",
                    filename=live_validation_coverage_promotion_proposal_export_filename("md"),
                )
                return
            if path == "/api/polymarket/live-validation/promotion-proposal/snapshots":
                self._send_json(HTTPStatus.OK, polymarket_live_validation_promotion_proposal_snapshots_payload())
                return
            if path.startswith("/api/polymarket/live-validation/promotion-proposal/snapshots/"):
                suffix = path[len("/api/polymarket/live-validation/promotion-proposal/snapshots/") :]
                export_json = suffix.endswith("/export.json")
                export_markdown = suffix.endswith("/export.md")
                if export_json:
                    raw_key = suffix[: -len("/export.json")]
                elif export_markdown:
                    raw_key = suffix[: -len("/export.md")]
                else:
                    raw_key = suffix
                snapshot_key = unquote(raw_key.strip("/"))
                if not snapshot_key or "/" in snapshot_key:
                    self._send_error(HTTPStatus.NOT_FOUND, "not_found", "Unknown promotion proposal snapshot.")
                    return
                snapshot = polymarket_live_validation_promotion_proposal_snapshot_payload(snapshot_key)
                if snapshot is None:
                    self._send_error(HTTPStatus.NOT_FOUND, "not_found", "Unknown promotion proposal snapshot.")
                    return
                if export_json:
                    self._send_text(
                        HTTPStatus.OK,
                        json.dumps(snapshot, indent=2, sort_keys=True),
                        content_type="application/json; charset=utf-8",
                        filename=live_validation_promotion_proposal_snapshot_export_filename(snapshot_key, "json"),
                    )
                elif export_markdown:
                    self._send_text(
                        HTTPStatus.OK,
                        live_validation_promotion_proposal_snapshot_markdown(snapshot),
                        content_type="text/markdown; charset=utf-8",
                        filename=live_validation_promotion_proposal_snapshot_export_filename(snapshot_key, "md"),
                    )
                else:
                    self._send_json(HTTPStatus.OK, snapshot)
                return
            if path.startswith("/api/polymarket/live-validation/reports/"):
                suffix = path[len("/api/polymarket/live-validation/reports/") :]
                export_json = suffix.endswith("/export.json")
                review_json = suffix.endswith("/review.json")
                review_markdown = suffix.endswith("/review.md")
                if export_json:
                    raw_key = suffix[: -len("/export.json")]
                elif review_json:
                    raw_key = suffix[: -len("/review.json")]
                elif review_markdown:
                    raw_key = suffix[: -len("/review.md")]
                else:
                    raw_key = suffix
                report_key = unquote(raw_key.strip("/"))
                if not report_key or "/" in report_key:
                    self._send_error(HTTPStatus.NOT_FOUND, "not_found", "Unknown live validation report.")
                    return
                if review_json or review_markdown:
                    review = polymarket_live_validation_report_review_payload(report_key)
                    if review is None:
                        self._send_error(HTTPStatus.NOT_FOUND, "not_found", "Unknown live validation report.")
                        return
                    if review_json:
                        self._send_text(
                            HTTPStatus.OK,
                            json.dumps(review, indent=2, sort_keys=True),
                            content_type="application/json; charset=utf-8",
                            filename=str(review["export"]["json_filename"]),
                        )
                    else:
                        self._send_text(
                            HTTPStatus.OK,
                            live_validation_report_review_markdown(review["bundle"]),
                            content_type="text/markdown; charset=utf-8",
                            filename=str(review["export"]["markdown_filename"]),
                        )
                    return
                report = polymarket_live_validation_report_payload(report_key)
                if report is None:
                    self._send_error(HTTPStatus.NOT_FOUND, "not_found", "Unknown live validation report.")
                    return
                if export_json:
                    self._send_text(
                        HTTPStatus.OK,
                        json.dumps(report, indent=2, sort_keys=True),
                        content_type="application/json; charset=utf-8",
                        filename=str(report["export"]["filename"]),
                    )
                else:
                    self._send_json(HTTPStatus.OK, report)
                return
            if path == "/api/polymarket/live-validation":
                self._send_json(HTTPStatus.OK, polymarket_live_validation_payload(cfg))
                return
            self._send_error(HTTPStatus.NOT_FOUND, "not_found", "Unknown API route.")
        except PolymarketRateLimitError as exc:
            self._send_error(
                HTTPStatus.TOO_MANY_REQUESTS,
                "polymarket_rate_limited",
                "Polymarket API rate limit was reached; retry after the upstream backoff window.",
                {"rate_limit": polymarket_rate_limit_status(exc)},
            )
        except LiveValidationReportSchemaError as exc:
            self._send_error(
                HTTPStatus.BAD_REQUEST,
                "live_validation_report_schema_error",
                "Live validation report failed schema validation.",
                {"schema_validation": exc.validation},
            )
        except ValueError as exc:
            self._send_error(HTTPStatus.BAD_REQUEST, "validation_error", str(exc))
        except Exception as exc:
            print(f"[web-gui] internal error while handling GET {path}: {type(exc).__name__}")
            self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, "internal_error", "Internal server error.")

    def _handle_mutation(self, method: str) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if not path.startswith("/api/"):
            self._send_error(HTTPStatus.NOT_FOUND, "not_found", "Unknown route.")
            return

        try:
            payload = _read_json_body(self)
            cfg = self._load_config()
            if method == "PATCH" and path == "/api/config":
                apply_config_patch(cfg, payload)
                self._save_config(cfg)
                self._send_json(HTTPStatus.OK, config_payload(cfg))
                return
            if method == "PATCH" and path.startswith("/api/markets/"):
                market_id = path.rsplit("/", 1)[-1]
                apply_market_patch(cfg, market_id, payload)
                self._save_config(cfg)
                self._send_json(HTTPStatus.OK, markets_payload(cfg, self.app_server.adapter_registry))
                return
            if method == "POST" and path == "/api/polymarket/users/mdd/cache/purge":
                self._send_json(HTTPStatus.OK, polymarket_mdd_cache_purge_payload(payload))
                return
            if method == "DELETE" and path.startswith("/api/polymarket/users/mdd/cache/"):
                cache_key = unquote(path.rsplit("/", 1)[-1])
                self._send_json(HTTPStatus.OK, polymarket_mdd_cache_purge_payload({"key": cache_key}))
                return
            if method == "POST" and path == "/api/polymarket/live-validation/reports":
                self._send_json(HTTPStatus.OK, polymarket_live_validation_report_store_payload(cfg, payload))
                return
            if method == "POST" and path == "/api/polymarket/live-validation/decisions":
                self._send_json(HTTPStatus.OK, polymarket_live_validation_decision_store_payload(payload))
                return
            if method == "POST" and path == "/api/polymarket/live-validation/promotion-proposal/snapshots":
                self._send_json(HTTPStatus.OK, polymarket_live_validation_promotion_proposal_snapshot_store_payload(payload))
                return
            if method == "DELETE" and path.startswith("/api/polymarket/live-validation/promotion-proposal/snapshots/"):
                snapshot_key = unquote(path.rsplit("/", 1)[-1])
                self._send_json(
                    HTTPStatus.OK,
                    polymarket_live_validation_promotion_proposal_snapshot_purge_payload({"key": snapshot_key}),
                )
                return
            if method == "DELETE" and path.startswith("/api/polymarket/live-validation/reports/"):
                report_key = unquote(path.rsplit("/", 1)[-1])
                self._send_json(HTTPStatus.OK, polymarket_live_validation_report_purge_payload({"key": report_key}))
                return
            if method == "POST" and path == "/api/alerts":
                alert = alert_from_payload(cfg, self.app_server.adapter_registry, payload)
                cfg.alerts.append(alert)
                self._save_config(cfg)
                self._send_json(
                    HTTPStatus.OK,
                    alerts_payload(cfg, self.app_server.adapter_registry, self.app_server.alert_price_state),
                )
                return
            if method == "POST" and path == "/api/alerts/refresh":
                result = refresh_all_alert_prices(cfg, self.app_server.adapter_registry, self.app_server.alert_price_state)
                self._save_config(cfg)
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "alerts": alerts_payload(cfg, self.app_server.adapter_registry, self.app_server.alert_price_state),
                        "message": f"Refreshed {len(result['refreshed'])} alert price source(s).",
                        **result,
                    },
                )
                return
            if method == "POST" and path.startswith("/api/alerts/") and path.endswith("/refresh"):
                alert_id = path.strip("/").split("/")[-2]
                alert = find_alert(cfg, alert_id)
                result = refresh_alert_price(
                    cfg,
                    self.app_server.adapter_registry,
                    alert,
                    self.app_server.alert_price_state,
                )
                self._save_config(cfg)
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "alerts": alerts_payload(cfg, self.app_server.adapter_registry, self.app_server.alert_price_state),
                        "message": f"Refreshed {alert.label}: {alert.source}.",
                        "refreshed": [result],
                        "problems": [],
                    },
                )
                return
            if method == "PATCH" and path.startswith("/api/alerts/"):
                alert_id = path.rsplit("/", 1)[-1]
                alert = find_alert(cfg, alert_id)
                previous_key = _alert_price_state_key(_alert_market_id(alert), alert.token_id)
                alert_from_payload(cfg, self.app_server.adapter_registry, payload, existing=alert)
                next_key = _alert_price_state_key(_alert_market_id(alert), alert.token_id)
                if next_key != previous_key:
                    self.app_server.alert_price_state.pop(previous_key, None)
                self._save_config(cfg)
                self._send_json(
                    HTTPStatus.OK,
                    alerts_payload(cfg, self.app_server.adapter_registry, self.app_server.alert_price_state),
                )
                return
            if method == "DELETE" and path.startswith("/api/alerts/"):
                alert_id = path.rsplit("/", 1)[-1]
                alert = delete_alert(cfg, alert_id)
                if not any(_alert_price_state_key(_alert_market_id(item), item.token_id) == _alert_price_state_key(_alert_market_id(alert), alert.token_id) for item in cfg.alerts):
                    self.app_server.alert_price_state.pop(_alert_price_state_key(_alert_market_id(alert), alert.token_id), None)
                self._save_config(cfg)
                self._send_json(
                    HTTPStatus.OK,
                    alerts_payload(cfg, self.app_server.adapter_registry, self.app_server.alert_price_state),
                )
                return
            if method == "POST" and path == "/api/wallets":
                add_wallet_watch(cfg, payload)
                self._save_config(cfg)
                self._send_json(
                    HTTPStatus.OK,
                    wallets_payload(cfg, self.app_server.wallet_polling, self.app_server.wallet_recent_activity),
                )
                return
            if method == "PATCH" and path == "/api/wallets/polling":
                interval = optional_positive_float(payload.get("poll_interval_seconds"), "Poll interval")
                if interval is not None:
                    self.app_server.wallet_polling["poll_interval_seconds"] = max(2.0, float(interval))
                self.app_server.wallet_polling["last_message"] = "Polling settings updated."
                self._send_json(
                    HTTPStatus.OK,
                    wallets_payload(cfg, self.app_server.wallet_polling, self.app_server.wallet_recent_activity),
                )
                return
            if method == "POST" and path == "/api/wallets/poll":
                limit = int(_safe_float(payload.get("limit"), 25) or 25)
                result = poll_wallet_activity(
                    cfg,
                    self.app_server.adapter_registry,
                    self.app_server.wallet_recent_activity,
                    limit=max(1, min(limit, 100)),
                )
                self.app_server.wallet_polling["last_polled_at"] = time.time()
                self.app_server.wallet_polling["last_message"] = (
                    f"Polled {result['polled_wallets']} wallet(s); {len(result['activity'])} new activity item(s)."
                )
                self._save_config(cfg)
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "wallets": wallets_payload(cfg, self.app_server.wallet_polling, self.app_server.wallet_recent_activity),
                        "copy": copy_payload(cfg, self.app_server.adapter_registry),
                        "message": self.app_server.wallet_polling["last_message"],
                        **result,
                    },
                )
                return
            if method == "PATCH" and path.startswith("/api/wallets/"):
                wallet_id = path.rsplit("/", 1)[-1]
                update_wallet_watch(cfg, wallet_id, payload)
                self._save_config(cfg)
                self._send_json(
                    HTTPStatus.OK,
                    wallets_payload(cfg, self.app_server.wallet_polling, self.app_server.wallet_recent_activity),
                )
                return
            if method == "DELETE" and path.startswith("/api/wallets/"):
                wallet_id = path.rsplit("/", 1)[-1]
                wallet = delete_wallet_watch(cfg, wallet_id)
                self.app_server.wallet_recent_activity = [
                    item for item in self.app_server.wallet_recent_activity if item.get("wallet_id") != wallet.id
                ]
                self._save_config(cfg)
                self._send_json(
                    HTTPStatus.OK,
                    wallets_payload(cfg, self.app_server.wallet_polling, self.app_server.wallet_recent_activity),
                )
                return
            if method == "PATCH" and path == "/api/copy":
                apply_copy_settings_patch(cfg, payload)
                self._save_config(cfg)
                self._send_json(HTTPStatus.OK, copy_payload(cfg, self.app_server.adapter_registry))
                return
            if method == "POST" and path == "/api/copy/preview":
                self._send_json(HTTPStatus.OK, copy_preview_payload(cfg, self.app_server.adapter_registry, payload))
                return
            if method == "POST" and path == "/api/live-safety/preflight":
                self._send_json(HTTPStatus.OK, live_preflight_payload(cfg, self.app_server.adapter_registry, payload))
                return
            if method == "POST" and path == "/api/paper/quote":
                self._send_json(HTTPStatus.OK, paper_quote_payload(cfg, self.app_server.adapter_registry, payload))
                return
            if method == "POST" and path == "/api/paper/quote-limit":
                self._send_json(HTTPStatus.OK, paper_quote_limit_payload(cfg, self.app_server.adapter_registry, payload))
                return
            if method == "POST" and path == "/api/paper/preview-impact":
                order = paper_order_from_payload(payload)
                impact = paper_order_impact(cfg.paper_trades, order)
                self._send_json(HTTPStatus.OK, {"impact": impact, "message": format_paper_order_impact(impact)})
                return
            if method == "POST" and path == "/api/paper/orders":
                result = submit_paper_order(cfg, self.app_server.adapter_registry, payload)
                self._save_config(cfg)
                self.app_server.paper_position_marks = _paper_marks_for_rows(
                    self.app_server.paper_position_marks,
                    paper_position_rows(cfg.paper_trades),
                )
                self._send_json(
                    HTTPStatus.OK,
                    {
                        **result,
                        "paper": paper_payload(cfg, self.app_server.paper_position_marks),
                    },
                )
                return
            if method == "POST" and path == "/api/paper/history/use":
                self._send_json(HTTPStatus.OK, history_refill_payload(cfg, str(payload.get("record_id") or "")))
                return
            if method == "POST" and path == "/api/paper/positions/use":
                self._send_json(
                    HTTPStatus.OK,
                    position_refill_payload(
                        cfg,
                        str(payload.get("market_id") or ""),
                        str(payload.get("contract_id") or ""),
                    ),
                )
                return
            if method == "POST" and path == "/api/paper/marks/refresh":
                rows = paper_position_rows(cfg.paper_trades)
                self.app_server.paper_position_marks, problems = refresh_paper_marks(
                    cfg,
                    self.app_server.adapter_registry,
                    rows,
                    self.app_server.paper_position_marks,
                )
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "paper": paper_payload(cfg, self.app_server.paper_position_marks),
                        "problems": problems,
                        "message": f"Marked {len(self.app_server.paper_position_marks)}/{len(rows)} paper positions.",
                    },
                )
                return
            if method == "POST" and path == "/api/paper/marks/refresh-selected":
                self.app_server.paper_position_marks = refresh_selected_paper_mark(
                    cfg,
                    self.app_server.adapter_registry,
                    str(payload.get("market_id") or ""),
                    str(payload.get("contract_id") or ""),
                    self.app_server.paper_position_marks,
                )
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "paper": paper_payload(cfg, self.app_server.paper_position_marks),
                        "message": "Selected paper exposure mark refreshed.",
                    },
                )
                return
            if method == "POST" and path == "/api/paper/marks/clear":
                self.app_server.paper_position_marks = {}
                self._send_json(
                    HTTPStatus.OK,
                    {"paper": paper_payload(cfg, self.app_server.paper_position_marks), "message": "Paper exposure marks cleared."},
                )
                return
            if method == "POST" and path == "/api/paper/marks/clear-selected":
                key = (str(payload.get("market_id") or "").strip().lower(), str(payload.get("contract_id") or "").strip())
                marks = _paper_marks_for_rows(self.app_server.paper_position_marks, paper_position_rows(cfg.paper_trades))
                marks.pop(key, None)
                self.app_server.paper_position_marks = marks
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "paper": paper_payload(cfg, self.app_server.paper_position_marks),
                        "message": f"Selected paper exposure mark cleared: {key[0]}:{key[1]}",
                    },
                )
                return
            if method == "POST" and path == "/api/paper/history/clear":
                cfg.paper_trades = []
                self.app_server.paper_position_marks = {}
                self._save_config(cfg)
                self._send_json(HTTPStatus.OK, paper_payload(cfg, self.app_server.paper_position_marks))
                return
        except json.JSONDecodeError:
            self._send_error(HTTPStatus.BAD_REQUEST, "invalid_json", "Invalid JSON request body.")
            return
        except UnsupportedFeatureError as exc:
            self._send_error(
                HTTPStatus.BAD_REQUEST,
                "unsupported_feature",
                str(exc),
                {"market_id": exc.market_id, "feature": exc.feature},
            )
            return
        except LiveValidationReportSchemaError as exc:
            self._send_error(
                HTTPStatus.BAD_REQUEST,
                "live_validation_report_schema_error",
                "Live validation report failed schema validation.",
                {"schema_validation": exc.validation},
            )
            return
        except ValueError as exc:
            self._send_error(HTTPStatus.BAD_REQUEST, "validation_error", str(exc))
            return
        except Exception as exc:
            print(f"[web-gui] internal error while handling {method} {path}: {type(exc).__name__}")
            self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, "internal_error", "Internal server error.")
            return

        self._send_error(HTTPStatus.NOT_FOUND, "not_found", "Unknown API route.")

    def _serve_static(self, raw_path: str) -> None:
        frontend_dir = self.app_server.frontend_dir
        index = frontend_dir / "index.html"
        if not index.exists():
            self._send_error(
                HTTPStatus.NOT_FOUND,
                "react_build_missing",
                "React build is missing.",
                {
                    "hint": "Build the React app, use the Vite dev server, or start the Tkinter fallback.",
                    "build_command": REACT_BUILD_COMMAND,
                    "dev_command": REACT_DEV_COMMAND,
                    "prod_command": REACT_PROD_COMMAND,
                    "tkinter_fallback": f"{PYTHON_GUI_SCRIPT} or {PYTHON_GUI_COMMAND}",
                },
            )
            return

        target = self._resolve_static_path(frontend_dir, raw_path)
        if target is None or not target.exists() or not target.is_file():
            target = index

        content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        data = target.read_bytes()
        self.send_response(HTTPStatus.OK)
        self._send_cors_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _resolve_static_path(self, frontend_dir: Path, raw_path: str) -> Optional[Path]:
        path = unquote(raw_path.split("?", 1)[0])
        if path in {"", "/"}:
            return frontend_dir / "index.html"
        normalized = posixpath.normpath(path.lstrip("/"))
        if normalized.startswith("../") or normalized == "..":
            return None
        return frontend_dir / normalized

    def _send_json(self, status: int, payload: Dict[str, Any]) -> None:
        data = _json_bytes(payload)
        self.send_response(status)
        self._send_cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)
        self.wfile.flush()
        self.close_connection = True

    def _send_text(self, status: int, text: str, *, content_type: str, filename: Optional[str] = None) -> None:
        data = str(text).encode("utf-8")
        self.send_response(status)
        self._send_cors_headers()
        self.send_header("Content-Type", content_type)
        if filename:
            safe_name = str(filename).replace('"', "")
            self.send_header("Content-Disposition", f'attachment; filename="{safe_name}"')
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)
        self.wfile.flush()
        self.close_connection = True

    def _send_error(
        self,
        status: int,
        code: str,
        message: str,
        details: Optional[Mapping[str, Any]] = None,
    ) -> None:
        self._send_json(status, api_error_payload(status, code, message, details))

    def _send_cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, PATCH, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")


def run_server(host: str, port: int, config_path: Path, frontend_dir: Path) -> None:
    server = ReactGuiServer((host, port), ReactGuiHandler, config_path=config_path, frontend_dir=frontend_dir)
    print(f"React GUI API listening on http://{host}:{port}")
    if (frontend_dir / "index.html").exists():
        print(f"Serving built React GUI from {frontend_dir}")
    else:
        print(f"React build not found at {frontend_dir}")
        print(f"Build it with `{REACT_BUILD_COMMAND}`, or run `{REACT_DEV_COMMAND}` for Vite.")
    print(f"Tkinter GUI is unchanged: run `{PYTHON_GUI_SCRIPT}` or `{PYTHON_GUI_COMMAND}`.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping React GUI API.")
    finally:
        server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the optional local API for the React/TypeScript GUI.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--frontend-dir", type=Path, default=DEFAULT_FRONTEND_DIR)
    args = parser.parse_args()
    run_server(args.host, args.port, args.config, args.frontend_dir)


if __name__ == "__main__":
    main()
