import pandas as pd
import streamlit as st
from lib import charts, data, fmt, ui

from bourse.analytics import valuation as val
from bourse.config import get_settings

data.demo_banner()
sc = data.screener().sort_values("symbol")
symbols = sc["symbol"].tolist()

# ------------------------------------------------------------------ sélection du ticker
qp = st.query_params.get("symbol")
if st.session_state.pop("_nav_symbol", False):  # arrivée depuis un tableau / la recherche
    qp = None
current = (qp or st.session_state.get("symbol") or ("MSFT" if "MSFT" in symbols else symbols[0])).upper()
if current not in symbols:
    st.error(f"{current} ne fait pas partie de l'univers suivi. Ajoutez-le dans `config/settings.yaml` "
             "puis relancez le pipeline.")
    current = symbols[0]
labels = {r.symbol: f"{r.symbol} — {r.name}" for r in sc.itertuples()}
sym = st.selectbox("Action", symbols, index=symbols.index(current), format_func=lambda s: labels.get(s, s),
                   label_visibility="collapsed")
st.session_state["symbol"] = sym
st.query_params["symbol"] = sym

row = sc[sc["symbol"] == sym].iloc[0].to_dict()
cur = row.get("currency") or "USD"
profile = data.for_symbol("silver", "profile", sym)
p = profile.iloc[0].to_dict() if not profile.empty else {}

# ------------------------------------------------------------------ en-tête
h1, h2 = st.columns([3, 1])
with h1:
    if p.get("image"):
        st.image(p["image"], width=48)
    st.title(f"{row.get('name') or sym} ({sym})")
    st.caption(" · ".join(str(x) for x in [p.get("exchange") or row.get("exchange"), row.get("sector"),
                                             row.get("industry"), row.get("country")] if x))
with h2:
    st.metric("Cours", fmt.money(row.get("price"), cur),
              delta=fmt.num(row.get("change_pct"), 2) + " %" if pd.notna(row.get("change_pct")) else None)
    wl = data.get_watchlist()
    if sym in wl:
        if st.button("Retirer de la watchlist", icon=":material/star:", width="stretch"):
            data.set_watchlist([s for s in wl if s != sym])
            st.rerun()
    elif st.button("Ajouter à la watchlist", icon=":material/star_border:", width="stretch"):
        data.set_watchlist(wl + [sym])
        st.rerun()

k = st.columns(6)
ui.kpi(k[0], "Capitalisation", fmt.money(row.get("market_cap"), cur, short=True))
ui.kpi(k[1], "P/E (TTM)", fmt.ratio(row.get("pe")))
ui.kpi(k[2], "P/B", fmt.ratio(row.get("pb")))
ui.kpi(k[3], "Rendement div.", fmt.pct(row.get("dividend_yield"), 2))
ui.kpi(k[4], "Bêta", fmt.num(row.get("beta"), 2))
ui.kpi(k[5], "Bas – haut 52 sem.", f"{fmt.num(row.get('year_low'))} – {fmt.num(row.get('year_high'))}")
st.write("")

tabs = st.tabs(["Résumé", "Valorisation", "Critères de Graham", "Finances", "Rentabilité & solidité",
                "Dividendes", "Analystes", "Actualités", "Initiés"])

prices = data.for_symbol("silver", "prices_daily", sym).sort_values("date")
fund = data.for_symbol("gold", "fundamentals_annual", sym).sort_values("fiscal_year")
scores = data.for_symbol("gold", "scores", sym)
criteria = data.for_symbol("gold", "graham_criteria", sym)

