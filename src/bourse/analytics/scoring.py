"""Notes de 0 à 100 sur 5 dimensions (valorisation, rentabilité, croissance, santé, dividende)."""

from __future__ import annotations

import math
from typing import Any

DIMENSIONS = {
    "valuation": "Valorisation",
    "profitability": "Rentabilité",
    "growth": "Croissance",
    "health": "Santé financière",
    "dividend": "Dividende",
}

# metric -> (dimension, valeur donnant 0, valeur donnant 100)
# Si "zéro" > "cent", la note est inversée (plus petit = mieux).
RULES: dict[str, tuple[str, float, float]] = {
    "margin_of_safety": ("valuation", -0.5, 0.5),
    "pe": ("valuation", 40, 10),
    "pb": ("valuation", 10, 1),
    "fcf_yield": ("valuation", 0.0, 0.08),
    "roe": ("profitability", 0.0, 0.25),
    "roic": ("profitability", 0.0, 0.20),
    "net_margin": ("profitability", 0.0, 0.25),
    "gross_margin": ("profitability", 0.10, 0.60),
    "revenue_cagr_5y": ("growth", -0.05, 0.15),
    "eps_cagr_5y": ("growth", -0.05, 0.20),
    "fcf_cagr_5y": ("growth", -0.05, 0.20),
    "current_ratio": ("health", 0.8, 2.5),
    "debt_to_equity": ("health", 2.0, 0.0),
    "debt_to_fcf": ("health", 8.0, 0.0),
    "altman_z": ("health", 1.8, 3.0),
    "piotroski": ("health", 2, 9),
    "dividend_yield": ("dividend", 0.0, 0.04),
    "payout_ratio": ("dividend", 1.0, 0.3),
    "dividend_years": ("dividend", 0, 20),
    "dividend_cagr_5y": ("dividend", 0.0, 0.10),
}


def _valid(x: Any) -> bool:
    return x is not None and isinstance(x, (int, float)) and math.isfinite(x)


def scale(x: float, zero: float, hundred: float) -> float:
    t = (x - zero) / (hundred - zero)
    return max(0.0, min(100.0, 100.0 * t))


def score_metrics(metrics: dict[str, Any]) -> dict[str, float | None]:
    """Retourne une note par dimension (+ 'overall'), None si aucune métrique disponible."""
    buckets: dict[str, list[float]] = {d: [] for d in DIMENSIONS}
    no_dividend = not _valid(metrics.get("dividend_yield")) or metrics.get("dividend_yield", 0) <= 0
    for metric, (dim, zero, hundred) in RULES.items():
        value = metrics.get(metric)
        if dim == "dividend" and no_dividend:
            continue
        if metric == "debt_to_fcf" and _valid(value) and value < 0:
            value = 99.0  # FCF négatif -> dette non couverte
        if metric == "pe" and _valid(value) and value <= 0:
            value = 99.0  # pertes
        if _valid(value):
            buckets[dim].append(scale(float(value), zero, hundred))
    scores: dict[str, float | None] = {
        dim: (sum(v) / len(v) if v else None) for dim, v in buckets.items()
    }
    if no_dividend:
        scores["dividend"] = 0.0
    available = [s for s in scores.values() if s is not None]
    scores["overall"] = sum(available) / len(available) if available else None
    return scores
