"""Point d'entrée de l'application : `streamlit run app/streamlit_app.py`."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "src", Path(__file__).resolve().parent):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import streamlit as st  # noqa: E402
from lib import data  # noqa: E402

st.set_page_config(page_title="Bourse — analyse à la Graham", page_icon=":material/monitoring:", layout="wide")

pages = {
    "Marchés": [
        st.Page("views/home.py", title="Vue d'ensemble", icon=":material/public:", default=True),
        st.Page("views/stock.py", title="Analyse d'une action", icon=":material/query_stats:", url_path="action"),
        st.Page("views/screener.py", title="Screener", icon=":material/filter_alt:", url_path="screener"),
        st.Page("views/watchlist.py", title="Ma watchlist", icon=":material/star:", url_path="watchlist"),
    ],
    "Informations": [
        st.Page("views/news.py", title="Actualités", icon=":material/newspaper:", url_path="actualites"),
        st.Page("views/earnings.py", title="Calendrier des résultats", icon=":material/event:",
                url_path="calendrier"),
    ],
    "Administration": [
        st.Page("views/pipeline.py", title="Données & pipeline", icon=":material/database:", url_path="donnees"),
    ],
}

nav = st.navigation(pages)

with st.sidebar:
    screener = data.screener()
    if not screener.empty:
        options = screener.sort_values("symbol")
        labels = {r.symbol: f"{r.symbol} — {r.name}" for r in options.itertuples()}
        choice = st.selectbox("Rechercher une action", options=list(labels), index=None,
                              format_func=lambda s: labels.get(s, s), placeholder="Ticker ou nom...")
        if choice and choice != st.session_state.get("_last_search"):
            st.session_state["_last_search"] = choice
            st.session_state["symbol"] = choice
            st.session_state["_nav_symbol"] = True
            st.switch_page("views/stock.py")
    last = data.last_update()
    st.caption(f"Dernière mise à jour : {last:%d/%m/%Y %H:%M} UTC" if last is not None else "Aucune donnée chargée")
    st.caption("Données : Financial Modeling Prep. Ceci n'est pas un conseil en investissement.")

if screener.empty:
    st.title("Bourse")
    st.info(
        "Aucune donnée disponible pour l'instant.\n\n"
        "1. Renseignez votre clé dans `.env` (`FMP_API_KEY=...`) puis lancez `bourse run`, **ou**\n"
        "2. testez avec des données synthétiques : `bourse demo`.",
        icon=":material/info:",
    )
    st.stop()

nav.run()