# ================================================================== Résumé
with tabs[0]:
    left, right = st.columns([2, 1])
    with left:
        rng = st.segmented_control("Période", ["1M", "6M", "1A", "5A", "Max"], default="1A",
                                   label_visibility="collapsed", key="range")
        if prices.empty:
            st.caption("Historique de cours indisponible.")
        else:
            days = {"1M": 31, "6M": 183, "1A": 366, "5A": 5 * 366}.get(rng or "1A")
            pr = prices[prices["date"] >= prices["date"].max() - pd.Timedelta(days=days)] if days else prices
            perf = pr["close"].iloc[-1] / pr["close"].iloc[0] - 1 if len(pr) > 1 else None
            st.caption(f"Performance sur la période : **{fmt.pct(perf, 1, signed=True)}**")
            st.plotly_chart(charts.price_chart(pr, row.get("fair_value"), cur), width="stretch",
                            config={"displayModeBar": False})
    with right:
        with st.container(border=True):
            verdict = row.get("verdict", "Non évaluable")
            st.markdown(f"#### :{ui.VERDICT_COLORS.get(verdict, 'gray')}[{ui.VERDICT_ICONS.get(verdict, verdict)}]")
            a, b = st.columns(2)
            a.metric("Juste valeur", fmt.money(row.get("fair_value"), cur))
            b.metric("Marge de sécurité", fmt.pct(row.get("margin_of_safety")))
            st.caption(f"Médiane de {int(row.get('methods_used') or 0)} méthodes — détail dans l'onglet "
                       "« Valorisation ».")
        with st.container(border=True):
            st.markdown(f"**Critères de Graham : {fmt.num(row.get('graham_score'), 1)} / 7**")
            if not criteria.empty:
                st.markdown("  \n".join(f"{ui.STATUS_ICONS.get(c.status, '')} {c.label}"
                                        for c in criteria.itertuples()))
    st.subheader("Notes")
    if not scores.empty:
        overall = scores.loc[scores["dimension"] == "overall", "score"]
        st.caption(f"Note globale : **{fmt.num(overall.iloc[0], 0) if not overall.empty else '—'} / 100**")
        st.plotly_chart(charts.scores_bars(scores), width="stretch", config={"displayModeBar": False})
    if p.get("description"):
        with st.expander("À propos de la société", expanded=False):
            st.write(p["description"])
            info = {"Dirigeant": p.get("ceo"), "Employés": p.get("full_time_employees"),
                    "Introduction en bourse": p.get("ipo_date"), "Site web": p.get("website")}
            st.markdown("  \n".join(f"**{k}** : {v}" for k, v in info.items() if v and str(v) != "NaT"))

