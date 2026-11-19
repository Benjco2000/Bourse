"""Silver (schéma FMP) -> Core (modèle normalisé, indépendant du fournisseur).

C'est le SEUL module qui connaît les noms de champs FMP pour les données financières. Un autre fournisseur
(CSV, autre API) n'aurait qu'à produire les mêmes tables `core.*` (cf. docs/DATA_DICTIONARY.md).

Tables produites :
    core.companies             une ligne par société
    core.financial_statements  une ligne par (ticker, fin de période, type de période) — compte de résultat,
                               bilan et flux de trésorerie fusionnés, avec dates de publication / disponibilité
    core.ttm                   agrégats sur 12 mois glissants, datés de leur disponibilité (point-in-time)
    core.prices_daily          cours de clôture quotidiens
    core.market_cap_daily      capitalisation quotidienne (source FMP, ou approximation documentée)
    core.revenue_segments      CA par segment produit / géographique (format long)
    core.peers                 concurrents (configuration prioritaire, sinon FMP)
"""

from __future__ import annotations

import json
import logging
from typing import Any

import numpy as np
import pandas as pd

from ..config import Settings
from ..storage import Store

log = logging.getLogger(__name__)

SOURCE = "fmp"

# champ normalisé -> alias FMP possibles (stable puis v3)
INCOME_MAP = {
    "revenue": ["revenue"],
    "cost_of_revenue": ["cost_of_revenue"],
    "gross_profit_reported": ["gross_profit"],
    "operating_income": ["operating_income"],
    "ebit": ["ebit", "operating_income"],
    "pretax_income": ["income_before_tax"],
    "income_tax": ["income_tax_expense"],
    "net_income": ["net_income"],
    "eps_diluted": ["eps_diluted", "epsdiluted"],
    "shares_basic": ["weighted_average_shs_out"],
    "shares_diluted": ["weighted_average_shs_out_dil"],
}
BALANCE_MAP = {
    "total_assets": ["total_assets"],
    "current_assets": ["total_current_assets"],
    "current_liabilities": ["total_current_liabilities"],
    "total_liabilities": ["total_liabilities"],
    "total_equity": ["total_stockholders_equity", "total_equity"],
    "total_equity_incl_minority": ["total_equity"],
    "minority_interest": ["minority_interest"],
    "cash_and_equivalents": ["cash_and_cash_equivalents"],
    "short_term_investments": ["short_term_investments"],
    "total_debt": ["total_debt"],
    "short_term_debt": ["short_term_debt"],
    "long_term_debt": ["long_term_debt"],
    "total_liabilities_and_equity": ["total_liabilities_and_total_equity"],
}
CASHFLOW_MAP = {
    "operating_cash_flow": ["operating_cash_flow", "net_cash_provided_by_operating_activities"],
    "capex_reported": ["capital_expenditure", "investments_in_property_plant_and_equipment"],
    "stock_based_compensation": ["stock_based_compensation"],
    "dividends_reported": ["common_dividends_paid", "net_dividends_paid", "dividends_paid"],
    "buybacks_reported": ["common_stock_repurchased"],
    "stock_issuance": ["common_stock_issuance", "common_stock_issued"],
    "acquisitions_reported": ["acquisitions_net"],
    "net_debt_issuance": ["net_debt_issuance", "debt_repayment"],
    "net_change_in_cash": ["net_change_in_cash"],
}
FLOW_FIELDS = ["revenue", "cost_of_revenue", "gross_profit", "operating_income", "ebit", "pretax_income",
               "income_tax", "net_income", "operating_cash_flow", "capex", "free_cash_flow",
               "stock_based_compensation", "dividends_paid", "share_repurchases", "stock_issuance",
               "acquisitions", "net_debt_issuance", "net_change_in_cash"]
STOCK_FIELDS = ["total_assets", "current_assets", "current_liabilities", "total_equity", "cash_and_equivalents",
                "short_term_investments", "total_debt", "shares_diluted", "shares_basic"]


def _pick(df: pd.DataFrame, aliases: list[str]) -> pd.Series:
    for a in aliases:
        if a in df.columns:
            return pd.to_numeric(df[a], errors="coerce")
    return pd.Series(np.nan, index=df.index, dtype="float64")


