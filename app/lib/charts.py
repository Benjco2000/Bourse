"""Graphiques Plotly (palette catégorielle validée, traits fins, une seule échelle par graphique)."""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go

# Palette catégorielle (ordre fixe) + statuts
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
GOOD, WARNING, CRITICAL = "#0ca30c", "#fab219", "#d03b3b"
MUTED = "#8a8985"

BASE_LAYOUT = dict(
    margin=dict(l=8, r=8, t=36, b=8),
    hovermode="x unified",
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0, title=None),
    xaxis=dict(showgrid=False),
    yaxis=dict(gridcolor="rgba(128,128,128,0.15)", zeroline=False),
    bargap=0.35,
    separators=", ",
)


def _layout(fig: go.Figure, title: str | None = None, height: int = 320, **kw) -> go.Figure:
    layout = {**BASE_LAYOUT, "height": height, "title": dict(text=title or "", x=0, font=dict(size=14)), **kw}
    fig.update_layout(**layout)
    return fig


def price_chart(prices: pd.DataFrame, fair_value: float | None = None, currency: str = "") -> go.Figure:
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=prices["date"], y=prices["close"], mode="lines", name="Cours",
                             line=dict(color=SERIES[0], width=2),
                             hovertemplate="%{y:,.2f} " + currency + "<extra></extra>"))
    if fair_value:
        fig.add_hline(y=fair_value, line=dict(color=SERIES[1], width=2, dash="dash"),
                      annotation_text=f"Juste valeur {fair_value:,.2f}", annotation_position="top left")
    return _layout(fig, height=360, showlegend=False)


def bar_by_year(df: pd.DataFrame, x: str, y: str, title: str, color: str = SERIES[0],
                fmt: str = ",.3s", signed_colors: bool = True) -> go.Figure:
    colors = [CRITICAL if signed_colors and v is not None and v < 0 else color for v in df[y]]
    fig = go.Figure(go.Bar(x=df[x].astype(str), y=df[y], marker=dict(color=colors, cornerradius=4),
                           name=title, hovertemplate="%{x} : %{y:" + fmt + "}<extra></extra>"))
    return _layout(fig, title, showlegend=False, hovermode="closest")


def lines_by_year(df: pd.DataFrame, x: str, series: dict[str, str], title: str, percent: bool = True) -> go.Figure:
    fig = go.Figure()
    for i, (col, label) in enumerate(series.items()):
        if col not in df.columns or df[col].isna().all():
            continue
        fig.add_trace(go.Scatter(x=df[x].astype(str), y=df[col], name=label, mode="lines+markers",
                                 line=dict(color=SERIES[i], width=2), marker=dict(size=8),
                                 hovertemplate="%{y:" + (".1%" if percent else ",.2f") + "}"))
    fig = _layout(fig, title)
    if percent:
        fig.update_yaxes(tickformat=".0%")
    return fig


def valuation_methods(methods: dict[str, float | None], price: float | None, fair_value: float | None) -> go.Figure:
    items = [(k, v) for k, v in methods.items() if v is not None]
    labels = [k for k, _ in items]
    values = [v for _, v in items]
    fig = go.Figure(go.Bar(y=labels, x=values, orientation="h", marker=dict(color=SERIES[0], cornerradius=4),
                           text=[f"{v:,.2f}" for v in values], textposition="outside",
                           hovertemplate="%{y} : %{x:,.2f}<extra></extra>"))
    if price:
        fig.add_vline(x=price, line=dict(color=MUTED, width=2), annotation_text=f"Cours {price:,.2f}",
                      annotation_position="top right")
    if fair_value:
        fig.add_vline(x=fair_value, line=dict(color=SERIES[1], width=2, dash="dash"),
                      annotation_text=f"Juste valeur {fair_value:,.2f}", annotation_position="top left")
    fig = _layout(fig, height=60 + 52 * max(len(items), 1), showlegend=False, hovermode="closest")
    fig.update_yaxes(autorange="reversed", showgrid=False)
    fig.update_xaxes(gridcolor="rgba(128,128,128,0.15)", rangemode="tozero")
    return fig


