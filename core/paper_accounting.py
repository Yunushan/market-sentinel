"""Average-cost accounting for the durable, newest-first paper ledger.

Accepted previews without fills are hypothetical full fills at their limit.
They are never represented as verified executions. Stake, budget, odds and
forecast venues need their own quantity models before share PnL is available.
"""
from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

from .models import MAX_PAPER_TRADES, PaperTradeRecord


SHARE_QUOTE_CURRENCIES = {
    "polymarket": "pUSD", "kalshi": "USD", "predictit": "USD",
    "iowa_electronic_markets": "USD", "ibkr_forecasttrader": "USD",
    "forecastex": "USD", "cme_prediction_markets": "USD",
    "robinhood_prediction_markets": "USD", "coinbase_prediction_markets": "USD",
    "gemini_titan": "USD", "context_v2": None,
    "limitless_exchange": "USDC", "probable": "USDT",
    "blinq": "pUSD", "xmarket": None, "predict_fun": "USDT",
}


def ensure_paper_history_capacity(records: Sequence[PaperTradeRecord]) -> None:
    if len(records) >= MAX_PAPER_TRADES:
        raise ValueError(
            f"Paper history capacity ({MAX_PAPER_TRADES} records) reached; no order was recorded. "
            "Export and reconcile the complete ledger before explicitly clearing it. "
            "Open inventory is never silently pruned."
        )


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _empty_row(record: PaperTradeRecord) -> dict[str, Any]:
    return {
        "market_id": record.market_id, "contract_id": record.contract_id,
        "net_size": 0.0, "notional": None, "average_price": None,
        "realized": 0.0, "trades": 0, "currency": record.quote_currency,
        "accounting_status": "complete", "incomplete_reasons": [],
        "execution_assumptions": [], "quantity_unit": "shares",
    }