def _statement(df: pd.DataFrame, mapping: dict[str, list[str]], keep_meta: bool) -> pd.DataFrame:
    if df.empty or "symbol" not in df.columns:
        return pd.DataFrame()
    out = pd.DataFrame({"ticker": df["symbol"].astype(str), "period_end": pd.to_datetime(df["date"])})
    out["period_label"] = df["period"].astype(str) if "period" in df.columns else "FY"
    if keep_meta:
        fy = _pick(df, ["fiscal_year", "calendar_year"])
        out["fiscal_year"] = fy.fillna(out["period_end"].dt.year).astype(int)
        out["reported_currency"] = df["reported_currency"] if "reported_currency" in df.columns else None
        out["filing_date"] = pd.to_datetime(df["filing_date"], errors="coerce") if "filing_date" in df.columns \
            else pd.NaT
        out["accepted_date"] = pd.to_datetime(df["accepted_date"], errors="coerce") \
            if "accepted_date" in df.columns else pd.NaT
        out["retrieved_at"] = pd.to_datetime(df["_fetched_at"]) if "_fetched_at" in df.columns else pd.NaT
    for name, aliases in mapping.items():
        out[name] = _pick(df, aliases)
    return out


def build_statements(store: Store, settings: Settings) -> pd.DataFrame:
    lags = settings.section("availability_lag_days") or {}
    frames = []
    for period_type, suffix in (("annual", "annual"), ("quarter", "quarter")):
        inc = _statement(store.read("silver", f"income_statement_{suffix}"), INCOME_MAP, keep_meta=True)
        if inc.empty:
            continue
        keys = ["ticker", "period_end", "period_label"]
        bal = _statement(store.read("silver", f"balance_sheet_{suffix}"), BALANCE_MAP, keep_meta=False)
        cf = _statement(store.read("silver", f"cash_flow_{suffix}"), CASHFLOW_MAP, keep_meta=False)
        df = inc
        for other in (bal, cf):
            if not other.empty:
                df = df.merge(other, on=keys, how="left")
        df["period_type"] = period_type
        lag = int(lags.get(period_type, 90 if period_type == "annual" else 45))
        available = df["accepted_date"].dt.normalize().fillna(df["filing_date"])
        df["availability_estimated"] = available.isna()
        df["available_date"] = available.fillna(df["period_end"] + pd.Timedelta(days=lag))
        frames.append(df)
    if not frames:
        return pd.DataFrame(columns=["ticker", "period_end", "period_type"])
    df = pd.concat(frames, ignore_index=True)
    for col in set(sum([list(m) for m in (INCOME_MAP, BALANCE_MAP, CASHFLOW_MAP)], [])) - set(df.columns):
        df[col] = np.nan

    # ---- conventions de signe (tous les décaissements en montants positifs)
    df["capex"] = df["capex_reported"].abs()
    df["dividends_paid"] = df["dividends_reported"].abs()
    df["share_repurchases"] = df["buybacks_reported"].abs()
    df["stock_issuance"] = df["stock_issuance"].abs()
    df["acquisitions"] = -df["acquisitions_reported"]  # > 0 = cash dépensé en acquisitions (net de cessions)
    df["free_cash_flow"] = df["operating_cash_flow"] - df["capex"]
    gp_calc = df["revenue"] - df["cost_of_revenue"]
    df["gross_profit"] = gp_calc.where(df["cost_of_revenue"].notna(), df["gross_profit_reported"])
    df["unit"] = "units"
    df["source"] = SOURCE

    # ---- contrôles de cohérence (signalés, jamais corrigés silencieusement)
    flags: list[list[str]] = [[] for _ in range(len(df))]

    def flag(mask: pd.Series, code: str) -> None:
        for i in np.flatnonzero(mask.fillna(False).to_numpy()):
            flags[i].append(code)

    rev = df["revenue"]
    flag(rev.isna(), "revenue_missing")
    flag(rev <= 0, "revenue_non_positive")
    flag((df["cost_of_revenue"] == 0) & (rev > 0), "cost_of_revenue_zero")
    flag(((gp_calc - df["gross_profit_reported"]).abs() > 0.01 * rev.abs()) & df["gross_profit_reported"].notna(),
         "gross_profit_mismatch")
    # Actif = passif + capitaux propres y compris intérêts minoritaires
    equity_all = df["total_equity_incl_minority"].fillna(df["total_equity"] + df["minority_interest"].fillna(0))
    flag(((df["total_liabilities"] + equity_all - df["total_assets"]).abs() > 0.02 * df["total_assets"]),
         "balance_sheet_mismatch")
    flag(df["shares_diluted"].isna() | (df["shares_diluted"] <= 0), "shares_missing")
    flag(df["operating_cash_flow"].isna(), "ocf_missing")
    flag(df["availability_estimated"], "availability_estimated")
    df["quality_flags"] = [",".join(f) for f in flags]

    drop = ["capex_reported", "dividends_reported", "buybacks_reported", "acquisitions_reported",
            "gross_profit_reported"]
    df = df.drop(columns=drop).sort_values(["ticker", "period_type", "period_end"])
    return df.drop_duplicates(["ticker", "period_type", "period_end"], keep="last").reset_index(drop=True)


