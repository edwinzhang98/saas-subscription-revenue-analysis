"""Notebook helpers: one-line setup and number-aware markdown, so findings text always matches the data."""
from __future__ import annotations

import pandas as pd
import plotly.io as pio
from IPython.display import Markdown, display

from . import theme  # noqa: F401  (registers the Plotly template)


def setup() -> None:
    # interactive charts in the HTML export (plotly.js from CDN) + a PNG copy so the .ipynb renders on GitHub
    pio.renderers.default = "notebook_connected+png"
    pio.renderers["png"].width = 1100
    pio.renderers["png"].height = 480
    pd.set_option("display.max_columns", 40)
    pd.set_option("display.width", 200)
    pd.set_option("display.float_format", lambda v: f"{v:,.4f}")


def md(text: str) -> None:
    display(Markdown(text))


def show(fig, height: int | None = None) -> None:
    """Render a chart at its own height (PNG copy too)."""
    h = height or int(fig.layout.height or 450)
    pio.renderers["png"].height = h
    fig.show()