# ================================================================== Valorisation
with tabs[1]:
    method_labels = {
        "value_graham_number": "Nombre de Graham",
        "value_graham_formula": "Formule de Graham",
        "value_dcf": "DCF (flux de trésorerie)",
        "value_pe_multiple": "P/E historique",
        "value_fmp_dcf": "DCF FMP (indicatif)",
    }
    methods = {label: (row.get(col) if pd.notna(row.get(col)) else None) for col, label in method_labels.items()}
    st.plotly_chart(charts.valuation_methods(methods, row.get("price"), row.get("fair_value")), width="stretch",
                    config={"displayModeBar": False})
    st.caption("La juste valeur composite est la médiane des 4 premières méthodes disponibles "
               "(paramétrable dans `config/settings.yaml`). Le DCF FMP est affiché à titre indicatif.")

    explain = pd.DataFrame([
        ("Nombre de Graham", "√(22,5 × BPA × actif net par action)",
         f"BPA {fmt.num(row.get('eps_ttm'))} · ANPA {fmt.num(row.get('bvps'))}", methods["Nombre de Graham"]),
        ("Formule de Graham", "BPA × (8,5 + 2g) × 4,4 / Y",
         f"g {fmt.pct(row.get('growth_used'))} ({row.get('growth_source')}) · Y {fmt.pct(row.get('aaa_yield'), 2)}",
         methods["Formule de Graham"]),
        ("DCF", "FCF/action actualisés, 2 étages + valeur terminale",
         f"FCF/action {fmt.num(row.get('fcf_per_share'))} · r {fmt.pct(row.get('discount_rate'))} · "
         f"g∞ {fmt.pct(row.get('terminal_growth'))}", methods["DCF (flux de trésorerie)"]),
        ("P/E historique", "BPA × P/E médian sur 5 ans", f"BPA {fmt.num(row.get('eps_ttm'))}",
         methods["P/E historique"]),
    ], columns=["Méthode", "Formule", "Hypothèses", "Valeur"])
    st.dataframe(explain, hide_index=True, width="stretch",
                 column_config={"Valeur": st.column_config.NumberColumn(format="%.2f")})

    st.subheader("Simulateur")
    st.caption("Modifiez les hypothèses : les valeurs sont recalculées instantanément (sans toucher aux données).")
    cfg = get_settings().valuation
    s1, s2 = st.columns(2)
    with s1, st.container(border=True):
        st.markdown("**DCF**")
        g = st.slider("Croissance des 5 prochaines années", -0.10, 0.30, float(row.get("growth_used") or 0.05),
                      0.005, format="%.3f")
        r = st.slider("Taux d'actualisation", 0.05, 0.15, float(cfg.get("discount_rate", 0.09)), 0.005,
                      format="%.3f")
        gt = st.slider("Croissance perpétuelle", 0.0, 0.04, float(cfg.get("terminal_growth", 0.025)), 0.0025,
                       format="%.4f")
        fcf = st.number_input("FCF par action (TTM)", value=float(row.get("fcf_per_share") or 0.0), format="%.2f")
        v = val.dcf_per_share(fcf, g, r, gt)
        st.metric("Valeur DCF", fmt.money(v, cur), delta=fmt.pct(val.margin_of_safety(v, row.get("price")))
                  + " de marge" if v else None)
    with s2, st.container(border=True):
        st.markdown("**Formule de Graham**")
        eps = st.number_input("BPA (TTM)", value=float(row.get("eps_ttm") or 0.0), format="%.2f")
        g2 = st.slider("Croissance attendue (g)", 0.0, 0.25, float(max(row.get("growth_used") or 0.05, 0.0)),
                       0.005, format="%.3f", key="g_graham")
        y = st.slider("Rendement obligations AAA (Y)", 0.02, 0.09, float(row.get("aaa_yield") or 0.05), 0.0025,
                      format="%.4f")
        v2 = val.graham_formula(eps, g2, y)
        st.metric("Valeur Graham", fmt.money(v2, cur),
                  delta=fmt.pct(val.margin_of_safety(v2, row.get("price"))) + " de marge" if v2 else None)

# ================================================================== Critères de Graham
with tabs[2]:
    st.markdown(f"### {fmt.num(row.get('graham_score'), 1)} / 7 critères de l'investisseur défensif")
    st.caption("Benjamin Graham, *L'investisseur intelligent* (ch. 14). Seuils modernisés et paramétrables.")
    for c in criteria.itertuples():
        with st.container(border=True):
            a, b, c3 = st.columns([0.6, 5, 3])
            a.markdown(f"## {ui.STATUS_ICONS.get(c.status, '')}")
            b.markdown(f"**{c.label}**  \n{c.description}")
            c3.markdown(f"**{c.value_text}**  \nSeuil : {c.threshold_text}  \n"
                        f"<small>{ui.STATUS_LABELS.get(c.status, '')}</small>", unsafe_allow_html=True)

