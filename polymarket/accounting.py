from __future__ import annotations

import csv
import io
import re
import zipfile
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional

from . import data_api
from .util import normalize_wallet


MAX_ACCOUNTING_CSV_FILES = 20
MAX_ACCOUNTING_ROWS_PER_FILE = 20000

EQUITY_VALUE_KEYS = (
    "equity",
    "total_equity",
    "account_equity",
    "portfolio_value",
    "portfolio",
    "total_value",
    "net_liquidation",
    "net_liq",
    "balance",
    "value",
)
POSITION_CURRENT_VALUE_KEYS = (
    "current_value",
    "market_value",
    "position_value",
    "value",
    "amount",
)
POSITION_REALIZED_KEYS = ("realized_pnl", "realizedpnl", "realized_profit", "realized")
POSITION_CASH_PNL_KEYS = ("cash_pnl", "cashpnl")
POSITION_INITIAL_KEYS = ("initial_value", "total_bought", "cost_basis", "cost", "notional")
DEPOSIT_KEYS = ("deposit", "deposits", "deposit_usd", "deposits_usd", "deposit_usdc")
WITHDRAWAL_KEYS = ("withdrawal", "withdrawals", "withdrawal_usd", "withdrawals_usd", "withdrawal_usdc")
CASH_FLOW_KEYS = ("cash_flow", "cashflow", "net_cash_flow", "net_deposit", "net_deposits", "funding")


def _normalize_key(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")
    return normalized


def _compact_key(value: str) -> str:
    return _normalize_key(value).replace("_", "")


def _safe_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if value is None:
            return default
        text = str(value).strip().replace(",", "")
        if not text:
            return default
        if text.startswith("(") and text.endswith(")"):
            text = "-" + text[1:-1]
        return float(text)
    except (TypeError, ValueError):
        return default


def _row_value(row: Mapping[str, Any], keys: Iterable[str]) -> Any:
    compact = {_compact_key(key): value for key, value in row.items()}
    for key in keys:
        normalized = _normalize_key(key)
        if normalized in row:
            return row[normalized]
        value = compact.get(_compact_key(key))
        if value is not None:
            return value
    return None


def _row_float(row: Mapping[str, Any], keys: Iterable[str]) -> Optional[float]:
    return _safe_float(_row_value(row, keys), None)


def _parse_timestamp(value: Any) -> Optional[int]:
    number = _safe_float(value, None)
    if number is not None:
        timestamp = int(number)
        if timestamp > 10_000_000_000:
            timestamp //= 1000
        return timestamp
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp())


def _normalize_row(row: Mapping[str, Any]) -> Dict[str, Any]:
    normalized: Dict[str, Any] = {}
    for key, value in row.items():
        normalized[_normalize_key(str(key))] = value
    return normalized


