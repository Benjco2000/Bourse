"""Silver -> Gold : fondamentaux annuels enrichis, valorisation, critères de Graham, notes, screener.

Les noms de colonnes FMP ont un peu varié entre l'API v3 et l'API `stable` : la fonction `col()`
accepte plusieurs alias et prend le premier disponible.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from ..analytics import scoring
from ..analytics import valuation as val
from ..config import Settings
from ..storage import Store, utcnow

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- helpers
def col(df: pd.DataFrame, *names: str, default: float = np.nan) -> pd.Series:
    for name in names:
        if name in df.columns:
            return df[name]
    return pd.Series(default, index=df.index, dtype="float64")


def first(df: pd.DataFrame, *names: str) -> Any:
    """Première valeur non nulle de la première colonne disponible (df d'une ligne)."""
    if df.empty:
        return None
    for name in names:
        if name in df.columns:
            v = df[name].iloc[0]
            if v is not None and not (isinstance(v, float) and math.isnan(v)):
                return v
    return None


def fnum(x: Any) -> float | None:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def safe_div(a: pd.Series | float, b: pd.Series | float) -> pd.Series | float:
    if isinstance(a, pd.Series) or isinstance(b, pd.Series):
        b = pd.Series(b) if not isinstance(b, pd.Series) else b
        return a / b.where(b != 0)
    a_, b_ = fnum(a), fnum(b)
    if a_ is None or b_ is None or b_ == 0:
        return None
    return a_ / b_


def _by_symbol(df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    if df.empty or "symbol" not in df.columns:
        return {}
    return {s: g for s, g in df.groupby("symbol")}


# --------------------------------------------------------------- fondamentaux annuels
FUND_COLUMNS = [
    "symbol", "date", "fiscal_year", "currency", "revenue", "gross_profit", "operating_income", "net_income",
    "eps", "shares", "equity", "total_debt", "cash", "current_assets", "current_liabilities", "long_term_debt",
    "free_cash_flow", "operating_cash_flow", "capex", "dividends_paid", "buybacks", "bvps", "fcf_per_share",
    "gross_margin", "operating_margin", "net_margin", "fcf_margin", "roe", "roic", "current_ratio",
    "debt_to_equity", "net_current_assets", "payout_ratio",
]


def empty_fundamentals() -> pd.DataFrame:
    dtypes = {"symbol": "object", "currency": "object", "date": "datetime64[ns]", "fiscal_year": "int64"}
    return pd.DataFrame({c: pd.Series(dtype=dtypes.get(c, "float64")) for c in FUND_COLUMNS})


def build_fundamentals(store: Store) -> pd.DataFrame:
    inc = store.read("silver", "income_statement_annual")
    bal = store.read("silver", "balance_sheet_annual")
    cf = store.read("silver", "cash_flow_annual")
    if inc.empty or "symbol" not in inc.columns:
        return empty_fundamentals()

    f = pd.DataFrame({
        "symbol": inc["symbol"],
        "date": inc["date"],
        "fiscal_year": col(inc, "fiscal_year", "calendar_year"),
        "currency": col(inc, "reported_currency", default=None),
        "revenue": col(inc, "revenue"),
        "gross_profit": col(inc, "gross_profit"),
        "operating_income": col(inc, "operating_income"),
        "net_income": col(inc, "net_income"),
        "ebitda": col(inc, "ebitda"),
        "income_before_tax": col(inc, "income_before_tax"),
        "income_tax_expense": col(inc, "income_tax_expense"),
        "interest_expense": col(inc, "interest_expense"),
        "eps": col(inc, "eps_diluted", "epsdiluted", "eps"),
        "shares": col(inc, "weighted_average_shs_out_dil", "weighted_average_shs_out"),
    })
    f["fiscal_year"] = pd.to_numeric(f["fiscal_year"], errors="coerce").fillna(f["date"].dt.year).astype(int)

    if not bal.empty:
        b = pd.DataFrame({
            "symbol": bal["symbol"], "date": bal["date"],
            "total_assets": col(bal, "total_assets"),
            "total_liabilities": col(bal, "total_liabilities"),
            "equity": col(bal, "total_stockholders_equity", "total_equity"),
            "total_debt": col(bal, "total_debt"),
            "long_term_debt": col(bal, "long_term_debt"),
            "cash": col(bal, "cash_and_short_term_investments", "cash_and_cash_equivalents"),
            "current_assets": col(bal, "total_current_assets"),
            "current_liabilities": col(bal, "total_current_liabilities"),
        })
        f = f.merge(b, on=["symbol", "date"], how="left")
    if not cf.empty:
        c = pd.DataFrame({
            "symbol": cf["symbol"], "date": cf["date"],
            "operating_cash_flow": col(cf, "operating_cash_flow", "net_cash_provided_by_operating_activities"),
            "capex": col(cf, "capital_expenditure", "investments_in_property_plant_and_equipment"),
            "free_cash_flow": col(cf, "free_cash_flow"),
            "dividends_paid": col(cf, "common_dividends_paid", "net_dividends_paid", "dividends_paid").abs(),
            "buybacks": col(cf, "common_stock_repurchased").abs(),
            "stock_based_compensation": col(cf, "stock_based_compensation"),
        })
        f = f.merge(c, on=["symbol", "date"], how="left")

    for needed in ("equity", "total_debt", "cash", "current_assets", "current_liabilities", "long_term_debt",
                   "free_cash_flow", "operating_cash_flow", "capex", "dividends_paid", "buybacks"):
        if needed not in f.columns:
            f[needed] = np.nan

    f["free_cash_flow"] = f["free_cash_flow"].fillna(f["operating_cash_flow"] + f["capex"])
    f["eps"] = f["eps"].fillna(safe_div(f["net_income"], f["shares"]))
    f["bvps"] = safe_div(f["equity"], f["shares"])
    f["fcf_per_share"] = safe_div(f["free_cash_flow"], f["shares"])
    f["gross_margin"] = safe_div(f["gross_profit"], f["revenue"])
    f["operating_margin"] = safe_div(f["operating_income"], f["revenue"])
    f["net_margin"] = safe_div(f["net_income"], f["revenue"])
    f["fcf_margin"] = safe_div(f["free_cash_flow"], f["revenue"])
    f["roe"] = safe_div(f["net_income"], f["equity"].where(f["equity"] > 0))
    tax_rate = safe_div(f["income_tax_expense"], f["income_before_tax"]).clip(0, 0.5).fillna(0.21)
    invested = (f["total_debt"].fillna(0) + f["equity"] - f["cash"].fillna(0))
    f["roic"] = safe_div(f["operating_income"] * (1 - tax_rate), invested.where(invested > 0))
    f["current_ratio"] = safe_div(f["current_assets"], f["current_liabilities"])
    f["debt_to_equity"] = safe_div(f["total_debt"], f["equity"].where(f["equity"] > 0))
    f["net_current_assets"] = f["current_assets"] - f["current_liabilities"]
    f["payout_ratio"] = safe_div(f["dividends_paid"], f["net_income"].where(f["net_income"] > 0))

    f = f.sort_values(["symbol", "fiscal_year"]).drop_duplicates(["symbol", "fiscal_year"], keep="last")
    for metric in ("revenue", "eps", "free_cash_flow", "net_income"):
        prev = f.groupby("symbol")[metric].shift(1)
        f[f"{metric}_growth"] = ((f[metric] - prev) / prev.abs()).where(prev.abs() > 0)
    return f.reset_index(drop=True)


# ------------------------------------------------------------------- calculs par ticker
def _cagr_n(series: pd.Series, n: int = 5) -> float | None:
    s = series.dropna()
    if len(s) < 3:
        return None
    n = min(n, len(s) - 1)
    return val.cagr(fnum(s.iloc[-1 - n]), fnum(s.iloc[-1]), n)


def _ttm_sum(q: pd.DataFrame, *names: str) -> float | None:
    if q is None or q.empty:
        return None
    q = q.sort_values("date").tail(4)
    if len(q) < 4:
        return None
    s = col(q, *names)
    return float(s.sum()) if s.notna().all() else None


def _aaa_yield(settings: Settings, store: Store) -> float:
    cfg = settings.valuation
    setting = cfg.get("aaa_yield", "auto")
    if setting != "auto":
        return float(setting)
    tr = store.read("silver", "treasury_rates")
    if not tr.empty and "year10" in tr.columns:
        y10 = tr.sort_values("date")["year10"].dropna()
        if not y10.empty:
            return float(y10.iloc[-1]) / 100 + float(cfg.get("aaa_spread", 0.008))
    return float(cfg.get("aaa_yield_fallback", 0.05))


def _dividend_stats(div: pd.DataFrame | None, price: float | None) -> dict[str, Any]:
    out: dict[str, Any] = {"dividend_ttm": None, "dividend_yield": None, "dividend_years": 0,
                           "dividend_cagr_5y": None}
    if div is None or div.empty or "date" not in div.columns:
        return out
    d = div.copy()
    d["amount"] = col(d, "adj_dividend", "dividend")
    d = d.dropna(subset=["date", "amount"])
    d = d[d["amount"] > 0]
    if d.empty:
        return out
    now = pd.Timestamp(datetime.now().date())
    ttm = float(d.loc[d["date"] > now - pd.Timedelta(days=365), "amount"].sum())
    out["dividend_ttm"] = ttm
    out["dividend_yield"] = safe_div(ttm, price) if price else None
    yearly = d.groupby(d["date"].dt.year)["amount"].sum()
    last_full = now.year - 1
    years = 0
    y = last_full if last_full in yearly.index else now.year
    while y in yearly.index and yearly[y] > 0:
        years += 1
        y -= 1
    out["dividend_years"] = years
    if last_full in yearly.index and (last_full - 5) in yearly.index:
        out["dividend_cagr_5y"] = val.cagr(float(yearly[last_full - 5]), float(yearly[last_full]), 5)
    return out


def _analyst_growth(est: pd.DataFrame | None, eps_now: float | None) -> float | None:
    if est is None or est.empty or not eps_now or eps_now <= 0:
        return None
    e = est.copy()
    e["eps_est"] = col(e, "eps_avg", "estimated_eps_avg")
    now = pd.Timestamp(datetime.now().date())
    fut = e[(e["date"] > now) & e["eps_est"].notna()].sort_values("date")
    if fut.empty:
        return None
    far = fut.iloc[min(len(fut), 3) - 1]
    years = max((far["date"] - now).days / 365.25, 0.5)
    return val.cagr(eps_now, float(far["eps_est"]), years)


def compute_symbol(sym: str, ctx: dict[str, Any], settings: Settings, aaa: float) -> dict[str, Any]:
    vcfg, gcfg = settings.valuation, settings.graham
    g = lambda name: ctx[name].get(sym, pd.DataFrame())  # noqa: E731
    fund = g("fund")
    fund = empty_fundamentals() if fund.empty else fund.sort_values("fiscal_year")
    quote, profile = g("quote"), g("profile")
    last = fund.iloc[-1] if not fund.empty else pd.Series(dtype=float)

    price = fnum(first(quote, "price")) or fnum(first(profile, "price"))
    if price is None and not g("prices").empty and "close" in g("prices").columns:
        price = fnum(g("prices").sort_values("date")["close"].iloc[-1])

    inc_q, bal_q, cf_q = g("inc_q"), g("bal_q"), g("cf_q")
    eps_ttm = _ttm_sum(inc_q, "eps_diluted", "epsdiluted", "eps") or fnum(last.get("eps"))
    fcf_ttm = _ttm_sum(cf_q, "free_cash_flow") or fnum(last.get("free_cash_flow"))
    shares = None
    if not inc_q.empty:
        shares = fnum(first(inc_q.sort_values("date", ascending=False),
                            "weighted_average_shs_out_dil", "weighted_average_shs_out"))
    shares = shares or fnum(last.get("shares"))
    if not shares and price:
        shares = safe_div(fnum(first(quote, "market_cap")), price)

    latest_bal = bal_q.sort_values("date").iloc[[-1]] if not bal_q.empty else pd.DataFrame()
    equity = fnum(first(latest_bal, "total_stockholders_equity", "total_equity")) or fnum(last.get("equity"))
    total_debt = fnum(first(latest_bal, "total_debt")) or fnum(last.get("total_debt"))
    ca = fnum(first(latest_bal, "total_current_assets")) or fnum(last.get("current_assets"))
    cl = fnum(first(latest_bal, "total_current_liabilities")) or fnum(last.get("current_liabilities"))
    ltd = fnum(first(latest_bal, "long_term_debt")) or fnum(last.get("long_term_debt"))

    bvps = safe_div(equity, shares)
    fcf_ps = safe_div(fcf_ttm, shares)
    market_cap = fnum(first(quote, "market_cap")) or fnum(first(profile, "market_cap", "mkt_cap"))
    if not market_cap and price and shares:
        market_cap = price * shares

    m: dict[str, Any] = {
        "symbol": sym, "price": price, "market_cap": market_cap, "eps_ttm": eps_ttm, "bvps": bvps,
        "fcf_ttm": fcf_ttm, "fcf_per_share": fcf_ps, "shares": shares,
        "pe": safe_div(price, eps_ttm) if eps_ttm else None,
        "pb": safe_div(price, bvps) if bvps and bvps > 0 else None,
        "fcf_yield": safe_div(fcf_ttm, market_cap),
        "roe": fnum(last.get("roe")), "roic": fnum(last.get("roic")),
        "gross_margin": fnum(last.get("gross_margin")), "net_margin": fnum(last.get("net_margin")),
        "operating_margin": fnum(last.get("operating_margin")),
        "current_ratio": safe_div(ca, cl), "debt_to_equity": safe_div(total_debt, equity) if equity and equity > 0 else None,
        "debt_to_fcf": safe_div(total_debt, fcf_ttm),
        "revenue_cagr_5y": _cagr_n(fund.get("revenue", pd.Series(dtype=float))),
        "eps_cagr_5y": _cagr_n(fund.get("eps", pd.Series(dtype=float))),
        "fcf_cagr_5y": _cagr_n(fund.get("free_cash_flow", pd.Series(dtype=float))),
        "revenue": fnum(last.get("revenue")),
        "altman_z": fnum(first(g("scores"), "altman_z_score")),
        "piotroski": fnum(first(g("scores"), "piotroski_score")),
    }
    m.update(_dividend_stats(g("div"), price))
    m["payout_ratio"] = safe_div(m["dividend_ttm"], eps_ttm) if m["dividend_ttm"] and eps_ttm and eps_ttm > 0 else None

    # ---------------- hypothèses de croissance
    g_analyst = _analyst_growth(g("est"), eps_ttm)
    g_hist = m["eps_cagr_5y"] if m["eps_cagr_5y"] is not None else m["revenue_cagr_5y"]
    if g_analyst is not None:
        growth, source = g_analyst, "Consensus analystes (EPS)"
    elif g_hist is not None:
        growth, source = g_hist, "Historique 5 ans"
    else:
        growth, source = 0.03, "Par défaut"
    growth = float(np.clip(growth, vcfg.get("min_growth", -0.05), vcfg.get("max_growth", 0.15)))

    # ---------------- valorisation
    ratios = g("ratios")
    pes = col(ratios.sort_values("date").tail(5), "price_to_earnings_ratio", "price_earnings_ratio").tolist() \
        if not ratios.empty else []
    methods = {
        "graham_number": val.graham_number(eps_ttm, bvps),
        "graham_formula": val.graham_formula(eps_ttm, growth, aaa),
        "dcf": val.dcf_per_share(fcf_ps, growth, vcfg.get("discount_rate", 0.09), vcfg.get("terminal_growth", 0.025),
                                 int(vcfg.get("high_growth_years", 5)), int(vcfg.get("high_growth_years", 5))),
        "pe_multiple": val.pe_multiple_value(eps_ttm, pes),
        "fmp_dcf": fnum(first(g("dcf"), "dcf")),
    }
    used = [methods[k] for k in vcfg.get("composite_methods", []) if methods.get(k) is not None]
    fair_value = float(np.median(used)) if used else None
    mos = val.margin_of_safety(fair_value, price)
    m.update({f"value_{k}": v for k, v in methods.items()})
    m.update({
        "growth_used": growth, "growth_source": source, "aaa_yield": aaa,
        "discount_rate": vcfg.get("discount_rate"), "terminal_growth": vcfg.get("terminal_growth"),
        "fair_value": fair_value, "margin_of_safety": mos,
        "upside": (fair_value / price - 1) if fair_value and price else None,
        "verdict": val.verdict(mos, vcfg.get("undervalued_mos", 0.2), vcfg.get("overvalued_mos", -0.1)),
        "methods_used": len(used),
    })

    # ---------------- critères de Graham
    m["criteria"] = graham_criteria(fund, m, ca, cl, ltd, gcfg)
    m["graham_score"] = sum({"pass": 1, "partial": 0.5}.get(c["status"], 0) for c in m["criteria"])
    m["scores"] = scoring.score_metrics(m)
    return m


def graham_criteria(fund: pd.DataFrame, m: dict[str, Any], ca: float | None, cl: float | None,
                    ltd: float | None, cfg: dict[str, Any]) -> list[dict[str, Any]]:
    out = []

    def add(cid: str, label: str, desc: str, status: str, value: str, threshold: str) -> None:
        out.append({"criterion_id": cid, "label": label, "description": desc, "status": status,
                    "value_text": value, "threshold_text": threshold})

    def fmt_money(x: float | None) -> str:
        if x is None:
            return "n.d."
        for div, suf in ((1e12, " T"), (1e9, " Md"), (1e6, " M")):
            if abs(x) >= div:
                return f"{x / div:,.1f}{suf}".replace(",", " ")
        return f"{x:,.0f}".replace(",", " ")

    # 1. Taille
    rev, min_rev = m.get("revenue"), float(cfg.get("min_revenue", 2e9))
    add("size", "Taille suffisante", "Chiffre d'affaires annuel suffisant pour limiter le risque.",
        "na" if rev is None else ("pass" if rev >= min_rev else "fail"), fmt_money(rev), f"≥ {fmt_money(min_rev)}")

    # 2. Situation financière solide
    cr = m.get("current_ratio")
    nca = (ca - cl) if ca is not None and cl is not None else None
    min_cr = float(cfg.get("min_current_ratio", 2.0))
    if cr is None:
        status = "na"
    else:
        ok_ltd = ltd is None or nca is None or ltd <= nca
        status = "pass" if cr >= min_cr and ok_ltd else "fail"
    add("financial_condition", "Situation financière solide",
        "Actif courant ≥ 2 × passif courant, et dette à long terme ≤ fonds de roulement.",
        status, f"Ratio courant {cr:.2f} · Dette LT {fmt_money(ltd)} vs FR {fmt_money(nca)}" if cr else "n.d.",
        f"≥ {min_cr:g} et dette LT ≤ FR")

    eps = fund.dropna(subset=["eps"]) if not fund.empty else fund
    # 3. Stabilité des bénéfices
    n = int(cfg.get("earnings_stability_years", 10))
    if eps.empty:
        add("earnings_stability", "Stabilité des bénéfices", "Bénéfices positifs chaque année.", "na", "n.d.",
            f"{n} ans")
    else:
        window = eps.tail(n)
        negatives = int((window["eps"] <= 0).sum())
        status = "fail" if negatives else ("pass" if len(window) >= n else "partial")
        add("earnings_stability", "Stabilité des bénéfices",
            f"Bénéfice par action positif sur chacune des {n} dernières années.",
            status, f"{len(window) - negatives}/{len(window)} années positives", f"{n}/{n} années")

    # 4. Historique de dividendes
    years_req = int(cfg.get("dividend_record_years", 20))
    dy = int(m.get("dividend_years") or 0)
    add("dividend_record", "Historique de dividendes", "Dividende versé sans interruption.",
        "pass" if dy >= years_req else "fail", f"{dy} années consécutives", f"≥ {years_req} ans")

    # 5. Croissance des bénéfices
    min_g = float(cfg.get("min_eps_growth", 0.33))
    window = eps.tail(10) if not eps.empty else eps
    if len(window) >= 6:
        start, end = window["eps"].head(3).mean(), window["eps"].tail(3).mean()
        growth = (end - start) / abs(start) if start else None
        if growth is None:
            status, txt = "na", "n.d."
        else:
            status = "pass" if growth >= min_g else "fail"
            if status == "pass" and len(window) < 10:
                status = "partial"
            txt = f"{growth:+.0%} sur {len(window)} ans"
    else:
        status, txt = "na", "historique insuffisant"
    add("earnings_growth", "Croissance des bénéfices",
        "EPS moyen des 3 dernières années vs 3 premières, sur 10 ans.", status, txt, f"≥ +{min_g:.0%}")

    # 6. P/E modéré (sur EPS moyen 3 ans)
    price = m.get("price")
    eps3 = eps["eps"].tail(3).mean() if len(eps) >= 3 else None
    pe3 = price / eps3 if price and eps3 and eps3 > 0 else None
    max_pe = float(cfg.get("max_pe", 15))
    add("moderate_pe", "P/E modéré", "Cours / bénéfice moyen des 3 dernières années.",
        "na" if pe3 is None else ("pass" if pe3 <= max_pe else "fail"),
        f"{pe3:.1f}" if pe3 else ("pertes" if eps3 is not None and eps3 <= 0 else "n.d."), f"≤ {max_pe:g}")

    # 7. P/B modéré
    pb = m.get("pb")
    max_pb, max_prod = float(cfg.get("max_pb", 1.5)), float(cfg.get("max_pe_x_pb", 22.5))
    if pb is None:
        status, txt = "na", "n.d."
    else:
        prod = pe3 * pb if pe3 else None
        status = "pass" if pb <= max_pb or (prod is not None and prod <= max_prod) else "fail"
        txt = f"P/B {pb:.2f}" + (f" · P/E×P/B {prod:.1f}" if prod else "")
    add("moderate_pb", "P/B modéré", "Cours / valeur comptable, ou P/E × P/B ≤ 22,5.", status, txt,
        f"P/B ≤ {max_pb:g} ou P/E×P/B ≤ {max_prod:g}")
    return out


# --------------------------------------------------------------------------- build
def build_gold(settings: Settings, store: Store) -> dict[str, int]:
    fund = build_fundamentals(store)
    store.write("gold", "fundamentals_annual", fund)

    read = store.read
    ctx = {
        "fund": _by_symbol(fund),
        "quote": _by_symbol(read("silver", "quote")),
        "profile": _by_symbol(read("silver", "profile")),
        "prices": _by_symbol(read("silver", "prices_daily", columns=None)),
        "inc_q": _by_symbol(read("silver", "income_statement_quarter")),
        "bal_q": _by_symbol(read("silver", "balance_sheet_quarter")),
        "cf_q": _by_symbol(read("silver", "cash_flow_quarter")),
        "ratios": _by_symbol(read("silver", "ratios_annual")),
        "scores": _by_symbol(read("silver", "financial_scores")),
        "div": _by_symbol(read("silver", "dividends")),
        "est": _by_symbol(read("silver", "analyst_estimates")),
        "dcf": _by_symbol(read("silver", "dcf_fmp")),
    }
    symbols = sorted(set(ctx["quote"]) | set(ctx["profile"]) | set(ctx["fund"]))
    aaa = _aaa_yield(settings, store)
    now = utcnow().replace(tzinfo=None)

    val_rows, crit_rows, score_rows = [], [], []
    for sym in symbols:
        try:
            m = compute_symbol(sym, ctx, settings, aaa)
        except Exception as exc:  # un ticker en erreur ne doit pas bloquer les autres
            log.exception("gold: échec du calcul pour %s : %s", sym, exc)
            continue
        criteria, scores = m.pop("criteria"), m.pop("scores")
        val_rows.append(m | {"computed_at": now})
        crit_rows += [c | {"symbol": sym} for c in criteria]
        score_rows += [{"symbol": sym, "dimension": d, "label": scoring.DIMENSIONS.get(d, "Global"), "score": s}
                       for d, s in scores.items()]
        val_rows[-1].update({f"score_{d}": s for d, s in scores.items()})

    valuation = pd.DataFrame(val_rows) if val_rows else pd.DataFrame({"symbol": pd.Series(dtype=str)})
    criteria = pd.DataFrame(crit_rows)
    scores = pd.DataFrame(score_rows)
    store.write("gold", "valuation", valuation)
    store.write("gold", "graham_criteria", criteria)
    store.write("gold", "scores", scores)

    # ---------------- screener (une ligne par action, prête pour l'app)
    profile = read("silver", "profile")
    quote = read("silver", "quote")
    screener = valuation.copy()
    if not profile.empty and "symbol" in profile.columns:
        p = pd.DataFrame({
            "symbol": profile["symbol"],
            "name": col(profile, "company_name", default=None),
            "sector": col(profile, "sector", default=None),
            "industry": col(profile, "industry", default=None),
            "country": col(profile, "country", default=None),
            "exchange": col(profile, "exchange", "exchange_short_name", default=None),
            "currency": col(profile, "currency", default=None),
            "beta": col(profile, "beta"),
            "image": col(profile, "image", default=None),
        })
        screener = screener.merge(p, on="symbol", how="left")
    if not quote.empty and "symbol" in quote.columns:
        q = pd.DataFrame({
            "symbol": quote["symbol"],
            "quote_name": col(quote, "name", default=None),
            "change_pct": col(quote, "change_percentage", "changes_percentage"),
            "year_high": col(quote, "year_high"),
            "year_low": col(quote, "year_low"),
            "volume": col(quote, "volume"),
        })
        screener = screener.merge(q, on="symbol", how="left")
        if "name" in screener.columns:
            screener["name"] = screener["name"].fillna(screener["quote_name"])
        else:
            screener["name"] = screener["quote_name"]
        screener = screener.drop(columns=["quote_name"])
    store.write("gold", "screener", screener)

    # ---------------- dividendes annuels (graphique)
    div = read("silver", "dividends")
    if not div.empty and "date" in div.columns:
        d = div.assign(amount=col(div, "adj_dividend", "dividend"), year=div["date"].dt.year)
        store.write("gold", "dividends_annual",
                    d.groupby(["symbol", "year"], as_index=False)["amount"].sum()
                     .rename(columns={"amount": "dividend_per_share"}))

    log.info("gold : %d actions valorisées", len(valuation))
    return {"fundamentals_annual": len(fund), "valuation": len(valuation), "graham_criteria": len(criteria),
            "scores": len(scores), "screener": len(screener)}
