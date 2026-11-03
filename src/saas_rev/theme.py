"""Shared chart theme: one palette, one Plotly template, one set of number formats.

Used by the Streamlit app, the notebooks and reports/report.html, so a chart looks the same
everywhere. Colors come from a validated reference palette (categorical order checked for
colour-vision deficiency with a palette validator); MRR movement colors are fixed per movement
type and never change with filters.
"""
from __future__ import annotations

import math

import plotly.graph_objects as go
import plotly.io as pio

# ---- chrome / ink
SURFACE = "#fcfcfb"
PAGE = "#f9f9f7"
INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"
DEEMPH = "#c3c2b7"          # emphasis form: "the rest" in gray
NEUTRAL_MID = "#f0efec"     # diverging midpoint
FONT = 'system-ui, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif'

# ---- categorical slots, fixed order (never cycled; fold the tail into "Other")
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
ACCENT = SERIES[0]

# ---- MRR movement identity (validated in this vertical order: reactivation / expansion / new | contraction / churn)
MOVEMENT_COLORS = {
    "new": "#2a78d6",
    "expansion": "#1baf7a",
    "reactivation": "#6250d6",
    "contraction": "#eda100",
    "churn": "#e34948",
}
MOVEMENT_LABELS = {
    "new": "New", "expansion": "Expansion", "reactivation": "Reactivation",
    "contraction": "Contraction", "churn": "Churn",
}

# ---- status (reserved for state; always shipped with an icon + label)
STATUS = {"good": "#0ca30c", "warning": "#fab219", "serious": "#ec835a", "critical": "#d03b3b"}

# ---- sequential (blue, light -> dark) and diverging (red <- gray -> blue)
SEQ_BLUE = ["#cde2fb", "#b7d3f6", "#9ec5f4", "#86b6ef", "#6da7ec", "#5598e7", "#3987e5",
            "#2a78d6", "#256abf", "#1c5cab", "#184f95", "#104281", "#0d366b"]
SEQ_SCALE = [[i / (len(SEQ_BLUE) - 1), c] for i, c in enumerate(SEQ_BLUE)]
DIVERGING_SCALE = [
    [0.00, "#a12b2b"], [0.15, "#e34948"], [0.35, "#f1a6a2"], [0.50, NEUTRAL_MID],
    [0.65, "#9ec5f4"], [0.85, "#3987e5"], [1.00, "#184f95"],
]

# ---- segment colors (identity follows the entity, not the rank)
SEGMENT_COLORS = {"SMB": SERIES[0], "Mid-Market": SERIES[1], "Enterprise": SERIES[2]}
PLAN_COLORS = {"basic": SERIES[0], "premium": SERIES[1], "enterprise": SERIES[2], "churned": DEEMPH}
PLAN_LABELS = {"basic": "Basic", "premium": "Premium", "enterprise": "Enterprise", "churned": "Churned"}


def template() -> go.layout.Template:
    t = go.layout.Template()
    t.layout = go.Layout(
        font=dict(family=FONT, size=13, color=INK_2),
        title=dict(font=dict(family=FONT, size=16, color=INK), x=0.0, xanchor="left", xref="paper",
                   y=1.0, yanchor="top", yref="container", pad=dict(t=14, b=0, l=0)),
        paper_bgcolor=SURFACE,
        plot_bgcolor=SURFACE,
        colorway=SERIES,
        margin=dict(l=56, r=24, t=72, b=48),
        xaxis=dict(showgrid=False, linecolor=AXIS, linewidth=1, ticks="outside", tickcolor=AXIS, ticklen=4,
                   tickfont=dict(color=MUTED, size=12), title=dict(font=dict(color=INK_2, size=12)), zeroline=False),
        yaxis=dict(showgrid=True, gridcolor=GRID, gridwidth=1, zeroline=False, linecolor=AXIS, showline=False,
                   tickfont=dict(color=MUTED, size=12), title=dict(font=dict(color=INK_2, size=12))),
        legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="left", x=0.0, font=dict(color=INK_2, size=12),
                    bgcolor="rgba(0,0,0,0)", title=dict(text="")),
        hoverlabel=dict(bgcolor="#ffffff", bordercolor=AXIS, font=dict(family=FONT, color=INK, size=12)),
        hovermode="closest",
        bargap=0.35,
        barcornerradius=4,
    )
    t.data.scatter = [go.Scatter(line=dict(width=2), marker=dict(size=8, line=dict(width=2, color=SURFACE)))]
    t.data.bar = [go.Bar(marker=dict(line=dict(width=0)))]
    return t


