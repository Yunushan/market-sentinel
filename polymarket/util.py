from __future__ import annotations

import re
from typing import Optional


_WALLET_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")


def is_wallet_address(s: str) -> bool:
    return bool(_WALLET_RE.match((s or "").strip()))


def normalize_wallet(s: str) -> Optional[str]:
    s = (s or "").strip()
    if not s:
        return None
    if is_wallet_address(s):
        return s.lower()
    return None
