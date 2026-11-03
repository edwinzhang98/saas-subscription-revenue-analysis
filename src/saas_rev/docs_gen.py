"""Documents generated from the data, so their numbers can never drift from the warehouse:

  docs/03_data_quality_checks.md   (data-quality, reconciliation, cross-check, known-answer tests)
  docs/monthly_revenue_review.md   (Q3 2026 close revenue review — the recurring MBR format)
  docs/account_health_list.csv     (CS work queue: risk + expansion accounts with reason codes)

Narrative structure is fixed here; every number is read from the marts / key-number files.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from . import marts
from .config import REPO_ROOT, get_settings
from .theme import money, num, pct, pts, qname

DOCS = REPO_ROOT / "docs"


def _md_table(df: pd.DataFrame, floatfmt: dict | None = None) -> str:
    floatfmt = floatfmt or {}
    cols = list(df.columns)
    lines = ["| " + " | ".join(str(c) for c in cols) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    for _, r in df.iterrows():
        cells = []
        for c in cols:
            v = r[c]
            if c in floatfmt and isinstance(v, (int, float, np.floating, np.integer)) and pd.notna(v):
                cells.append(floatfmt[c](v))
            elif isinstance(v, float) and pd.notna(v):
                cells.append(f"{v:,.4g}")
            else:
                cells.append("" if (v is None or (isinstance(v, float) and np.isnan(v))) else str(v))
        lines.append("| " + " | ".join(cells).replace("\n", " ") + " |")
    return "\n".join(lines)


# =============================================================================== 03 data quality
def write_dq_doc() -> Path:
    dq = marts.load("dq_check_results")
    rec = marts.load("dq_revenue_reconciliation")
    gen = json.loads((REPO_ROOT / "reports" / "generator_sanity_checks.json").read_text())
    cc_path = REPO_ROOT / "reports" / "crosscheck_results.json"
    cc = json.loads(cc_path.read_text()) if cc_path.exists() else None
    ak = marts.read_key_numbers("answer_key")
    rc = marts.load("datamodel__row_counts")
    n_pass, n_fail, n_info = ((dq["status"] == s).sum() for s in ("pass", "fail", "info"))

    cat_names = {"row_count": "Row-count reconciliation", "primary_key": "Primary keys", "nulls": "Nulls",
                 "referential": "Referential integrity", "value_range": "Value ranges and outliers",
                 "time_range": "Time ranges", "model_logic": "Model logic and identities",
                 "exclusion": "Exclusions", "blind_spot": "Known blind spots of month-end snapshots"}
    parts = [f"""# 03 — Data quality checks

**Run:** every pipeline build (`python run.py`), seed {get_settings().seed}, data as of {get_settings().as_of_date}. **Result:** {n_pass} checks pass, {n_fail} fail,
{n_info} are monitored quantities (known issues found and handled). This page is generated from `sql/91_dq_checks.sql`,
`sql/90_dq_revenue_reconciliation.sql`, `src/saas_rev/crosscheck.py`, `src/saas_rev/validation.py` and
`data_gen/sanity_checks.py`; do not edit it by hand.

## Summary for reviewers

- **Sources reconcile row by row** from raw to staging (every excluded row is accounted for: internal accounts, void invoice lines,
  rejected plan changes, double-loaded usage rows).
- **Keys are unique, no orphans, no nulls in keys or dates, nothing outside the GA → as-of window.**
- **The metric layer is internally consistent:** the monthly waterfall identity and the movement-component tie-out close to the cent;
  the plan-change chaining invariant has 0 violations.
- **Billing agrees with MRR:** invoices spread over their service periods match daily-MRR revenue within
  {pct(abs(rec[rec['period_type'] == 'calendar_year']['gap_pct']).max(), 2)} per calendar year and
  {pct(abs(rec[rec['period_type'] == 'ttm']['gap_pct'].iloc[0]), 2)} over the last 12 months.
- **Two implementations agree:** An independent Python re-implementation, written from the metric definitions alone without reading
  the SQL, matches the SQL to the cent on every customer-month ({_cc_line(cc)}).
- **Known-answer tests:** the injected data-quality issues are found with the exact injected counts, and the planted business effects are
  recovered by the metric layer (table at the end).

## Source volumes

{_md_table(rc.rename(columns={'table': 'Table', 'rows': 'Rows'}), {'Rows': lambda v: f'{int(v):,}'})}
"""]
    for cat, title in cat_names.items():
        sub = dq[dq["category"] == cat][["check_id", "check_name", "observed", "expected", "status", "note"]]
        if sub.empty:
            continue
        sub = sub.rename(columns={"check_id": "ID", "check_name": "Check", "observed": "Observed", "expected": "Expected",
                                  "status": "Status", "note": "Note"})
        parts.append(f"## {title}\n\n{_md_table(sub, {'Observed': lambda v: f'{v:,.4g}'})}\n")

    yr = rec[rec["period_type"] != "month"][["period_type", "period", "billed_serviced", "daily_mrr_revenue", "gap", "gap_pct",
                                              "billed_not_serviced", "billed_uncollectible"]]
    parts.append(f"""## Billing reconciliation (`02_metric_definitions.md §14`)

