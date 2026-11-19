"""Core -> gold.kpis : les 7 KPIs fondamentaux par société (format long, un KPI par ligne)."""

from __future__ import annotations

import logging

import pandas as pd

from ..config import Settings
from ..core.normalize import ttm_as_of
from ..financials.kpis import compute_kpis
from ..storage import Store, utcnow

log = logging.getLogger(__name__)


def build_kpis(settings: Settings, store: Store) -> int:
    st = store.read("core", "financial_statements")
    ttm = store.read("core", "ttm")
    if st.empty:
        store.write("gold", "kpis", pd.DataFrame(columns=["ticker", "kpi_id"]))
        return 0
    cfg = settings.section("kpis")
    rows = []
    for ticker, g in st.groupby("ticker"):
        annual = g[g["period_type"] == "annual"]
        latest = g.sort_values("period_end").iloc[-1]
        last_ttm = ttm_as_of(ttm, ticker) if not ttm.empty else None
        for k in compute_kpis(annual, last_ttm, latest, cfg):
            rows.append(k.as_row(ticker) | {"balance_date": latest["period_end"]})
    df = pd.DataFrame(rows)
    df["computed_at"] = utcnow().replace(tzinfo=None)
    store.write("gold", "kpis", df)
    log.info("gold.kpis : %d lignes (%d sociétés)", len(df), df["ticker"].nunique())
    return len(df)
