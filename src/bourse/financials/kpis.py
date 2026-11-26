"""Les 7 KPIs fondamentaux de la page « Vue globale ».

Statuts :
    good     seuil favorable atteint (calcul fiable)
    bad      seuil non atteint (calcul fiable)
    warning  historique insuffisant, valeur non interprétable, incohérence ou qualité douteuse
    na       donnée indisponible
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from . import metrics as m

KPI_META = {
    "revenue_cagr_5y": ("Croissance du CA (5 ans)", "percent",
                        "Taux de croissance annuel composé du chiffre d'affaires sur 5 exercices fiscaux."),
    "fcf_cagr_5y": ("Croissance du FCF (5 ans)", "percent",
                    "TCAM du free cash flow (OCF - CAPEX) ; non calculable si le FCF de départ ou d'arrivée est ≤ 0."),
    "share_change_5y": ("Évol. du nb d'actions (5 ans)", "percent",
                        "Variation cumulée du nombre moyen d'actions diluées. Négatif = rachats nets (relution)."),
    "roce_avg_5y": ("ROCE moyen (5 ans)", "percent",
                    "Moyenne des ROCE annuels : EBIT × (1 - taux d'IS effectif) / (actif total - passif courant)."),
    "net_debt_to_fcf": ("Dette nette / FCF", "years",
                        "Années de FCF (TTM) pour rembourser la dette nette (dette financière - trésorerie et "
                        "placements court terme). Négatif = trésorerie nette."),
    "gross_margin_ttm": ("Marge brute", "percent", "(CA - coût des ventes) / CA, sur 12 mois glissants."),
    "fcf_margin_ttm": ("Marge de FCF", "percent", "FCF / CA, sur 12 mois glissants."),
}


@dataclass
class KPIResult:
    kpi_id: str
    label: str
    unit: str
    value: float | None
    status: str
    threshold: float
    direction: str
    period_label: str
    explanation: str
    note: str | None = None
    history: list[tuple[int, float]] = field(default_factory=list)
    short_history: bool = False  # calculé sur moins d'années que la fenêtre demandée

    def as_row(self, ticker: str) -> dict[str, Any]:
        d = asdict(self)
        d["ticker"] = ticker
        d["history"] = ";".join(f"{y}:{v:.6g}" for y, v in self.history)
        return d


def _status(value: float | None, threshold: float, direction: str, warning: bool) -> str:
    if value is None:
        return "na"
    if warning:
        return "warning"
    ok = value >= threshold if direction == "higher" else value <= threshold
    return "good" if ok else "bad"


def _make(kpi_id: str, cfg: dict[str, Any], value: m.Value, period: str, warning: bool = False,
          note: str | None = None, history: list[tuple[int, float]] | None = None,
          force_status: str | None = None, short: bool = False) -> KPIResult:
    label, unit, expl = KPI_META[kpi_id]
    c = cfg.get(kpi_id, {})
    thr, direction = float(c.get("threshold", 0)), c.get("direction", "higher")
    status = force_status or _status(value.value, thr, direction, warning)
    if value.value is None and value.note and status == "na":
        # Non calculable pour une raison métier (ex. FCF négatif) -> non interprétable (orange)
        if not any(w in value.note for w in ("manquant", "indisponible")):
            status = "warning"
    return KPIResult(kpi_id, label, unit, value.value, status, thr, direction, period, expl,
                     note or value.note, history or [], short)


def _span(annual: pd.DataFrame, field_name: str, lookback: int) -> tuple[pd.Series | None, pd.Series | None, int]:
    """(ligne de départ, ligne d'arrivée, nb d'années) sur `lookback` ans, ou plus court si l'historique manque."""
    a = annual.dropna(subset=[field_name])
    if len(a) < 2:
        return None, None, 0
    end = a.iloc[-1]
    target = int(end["fiscal_year"]) - lookback
    candidates = a[a["fiscal_year"] >= target]
    start = candidates.iloc[0]
    years = int(end["fiscal_year"]) - int(start["fiscal_year"])
    return start, end, years


