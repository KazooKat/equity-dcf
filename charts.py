"""Plotly figure builders for the valuation dashboard.

Charting conventions applied throughout (kept deliberately boring):
- One y-axis per chart, never dual scales.
- Categorical colors assigned in fixed slot order; a series keeps its hue.
- Thin marks, solid hairline gridlines, recessive axes, generous padding.
- Legend whenever there are >= 2 series, plus selective direct labels only
  (endpoints), never a number on every point.
- The sensitivity heatmap is the one diverging scale: two opposing hues around
  a neutral midpoint anchored at the market price ("nothing" = fairly valued).
- Low-contrast slots (aqua/yellow on light surface) get relief via direct
  end-labels here and a table view in the app.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# Validated light-mode palette (categorical slots in fixed order).
S1_BLUE = "#2a78d6"
S2_AQUA = "#1baf7a"
S3_YELLOW = "#eda100"
BLUE_LIGHT = "#9ec5f4"      # same hue, lighter step — used for "projected"
RED = "#e34948"             # diverging warm pole only, never a 4th series
NEUTRAL_MID = "#f0efec"     # diverging midpoint
SURFACE = "#fcfcfb"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"
INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#898781"
FONT = 'system-ui, -apple-system, "Segoe UI", sans-serif'


def _base_layout(fig: go.Figure, *, height: int = 360, ylabel: str = "") -> go.Figure:
    fig.update_layout(
        paper_bgcolor=SURFACE, plot_bgcolor=SURFACE,
        font=dict(family=FONT, size=13, color=INK_2),
        height=height, margin=dict(l=8, r=8, t=32, b=8),
        hovermode="x unified",
        hoverlabel=dict(bgcolor="white", font=dict(family=FONT, size=12, color=INK)),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0, font=dict(size=12)),
    )
    fig.update_xaxes(showgrid=False, linecolor=AXIS, tickfont=dict(color=MUTED), zeroline=False)
    fig.update_yaxes(
        gridcolor=GRID, griddash="solid", linecolor=SURFACE, zeroline=False,
        tickfont=dict(color=MUTED), title=dict(text=ylabel, font=dict(size=12, color=MUTED)),
    )
    return fig


def _usd_b(v: float) -> str:
    return f"${v / 1e9:,.1f}B"


def fig_revenue_income(df: pd.DataFrame) -> go.Figure:
    """Grouped annual bars: revenue (slot 1) and net income (slot 2), one axis."""
    years = df["fiscal_year"]
    fig = go.Figure()
    fig.add_bar(x=years, y=df["revenue"], name="Revenue", marker_color=S1_BLUE,
                hovertemplate="Revenue %{y:$.3s}<extra></extra>")
    fig.add_bar(x=years, y=df["net_income"], name="Net income", marker_color=S2_AQUA,
                hovertemplate="Net income %{y:$.3s}<extra></extra>")
    # Selective direct labels: latest year only (relief for the aqua slot).
    # Grouped bars sit either side of the year tick — offset each label to its
    # own bar so neither collides with the taller neighbor.
    for col, color, xshift in (("revenue", S1_BLUE, -18), ("net_income", "#12805a", 18)):
        idx = df[col].last_valid_index()
        if idx is not None:
            fig.add_annotation(x=df.loc[idx, "fiscal_year"], y=df.loc[idx, col],
                               text=_usd_b(df.loc[idx, col]),
                               yshift=12, xshift=xshift, showarrow=False,
                               font=dict(size=11, color=color), xanchor="center")
    fig.update_layout(bargap=0.35, bargroupgap=0.12)
    return _base_layout(fig, ylabel="USD")


def fig_margins(df: pd.DataFrame) -> go.Figure:
    """Gross / operating / net margin lines with direct end-labels."""
    years = df["fiscal_year"]
    series = [
        ("Gross margin", df["gross_profit"] / df["revenue"], S1_BLUE),
        ("Operating margin", df["operating_income"] / df["revenue"], S2_AQUA),
        ("Net margin", df["net_income"] / df["revenue"], S3_YELLOW),
    ]
    fig = go.Figure()
    labels: list[tuple[float, str, str, int]] = []  # (y, text, color, fiscal_year)
    for name, vals, color in series:
        pct = vals * 100
        if pct.dropna().empty:
            continue
        fig.add_scatter(x=years, y=pct, name=name, mode="lines",
                        line=dict(color=color, width=2),
                        hovertemplate=name + " %{y:.1f}%<extra></extra>")
        idx = pct.last_valid_index()
        labels.append((float(pct.loc[idx]), f"{pct.loc[idx]:.0f}%", color,
                       int(df.loc[idx, "fiscal_year"])))
    # Dodge end-labels that land on top of each other (e.g. two margins both
    # ending at 11%): nudge later labels down in 12px steps when too close.
    labels.sort(key=lambda t: -t[0])
    prev_y, bump = None, 0
    span = max(v[0] for v in labels) - min(v[0] for v in labels) if labels else 0
    min_gap = max(span * 0.06, 1.0)
    for y_val, text, color, fy in labels:
        bump = bump + 14 if prev_y is not None and (prev_y - y_val) < min_gap else 0
        fig.add_annotation(x=fy, y=y_val, text=text, xshift=18, yshift=-bump,
                           showarrow=False, font=dict(size=11, color=color))
        prev_y = y_val
    return _base_layout(fig, ylabel="% of revenue")


def fig_ratio_panels(df: pd.DataFrame) -> go.Figure:
    """Small multiples (one hue, one axis each) for the core health ratios."""
    years = df["fiscal_year"]
    panels = [
        ("Return on equity", df["net_income"] / df["equity"] * 100, "%"),
        ("FCF margin", df["fcf"] / df["revenue"] * 100, "%"),
        ("Current ratio", df["assets_current"] / df["liabilities_current"], "x"),
        ("Debt / equity", df["total_debt"] / df["equity"], "x"),
    ]
    fig = make_subplots(rows=2, cols=2, subplot_titles=[p[0] for p in panels],
                        vertical_spacing=0.22, horizontal_spacing=0.12)
    for i, (name, vals, unit) in enumerate(panels):
        row, col = i // 2 + 1, i % 2 + 1
        suffix = "%" if unit == "%" else "x"
        fig.add_scatter(x=years, y=vals, mode="lines", line=dict(color=S1_BLUE, width=2),
                        showlegend=False, row=row, col=col,
                        hovertemplate=name + " %{y:.2f}" + suffix + "<extra></extra>")
        idx = vals.last_valid_index()
        if idx is not None:
            fig.add_annotation(x=df.loc[idx, "fiscal_year"], y=vals.loc[idx],
                               text=f"{vals.loc[idx]:.1f}{suffix}", xshift=16, showarrow=False,
                               font=dict(size=11, color=S1_BLUE), row=row, col=col)
    fig.update_annotations(font=dict(size=13, color=INK_2))
    _base_layout(fig, height=480)
    fig.update_layout(hovermode="closest")
    return fig


def fig_fcf_projection(hist: pd.DataFrame, proj: pd.DataFrame) -> go.Figure:
    """Historical FCF (solid slot 1) vs projected FCF (lighter step of the same hue)."""
    hist_years = hist["fiscal_year"]
    last_year = int(hist_years.iloc[-1])
    proj_years = [last_year + int(t) for t in proj["year"]]
    fig = go.Figure()
    fig.add_bar(x=hist_years, y=hist["fcf"], name="FCF (actual)", marker_color=S1_BLUE,
                hovertemplate="FCF %{y:$.3s}<extra></extra>")
    fig.add_bar(x=proj_years, y=proj["fcf"], name="FCF (projected)", marker_color=BLUE_LIGHT,
                customdata=proj["growth"] * 100,
                hovertemplate="Projected %{y:$.3s} (growth %{customdata:.1f}%)<extra></extra>")
    last = hist["fcf"].dropna()
    if not last.empty:
        fig.add_annotation(x=last_year, y=last.iloc[-1], text=_usd_b(last.iloc[-1]),
                           yshift=12, showarrow=False, font=dict(size=11, color=S1_BLUE))
    fig.update_layout(bargap=0.35)
    return _base_layout(fig, ylabel="USD")


def fig_sensitivity(grid: pd.DataFrame, market_price: float | None) -> go.Figure:
    """Implied price over WACC x terminal growth.

    With a market price: diverging scale, neutral gray pinned AT the market
    price (above market = cool/blue, below = warm/red). Without one: a plain
    one-hue sequential ramp.
    """
    z = grid.values.astype(float)
    x = [f"{g * 100:.2f}%" for g in grid.columns]
    y = [f"{w * 100:.1f}%" for w in grid.index]
    text = np.where(np.isnan(z), "", np.vectorize(lambda v: f"${v:,.0f}" if not np.isnan(v) else "")(z))

    if market_price and np.isfinite(z).any():
        span = max(abs(np.nanmax(z) - market_price), abs(market_price - np.nanmin(z)), 1e-9)
        zmin, zmax = market_price - span, market_price + span
        colorscale = [[0.0, "#c23a39"], [0.35, "#eba5a4"], [0.5, NEUTRAL_MID],
                      [0.65, "#86b6ef"], [1.0, "#104281"]]
    else:
        zmin, zmax = np.nanmin(z), np.nanmax(z)
        colorscale = [[0.0, "#cde2fb"], [1.0, "#0d366b"]]

    fig = go.Figure(go.Heatmap(
        z=z, x=x, y=y, text=text, texttemplate="%{text}",
        textfont=dict(size=12, family=FONT),
        colorscale=colorscale, zmin=zmin, zmax=zmax,
        xgap=2, ygap=2, hovertemplate="WACC %{y} · growth %{x}: %{text}<extra></extra>",
        colorbar=dict(title=dict(text="$/share", font=dict(size=11, color=MUTED)),
                      tickprefix="$", outlinewidth=0, thickness=12, tickfont=dict(color=MUTED)),
    ))
    # The grid is a linspace centered on the user's assumptions, so the center
    # cell IS the base case — outline it so readers can anchor themselves.
    ci, cj = len(grid.index) // 2, len(grid.columns) // 2
    fig.add_shape(type="rect", x0=cj - 0.5, x1=cj + 0.5, y0=ci - 0.5, y1=ci + 0.5,
                  line=dict(color=INK, width=2))
    _base_layout(fig, height=380)
    fig.update_layout(hovermode="closest")
    # type="category" keeps the "8.0%" tick labels verbatim — otherwise Plotly
    # coerces the numeric-looking strings and drops the % sign.
    fig.update_yaxes(title=dict(text="WACC", font=dict(size=12, color=MUTED)),
                     autorange="reversed", showgrid=False, type="category")
    fig.update_xaxes(title=dict(text="Terminal growth", font=dict(size=12, color=MUTED)),
                     side="bottom", type="category")
    return fig


def fig_football_field(
    dcf_price: float,
    sens_min: float, sens_max: float,
    wk52_low: float | None, wk52_high: float | None,
    market_price: float | None,
) -> go.Figure:
    """Valuation ranges as horizontal bars, market price as a reference line."""
    rows = [("DCF sensitivity range", sens_min, sens_max)]
    if wk52_low and wk52_high:
        rows.append(("52-week trading range", wk52_low, wk52_high))
    fig = go.Figure()
    for label, lo, hi in rows:
        fig.add_bar(y=[label], x=[hi - lo], base=[lo], orientation="h",
                    marker_color=BLUE_LIGHT, width=0.45, showlegend=False,
                    hovertemplate=f"{label}: ${lo:,.0f} – ${hi:,.0f}<extra></extra>")
        for v, anchor in ((lo, "right"), (hi, "left")):
            fig.add_annotation(y=label, x=v, text=f"${v:,.0f}", showarrow=False,
                               xanchor=anchor, xshift=-8 if anchor == "right" else 8,
                               font=dict(size=11, color=INK_2))
    fig.add_scatter(y=[rows[0][0]], x=[dcf_price], mode="markers", name="DCF base case",
                    marker=dict(color=S1_BLUE, size=12, line=dict(color=SURFACE, width=2)),
                    hovertemplate="DCF base case $%{x:,.0f}<extra></extra>")
    if market_price:
        fig.add_vline(x=market_price, line_color=INK_2, line_width=1)
        fig.add_annotation(x=market_price, y=1.08, yref="paper", showarrow=False,
                           text=f"Market ${market_price:,.0f}", font=dict(size=11, color=INK))
    _base_layout(fig, height=230)
    fig.update_layout(hovermode="closest", showlegend=True)
    fig.update_xaxes(showgrid=True, gridcolor=GRID, tickprefix="$")
    fig.update_yaxes(showgrid=False)
    return fig
