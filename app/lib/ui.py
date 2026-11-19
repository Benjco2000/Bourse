"""Composants d'interface partagés."""

from __future__ import annotations

import pandas as pd
import streamlit as st

STOCK_PAGE = "views/stock.py"

VERDICT_ICONS = {
    "Sous-évaluée": ":material/trending_up: Sous-évaluée",
    "Proche de sa juste valeur": ":material/drag_handle: Juste valeur",
    "Surévaluée": ":material/trending_down: Surévaluée",
    "Non évaluable": "Non évaluable",
}
VERDICT_COLORS = {"Sous-évaluée": "green", "Proche de sa juste valeur": "orange", "Surévaluée": "red",
                  "Non évaluable": "gray"}
STATUS_ICONS = {"pass": "✅", "fail": "❌", "partial": "◐", "na": "➖"}
STATUS_LABELS = {"pass": "Validé", "fail": "Non validé", "partial": "Validé (historique partiel)",
                 "na": "Données insuffisantes"}


def goto_stock(symbol: str) -> None:
    st.session_state["symbol"] = symbol
    st.session_state["_nav_symbol"] = True
    st.switch_page(STOCK_PAGE)


def stock_table(df: pd.DataFrame, column_config: dict, key: str, height: int | str = "auto",
                column_order: list[str] | None = None) -> None:
    """Tableau d'actions : cliquer sur une ligne ouvre la fiche de l'action."""
    if df.empty:
        st.caption("Aucune action à afficher.")
        return
    event = st.dataframe(df, column_config=column_config, hide_index=True, width="stretch", height=height,
                         on_select="rerun", selection_mode="single-row", key=key, column_order=column_order)
    rows = event.selection.rows if event and event.selection else []
    if rows:
        goto_stock(str(df.iloc[rows[0]]["symbol"]))


SCREENER_COLUMNS = {
    "symbol": st.column_config.TextColumn("Ticker", width="small"),
    "name": st.column_config.TextColumn("Société"),
    "sector": st.column_config.TextColumn("Secteur"),
    "price": st.column_config.NumberColumn("Cours", format="%.2f"),
    "change_pct": st.column_config.NumberColumn("Var. jour", format="%.2f %%"),
    "market_cap": st.column_config.NumberColumn("Capitalisation", format="compact"),
    "pe": st.column_config.NumberColumn("P/E", format="%.1f"),
    "pb": st.column_config.NumberColumn("P/B", format="%.1f"),
    "dividend_yield": st.column_config.NumberColumn("Rendement", format="percent"),
    "fair_value": st.column_config.NumberColumn("Juste valeur", format="%.2f"),
    "margin_of_safety": st.column_config.NumberColumn("Marge de sécurité", format="percent"),
    "verdict": st.column_config.TextColumn("Verdict"),
    "graham_score": st.column_config.NumberColumn("Graham /7", format="%.1f"),
    "score_overall": st.column_config.ProgressColumn("Note globale", min_value=0, max_value=100, format="%.0f"),
    "score_valuation": st.column_config.ProgressColumn("Valorisation", min_value=0, max_value=100, format="%.0f"),
    "score_profitability": st.column_config.ProgressColumn("Rentabilité", min_value=0, max_value=100, format="%.0f"),
    "score_growth": st.column_config.ProgressColumn("Croissance", min_value=0, max_value=100, format="%.0f"),
    "score_health": st.column_config.ProgressColumn("Santé", min_value=0, max_value=100, format="%.0f"),
    "score_dividend": st.column_config.ProgressColumn("Dividende", min_value=0, max_value=100, format="%.0f"),
    "roe": st.column_config.NumberColumn("ROE", format="percent"),
    "roic": st.column_config.NumberColumn("ROIC", format="percent"),
    "net_margin": st.column_config.NumberColumn("Marge nette", format="percent"),
    "revenue_cagr_5y": st.column_config.NumberColumn("CA TCAM 5a", format="percent"),
    "debt_to_equity": st.column_config.NumberColumn("Dette/CP", format="%.2f"),
    "altman_z": st.column_config.NumberColumn("Altman Z", format="%.1f"),
    "piotroski": st.column_config.NumberColumn("Piotroski", format="%d"),
}


def kpi(container, label: str, value: str, help: str | None = None) -> None:
    """Indicateur compact (évite la troncature de st.metric sur les valeurs longues)."""
    title = f' title="{help}"' if help else ""
    container.markdown(
        f'<div{title} style="line-height:1.25"><div style="font-size:0.85rem;opacity:0.7">{label}</div>'
        f'<div style="font-size:1.35rem;font-weight:600;white-space:nowrap">{value}</div></div>',
        unsafe_allow_html=True)


SPARK_LABELS = {
    "revenue_cagr_5y": ("CA annuel", False),
    "fcf_cagr_5y": ("FCF annuel", False),
    "share_change_5y": ("Actions diluées", False),
    "roce_avg_5y": ("ROCE annuel", True),
    "gross_margin_ttm": ("Marge brute annuelle", True),
    "fcf_margin_ttm": ("Marge de FCF annuelle", True),
}

KPI_STATUS = {
    "good": ("green", ":material/check_circle:", "Favorable"),
    "bad": ("red", ":material/cancel:", "Défavorable"),
    "warning": ("orange", ":material/warning:", "À interpréter"),
    "na": ("gray", ":material/remove_circle_outline:", "Indisponible"),
}


SIGNED_KPIS = {"revenue_cagr_5y", "fcf_cagr_5y", "share_change_5y"}


def format_kpi(value, unit: str, kpi_id: str = "") -> str:
    from . import fmt

    if value is None or (isinstance(value, float) and value != value):
        return "N/A"
    if unit == "percent":
        return fmt.pct(value, 1 if abs(value) < 10 else 0, signed=kpi_id in SIGNED_KPIS)
    if unit == "years":
        return "Trésorerie nette" if value < 0 else f"{fmt.num(value, 1)} ans"
    return fmt.num(value, 2)


def format_threshold(threshold: float, direction: str, unit: str) -> str:
    from . import fmt

    sign = ">" if direction == "higher" else "<"
    if unit == "percent":
        return f"{sign} {fmt.pct(threshold, 0)}"
    return f"{sign} {fmt.num(threshold, 0)} ans"


def kpi_card(row) -> None:
    """Carte KPI : valeur, statut (couleur + icône + libellé), seuil, période, explication, mini-courbe."""
    from . import charts

    color, icon, label = KPI_STATUS.get(row.status, KPI_STATUS["na"])
    with st.container(border=True):
        st.markdown(f"**{row.label}**", help=row.explanation)
        st.markdown(f"### :{color}[{format_kpi(row.value, row.unit, row.kpi_id)}]")
        st.markdown(f":{color}-badge[{icon} {label}]")
        st.caption(f"Seuil : {format_threshold(row.threshold, row.direction, row.unit)} · {row.period_label}")
        if isinstance(row.note, str) and row.note:
            st.caption(f"ⓘ {row.note}")
        hist = [(int(y), float(v)) for y, v in (p.split(":") for p in str(row.history or "").split(";") if p)][-10:]
        if len(hist) >= 2:
            label_hist, percent = SPARK_LABELS.get(row.kpi_id, ("Historique", True))
            st.plotly_chart(charts.sparkline(hist, percent=percent), width="stretch",
                            config={"displayModeBar": False}, key=f"spark_{row.ticker}_{row.kpi_id}")
            st.caption(f"{label_hist}, {hist[0][0]}–{hist[-1][0]}")
