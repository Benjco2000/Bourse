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
