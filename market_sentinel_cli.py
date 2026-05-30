from __future__ import annotations

import argparse
import csv
import importlib
import importlib.metadata as importlib_metadata
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, TextIO
from urllib import error as urllib_error
from urllib import request as urllib_request

from core.storage import DEFAULT_CONFIG_PATH, load_config, save_config
from market_adapters import build_default_registry
from web_api import (
    DEFAULT_FRONTEND_DIR,
    add_wallet_watch,
    alert_from_payload,
    alerts_payload,
    app_state_payload,
    apply_config_patch,
    apply_copy_settings_patch,
    apply_market_patch,
    config_payload,
    copy_payload,
    copy_preview_payload,
    delete_alert,
    delete_wallet_watch,
    find_alert,
    health_payload,
    history_refill_payload,
    live_preflight_payload,
    live_safety_payload,
    markets_payload,
    paper_order_from_payload,
    paper_order_impact,
    paper_payload,
    paper_quote_limit_payload,
    paper_quote_payload,
    polymarket_clob_readiness_payload,
    polymarket_leaderboard_payload,
    polymarket_live_validation_payload,
    polymarket_mdd_cache_health_payload,
    polymarket_mdd_cache_payload,
    polymarket_mdd_cache_purge_payload,
    polymarket_user_mdd_payload,
    polymarket_user_search_payload,
    poll_wallet_activity,
    position_refill_payload,
    refresh_alert_price,
    refresh_all_alert_prices,
    run_server,
    submit_paper_order,
    update_wallet_watch,
    wallets_payload,
)


LEADERBOARD_FIELDS = [
    "rank",
    "display_name",
    "wallet",
    "pnl_usd",
    "volume_usd",
    "roi_pct",
    "trade_count",
    "mdd_usd",
    "mdd_pct",
    "mdd_method",
    "mdd_pct_basis",
    "mdd_source",
]

SORT_ALIASES = {
    "roi": "roi_pct",
    "roi_pct": "roi_pct",
    "pnl": "pnl_usd",
    "pnl_usd": "pnl_usd",
    "volume": "volume_usd",
    "vol": "volume_usd",
    "volume_usd": "volume_usd",
    "mdd": "mdd_pct",
    "mdd_pct": "mdd_pct",
    "mdd_usd": "mdd_usd",
}

DEPENDENCY_IMPORT_FALLBACKS = {
    "websocket-client": ("websocket",),
    "python-dotenv": ("dotenv",),
    "py-clob-client": ("py_clob_client",),
    "eth-account": ("eth_account",),
    "eth-abi": ("eth_abi",),
}


def _config_path(args: argparse.Namespace) -> Path:
    return Path(getattr(args, "config", DEFAULT_CONFIG_PATH)).expanduser()


def _load_cfg(args: argparse.Namespace):
    return load_config(_config_path(args))


def _save_cfg(args: argparse.Namespace, cfg: Any) -> None:
    save_config(cfg, _config_path(args))


def _registry():
    return build_default_registry()