def build_ttm(statements: pd.DataFrame) -> pd.DataFrame:
    """TTM point-in-time.

    - Méthode principale : somme de 4 trimestres CONSÉCUTIFS (écart de 80 à 100 jours entre fins de trimestre).
      Date de disponibilité = celle du trimestre le plus récent.
    - Repli : dernier exercice annuel (méthode `annual`), quand les trimestres manquent.
    Postes de bilan et nombre d'actions : valeur du dernier trimestre (ou de l'exercice).
    """
    rows: list[dict[str, Any]] = []
    if statements.empty:
        statements = pd.DataFrame(columns=["ticker", "period_type", "period_end", "available_date"])
    missing = [c for c in FLOW_FIELDS + STOCK_FIELDS if c not in statements.columns]
    statements = statements.assign(**{c: np.nan for c in missing})
    for ticker, g in statements.groupby("ticker"):
        q = g[g["period_type"] == "quarter"].sort_values("period_end").reset_index(drop=True)
        for i in range(3, len(q)):
            window = q.iloc[i - 3:i + 1]
            gaps = window["period_end"].diff().dt.days.dropna()
            if not gaps.between(80, 100).all():
                continue
            last = window.iloc[-1]
            row = {"ticker": ticker, "period_end": last["period_end"], "available_date": last["available_date"],
                   "method": "sum_4_quarters", "fiscal_year": last["fiscal_year"],
                   "reported_currency": last["reported_currency"]}
            for f in FLOW_FIELDS:
                vals = window[f]
                row[f] = float(vals.sum()) if vals.notna().all() else np.nan
            for f in STOCK_FIELDS:
                row[f] = last[f]
            rows.append(row)
        a = g[g["period_type"] == "annual"].sort_values("period_end")
        for _, last in a.iterrows():
            row = {"ticker": ticker, "period_end": last["period_end"], "available_date": last["available_date"],
                   "method": "annual", "fiscal_year": last["fiscal_year"],
                   "reported_currency": last["reported_currency"]}
            row.update({f: last[f] for f in FLOW_FIELDS + STOCK_FIELDS})
            rows.append(row)
    if not rows:
        return pd.DataFrame(columns=["ticker", "period_end", "available_date", "method", "fiscal_year",
                                     "reported_currency", *FLOW_FIELDS, *STOCK_FIELDS])
    ttm = pd.DataFrame(rows)
    # Pour une même date de disponibilité, la somme de trimestres est préférée à l'annuel
    ttm["_rank"] = (ttm["method"] != "sum_4_quarters").astype(int)
    ttm = ttm.sort_values(["ticker", "available_date", "_rank"]).drop_duplicates(["ticker", "period_end", "method"])
    return ttm.drop(columns="_rank").reset_index(drop=True)


def ttm_as_of(ttm: pd.DataFrame, ticker: str, as_of: pd.Timestamp | None = None) -> pd.Series | None:
    """Dernier TTM disponible à la date `as_of` (préférence aux trimestres, puis au plus récent)."""
    t = ttm[ttm["ticker"] == ticker]
    if as_of is not None:
        t = t[t["available_date"] <= as_of]
    if t.empty:
        return None
    t = t.assign(_q=(t["method"] == "sum_4_quarters").astype(int))
    latest_end = t["period_end"].max()
    best = t[t["period_end"] >= latest_end - pd.Timedelta(days=100)].sort_values(["_q", "period_end"])
    return best.iloc[-1].drop(labels="_q")


def build_market_cap(store: Store, statements: pd.DataFrame) -> pd.DataFrame:
    prices = store.read("silver", "prices_daily")
    if prices.empty:
        return pd.DataFrame(columns=["ticker", "date", "market_cap", "source"])
    px = pd.DataFrame({"ticker": prices["symbol"], "date": pd.to_datetime(prices["date"]), "close": prices["close"]})
    fmp = store.read("silver", "market_cap_history")
    out = []
    for ticker, p in px.groupby("ticker"):
        p = p.sort_values("date")
        m = fmp[fmp["symbol"] == ticker] if not fmp.empty else pd.DataFrame()
        if not m.empty and "market_cap" in m.columns:
            mc = pd.DataFrame({"ticker": ticker, "date": pd.to_datetime(m["date"]), "market_cap": m["market_cap"],
                               "source": "fmp_historical_market_cap"})
            out.append(mc.dropna(subset=["market_cap"]).sort_values("date"))
            continue
        # Approximation : cours × actions diluées du dernier état publié à la date (point-in-time)
        st = statements[(statements["ticker"] == ticker) & statements["shares_diluted"].notna()]
        if st.empty:
            continue
        shares = st[["available_date", "shares_diluted"]].sort_values("available_date")
        merged = pd.merge_asof(p, shares, left_on="date", right_on="available_date", direction="backward")
        merged = merged.dropna(subset=["shares_diluted"])
        out.append(pd.DataFrame({"ticker": ticker, "date": merged["date"],
                                 "market_cap": merged["close"] * merged["shares_diluted"],
                                 "source": "approx_close_x_diluted_shares"}))
    if not out:
        return pd.DataFrame(columns=["ticker", "date", "market_cap", "source"])
    return pd.concat(out, ignore_index=True).drop_duplicates(["ticker", "date"], keep="last")


