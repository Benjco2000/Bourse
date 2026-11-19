"""Méthodes de valorisation (fonctions pures, réutilisées par le pipeline ET l'application)."""

from __future__ import annotations

import math
from collections.abc import Iterable

import numpy as np


def _ok(x: float | None) -> bool:
    return x is not None and not (isinstance(x, float) and math.isnan(x)) and math.isfinite(x)


def graham_number(eps: float | None, bvps: float | None) -> float | None:
    """√(22,5 × EPS × BVPS) — prix maximum qu'un investisseur défensif devrait payer."""
    if not (_ok(eps) and _ok(bvps)) or eps <= 0 or bvps <= 0:
        return None
    return math.sqrt(22.5 * eps * bvps)


def graham_formula(eps: float | None, growth: float | None, aaa_yield: float | None) -> float | None:
    """Formule révisée de Graham (1974) : V = EPS × (8,5 + 2g) × 4,4 / Y.

    `growth` et `aaa_yield` en décimal (0.07 = 7 %). g est exprimé en points dans la formule.
    """
    if not (_ok(eps) and _ok(growth) and _ok(aaa_yield)) or eps <= 0 or aaa_yield <= 0:
        return None
    g_pts = growth * 100
    y_pts = aaa_yield * 100
    return eps * (8.5 + 2 * g_pts) * 4.4 / y_pts


def dcf_per_share(fcf_per_share: float | None, growth: float, discount_rate: float,
                  terminal_growth: float, high_growth_years: int = 5, fade_years: int = 5) -> float | None:
    """DCF à 2 étages sur le free cash flow par action.

    - `high_growth_years` années à `growth`,
    - puis `fade_years` années de convergence linéaire vers `terminal_growth`,
    - puis valeur terminale de Gordon-Shapiro.
    """
    if not _ok(fcf_per_share) or fcf_per_share <= 0 or discount_rate <= terminal_growth:
        return None
    value, cf, t = 0.0, fcf_per_share, 0
    for _ in range(high_growth_years):
        t += 1
        cf *= 1 + growth
        value += cf / (1 + discount_rate) ** t
    for i in range(1, fade_years + 1):
        t += 1
        g = growth + (terminal_growth - growth) * i / (fade_years + 1)
        cf *= 1 + g
        value += cf / (1 + discount_rate) ** t
    terminal = cf * (1 + terminal_growth) / (discount_rate - terminal_growth)
    value += terminal / (1 + discount_rate) ** t
    return value


def pe_multiple_value(eps: float | None, historical_pes: Iterable[float]) -> float | None:
    """EPS × P/E médian historique (borné à [5 ; 40] pour éviter les valeurs aberrantes)."""
    pes = [p for p in historical_pes if _ok(p) and p > 0]
    if not _ok(eps) or eps <= 0 or len(pes) < 3:
        return None
    pe = float(np.clip(np.median(pes), 5, 40))
    return eps * pe


def cagr(first: float | None, last: float | None, years: float) -> float | None:
    if not (_ok(first) and _ok(last)) or first <= 0 or last <= 0 or years <= 0:
        return None
    return (last / first) ** (1 / years) - 1


def margin_of_safety(fair_value: float | None, price: float | None) -> float | None:
    if not (_ok(fair_value) and _ok(price)) or fair_value <= 0:
        return None
    return (fair_value - price) / fair_value


def verdict(mos: float | None, undervalued: float = 0.20, overvalued: float = -0.10) -> str:
    if mos is None or (isinstance(mos, float) and math.isnan(mos)):
        return "Non évaluable"
    if mos >= undervalued:
        return "Sous-évaluée"
    if mos <= overvalued:
        return "Surévaluée"
    return "Proche de sa juste valeur"
