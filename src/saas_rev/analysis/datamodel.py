"""Data-model page inputs: source-table volumes, column lists and small samples, generator
sanity checks — written to the parquet cache so the dashboard never reads raw files."""
from __future__ import annotations

import json

import duckdb
import pandas as pd

from .. import marts
from ..config import REPO_ROOT, get_settings

TOPIC = "datamodel"
SOURCE_TABLES = ["customers", "subscriptions", "plan_changes", "invoices", "usage_events", "plans"]


def build() -> dict[str, pd.DataFrame]:
    s = get_settings()
    con = duckdb.connect(str(s.warehouse_path), read_only=True)
    counts, columns, samples = [], [], {}
    for t in SOURCE_TABLES:
        n = con.execute(f"SELECT COUNT(*) FROM raw.{t}").fetchone()[0]
        counts.append({"table": t, "rows": int(n)})
        desc = con.execute(f"DESCRIBE raw.{t}").df()[["column_name", "column_type"]]
        desc.insert(0, "table", t)
        columns.append(desc)
        order = "ORDER BY customer_id, week_start, event_name" if t == "usage_events" else ""
        samples[t] = con.execute(
            f"SELECT * FROM raw.{t} WHERE customer_id IN ('CUS-000849', 'CUS-001001') {order} LIMIT 8"
            if t not in ("plans",) else f"SELECT * FROM raw.{t}").df()
    con.close()
    tables = {
        "datamodel__row_counts": pd.DataFrame(counts),
        "datamodel__columns": pd.concat(columns, ignore_index=True),
    }
    for t, df in samples.items():
        tables[f"datamodel__sample_{t}"] = df.astype(str)
    sanity = json.loads((REPO_ROOT / "reports" / "generator_sanity_checks.json").read_text())
    tables["datamodel__generator_checks"] = pd.DataFrame(sanity["checks"]).astype({"value": str, "min": str, "max": str})
    for name, df in tables.items():
        marts.write(name, df)
    return tables


def key_numbers() -> dict:
    rc = marts.load("datamodel__row_counts").set_index("table")["rows"].to_dict()
    gc = marts.load("datamodel__generator_checks")
    dq = marts.load("dq_check_results")
    return {
        "row_counts": {k: int(v) for k, v in rc.items()},
        "generator_checks_passed": int(gc["pass"].sum()), "generator_checks_total": int(len(gc)),
        "dq_checks_pass": int((dq["status"] == "pass").sum()), "dq_checks_fail": int((dq["status"] == "fail").sum()),
        "dq_checks_info": int((dq["status"] == "info").sum()),
    }
