import streamlit as st
from lib import data, ui

data.demo_banner()
st.title("Screener")
st.caption("Filtrez l'univers suivi. Cliquez sur une ligne pour ouvrir la fiche de l'action.")

sc = data.screener()

with st.container(border=True):
    c1, c2, c3 = st.columns(3)
    sectors = sorted(sc["sector"].dropna().unique()) if "sector" in sc else []
    sel_sectors = c1.multiselect("Secteurs", sectors, placeholder="Tous")
    verdicts = c2.multiselect("Verdict", ["Sous-évaluée", "Proche de sa juste valeur", "Surévaluée",
                                          "Non évaluable"], placeholder="Tous")
    min_graham = c3.slider("Critères de Graham minimum", 0.0, 7.0, 0.0, 0.5)
    c4, c5, c6 = st.columns(3)
    min_score = c4.slider("Note globale minimum", 0, 100, 0, 5)
    max_pe = c5.number_input("P/E maximum (0 = pas de filtre)", min_value=0.0, value=0.0, step=1.0)
    min_yield = c6.slider("Rendement du dividende minimum (%)", 0.0, 10.0, 0.0, 0.25)

f = sc.copy()
if sel_sectors:
    f = f[f["sector"].isin(sel_sectors)]
if verdicts:
    f = f[f["verdict"].isin(verdicts)]
f = f[f["graham_score"].fillna(0) >= min_graham]
f = f[f["score_overall"].fillna(0) >= min_score]
if max_pe:
    f = f[(f["pe"] > 0) & (f["pe"] <= max_pe)]
if min_yield:
    f = f[f["dividend_yield"].fillna(0) * 100 >= min_yield]

view = st.segmented_control("Colonnes", ["Valorisation", "Notes", "Qualité"], default="Valorisation",
                            label_visibility="collapsed")
columns = {
    "Valorisation": ["symbol", "name", "sector", "price", "change_pct", "market_cap", "pe", "pb", "dividend_yield",
                     "fair_value", "margin_of_safety", "verdict", "graham_score"],
    "Notes": ["symbol", "name", "score_overall", "score_valuation", "score_profitability", "score_growth",
              "score_health", "score_dividend"],
    "Qualité": ["symbol", "name", "roe", "roic", "net_margin", "revenue_cagr_5y", "debt_to_equity", "altman_z",
                "piotroski"],
}[view or "Valorisation"]

st.caption(f"{len(f)} action(s)")
f = f.sort_values("margin_of_safety", ascending=False, na_position="last")
ui.stock_table(f[[c for c in columns if c in f.columns]], ui.SCREENER_COLUMNS, key=f"screener_{view}", height=600)
st.download_button("Exporter (CSV)", f.to_csv(index=False).encode(), "screener.csv", icon=":material/download:")
