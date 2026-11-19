import pandas as pd
import streamlit as st
from lib import data

data.demo_banner()
st.title("Calendrier des résultats")

cal = data.table("silver", "earnings_calendar")
if cal.empty:
    st.caption("Calendrier indisponible (plan FMP ou données non chargées).")
    st.stop()

universe = set(data.screener()["symbol"])
only_universe = st.toggle("Uniquement les actions suivies", value=True)
if only_universe:
    cal = cal[cal["symbol"].isin(universe)]

today = pd.Timestamp.now().normalize()
upcoming = cal[cal["date"] >= today].sort_values("date")
past = cal[cal["date"] < today].sort_values("date", ascending=False)

cols = {"date": "Date", "symbol": "Ticker", "eps_estimated": "BPA estimé", "eps_actual": "BPA publié",
        "revenue_estimated": "CA estimé", "revenue_actual": "CA publié"}
cfg = {"Date": st.column_config.DateColumn(format="DD/MM/YYYY"),
       "BPA estimé": st.column_config.NumberColumn(format="%.2f"),
       "BPA publié": st.column_config.NumberColumn(format="%.2f"),
       "CA estimé": st.column_config.NumberColumn(format="compact"),
       "CA publié": st.column_config.NumberColumn(format="compact")}

st.subheader(f"À venir ({len(upcoming)})")
st.dataframe(upcoming[[c for c in cols if c in cal.columns]].rename(columns=cols), hide_index=True,
             width="stretch", column_config=cfg)
st.subheader("Publiés récemment")
p = past[[c for c in cols if c in cal.columns]].rename(columns=cols)
if {"BPA estimé", "BPA publié"} <= set(p.columns):
    p["Surprise BPA"] = (p["BPA publié"] / p["BPA estimé"] - 1)
    cfg["Surprise BPA"] = st.column_config.NumberColumn(format="percent")
st.dataframe(p, hide_index=True, width="stretch", column_config=cfg)
