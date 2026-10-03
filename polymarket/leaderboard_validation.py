from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

from .http_client import PolymarketResponseError
from .util import normalize_wallet


WALLET_ALIASES = ("proxyWallet", "proxy_wallet", "wallet", "address", "userAddress")
PNL_ALIASES = ("pnl", "pnlUsd", "pnl_usd", "profit", "profitLoss", "realizedPnl", "realizedPnlUsd")
VOLUME_ALIASES = ("volume", "volumeUsd", "volume_usd", "vol", "totalVolume", "totalVolumeUsd")
MDD_USD_ALIASES = ("mdd", "mddUsd", "mdd_usd", "maxDrawdown", "max_drawdown", "maxDrawdownUsd")
MDD_PCT_ALIASES = ("mddPct", "mdd_pct", "maxDrawdownPct", "max_drawdown_pct")


def _invalid(field: str) -> PolymarketResponseError:
    return PolymarketResponseError(f"Leaderboard has an invalid, missing, or contradictory {field}; board coverage is unknown.")


def _number(value: Any, field: str, *, nonnegative: bool = False, integer: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise _invalid(field)
    try:
        number = float(value)
    except (ValueError, OverflowError) as exc:
        raise _invalid(field) from exc
    if not math.isfinite(number) or (nonnegative and number < 0) or (integer and not number.is_integer()):
        raise _invalid(field)
    return number


def _alias_values(row: Mapping[str, Any], keys: Sequence[str]) -> list[Any]:
    sources = [row, *[row[key] for key in ("user", "profile", "trader") if isinstance(row.get(key), Mapping)]]
    return [source[key] for source in sources for key in keys if key in source]


def _numeric_alias(row: Mapping[str, Any], keys: Sequence[str], field: str, *, required: bool = False,
                   nonnegative: bool = False, integer: bool = False) -> float | None:
    values = _alias_values(row, keys)
    if not required:
        values = [value for value in values if value is not None]
    if not values:
        if required:
            raise _invalid(field)
        return None
    numbers = [_number(value, field, nonnegative=nonnegative, integer=integer) for value in values]
    if any(number != numbers[0] for number in numbers[1:]):
        raise _invalid(field)
    return numbers[0]


def source_leaderboard_fields(row: Mapping[str, Any], *, version: int = 1) -> dict[str, Any]:
    """Validate source identities/economics before normalization or pagination."""
    if not isinstance(row, Mapping):
        raise _invalid("row object")
    values = _alias_values(row, ("user_id",) if version == 2 else WALLET_ALIASES)
    wallets = [normalize_wallet(value.strip().lower()) if isinstance(value, str) else None for value in values]
    if not wallets or any(wallet is None or wallet != wallets[0] for wallet in wallets):
        raise _invalid("wallet identity")
    pnl = _numeric_alias(row, ("pnl",) if version == 2 else PNL_ALIASES, "PnL", required=True)
    volume = _numeric_alias(row, ("volume",) if version == 2 else VOLUME_ALIASES, "volume", required=True, nonnegative=True)
    if version == 1 and volume > 0 and not math.isfinite(pnl / volume * 100):
        raise _invalid("derived PnL/volume ratio")
    rank = _numeric_alias(row, ("rank", "position"), "rank", nonnegative=True, integer=True)
    if rank is not None and rank < 1:
        raise _invalid("rank")
    trades = _numeric_alias(row, ("trades", "tradeCount", "trade_count", "totalTrades"), "trade count", nonnegative=True, integer=True)
    return {
        "wallet": wallets[0], "pnl": pnl, "volume": volume,
        "rank": int(rank) if rank is not None else None,
        "trade_count": int(trades) if trades is not None else 0,
        "mdd_usd": _numeric_alias(row, MDD_USD_ALIASES, "dollar drawdown", nonnegative=True),
        "mdd_pct": _numeric_alias(row, MDD_PCT_ALIASES, "percentage drawdown", nonnegative=True),
    }


def validate_stored_leaderboard_row(row: Mapping[str, Any]) -> None:
    """Protect direct state writes and legacy reads as well as source fetches."""
    if not isinstance(row, Mapping) or not isinstance(row.get("wallet"), str) or not normalize_wallet(row["wallet"].strip().lower()):
        raise _invalid("stored wallet identity")
    pnl = _number(row.get("pnl_usd"), "stored PnL")
    raw = row.get("raw")
    version = 2 if isinstance(raw, Mapping) and "user_id" in raw else 1
    if "source_api_version" in row and (type(row["source_api_version"]) is not int or row["source_api_version"] != version):
        raise _invalid("stored source version")
    volume = None if version == 2 else _number(row.get("volume_usd"), "stored volume", nonnegative=True)
    if version == 2 and (row.get("volume_usd") is not None or row.get("roi_pct") is not None):
        raise _invalid("v2 share volume represented as monetary turnover")
    ratio = row.get("roi_pct")
    expected = pnl / volume * 100 if volume is not None and volume > 0 else None
    if expected is not None and not math.isfinite(expected):
        raise _invalid("derived PnL/volume ratio")
    if ratio is not None:
        number = _number(ratio, "stored PnL/volume ratio")
        if expected is None or not math.isclose(number, expected, rel_tol=1e-12, abs_tol=1e-9):
            raise _invalid("stored PnL/volume ratio")
    for key in ("mdd_usd", "mdd_pct"):
        if row.get(key) is not None:
            _number(row[key], "stored drawdown", nonnegative=True)
    if "raw" in row and not isinstance(raw, Mapping):
        raise _invalid("stored source object")
    if isinstance(raw, Mapping) and (version == 2 or _alias_values(raw, (*WALLET_ALIASES, *PNL_ALIASES, *VOLUME_ALIASES))):
        fields = source_leaderboard_fields(raw, version=version)
        if fields["wallet"] != normalize_wallet(row["wallet"].strip().lower()) or fields["pnl"] != pnl or (version == 1 and fields["volume"] != volume):
            raise _invalid("stored source binding")
        if version == 2 and _number(row.get("volume_shares"), "stored share volume", nonnegative=True) != fields["volume"]:
            raise _invalid("stored share volume binding")
