"""Page 2 — Historique de valorisation (multiples de capitalisation point-in-time)."""

import pandas as pd
import streamlit as st
from lib import charts, data, fmt

data.demo_banner()
sym = st.session_state.get("symbol") or data.companies()["ticker"].iloc[0]
hist = data.for_symbol("gold", "valuation_history", sym, symbol_col="ticker")
snap_all = data.table("gold", "company_snapshot")
snap = snap_all[snap_all["ticker"] == sym].iloc[0].to_dict() if sym in set(snap_all.get("ticker", [])) else {}

st.title(f"Historique de valorisation — {sym}")
st.caption("Multiple = capitalisation boursière du jour / dernier agrégat sur 12 mois (TTM) **effectivement publié "
           "à cette date** : aucun résultat publié ultérieurement n'est utilisé.")

if hist.empty:
    st.info("Historique indisponible : lancez `bourse run` (cours, capitalisations et états financiers requis).")
    st.stop()

hist = hist.sort_values("date")
c1, c2 = st.columns([3, 2])
period = c1.segmented_control("Période", ["1A", "3A", "5A", "10A", "Max"], default="5A", key="val_period")
years = {"1A": 1, "3A": 3, "5A": 5, "10A": 10}.get(period or "5A")
end = hist["date"].max()
view = hist[hist["date"] >= end - pd.DateOffset(years=years)] if years else hist
freq_label = "quotidienne"
if (view["date"].max() - view["date"].min()).days > 3 * 365:
    view = view.set_index("date").resample("W-FRI").last().reset_index()
    freq_label = "hebdomadaire (dernier cours de la semaine)"

approx = (view["market_cap_source"] != "fmp_historical_market_cap").mean()
ccy = snap.get("trading_currency") or ""
c2.caption(f"Fréquence : {freq_label} · devise : {ccy or 'n.d.'} · dernière donnée : {end:%d/%m/%Y}")
if approx > 0:
    st.warning(f"Capitalisation **approximée** (cours × actions diluées publiées) sur {approx:.0%} de la période : "
               "les variations du nombre d'actions entre deux publications ne sont pas captées.",
               icon=":material/info:")
annual = (view["ttm_method"] == "annual").mean()
if annual > 0:
    st.info(f"Sur {annual:.0%} de la période, le dénominateur est un **exercice annuel** (comptes trimestriels "
            "indisponibles) : le multiple ne bouge alors qu'une fois par an côté dénominateur.", icon=":material/info:")

MULTS = [("p_fcf", "Price / FCF"), ("p_ocf", "Price / OCF"), ("p_net_income", "Price / Net Income"),
         ("p_gross_profit", "Price / Gross Profit"), ("p_sales", "Price / Sales")]

summary = []
for col, label in MULTS:
    s = view[col].dropna()
    current = view[col].iloc[-1] if not view.empty else None
    pct_rank = (s <= current).mean() if pd.notna(current) and len(s) else None
    summary.append({"Multiple": label, "Actuel": f"{fmt.num(current, 1)} x" if pd.notna(current) else "N/A",
                    "Médiane": f"{fmt.num(s.median(), 1)} x" if len(s) else "N/A",
                    "Q1 – Q3": f"{fmt.num(s.quantile(.25), 1)} – {fmt.num(s.quantile(.75), 1)}" if len(s) else "N/A",
                    "Percentile actuel": fmt.pct(pct_rank, 0) if pct_rank is not None else "N/A",
                    "Non calculable": fmt.pct(1 - len(s) / len(view), 0) if len(view) else "N/A"})
st.dataframe(pd.DataFrame(summary), hide_index=True, width="stretch", column_config={
    "Percentile actuel": st.column_config.TextColumn(help="Part de la période où le multiple était inférieur ou égal "
                                                          "à sa valeur actuelle (100 % = plus haut de la période)."),
    "Non calculable": st.column_config.TextColumn(help="Part de la période sans multiple : dénominateur ≤ 0, TTM "
                                                       "indisponible ou périmé (> 15 mois)."),
})

for i in range(0, len(MULTS), 2):
    cols = st.columns(2)
    for col_box, (col, label) in zip(cols, MULTS[i:i + 2], strict=False):
        with col_box:
            st.markdown(f"**{label}**")
            st.plotly_chart(charts.multiple_history(view, col, label), width="stretch",
                            config={"displayModeBar": False}, key=f"mult_{col}")
            missing = view[col].isna().mean()
            if missing > 0:
                st.caption(f"Non calculable sur {missing:.0%} de la période (trous dans la courbe) : dénominateur "
                           "nul ou négatif, ou TTM indisponible.")

st.caption("Médiane et quartiles calculés sur la période affichée. « Price » = capitalisation boursière (pas "
           "d'Enterprise Value). Source : FMP ; calcul : pipeline Bourse (`gold.valuation_history`).")
