"""Core -> gold.company_snapshot : situation actuelle de chaque société (page « Vue globale »).

Capitalisation la plus récente, agrégats TTM disponibles à cette date et multiples de capitalisation
(P/S, P/OCF, P/FCF), avec la raison de chaque N/A et des notes de qualité.
"""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from ..config import Settings
from ..core.normalize import ttm_as_of
from ..financials import metrics as m
from ..storage import Store, utcnow

log = logging.getLogger(__name__)

MULTIPLES = {"ps": "revenue", "pocf": "operating_cash_flow", "pfcf": "free_cash_flow"}


def snapshot_row(company: dict[str, Any], mc: pd.Series | None, ttm: pd.Series | None,
                 statements: pd.DataFrame, buckets: dict[str, float]) -> dict[str, Any]:
    trading_ccy = company.get("trading_currency")
    row: dict[str, Any] = {k: company.get(k) for k in ("ticker", "name", "exchange", "sector", "industry",
                                                       "country", "trading_currency")}
    notes: list[str] = []

    row["market_cap"] = m.num(mc["market_cap"]) if mc is not None else None
    row["market_cap_date"] = mc["date"] if mc is not None else pd.NaT
    row["market_cap_source"] = mc["source"] if mc is not None else None
    if mc is None:
        notes.append("Capitalisation indisponible.")
    elif mc["source"] != "fmp_historical_market_cap":
        notes.append("Capitalisation approximée (cours × actions diluées).")
    row["cap_bucket"] = m.cap_bucket(row["market_cap"], buckets)

    reported_ccy = ttm.get("reported_currency") if ttm is not None else None
    row["reported_currency"] = reported_ccy
    currency_mismatch = bool(trading_ccy and reported_ccy and trading_ccy != reported_ccy)
    if currency_mismatch:
        notes.append(f"Devise de cotation ({trading_ccy}) ≠ devise des comptes ({reported_ccy}) : "
                     "multiples non calculés (conversion non implémentée).")

    for f in ("revenue", "gross_profit", "net_income", "operating_cash_flow", "capex", "free_cash_flow",
              "stock_based_compensation", "shares_diluted"):
        row[f"{f}_ttm"] = m.num(ttm.get(f)) if ttm is not None else None
    row["ttm_period_end"] = ttm["period_end"] if ttm is not None else pd.NaT
    row["ttm_available_date"] = ttm["available_date"] if ttm is not None else pd.NaT
    row["ttm_method"] = ttm["method"] if ttm is not None else None
    if ttm is None:
        notes.append("Aucun agrégat 12 mois disponible.")
    elif ttm["method"] != "sum_4_quarters":
        notes.append("TTM trimestriel indisponible : dernier exercice annuel utilisé.")

    for key, field in MULTIPLES.items():
        if currency_mismatch:
            v = m.Value(None, "devises différentes")
        else:
            v = m.equity_multiple(row["market_cap"], row[f"{field}_ttm"])
        row[key] = v.value
        row[f"{key}_note"] = v.note

    # Anomalies récentes (5 derniers exercices + trimestres du TTM)
    recent = statements[statements["period_end"] >= statements["period_end"].max() - pd.DateOffset(years=5)] \
        if not statements.empty else statements
    flags = sorted({f for fl in recent.get("quality_flags", pd.Series(dtype=str)).fillna("") for f in fl.split(",")
                    if f and f != "availability_estimated"})
    if "cost_of_revenue_zero" in flags:
        notes.append("Coût des ventes non publié : marge brute peu significative.")
    serious = [f for f in flags if f != "cost_of_revenue_zero"]
    if serious:
        notes.append("Incohérences signalées dans les comptes récents : " + ", ".join(serious) + ".")
    if not statements.empty and statements["availability_estimated"].any():
        notes.append("Certaines dates de publication sont estimées (délai standard).")
    row["quality_notes"] = " | ".join(notes)
    row["quality_level"] = "complete" if not notes else ("partial" if not serious and mc is not None
                                                          and ttm is not None else "degraded")
    return row


def build_snapshot(settings: Settings, store: Store) -> pd.DataFrame:
    companies = store.read("core", "companies")
    if companies.empty:
        store.write("gold", "company_snapshot", pd.DataFrame(columns=["ticker"]))
        return companies
    mcd = store.read("core", "market_cap_daily")
    ttm = store.read("core", "ttm")
    st = store.read("core", "financial_statements")
    buckets = settings.section("cap_buckets") or {}
    rows = []
    for company in companies.to_dict("records"):
        t = company["ticker"]
        mc_t = mcd[mcd["ticker"] == t].sort_values("date") if not mcd.empty else pd.DataFrame()
        mc = mc_t.iloc[-1] if not mc_t.empty else None
        as_of = mc["date"] if mc is not None else None
        last_ttm = ttm_as_of(ttm, t, as_of) if not ttm.empty else None
        st_t = st[st["ticker"] == t] if not st.empty else st
        rows.append(snapshot_row(company, mc, last_ttm, st_t, buckets))
    df = pd.DataFrame(rows)
    df["computed_at"] = utcnow().replace(tzinfo=None)
    store.write("gold", "company_snapshot", df)
    log.info("gold.company_snapshot : %d sociétés", len(df))
    return df