pio.templates["saas"] = template()
pio.templates.default = "saas"


def apply(fig: go.Figure, title: str | None = None, subtitle: str | None = None, height: int | None = None,
          wrap: int = 84) -> go.Figure:
    """Title (bold claim) + optional subtitle (what is plotted, units). Long lines wrap so they never clip."""
    import textwrap
    if title is not None:
        t_lines = textwrap.wrap(esc(title), width=wrap) or [""]
        s_lines = textwrap.wrap(esc(subtitle), width=int(wrap * 1.35)) if subtitle else []
        text = "<b>" + "<br>".join(t_lines) + "</b>"
        if s_lines:
            text += "<br>" + "<br>".join(f"<span style='font-size:12px;color:{MUTED}'>{line}</span>" for line in s_lines)
        top = 30 + 21 * len(t_lines) + 17 * len(s_lines) + (26 if fig.layout.showlegend is not False else 8)
        fig.update_layout(title=dict(text=text), margin=dict(t=max(top, int(fig.layout.margin.t or 0))))
    if height:
        fig.update_layout(height=height)
    return fig


def esc(text: str) -> str:
    """Plotly renders text between two '$' signs as LaTeX; escape dollars in titles and labels."""
    return text.replace("$", "&#36;")


def qname(q: str) -> str:
    """'2026-Q3' -> 'Q3 2026'."""
    y, n = q.split("-Q")
    return f"Q{n} {y}"


def month_x(s):
    """Plot a month-end series at the start of its month, so 'Sep 2026' sits under the Sep tick."""
    import pandas as pd
    return pd.to_datetime(s).dt.to_period("M").dt.to_timestamp()


# ---- number formats -------------------------------------------------------------
def money(x: float | None, decimals: int = 1) -> str:
    """$1.2M / $12.3K / $950."""
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "–"
    sign = "−" if x < 0 else ""
    a = abs(x)
    if a >= 1e6:
        return f"{sign}${a / 1e6:.{decimals}f}M"
    if a >= 1e3:
        return f"{sign}${a / 1e3:.{decimals}f}K"
    return f"{sign}${a:,.0f}"


def pct(x: float | None, decimals: int = 1, signed: bool = False) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "–"
    s = f"{x * 100:+.{decimals}f}%" if signed else f"{x * 100:.{decimals}f}%"
    return s.replace("-", "−")


def delta_pct(x: float | None, decimals: int = 1, suffix: str = "") -> str:
    """Signed % for st.metric deltas (ASCII minus, so Streamlit colours the arrow correctly)."""
    return "" if x is None else f"{x * 100:+.{decimals}f}%{suffix}"


def delta_pts(x: float | None, decimals: int = 1, suffix: str = "") -> str:
    """Signed percentage-point change for st.metric deltas (ASCII minus)."""
    return "" if x is None else f"{x * 100:+.{decimals}f} pts{suffix}"


def pts(x: float | None, decimals: int = 1) -> str:
    """Percentage-point change, e.g. +1.2 pts."""
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "–"
    return f"{x * 100:+.{decimals}f} pts".replace("-", "−")


def num(x: float | None, decimals: int = 0) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "–"
    return f"{x:,.{decimals}f}"