# ================================================================== Finances
with tabs[3]:
    if fund.empty:
        st.caption("États financiers indisponibles.")
    else:
        f = fund.tail(10)
        a, b = st.columns(2)
        a.plotly_chart(charts.bar_by_year(f, "fiscal_year", "revenue", "Chiffre d'affaires"), width="stretch")
        b.plotly_chart(charts.bar_by_year(f, "fiscal_year", "net_income", "Résultat net"), width="stretch")
        a, b = st.columns(2)
        a.plotly_chart(charts.bar_by_year(f, "fiscal_year", "eps", "Bénéfice par action (BPA)", fmt=",.2f"),
                       width="stretch")
        b.plotly_chart(charts.bar_by_year(f, "fiscal_year", "free_cash_flow", "Free cash flow"), width="stretch")
        st.plotly_chart(charts.lines_by_year(f, "fiscal_year", {"gross_margin": "Marge brute",
                                                                "operating_margin": "Marge opérationnelle",
                                                                "net_margin": "Marge nette"}, "Marges"),
                        width="stretch")
        st.subheader("Tableau des données annuelles")
        fields = {"revenue": "Chiffre d'affaires", "gross_profit": "Marge brute", "operating_income": "Résultat opérationnel",
                  "net_income": "Résultat net", "eps": "BPA", "free_cash_flow": "Free cash flow",
                  "total_assets": "Total actif", "equity": "Capitaux propres", "total_debt": "Dette totale",
                  "cash": "Trésorerie", "dividends_paid": "Dividendes versés", "buybacks": "Rachats d'actions",
                  "shares": "Actions (diluées)"}
        t = f.set_index("fiscal_year")[[c for c in fields if c in f.columns]].T.rename(index=fields)
        t = t.map(lambda x: fmt.compact(x, 2) if pd.notna(x) and abs(x) >= 1000 else fmt.num(x, 2))
        st.dataframe(t, width="stretch")
        st.download_button("Télécharger (CSV)", fund.to_csv(index=False).encode(), f"{sym}_fondamentaux.csv",
                           icon=":material/download:")

# ================================================================== Rentabilité & solidité
with tabs[4]:
    if not fund.empty:
        f = fund.tail(10)
        a, b = st.columns(2)
        a.plotly_chart(charts.lines_by_year(f, "fiscal_year", {"roe": "ROE", "roic": "ROIC"},
                                            "Rentabilité des capitaux"), width="stretch")
        b.plotly_chart(charts.lines_by_year(f, "fiscal_year", {"current_ratio": "Ratio de liquidité générale"},
                                            "Liquidité", percent=False), width="stretch")
        a, b = st.columns(2)
        a.plotly_chart(charts.lines_by_year(f, "fiscal_year", {"debt_to_equity": "Dette / capitaux propres"},
                                            "Endettement", percent=False), width="stretch")
        ratios = data.for_symbol("silver", "ratios_annual", sym).sort_values("date")
        pe_col = next((c for c in ("price_to_earnings_ratio", "price_earnings_ratio") if c in ratios.columns), None)
        if pe_col:
            ratios["year"] = ratios["date"].dt.year
            b.plotly_chart(charts.lines_by_year(ratios.tail(10), "year", {pe_col: "P/E"}, "P/E historique",
                                                percent=False), width="stretch")
    m = st.columns(4)
    m[0].metric("Altman Z-Score", fmt.num(row.get("altman_z"), 2), help="> 3 : zone sûre ; < 1,8 : zone de détresse")
    m[1].metric("Piotroski F-Score", fmt.num(row.get("piotroski"), 0) + " / 9", help="≥ 7 : solide ; ≤ 3 : fragile")
    m[2].metric("Dette / FCF", fmt.num(row.get("debt_to_fcf"), 1) + " ans",
                help="Nombre d'années de free cash flow pour rembourser la dette")
    m[3].metric("Rendement FCF", fmt.pct(row.get("fcf_yield")))

# ================================================================== Dividendes
with tabs[5]:
    m = st.columns(4)
    m[0].metric("Dividende (12 mois)", fmt.money(row.get("dividend_ttm"), cur))
    m[1].metric("Rendement", fmt.pct(row.get("dividend_yield"), 2))
    m[2].metric("Taux de distribution", fmt.pct(row.get("payout_ratio")))
    m[3].metric("Années consécutives", fmt.num(row.get("dividend_years"), 0))
    dann = data.for_symbol("gold", "dividends_annual", sym)
    if dann.empty:
        st.caption("Aucun dividende versé (ou données indisponibles).")
    else:
        dann = dann[dann["year"] < pd.Timestamp.now().year].tail(20)
        st.plotly_chart(charts.bar_by_year(dann, "year", "dividend_per_share", "Dividende annuel par action",
                                           fmt=",.2f"), width="stretch")
        st.caption(f"Croissance annuelle moyenne sur 5 ans : **{fmt.pct(row.get('dividend_cagr_5y'))}**")
        divs = data.for_symbol("silver", "dividends", sym).sort_values("date", ascending=False).head(12)
        keep = [c for c in ("date", "payment_date", "dividend", "adj_dividend", "frequency") if c in divs.columns]
        st.dataframe(divs[keep], hide_index=True, width="stretch")