def paper_accounting(records: Sequence[PaperTradeRecord]) -> dict[str, Any]:
    """Compute remaining inventory and realized PnL without discarding history.

    Persisted records are newest-first. Timestamps establish chronology, with
    reverse list order breaking same-second ties in insertion order. Existing
    ledgers pruned by older releases cannot be reconstructed retroactively.
    """
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    # Do not use random UUIDs as a financial execution ordering rule.
    ordered = sorted(enumerate(records), key=lambda pair: (_number(pair[1].created_at) or 0, -pair[0]))
    seen_ids: set[str] = set()
    for _index, record in ordered:
        if not record.accepted:
            continue
        key = (record.market_id, record.contract_id)
        row = grouped.setdefault(key, _empty_row(record))
        row["trades"] += 1
        reasons = row["incomplete_reasons"]
        if record.quote_currency != row["currency"]:
            reasons.append("inconsistent_quote_currency")
            row.update(currency=None, realized=None, notional=None, average_price=None)
        if record.id in seen_ids:
            reasons.append("duplicate_record_identity")
            row["net_size"] = None
        seen_ids.add(record.id)
        side = str(record.side).upper()
        if record.market_id not in SHARE_QUOTE_CURRENCIES or side not in {"BUY", "SELL"}:
            reasons.append("unsupported_venue_quantity_model")
            row.update(net_size=None, quantity_unit="unavailable", notional=None, average_price=None, realized=None)
            continue
        if row["currency"] is None:
            reasons.append("quote_currency_unavailable")
            row["realized"] = None
        requested, filled = _number(record.size), _number(record.filled_size)
        if requested is None or requested <= 0 or filled is None or filled < 0 or filled > requested:
            reasons.append("invalid_fill_quantity")
            row["net_size"] = None
        timestamp = _number(record.created_at)
        if (timestamp is None or timestamp < 0 or not timestamp.is_integer()
                or not isinstance(record.id, str) or not record.id.strip()
                or not isinstance(record.contract_id, str) or not record.contract_id.strip()):
            reasons.append("invalid_record_identity_or_timestamp")
            row["net_size"] = None
        if row["net_size"] is None:
            row.update(notional=None, average_price=None, realized=None)
            continue
        hypothetical = filled == 0
        size = requested if hypothetical else filled
        price = _number(record.limit_price if hypothetical else record.average_price)
        if hypothetical and "assumed_full_fill_at_limit" not in row["execution_assumptions"]:
            row["execution_assumptions"].append("assumed_full_fill_at_limit")
        if price is None or not 0 <= price <= 1:
            price = None
            reasons.append("fill_price_unavailable")
        signed = float(size) * (1 if side == "BUY" else -1)
        current = row["net_size"]
        average = row["average_price"]
        next_quantity = current + signed
        if not math.isfinite(next_quantity):
            reasons.append("quantity_overflow")
            row.update(net_size=None, notional=None, average_price=None, realized=None)
            continue
        if current == 0 or current * signed > 0:
            average = (
                (abs(current) * average + abs(signed) * price) / abs(next_quantity)
                if price is not None and (current == 0 or average is not None)
                else None
            ) if current != 0 else price
        else:
            closed = min(abs(current), abs(signed))
            if average is None or price is None or row["realized"] is None:
                row["realized"] = None
            else:
                row["realized"] += closed * (price - average) * (1 if current > 0 else -1)
            if abs(signed) > abs(current):
                average = price
        if abs(next_quantity) <= 1e-12 * max(abs(current), abs(signed)):
            next_quantity, average = 0.0, None
        basis = next_quantity * average if average is not None else (0.0 if next_quantity == 0 else None)
        if any(value is not None and not math.isfinite(value) for value in (average, basis, row["realized"])):
            reasons.append("financial_value_overflow")
            average, basis, row["realized"] = None, None, None
        if row["currency"] is None:
            basis = None
        row.update(net_size=next_quantity, average_price=average, notional=basis)

    all_rows = sorted(grouped.values(), key=lambda row: (row["market_id"], row["contract_id"]))
    for row in all_rows:
        row["incomplete_reasons"] = list(dict.fromkeys(row["incomplete_reasons"]))
        if row["incomplete_reasons"]:
            row["accounting_status"] = "incomplete"
    currencies = sorted({row["currency"] for row in all_rows if row["currency"]})
    reasons = sorted({reason for row in all_rows for reason in row["incomplete_reasons"]})
    if len(currencies) > 1:
        reasons.append("multiple_quote_currencies")
    complete = not reasons
    realized_total = sum(row["realized"] for row in all_rows) if complete else None
    if realized_total is not None and not math.isfinite(realized_total):
        realized_total = None
        reasons.append("portfolio_value_overflow")
        complete = False
    return {
        "method": "chronological_average_cost_v1", "status": "complete" if complete else "incomplete",
        "scope": "retained_ledger_simulation", "opening_inventory_assumption": "empty",
        "positions": [row for row in all_rows if row["net_size"] is None or row["net_size"] != 0],
        "closed_positions": [row for row in all_rows if row["net_size"] == 0],
        "realized": realized_total,
        "quote_currency": currencies[0] if len(currencies) == 1 else None,
        "incomplete_reasons": reasons,
        "execution_assumptions": sorted({assumption for row in all_rows for assumption in row["execution_assumptions"]}),
        "limitations": [
            "Paper previews assume full execution at the limit; market orders without a fill price have unknown cost.",
            "Fees, executable depth, funding, and settlement are not simulated.",
            "Previously pruned or explicitly cleared history cannot establish complete opening inventory.",
            "Ledger insertion order resolves same-second timestamps; quote currencies are not converted.",
            "Records without a persisted quote asset have unavailable financial amounts; historical collateral migrations require reconciliation.",
        ],
    }


def paper_position_rows(records: Sequence[PaperTradeRecord]) -> list[dict[str, Any]]:
    return paper_accounting(records)["positions"]


def paper_unrealized(row: Mapping[str, Any], mark_price: Any) -> float | None:
    quantity, basis, price = _number(row.get("net_size")), _number(row.get("notional")), _number(mark_price)
    if quantity is None or basis is None or price is None or not 0 <= price <= 1:
        return None
    result = quantity * price - basis
    return result if math.isfinite(result) else None