def compute_kpis(annual: pd.DataFrame, ttm: pd.Series | None, latest_balance: pd.Series | None,
                 cfg: dict[str, Any]) -> list[KPIResult]:
    """`annual` : exercices annuels normalisés (core.financial_statements) d'UNE société, triés.
    `ttm` : ligne core.ttm la plus récente ; `latest_balance` : dernier état (trimestre ou annuel)."""
    lookback = int(cfg.get("lookback_years", 5))
    min_years = int(cfg.get("min_years_for_5y", lookback))
    default_tax = float(cfg.get("default_tax_rate", 0.21))
    annual = annual.sort_values("fiscal_year").reset_index(drop=True)
    out: list[KPIResult] = []

    def hist(series: pd.Series) -> list[tuple[int, float]]:
        return [(int(y), float(v)) for y, v in zip(annual["fiscal_year"], series, strict=True)
                if v is not None and np.isfinite(v)]

    # 1-2. CAGR CA et FCF
    for kpi_id, col in (("revenue_cagr_5y", "revenue"), ("fcf_cagr_5y", "free_cash_flow")):
        start, end, years = _span(annual, col, lookback)
        if start is None:
            out.append(_make(kpi_id, cfg, m.Value(None, "historique indisponible"), "—"))
            continue
        v = m.cagr(start[col], end[col], years)
        period = f"FY{int(start['fiscal_year'])}→FY{int(end['fiscal_year'])}"
        short = years < min_years
        out.append(_make(kpi_id, cfg, v, period, warning=short,
                         note=f"historique de {years} ans seulement (< {lookback})" if short and v.ok else None,
                         history=hist(annual[col]), short=short))

    # 3. Variation du nombre d'actions
    start, end, years = _span(annual, "shares_diluted", lookback)
    if start is None:
        out.append(_make("share_change_5y", cfg, m.Value(None, "nombre d'actions indisponible"), "—"))
    else:
        v = m.share_change(start["shares_diluted"], end["shares_diluted"])
        short = years < min_years
        atypical = v.note is not None
        out.append(_make("share_change_5y", cfg, v, f"FY{int(start['fiscal_year'])}→FY{int(end['fiscal_year'])}",
                         warning=short or atypical,
                         note=v.note or (f"historique de {years} ans seulement" if short else None),
                         history=hist(annual["shares_diluted"]), short=short))

    # 4. ROCE moyen
    roces = []
    for _, r in annual.iterrows():
        tax = m.effective_tax_rate(r.get("income_tax"), r.get("pretax_income"), default_tax)
        roces.append(m.roce(r.get("ebit"), r.get("total_assets"), r.get("current_liabilities"), tax).value)
    roce_series = pd.Series([np.nan if x is None else x for x in roces], dtype="float64")
    window = annual.assign(roce=roce_series).tail(lookback)
    valid = window.dropna(subset=["roce"])
    if valid.empty:
        out.append(_make("roce_avg_5y", cfg, m.Value(None, "EBIT ou bilan manquant"), "—"))
    else:
        period = f"FY{int(valid['fiscal_year'].iloc[0])}–FY{int(valid['fiscal_year'].iloc[-1])}"
        short = len(valid) < min_years
        out.append(_make("roce_avg_5y", cfg, m.Value(float(valid["roce"].mean())), period, warning=short,
                         note=f"moyenne sur {len(valid)} exercice(s) valide(s)" if short else None,
                         history=hist(roce_series), short=short))

    # 5. Dette nette / FCF (bilan le plus récent, FCF TTM)
    ttm_label = _ttm_label(ttm)
    if ttm is None or latest_balance is None:
        out.append(_make("net_debt_to_fcf", cfg, m.Value(None, "bilan ou FCF TTM indisponible"), "—"))
    else:
        nd = m.net_debt(latest_balance.get("total_debt"), latest_balance.get("cash_and_equivalents"),
                        latest_balance.get("short_term_investments"))
        v = m.net_debt_to_fcf(nd.value, ttm.get("free_cash_flow")) if nd.ok else nd
        out.append(_make("net_debt_to_fcf", cfg, v, ttm_label, note=v.note))

    # 6-7. Marges TTM
    if ttm is None:
        for kpi_id in ("gross_margin_ttm", "fcf_margin_ttm"):
            out.append(_make(kpi_id, cfg, m.Value(None, "TTM indisponible"), "—"))
    else:
        gm = m.margin(ttm.get("gross_profit"), ttm.get("revenue"))
        cogs = m.num(ttm.get("cost_of_revenue"))
        suspicious = cogs == 0
        out.append(_make("gross_margin_ttm", cfg, gm, ttm_label, warning=suspicious,
                         note="coût des ventes non publié (fréquent dans la finance) : marge brute peu "
                              "significative" if suspicious else None,
                         history=hist(annual["gross_profit"] / annual["revenue"].where(annual["revenue"] > 0))))
        out.append(_make("fcf_margin_ttm", cfg, m.margin(ttm.get("free_cash_flow"), ttm.get("revenue")),
                         ttm_label,
                         history=hist(annual["free_cash_flow"] / annual["revenue"].where(annual["revenue"] > 0))))
    return out


def _ttm_label(ttm: pd.Series | None) -> str:
    if ttm is None:
        return "—"
    end = pd.Timestamp(ttm["period_end"]).strftime("%m/%Y")
    if ttm.get("method") == "sum_4_quarters":
        return f"TTM au {end}"
    return f"FY{int(ttm['fiscal_year'])} (TTM trimestriel indisponible)"
