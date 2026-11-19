"""Formatage des nombres à la française."""

from __future__ import annotations

import math
from typing import Any

CURRENCY_SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥", "CHF": "CHF", "CAD": "C$"}
NA = "—"


def _valid(x: Any) -> bool:
    try:
        return x is not None and math.isfinite(float(x))
    except (TypeError, ValueError):
        return False


def num(x: Any, decimals: int = 2) -> str:
    if not _valid(x):
        return NA
    s = f"{float(x):,.{decimals}f}"
    return s.replace(",", " ").replace(".", ",")


def compact(x: Any, decimals: int = 1) -> str:
    if not _valid(x):
        return NA
    v = float(x)
    for div, suf in ((1e12, " T"), (1e9, " Md"), (1e6, " M"), (1e3, " k")):
        if abs(v) >= div:
            return num(v / div, decimals) + suf
    return num(v, decimals)


def money(x: Any, currency: str | None = "USD", decimals: int = 2, short: bool = False) -> str:
    if not _valid(x):
        return NA
    sym = CURRENCY_SYMBOLS.get(currency or "USD", currency or "")
    return f"{compact(x, decimals) if short else num(x, decimals)} {sym}".strip()


def pct(x: Any, decimals: int = 1, signed: bool = False) -> str:
    if not _valid(x):
        return NA
    v = float(x) * 100
    s = num(v, decimals)
    if signed and v > 0:
        s = "+" + s
    return s + " %"


def ratio(x: Any, decimals: int = 1) -> str:
    return num(x, decimals) if _valid(x) and float(x) > 0 else NA
