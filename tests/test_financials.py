import math

import pandas as pd
import pytest

from bourse.core.normalize import build_ttm, ttm_as_of
from bourse.financials import metrics as m
from bourse.financials.kpis import compute_kpis

CFG = {
    "revenue_cagr_5y": {"threshold": 0.10, "direction": "higher"},
    "fcf_cagr_5y": {"threshold": 0.10, "direction": "higher"},
    "share_change_5y": {"threshold": 0.0, "direction": "lower"},
    "roce_avg_5y": {"threshold": 0.15, "direction": "higher"},
    "net_debt_to_fcf": {"threshold": 3.0, "direction": "lower"},
    "gross_margin_ttm": {"threshold": 0.15, "direction": "higher"},
    "fcf_margin_ttm": {"threshold": 0.15, "direction": "higher"},
    "lookback_years": 5, "min_years_for_5y": 5, "default_tax_rate": 0.21,
}


# ------------------------------------------------------------------ fonctions élémentaires
def test_cagr_cases():
    assert m.cagr(100, 161.051, 5).value == pytest.approx(0.10)
    assert m.cagr(100, 50, 2).value == pytest.approx(math.sqrt(0.5) - 1)
    assert m.cagr(0, 100, 5).value is None
    assert m.cagr(-10, 100, 5).value is None
    assert m.cagr(100, -5, 5).value is None
    assert m.cagr(None, 100, 5).value is None
    assert m.cagr(float("nan"), 100, 5).note


def test_free_cash_flow_capex_sign_convention():
    assert m.free_cash_flow(100, 30).value == 70
    assert m.free_cash_flow(100, -30).value == 70  # CAPEX publié négatif -> même résultat
    assert m.free_cash_flow(100, None).value is None


def test_net_debt_and_ratio():
    assert m.net_debt(100, 20, 10).value == 70
    assert m.net_debt(100, 20).value == 80
    assert m.net_debt(None, 20).value is None
    assert m.net_debt_to_fcf(60, 20).value == 3
    assert m.net_debt_to_fcf(-40, 20).value == -2  # trésorerie nette
    assert "trésorerie nette" in m.net_debt_to_fcf(-40, 20).note
    assert m.net_debt_to_fcf(60, 0).value is None
    assert m.net_debt_to_fcf(60, -5).value is None


def test_roce_and_tax_rate():
    assert m.effective_tax_rate(20, 100) == pytest.approx(0.2)
    assert m.effective_tax_rate(80, 100) == 0.5  # borné
    assert m.effective_tax_rate(10, -100, default=0.21) == 0.21  # perte -> défaut
    assert m.roce(100, 1000, 200, 0.2).value == pytest.approx(0.1)
    assert m.roce(100, 200, 300, 0.2).value is None  # capitaux employés négatifs
    assert m.roce(None, 1000, 200, 0.2).value is None


def test_margins():
    assert m.margin(30, 100).value == 0.3
    assert m.margin(30, 0).value is None
    assert m.margin(None, 100).value is None


def test_share_change():
    assert m.share_change(100, 95).value == pytest.approx(-0.05)
    assert m.share_change(100, 400).note  # split probable
    assert m.share_change(0, 100).value is None


def test_multiples_and_price_conversion():
    assert m.equity_multiple(1000, 50).value == 20
    assert m.equity_multiple(1000, 0).value is None
    assert m.equity_multiple(1000, -10).value is None
    assert m.price_from_multiple(20, 50, 10).value == 100
    assert m.price_from_multiple(20, -50, 10).value is None
    assert "non applicable" in m.price_from_multiple(20, -50, 10).note
    assert m.price_from_multiple(20, 50, 0).value is None


def test_cap_bucket():
    b = {"small": 2e9, "mid": 10e9, "large": 100e9}
    assert m.cap_bucket(1e9, b) == "small"
    assert m.cap_bucket(5e9, b) == "mid"
    assert m.cap_bucket(50e9, b) == "large"
    assert m.cap_bucket(500e9, b) == "mega"
    assert m.cap_bucket(None, b) is None


# ------------------------------------------------------------------ TTM point-in-time
def _quarters(ends, revenue=10.0, lag=30):
    return pd.DataFrame({
        "ticker": "X", "period_type": "quarter", "period_end": pd.to_datetime(ends),
        "available_date": pd.to_datetime(ends) + pd.Timedelta(days=lag), "fiscal_year": 2024,
        "reported_currency": "USD", "revenue": revenue,
    })


