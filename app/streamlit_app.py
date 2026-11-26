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
    "Analyse": [
        st.Page("views/overview.py", title="Vue globale", icon=":material/dashboard:", default=True),
        st.Page("views/valuation.py", title="Historique de valorisation", icon=":material/show_chart:",
                url_path="valorisation"),
        st.Page("views/news_sentiment.py", title="Actualités & sentiment", icon=":material/newspaper:",
                url_path="actualites"),
        st.Page("views/ml.py", title="Valorisation IA", icon=":material/model_training:", url_path="valorisation-ia"),
        st.Page("views/cash.py", title="Cash & actionnaires", icon=":material/payments:", url_path="cash"),
    ],
    "Bonus": [
        st.Page("views/home.py", title="Marché", icon=":material/public:", url_path="marche"),
        st.Page("views/stock.py", title="Analyse Graham", icon=":material/query_stats:", url_path="graham"),
        st.Page("views/screener.py", title="Screener", icon=":material/filter_alt:", url_path="screener"),
        st.Page("views/watchlist.py", title="Ma watchlist", icon=":material/star:", url_path="watchlist"),
        st.Page("views/news.py", title="Flux d'actualités", icon=":material/feed:", url_path="flux-actualites"),
        st.Page("views/earnings.py", title="Calendrier des résultats", icon=":material/event:",
                url_path="calendrier"),
    ],
    "Administration": [
        st.Page("views/pipeline.py", title="Données & pipeline", icon=":material/database:", url_path="donnees"),
    ],
}

nav = st.navigation(pages)
companies = data.companies()

with st.sidebar:
    if not companies.empty:
        tickers = companies["ticker"].tolist()
        labels = {r.ticker: f"{r.ticker} — {r.name}" for r in companies.itertuples()}
        qp = (st.query_params.get("symbol") or "").upper()
        if qp in tickers and not st.session_state.pop("_nav_symbol", False):
            st.session_state["symbol"] = qp
        current = st.session_state.get("symbol")
        current = current if current in tickers else tickers[0]
        sym = st.selectbox("Société", tickers, index=tickers.index(current), format_func=lambda s: labels.get(s, s),
                           help="Recherche par ticker ou par nom : tapez quelques lettres.")
        st.session_state["symbol"] = sym
        if st.query_params.get("symbol") != sym:
            st.query_params["symbol"] = sym
    last = data.last_update()
    st.caption(f"Dernière mise à jour : {last:%d/%m/%Y %H:%M} UTC" if last is not None else "Aucune donnée chargée")
    st.caption("Données : Financial Modeling Prep. Ceci n'est pas un conseil en investissement.")

if companies.empty:
    st.title("Bourse")
    st.info(
        "Aucune donnée disponible pour l'instant.\n\n"
        "1. Renseignez votre clé dans `.env` (`FMP_API_KEY=...`) puis lancez `bourse run`, **ou**\n"
        "2. testez avec des données synthétiques : `bourse demo`.",
        icon=":material/info:",
    )
    st.stop()

nav.run()