# ================================================================== Analystes
with tabs[6]:
    pt = data.for_symbol("silver", "price_target_consensus", sym)
    gr = data.for_symbol("silver", "grades_consensus", sym)
    if pt.empty and gr.empty:
        st.caption("Données analystes indisponibles (plan FMP).")
    if not pt.empty:
        t = pt.iloc[0]
        st.subheader("Objectifs de cours")
        st.plotly_chart(charts.price_targets(row.get("price"), t.get("target_low"), t.get("target_median"),
                                             t.get("target_consensus"), t.get("target_high")), width="stretch",
                        config={"displayModeBar": False})
        upside = t.get("target_consensus") / row["price"] - 1 if row.get("price") else None
        st.caption(f"Potentiel vs consensus : **{fmt.pct(upside, 1, signed=True)}**")
    if not gr.empty:
        g0 = gr.iloc[0].to_dict()
        st.subheader(f"Recommandations — consensus : {g0.get('consensus', '—')}")
        st.plotly_chart(charts.analyst_grades(g0), width="stretch", config={"displayModeBar": False})
    est = data.for_symbol("silver", "analyst_estimates", sym).sort_values("date")
    if not est.empty:
        st.subheader("Estimations")
        cols = {"date": "Exercice", "revenue_avg": "CA estimé", "eps_avg": "BPA estimé",
                "num_analysts_eps": "Nb analystes"}
        e = est[[c for c in cols if c in est.columns]].rename(columns=cols)
        if "Exercice" in e:
            e["Exercice"] = e["Exercice"].dt.year
        st.dataframe(e, hide_index=True, width="stretch",
                     column_config={"CA estimé": st.column_config.NumberColumn(format="compact"),
                                    "BPA estimé": st.column_config.NumberColumn(format="%.2f")})

# ================================================================== Actualités
with tabs[7]:
    news = data.for_symbol("silver", "stock_news", sym, symbol_col="query_symbol")
    if news.empty or "published_date" not in news.columns:
        st.caption("Aucune actualité (non incluse dans le plan FMP gratuit).")
        news = news.iloc[0:0].assign(published_date=pd.NaT)
    for n in news.sort_values("published_date", ascending=False).head(20).itertuples():
        with st.container(border=True):
            st.markdown(f"**[{n.title}]({n.url})**")
            st.caption(f"{getattr(n, 'publisher', '') or getattr(n, 'site', '')} · "
                       f"{pd.Timestamp(n.published_date):%d/%m/%Y %H:%M}")
            text = getattr(n, "text", "") or ""
            if text:
                st.write(text[:300] + ("…" if len(text) > 300 else ""))

# ================================================================== Initiés
with tabs[8]:
    ins = data.for_symbol("silver", "insider_trades", sym)
    if ins.empty:
        st.caption("Aucune transaction d'initiés (ou données indisponibles avec votre plan).")
    else:
        cols = {"transaction_date": "Date", "reporting_name": "Initié", "type_of_owner": "Fonction",
                "transaction_type": "Type", "securities_transacted": "Titres", "price": "Prix",
                "securities_owned": "Détention après"}
        t = ins.sort_values("transaction_date", ascending=False)[[c for c in cols if c in ins.columns]]
        st.dataframe(t.rename(columns=cols), hide_index=True, width="stretch",
                     column_config={"Prix": st.column_config.NumberColumn(format="%.2f"),
                                    "Titres": st.column_config.NumberColumn(format="compact")})