def paper_summary(
    rows: Sequence[Mapping[str, Any]], marks: Mapping[Any, Mapping[str, Any]],
    accounting: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Never present partial financial totals as a complete portfolio value."""
    currencies = {row.get("currency") for row in rows if row.get("currency")}
    quantities = [_number(row.get("net_size")) for row in rows]
    bases = [_number(row.get("notional")) for row in rows]
    values = []
    sources: dict[str, int] = {}
    marked = 0
    latest = None
    for row in rows:
        mark = marks.get((str(row["market_id"]), str(row["contract_id"])), {})
        price = _number(mark.get("mark_price"))
        marked += int(price is not None and 0 <= price <= 1)
        values.append(paper_unrealized(row, price))
        source = str(mark.get("source") or "")
        if source:
            sources[source] = sources.get(source, 0) + 1
        timestamp = _number(mark.get("marked_at"))
        if timestamp is not None:
            latest = max(latest or timestamp, timestamp)
    reasons = sorted({reason for row in rows for reason in row.get("incomplete_reasons", [])})
    if len(currencies) > 1:
        reasons.append("multiple_quote_currencies")
    if accounting:
        reasons = sorted(set(reasons) | set(accounting.get("incomplete_reasons", [])))

    def total(numbers: Sequence[float | None], *, absolute: bool = False) -> float | None:
        if any(number is None for number in numbers):
            return None
        result = sum(abs(number) if absolute else number for number in numbers if number is not None)
        return result if math.isfinite(result) else None

    monetary_available = len(currencies) <= 1 and all(value is not None for value in bases)
    return {
        "positions": len(rows), "gross_size": total(quantities, absolute=True),
        "entry_notional": total(bases, absolute=True) if monetary_available else None,
        "net_notional": total(bases) if monetary_available else None,
        "marked": marked, "unrealized": total(values) if rows and len(currencies) <= 1 else None,
        "realized": accounting.get("realized") if accounting else None,
        "quote_currency": next(iter(currencies)) if len(currencies) == 1 else None,
        "accounting_status": "incomplete" if reasons else "complete",
        "incomplete_reasons": reasons, "unavailable_positions": sum(value is None for value in quantities),
        "mark_sources": sources, "last_marked_at": latest,
    }


def paper_order_impact(records: Sequence[PaperTradeRecord], order: Any) -> dict[str, Any]:
    before = paper_accounting(records)
    current = next((row for row in before["positions"] if (row["market_id"], row["contract_id"]) == (order.market_id, order.contract_id)), None)
    timestamp = max((_number(record.created_at) or 0 for record in records), default=0) + 1
    preview = PaperTradeRecord(
        market_id=order.market_id, contract_id=order.contract_id, side=order.side,
        size=order.size, limit_price=order.limit_price, accepted=True,
        message="Hypothetical full-fill impact", created_at=int(timestamp),
        quote_currency=SHARE_QUOTE_CURRENCIES.get(order.market_id),
    )
    after = paper_accounting([preview, *records])
    projected = next((row for row in after["positions"] + after["closed_positions"] if (row["market_id"], row["contract_id"]) == (order.market_id, order.contract_id)), None)
    supported = order.market_id in SHARE_QUOTE_CURRENCIES and order.side in {"BUY", "SELL"}
    size, price = _number(order.size), _number(order.limit_price)
    signed = size * (-1 if order.side == "SELL" else 1) if supported and size is not None and size > 0 else None
    notional = signed * price if signed is not None and price is not None and 0 <= price <= 1 else None
    if notional is not None and not math.isfinite(notional):
        notional = None
    current_net = current["net_size"] if current else (0.0 if supported else None)
    next_net = projected["net_size"] if projected else None
    effect = "unavailable"
    if current_net is not None and next_net is not None and signed is not None:
        effect = ("opens position" if current_net == 0 else "closes position" if next_net == 0 else
                  "flips position" if current_net * next_net < 0 else "adds to position" if current_net * signed > 0 else "reduces position")
    return {
        "market_id": order.market_id, "contract_id": order.contract_id, "side": order.side,
        "size": order.size, "limit_price": order.limit_price,
        "current_net": current_net, "signed_size": signed, "projected_net": next_net, "effect": effect,
        "order_notional": notional if SHARE_QUOTE_CURRENCIES.get(order.market_id) is not None else None,
        "projected_notional": projected["notional"] if projected else None,
        "projected_average": projected["average_price"] if projected else None,
        "projected_realized": projected["realized"] if projected else None,
        "accounting_status": projected["accounting_status"] if projected else "incomplete",
        "incomplete_reasons": projected["incomplete_reasons"] if projected else ["unsupported_venue_quantity_model"],
        "execution_assumptions": ["assumed_full_fill_at_limit"],
    }