def _decode_csv(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _read_csv_rows(raw: bytes, max_rows: int) -> List[Dict[str, Any]]:
    text = _decode_csv(raw)
    reader = csv.DictReader(io.StringIO(text))
    rows: List[Dict[str, Any]] = []
    for index, row in enumerate(reader):
        if index >= max_rows:
            break
        if row:
            rows.append(_normalize_row(row))
    return rows


def _timestamp_from_row(row: Mapping[str, Any]) -> Optional[int]:
    return _parse_timestamp(
        _row_value(row, ("timestamp", "time", "datetime", "date", "as_of", "asof", "created_at", "updated_at"))
    )


def _summarize_equity(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    points: List[Dict[str, Any]] = []
    deposits = 0.0
    withdrawals = 0.0
    explicit_cash_flow = 0.0
    rows_with_cash_flow = 0
    for index, row in enumerate(rows):
        equity = _row_float(row, EQUITY_VALUE_KEYS)
        timestamp = _timestamp_from_row(row)
        if equity is not None:
            points.append({"timestamp": timestamp if timestamp is not None else index, "equity_usd": equity})
        deposit = _row_float(row, DEPOSIT_KEYS)
        withdrawal = _row_float(row, WITHDRAWAL_KEYS)
        cash_flow = _row_float(row, CASH_FLOW_KEYS)
        if deposit is not None:
            deposits += max(deposit, 0.0)
            rows_with_cash_flow += 1
        if withdrawal is not None:
            withdrawals += abs(withdrawal)
            rows_with_cash_flow += 1
        if cash_flow is not None:
            explicit_cash_flow += cash_flow
            rows_with_cash_flow += 1
    points.sort(key=lambda item: item["timestamp"] if item["timestamp"] is not None else 0)
    values = [float(point["equity_usd"]) for point in points]
    first = values[0] if values else None
    last = values[-1] if values else None
    observed_change = (last - first) if first is not None and last is not None else None
    net_cash_flow = explicit_cash_flow + deposits - withdrawals
    cash_flow_gap = (observed_change - net_cash_flow) if observed_change is not None and rows_with_cash_flow else None
    return {
        "rows": len(rows),
        "points": points[-50:],
        "points_total": len(points),
        "first_equity_usd": first,
        "last_equity_usd": last,
        "max_equity_usd": max(values) if values else None,
        "min_equity_usd": min(values) if values else None,
        "base_equity_usd": max(values) if values else None,
        "base_source": "accounting_snapshot_max_equity" if values else "",
        "cash_flows": {
            "deposits_usd": deposits,
            "withdrawals_usd": withdrawals,
            "explicit_cash_flow_usd": explicit_cash_flow,
            "net_cash_flow_usd": net_cash_flow if rows_with_cash_flow else None,
            "rows_with_cash_flow": rows_with_cash_flow,
            "observed_equity_change_usd": observed_change,
            "cash_flow_gap_usd": cash_flow_gap,
            "has_explicit_cash_flows": bool(rows_with_cash_flow),
        },
    }


def _summarize_positions(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    current_value = 0.0
    realized_pnl = 0.0
    cash_pnl = 0.0
    initial_value = 0.0
    current_count = 0
    realized_count = 0
    cash_count = 0
    initial_count = 0
    for row in rows:
        current = _row_float(row, POSITION_CURRENT_VALUE_KEYS)
        realized = _row_float(row, POSITION_REALIZED_KEYS)
        cash = _row_float(row, POSITION_CASH_PNL_KEYS)
        initial = _row_float(row, POSITION_INITIAL_KEYS)
        if current is not None:
            current_value += current
            current_count += 1
        if realized is not None:
            realized_pnl += realized
            realized_count += 1
        if cash is not None:
            cash_pnl += cash
            cash_count += 1
        if initial is not None:
            initial_value += max(initial, 0.0)
            initial_count += 1
    return {
        "rows": len(rows),
        "current_value_usd": current_value if current_count else None,
        "current_value_rows": current_count,
        "realized_pnl_usd": realized_pnl if realized_count else None,
        "realized_pnl_rows": realized_count,
        "cash_pnl_usd": cash_pnl if cash_count else None,
        "cash_pnl_rows": cash_count,
        "initial_value_usd": initial_value if initial_count else None,
        "initial_value_rows": initial_count,
    }


def parse_accounting_snapshot_zip(raw: bytes, *, max_rows_per_file: int = MAX_ACCOUNTING_ROWS_PER_FILE) -> Dict[str, Any]:
    warnings: List[str] = []
    csv_files: List[Dict[str, Any]] = []
    equity_rows: List[Dict[str, Any]] = []
    position_rows: List[Dict[str, Any]] = []
    all_rows: List[Dict[str, Any]] = []
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
            if len(names) > MAX_ACCOUNTING_CSV_FILES:
                warnings.append(f"Snapshot contains {len(names)} CSV files; only first {MAX_ACCOUNTING_CSV_FILES} were parsed.")
                names = names[:MAX_ACCOUNTING_CSV_FILES]
            for name in names:
                rows = _read_csv_rows(archive.read(name), max_rows_per_file)
                lower_name = name.lower()
                file_info = {"name": name, "rows": len(rows)}
                csv_files.append(file_info)
                all_rows.extend(rows)
                if "equity" in lower_name:
                    equity_rows.extend(rows)
                if "position" in lower_name:
                    position_rows.extend(rows)
    except zipfile.BadZipFile:
        return {
            "status": "invalid_zip",
            "files": [],
            "equity": _summarize_equity([]),
            "positions": _summarize_positions([]),
            "warnings": ["Accounting snapshot bytes were not a valid ZIP archive."],
        }
    if not equity_rows:
        equity_rows = [row for row in all_rows if _row_float(row, EQUITY_VALUE_KEYS) is not None]
        if equity_rows:
            warnings.append("No equity-named CSV was found; equity values were inferred from available numeric columns.")
    if not position_rows:
        position_rows = [
            row
            for row in all_rows
            if _row_float(row, POSITION_CURRENT_VALUE_KEYS) is not None
            or _row_float(row, POSITION_REALIZED_KEYS) is not None
            or _row_float(row, POSITION_INITIAL_KEYS) is not None
        ]
        if position_rows:
            warnings.append("No positions-named CSV was found; position values were inferred from available numeric columns.")
    status = "ok" if csv_files else "empty"
    return {
        "status": status,
        "files": csv_files,
        "equity": _summarize_equity(equity_rows),
        "positions": _summarize_positions(position_rows),
        "warnings": warnings,
    }


def download_and_parse_accounting_snapshot(wallet: str, *, timeout: float = 30.0) -> Dict[str, Any]:
    normalized_wallet = normalize_wallet(str(wallet or "").strip())
    if not normalized_wallet:
        raise ValueError("user must be a valid 0x wallet/proxyWallet address.")
    raw = data_api.download_accounting_snapshot(normalized_wallet, timeout=timeout)
    payload = parse_accounting_snapshot_zip(raw)
    payload["wallet"] = normalized_wallet
    return payload


def _pct_from_drawdown(mdd_usd: Any, peak_value: Any, equity_base_usd: Any) -> Optional[float]:
    drawdown = _safe_float(mdd_usd, None)
    peak = _safe_float(peak_value, 0.0)
    base = _safe_float(equity_base_usd, None)
    if drawdown is None or base is None or base <= 0:
        return None
    denominator = base + float(peak or 0.0)
    return drawdown / denominator * 100.0 if denominator > 0 else None


def reconcile_mdd_payload_with_accounting(payload: Mapping[str, Any], snapshot: Mapping[str, Any]) -> Dict[str, Any]:
    result = dict(payload)
    equity = snapshot.get("equity") if isinstance(snapshot.get("equity"), Mapping) else {}
    positions = snapshot.get("positions") if isinstance(snapshot.get("positions"), Mapping) else {}
    base = _safe_float(equity.get("base_equity_usd"), None)
    previous_base = result.get("equity_base_usd")
    previous_pct = result.get("mdd_pct")
    if base and base > 0:
        result["equity_base_usd"] = base
        result["equity_base_source"] = "accounting_snapshot_max_equity"
        recalculated_pct = _pct_from_drawdown(result.get("mdd_usd"), result.get("peak_value"), base)
        if recalculated_pct is not None:
            result["mdd_pct"] = recalculated_pct
    open_current_value = _safe_float(result.get("open_current_value"), None)
    snapshot_current_value = _safe_float(positions.get("current_value_usd"), None)
    current_delta = (
        snapshot_current_value - open_current_value
        if snapshot_current_value is not None and open_current_value is not None
        else None
    )
    cumulative_realized = _safe_float(result.get("cumulative_realized_pnl"), None)
    snapshot_realized = _safe_float(positions.get("realized_pnl_usd"), None)
    realized_delta = (
        snapshot_realized - cumulative_realized
        if snapshot_realized is not None and cumulative_realized is not None
        else None
    )
    cash_flows = equity.get("cash_flows") if isinstance(equity.get("cash_flows"), Mapping) else {}
    material_gaps = [
        value
        for value in (
            current_delta,
            realized_delta,
            _safe_float(cash_flows.get("cash_flow_gap_usd"), None),
        )
        if value is not None and abs(value) > 0.01
    ]
    result["accounting_snapshot"] = {
        "status": snapshot.get("status", "unknown"),
        "files": list(snapshot.get("files", [])) if isinstance(snapshot.get("files"), list) else [],
        "warnings": list(snapshot.get("warnings", [])) if isinstance(snapshot.get("warnings"), list) else [],
        "equity": {
            "rows": equity.get("rows"),
            "first_equity_usd": equity.get("first_equity_usd"),
            "last_equity_usd": equity.get("last_equity_usd"),
            "max_equity_usd": equity.get("max_equity_usd"),
            "min_equity_usd": equity.get("min_equity_usd"),
            "base_equity_usd": equity.get("base_equity_usd"),
            "base_source": equity.get("base_source"),
            "cash_flows": dict(cash_flows),
        },
        "positions": dict(positions),
        "reconciliation": {
            "status": "reconciled_with_gaps" if material_gaps else "reconciled",
            "previous_equity_base_usd": previous_base,
            "previous_mdd_pct": previous_pct,
            "mdd_pct_uses_accounting_base": bool(base and base > 0),
            "open_current_value_delta_usd": current_delta,
            "realized_pnl_delta_usd": realized_delta,
            "cash_flow_gap_usd": cash_flows.get("cash_flow_gap_usd"),
            "cash_flow_gap_reported": cash_flows.get("cash_flow_gap_usd") is not None,
        },
    }
    return result
