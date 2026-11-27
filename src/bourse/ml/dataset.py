"""Dataset ML point-in-time : une observation = (société, fin de trimestre t).

Features : les 7 KPIs de la page « Vue globale », recalculés avec la même fonction (`compute_kpis`) mais en
ne gardant que les états financiers PUBLIÉS au plus tard à t (anti look-ahead), + secteur et tranche de
capitalisation à t. Cible : P/FCF observé à t = capitalisation(t) / FCF TTM publié à t (FCF > 0).
"""

from __future__ import annotations

import json
import logging
import math
import os
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from ..config import Settings
from ..core.normalize import ttm_as_of
from ..financials.kpis import compute_kpis
from ..financials.metrics import cap_bucket
from ..storage import Store, merge_asof, utcnow

log = logging.getLogger(__name__)

# Colonnes nécessaires aux KPIs (réduire la largeur des tables divise le temps de calcul)
ST_COLS = ["ticker", "period_type", "period_end", "available_date", "fiscal_year", "revenue", "cost_of_revenue",
           "gross_profit", "free_cash_flow", "shares_diluted", "ebit", "income_tax", "pretax_income",
           "total_assets", "current_liabilities", "total_debt", "cash_and_equivalents", "short_term_investments"]
TTM_COLS = ["ticker", "period_end", "available_date", "method", "fiscal_year", "reported_currency", "revenue",
            "gross_profit", "cost_of_revenue", "free_cash_flow", "shares_diluted"]

KPI_FEATURES = ["revenue_cagr_5y", "fcf_cagr_5y", "share_change_5y", "roce_avg_5y", "net_debt_to_fcf",
                "gross_margin_ttm", "fcf_margin_ttm"]
CAT_FEATURES = ["sector", "cap_bucket"]


@dataclass
class DatasetReport:
    n_candidates: int = 0
    n_rows: int = 0
    exclusions: dict[str, int] = field(default_factory=dict)

    def exclude(self, reason: str) -> None:
        self.exclusions[reason] = self.exclusions.get(reason, 0) + 1


def observation_dates(start: str, end: pd.Timestamp) -> list[pd.Timestamp]:
    return list(pd.date_range(pd.Timestamp(start), end, freq="QE"))


def ttm_at(ttm: pd.DataFrame, ticker: str, t: pd.Timestamp, max_ttm_age_days: int = 460) -> pd.Series | None:
    row = ttm_as_of(ttm, ticker, t) if not ttm.empty else None
    if row is not None and (t - pd.Timestamp(row["period_end"])).days > max_ttm_age_days:
        return None
    return row


_UNSET = object()


def features_as_of(annual: pd.DataFrame, statements: pd.DataFrame, ttm: pd.DataFrame, ticker: str,
                   t: pd.Timestamp, kpi_cfg: dict[str, Any], max_ttm_age_days: int = 460,
                   ttm_row: Any = _UNSET) -> tuple[dict[str, float], pd.Series | None]:
    """Les 7 KPIs tels qu'ils étaient calculables à la date t. Retourne (features, ttm utilisé)."""
    a = annual[annual["available_date"] <= t]
    known = statements[statements["available_date"] <= t]
    if ttm_row is _UNSET:
        ttm_row = ttm_at(ttm, ticker, t, max_ttm_age_days)
    latest = known.sort_values("period_end").iloc[-1] if not known.empty else None
    feats: dict[str, float] = {}
    for k in compute_kpis(a, ttm_row, latest, kpi_cfg):
        unreliable = k.short_history or (k.kpi_id == "share_change_5y" and k.note and "split" in k.note)
        feats[k.kpi_id] = np.nan if (k.value is None or unreliable) else float(k.value)
    return feats, ttm_row


def _ticker_rows(job: tuple) -> tuple[list[dict[str, Any]], dict[str, int], int]:
    """Observations d'une société (fonction de niveau module : exécutable dans un processus séparé)."""
    ticker, st_t, mc_t, ttm_t, dates, sector, kpi_cfg, buckets, lo, hi, max_age = job
    excl: dict[str, int] = {}

    def exclude(reason: str, n: int = 1) -> None:
        excl[reason] = excl.get(reason, 0) + n

    if mc_t is None or mc_t.empty:
        exclude("capitalisation indisponible (société)", len(dates))
        return [], excl, len(dates)
    annual = st_t[st_t["period_type"] == "annual"].sort_values("fiscal_year")
    caps = merge_asof(pd.DataFrame({"t": dates}),
                         mc_t[["date", "market_cap"]].sort_values("date").rename(columns={"date": "d"}),
                         left_on="t", right_on="d", direction="backward", tolerance=pd.Timedelta(days=7))
    rows = []
    for t, cap in zip(caps["t"], caps["market_cap"], strict=True):
        if pd.isna(cap):
            exclude("capitalisation indisponible à la date")
            continue
        ttm_row = ttm_at(ttm_t, ticker, t, max_age)  # contrôles bon marché avant le calcul des KPIs
        if ttm_row is None:
            exclude("TTM indisponible ou périmé à la date")
            continue
        fcf = ttm_row.get("free_cash_flow")
        if fcf is None or pd.isna(fcf) or fcf <= 0:
            exclude("FCF TTM ≤ 0 (cible non définie)")
            continue
        pfcf = cap / fcf
        if not (lo <= pfcf <= hi):
            exclude(f"P/FCF hors [{lo:g} ; {hi:g}]")
            continue
        feats, _ = features_as_of(annual, st_t, ttm_t, ticker, t, kpi_cfg, max_age, ttm_row=ttm_row)
        if sum(not math.isnan(v) for v in feats.values()) < 4:
            exclude("moins de 4 KPIs calculables")
            continue
        rows.append({"ticker": ticker, "date": t, **feats, "sector": sector, "cap_bucket": cap_bucket(cap, buckets),
                     "market_cap": cap, "fcf_ttm": float(fcf), "ttm_period_end": ttm_row["period_end"],
                     "ttm_method": ttm_row["method"], "pfcf": pfcf, "target": math.log(pfcf)})
    return rows, excl, len(dates)