def _coerce_value(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    text = value.strip()
    lowered = text.lower()
    if lowered in {"true", "yes", "on"}:
        return True
    if lowered in {"false", "no", "off"}:
        return False
    if lowered in {"none", "null"}:
        return None
    if not text:
        return ""
    try:
        return json.loads(text)
    except Exception:
        return text


def _json_arg(value: Optional[str]) -> Dict[str, Any]:
    if not value:
        return {}
    raw = value
    if raw.startswith("@"):
        raw = Path(raw[1:]).expanduser().read_text(encoding="utf-8")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise argparse.ArgumentTypeError("JSON payload must be an object.")
    return data


def _merge_kv(payload: Dict[str, Any], values: Optional[Sequence[tuple[str, str]]]) -> Dict[str, Any]:
    for key, value in values or []:
        payload[key] = _coerce_value(value)
    return payload


def _put_optional(payload: Dict[str, Any], key: str, value: Any) -> None:
    if value is not None:
        payload[key] = value


def _add_json_output_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--output", "-o", default="-", help="Output path, or - for stdout.")
    parser.add_argument("--compact", action="store_true", help="Write compact JSON instead of indented JSON.")


def _write_json(payload: Mapping[str, Any], *, output: Optional[str] = "-", compact: bool = False) -> None:
    stream, should_close = _open_output(output)
    try:
        if compact:
            json.dump(payload, stream, separators=(",", ":"), sort_keys=True)
        else:
            json.dump(payload, stream, indent=2, sort_keys=True)
        stream.write("\n")
    finally:
        if should_close:
            stream.close()


def _write_command_payload(args: argparse.Namespace, payload: Mapping[str, Any]) -> int:
    _write_json(payload, output=getattr(args, "output", "-"), compact=bool(getattr(args, "compact", False)))
    return 0


def _add_param(params: Dict[str, List[str]], key: str, value: Any) -> None:
    if value is None:
        return
    if isinstance(value, bool):
        params[key] = ["true" if value else "false"]
        return
    text = str(value).strip()
    if text:
        params[key] = [text]


def _split_key_value(raw: str) -> tuple[str, str]:
    if "=" not in raw:
        raise argparse.ArgumentTypeError("--param values must use KEY=VALUE format.")
    key, value = raw.split("=", 1)
    key = key.strip()
    if not key:
        raise argparse.ArgumentTypeError("--param key cannot be empty.")
    return key, value.strip()


def build_polymarket_leaderboard_params(args: argparse.Namespace) -> Dict[str, List[str]]:
    params: Dict[str, List[str]] = {}
    sort = SORT_ALIASES.get(str(args.sort or "roi_pct").strip().lower(), "roi_pct")
    _add_param(params, "sort", sort)
    _add_param(params, "direction", args.direction)
    _add_param(params, "period", args.period)
    _add_param(params, "category", args.category)
    _add_param(params, "limit", args.returned)
    _add_param(params, "scan_limit", args.scanned)
    _add_param(params, "compute_mdd", args.compute_mdd)
    _add_param(params, "fast_scan", args.fast_scan)
    _add_param(params, "mdd_mode", args.mdd_mode)
    _add_param(params, "mdd_scan_limit", args.mdd_scan)
    _add_param(params, "mdd_history_limit", args.mdd_history_limit)
    _add_param(params, "mdd_activity_limit", args.mdd_activity_limit)
    _add_param(params, "mdd_trade_limit", args.mdd_trade_limit)
    _add_param(params, "mdd_open_limit", args.mdd_open_limit)
    _add_param(params, "mdd_mark_replay_token_limit", args.mdd_mark_replay_token_limit)
    _add_param(params, "mdd_mark_replay_point_limit", args.mdd_mark_replay_point_limit)
    _add_param(params, "mdd_mark_replay_interval", args.mdd_mark_replay_interval)
    _add_param(params, "mdd_mark_replay_fidelity", args.mdd_mark_replay_fidelity)
    _add_param(params, "mdd_include_accounting", args.mdd_include_accounting)
    _add_param(params, "mdd_accounting_timeout", args.mdd_accounting_timeout)
    _add_param(params, "mdd_persist_cache", args.mdd_persist_cache)
    _add_param(params, "mdd_cache_ttl_seconds", args.mdd_cache_ttl_seconds)
    _add_param(params, "equity_base_usd", args.equity_base_usd)
    _add_param(params, "scan_concurrency", args.scan_concurrency)
    _add_param(params, "mdd_concurrency", args.mdd_concurrency)
    _add_param(params, "mdd_stop_on_limit", args.mdd_stop_on_limit)

    for key in (
        "min_pnl_usd",
        "max_pnl_usd",
        "min_volume_usd",
        "max_volume_usd",
        "min_roi_pct",
        "max_roi_pct",
        "min_mdd_usd",
        "max_mdd_usd",
        "min_mdd_pct",
        "max_mdd_pct",
    ):
        _add_param(params, key, getattr(args, key))

    for key, value in args.param or []:
        _add_param(params, key, value)

    return params


def _row_mdd_source(row: Mapping[str, Any]) -> str:
    return str(
        row.get("mdd_accounting_status")
        or row.get("mdd_mark_replay_status")
        or row.get("mdd_method")
        or ""
    )


def _csv_rows(rows: Iterable[Mapping[str, Any]]) -> Iterable[Dict[str, Any]]:
    for row in rows:
        item = {field: row.get(field, "") for field in LEADERBOARD_FIELDS}
        item["mdd_source"] = _row_mdd_source(row)
        yield item


def _open_output(path: Optional[str]) -> tuple[TextIO, bool]:
    if not path or path == "-":
        return sys.stdout, False
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    return output.open("w", encoding="utf-8", newline=""), True


def write_leaderboard_payload(payload: Mapping[str, Any], *, output_format: str, output: Optional[str]) -> None:
    stream, should_close = _open_output(output)
    try:
        if output_format == "json":
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.write("\n")
            return

        writer = csv.DictWriter(stream, fieldnames=LEADERBOARD_FIELDS)
        writer.writeheader()
        writer.writerows(_csv_rows(payload.get("rows") or []))
    finally:
        if should_close:
            stream.close()


def _progress_printer(enabled: bool):
    if not enabled:
        return None

    def emit(progress: Dict[str, Any]) -> None:
        phase = str(progress.get("phase") or "scan")
        scanned = progress.get("scanned", 0)
        scan_limit = "unlimited" if progress.get("scan_limit_unlimited") else progress.get("scan_limit", 0)
        mdd_attempted = progress.get("mdd_attempted", 0)
        mdd_total = progress.get("mdd_total", 0)
        message = str(progress.get("message") or "").strip()
        if not message:
            message = f"{phase}: scanned {scanned}/{scan_limit}; mdd {mdd_attempted}/{mdd_total}"
        print(message, file=sys.stderr, flush=True)

    return emit


def run_polymarket_leaderboard(args: argparse.Namespace) -> int:
    params = build_polymarket_leaderboard_params(args)
    payload = polymarket_leaderboard_payload(params, progress_callback=_progress_printer(not args.quiet))
    write_leaderboard_payload(payload, output_format=args.format, output=args.output)

    counts = payload.get("counts") or {}
    warning_count = len(payload.get("warnings") or [])
    if not args.quiet:
        print(
            "Done: returned={returned} filtered={filtered} scanned={scanned} mdd_computed={mdd_computed} warnings={warnings}".format(
                returned=counts.get("returned", 0),
                filtered=counts.get("filtered", 0),
                scanned=counts.get("scanned", 0),
                mdd_computed=counts.get("mdd_computed", 0),
                warnings=warning_count,
            ),
            file=sys.stderr,
        )
    return 0


def run_health(args: argparse.Namespace) -> int:
    return _write_command_payload(args, health_payload(_config_path(args), Path(args.frontend_dir).expanduser()))


def run_state(args: argparse.Namespace) -> int:
    cfg = _load_cfg(args)
    payload = app_state_payload(
        cfg,
        config_path=_config_path(args),
        frontend_dir=Path(args.frontend_dir).expanduser(),
        registry=_registry(),
    )
    return _write_command_payload(args, payload)


def run_config_show(args: argparse.Namespace) -> int:
    return _write_command_payload(args, config_payload(_load_cfg(args)))


def run_config_set(args: argparse.Namespace) -> int:
    cfg = _load_cfg(args)
    payload = _json_arg(args.json)
    _put_optional(payload, "selected_market_id", args.market)
    _put_optional(payload, "theme", args.theme)
    _put_optional(payload, "ui_design", args.design)
    apply_config_patch(cfg, payload)
    _save_cfg(args, cfg)
    return _write_command_payload(args, config_payload(cfg))


def run_markets_list(args: argparse.Namespace) -> int:
    return _write_command_payload(args, markets_payload(_load_cfg(args), _registry()))


def run_market_set(args: argparse.Namespace) -> int:
    cfg = _load_cfg(args)
    payload = _json_arg(args.json)
    _put_optional(payload, "enabled", args.enabled)
    _put_optional(payload, "live_trading_enabled", args.live_trading_enabled)
    _put_optional(payload, "live_trading_confirmed", args.live_trading_confirmed)
    _put_optional(payload, "live_trading_kill_switch", args.live_trading_kill_switch)
    _put_optional(payload, "live_trading_max_size", args.live_trading_max_size)
    _put_optional(payload, "live_trading_max_notional", args.live_trading_max_notional)
    if args.setting:
        settings = dict(payload.get("settings") or {})
        _merge_kv(settings, args.setting)
        payload["settings"] = settings
    apply_market_patch(cfg, args.market_id, payload)
    _save_cfg(args, cfg)
    return _write_command_payload(args, markets_payload(cfg, _registry()))


def run_live_safety_show(args: argparse.Namespace) -> int:
    return _write_command_payload(args, live_safety_payload(_load_cfg(args), _registry(), args.market))


def _order_payload(args: argparse.Namespace) -> Dict[str, Any]:
    payload = _json_arg(getattr(args, "json", None))
    market_id = getattr(args, "market", None)
    if not market_id:
        market_id = _load_cfg(args).selected_market_id
    _put_optional(payload, "market_id", market_id)
    _put_optional(payload, "contract_id", getattr(args, "contract", None))
    _put_optional(payload, "side", getattr(args, "side", None))
    _put_optional(payload, "size", getattr(args, "size", None))
    _put_optional(payload, "limit_price", getattr(args, "limit_price", None))
    if getattr(args, "metadata", None):
        metadata = dict(payload.get("metadata") or {})
        _merge_kv(metadata, args.metadata)
        payload["metadata"] = metadata
    return payload


def run_live_safety_preflight(args: argparse.Namespace) -> int:
    cfg = _load_cfg(args)
    return _write_command_payload(args, live_preflight_payload(cfg, _registry(), _order_payload(args)))


def _alert_payload_from_args(args: argparse.Namespace, *, default_market: bool = False) -> Dict[str, Any]:
    payload = _json_arg(getattr(args, "json", None))
    market = args.market
    if not market and default_market:
        market = _load_cfg(args).selected_market_id
    _put_optional(payload, "market_id", market)
    _put_optional(payload, "contract_id", args.contract)
    _put_optional(payload, "label", args.label)
    _put_optional(payload, "direction", args.direction)
    _put_optional(payload, "threshold", args.threshold)
    _put_optional(payload, "source", args.source)
    _put_optional(payload, "once", args.once)
    _put_optional(payload, "enabled", args.enabled)
    return payload


def run_alerts_list(args: argparse.Namespace) -> int:
    return _write_command_payload(args, alerts_payload(_load_cfg(args), _registry()))


def run_alert_add(args: argparse.Namespace) -> int:
    cfg = _load_cfg(args)
    registry = _registry()
    alert = alert_from_payload(cfg, registry, _alert_payload_from_args(args, default_market=True))
    cfg.alerts.append(alert)
    _save_cfg(args, cfg)
    return _write_command_payload(args, alerts_payload(cfg, registry))


def run_alert_update(args: argparse.Namespace) -> int:
    cfg = _load_cfg(args)
    registry = _registry()
    alert = find_alert(cfg, args.alert_id)
    alert_from_payload(cfg, registry, _alert_payload_from_args(args), existing=alert)
    _save_cfg(args, cfg)
    return _write_command_payload(args, alerts_payload(cfg, registry))


def run_alert_delete(args: argparse.Namespace) -> int:
    cfg = _load_cfg(args)
    deleted = delete_alert(cfg, args.alert_id)
    _save_cfg(args, cfg)
    return _write_command_payload(args, {"deleted": deleted.to_dict(), **alerts_payload(cfg, _registry())})


def run_alert_refresh(args: argparse.Namespace) -> int:
    cfg = _load_cfg(args)
    registry = _registry()
    price_state: Dict[Any, Dict[str, Any]] = {}
    if args.alert_id:
        result = refresh_alert_price(cfg, registry, find_alert(cfg, args.alert_id), price_state)
        payload = {"refreshed": [result], "problems": [], "alerts": alerts_payload(cfg, registry, price_state)}
    else:
        result = refresh_all_alert_prices(cfg, registry, price_state)
        payload = {**result, "alerts": alerts_payload(cfg, registry, price_state)}
    _save_cfg(args, cfg)
    return _write_command_payload(args, payload)


def run_wallets_list(args: argparse.Namespace) -> int:
    return _write_command_payload(args, wallets_payload(_load_cfg(args)))


def _wallet_payload_from_args(args: argparse.Namespace) -> Dict[str, Any]:
    payload = _json_arg(getattr(args, "json", None))
    _put_optional(payload, "wallet", getattr(args, "wallet", None))
    _put_optional(payload, "display_name", getattr(args, "display_name", None))
    _put_optional(payload, "enabled", getattr(args, "enabled", None))
    _put_optional(payload, "only_market_slug", getattr(args, "only_market_slug", None))
    return payload


def run_wallet_add(args: argparse.Namespace) -> int:
    cfg = _load_cfg(args)
    add_wallet_watch(cfg, _wallet_payload_from_args(args))
    _save_cfg(args, cfg)
    return _write_command_payload(args, wallets_payload(cfg))


def run_wallet_update(args: argparse.Namespace) -> int:
    cfg = _load_cfg(args)
    update_wallet_watch(cfg, args.wallet_id, _wallet_payload_from_args(args))
    _save_cfg(args, cfg)
    return _write_command_payload(args, wallets_payload(cfg))


def run_wallet_delete(args: argparse.Namespace) -> int:
    cfg = _load_cfg(args)
    deleted = delete_wallet_watch(cfg, args.wallet_id)
    _save_cfg(args, cfg)
    return _write_command_payload(args, {"deleted": deleted.to_dict(), **wallets_payload(cfg)})


def run_wallet_poll(args: argparse.Namespace) -> int:
    cfg = _load_cfg(args)
    recent_activity: List[Dict[str, Any]] = []
    result = poll_wallet_activity(cfg, _registry(), recent_activity, limit=max(1, min(int(args.limit), 100)))
    _save_cfg(args, cfg)
    payload = {
        **result,
        "wallets": wallets_payload(
            cfg,
            {
                "poll_interval_seconds": 10.0,
                "last_polled_at": time.time(),
                "last_message": f"Polled {result['polled_wallets']} wallet(s); {len(result['activity'])} new activity item(s).",
            },
            recent_activity,
        ),
        "copy": copy_payload(cfg, _registry()),
    }
    return _write_command_payload(args, payload)


def _wallet_poll_once(args: argparse.Namespace, recent_activity: List[Dict[str, Any]]) -> Dict[str, Any]:
    cfg = _load_cfg(args)
    registry = _registry()
    result = poll_wallet_activity(cfg, registry, recent_activity, limit=max(1, min(int(args.limit), 100)))
    _save_cfg(args, cfg)
    return {
        **result,
        "wallets": wallets_payload(
            cfg,
            {
                "poll_interval_seconds": float(args.interval),
                "last_polled_at": time.time(),
                "last_message": f"Polled {result['polled_wallets']} wallet(s); {len(result['activity'])} new activity item(s).",
            },
            recent_activity,
        ),
        "copy": copy_payload(cfg, registry),
    }


def run_wallet_watch(args: argparse.Namespace) -> int:
    recent_activity: List[Dict[str, Any]] = []
    stream, should_close = _open_output(args.output)
    iterations = None if args.iterations in (None, "") else max(1, int(args.iterations))
    interval = max(1.0, float(args.interval))
    completed = 0
    try:
        while iterations is None or completed < iterations:
            payload = _wallet_poll_once(args, recent_activity)
            if args.compact:
                json.dump(payload, stream, separators=(",", ":"), sort_keys=True)
            else:
                json.dump(payload, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            completed += 1
            if iterations is not None and completed >= iterations:
                break
            time.sleep(interval)
    except KeyboardInterrupt:
        return 130
    finally:
        if should_close:
            stream.close()
    return 0


def run_copy_show(args: argparse.Namespace) -> int:
    return _write_command_payload(args, copy_payload(_load_cfg(args), _registry()))


def run_copy_set(args: argparse.Namespace) -> int:
    cfg = _load_cfg(args)
    payload = _json_arg(args.json)
    _put_optional(payload, "enabled", args.enabled)
    _put_optional(payload, "live", args.live)
    _put_optional(payload, "follow_wallet", args.follow_wallet)
    _put_optional(payload, "follow_wallets", args.follow_wallets)
    _put_optional(payload, "copy_percentage", args.copy_percentage)
    _put_optional(payload, "scale", args.scale)
    _put_optional(payload, "max_usdc_per_trade", args.max_usdc_per_trade)
    _put_optional(payload, "slippage", args.slippage)
    _put_optional(payload, "allow_sells", args.allow_sells)
    _put_optional(payload, "conflict_guard", args.conflict_guard)
    _put_optional(payload, "conflict_window_seconds", args.conflict_window_seconds)
    apply_copy_settings_patch(cfg, payload)
    _save_cfg(args, cfg)
    return _write_command_payload(args, copy_payload(cfg, _registry()))


def run_copy_preview(args: argparse.Namespace) -> int:
    cfg = _load_cfg(args)
    payload = _json_arg(args.json)
    _put_optional(payload, "proxyWallet", args.proxy_wallet)
    _put_optional(payload, "asset", args.asset or args.token_id)
    _put_optional(payload, "side", args.side)
    _put_optional(payload, "size", args.size)
    _put_optional(payload, "price", args.price)
    _put_optional(payload, "slug", args.slug)
    _put_optional(payload, "outcome", args.outcome)
    return _write_command_payload(args, copy_preview_payload(cfg, _registry(), payload))


def run_paper_show(args: argparse.Namespace) -> int:
    return _write_command_payload(args, paper_payload(_load_cfg(args)))


def run_paper_quote(args: argparse.Namespace) -> int:
    cfg = _load_cfg(args)
    return _write_command_payload(args, paper_quote_payload(cfg, _registry(), _order_payload(args)))


def run_paper_quote_limit(args: argparse.Namespace) -> int:
    cfg = _load_cfg(args)
    return _write_command_payload(args, paper_quote_limit_payload(cfg, _registry(), _order_payload(args)))


def run_paper_impact(args: argparse.Namespace) -> int:
    cfg = _load_cfg(args)
    order = paper_order_from_payload(_order_payload(args))
    impact = paper_order_impact(cfg.paper_trades, order)
    return _write_command_payload(args, {"impact": impact})


def run_paper_order(args: argparse.Namespace) -> int:
    cfg = _load_cfg(args)
    result = submit_paper_order(cfg, _registry(), _order_payload(args))
    _save_cfg(args, cfg)
    return _write_command_payload(args, {**result, "paper": paper_payload(cfg)})


def run_paper_use_history(args: argparse.Namespace) -> int:
    return _write_command_payload(args, history_refill_payload(_load_cfg(args), args.record_id))


def run_paper_use_position(args: argparse.Namespace) -> int:
    return _write_command_payload(args, position_refill_payload(_load_cfg(args), args.market, args.contract))


def run_paper_clear_history(args: argparse.Namespace) -> int:
    cfg = _load_cfg(args)
    cfg.paper_trades = []
    _save_cfg(args, cfg)
    return _write_command_payload(args, paper_payload(cfg))


def _project_root() -> Path:
    return Path(__file__).resolve().parent


def _parse_requirement_entry(raw: str) -> Optional[Dict[str, str]]:
    line = raw.strip()
    if not line or line.startswith("#"):
        return None
    if "#" in line:
        line = line.split("#", 1)[0].strip()
    if not line:
        return None
    try:
        from packaging.requirements import Requirement

        requirement = Requirement(line)
        if requirement.marker is not None and not requirement.marker.evaluate():
            return None
        extras = f"[{','.join(sorted(requirement.extras))}]" if requirement.extras else ""
        return {"name": requirement.name, "display": f"{requirement.name}{extras}", "spec": str(requirement.specifier)}
    except Exception:
        if ";" in line:
            line = line.split(";", 1)[0].strip()
    name = line
    spec = ""
    for marker in ("==", ">=", "<=", "~=", "!=", ">", "<"):
        if marker in line:
            name, spec = line.split(marker, 1)
            spec = marker + spec
            break
    return {"name": name.strip(), "display": name.strip(), "spec": spec.strip()} if name.strip() else None


def _load_requirements() -> List[Dict[str, str]]:
    root = _project_root()
    requirements = root / "requirements.txt"
    raw_entries: List[str] = []
    if requirements.exists():
        raw_entries = requirements.read_text(encoding="utf-8").splitlines()
    else:
        pyproject = root / "pyproject.toml"
        if pyproject.exists():
            try:
                try:
                    import tomllib
                except ModuleNotFoundError:
                    import tomli as tomllib  # type: ignore
                data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
                raw_entries = [str(item) for item in data.get("project", {}).get("dependencies", [])]
            except Exception:
                raw_entries = []
    return [parsed for raw in raw_entries if (parsed := _parse_requirement_entry(raw))]


def _installed_version(package: str) -> str:
    try:
        return importlib_metadata.version(package)
    except importlib_metadata.PackageNotFoundError:
        pass
    for module_name in DEPENDENCY_IMPORT_FALLBACKS.get(package, (package.replace("-", "_"),)):
        try:
            module = importlib.import_module(module_name)
        except Exception:
            continue
        version = str(getattr(module, "__version__", "") or getattr(module, "version", "") or "").strip()
        return version or "installed"
    return ""


def _fetch_latest_version(package: str) -> str:
    request = urllib_request.Request(
        f"https://pypi.org/pypi/{package}/json",
        headers={"User-Agent": "MarketSentinel CLI"},
    )
    try:
        with urllib_request.urlopen(request, timeout=10) as response:
            if response.status != 200:
                return ""
            data = json.loads(response.read().decode("utf-8"))
            return str(data.get("info", {}).get("version") or "").strip()
    except (urllib_error.URLError, urllib_error.HTTPError, TimeoutError):
        return ""


def _is_up_to_date(installed: str, latest: str) -> bool:
    try:
        from packaging.version import Version

        return Version(installed) >= Version(latest)
    except Exception:
        return installed == latest


def _dependency_rows(*, latest: bool = False) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    for req in _load_requirements():
        installed = _installed_version(req["name"])
        latest_version = _fetch_latest_version(req["name"]) if latest else ""
        if not installed:
            status = "missing"
        elif installed == "installed":
            status = "installed"
        elif latest_version:
            status = "ok" if _is_up_to_date(installed, latest_version) else "outdated"
        else:
            status = "ok"
        rows.append(
            {
                "package": req["display"],
                "required": req["spec"],
                "installed": installed or "not installed",
                "latest": latest_version or "-",
                "status": status,
            }
        )
    return rows


def run_dependencies(args: argparse.Namespace) -> int:
    rows = _dependency_rows(latest=bool(args.latest))
    return _write_command_payload(
        args,
        {
            "checked_latest": bool(args.latest),
            "dependencies": rows,
            "counts": {
                "total": len(rows),
                "missing": sum(1 for row in rows if row["status"] == "missing"),
                "outdated": sum(1 for row in rows if row["status"] == "outdated"),
            },
        },
    )


def run_polymarket_user_search(args: argparse.Namespace) -> int:
    return _write_command_payload(args, polymarket_user_search_payload(args.query, limit=int(args.limit)))


def run_polymarket_user_mdd(args: argparse.Namespace) -> int:
    payload = polymarket_user_mdd_payload(
        args.wallet,
        mode=args.mode,
        closed_limit=int(args.closed_limit),
        open_limit=int(args.open_limit),
        activity_limit=int(args.activity_limit),
        trade_limit=int(args.trade_limit),
        include_open=bool(args.include_open),
        equity_base_usd=None if args.equity_base_usd in (None, "") else float(args.equity_base_usd),
        max_points=int(args.max_points),
        cache_ttl_seconds=int(args.cache_ttl_seconds),
        mark_replay_token_limit=int(args.mark_replay_token_limit),
        mark_replay_point_limit=int(args.mark_replay_point_limit),
        mark_replay_interval=args.mark_replay_interval,
        mark_replay_fidelity=int(args.mark_replay_fidelity),
        include_accounting_snapshot=bool(args.include_accounting),
        accounting_timeout=float(args.accounting_timeout),
    )
    return _write_command_payload(args, payload)


def run_polymarket_readiness(args: argparse.Namespace) -> int:
    cfg = _load_cfg(args)
    payload = {
        "clob_readiness": polymarket_clob_readiness_payload(cfg),
        "live_validation": polymarket_live_validation_payload(cfg),
    }
    return _write_command_payload(args, payload)


def run_polymarket_mdd_cache_list(args: argparse.Namespace) -> int:
    return _write_command_payload(args, polymarket_mdd_cache_payload(include_expired=bool(args.include_expired)))


def run_polymarket_mdd_cache_health(args: argparse.Namespace) -> int:
    return _write_command_payload(args, polymarket_mdd_cache_health_payload())


def run_polymarket_mdd_cache_purge(args: argparse.Namespace) -> int:
    payload: Dict[str, Any] = {"key": args.key or "", "expired_only": args.expired_only, "all": args.all}
    return _write_command_payload(args, polymarket_mdd_cache_purge_payload(payload))


def run_serve(args: argparse.Namespace) -> int:
    run_server(args.host, int(args.port), _config_path(args), Path(args.frontend_dir).expanduser())
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="market-sentinel", description="MarketSentinel headless utilities.")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help="Config JSON path. Defaults to data/config.json or PREDICTION_MARKET_CONFIG_PATH.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    leaderboard = subparsers.add_parser(
        "polymarket-leaderboard",
        aliases=["leaderboard", "polymarket-analytics"],
        parents=[common],
        help="Run the Polymarket ROI/PnL/volume/MDD leaderboard scan without a GUI.",
    )
    leaderboard.add_argument("--sort", default="roi_pct", help="roi_pct, pnl_usd, volume_usd, mdd_pct, or mdd_usd.")
    leaderboard.add_argument("--direction", default="DESC", choices=["ASC", "DESC"])
    leaderboard.add_argument("--period", default="all")
    leaderboard.add_argument("--category", default="OVERALL")
    leaderboard.add_argument("--returned", "--limit", default="100", help="Rows to return; use unlimited, all, 0, or -1 for no local cap.")
    leaderboard.add_argument("--scanned", "--scan-limit", default="500", help="Rows to scan; use unlimited, all, 0, or -1 to scan until the API is exhausted.")
    leaderboard.add_argument("--compute-mdd", action="store_true")
    leaderboard.add_argument("--fast-scan", action="store_true")
    leaderboard.add_argument("--mdd-mode", default="fast", choices=["fast", "mark_replay"])
    leaderboard.add_argument("--mdd-scan", "--mdd-scan-limit", default="100", help="Candidate rows to compute MDD for; use unlimited, all, 0, or -1 for all candidates.")
    leaderboard.add_argument("--mdd-history-limit", default="500")
    leaderboard.add_argument("--mdd-activity-limit", default="1000")
    leaderboard.add_argument("--mdd-trade-limit", default="1000")
    leaderboard.add_argument("--mdd-open-limit", default="500")
    leaderboard.add_argument("--mdd-mark-replay-token-limit", default="10")
    leaderboard.add_argument("--mdd-mark-replay-point-limit", default="5000")
    leaderboard.add_argument("--mdd-mark-replay-interval", default="1h")
    leaderboard.add_argument("--mdd-mark-replay-fidelity", default="60")
    leaderboard.add_argument("--mdd-include-accounting", action="store_true")
    leaderboard.add_argument("--mdd-accounting-timeout", default="30")
    leaderboard.add_argument("--mdd-persist-cache", action="store_true")
    leaderboard.add_argument("--mdd-cache-ttl-seconds", default="60")
    leaderboard.add_argument("--equity-base-usd", default="")
    leaderboard.add_argument("--scan-concurrency", default="")
    leaderboard.add_argument("--mdd-concurrency", default="")
    leaderboard.add_argument("--mdd-stop-on-limit", action="store_true", default=None)
    leaderboard.add_argument("--min-pnl-usd", default="")
    leaderboard.add_argument("--max-pnl-usd", default="")
    leaderboard.add_argument("--min-volume-usd", default="")
    leaderboard.add_argument("--max-volume-usd", default="")
    leaderboard.add_argument("--min-roi-pct", default="")
    leaderboard.add_argument("--max-roi-pct", default="")
    leaderboard.add_argument("--min-mdd-usd", default="")
    leaderboard.add_argument("--max-mdd-usd", default="")
    leaderboard.add_argument("--min-mdd-pct", default="")
    leaderboard.add_argument("--max-mdd-pct", default="")
    leaderboard.add_argument("--param", action="append", type=_split_key_value, default=[], help="Raw API query override in KEY=VALUE form. Can be passed more than once.")
    leaderboard.add_argument("--format", choices=["csv", "json"], default="csv")
    leaderboard.add_argument("--output", "-o", default="-", help="Output file path, or - for stdout.")
    leaderboard.add_argument("--quiet", action="store_true", help="Suppress progress and summary messages on stderr.")
    leaderboard.set_defaults(func=run_polymarket_leaderboard)

    health = subparsers.add_parser("health", parents=[common], help="Print API/app health and route metadata.")
    health.add_argument("--frontend-dir", type=Path, default=DEFAULT_FRONTEND_DIR)
    _add_json_output_args(health)
    health.set_defaults(func=run_health)

    state = subparsers.add_parser("state", parents=[common], help="Print the full headless app state.")
    state.add_argument("--frontend-dir", type=Path, default=DEFAULT_FRONTEND_DIR)
    _add_json_output_args(state)
    state.set_defaults(func=run_state)

    config = subparsers.add_parser("config", parents=[common], help="Show or update global app config.")
    config_sub = config.add_subparsers(dest="config_command", required=True)
    config_show = config_sub.add_parser("show", parents=[common], help="Show selected market, theme, design, wallets, and copy settings.")
    _add_json_output_args(config_show)
    config_show.set_defaults(func=run_config_show)
    config_set = config_sub.add_parser("set", parents=[common], help="Update selected market, theme, or design.")
    config_set.add_argument("--market", dest="market", default=None)
    config_set.add_argument("--theme", choices=["light", "dark"], default=None)
    config_set.add_argument("--design", choices=["classic", "aurora_2026", "graphite_2026", "sentinel_2027"], default=None)
    config_set.add_argument("--json", default=None, help="Inline JSON object or @file to merge before explicit flags.")
    _add_json_output_args(config_set)
    config_set.set_defaults(func=run_config_set)

    markets = subparsers.add_parser("markets", parents=[common], help="List or update market enablement and safety settings.")
    markets_sub = markets.add_subparsers(dest="markets_command", required=True)
    markets_list = markets_sub.add_parser("list", parents=[common], help="List configured markets and capabilities.")
    _add_json_output_args(markets_list)
    markets_list.set_defaults(func=run_markets_list)
    market_set = markets_sub.add_parser("set", parents=[common], help="Patch one market config.")
    market_set.add_argument("market_id")
    market_set.add_argument("--enabled", action=argparse.BooleanOptionalAction, default=None)
    market_set.add_argument("--live-trading-enabled", action=argparse.BooleanOptionalAction, default=None)
    market_set.add_argument("--live-trading-confirmed", action=argparse.BooleanOptionalAction, default=None)
    market_set.add_argument("--live-trading-kill-switch", action=argparse.BooleanOptionalAction, default=None)
    market_set.add_argument("--live-trading-max-size", default=None)
    market_set.add_argument("--live-trading-max-notional", default=None)
    market_set.add_argument("--setting", action="append", type=_split_key_value, default=[], help="Raw market setting KEY=VALUE.")
    market_set.add_argument("--json", default=None, help="Inline JSON object or @file to merge before explicit flags.")
    _add_json_output_args(market_set)
    market_set.set_defaults(func=run_market_set)

    live = subparsers.add_parser("live-safety", parents=[common], help="Inspect live safety gates or run a no-order preflight.")
    live_sub = live.add_subparsers(dest="live_command", required=True)
    live_show = live_sub.add_parser("show", parents=[common], help="Show live safety gates.")
    live_show.add_argument("--market", default=None)
    _add_json_output_args(live_show)
    live_show.set_defaults(func=run_live_safety_show)
    live_preflight = live_sub.add_parser("preflight", parents=[common], help="Validate a live order without placing it.")
    live_preflight.add_argument("--market", default=None)
    live_preflight.add_argument("--contract", required=True)
    live_preflight.add_argument("--side", required=True, choices=["BUY", "SELL", "BACK", "LAY"])
    live_preflight.add_argument("--size", required=True)
    live_preflight.add_argument("--limit-price", default=None)
    live_preflight.add_argument("--metadata", action="append", type=_split_key_value, default=[])
    live_preflight.add_argument("--json", default=None)
    _add_json_output_args(live_preflight)
    live_preflight.set_defaults(func=run_live_safety_preflight)

    alerts = subparsers.add_parser("alerts", parents=[common], help="Manage price alerts.")
    alerts_sub = alerts.add_subparsers(dest="alerts_command", required=True)
    alerts_list = alerts_sub.add_parser("list", parents=[common], help="List alerts.")
    _add_json_output_args(alerts_list)
    alerts_list.set_defaults(func=run_alerts_list)
    alert_add = alerts_sub.add_parser("add", parents=[common], help="Add a price alert.")
    alert_add.add_argument("--market", default=None)
    alert_add.add_argument("--contract", required=True)
    alert_add.add_argument("--label", default=None)
    alert_add.add_argument("--direction", choices=["above", "below"], required=True)
    alert_add.add_argument("--threshold", required=True)
    alert_add.add_argument("--source", choices=["last_trade", "midpoint", "best_bid", "best_ask"], default="last_trade")
    alert_add.add_argument("--once", action=argparse.BooleanOptionalAction, default=None)
    alert_add.add_argument("--enabled", action=argparse.BooleanOptionalAction, default=None)
    alert_add.add_argument("--json", default=None)
    _add_json_output_args(alert_add)
    alert_add.set_defaults(func=run_alert_add)
    alert_update = alerts_sub.add_parser("update", parents=[common], help="Update an alert.")
    alert_update.add_argument("alert_id")
    alert_update.add_argument("--market", default=None)
    alert_update.add_argument("--contract", default=None)
    alert_update.add_argument("--label", default=None)
    alert_update.add_argument("--direction", choices=["above", "below"], default=None)
    alert_update.add_argument("--threshold", default=None)
    alert_update.add_argument("--source", choices=["last_trade", "midpoint", "best_bid", "best_ask"], default=None)
    alert_update.add_argument("--once", action=argparse.BooleanOptionalAction, default=None)
    alert_update.add_argument("--enabled", action=argparse.BooleanOptionalAction, default=None)
    alert_update.add_argument("--json", default=None)
    _add_json_output_args(alert_update)
    alert_update.set_defaults(func=run_alert_update)
    alert_delete = alerts_sub.add_parser("delete", parents=[common], help="Delete an alert.")
    alert_delete.add_argument("alert_id")
    _add_json_output_args(alert_delete)
    alert_delete.set_defaults(func=run_alert_delete)
    alert_refresh = alerts_sub.add_parser("refresh", parents=[common], help="Refresh all alerts or one alert id.")
    alert_refresh.add_argument("alert_id", nargs="?")
    _add_json_output_args(alert_refresh)
    alert_refresh.set_defaults(func=run_alert_refresh)

    wallets = subparsers.add_parser("wallets", parents=[common], help="Manage Polymarket wallet tracking.")
    wallets_sub = wallets.add_subparsers(dest="wallets_command", required=True)
    wallets_list = wallets_sub.add_parser("list", parents=[common], help="List tracked wallets.")
    _add_json_output_args(wallets_list)
    wallets_list.set_defaults(func=run_wallets_list)
    wallet_add = wallets_sub.add_parser("add", parents=[common], help="Track a wallet.")
    wallet_add.add_argument("--wallet", required=True)
    wallet_add.add_argument("--display-name", default=None)
    wallet_add.add_argument("--enabled", action=argparse.BooleanOptionalAction, default=None)
    wallet_add.add_argument("--only-market-slug", default=None)
    wallet_add.add_argument("--json", default=None)
    _add_json_output_args(wallet_add)
    wallet_add.set_defaults(func=run_wallet_add)
    wallet_update = wallets_sub.add_parser("update", parents=[common], help="Update a tracked wallet.")
    wallet_update.add_argument("wallet_id")
    wallet_update.add_argument("--wallet", default=None)
    wallet_update.add_argument("--display-name", default=None)
    wallet_update.add_argument("--enabled", action=argparse.BooleanOptionalAction, default=None)
    wallet_update.add_argument("--only-market-slug", default=None)
    wallet_update.add_argument("--json", default=None)
    _add_json_output_args(wallet_update)
    wallet_update.set_defaults(func=run_wallet_update)
    wallet_delete = wallets_sub.add_parser("delete", parents=[common], help="Delete a tracked wallet.")
    wallet_delete.add_argument("wallet_id")
    _add_json_output_args(wallet_delete)
    wallet_delete.set_defaults(func=run_wallet_delete)
    wallet_poll = wallets_sub.add_parser("poll", parents=[common], help="Poll tracked wallets once and run copy previews.")
    wallet_poll.add_argument("--limit", type=int, default=25)
    _add_json_output_args(wallet_poll)
    wallet_poll.set_defaults(func=run_wallet_poll)
    wallet_watch = wallets_sub.add_parser("watch", parents=[common], help="Continuously poll tracked wallets from CLI until Ctrl+C.")
    wallet_watch.add_argument("--limit", type=int, default=25)
    wallet_watch.add_argument("--interval", default="10")
    wallet_watch.add_argument("--iterations", default=None, help="Optional number of polls for batch/smoke runs.")
    _add_json_output_args(wallet_watch)
    wallet_watch.set_defaults(func=run_wallet_watch)

    copy = subparsers.add_parser("copy", parents=[common], help="Show, update, or preview guarded copy trading.")
    copy_sub = copy.add_subparsers(dest="copy_command", required=True)
    copy_show = copy_sub.add_parser("show", parents=[common], help="Show copy trading settings.")
    _add_json_output_args(copy_show)
    copy_show.set_defaults(func=run_copy_show)
    copy_set = copy_sub.add_parser("set", parents=[common], help="Patch copy trading settings.")
    copy_set.add_argument("--enabled", action=argparse.BooleanOptionalAction, default=None)
    copy_set.add_argument("--live", action=argparse.BooleanOptionalAction, default=None)
    copy_set.add_argument("--follow-wallet", default=None)
    copy_set.add_argument("--follow-wallets", default=None, help="Comma-separated wallet list.")
    copy_set.add_argument("--copy-percentage", default=None)
    copy_set.add_argument("--scale", default=None)
    copy_set.add_argument("--max-usdc-per-trade", default=None)
    copy_set.add_argument("--slippage", default=None)
    copy_set.add_argument("--allow-sells", action=argparse.BooleanOptionalAction, default=None)
    copy_set.add_argument("--conflict-guard", action=argparse.BooleanOptionalAction, default=None)
    copy_set.add_argument("--conflict-window-seconds", default=None)
    copy_set.add_argument("--json", default=None)
    _add_json_output_args(copy_set)
    copy_set.set_defaults(func=run_copy_set)
    copy_preview = copy_sub.add_parser("preview", parents=[common], help="Preview a copy-trading activity without placing an order.")
    copy_preview.add_argument("--proxy-wallet", default=None)
    copy_preview.add_argument("--asset", default=None)
    copy_preview.add_argument("--token-id", default=None)
    copy_preview.add_argument("--side", choices=["BUY", "SELL"], default="BUY")
    copy_preview.add_argument("--size", default="0")
    copy_preview.add_argument("--price", default=None)
    copy_preview.add_argument("--slug", default=None)
    copy_preview.add_argument("--outcome", default=None)
    copy_preview.add_argument("--json", default=None)
    _add_json_output_args(copy_preview)
    copy_preview.set_defaults(func=run_copy_preview)

    paper = subparsers.add_parser("paper", parents=[common], help="Paper trading state, quotes, impact, and orders.")
    paper_sub = paper.add_subparsers(dest="paper_command", required=True)
    paper_show = paper_sub.add_parser("show", parents=[common], help="Show paper history and positions.")
    _add_json_output_args(paper_show)
    paper_show.set_defaults(func=run_paper_show)
    paper_quote = paper_sub.add_parser("quote", parents=[common], help="Fetch a quote/orderbook for a contract.")
    paper_quote.add_argument("--market", default=None)
    paper_quote.add_argument("--contract", required=True)
    paper_quote.add_argument("--json", default=None)
    _add_json_output_args(paper_quote)
    paper_quote.set_defaults(func=run_paper_quote)
    paper_quote_limit = paper_sub.add_parser("quote-limit", parents=[common], help="Fetch the side-aware best bid/ask limit.")
    paper_quote_limit.add_argument("--market", default=None)
    paper_quote_limit.add_argument("--contract", required=True)
    paper_quote_limit.add_argument("--side", required=True, choices=["BUY", "SELL", "BACK", "LAY"])
    paper_quote_limit.add_argument("--json", default=None)
    _add_json_output_args(paper_quote_limit)
    paper_quote_limit.set_defaults(func=run_paper_quote_limit)
    paper_impact = paper_sub.add_parser("impact", parents=[common], help="Preview position impact without recording an order.")
    paper_impact.add_argument("--market", default=None)
    paper_impact.add_argument("--contract", required=True)
    paper_impact.add_argument("--side", required=True, choices=["BUY", "SELL", "BACK", "LAY"])
    paper_impact.add_argument("--size", required=True)
    paper_impact.add_argument("--limit-price", default=None)
    paper_impact.add_argument("--metadata", action="append", type=_split_key_value, default=[])
    paper_impact.add_argument("--json", default=None)
    _add_json_output_args(paper_impact)
    paper_impact.set_defaults(func=run_paper_impact)
    paper_order = paper_sub.add_parser("order", parents=[common], help="Submit a guarded paper order.")
    paper_order.add_argument("--market", default=None)
    paper_order.add_argument("--contract", required=True)
    paper_order.add_argument("--side", required=True, choices=["BUY", "SELL", "BACK", "LAY"])
    paper_order.add_argument("--size", required=True)
    paper_order.add_argument("--limit-price", default=None)
    paper_order.add_argument("--metadata", action="append", type=_split_key_value, default=[])
    paper_order.add_argument("--json", default=None)
    _add_json_output_args(paper_order)
    paper_order.set_defaults(func=run_paper_order)
    paper_history = paper_sub.add_parser("use-history", parents=[common], help="Return an order form payload from a paper history record.")
    paper_history.add_argument("record_id")
    _add_json_output_args(paper_history)
    paper_history.set_defaults(func=run_paper_use_history)
    paper_position = paper_sub.add_parser("use-position", parents=[common], help="Return a close-order payload from a paper position.")
    paper_position.add_argument("--market", required=True)
    paper_position.add_argument("--contract", required=True)
    _add_json_output_args(paper_position)
    paper_position.set_defaults(func=run_paper_use_position)
    paper_clear = paper_sub.add_parser("clear-history", parents=[common], help="Clear paper history.")
    _add_json_output_args(paper_clear)
    paper_clear.set_defaults(func=run_paper_clear_history)

    deps = subparsers.add_parser("dependencies", parents=[common], aliases=["deps"], help="Check local dependency install status.")
    deps.add_argument("--latest", action="store_true", help="Also query PyPI for latest versions.")
    _add_json_output_args(deps)
    deps.set_defaults(func=run_dependencies)

    search = subparsers.add_parser("polymarket-user-search", parents=[common], help="Search public Polymarket profiles.")
    search.add_argument("--query", "-q", required=True)
    search.add_argument("--limit", type=int, default=10)
    _add_json_output_args(search)
    search.set_defaults(func=run_polymarket_user_search)

    user_mdd = subparsers.add_parser("polymarket-user-mdd", parents=[common], help="Compute one Polymarket wallet MDD payload.")
    user_mdd.add_argument("--wallet", required=True)
    user_mdd.add_argument("--mode", default="fast", choices=["fast", "mark_replay"])
    user_mdd.add_argument("--closed-limit", default="500")
    user_mdd.add_argument("--open-limit", default="500")
    user_mdd.add_argument("--activity-limit", default="1000")
    user_mdd.add_argument("--trade-limit", default="1000")
    user_mdd.add_argument("--include-open", action=argparse.BooleanOptionalAction, default=True)
    user_mdd.add_argument("--equity-base-usd", default=None)
    user_mdd.add_argument("--max-points", default="50")
    user_mdd.add_argument("--cache-ttl-seconds", default="0")
    user_mdd.add_argument("--mark-replay-token-limit", default="10")
    user_mdd.add_argument("--mark-replay-point-limit", default="5000")
    user_mdd.add_argument("--mark-replay-interval", default="1h")
    user_mdd.add_argument("--mark-replay-fidelity", default="60")
    user_mdd.add_argument("--include-accounting", action="store_true")
    user_mdd.add_argument("--accounting-timeout", default="30")
    _add_json_output_args(user_mdd)
    user_mdd.set_defaults(func=run_polymarket_user_mdd)

    readiness = subparsers.add_parser("polymarket-readiness", parents=[common], help="Show Polymarket CLOB/live-validation readiness.")
    _add_json_output_args(readiness)
    readiness.set_defaults(func=run_polymarket_readiness)

    mdd_cache = subparsers.add_parser("polymarket-mdd-cache", parents=[common], help="Inspect or purge cached Polymarket MDD audits.")
    mdd_cache_sub = mdd_cache.add_subparsers(dest="mdd_cache_command", required=True)
    mdd_cache_list = mdd_cache_sub.add_parser("list", parents=[common])
    mdd_cache_list.add_argument("--include-expired", action=argparse.BooleanOptionalAction, default=True)
    _add_json_output_args(mdd_cache_list)
    mdd_cache_list.set_defaults(func=run_polymarket_mdd_cache_list)
    mdd_cache_health = mdd_cache_sub.add_parser("health", parents=[common])
    _add_json_output_args(mdd_cache_health)
    mdd_cache_health.set_defaults(func=run_polymarket_mdd_cache_health)
    mdd_cache_purge = mdd_cache_sub.add_parser("purge", parents=[common])
    mdd_cache_purge.add_argument("--key", default="")
    mdd_cache_purge.add_argument("--expired-only", action="store_true")
    mdd_cache_purge.add_argument("--all", action="store_true")
    _add_json_output_args(mdd_cache_purge)
    mdd_cache_purge.set_defaults(func=run_polymarket_mdd_cache_purge)

    serve = subparsers.add_parser("serve", parents=[common], help="Run the local HTTP API/web GUI server from CLI.")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--frontend-dir", type=Path, default=DEFAULT_FRONTEND_DIR)
    serve.set_defaults(func=run_serve)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except Exception as exc:
        if os.environ.get("MARKET_SENTINEL_CLI_DEBUG"):
            raise
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
