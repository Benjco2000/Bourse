"""Dataset ML point-in-time : une observation = (société, fin de trimestre t).

Features : les 7 KPIs de la page « Vue globale », recalculés avec la même fonction (`compute_kpis`) mais en
ne gardant que les états financiers PUBLIÉS au plus tard à t (anti look-ahead), + secteur et tranche de
capitalisation à t. Cible : P/FCF observé à t = capitalisation(t) / FCF TTM publié à t (FCF > 0).
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from ..config import Settings
from ..core.normalize import ttm_as_of
from ..financials.kpis import compute_kpis
from ..financials.metrics import cap_bucket
from ..storage import Store, utcnow

log = logging.getLogger(__name__)

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


def features_as_of(annual: pd.DataFrame, statements: pd.DataFrame, ttm: pd.DataFrame, ticker: str,
                   t: pd.Timestamp, kpi_cfg: dict[str, Any], max_ttm_age_days: int = 460
                   ) -> tuple[dict[str, float], pd.Series | None]:
    """Les 7 KPIs tels qu'ils étaient calculables à la date t. Retourne (features, ttm utilisé)."""
    a = annual[annual["available_date"] <= t]
    known = statements[statements["available_date"] <= t]
    ttm_row = ttm_as_of(ttm, ticker, t) if not ttm.empty else None
    if ttm_row is not None and (t - pd.Timestamp(ttm_row["period_end"])).days > max_ttm_age_days:
        ttm_row = None
    latest = known.sort_values("period_end").iloc[-1] if not known.empty else None
    feats: dict[str, float] = {}
    for k in compute_kpis(a, ttm_row, latest, kpi_cfg):
        unreliable = k.short_history or (k.kpi_id == "share_change_5y" and k.note and "split" in k.note)
        feats[k.kpi_id] = np.nan if (k.value is None or unreliable) else float(k.value)
    return feats, ttm_row


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
    mc = store.read("core", "market_cap_daily")
    companies = store.read("core", "companies")
    report = DatasetReport()
    if st.empty or mc.empty:
        return pd.DataFrame(), report
    sectors = dict(zip(companies.get("ticker", []), companies.get("sector", []), strict=False))
    end = end or mc["date"].max()
    dates = observation_dates(ml.get("observations", {}).get("start", "2012-12-31"), end)

    rows = []
    for ticker, st_t in st.groupby("ticker"):
        mc_t = mc[mc["ticker"] == ticker].sort_values("date")
        if mc_t.empty:
            report.exclude("capitalisation indisponible (société)")
            continue
        ttm_t = ttm[ttm["ticker"] == ticker]
        annual = st_t[st_t["period_type"] == "annual"].sort_values("fiscal_year")
        caps = pd.merge_asof(pd.DataFrame({"t": dates}), mc_t[["date", "market_cap"]].rename(columns={"date": "d"}),
                             left_on="t", right_on="d", direction="backward",
                             tolerance=pd.Timedelta(days=7))
        for t, cap in zip(caps["t"], caps["market_cap"], strict=True):
            report.n_candidates += 1
            if pd.isna(cap):
                report.exclude("capitalisation indisponible à la date")
                continue
            feats, ttm_row = features_as_of(annual, st_t, ttm_t, ticker, t, kpi_cfg, max_age)
            if ttm_row is None:
                report.exclude("TTM indisponible ou périmé à la date")
                continue
            fcf = ttm_row.get("free_cash_flow")
            if fcf is None or pd.isna(fcf) or fcf <= 0:
                report.exclude("FCF TTM ≤ 0 (cible non définie)")
                continue
            pfcf = cap / fcf
            if not (lo <= pfcf <= hi):
                report.exclude(f"P/FCF hors [{lo:g} ; {hi:g}]")
                continue
            if sum(not math.isnan(v) for v in feats.values()) < 4:
                report.exclude("moins de 4 KPIs calculables")
                continue
            rows.append({"ticker": ticker, "date": t, **feats,
                         "sector": sectors.get(ticker) or "Inconnu", "cap_bucket": cap_bucket(cap, buckets),
                         "market_cap": cap, "fcf_ttm": float(fcf), "ttm_period_end": ttm_row["period_end"],
                         "ttm_method": ttm_row["method"], "pfcf": pfcf, "target": math.log(pfcf)})
    df = pd.DataFrame(rows)
    report.n_rows = len(df)
    return df, report


def write_dataset(settings: Settings, store: Store) -> tuple[pd.DataFrame, dict[str, Any]]:
    df, report = build_dataset(settings, store)
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
