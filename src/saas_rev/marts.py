"""Parquet cache ("marts") shared by the dashboard, notebooks and the HTML report.

The warehouse is built once by the pipeline; selected tables are exported to parquet here, and
each analysis module writes its derived tables next to them as `<topic>__<name>.parquet`.
Readers (Streamlit pages, notebooks, report) only ever read these files — they never rebuild
the warehouse.
"""
from __future__ import annotations

import datetime
import json
from functools import lru_cache
from pathlib import Path

import duckdb
import pandas as pd

from .config import REPO_ROOT, get_settings

# Warehouse tables exported as-is (see docs/02_metric_definitions.md §16 for definitions)
WAREHOUSE_TABLES = [
    "meta_run",
    "stg_customers",
    "stg_plans",
    "dim_month",
    "fct_customer_mrr_monthly",
    "fct_mrr_movements",
    "fct_change_components",
    "fct_mrr_movement_components",
    "rpt_mrr_waterfall_monthly",
    "rpt_mrr_quarterly",
    "rpt_nrr_grr_ttm",
    "rpt_nrr_quarterly_runrate",
    "rpt_segment_retention_ttm",
    "rpt_cohort_retention",
    "rpt_plan_transition_matrix",
    "rpt_plan_change_flows",
    "rpt_upgrade_rate_monthly",
    "fct_basic_upgrade_monthly",
    "fct_account_month_features",
    "fct_account_usage_weekly",
    "int_billing_periods",
    "rpt_silent_contraction",
    "int_next_renewal",
    "dq_revenue_reconciliation",
    "dq_check_results",
]

KEY_NUMBERS_DIR = REPO_ROOT / "reports" / "key_numbers"


def marts_dir() -> Path:
    d = get_settings().marts_dir
    d.mkdir(parents=True, exist_ok=True)
    return d


def path(name: str) -> Path:
    return marts_dir() / f"{name}.parquet"


def export_warehouse(con: duckdb.DuckDBPyConnection | None = None) -> list[str]:
    """Copy the warehouse tables the readers need into the parquet cache."""
    own = con is None
    if own:
        con = duckdb.connect(str(get_settings().warehouse_path), read_only=True)
    out = []
    for t in WAREHOUSE_TABLES:
        p = path(t)
        con.execute(f"COPY (SELECT * FROM {t}) TO '{p.as_posix()}' (FORMAT parquet)")
        out.append(t)
    if own:
        con.close()
    load.cache_clear()
    return out


def write(name: str, df: pd.DataFrame) -> Path:
    p = path(name)
    df.to_parquet(p, index=False)
    load.cache_clear()
    return p


@lru_cache(maxsize=None)
def load(name: str) -> pd.DataFrame:
    """Read a mart (cached in-process). Callers must .copy() before mutating."""
    p = path(name)
    if not p.exists():
        raise FileNotFoundError(f"mart {name!r} not found at {p} — run `make all` (or `python run.py marts`) first")
    df = pd.read_parquet(p)
    for c in df.columns:
        # parquet DATE columns arrive as python dates (object dtype): make them Timestamps
        if df[c].dtype == object:
            first = df[c].dropna().head(1)
            if len(first) and isinstance(first.iloc[0], (datetime.date, datetime.datetime)):
                df[c] = pd.to_datetime(df[c])
        elif str(df[c].dtype).startswith("datetime64"):
            df[c] = pd.to_datetime(df[c])
    return df


def write_key_numbers(topic: str, numbers: dict) -> Path:
    """Headline numbers per topic, committed to git so documents can cite their source."""
    KEY_NUMBERS_DIR.mkdir(parents=True, exist_ok=True)
    p = KEY_NUMBERS_DIR / f"{topic}.json"
    p.write_text(json.dumps(numbers, indent=2, default=str, sort_keys=False) + "\n")
    return p


def read_key_numbers(topic: str) -> dict:
    p = KEY_NUMBERS_DIR / f"{topic}.json"
    return json.loads(p.read_text()) if p.exists() else {}