def test_ttm_requires_four_consecutive_quarters_and_no_lookahead():
    q = _quarters(["2024-03-31", "2024-06-30", "2024-09-30", "2024-12-31", "2025-03-31"])
    ttm = build_ttm(q)
    assert (ttm["method"] == "sum_4_quarters").sum() == 2
    assert ttm["revenue"].iloc[0] == 40
    # Au 15/01/2025, le trimestre clos fin décembre n'est pas encore publié (dispo le 30/01)
    row = ttm_as_of(ttm, "X", pd.Timestamp("2025-01-15"))
    assert row is None
    row = ttm_as_of(ttm, "X", pd.Timestamp("2025-02-15"))
    assert row["period_end"] == pd.Timestamp("2024-12-31")

    gap = _quarters(["2023-03-31", "2023-06-30", "2023-12-31", "2024-03-31"])  # trimestre manquant
    assert build_ttm(gap).empty or (build_ttm(gap)["method"] == "sum_4_quarters").sum() == 0


# ------------------------------------------------------------------ KPIs
def _annual(years=6, revenue_growth=0.12):
    rows = []
    for i, y in enumerate(range(2019, 2019 + years)):
        rev = 100 * (1 + revenue_growth) ** i
        rows.append({"fiscal_year": y, "revenue": rev, "cost_of_revenue": rev * 0.4, "gross_profit": rev * 0.6,
                     "free_cash_flow": rev * 0.2, "shares_diluted": 1000 - 10 * i, "ebit": rev * 0.3,
                     "income_tax": rev * 0.05, "pretax_income": rev * 0.25, "total_assets": rev * 2,
                     "current_liabilities": rev * 0.5})
    return pd.DataFrame(rows)


def test_kpis_full_history():
    annual = _annual()
    ttm = pd.Series({"period_end": pd.Timestamp("2024-12-31"), "fiscal_year": 2024, "method": "sum_4_quarters",
                     "revenue": 200.0, "gross_profit": 120.0, "cost_of_revenue": 80.0, "free_cash_flow": 40.0})
    bal = pd.Series({"total_debt": 100.0, "cash_and_equivalents": 20.0, "short_term_investments": 0.0})
    k = {r.kpi_id: r for r in compute_kpis(annual, ttm, bal, CFG)}
    assert len(k) == 7
    assert k["revenue_cagr_5y"].value == pytest.approx(0.12)
    assert k["revenue_cagr_5y"].status == "good"
    assert k["share_change_5y"].value == pytest.approx(950 / 1000 - 1)
    assert k["share_change_5y"].status == "good"
    assert k["net_debt_to_fcf"].value == pytest.approx(2.0)
    assert k["gross_margin_ttm"].value == pytest.approx(0.6)
    assert k["fcf_margin_ttm"].value == pytest.approx(0.2)
    assert k["roce_avg_5y"].status == "good"
    assert k["revenue_cagr_5y"].period_label == "FY2019→FY2024"


def test_kpis_short_history_and_negative_fcf():
    annual = _annual(years=3)
    annual.loc[0, "free_cash_flow"] = -5  # FCF de départ négatif
    ttm = pd.Series({"period_end": pd.Timestamp("2021-12-31"), "fiscal_year": 2021, "method": "annual",
                     "revenue": 125.0, "gross_profit": 75.0, "cost_of_revenue": 50.0, "free_cash_flow": -1.0})
    bal = pd.Series({"total_debt": 100.0, "cash_and_equivalents": 20.0})
    k = {r.kpi_id: r for r in compute_kpis(annual, ttm, bal, CFG)}
    assert k["revenue_cagr_5y"].status == "warning"  # 2 ans d'historique seulement
    assert k["fcf_cagr_5y"].value is None and k["fcf_cagr_5y"].status == "warning"
    assert k["net_debt_to_fcf"].value is None and k["net_debt_to_fcf"].status == "warning"
    assert "TTM trimestriel indisponible" in k["gross_margin_ttm"].period_label


def test_kpis_missing_data_is_na_not_zero():
    annual = _annual().assign(revenue=float("nan"))
    k = {r.kpi_id: r for r in compute_kpis(annual, None, None, CFG)}
    assert k["revenue_cagr_5y"].status == "na"
    assert k["gross_margin_ttm"].status == "na"
    assert k["net_debt_to_fcf"].value is None
