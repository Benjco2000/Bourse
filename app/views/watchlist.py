import streamlit as st
from lib import data, ui

data.demo_banner()
st.title("Ma watchlist")

sc = data.screener()
wl = data.get_watchlist()
labels = {r.symbol: f"{r.symbol} — {r.name}" for r in sc.itertuples()}

new = st.multiselect("Actions suivies", options=sorted(labels), default=[s for s in wl if s in labels],
                     format_func=lambda s: labels.get(s, s), placeholder="Ajoutez des actions...")
if sorted(new) != sorted(wl):
    data.set_watchlist(new)
    st.rerun()

if not new:
    st.info("Votre watchlist est vide. Ajoutez des actions ci-dessus ou depuis la fiche d'une action.",
            icon=":material/star:")
else:
    cols = ["symbol", "name", "price", "change_pct", "fair_value", "margin_of_safety", "verdict", "graham_score",
            "score_overall", "dividend_yield"]
    ui.stock_table(sc[sc["symbol"].isin(new)][cols], ui.SCREENER_COLUMNS, key="wl_table")
st.caption("La watchlist est enregistrée dans le dossier de données (`app/watchlist.json`).")
