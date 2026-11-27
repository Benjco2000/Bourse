"""Core -> gold.valuation_history : multiples de capitalisation quotidiens, point-in-time.

Pour chaque jour t : capitalisation(t) / dernier agrégat TTM **publié au plus tard à t**.
- Les TTM trimestriels (somme de 4 trimestres) sont préférés ; un TTM annuel n'est utilisé que s'il n'existe
  pas de TTM trimestriel pour la même fin de période (historique ancien, plan sans trimestriel).
- Un TTM dont la période s'est terminée plus de 15 mois avant t est jugé périmé : multiple non calculé.
- Dénominateur ≤ 0 : multiple non calculé (NaN), jamais de valeur artificielle.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from ..storage import Store, merge_asof

log = logging.getLogger(__name__)

MULTIPLES = {
    "p_fcf": "free_cash_flow",
    "p_ocf": "operating_cash_flow",
    "p_net_income": "net_income",
    "p_gross_profit": "gross_profit",
    "p_sales": "revenue",
}
MAX_TTM_AGE_DAYS = 460


def ttm_timeline(ttm: pd.DataFrame) -> pd.DataFrame:
    """TTM d'une société, une ligne par fin de période (trimestriel prioritaire), trié par disponibilité."""
    q = ttm[ttm["method"] == "sum_4_quarters"]
    a = ttm[ttm["method"] != "sum_4_quarters"]
    if not q.empty and not a.empty:
        q_ends = q["period_end"].to_numpy()
        keep = [not (np.abs((q_ends - end).astype("timedelta64[D]").astype(int)) <= 10).any()
                for end in a["period_end"].to_numpy()]
        a = a[keep]
    out = pd.concat([q, a], ignore_index=True)
    return out.sort_values(["available_date", "period_end"]).drop_duplicates("available_date", keep="last")


def build_valuation_history(store: Store) -> pd.DataFrame:
    mc = store.read("core", "market_cap_daily")
    ttm = store.read("core", "ttm")
    cols = ["ticker", "date", "market_cap", "market_cap_source", "ttm_period_end", "ttm_method",
            *MULTIPLES.values(), *MULTIPLES]
    if mc.empty or ttm.empty:
        store.write("gold", "valuation_history", pd.DataFrame(columns=cols))
        return pd.DataFrame(columns=cols)
    frames = []
    for ticker, m in mc.groupby("ticker"):
        t = ttm[ttm["ticker"] == ticker]
        if t.empty:
            continue
        timeline = ttm_timeline(t)[["available_date", "period_end", "method", *MULTIPLES.values()]]
        m = m.sort_values("date")
        merged = merge_asof(m, timeline, left_on="date", right_on="available_date", direction="backward")
        stale = (merged["date"] - merged["period_end"]).dt.days > MAX_TTM_AGE_DAYS
        merged.loc[stale, list(MULTIPLES.values())] = np.nan
        for mult, denom in MULTIPLES.items():
            d = merged[denom]
            merged[mult] = (merged["market_cap"] / d).where((d > 0) & (merged["market_cap"] > 0))
        merged = merged.rename(columns={"period_end": "ttm_period_end", "method": "ttm_method",
                                        "source": "market_cap_source"})
        frames.append(merged[cols])
    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=cols)
    float_cols = [*MULTIPLES.values(), *MULTIPLES]
    df[float_cols] = df[float_cols].astype("float64")
    store.write("gold", "valuation_history", df)
    log.info("gold.valuation_history : %d lignes", len(df))
    return df
