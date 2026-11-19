"""Page 3 — Actualités et sentiment."""

import json

import pandas as pd
import streamlit as st
from lib import charts, data, fmt

data.demo_banner()
sym = st.session_state.get("symbol") or data.companies()["ticker"].iloc[0]
st.title(f"Actualités & sentiment — {sym}")
st.caption("Articles récupérés via FMP pour cette société. Le sentiment mesure le ton des médias, ce n'est pas une "
           "recommandation d'achat ou de vente.")

news = data.for_symbol("core", "news", sym, symbol_col="ticker")
if news.empty:
    st.info("Aucune actualité chargée pour cette société (lancez `bourse run` ; l'historique initial couvre "
            "jusqu'à un an selon le plan FMP).")
    st.stop()
sent = data.for_symbol("gold", "news_sentiment", sym, symbol_col="ticker")
news = news.merge(sent[["url", "score", "method"]], on="url", how="left") if not sent.empty else \
    news.assign(score=float("nan"), method=None)
synth_all = data.table("gold", "news_synthesis")
synth = synth_all[synth_all["ticker"] == sym].iloc[0].to_dict() if not synth_all.empty and \
    sym in set(synth_all["ticker"]) else {}

first, last = news["published_at"].min(), news["published_at"].max()
st.caption(f"Période réellement couverte : du {first:%d/%m/%Y} au {last:%d/%m/%Y} · "
           f"{int(news['is_primary'].sum())} articles uniques ({len(news)} avec les reprises).")


def badge(score) -> str:
    if score is None or pd.isna(score):
        return ":gray-badge[sentiment n.d.]"
    if score > 0.15:
        return f":green-badge[:material/trending_up: positif {score:+.2f}]"
    if score < -0.15:
        return f":red-badge[:material/trending_down: négatif {score:+.2f}]"
    return f":gray-badge[:material/trending_flat: neutre {score:+.2f}]"


# ------------------------------------------------------------------ filtres
f1, f2, f3, f4 = st.columns([2, 1, 1, 2])
default_start = max(first.date(), (last - pd.Timedelta(days=90)).date())
dates = f1.date_input("Période", (default_start, last.date()), min_value=first.date(), max_value=last.date(),
                      format="DD/MM/YYYY")
order = f2.selectbox("Tri", ["Date", "Pertinence"])
dedup = f3.toggle("Sans reprises", value=True, help="Regroupe les articles au titre identique publiés à moins "
                                                            "de 3 jours d'intervalle.")
query = f4.text_input("Rechercher dans les titres", placeholder="ex. guidance, lawsuit…")

view = news.copy()
if isinstance(dates, tuple) and len(dates) == 2:
    view = view[(view["published_at"].dt.date >= dates[0]) & (view["published_at"].dt.date <= dates[1])]
if dedup:
    view = view[view["is_primary"]]
if query:
    view = view[view["title"].str.contains(query, case=False, na=False)]
view = view.sort_values(["relevance", "published_at"] if order == "Pertinence" else ["published_at"],
                        ascending=False)

st.subheader(f"Articles ({len(view)})")
n_show = st.session_state.get("news_n", 10)
for r in view.head(n_show).itertuples():
    with st.container(border=True):
        st.markdown(f"**[{r.title}]({r.url})**")
        extra = f" · repris par {int(r.duplicates)} autre(s) source(s)" if r.duplicates and r.is_primary else ""
        st.markdown(f"<small>{r.source} · {r.published_at:%d/%m/%Y %H:%M}{extra}</small> &nbsp; {badge(r.score)}",
                    unsafe_allow_html=True)
        if isinstance(r.summary, str) and r.summary:
            st.caption(r.summary[:280] + ("…" if len(r.summary) > 280 else ""))
if len(view) > n_show and st.button(f"Afficher plus ({len(view) - n_show} restants)"):
    st.session_state["news_n"] = n_show + 20
    st.rerun()