def build_segments(store: Store) -> pd.DataFrame:
    rows = []
    for kind, table in (("product", "revenue_segments_product"), ("geography", "revenue_segments_geo")):
        df = store.read("silver", table)
        if df.empty or "data" not in df.columns:
            continue
        for r in df.itertuples():
            try:
                data = json.loads(r.data) if isinstance(r.data, str) else (r.data or {})
            except ValueError:
                continue
            for seg, value in data.items():
                rows.append({"ticker": r.symbol, "fiscal_year": getattr(r, "fiscal_year", None),
                             "period_end": r.date, "segment_type": kind, "segment": seg,
                             "revenue": pd.to_numeric(value, errors="coerce"), "source": SOURCE})
    return pd.DataFrame(rows)


def build_peers(store: Store, settings: Settings, tickers: list[str]) -> pd.DataFrame:
    rows = []
    groups = (settings.section("universe").get("peer_groups") or {})
    for group, members in groups.items():
        for t in members:
            for p in members:
                if p != t:
                    rows.append({"ticker": t, "peer": p, "source": f"config:{group}"})
    configured = {r["ticker"] for r in rows}
    fmp = store.read("silver", "stock_peers")
    if not fmp.empty and "query_symbol" in fmp.columns:
        for r in fmp.itertuples():
            if r.query_symbol not in configured:
                rows.append({"ticker": r.query_symbol, "peer": r.symbol, "source": "fmp_stock_peers"})
    peers = pd.DataFrame(rows, columns=["ticker", "peer", "source"]).drop_duplicates(["ticker", "peer"])
    peers = peers[peers["ticker"].isin(tickers)] if tickers else peers
    peers["peer_in_universe"] = peers["peer"].isin(tickers)
    return peers


def build_companies(store: Store) -> pd.DataFrame:
    prof = store.read("silver", "profile")
    if prof.empty:
        return pd.DataFrame(columns=["ticker"])

    def c(name: str) -> pd.Series:
        return prof[name] if name in prof.columns else pd.Series(None, index=prof.index)

    return pd.DataFrame({
        "ticker": prof["symbol"], "name": c("company_name"), "exchange": c("exchange"),
        "exchange_full_name": c("exchange_full_name"), "sector": c("sector"), "industry": c("industry"),
        "country": c("country"), "trading_currency": c("currency"), "description": c("description"),
        "website": c("website"), "ceo": c("ceo"), "employees": pd.to_numeric(c("full_time_employees"),
                                                                            errors="coerce"),
        "ipo_date": c("ipo_date"), "image": c("image"), "cik": c("cik"), "isin": c("isin"),
        "source": SOURCE, "retrieved_at": c("_fetched_at"),
    })


def _restrict(df: pd.DataFrame, tickers: list[str]) -> pd.DataFrame:
    """Ne garde que l'univers courant (le silver peut contenir des tickers retirés de la configuration)."""
    if df.empty or not tickers:
        return df
    col = "ticker" if "ticker" in df.columns else "symbol" if "symbol" in df.columns else None
    return df[df[col].isin(tickers)].reset_index(drop=True) if col else df


def build_core(settings: Settings, store: Store, tickers: list[str]) -> dict[str, int]:
    statements = _restrict(build_statements(store, settings), tickers)
    tables = {
        "companies": _restrict(build_companies(store), tickers),
        "financial_statements": statements,
        "ttm": build_ttm(statements),
        "market_cap_daily": _restrict(build_market_cap(store, statements), tickers),
        "revenue_segments": _restrict(build_segments(store), tickers),
        "peers": build_peers(store, settings, tickers),
    }
    prices = _restrict(store.read("silver", "prices_daily"), tickers)
    if not prices.empty:
        tables["prices_daily"] = pd.DataFrame({
            "ticker": prices["symbol"], "date": pd.to_datetime(prices["date"]), "close": prices["close"],
            "volume": prices.get("volume"), "source": SOURCE})
    counts = {}
    for name, df in tables.items():
        store.write("core", name, df)
        counts[name] = len(df)
        log.info("core.%-22s %7d lignes", name, len(df))
    return counts