def scores_bars(scores: pd.DataFrame) -> go.Figure:
    """Notes 0-100 par dimension (barres horizontales, une seule teinte)."""
    s = scores[scores["dimension"] != "overall"]
    fig = go.Figure(go.Bar(y=s["label"], x=s["score"], orientation="h",
                           marker=dict(color=SERIES[0], cornerradius=4),
                           text=[f"{v:.0f}" if pd.notna(v) else "n.d." for v in s["score"]], textposition="outside",
                           hovertemplate="%{y} : %{x:.0f}/100<extra></extra>"))
    fig = _layout(fig, height=240, showlegend=False, hovermode="closest")
    fig.update_xaxes(range=[0, 110], gridcolor="rgba(128,128,128,0.15)", tickvals=[0, 25, 50, 75, 100])
    fig.update_yaxes(autorange="reversed", showgrid=False)
    return fig


def analyst_grades(row: dict[str, float]) -> go.Figure:
    cats = [("strong_buy", "Achat fort", "#1c5cab"), ("buy", "Achat", "#6da7ec"), ("hold", "Conserver", "#c3c2b7"),
            ("sell", "Vente", "#e66767"), ("strong_sell", "Vente forte", "#b02a2a")]
    fig = go.Figure()
    for key, label, color in cats:
        v = row.get(key) or 0
        fig.add_trace(go.Bar(y=["Analystes"], x=[v], name=label, orientation="h", marker=dict(color=color),
                             text=[int(v)] if v else None, textposition="inside",
                             hovertemplate=f"{label} : %{{x}}<extra></extra>"))
    fig = _layout(fig, height=150, barmode="stack", hovermode="closest")
    fig.update_traces(marker_line=dict(width=2, color="rgba(255,255,255,0.9)"))
    fig.update_yaxes(showticklabels=False)
    return fig


def price_targets(price: float | None, low: float, median: float, consensus: float, high: float) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=[low, high], y=[0, 0], mode="lines", line=dict(color="#c3c2b7", width=8),
                             hoverinfo="skip", showlegend=False))
    points = [("Bas", low, MUTED), ("Médian", median, SERIES[0]), ("Consensus", consensus, SERIES[0]),
              ("Haut", high, MUTED)]
    for label, v, color in points:
        fig.add_trace(go.Scatter(x=[v], y=[0], mode="markers+text", marker=dict(size=12, color=color),
                                 text=[f"{label}<br>{v:,.2f}"], textposition="top center", name=label,
                                 showlegend=False, hovertemplate=f"{label} : %{{x:,.2f}}<extra></extra>"))
    if price:
        fig.add_trace(go.Scatter(x=[price], y=[0], mode="markers+text", name="Cours",
                                 marker=dict(size=14, color=SERIES[1], symbol="diamond",
                                             line=dict(width=2, color="white")),
                                 text=[f"Cours<br>{price:,.2f}"], textposition="bottom center", showlegend=False,
                                 hovertemplate="Cours : %{x:,.2f}<extra></extra>"))
    fig = _layout(fig, height=190, hovermode="closest")
    fig.update_yaxes(visible=False, range=[-1, 1])
    fig.update_xaxes(gridcolor="rgba(128,128,128,0.15)")
    return fig


def sparkline(points: list[tuple[int, float]], percent: bool = True, color: str = SERIES[0]) -> go.Figure:
    """Mini-courbe annuelle pour une carte KPI (sans axes, valeur au survol)."""
    xs = [str(x) for x, _ in points]
    ys = [y for _, y in points]
    fmt = ".1%" if percent else ",.3s"
    fig = go.Figure(go.Scatter(x=xs, y=ys, mode="lines+markers", line=dict(color=color, width=2),
                               marker=dict(size=5), hovertemplate="%{x} : %{y:" + fmt + "}<extra></extra>"))
    fig.update_layout(height=70, margin=dict(l=0, r=0, t=4, b=0), showlegend=False, separators=", ",
                      xaxis=dict(visible=False), yaxis=dict(visible=False), hovermode="closest")
    return fig


def segment_bars(df: pd.DataFrame, title: str, currency: str = "") -> go.Figure:
    """Répartition du CA par segment : barres horizontales, part en % en libellé direct."""
    d = df.sort_values("revenue", ascending=True)
    total = d["revenue"].sum()
    fig = go.Figure(go.Bar(
        y=d["segment"], x=d["revenue"], orientation="h", marker=dict(color=SERIES[0], cornerradius=4),
        text=[f"{v / total:.0%}" for v in d["revenue"]], textposition="outside", cliponaxis=False,
        hovertemplate="%{y} : %{x:,.4s} " + currency + "<extra></extra>"))
    fig = _layout(fig, title, height=50 + 34 * len(d), showlegend=False, hovermode="closest")
    fig.update_xaxes(visible=False, range=[0, d["revenue"].max() * 1.15])
    fig.update_yaxes(showgrid=False)
    return fig
