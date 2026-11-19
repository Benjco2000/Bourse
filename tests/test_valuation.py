import math

import pytest

from bourse.analytics import scoring
from bourse.analytics import valuation as val


def test_graham_number():
    assert val.graham_number(3, 20) == pytest.approx(math.sqrt(22.5 * 3 * 20))
    assert val.graham_number(-1, 20) is None
    assert val.graham_number(2, None) is None


def test_graham_formula():
    # EPS 2, g 5 %, Y 4,4 % -> 2 × (8,5 + 10) × 4,4 / 4,4 = 37
    assert val.graham_formula(2, 0.05, 0.044) == pytest.approx(37.0)
    assert val.graham_formula(0, 0.05, 0.044) is None


def test_dcf_zero_growth_is_perpetuity():
    # Sans croissance, la valeur tend vers FCF / r
    v = val.dcf_per_share(10, 0.0, 0.10, 0.0, high_growth_years=5, fade_years=5)
    assert v == pytest.approx(100.0, rel=1e-6)


def test_dcf_invalid_inputs():
    assert val.dcf_per_share(-1, 0.05, 0.09, 0.02) is None
    assert val.dcf_per_share(5, 0.05, 0.02, 0.03) is None  # r <= g terminal


def test_pe_multiple_requires_history():
    assert val.pe_multiple_value(2, [10, 12]) is None
    assert val.pe_multiple_value(2, [10, 12, 14]) == pytest.approx(24)
    assert val.pe_multiple_value(2, [100, 100, 100]) == pytest.approx(80)  # borné à 40


def test_cagr_and_mos_and_verdict():
    assert val.cagr(100, 121, 2) == pytest.approx(0.10)
    assert val.cagr(-1, 121, 2) is None
    assert val.margin_of_safety(100, 70) == pytest.approx(0.30)
    assert val.verdict(0.3) == "Sous-évaluée"
    assert val.verdict(-0.5) == "Surévaluée"
    assert val.verdict(0.0) == "Proche de sa juste valeur"
    assert val.verdict(None) == "Non évaluable"


def test_scoring_bounds_and_no_dividend():
    s = scoring.score_metrics({"pe": 10, "roe": 0.5, "current_ratio": 0.1, "dividend_yield": None})
    assert s["valuation"] == 100
    assert s["profitability"] == 100
    assert s["health"] == 0
    assert s["dividend"] == 0
    assert s["growth"] is None
    assert 0 <= s["overall"] <= 100
