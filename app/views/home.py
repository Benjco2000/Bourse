import pandas as pd
import streamlit as st
from lib import data, fmt, ui

data.demo_banner()
st.title("Vue d'ensemble")

sc = data.screener()
c1, c2, c3, c4 = st.columns(4)
c1.metric("Actions suivies", len(sc))
c2.metric("Sous-évaluées", int((sc["verdict"] == "Sous-évaluée").sum()))
c3.metric("Surévaluées", int((sc["verdict"] == "Surévaluée").sum()))
c4.metric("Note globale médiane", fmt.num(sc["score_overall"].median(), 0) + " / 100")

cols = ["symbol", "name", "price", "fair_value", "margin_of_safety", "graham_score", "score_overall"]
left, right = st.columns(2)
with left:
    st.subheader("Plus fortes marges de sécurité")
    st.caption("Écart entre la juste valeur composite et le cours — cliquez sur une ligne pour ouvrir la fiche.")
    top = sc.dropna(subset=["margin_of_safety"]).sort_values("margin_of_safety", ascending=False).head(10)
    ui.stock_table(top[cols], ui.SCREENER_COLUMNS, key="top_mos")
with right:
    st.subheader("Meilleures notes globales")
    st.caption("Moyenne des notes valorisation, rentabilité, croissance, santé financière et dividende.")
    best = sc.dropna(subset=["score_overall"]).sort_values("score_overall", ascending=False).head(10)
    ui.stock_table(best[cols], ui.SCREENER_COLUMNS, key="top_score")

st.divider()
st.subheader("Mouvements du marché (US)")
movers = {"Plus fortes hausses": "market_gainers", "Plus fortes baisses": "market_losers",
          "Plus actives": "market_actives"}
mcols = st.columns(3)
for col, (title, table) in zip(mcols, movers.items(), strict=True):
    with col:
        st.markdown(f"**{title}**")
        df = data.table("silver", table)
        if df.empty:
            st.caption("Non disponible (plan FMP ou données non chargées).")
            continue
        change = "changes_percentage" if "changes_percentage" in df.columns else "change_percentage"
        show = pd.DataFrame({"Ticker": df["symbol"], "Nom": df.get("name"), "Cours": df.get("price"),
                             "Var. %": df.get(change)}).head(10)
        st.dataframe(show, hide_index=True, width="stretch",
                     column_config={"Cours": st.column_config.NumberColumn(format="%.2f"),
                                    "Var. %": st.column_config.NumberColumn(format="%.2f %%")})

st.divider()
st.subheader("Dernières actualités")
news = data.table("silver", "general_news")
if news.empty:
    st.caption("Aucune actualité chargée.")
else:
    for r in news.sort_values("published_date", ascending=False).head(8).itertuples():
        st.markdown(f"**[{r.title}]({r.url})**  \n"
                    f"<small>{getattr(r, 'publisher', '') or getattr(r, 'site', '')} · "
                    f"{pd.Timestamp(r.published_date):%d/%m/%Y %H:%M}</small>", unsafe_allow_html=True)