# ------------------------------------------------------------------ synthèse
st.subheader("Synthèse des actualités récentes")
if synth:
    period = f"{pd.Timestamp(synth['window_start']):%d/%m/%Y} – {pd.Timestamp(synth['window_end']):%d/%m/%Y}"
    cls = synth.get("score_class")
    methods = news["method"].dropna()
    method_txt = "notés par LLM" if methods.str.startswith("llm").all() and len(methods) else \
        "notés par lexique financier (sans LLM)" if (methods == "lexicon").all() and len(methods) else \
        "notés par LLM et, à défaut, par lexique"
    k = st.columns(3)
    icon = {"positif": ":green[:material/trending_up: Positif]", "négatif": ":red[:material/trending_down: Négatif]",
            "neutre": ":gray[:material/trending_flat: Neutre]"}
    k[0].markdown(f"**Sentiment global (scores)**  \n### {icon.get(cls, 'Données insuffisantes')}")
    k[1].metric("Score moyen pondéré", fmt.num(synth.get("mean_score"), 2) if synth.get("mean_score") is not None
                else "n.d.", help="Moyenne des scores par article (−1 à +1), pondérée par la pertinence.")
    k[2].metric("Articles analysés", int(synth.get("n_articles") or 0), help=f"Fenêtre : {period}")
    st.caption(f"Fenêtre : {period} · articles {method_txt}.")

    if synth.get("status") == "ok" and synth.get("synthesis_json"):
        s = json.loads(synth["synthesis_json"])
        with st.container(border=True):
            st.markdown(f"**Synthèse** :violet-badge[:material/auto_awesome: Généré par IA] — sentiment estimé : "
                        f"**{s.get('sentiment', 'n.d.')}**, confiance **{s.get('confidence', 'n.d.')}**")
            cols = st.columns(4)
            for col, (key, title) in zip(cols, [("themes", "Thèmes"), ("positives", "Éléments positifs"),
                                                ("negatives", "Éléments négatifs"),
                                                ("risks", "Risques / incertitudes")], strict=True):
                col.markdown(f"**{title}**")
                col.markdown("\n".join(f"- {x}" for x in s.get(key, [])) or "—")
            urls = s.get("article_urls", [])
            if urls:
                titles = dict(zip(news["url"], news["title"], strict=False))
                with st.expander(f"Articles justifiant la synthèse ({len(urls)})"):
                    for u in urls:
                        st.markdown(f"- [{titles.get(u, u)}]({u})")
            st.caption(f"Généré par {synth.get('model')} le {pd.Timestamp(synth['generated_at']):%d/%m/%Y} "
                       "uniquement à partir des articles listés. Peut contenir des erreurs.")
    elif synth.get("status") == "insufficient":
        st.info("Données insuffisantes : trop peu d'articles récents pour une synthèse fiable.")
    elif synth.get("status") == "error":
        st.warning("La synthèse IA a échoué lors du dernier run (voir les logs du pipeline).")
    else:
        st.info("Synthèse IA indisponible : renseignez `OPENAI_API_KEY` dans `.env` puis relancez `bourse run`. "
                "Le sentiment ci-dessus provient du lexique financier.", icon=":material/info:")

# ------------------------------------------------------------------ historique
st.subheader("Évolution du sentiment")
prim = news[news["is_primary"] & news["score"].notna()].copy()
if len(prim) < 5:
    st.caption("Pas assez d'articles notés pour tracer un historique.")
else:
    prim["week"] = prim["published_at"].dt.to_period("W-SUN").dt.start_time
    prim["w"] = prim["score"] * prim["relevance"]
    weekly = prim.groupby("week").agg(sw=("w", "sum"), r=("relevance", "sum"), n=("url", "count")).reset_index()
    weekly["score"] = weekly["sw"] / weekly["r"]
    st.plotly_chart(charts.sentiment_history(weekly), width="stretch", config={"displayModeBar": False})
    st.markdown("**Nombre d'articles par semaine**")
    st.plotly_chart(charts.article_counts(weekly), width="stretch", config={"displayModeBar": False})
    st.caption(f"Agrégation hebdomadaire, moyenne pondérée par la pertinence. Historique limité à la période "
               f"réellement couverte ({first:%m/%Y} – {last:%m/%Y}) : il n'est pas extrapolé.")
