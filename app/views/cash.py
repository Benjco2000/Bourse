"""Page 5 — De l'OCF au cash réellement disponible pour l'actionnaire."""

import pandas as pd
import streamlit as st
from lib import charts, data, fmt, ui

from bourse.financials.cash import buyback_effect, cash_bridge, shareholder_metrics

data.demo_banner()
sym = st.session_state.get("symbol") or data.companies()["ticker"].iloc[0]
st.title(f"Du cash généré au cash actionnaire — {sym}")
st.caption("Comment le flux de trésorerie d'exploitation est utilisé : investissements, rachats, dividendes, "
           "acquisitions, dette. Les postes indisponibles sont signalés, jamais remplacés par zéro.")

st_all = data.for_symbol("core", "financial_statements", sym, symbol_col="ticker")
if st_all.empty:
    st.info("États financiers indisponibles : lancez `bourse run`.")
    st.stop()
snap_all = data.table("gold", "company_snapshot")
snap = snap_all[snap_all["ticker"] == sym].iloc[0].to_dict() if sym in set(snap_all.get("ticker", [])) else {}
ccy = snap.get("reported_currency") or st_all["reported_currency"].dropna().iloc[-1]

c1, c2 = st.columns([1, 2])
mode = c1.segmented_control("Période", ["Annuelle", "Trimestrielle", "12 mois glissants"], default="Annuelle",
                            key="cash_mode")
mode = mode or "Annuelle"
if mode == "12 mois glissants":
    ttm = data.for_symbol("core", "ttm", sym, symbol_col="ticker").sort_values("period_end")
    if ttm.empty:
        st.info("Agrégats 12 mois indisponibles.")
        st.stop()
    ttm = ttm[ttm["method"] == "sum_4_quarters"] if (ttm["method"] == "sum_4_quarters").any() else ttm
    row = ttm.iloc[-1].to_dict()
    period_label = f"12 mois clos fin {pd.Timestamp(row['period_end']):%m/%Y}"
else:
    ptype = "annual" if mode == "Annuelle" else "quarter"
    rows = st_all[st_all["period_type"] == ptype].sort_values("period_end")
    if rows.empty:
        st.info(f"Aucune période {mode.lower()} disponible.")
        st.stop()
    labels = [f"FY{int(r.fiscal_year)}" if ptype == "annual" else f"{r.period_label} {int(r.fiscal_year)} "
              f"(clos {pd.Timestamp(r.period_end):%m/%Y})" for r in rows.itertuples()]
    choice = c2.selectbox("Exercice / trimestre", labels[::-1], index=0, key=f"cash_period_{ptype}")
    row = rows.iloc[labels.index(choice)].to_dict()
    period_label = choice

# ------------------------------------------------------------------ cascade
steps, missing = cash_bridge(row)
st.subheader(f"Cascade de trésorerie — {period_label}")
if len(steps) < 3:
    st.warning("OCF ou CAPEX indisponible : cascade impossible.")
else:
    st.plotly_chart(charts.waterfall(steps, ccy), width="stretch", config={"displayModeBar": False})
    notes = [s.note for s in steps if s.note]
    st.caption(f"Montants en {ccy}. Bleu : entrée de trésorerie · orange : sortie · gris : sous-total / total. "
               "« Autres flux » réconcilie avec la variation de trésorerie publiée (autres investissements, "
               "change, intérêts minoritaires…)." + (f" ⚠️ {notes[0]}." if notes else ""))
if missing:
    st.info("Postes non publiés par la source pour cette période : " + ", ".join(missing) + ".",
            icon=":material/info:")

# ------------------------------------------------------------------ actionnaire
mc = snap.get("market_cap") if mode == "12 mois glissants" or mode == "Annuelle" else None
sm = shareholder_metrics(row, mc)
st.subheader("Ce qui revient à l'actionnaire")
k = st.columns(4)
ui.kpi(k[0], "Rachats + dividendes", fmt.money(sm["returned"], ccy, short=True))
ui.kpi(k[1], "En % du FCF", fmt.pct(sm["returned_to_fcf"], 0) if sm["returned_to_fcf"] is not None else "N/A")
ui.kpi(k[2], "Rendement du dividende", fmt.pct(sm["dividend_yield"], 2) if sm["dividend_yield"] is not None
       else "N/A")
