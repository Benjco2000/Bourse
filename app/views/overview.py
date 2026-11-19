"""Page 1 — Vue globale de l'entreprise."""

import pandas as pd
import streamlit as st
from lib import charts, data, fmt, ui

data.demo_banner()
sym = st.session_state.get("symbol") or data.companies()["ticker"].iloc[0]

companies = data.companies()
company = companies[companies["ticker"] == sym].iloc[0].to_dict()
snap_all = data.table("gold", "company_snapshot")
snap = snap_all[snap_all["ticker"] == sym].iloc[0].to_dict() if sym in set(snap_all.get("ticker", [])) else {}
kpis = data.for_symbol("gold", "kpis", sym, symbol_col="ticker")
trading_ccy = company.get("trading_currency") or snap.get("trading_currency") or ""
reported_ccy = snap.get("reported_currency") or ""

# ------------------------------------------------------------------ en-tête
left, right = st.columns([3, 1])
with left:
    if company.get("image"):
        st.image(company["image"], width=48)
    st.title(f"{company.get('name') or sym}")
    meta = [sym, company.get("exchange_full_name") or company.get("exchange"), company.get("sector"),
            company.get("industry"), company.get("country")]
    st.caption(" · ".join(str(x) for x in meta if x and str(x) != "nan"))
with right:
    ui.kpi(st, "Capitalisation boursière", fmt.money(snap.get("market_cap"), trading_ccy, short=True))
    if pd.notna(snap.get("market_cap_date")):
        st.caption(f"au {pd.Timestamp(snap['market_cap_date']):%d/%m/%Y}"
                   + (" (approximation)" if snap.get("market_cap_source") != "fmp_historical_market_cap" else ""))

ccy_line = f"Devise de cotation : **{trading_ccy or 'n.d.'}**"
if reported_ccy and reported_ccy != trading_ccy:
    ccy_line += f" · devise des comptes : **{reported_ccy}** (différente)"
st.markdown(ccy_line)

# ------------------------------------------------------------------ qualité des données
notes = [n for n in str(snap.get("quality_notes") or "").split(" | ") if n]
level = snap.get("quality_level", "degraded")
if not snap:
    st.error("Aucune donnée consolidée pour cette société : lancez `bourse run`.", icon=":material/error:")
elif notes:
    with st.expander(("Qualité des données : partielle" if level == "partial" else
                      "Qualité des données : à vérifier") + f" ({len(notes)} point(s))", expanded=level == "degraded",
                     icon=":material/fact_check:"):
        for n in notes:
            st.markdown(f"- {n}")
else:
    st.caption(":material/verified: Qualité des données : complète (comptes trimestriels, capitalisation FMP, "
               "aucune incohérence récente).")

# ------------------------------------------------------------------ activité
st.subheader("Activité")
summaries = data.table("gold", "business_summaries")
summary = summaries[summaries["ticker"] == sym] if not summaries.empty else summaries
desc_col, seg_col = st.columns([1, 1])
with desc_col:
    if not summary.empty:
        s = summary.iloc[0]
        with st.container(border=True):
            st.markdown("**Comment l'entreprise gagne de l'argent** "
                        ":violet-badge[:material/auto_awesome: Généré par IA]")
            st.markdown(s["summary_md"])
            st.caption(f"Résumé généré par {s['model']} le {pd.Timestamp(s['generated_at']):%d/%m/%Y} à partir de la "
                       "description et des segments ci-contre. Peut contenir des erreurs : vérifiez avec la source.")
    with st.container(border=True):
        st.markdown("**Description** :gray-badge[Source : FMP]")
        desc = company.get("description")
        if desc and str(desc) != "nan":
            st.write(desc if len(desc) < 900 else desc[:900] + "…")
            if len(desc) >= 900:
                with st.expander("Lire la suite"):
                    st.write(desc)
        else:
            st.caption("Description indisponible.")
        if summary.empty:
            st.caption("Le résumé « comment l'entreprise gagne de l'argent » généré par IA s'affichera ici une fois "
                       "`OPENAI_API_KEY` renseignée et le pipeline relancé.")
with seg_col:
    seg = data.for_symbol("core", "revenue_segments", sym, symbol_col="ticker")
    if seg.empty:
        st.caption("Répartition du chiffre d'affaires par segment indisponible.")
    else:
        years = sorted(seg["fiscal_year"].dropna().astype(int).unique(), reverse=True)
        fy = st.selectbox("Exercice", years, index=0, key="seg_year") if len(years) > 1 else years[0]
        seg_y = seg[seg["fiscal_year"] == fy]
        for kind, title in (("product", "CA par activité"), ("geography", "CA par zone géographique")):
            part = seg_y[(seg_y["segment_type"] == kind) & (seg_y["revenue"] > 0)]
            if not part.empty:
                st.plotly_chart(charts.segment_bars(part, f"{title} — FY{fy}", reported_ccy), width="stretch",
                                config={"displayModeBar": False}, key=f"seg_{kind}")
        st.caption(f"Source : FMP (segments publiés), montants en {reported_ccy or 'devise des comptes'}.")

