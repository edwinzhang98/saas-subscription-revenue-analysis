"""Static PNG charts for the README and documents (reports/figures/). Same chart functions as the
dashboard and report; rendered with kaleido (uses the local Chrome)."""
from __future__ import annotations

import importlib
from pathlib import Path

from .config import REPO_ROOT

OUT = REPO_ROOT / "reports" / "figures"

# (file name, module, function, kwargs, width)
FIGURES = [
    ("mrr_waterfall", "metrics", "fig_monthly_movements", {}, 1200),
    ("nrr_trend", "metrics", "fig_nrr_trend", {}, 1200),
    ("expansion_bridge_yoy", "metrics", "fig_bridge", {"which": "yoy"}, 1200),
    ("expansion_bridge_qoq", "metrics", "fig_bridge", {"which": "qoq"}, 1200),
    ("expansion_rate_by_driver", "metrics", "fig_expansion_rate_by_bucket", {}, 1200),
    ("cohort_nrr", "metrics", "fig_cohort_heatmap", {"metric": "nrr"}, 1200),
]
EXTRA_FIGURES: list[tuple] = []   # slice modules add theirs (see report_slices.py)


def export_all() -> list[Path]:
    importlib.import_module("saas_rev.report")      # registers the slice figures (report_slices.py)
    OUT.mkdir(parents=True, exist_ok=True)
    out = []
    for entry in FIGURES + EXTRA_FIGURES:
        if callable(entry[1]):            # (name, builder, width): a zero-argument function returning the figure
            name, builder, width = entry
            f = builder()
        else:
            name, mod, fn, kwargs, width = entry
            m = importlib.import_module(f"saas_rev.analysis.{mod}")
            f = getattr(m, fn)(**kwargs)
        p = OUT / f"{name}.png"
        f.write_image(str(p), width=width, height=int(f.layout.height or 450), scale=2)
        out.append(p)
    return out
