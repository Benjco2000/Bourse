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


def _parse_history(history) -> list[tuple[int, float]]:
    return [(int(y), float(v)) for y, v in (p.split(":") for p in str(history or "").split(";") if p)]


@st.dialog("Historique détaillé", width="large")
def kpi_dialog(row, currency: str = "") -> None:
    import pandas as pd

    from . import charts, fmt

    label_hist, percent = SPARK_LABELS.get(row.kpi_id, ("Historique", True))
    hist = _parse_history(row.history)
    color, icon, status_label = KPI_STATUS.get(row.status, KPI_STATUS["na"])
    st.markdown(f"### {row.label} : :{color}[{format_kpi(row.value, row.unit, row.kpi_id)}]")
    st.markdown(f":{color}-badge[{icon} {status_label}] · Seuil : "
                f"{format_threshold(row.threshold, row.direction, row.unit)} · {row.period_label}")
    st.caption(row.explanation)
    if isinstance(row.note, str) and row.note:
        st.caption(f"ⓘ {row.note}")
    if len(hist) < 2:
        st.info("Pas assez d'historique annuel pour tracer une courbe.")
        return
    st.markdown(f"**{label_hist}, exercices {hist[0][0]}–{hist[-1][0]}** — survolez un point pour voir la "
                "variation par rapport à l'exercice précédent.")
    st.plotly_chart(charts.kpi_history(hist, percent, label_hist, currency), width="stretch",
                    config={"displayModeBar": False})
    rows = []
    for i, (y, v) in enumerate(hist):
        prev = hist[i - 1][1] if i else None
        if prev is None:
            change = "—"
        elif percent:
            change = f"{fmt.num((v - prev) * 100, 1)} pt"
        elif prev > 0 and v > 0:
            change = fmt.pct(v / prev - 1, 1, signed=True)
        else:
            change = "n.s."
        rows.append({"Exercice": str(y), label_hist: fmt.pct(v, 1) if percent else fmt.compact(v, 2),
                     "Variation sur 1 an": change})
    st.dataframe(pd.DataFrame(rows[::-1]), hide_index=True, width="stretch")
    if percent:
        st.caption("Pour un ratio en %, la variation est exprimée en points de pourcentage.")


def kpi_card(row, currency: str = "") -> None:
    """Carte KPI : valeur, statut (couleur + icône + libellé), seuil, période, explication, mini-courbe."""
    from . import charts

    color, icon, label = KPI_STATUS.get(row.status, KPI_STATUS["na"])
    with st.container(border=True):
        head, btn = st.columns([5, 1], vertical_alignment="center")
        head.markdown(f"**{row.label}**", help=row.explanation)
        if btn.button("", icon=":material/open_in_full:", key=f"open_{row.ticker}_{row.kpi_id}",
                      help="Agrandir le graphique", type="tertiary"):
            kpi_dialog(row, currency)
        st.markdown(f"### :{color}[{format_kpi(row.value, row.unit, row.kpi_id)}]")
        st.markdown(f":{color}-badge[{icon} {label}]")
        st.caption(f"Seuil : {format_threshold(row.threshold, row.direction, row.unit)} · {row.period_label}")
        if isinstance(row.note, str) and row.note:
            st.caption(f"ⓘ {row.note}")
        hist = _parse_history(row.history)[-10:]
        if len(hist) >= 2:
            label_hist, percent = SPARK_LABELS.get(row.kpi_id, ("Historique", True))
            st.plotly_chart(charts.sparkline(hist, percent=percent), width="stretch",
                            config={"displayModeBar": False}, key=f"spark_{row.ticker}_{row.kpi_id}")
            st.caption(f"{label_hist}, {hist[0][0]}–{hist[-1][0]} · ⤢ pour agrandir")