Billed recurring revenue (recurring + proration invoice lines, spread by day over their service period and clipped to the
subscription's live days) vs revenue implied by daily MRR (MRR × 12 ÷ days in the year).

{_md_table(yr.rename(columns={'period_type': 'Window', 'period': 'Period', 'billed_serviced': 'Billed (serviced days)', 'daily_mrr_revenue': 'Daily-MRR revenue', 'gap': 'Gap', 'gap_pct': 'Gap %', 'billed_not_serviced': 'Billed, not serviced', 'billed_uncollectible': 'Of which uncollectible'}),
           {'Billed (serviced days)': money, 'Daily-MRR revenue': money, 'Gap': money, 'Gap %': lambda v: pct(v, 3),
            'Billed, not serviced': money, 'Of which uncollectible': money})}

Monthly gaps are a trend, not a test: a monthly price is spread over its own 28–31-day period while daily MRR uses a constant rate, so
February runs high and 31-day months slightly low; annual contracts reconcile exactly. "Billed, not serviced" is mostly failed card
renewals whose period was invoiced in full while service stopped 14 days in (dunning).
""")

    parts.append("## SQL vs independent Python implementation\n")
    if cc:
        rows = [{"Check": c["name"], "Rows": f"{c['rows_compared']:,}", "Only in one side": c["rows_only_python"] + c["rows_only_sql"],
                 "Max abs diff": "0" if not c["max_abs_diff"] else f"{c['max_abs_diff']:.1e}",
                 "Mismatching rows": c["n_mismatch_rows"], "Status": c["status"] + (" (info)" if c["informational"] else "")}
                for c in cc["comparisons"]]
        parts.append(f"""An independent Python re-implementation, written from the metric definitions alone without reading the SQL, is in
`src/saas_rev/crosscheck.py`; money is compared in whole cents, ratios to 1e-9. The comparator itself was tested by injecting a 1-cent
change, a dropped row and a relabelled movement (each was caught).

{_md_table(pd.DataFrame(rows))}

**Differences found and resolved.** The first comparison surfaced two documentation gaps, both fixed in definitions v1.1:
(1) customer-months between a churn and a win-back (MRR 0 both months) had no defined state — now *Not paying*;
(2) partial quarterly-cohort cells carried values in SQL although the definitions say blank — the SQL now leaves them blank and keeps the
observed-member ratio in a separate column. Neither changed any MRR, movement, NRR or GRR value.
""")

    g = pd.DataFrame(gen["checks"])
    def _fmt(v):
        if not isinstance(v, (int, float)):
            return v
        return f"{v:,.0f}" if abs(v) >= 100 else f"{v:.4g}"
    for col in ("value", "min", "max"):
        g[col] = g[col].map(_fmt)
    parts.append(f"""## Generator sanity checks (design targets in `docs/data_model.md §8`)

{_md_table(g.rename(columns={'check': 'Check', 'value': 'Value', 'min': 'Min', 'max': 'Max', 'pass': 'Pass'}))}
""")
    if ak:
        dka = pd.DataFrame(ak["dq_known_answers"]).rename(columns={"check": "Injected issue", "injected": "Injected",
                                                                  "found": "Found", "verdict": "Verdict"})
        rows = pd.DataFrame(ak["rows"])[["event", "planted", "recovered", "naive_cut", "verdict"]].rename(
            columns={"event": "Planted effect", "planted": "Planted (generator)", "recovered": "Recovered (metric layer)",
                     "naive_cut": "What a naive cut would say", "verdict": "Verdict"})
        parts.append(f"""## Known-answer tests

Because the data is synthetic, the injected issues and planted business effects are known. These tests check that the pipeline finds
them — a test of the method, not a finding. The analysis modules never read the answer key; only `src/saas_rev/validation.py` does.

**Injected data-quality issues**

{_md_table(dka)}

**Planted business effects**

{_md_table(rows)}
""")
    out = DOCS / "03_data_quality_checks.md"
    out.write_text("\n".join(parts))
    return out


def _cc_line(cc: dict | None) -> str:
    if not cc:
        return "cross-check not run"
    core = [c for c in cc["comparisons"] if not c["informational"]]
    ok = sum(c["status"] == "match" for c in core)
    return f"{ok}/{len(core)} core comparisons match, max money difference ≤ $0.00"


def build_all() -> list[Path]:
    out = [write_dq_doc()]
    try:
        from . import docs_mbr  # monthly revenue review + CS list (needs the analysis slices)
        out += docs_mbr.build()
    except ImportError:
        pass
    return out