def build_dataset(settings: Settings, store: Store, end: pd.Timestamp | None = None
                  ) -> tuple[pd.DataFrame, DatasetReport]:
    ml = settings.section("ml")
    kpi_cfg = settings.section("kpis")
    buckets = settings.section("cap_buckets") or {}
    tcfg = ml.get("target", {})
    lo, hi = float(tcfg.get("min_pfcf", 1)), float(tcfg.get("max_pfcf", 150))
    max_age = int(ml.get("observations", {}).get("max_ttm_age_days", 460))

    st = store.read("core", "financial_statements")
    ttm = store.read("core", "ttm")
    mc = store.read("core", "market_cap_daily", columns=["ticker", "date", "market_cap"])
    companies = store.read("core", "companies")
    st = st[[c for c in ST_COLS if c in st.columns]]
    ttm = ttm[[c for c in TTM_COLS if c in ttm.columns]]
    report = DatasetReport()
    if st.empty:
        raise ValueError("Aucun état financier dans le lac ML : lancez `bourse ml ingest`.")
    if mc.empty:
        raise ValueError("Aucune capitalisation historique dans le lac ML (jeu `market_cap_history`) : "
                         "relancez `bourse ml ingest` pour compléter le chargement.")
    sectors = dict(zip(companies.get("ticker", []), companies.get("sector", []), strict=False))
    end = end or mc["date"].max()
    dates = observation_dates(ml.get("observations", {}).get("start", "2012-12-31"), end)

    mc_by = {t: g for t, g in mc[["ticker", "date", "market_cap"]].groupby("ticker")}
    ttm_by = {t: g for t, g in ttm.groupby("ticker")} if not ttm.empty else {}
    jobs = [(ticker, st_t, mc_by.get(ticker), ttm_by.get(ticker, ttm.iloc[0:0]), dates,
             sectors.get(ticker) or "Inconnu", kpi_cfg, buckets, lo, hi, max_age)
            for ticker, st_t in st.groupby("ticker")]
    n_jobs = int(ml.get("n_jobs", 0)) or max(1, (os.cpu_count() or 2) - 1)
    rows: list[dict[str, Any]] = []
    results = []
    if n_jobs > 1 and len(jobs) > 20:
        try:
            with ProcessPoolExecutor(max_workers=n_jobs) as pool:
                for i, res in enumerate(pool.map(_ticker_rows, jobs, chunksize=8), 1):
                    results.append(res)
                    if i % 100 == 0:
                        log.info("  dataset : %d/%d sociétés", i, len(jobs))
        except (OSError, RuntimeError) as exc:  # environnement sans multiprocessing : repli séquentiel
            log.warning("Calcul parallèle impossible (%s) : passage en séquentiel", exc)
            results = []
    if not results:
        for i, job in enumerate(jobs, 1):
            results.append(_ticker_rows(job))
            if i % 100 == 0:
                log.info("  dataset : %d/%d sociétés", i, len(jobs))
    for ticker_rows, excl, n_cand in results:
        rows += ticker_rows
        report.n_candidates += n_cand
        for reason, n in excl.items():
            report.exclusions[reason] = report.exclusions.get(reason, 0) + n
    df = pd.DataFrame(rows)
    report.n_rows = len(df)
    return df, report


def write_dataset(settings: Settings, store: Store) -> tuple[pd.DataFrame, dict[str, Any]]:
    df, report = build_dataset(settings, store)
    from ..ingestion import resolve_universe

    expected = len(resolve_universe(settings, store))
    got = int(df["ticker"].nunique()) if not df.empty else 0
    if expected >= 50 and got < 0.5 * expected or expected < 50 and got < 50:
        log.warning("Le dataset ne couvre que %d sociétés (univers configuré : %d). Si vous avez ingéré l'univers "
                    "élargi avec une version précédente (lac data/ml), lancez `bourse ml merge-lake` puis "
                    "`bourse ml dataset`.", got, expected)
    card = {
        "built_at": utcnow().isoformat(), "n_rows": report.n_rows, "n_candidates": report.n_candidates,
        "exclusions": report.exclusions,
        "n_companies": int(df["ticker"].nunique()) if not df.empty else 0,
        "period": [str(df["date"].min().date()), str(df["date"].max().date())] if not df.empty else None,
        "sectors": df["sector"].value_counts().to_dict() if not df.empty else {},
        "cap_buckets": df["cap_bucket"].value_counts().to_dict() if not df.empty else {},
        "features": KPI_FEATURES + CAT_FEATURES, "target": "log(P/FCF)",
        "missing_rate": df[KPI_FEATURES].isna().mean().round(3).to_dict() if not df.empty else {},
    }
    store.write("gold", "ml_dataset", df)
    store.write_json("gold", "ml_dataset_card", card)
    log.info("Dataset ML : %d observations, %d sociétés, exclusions : %s", card["n_rows"], card["n_companies"],
             json.dumps(report.exclusions, ensure_ascii=False))
    return df, card
