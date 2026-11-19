"""Calculs financiers élémentaires (fonctions pures, testées).

Chaque fonction renvoie un `Value` : la valeur (ou None) et, le cas échéant, une explication courte.
Règle : une donnée absente n'est JAMAIS traitée comme zéro, et un dénominateur nul ou négatif donne N/A.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Value:
    value: float | None
    note: str | None = None

    @property
    def ok(self) -> bool:
        return self.value is not None


def num(x: Any) -> float | None:
    """Convertit en float fini, sinon None (NaN, None, texte...)."""
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def cagr(start: Any, end: Any, years: float) -> Value:
    """Taux de croissance annuel composé : (fin / début)^(1/années) - 1."""
    s, e = num(start), num(end)
    if s is None or e is None:
        return Value(None, "valeur de départ ou d'arrivée manquante")
    if years <= 0:
        return Value(None, "durée nulle")
    if s <= 0 or e <= 0:
        return Value(None, "valeur de départ ou d'arrivée nulle ou négative : CAGR non calculable")
    return Value((e / s) ** (1 / years) - 1)


def free_cash_flow(operating_cash_flow: Any, capex: Any) -> Value:
    """FCF = OCF - CAPEX, avec CAPEX exprimé en montant positif dépensé."""
    ocf, cx = num(operating_cash_flow), num(capex)
    if ocf is None or cx is None:
        return Value(None, "OCF ou CAPEX manquant")
    return Value(ocf - abs(cx))


def net_debt(total_debt: Any, cash_and_equivalents: Any, short_term_investments: Any = None) -> Value:
    """Dette nette = dette financière - (trésorerie + placements court terme). Négative = trésorerie nette."""
    d, c = num(total_debt), num(cash_and_equivalents)
    if d is None or c is None:
        return Value(None, "dette ou trésorerie manquante")
    sti = num(short_term_investments) or 0.0
    return Value(d - c - sti)


def net_debt_to_fcf(net_debt_value: Any, fcf: Any) -> Value:
    nd, f = num(net_debt_value), num(fcf)
    if nd is None or f is None:
        return Value(None, "dette nette ou FCF manquant")
    if f <= 0:
        return Value(None, "FCF nul ou négatif : ratio non interprétable")
    if nd <= 0:
        return Value(nd / f, "trésorerie nette (dette nette négative)")
    return Value(nd / f)


def effective_tax_rate(income_tax: Any, pretax_income: Any, default: float = 0.21) -> float:
    """Taux effectif borné à [0 ; 50 %] ; taux par défaut si non calculable (perte, données manquantes)."""
    t, p = num(income_tax), num(pretax_income)
    if t is None or p is None or p <= 0:
        return default
    return min(max(t / p, 0.0), 0.5)


def roce(ebit: Any, total_assets: Any, current_liabilities: Any, tax_rate: float) -> Value:
    """ROCE = NOPAT / capitaux employés ; NOPAT = EBIT × (1 - taux d'IS) ; CE = actif total - passif courant."""
    e, ta, cl = num(ebit), num(total_assets), num(current_liabilities)
    if e is None or ta is None or cl is None:
        return Value(None, "EBIT, actif total ou passif courant manquant")
    capital_employed = ta - cl
    if capital_employed <= 0:
        return Value(None, "capitaux employés nuls ou négatifs")
    return Value(e * (1 - tax_rate) / capital_employed)


def margin(numerator: Any, revenue: Any) -> Value:
    n, r = num(numerator), num(revenue)
    if n is None or r is None:
        return Value(None, "donnée manquante")
    if r <= 0:
        return Value(None, "chiffre d'affaires nul ou négatif")
    return Value(n / r)


def share_change(start_shares: Any, end_shares: Any) -> Value:
    """Variation cumulée (non annualisée) du nombre d'actions."""
    s, e = num(start_shares), num(end_shares)
    if s is None or e is None or s <= 0 or e <= 0:
        return Value(None, "nombre d'actions manquant ou invalide")
    change = e / s - 1
    if change > 0.8 or change < -0.45:
        return Value(change, "variation atypique : possible split / regroupement non ajusté")
    return Value(change)


def equity_multiple(market_cap: Any, denominator: Any) -> Value:
    """Multiple de capitalisation (Price / X = capitalisation / X). Dénominateur ≤ 0 -> N/A."""
    m, d = num(market_cap), num(denominator)
    if m is None or d is None:
        return Value(None, "capitalisation ou dénominateur manquant")
    if m <= 0:
        return Value(None, "capitalisation invalide")
    if d <= 0:
        return Value(None, "dénominateur nul ou négatif : multiple non significatif")
    return Value(m / d)


def price_from_multiple(multiple: Any, fcf_ttm: Any, diluted_shares: Any) -> Value:
    """Prix théorique = (multiple × FCF TTM) / actions diluées. Non applicable si FCF ≤ 0."""
    k, f, n = num(multiple), num(fcf_ttm), num(diluted_shares)
    if k is None or f is None or n is None:
        return Value(None, "multiple, FCF ou nombre d'actions manquant")
    if f <= 0:
        return Value(None, "FCF négatif ou nul : méthode P/FCF non applicable")
    if n <= 0 or k <= 0:
        return Value(None, "nombre d'actions ou multiple invalide")
    return Value(k * f / n)


def cap_bucket(market_cap: Any, buckets: dict[str, float]) -> str | None:
    m = num(market_cap)
    if m is None or m <= 0:
        return None
    if m < float(buckets.get("small", 2e9)):
        return "small"
    if m < float(buckets.get("mid", 10e9)):
        return "mid"
    if m < float(buckets.get("large", 100e9)):
        return "large"
    return "mega"
