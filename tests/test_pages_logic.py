import pandas as pd
import pytest

from bourse.financials.cash import buyback_effect, cash_bridge, shareholder_metrics
from bourse.transform.valuation_history import ttm_timeline


def test_cash_bridge_reconciles_to_net_change():
    row = {"operating_cash_flow": 100, "capex": 30, "share_repurchases": 20, "dividends_paid": 25,
           "acquisitions": 10, "net_debt_issuance": -5, "stock_issuance": 2, "net_change_in_cash": 15}
    steps, missing = cash_bridge(row)
    by = {s.label: s for s in steps}
    assert not missing
    assert by["Free cash flow (FCF)"].value == 70
    assert by["Rachats d'actions"].value == -20 and by["Dividendes"].value == -25
    assert by["Acquisitions (nettes)"].value == -10
    assert by["Autres flux / réconciliation"].value == pytest.approx(15 - (70 - 20 - 25 - 10 - 5 + 2))
    assert sum(s.value for s in steps if s.kind == "relative") == pytest.approx(15)


def test_cash_bridge_missing_items_are_not_zero():
    row = {"operating_cash_flow": 100, "capex": 30, "dividends_paid": 25, "net_change_in_cash": 10}
    steps, missing = cash_bridge(row)
    labels = [s.label for s in steps]
    assert "Rachats d'actions" not in labels and "Rachats d'actions" in missing
    assert any("postes indisponibles" in s.label for s in steps)
    assert cash_bridge({"capex": 3})[0] == []


def test_shareholder_metrics_and_sbc():
    m = shareholder_metrics({"operating_cash_flow": 100, "capex": 20, "stock_based_compensation": 10,
                             "revenue": 400, "share_repurchases": 30, "dividends_paid": 10}, market_cap=1000)
    assert m["fcf_after_sbc"] == 70 and m["sbc_to_revenue"] == 0.025
    assert m["returned_to_fcf"] == 0.5 and m["dividend_yield"] == 0.01
    assert shareholder_metrics({"operating_cash_flow": 10, "capex": 20})["returned_to_fcf"] is None


def test_buyback_effect():
    e = buyback_effect(buybacks=100, avg_price=10, shares_start=1000, shares_end=995)
    assert e["gross_bought_pct"] == pytest.approx(-0.01)
    assert e["net_change_pct"] == pytest.approx(-0.005)
    assert e["issued_pct"] == pytest.approx(0.005)
    assert buyback_effect(100, None, 1000, 990)["gross_bought_pct"] is None


def test_ttm_timeline_prefers_quarterly():
    ttm = pd.DataFrame({
        "period_end": pd.to_datetime(["2024-12-31", "2024-12-31", "2025-03-31"]),
        "available_date": pd.to_datetime(["2025-02-20", "2025-01-30", "2025-04-30"]),
        "method": ["annual", "sum_4_quarters", "sum_4_quarters"], "revenue": [1.0, 2.0, 3.0]})
    t = ttm_timeline(ttm)
    assert list(t["method"]) == ["sum_4_quarters", "sum_4_quarters"]


def test_valuation_history_point_in_time(demo_store):
    v = demo_store.read("gold", "valuation_history")
    ttm = demo_store.read("core", "ttm")
    assert not v.empty
    merged = v.dropna(subset=["ttm_period_end"]).merge(
        ttm[["ticker", "period_end", "available_date"]].rename(columns={"period_end": "ttm_period_end"}),
        on=["ticker", "ttm_period_end"])
    # chaque multiple n'utilise qu'un TTM publié au plus tard à sa date
    assert (merged.groupby(["ticker", "date"])["available_date"].min() <= merged.groupby(["ticker", "date"])["date"]
            .first()).all()
    assert (v["p_fcf"].dropna() > 0).all()
    ko = v[v["ticker"] == "KO"].dropna(subset=["p_sales"]).iloc[-1]
    assert ko["p_sales"] == pytest.approx(ko["market_cap"] / ko["revenue"])
