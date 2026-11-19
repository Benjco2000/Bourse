import pandas as pd
import streamlit as st
from lib import data

data.demo_banner()
st.title("Actualités")

scope = st.segmented_control("Source", ["Mes actions", "Marché"], default="Mes actions",
                             label_visibility="collapsed")
if scope == "Marché":
    news = data.table("silver", "general_news")
else:
    news = data.table("silver", "stock_news")
    wl = data.get_watchlist()
    only_wl = st.toggle("Uniquement ma watchlist", value=False, disabled=not wl)
    if only_wl and "query_symbol" in news.columns:
        news = news[news["query_symbol"].isin(wl)]

if news.empty:
    st.caption("Aucune actualité chargée.")
else:
    search = st.text_input("Rechercher", placeholder="Mot-clé dans le titre...")
    if search:
        news = news[news["title"].str.contains(search, case=False, na=False)]
    for n in news.sort_values("published_date", ascending=False).head(60).itertuples():
        with st.container(border=True):
            tag = f"`{n.symbol}` " if scope != "Marché" and getattr(n, "symbol", None) else ""
            st.markdown(f"{tag}**[{n.title}]({n.url})**")
            st.caption(f"{getattr(n, 'publisher', '') or getattr(n, 'site', '')} · "
                       f"{pd.Timestamp(n.published_date):%d/%m/%Y %H:%M}")