# ------------------------------------------------------------------ KPIs
st.subheader("Indicateurs fondamentaux")
if kpis.empty:
    st.caption("KPIs indisponibles : les états financiers n'ont pas été chargés.")
else:
    order = ["revenue_cagr_5y", "fcf_cagr_5y", "share_change_5y", "roce_avg_5y", "net_debt_to_fcf",
             "gross_margin_ttm", "fcf_margin_ttm"]
    kpis = kpis.set_index("kpi_id").loc[[k for k in order if k in set(kpis["kpi_id"])]].reset_index()
    rows = [kpis.iloc[:4], kpis.iloc[4:]]
    for chunk in rows:
        cols = st.columns(4)
        for col, r in zip(cols, chunk.itertuples(), strict=False):
            with col:
                ui.kpi_card(r, reported_ccy)
    st.caption("Vert : seuil favorable atteint · Rouge : non atteint · Orange : historique insuffisant, valeur non "
               "interprétable ou donnée douteuse · Gris : indisponible. Seuils modifiables dans "
               "`config/settings.yaml` (section `kpis`).")

# ------------------------------------------------------------------ concurrents
st.subheader("Comparaison avec les concurrents")
peers_all = data.table("core", "peers")
default_peers = []
if not peers_all.empty:
    p = peers_all[(peers_all["ticker"] == sym) & peers_all["peer_in_universe"]]
    default_peers = p["peer"].tolist()[:3]
override = data.get_peer_override(sym)
available = [t for t in snap_all["ticker"].tolist() if t != sym] if not snap_all.empty else []
chosen = st.multiselect("Concurrents comparés (3 maximum)", available,
                        default=[t for t in (override if override is not None else default_peers) if t in available],
                        max_selections=3, key=f"peers_{sym}",
                        help="Par défaut : groupe de pairs défini dans la configuration. Votre choix est enregistré.")
if chosen != (override if override is not None else default_peers):
    data.set_peer_override(sym, chosen)
if override is not None and st.button("Rétablir les concurrents par défaut", icon=":material/restart_alt:"):
    data.set_peer_override(sym, None)
    st.session_state.pop(f"peers_{sym}", None)
    st.rerun()

rows = snap_all[snap_all["ticker"].isin([sym, *chosen])].copy() if not snap_all.empty else pd.DataFrame()
if rows.empty:
    st.caption("Aucune donnée de comparaison.")
else:
    rows["_order"] = rows["ticker"].map({t: i for i, t in enumerate([sym, *chosen])})
    rows = rows.sort_values("_order")

    def remark(r) -> str:
        parts = []
        for key, label in (("ps", "P/S"), ("pocf", "P/OCF"), ("pfcf", "P/FCF")):
            note = r.get(f"{key}_note")
            if isinstance(note, str) and note:
                parts.append(f"{label} : {note}")
        if r.get("ttm_method") != "sum_4_quarters":
            parts.append("TTM = dernier exercice annuel")
        return " · ".join(parts)

    def money(col: str) -> list[str]:
        return [fmt.money(v, r.get("trading_currency") if col == "market_cap" else r.get("reported_currency"),
                          short=True) for v, r in zip(rows[col], rows.to_dict("records"), strict=True)]

    table = pd.DataFrame({
        "Société": [f"{r.ticker} — {r.name}" for r in rows.itertuples()],
        "Capitalisation": money("market_cap"),
        "CA TTM": money("revenue_ttm"),
        "FCF TTM": money("free_cash_flow_ttm"),
        "Price / Sales": [f"{fmt.num(v, 1)} x" if pd.notna(v) else "N/A" for v in rows["ps"]],
        "Price / OCF": [f"{fmt.num(v, 1)} x" if pd.notna(v) else "N/A" for v in rows["pocf"]],
        "Price / FCF": [f"{fmt.num(v, 1)} x" if pd.notna(v) else "N/A" for v in rows["pfcf"]],
        "Remarques": [remark(r) for r in rows.to_dict("records")],
    })
    if not table["Remarques"].str.len().any():
        table = table.drop(columns="Remarques")
    st.dataframe(table, hide_index=True, width="stretch", column_config={
        "Price / Sales": st.column_config.TextColumn(help="Capitalisation / CA TTM"),
        "Price / OCF": st.column_config.TextColumn(help="Capitalisation / flux de trésorerie d'exploitation TTM"),
        "Price / FCF": st.column_config.TextColumn(help="Capitalisation / FCF TTM"),
    })
    dates = []
    for r in rows.itertuples():
        mc_d = f"{pd.Timestamp(r.market_cap_date):%d/%m/%Y}" if pd.notna(r.market_cap_date) else "n.d."
        ttm_d = f"{pd.Timestamp(r.ttm_period_end):%m/%Y}" if pd.notna(r.ttm_period_end) else "n.d."
        dates.append(f"{r.ticker} : capitalisation au {mc_d}, TTM clos fin {ttm_d}")
    st.caption("« Price » = capitalisation boursière (multiples de fonds propres, pas d'Enterprise Value). "
               "Un dénominateur nul ou négatif donne « N/A », expliqué dans « Remarques ». "
               "Montants dans la devise de chaque société.  \n" + " · ".join(dates))