ui.kpi(k[3], "Rendement des rachats", fmt.pct(sm["buyback_yield"], 2) if sm["buyback_yield"] is not None else "N/A")
if mc:
    st.caption(f"Rendements calculés sur la capitalisation actuelle ({fmt.money(mc, snap.get('trading_currency'), short=True)}), "
               "donc indicatifs pour une période passée.")

st.markdown("#### Rémunération en actions (SBC)")
k = st.columns(3)
ui.kpi(k[0], "SBC", fmt.money(sm["sbc"], ccy, short=True))
ui.kpi(k[1], "SBC / chiffre d'affaires", fmt.pct(sm["sbc_to_revenue"], 1) if sm["sbc_to_revenue"] is not None
       else "N/A")
ui.kpi(k[2], "FCF après SBC", fmt.money(sm["fcf_after_sbc"], ccy, short=True))
st.caption("La SBC est une charge **non décaissée**, réintégrée dans l'OCF. « FCF après SBC » la retranche pour "
           "tenir compte de la dilution économique : c'est un indicateur analytique, pas un flux de trésorerie "
           "(elle n'apparaît donc pas dans la cascade).")

# ------------------------------------------------------------------ rachats vs nombre d'actions
annual = st_all[st_all["period_type"] == "annual"].sort_values("fiscal_year").tail(11)
if mode == "Annuelle" and len(annual) >= 2:
    st.markdown("#### Rachats et nombre d'actions")
    idx = annual.index.get_loc(annual.index[annual["period_end"] == row["period_end"]][0]) \
        if (annual["period_end"] == row["period_end"]).any() else None
    if idx is not None and idx > 0:
        prev = annual.iloc[idx - 1]
        prices = data.for_symbol("core", "prices_daily", sym, symbol_col="ticker")
        avg_price = None
        if not prices.empty:
            mask = (prices["date"] > prev["period_end"]) & (prices["date"] <= row["period_end"])
            avg_price = prices.loc[mask, "close"].mean() if mask.any() else None
        eff = buyback_effect(row.get("share_repurchases"), avg_price, prev["shares_diluted"], row["shares_diluted"])
        k = st.columns(3)
        ui.kpi(k[0], "Actions rachetées (estimation)", fmt.pct(eff["gross_bought_pct"], 1, signed=True)
               if eff["gross_bought_pct"] is not None else "N/A")
        ui.kpi(k[1], "Variation nette du nb d'actions", fmt.pct(eff["net_change_pct"], 1, signed=True)
               if eff["net_change_pct"] is not None else "N/A")
        ui.kpi(k[2], "Émissions implicites", fmt.pct(eff["issued_pct"], 1, signed=True)
               if eff["issued_pct"] is not None else "N/A")
        st.caption("Méthode : actions rachetées ≈ montant des rachats / cours moyen de l'exercice, rapporté au nombre "
                   "moyen d'actions diluées de l'exercice précédent. L'écart avec la variation nette approxime les "
                   "actions émises (rémunération en actions, conversions). Estimation : le cours moyen n'est pas le "
                   "prix réellement payé.")
    else:
        st.caption("Exercice précédent indisponible : effet des rachats non calculable pour cette période.")

# ------------------------------------------------------------------ historique
st.subheader("Historique annuel")
hist = annual.assign(fcf_after_sbc=annual["free_cash_flow"] - annual["stock_based_compensation"])
st.plotly_chart(charts.grouped_bars(hist, "fiscal_year", {
    "free_cash_flow": "FCF", "fcf_after_sbc": "FCF après SBC", "share_repurchases": "Rachats",
    "dividends_paid": "Dividendes", "acquisitions": "Acquisitions"}, ccy), width="stretch",
    config={"displayModeBar": False})
st.plotly_chart(charts.lines_by_year(hist, "fiscal_year", {"shares_diluted": "Actions diluées (moyenne)"},
                                     "Nombre d'actions diluées", percent=False), width="stretch",
                config={"displayModeBar": False})
st.caption(f"Montants en {ccy}. Source : FMP (tableaux de flux de trésorerie publiés). Acquisitions nettes des "
           "cessions ; leur financement (FCF, dette, actions, trésorerie existante) n'est pas déductible de ces "
           "seuls chiffres.")
