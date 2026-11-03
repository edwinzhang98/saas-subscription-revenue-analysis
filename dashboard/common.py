"""Shared helpers for the Streamlit pages: cached data access, page header / footer, KPI tiles,
charts and data tables in one consistent style."""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from saas_rev import marts, theme  # noqa: F401  (theme registers the Plotly template)

AS_OF_LABEL = "30 Sep 2026"

_CSS = """
<style>
  .block-container {padding-top: 1.6rem; padding-bottom: 3rem; max-width: 1350px;}
  h1 {font-size: 1.65rem !important; font-weight: 650 !important; margin-bottom: 0.2rem !important;}
  h2, h3 {font-weight: 600 !important;}
  .page-question {font-size: 1.02rem; color: #52514e; margin: 0 0 0.9rem 0;}
  .finding {border-left: 3px solid #2a78d6; padding: 0.35rem 0.9rem; margin: 0.4rem 0; background: #f6f8fc;}
  .note {color: #898781; font-size: 0.86rem;}
  [data-testid="stMetricValue"] {font-size: 1.6rem;}
</style>
"""


def page_header(title: str, question: str) -> None:
    """Title + the one question this page answers."""
    st.markdown(_CSS, unsafe_allow_html=True)
    st.title(title)
    st.markdown(f"<div class='page-question'>{question}</div>", unsafe_allow_html=True)


def how_to_read(markdown: str) -> None:
    st.divider()
    with st.expander("How to read this", expanded=False, icon=":material/help_outline:"):
        st.markdown(markdown)
    st.markdown(f"<div class='note'>Synthetic data · data as of {AS_OF_LABEL} · definitions in "
                "docs/02_metric_definitions.md</div>", unsafe_allow_html=True)


@st.cache_data(show_spinner=False)
def load(name: str) -> pd.DataFrame:
    return marts.load(name).copy()


@st.cache_data(show_spinner=False)
def key_numbers(topic: str) -> dict:
    return marts.read_key_numbers(topic)


_TEMPLATE = theme.template()


def plot(fig: go.Figure, key: str | None = None) -> None:
    fig.update_layout(template=_TEMPLATE)   # pin our template (Streamlit can change Plotly's default)
    st.plotly_chart(fig, width="stretch", theme=None, key=key,
                    config={"displaylogo": False, "modeBarButtonsToRemove": ["lasso2d", "select2d"]})


def data_table(df: pd.DataFrame, label: str = "Show data", **kwargs) -> None:
    """Every chart has a table twin (values are never hover-only)."""
    with st.expander(label, expanded=False, icon=":material/table:"):
        st.dataframe(df, hide_index=True, width="stretch", **kwargs)


def finding(text: str) -> None:
    st.markdown(f"<div class='finding'>{text}</div>", unsafe_allow_html=True)


def multiselect_filters(df_options: pd.DataFrame, columns: dict[str, str], key: str) -> dict:
    """One row of dimension filters above the charts it scopes."""
    cols = st.columns(len(columns))
    out = {}
    for c, (col, label) in zip(cols, columns.items()):
        opts = sorted(df_options[col].dropna().unique().tolist())
        out[col] = c.multiselect(label, opts, default=[], key=f"{key}_{col}", placeholder="All")
    return out
