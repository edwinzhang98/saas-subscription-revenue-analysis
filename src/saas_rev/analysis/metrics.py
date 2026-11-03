"""Core revenue metrics: MRR trend, waterfall, NRR / GRR, the Q3 2026 expansion bridges,
segment and cohort retention.

Answers the VP's first two questions (02_metric_definitions.md §4–§8, scope Q1–Q2):
  * what are NRR / GRR and how have they trended;
  * why did expansion slow in Q3 2026 — one-off vs structural, which drivers.

Every number is computed from the SQL marts (fct_* / rpt_* tables); nothing here redefines a metric.
Chart titles are computed from the same data the chart draws, so they cannot drift from it.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from .. import marts, theme
from ..theme import MOVEMENT_COLORS, MOVEMENT_LABELS, money, pct, pts

TOPIC = "metrics"
Q_CUR, Q_PREV, Q_LY = "2026-Q3", "2026-Q2", "2025-Q3"
AS_OF = pd.Timestamp("2026-09-30")
MOVEMENTS_UP = ["new", "expansion", "reactivation"]
MOVEMENTS_DOWN = ["contraction", "churn"]
BUCKETS = ["Technology (all)", "Non-Tech seat expansion", "Non-Tech plan upgrades", "Non-Tech other"]
ONE_OFF_BUCKET = "One-off rollout"
BUCKET_COLORS = {
    "Technology (all)": theme.TECH,            # orange is reserved for Technology accounts
    "Non-Tech seat expansion": theme.OTHER,
    "Non-Tech plan upgrades": theme.SERIES[2],
    "Non-Tech other": theme.DEEMPH,
}
BUCKET_SHORT = {"Technology (all)": "Technology", "Non-Tech seat expansion": "non-Tech seats",
                "Non-Tech plan upgrades": "non-Tech plan upgrades", "Non-Tech other": "other non-Tech"}
GRR_COLOR = theme.INK_2                        # dark neutral: GRR is the floor under NRR, not a story entity
DOWN_COLOR = "#e34948"                         # bridge steps that take expansion away
UP_COLOR = theme.ACCENT                        # bridge steps that add expansion
MIN_GROUP_BASE = 30                            # smallest group base shown in segment views (definitions §7)
GROUP_LABELS = {"plan_id": theme.PLAN_LABELS, "plan_at_start": theme.PLAN_LABELS,
                "billing_period": {"monthly": "Monthly", "annual": "Annual"},
                "billing_at_start": {"monthly": "Monthly", "annual": "Annual"}}
_GROUP_COLUMN = {"plan_at_start": "plan_id", "billing_at_start": "billing_period"}   # SQL dimension -> fct column


def quarter_of(ts: pd.Series) -> pd.Series:
    return ts.dt.year.astype(str) + "-Q" + ts.dt.quarter.astype(str)


def _signed_money(v: float) -> str:
    return ("+" if v > 0 else "") + money(v)


def _apply(fig: go.Figure, title: str, subtitle: str | None = None, height: int | None = None) -> go.Figure:
    """theme.apply (which now wraps before escaping "$", so money titles wrap at the normal width)."""
    return theme.apply(fig, title, subtitle, height=height)


# =============================================================================== compute
def expansion_buckets() -> pd.DataFrame:
    """Each expanding customer-month is attributed to ONE driver bucket, so buckets partition
    net expansion exactly: Technology accounts (any component) vs non-Tech by the dominant
    positive component (seats / plan / other)."""
    comp = marts.load("fct_mrr_movement_components")
    mv = marts.load("fct_mrr_movements")[["customer_id", "month_end", "expansion_mrr"]]
    x = comp[comp["movement_type"] == "expansion"].merge(mv, on=["customer_id", "month_end"])
    pos = pd.DataFrame({
        "seats": x["comp_seats"].clip(lower=0),
        "plan": x["comp_plan"].clip(lower=0),
        "other": (x["comp_discount"].clip(lower=0) + x["comp_billing_period"].clip(lower=0) + x["comp_other"].clip(lower=0)),
    })
    dominant = pos.idxmax(axis=1).where(pos.sum(axis=1) > 0, "other")
    x["bucket"] = np.where(
        x["industry"] == "Technology", "Technology (all)",
        dominant.map({"seats": "Non-Tech seat expansion", "plan": "Non-Tech plan upgrades", "other": "Non-Tech other"}))
    return x[["customer_id", "month_end", "quarter_label", "industry", "segment", "bucket", "expansion_mrr",
              "comp_seats", "comp_plan"]]


def quarterly_base() -> pd.DataFrame:
    q = marts.load("rpt_mrr_quarterly")
    return q.set_index("quarter_label")


def _one_offs(b: pd.DataFrame, quarter: str, share_threshold: float = 0.10) -> pd.DataFrame:
    """Single-account expansions above `share_threshold` of the quarter's expansion (on an expansion_buckets frame)."""
    qb = b[b["quarter_label"] == quarter]
    by_cust = qb.groupby("customer_id", as_index=False)["expansion_mrr"].sum()
    total = by_cust["expansion_mrr"].sum()
    out = by_cust[by_cust["expansion_mrr"] >= share_threshold * total].copy() if total > 0 else by_cust.iloc[0:0].copy()
    out["share_of_quarter"] = out["expansion_mrr"] / total if total > 0 else np.nan
    return out


def identify_one_offs(quarter: str, share_threshold: float = 0.10) -> pd.DataFrame:
    """Single-account expansions above `share_threshold` of the quarter's expansion."""
    out = _one_offs(expansion_buckets(), quarter, share_threshold)
    cust = marts.load("stg_customers")[["customer_id", "company_name", "segment", "industry"]]
    return out.merge(cust, on="customer_id").sort_values("expansion_mrr", ascending=False)


def one_off_pairs(b: pd.DataFrame | None = None) -> set[tuple[str, str]]:
    """(customer_id, quarter_label) of every one-off flagged by the same rule in any quarter."""
    b = expansion_buckets() if b is None else b
    return {(c, q) for q in sorted(b["quarter_label"].unique()) for c in _one_offs(b, q)["customer_id"]}


def bridge(q_from: str, q_to: str, exclude_one_offs: bool = True) -> pd.DataFrame:
    """Sequential bridge of expansion MRR from q_from to q_to.

    Order: start -> remove one-off accounts from q_from -> base growth (q_from's like-for-like
    rate applied to q_to's starting MRR) -> rate change by driver bucket -> end.
    Driver steps = (bucket rate in q_to - bucket rate in q_from) x q_to's starting MRR, so the
    steps add up exactly (buckets do not overlap)."""
    b = expansion_buckets()
    qb = quarterly_base()
    one_offs = identify_one_offs(q_from)["customer_id"].tolist() if exclude_one_offs else []
    f = b[b["quarter_label"] == q_from]
    t = b[b["quarter_label"] == q_to]
    boq_f = qb.loc[q_from, "mrr_start_of_quarter"]
    boq_t = qb.loc[q_to, "mrr_start_of_quarter"]
    exp_f = f["expansion_mrr"].sum()
    one_off = f[f["customer_id"].isin(one_offs)]["expansion_mrr"].sum()
    f_ex = f[~f["customer_id"].isin(one_offs)]
    rate_f = f_ex["expansion_mrr"].sum() / boq_f
    base_effect = rate_f * boq_t - f_ex["expansion_mrr"].sum()
    rows = [{"step": f"{theme.qname(q_from)} expansion", "kind": "total", "value": exp_f}]
    if exclude_one_offs and one_off:
        rows.append({"step": "Remove one-off rollout", "kind": "delta", "value": -one_off})
    rows.append({"step": f"Base growth (starting MRR {pct(boq_t / boq_f - 1, 0, signed=True)})", "kind": "delta",
                 "value": base_effect})
    for k in BUCKETS:
        r_f = f_ex.loc[f_ex["bucket"] == k, "expansion_mrr"].sum() / boq_f
        r_t = t.loc[t["bucket"] == k, "expansion_mrr"].sum() / boq_t
        rows.append({"step": k, "kind": "delta", "value": (r_t - r_f) * boq_t, "rate_from": r_f, "rate_to": r_t})
    rows.append({"step": f"{theme.qname(q_to)} expansion", "kind": "total", "value": t["expansion_mrr"].sum()})
    out = pd.DataFrame(rows)
    out.insert(0, "bridge", f"{q_from} -> {q_to}")
    gap = out.loc[out.kind == "total", "value"].iloc[0] + out.loc[out.kind == "delta", "value"].sum() \
        - out.loc[out.kind == "total", "value"].iloc[-1]
    assert abs(gap) < 0.01, f"bridge does not tie out: {gap}"
    return out


def expansion_rate_by_bucket(separate_one_offs: bool = False) -> pd.DataFrame:
    """Quarterly expansion rate (expansion / company starting MRR) split by driver bucket. With
    `separate_one_offs`, customer-quarters flagged by the one-off rule get their own bucket instead of
    their driver bucket (so the driver buckets read like for like)."""
    b = expansion_buckets()
    if separate_one_offs:
        pairs = one_off_pairs(b)
        flag = [(c, q) in pairs for c, q in zip(b["customer_id"], b["quarter_label"])]
        b = b.assign(bucket=np.where(flag, ONE_OFF_BUCKET, b["bucket"]))
    qb = quarterly_base()["mrr_start_of_quarter"]
    g = b.groupby(["quarter_label", "bucket"], as_index=False)["expansion_mrr"].sum()
    g["mrr_start_of_quarter"] = g["quarter_label"].map(qb)
    g["rate"] = g["expansion_mrr"] / g["mrr_start_of_quarter"]
    return g[g["mrr_start_of_quarter"] > 0]


def bucket_rate_table(separate_one_offs: bool = True, start: str = "2024-Q4") -> pd.DataFrame:
    """Quarter x bucket matrix of expansion rates (share of company starting MRR); missing = 0."""
    g = expansion_rate_by_bucket(separate_one_offs)
    g = g[g["quarter_label"] >= start]
    cols = BUCKETS + ([ONE_OFF_BUCKET] if separate_one_offs else [])
    piv = g.pivot_table(index="quarter_label", columns="bucket", values="rate", aggfunc="sum")
    return piv.reindex(columns=cols).fillna(0.0).sort_index()


def main_step(s: pd.Series, q_from: str, q_to: str) -> tuple[str | None, float]:
    """Which quarter-on-quarter step between q_from and q_to carried most of the change, and its share of it."""
    w = s.loc[q_from:q_to]
    total = w.iloc[-1] - w.iloc[0]
    steps = w.diff().dropna()
    if not len(steps) or total == 0:
        return None, float("nan")
    k = steps.idxmin() if total < 0 else steps.idxmax()
    return k, float(steps[k] / total)


def industry_rates(industry: str = "Technology") -> pd.DataFrame:
    """Quarterly expansion / contraction / churn and seat additions / cuts of one industry on ITS OWN base
    (the industry's MRR at the start of the quarter), next to its contribution to the company expansion rate."""
    mv = marts.load("fct_mrr_movements")
    comp = marts.load("fct_mrr_movement_components")
    q = quarterly_base()
    mv_i, comp_i = mv[mv["industry"] == industry], comp[comp["industry"] == industry]
    rows = []
    for ql, r in q.iterrows():
        start = mv_i.loc[mv_i["month_end"] == pd.Timestamp(r["first_month_end"]), "mrr_prev"].sum()
        x, c = mv_i[mv_i["quarter_label"] == ql], comp_i[comp_i["quarter_label"] == ql]
        rows.append({"quarter_label": ql, "mrr_start": start, "company_mrr_start": r["mrr_start_of_quarter"],
                     "expansion_mrr": x["expansion_mrr"].sum(), "contraction_mrr": x["contraction_mrr"].sum(),
                     "churn_mrr": x["churn_mrr"].sum(), "seat_adds_mrr": c["comp_seats"].clip(lower=0).sum(),
                     "seat_cuts_mrr": -c["comp_seats"].clip(upper=0).sum()})
    out = pd.DataFrame(rows).set_index("quarter_label")
    den = out["mrr_start"].replace(0, np.nan)
    for k in ("expansion", "contraction", "churn"):
        out[f"{k}_rate"] = out[f"{k}_mrr"] / den
    out["seat_add_rate"] = out["seat_adds_mrr"] / den
    out["seat_cut_rate"] = out["seat_cuts_mrr"] / den
    out["share_of_company_mrr"] = out["mrr_start"] / out["company_mrr_start"]
    out["expansion_contribution"] = out["expansion_mrr"] / out["company_mrr_start"]
    return out


def seasonality_q2_q3() -> pd.DataFrame:
    """Q2 -> Q3 change in the expansion rate in prior years (n = 2): the only seasonal evidence."""
    q = quarterly_base()
    rows = []
    for y in (2024, 2025):
        r2, r3 = q.loc[f"{y}-Q2", "expansion_rate"], q.loc[f"{y}-Q3", "expansion_rate"]
        rows.append({"year": y, "q2_rate": r2, "q3_rate": r3, "change_pts": r3 - r2})
    return pd.DataFrame(rows)


def nrr_excluding(customer_ids: list[str]) -> pd.DataFrame:
    """TTM NRR / GRR recomputed without the given customers (e.g., the one-off rollout)."""
    f = marts.load("fct_customer_mrr_monthly")[["customer_id", "month_end", "mrr"]]
    f = f[~f["customer_id"].isin(customer_ids)]
    piv = f.pivot(index="customer_id", columns="month_end", values="mrr")
    rows = []
    for t in sorted(f["month_end"].unique()):
        t = pd.Timestamp(t)
        t0 = t - pd.offsets.MonthEnd(12)
        if t0 not in piv.columns:
            continue
        base = piv[t0]
        base = base[base > 0]
        now = piv.loc[base.index, t].fillna(0)
        rows.append({"month_end": t, "nrr_ex": now.sum() / base.sum(), "grr_ex": np.minimum(now, base).sum() / base.sum()})
    return pd.DataFrame(rows)


def top_movers(quarter: str, n: int = 10, filters: dict | None = None) -> pd.DataFrame:
    """Largest expansion / contraction / churn accounts in the quarter. mrr_start = MRR just before the first
    movement of that kind in the quarter (so a customer who joined and churned inside the quarter shows the MRR it
    churned from, not 0); mrr_end = MRR at the quarter's last month-end. `filters` = the dashboard's customer filters."""
    mv = _apply_filters(marts.load("fct_mrr_movements"), filters)
    q = mv[mv["quarter_label"] == quarter].sort_values(["customer_id", "month_end"])
    end = q.groupby("customer_id")["mrr"].last()
    cust = marts.load("stg_customers")[["customer_id", "company_name", "segment", "industry", "cs_coverage", "csm_owner"]]
    out = []
    for kind in ("expansion", "contraction", "churn"):
        col = f"{kind}_mrr"
        rows = q[q[col] > 0]
        g = rows.groupby("customer_id", as_index=False).agg(amount=(col, "sum"), mrr_start=("mrr_prev", "first"))
        g["mrr_end"] = g["customer_id"].map(end)
        top = g.nlargest(n, "amount").merge(cust, on="customer_id")
        top.insert(0, "mover_type", kind)
        out.append(top)
    return pd.concat(out, ignore_index=True)[[
        "mover_type", "customer_id", "company_name", "segment", "industry", "csm_owner", "mrr_start", "mrr_end", "amount"]]


def segment_nrr_compare(month_end: pd.Timestamp = AS_OF, dimensions: tuple = ("segment", "industry",
                        "acquisition_channel", "plan_at_start", "billing_at_start", "region")) -> pd.DataFrame:
    seg = marts.load("rpt_segment_retention_ttm")
    prev = month_end - pd.offsets.MonthEnd(12)
    a = seg[(seg["month_end"] == month_end) & seg["dimension"].isin(dimensions)]
    b = seg[(seg["month_end"] == prev) & seg["dimension"].isin(dimensions)][
        ["dimension", "dimension_value", "nrr", "grr", "base_customers"]].rename(columns={"base_customers": "base_customers_year_ago"})
    out = a.merge(b, on=["dimension", "dimension_value"], how="left", suffixes=("", "_year_ago"))
    out["nrr_change"] = out["nrr"] - out["nrr_year_ago"]
    return out.sort_values(["dimension", "base_mrr"], ascending=[True, False])


def nrr_change_interval(as_of: pd.Timestamp, group_col: str, n_boot: int = 1000, seed: int = 7,
                        min_base: int = MIN_GROUP_BASE, level: float = 0.95) -> pd.DataFrame:
    """Customer-level bootstrap interval for each group's change in 12-month NRR vs 12 months earlier
    (each window resampled on its own; groups take the base month's attribute, as in ttm_retention).
    Group NRR rests on 50–1,500 customers with a few large expansions, so changes of 5–10 pts can be noise."""
    col = _GROUP_COLUMN.get(group_col, group_col)
    f = marts.load("fct_customer_mrr_monthly")[["customer_id", "month_end", "mrr", col]]

    def window(t: pd.Timestamp) -> pd.DataFrame:
        t0 = t - pd.offsets.MonthEnd(12)
        base = f[(f["month_end"] == t0) & (f["mrr"] > 0)]
        now = f[f["month_end"] == t].set_index("customer_id")["mrr"]
        return base.assign(mrr_now=base["customer_id"].map(now).fillna(0.0))

    a, p = window(as_of), window(as_of - pd.offsets.MonthEnd(12))
    rng = np.random.default_rng(seed)

    def boot(x: pd.DataFrame) -> np.ndarray:
        m, n = x["mrr"].to_numpy(), x["mrr_now"].to_numpy()
        idx = rng.integers(0, len(x), size=(n_boot, len(x)))
        return n[idx].sum(axis=1) / m[idx].sum(axis=1)

    rows = []
    for g in sorted(set(a[col].dropna()) & set(p[col].dropna())):
        A, P = a[a[col] == g], p[p[col] == g]
        if len(A) < min_base or len(P) < min_base:
            continue
        d = boot(A) - boot(P)
        lo, hi = np.quantile(d, [(1 - level) / 2, 1 - (1 - level) / 2])
        rows.append({"group": g, "nrr_change": A["mrr_now"].sum() / A["mrr"].sum() - P["mrr_now"].sum() / P["mrr"].sum(),
                     "change_lo": float(lo), "change_hi": float(hi)})
    return pd.DataFrame(rows, columns=["group", "nrr_change", "change_lo", "change_hi"])


def cohort_matrix(metric: str = "nrr", grain: str = "quarter", filters: dict | None = None,
                  max_k: int = 24) -> pd.DataFrame:
    """Cohort x months-since-first-paid matrix computed from fct_customer_mrr_monthly in pandas
    (same rules as sql/27; used interactively with filters). Incomplete cells are NaN.
    Filters use customer columns; `billing_at_start` filters on the billing period of the first paid month."""
    f = marts.load("fct_customer_mrr_monthly")
    if filters:
        for col, vals in filters.items():
            if not vals:
                continue
            if col == "billing_at_start":
                first_bp = f.loc[f["months_since_first_paid"] == 0].set_index("customer_id")["billing_period"]
                f = f[f["customer_id"].map(first_bp).isin(vals)]
            else:
                f = f[f[col].isin(vals)]
    f = f[["customer_id", "month_end", "mrr", "cohort_month_end", "months_since_first_paid"]].copy()
    first = f.sort_values("month_end").groupby("customer_id")["mrr"].first()
    f["mrr_0"] = f["customer_id"].map(first)
    if grain == "quarter":
        f["cohort"] = quarter_of(f["cohort_month_end"])
    else:
        f["cohort"] = f["cohort_month_end"].dt.strftime("%Y-%m")
    f = f[f["months_since_first_paid"] <= max_k]
    size = f[f["months_since_first_paid"] == 0].groupby("cohort")["customer_id"].nunique()
    g = f.groupby(["cohort", "months_since_first_paid"]).agg(
        n=("customer_id", "nunique"), mrr=("mrr", "sum"), mrr_0=("mrr_0", "sum"),
        capped=("mrr", lambda s: np.minimum(s, f.loc[s.index, "mrr_0"]).sum()),
        alive=("mrr", lambda s: (s > 0).mean()))
    g = g.reset_index()
    g["complete"] = g["n"] == g["cohort"].map(size)
    g["value"] = {"nrr": g["mrr"] / g["mrr_0"], "grr": g["capped"] / g["mrr_0"], "logo": g["alive"]}[metric]
    g.loc[~g["complete"], "value"] = np.nan
    m = g.pivot(index="cohort", columns="months_since_first_paid", values="value")
    m["customers"] = size
    return m


def renewal_steps(m: pd.DataFrame, age: int = 12) -> pd.DataFrame:
    """Per cohort with month `age - 1` and `age` observed for the whole cohort: the value before, at `age`, the step,
    and the median month-on-month step over months 1..age-1 (what an ordinary month looks like for that cohort)."""
    m = m.drop(columns="customers", errors="ignore")
    if not {age - 1, age} <= set(m.columns):
        return pd.DataFrame(columns=["before", "at", "step", "typical_step"])
    full = m.dropna(subset=[age - 1, age])
    ordinary = full[[c for c in range(age) if c in full.columns]].diff(axis=1).iloc[:, 1:]
    return pd.DataFrame({"before": full[age - 1], "at": full[age], "step": full[age] - full[age - 1],
                         "typical_step": ordinary.median(axis=1)})


def renewal_summary(metric: str = "nrr", grain: str = "quarter", filters: dict | None = None, age: int = 12) -> dict:
    """What the cohort table says about the first annual renewal, computed (used by titles and documents)."""
    s = renewal_steps(cohort_matrix(metric, grain, filters, max_k=max(age, 24)), age)
    if s.empty:
        return {"cohorts": 0}
    worst = s["step"].idxmin()
    return {"cohorts": int(len(s)), "cohorts_dipping": int((s["step"] < 0).sum()),
            "exceptions": [str(c) for c in s.index[s["step"] >= 0]], "median_step": float(s["step"].median()),
            "median_typical_step": float(s["typical_step"].median()), "worst_cohort": str(worst),
            "worst_step": float(s.loc[worst, "step"]), "worst_value": float(s.loc[worst, "at"])}


def build() -> dict[str, pd.DataFrame]:
    """Derived tables written to the parquet cache (topic prefix `metrics__`)."""
    one_q2 = identify_one_offs(Q_PREV)
    tables = {
        "metrics__expansion_buckets": expansion_buckets(),
        "metrics__expansion_rate_by_bucket": expansion_rate_by_bucket(),
        "metrics__bridges": pd.concat([bridge(Q_PREV, Q_CUR), bridge(Q_LY, Q_CUR)], ignore_index=True),
        "metrics__one_offs": pd.concat([identify_one_offs(q).assign(quarter=q) for q in (Q_LY, Q_PREV, Q_CUR)],
                                       ignore_index=True),
        "metrics__seasonality": seasonality_q2_q3(),
        "metrics__nrr_ex_one_off": nrr_excluding(one_q2["customer_id"].tolist()),
        "metrics__top_movers": top_movers(Q_CUR),
        "metrics__segment_nrr": segment_nrr_compare(),
        "metrics__technology_rates": industry_rates("Technology").reset_index(),
    }
    for name, df in tables.items():
        marts.write(name, df)
    return tables


def key_numbers() -> dict:
    wf = marts.load("rpt_mrr_waterfall_monthly").set_index("month_end")
    q = quarterly_base()
    nrr = marts.load("rpt_nrr_grr_ttm").set_index("month_end")
    rr = marts.load("rpt_nrr_quarterly_runrate").set_index("quarter_label")
    nx = marts.load("metrics__nrr_ex_one_off").set_index("month_end")
    br = marts.load("metrics__bridges")
    one = marts.load("metrics__one_offs")
    seas = marts.load("metrics__seasonality")
    tech = marts.load("metrics__technology_rates").set_index("quarter_label")
    t, t_prev, t_ly = AS_OF, pd.Timestamp("2026-06-30"), pd.Timestamp("2025-09-30")
    one_q2 = one[one["quarter"] == Q_PREV].iloc[0]
    q2_ex_rate = (q.loc[Q_PREV, "expansion_mrr"] - one_q2["expansion_mrr"]) / q.loc[Q_PREV, "mrr_start_of_quarter"]
    qoq = br[br["bridge"] == f"{Q_PREV} -> {Q_CUR}"].set_index("step")
    yoy = br[br["bridge"] == f"{Q_LY} -> {Q_CUR}"].set_index("step")
    # the one-off account's seat change in the month it happened
    mv = marts.load("fct_mrr_movements")
    oo = mv[(mv["customer_id"] == one_q2["customer_id"]) & (mv["quarter_label"] == Q_PREV)]
    oo = oo.loc[oo["expansion_mrr"].idxmax()]
    # driver buckets like for like (one-off set apart), and when each driver's fall happened
    piv = bucket_rate_table(separate_one_offs=True, start=Q_LY)
    tech_q, tech_share = main_step(piv["Technology (all)"], Q_LY, Q_CUR)
    plan_q, plan_share = main_step(piv["Non-Tech plan upgrades"], Q_LY, Q_CUR)
    ren = renewal_summary("nrr", "quarter")
    ren_annual = renewal_summary("nrr", "quarter", {"billing_at_start": ["annual"]})
    ren_monthly = renewal_summary("nrr", "quarter", {"billing_at_start": ["monthly"]})
    ci = nrr_change_interval(t, "industry").set_index("group")
    seg = segment_nrr_compare(t)
    tseg = seg[(seg["dimension"] == "industry") & (seg["dimension_value"] == "Technology")].iloc[0]
    return {
        "as_of": str(t.date()),
        "mrr": float(wf.loc[t, "mrr_end"]),
        "arr": float(wf.loc[t, "arr_end"]),
        "paying_customers": int(wf.loc[t, "customers_end"]),
        "arpa": float(wf.loc[t, "arpa"]),
        "mrr_yoy_growth": float(wf.loc[t, "mrr_end"] / wf.loc[t_ly, "mrr_end"] - 1),
        "nrr_ttm": float(nrr.loc[t, "nrr"]), "grr_ttm": float(nrr.loc[t, "grr"]),
        "logo_retention_ttm": float(nrr.loc[t, "logo_retention"]),
        "nrr_ttm_2026_06": float(nrr.loc[t_prev, "nrr"]), "nrr_ttm_2025_09": float(nrr.loc[t_ly, "nrr"]),
        "grr_ttm_2025_09": float(nrr.loc[t_ly, "grr"]), "grr_ttm_2026_06": float(nrr.loc[t_prev, "grr"]),
        "nrr_ttm_ex_one_off": float(nx.loc[t, "nrr_ex"]), "nrr_ttm_2026_06_ex_one_off": float(nx.loc[t_prev, "nrr_ex"]),
        "nrr_ttm_2025_09_ex_one_off": float(nx.loc[t_ly, "nrr_ex"]),
        "nrr_base_customers": int(nrr.loc[t, "base_customers"]),
        "nrr_runrate_q3_2026": float(rr.loc[Q_CUR, "nrr_runrate_annualized"]),
        "nrr_runrate_q3_2025": float(rr.loc[Q_LY, "nrr_runrate_annualized"]),
        "nrr_runrate_q2_2026": float(rr.loc[Q_PREV, "nrr_runrate_annualized"]),
        "nrr_runrate_first_below_100": bool(rr.loc[Q_CUR, "nrr_runrate_annualized"] < 1
                                            and (rr.loc[rr.index < Q_CUR, "nrr_runrate_annualized"] >= 1).all()),
        "expansion_q3_2026": float(q.loc[Q_CUR, "expansion_mrr"]),
        "expansion_q2_2026": float(q.loc[Q_PREV, "expansion_mrr"]),
        "expansion_q3_2025": float(q.loc[Q_LY, "expansion_mrr"]),
        "expansion_change_qoq": float(q.loc[Q_CUR, "expansion_mrr"] / q.loc[Q_PREV, "expansion_mrr"] - 1),
        "expansion_rate_q3_2026": float(q.loc[Q_CUR, "expansion_rate"]),
        "expansion_rate_q2_2026": float(q.loc[Q_PREV, "expansion_rate"]),
        "expansion_rate_q2_2026_ex_one_off": float(q2_ex_rate),
        "expansion_rate_q3_2025": float(q.loc[Q_LY, "expansion_rate"]),
        "contraction_rate_q3_2026": float(q.loc[Q_CUR, "contraction_rate"]),
        "contraction_rate_q3_2025": float(q.loc[Q_LY, "contraction_rate"]),
        "churn_rate_q3_2026": float(q.loc[Q_CUR, "churn_rate"]),
        "churn_rate_q3_2025": float(q.loc[Q_LY, "churn_rate"]),
        "one_off_customer_id": one_q2["customer_id"], "one_off_company": one_q2["company_name"],
        "one_off_expansion_mrr": float(one_q2["expansion_mrr"]), "one_off_share_of_q2": float(one_q2["share_of_quarter"]),
        "one_off_month": f"{oo['month_end']:%Y-%m}", "one_off_seats_before": int(oo["seats_prev"]),
        "one_off_seats_after": int(oo["seats"]),
        "bridge_qoq": {k: float(v) for k, v in qoq["value"].items()},
        "bridge_yoy": {k: float(v) for k, v in yoy["value"].items()},
        "seasonality_q2_q3_change_pts": {int(r.year): float(r.change_pts) for r in seas.itertuples()},
        # --- Technology on its OWN base (Technology MRR at the start of the quarter). The answer key's
        #     "Tech expansion rate 1.47% -> 0.28%" is the contribution to the COMPANY rate (expansion_contribution).
        "tech_mrr_start_q3_2025": float(tech.loc[Q_LY, "mrr_start"]),
        "tech_mrr_start_q3_2026": float(tech.loc[Q_CUR, "mrr_start"]),
        "tech_share_of_mrr_q3_2026": float(tech.loc[Q_CUR, "share_of_company_mrr"]),
        **{f"tech_base_{m}_rate_{lab}": float(tech.loc[ql, f"{m}_rate"])
           for m in ("expansion", "contraction", "churn", "seat_add", "seat_cut")
           for ql, lab in ((Q_LY, "q3_2025"), (Q_PREV, "q2_2026"), (Q_CUR, "q3_2026"))},
        "tech_expansion_contribution_q3_2025": float(tech.loc[Q_LY, "expansion_contribution"]),
        "tech_expansion_contribution_q3_2026": float(tech.loc[Q_CUR, "expansion_contribution"]),
        "tech_nrr_2025_09": float(tseg["nrr_year_ago"]), "tech_nrr_2026_09": float(tseg["nrr"]),
        "tech_nrr_change_lo": float(ci.loc["Technology", "change_lo"]),
        "tech_nrr_change_hi": float(ci.loc["Technology", "change_hi"]),
        "industry_nrr_falls_clear_of_noise": [str(g) for g in ci.index[ci["change_hi"] < 0]],
        # --- driver buckets, share of company starting MRR, one-off set apart (the expansion-by-driver chart)
        "expansion_contribution_by_bucket": {k: {ql: float(v) for ql, v in piv[k].items()} for k in piv.columns},
        "tech_expansion_fall_main_quarter": tech_q, "tech_expansion_fall_main_share": tech_share,
        "plan_upgrade_fall_main_quarter": plan_q, "plan_upgrade_fall_main_share": plan_share,
        # --- cohorts at the first annual renewal (month 11 -> 12), quarterly cohorts, NRR
        "cohort_month12": ren,
        "cohort_month12_annual_median_step": ren_annual.get("median_step"),
        "cohort_month12_monthly_median_step": ren_monthly.get("median_step"),
    }


# =============================================================================== figures
def _month_axis(fig: go.Figure, months: pd.Series | None = None) -> None:
    fig.update_xaxes(tickformat="%b %Y", dtick="M3")
    if months is not None and len(months):
        x = theme.month_x(pd.Series(months))
        fig.update_xaxes(range=[x.min() - pd.Timedelta(days=20), x.max() + pd.Timedelta(days=20)],
                         tickvals=theme.quarter_ticks(months))


def fig_mrr_trend(start: str | None = None) -> go.Figure:
    full = marts.load("rpt_mrr_waterfall_monthly")
    wf = full[full["month_end"] >= start] if start else full
    last = wf.iloc[-1]
    year_ago = full[full["month_end"] == last["month_end"] - pd.offsets.MonthEnd(12)]
    fig = go.Figure(go.Scatter(
        x=theme.month_x(wf["month_end"]), y=wf["mrr_end"], mode="lines", line=dict(color=theme.ACCENT, width=2),
        fill="tozeroy", fillcolor="rgba(42,120,214,0.10)", name="MRR",
        customdata=np.c_[wf["arr_end"], wf["customers_end"]],
        hovertemplate="%{x|%b %Y}<br>MRR %{y:$,.0f}<br>ARR %{customdata[0]:$,.0f}<br>%{customdata[1]:,} paying customers<extra></extra>"))
    fig.add_trace(go.Scatter(x=theme.month_x(pd.Series([last["month_end"]])), y=[last["mrr_end"]], mode="markers+text",
                             marker=dict(color=theme.ACCENT, size=9, line=dict(color=theme.SURFACE, width=2)),
                             text=[theme.esc(money(last["mrr_end"]))], textposition="top left",
                             textfont=dict(color=theme.INK), hoverinfo="skip", showlegend=False))
    fig.update_yaxes(tickprefix="$", tickformat="~s", range=[0, wf["mrr_end"].max() * 1.12])
    _month_axis(fig, wf["month_end"])
    fig.update_layout(showlegend=False)
    if len(year_ago):
        g = last["mrr_end"] / year_ago["mrr_end"].iloc[0] - 1
        title = (f"MRR {'grew' if g >= 0 else 'fell'} {pct(abs(g), 0)} in a year to {money(last['mrr_end'])} "
                 f"(ARR {money(last['arr_end'])}) at {last['month_end']:%b %Y}")
    else:
        title = f"MRR reached {money(last['mrr_end'])} (ARR {money(last['arr_end'])}) by {last['month_end']:%b %Y}"
    return _apply(fig, title, "Month-end MRR, USD; annual contracts counted as 1/12 per month", height=360)


def fig_monthly_movements(wf: pd.DataFrame | None = None, start: str | None = "2024-10-31",
                          title: str | None = None, note: str | None = None) -> go.Figure:
    """Stacked monthly movements: new / expansion / reactivation above zero, contraction / churn below.
    The default title compares the latest quarter's losses with its gains, over the whole series (not only the
    months drawn)."""
    full = marts.load("rpt_mrr_waterfall_monthly") if wf is None else wf
    wf = full[full["month_end"] >= start] if start else full
    fig = go.Figure()
    cols = {"new": "new_mrr", "expansion": "expansion_mrr", "reactivation": "reactivation_mrr",
            "contraction": "contraction_mrr", "churn": "churn_mrr"}
    for m in MOVEMENTS_UP + MOVEMENTS_DOWN:
        sign = 1 if m in MOVEMENTS_UP else -1
        fig.add_trace(go.Bar(x=theme.month_x(wf["month_end"]), y=sign * wf[cols[m]], name=MOVEMENT_LABELS[m],
                             marker=dict(color=MOVEMENT_COLORS[m], line=dict(color=theme.SURFACE, width=1)),
                             hovertemplate="%{x|%b %Y}<br>" + MOVEMENT_LABELS[m] + " %{y:$,.0f}<extra></extra>"))
    net = wf["new_mrr"] + wf["expansion_mrr"] + wf["reactivation_mrr"] - wf["contraction_mrr"] - wf["churn_mrr"]
    fig.add_trace(go.Scatter(x=theme.month_x(wf["month_end"]), y=net, name="Net new MRR", mode="lines+markers",
                             line=dict(color=theme.INK, width=2), marker=dict(size=7, color=theme.INK),
                             hovertemplate="%{x|%b %Y}<br>Net new MRR %{y:$,.0f}<extra></extra>"))
    fig.update_layout(barmode="relative", bargap=0.3)
    fig.update_yaxes(tickprefix="$", tickformat="~s", zeroline=True, zerolinecolor=theme.AXIS, zerolinewidth=1)
    _month_axis(fig, wf["month_end"])
    if title is None:
        qs = full.assign(_q=quarter_of(pd.to_datetime(full["month_end"]))).groupby("_q")[list(cols.values())].sum()
        cur = qs.index.max()
        r = qs.loc[cur]
        gains, losses = r["expansion_mrr"] + r["reactivation_mrr"], r["contraction_mrr"] + r["churn_mrr"]
        net_q = r["new_mrr"] + gains - losses
        earlier = qs.loc[qs.index < cur]
        first = bool(len(earlier)) and bool(((earlier["contraction_mrr"] + earlier["churn_mrr"])
                                             <= (earlier["expansion_mrr"] + earlier["reactivation_mrr"])).all())
        if losses > gains:
            tail = (f"new logos kept net new MRR positive at {money(net_q)}" if net_q >= 0
                    else f"net new MRR was {money(net_q)} even with new logos")
            title = (f"{theme.qname(cur)}: contraction and churn ({money(losses)}) outran expansion and win-backs "
                     f"({money(gains)}){' for the first time' if first else ''}; {tail}")
        else:
            title = (f"{theme.qname(cur)}: expansion and win-backs ({money(gains)}) still outran contraction and churn "
                     f"({money(losses)}); net new MRR {money(net_q)}")
    sub = "Monthly MRR movements, USD (gains above zero, losses below); black line = net new MRR"
    return _apply(fig, title, sub + (f" · {note}" if note else ""), height=430)


def fig_quarter_waterfall(quarter: str = Q_CUR, q: pd.DataFrame | None = None) -> go.Figure:
    """Classic waterfall for one quarter: start MRR -> movements -> end MRR (floating bars)."""
    q = (q if q is not None else quarterly_base())
    r = q.loc[quarter]
    steps = [("Start MRR", r["mrr_start_of_quarter"], "total", theme.INK_2),
             ("New", r["new_mrr"], "up", MOVEMENT_COLORS["new"]),
             ("Expansion", r["expansion_mrr"], "up", MOVEMENT_COLORS["expansion"]),
             ("Reactivation", r["reactivation_mrr"], "up", MOVEMENT_COLORS["reactivation"]),
             ("Contraction", -r["contraction_mrr"], "down", MOVEMENT_COLORS["contraction"]),
             ("Churn", -r["churn_mrr"], "down", MOVEMENT_COLORS["churn"]),
             ("End MRR", r["mrr_end_of_quarter"], "total", theme.INK_2)]
    x, base, y, colors, text = [], [], [], [], []
    level = 0.0
    for name, v, kind, c in steps:
        x.append(name)
        colors.append(c)
        if kind == "total":
            base.append(0)
            y.append(v)
            level = v
            text.append(money(v))
        else:
            base.append(level if v >= 0 else level + v)
            y.append(abs(v))
            level += v
            text.append(("+" if v >= 0 else "−") + money(abs(v)))
    fig = go.Figure(go.Bar(x=x, y=y, base=base, marker=dict(color=colors), text=[theme.esc(s) for s in text],
                           textposition="outside", textfont=dict(color=theme.INK), cliponaxis=False,
                           hovertemplate="%{x}: %{text}<extra></extra>"))
    positive = [b for b in base if b > 0]
    lo = min(positive) * 0.92 if positive else 0
    fig.update_yaxes(range=[lo, max(b + h for b, h in zip(base, y)) * 1.04], tickprefix="$", tickformat="~s")
    fig.update_layout(showlegend=False, bargap=0.45)
    start, end = r["mrr_start_of_quarter"], r["mrr_end_of_quarter"]
    net = end - start
    existing = r["expansion_mrr"] + r["reactivation_mrr"] - r["contraction_mrr"] - r["churn_mrr"]
    growth = f"{'grew' if net >= 0 else 'fell'} {pct(abs(net) / start, 1)}" if start else "started"
    title = (f"{theme.qname(quarter)}: MRR {growth} to {money(end)}; new logos added {money(r['new_mrr'])} and the "
             f"existing base {'added' if existing >= 0 else 'lost'} {money(abs(existing))} net")
    return _apply(fig, title, f"Quarter MRR bridge, USD: {money(start)} at the start of the quarter → {money(end)} "
                              "at the end; y-axis starts above zero to show the movements", height=400)


def _bridge_title(b: pd.DataFrame, q_from: str, q_to: str, exclude_one_offs: bool) -> str:
    """Conclusion of a bridge: how far expansion landed from what q_from's rate implies for the bigger base, and which
    drivers made the gap (start + one-off + base growth + drivers = end, so drivers = the gap exactly)."""
    d = b[b["kind"] == "delta"].set_index("step")["value"]
    one = float(d.get("Remove one-off rollout", 0.0))
    drv = d[[s for s in d.index if s in BUCKETS]]
    gap = float(drv.sum())               # = end - (start + one-off step + base growth): the gap the drivers explain
    side = drv[np.sign(drv) == np.sign(gap)].sort_values(key=lambda s: -s.abs())
    other = drv[np.sign(drv) == -np.sign(gap)].sort_values(key=lambda s: -s.abs())
    contains = set()
    if not exclude_one_offs:
        ids = set(identify_one_offs(q_from)["customer_id"])
        bb = expansion_buckets()
        contains = set(bb[(bb["quarter_label"] == q_from) & bb["customer_id"].isin(ids)]["bucket"])

    def name(k: str) -> str:
        return BUCKET_SHORT[k] + (" (includes the one-off)" if k in contains else "")

    head = f"Excluding the {money(-one)} one-off rollout, " if one else ""
    core = (f"{head}{theme.qname(q_to)} expansion was {money(abs(gap))} {'short of' if gap < 0 else 'above'} "
            f"{theme.qname(q_from)}'s rate on the bigger base")
    big = side[side.abs() >= 0.2 * abs(gap)] if gap else side.iloc[0:0]
    if len(big) == 1 and abs(big.iloc[0]) >= 0.9 * abs(gap):
        core += f" — almost all of it {name(big.index[0])} ({_signed_money(big.iloc[0])})"
    elif len(big):
        core += ": " + " and ".join(f"{name(k)} {_signed_money(v)}" for k, v in big.items())
    off = other[other.abs() >= 0.2 * abs(gap)] if gap else other.iloc[0:0]
    if len(off):
        core += ", partly offset by " + " and ".join(f"{name(k)} {_signed_money(v)}" for k, v in off.items())
    return core


def fig_bridge(which: str = "qoq", exclude_one_offs: bool = True) -> go.Figure:
    """Horizontal bridge (steps read top to bottom) — long step names stay legible."""
    q_from = Q_PREV if which == "qoq" else Q_LY
    b = bridge(q_from, Q_CUR, exclude_one_offs=exclude_one_offs)
    y, base, x, colors, text = [], [], [], [], []
    level = 0.0
    for r in b.itertuples():
        y.append(r.step)
        if r.kind == "total":
            base.append(0)
            x.append(r.value)
            level = r.value
            colors.append(theme.INK_2)
            text.append(money(r.value))
        else:
            base.append(level if r.value >= 0 else level + r.value)
            x.append(abs(r.value))
            level += r.value
            colors.append(UP_COLOR if r.value >= 0 else DOWN_COLOR)
            text.append(("+" if r.value >= 0 else "−") + money(abs(r.value)))
    fig = go.Figure(go.Bar(y=y, x=x, base=base, orientation="h", marker=dict(color=colors),
                           text=[theme.esc(s) for s in text], textposition="outside", textfont=dict(color=theme.INK),
                           cliponaxis=False, hovertemplate="%{y}: %{text}<extra></extra>"))
    fig.update_xaxes(tickprefix="$", tickformat="~s", rangemode="tozero", showgrid=True, gridcolor=theme.GRID)
    fig.update_yaxes(autorange="reversed", showgrid=False, automargin=True)
    fig.update_layout(showlegend=False, bargap=0.35, margin=dict(l=40, r=70))
    start, end = b["value"].iloc[0], b["value"].iloc[-1]
    label = "sequential" if which == "qoq" else "same quarter last year"
    sub = (f"Expansion MRR per quarter, USD: {theme.qname(q_from)} {money(start)} → {theme.qname(Q_CUR)} {money(end)} "
           f"({label}). Blue adds, red takes away; driver step = change in the driver's rate × {theme.qname(Q_CUR)} "
           "starting MRR")
    if not exclude_one_offs:
        sub += "; the one-off rollout is left inside its driver"
    return _apply(fig, _bridge_title(b, q_from, Q_CUR, exclude_one_offs), sub, height=420)


def fig_expansion_rate_by_bucket(exclude_one_offs: bool = True, start: str = "2024-Q4") -> go.Figure:
    """Stacked expansion rate by driver. With `exclude_one_offs` (the page's "Remove the one-off rollout" toggle) the
    one-off rollout is its own hatched segment, so the coloured drivers and the bar label read like for like."""
    piv = bucket_rate_table(separate_one_offs=exclude_one_offs, start=start)
    xq = [theme.qname(q) for q in piv.index]
    fig = go.Figure()
    for k in BUCKETS:
        fig.add_trace(go.Bar(x=xq, y=piv[k], name=k,
                             marker=dict(color=BUCKET_COLORS[k], line=dict(color=theme.SURFACE, width=1)),
                             hovertemplate="%{x}<br>" + k + " %{y:.2%} of starting MRR<extra></extra>"))
    one = piv[ONE_OFF_BUCKET] if ONE_OFF_BUCKET in piv.columns else pd.Series(0.0, index=piv.index)
    if one.sum() > 0:
        names = identify_one_offs(one.idxmax())["company_name"].tolist()
        fig.add_trace(go.Bar(x=xq, y=one, name=f"{ONE_OFF_BUCKET} ({', '.join(names)})" if names else ONE_OFF_BUCKET,
                             marker=dict(color=theme.SURFACE, line=dict(color=theme.MUTED, width=1),
                                         pattern=dict(shape="/", fgcolor=theme.DEEMPH, bgcolor=theme.SURFACE, solidity=0.25)),
                             text=[f"+{pct(o, 1)}" if o > 0 else "" for o in one], textposition="inside",
                             insidetextanchor="middle", textfont=dict(color=theme.INK, size=12),
                             hovertemplate="%{x}<br>One-off rollout %{y:.2%} of starting MRR (set apart)<extra></extra>"))
    lfl = piv[BUCKETS].sum(axis=1)
    labels = [pct(v, 1) + (f"<br><span style='font-size:11px;color:{theme.MUTED}'>excl. one-off</span>" if o > 0 else "")
              for v, o in zip(lfl, one)]
    fig.add_trace(go.Scatter(x=xq, y=lfl + one, mode="text", text=labels, textposition="top center",
                             textfont=dict(color=theme.INK), showlegend=False, hoverinfo="skip", cliponaxis=False))
    fig.update_layout(barmode="stack", bargap=0.45, legend=dict(traceorder="normal"))
    fig.update_yaxes(tickformat=".0%", range=[0, float((lfl + one).max()) * 1.2])
    # title: the two drivers that fell, Q3 2025 -> Q3 2026, and the quarter that carried most of each fall
    q_from, q_to = (Q_LY, Q_CUR) if {Q_LY, Q_CUR} <= set(piv.index) else (piv.index[0], piv.index[-1])

    def change(k: str, unit: str = "") -> str:
        """'fell from 1.5% to 0.3%{unit}, mostly in Q3 2026' — the 'mostly' only when one quarter carried half of it."""
        s = piv[k]
        when, share = main_step(s, q_from, q_to)
        verb = "fell" if s[q_to] < s[q_from] else "rose"
        tail = f", mostly in {theme.qname(when)}" if when and share >= 0.5 else ""
        return f"{verb} from {pct(s[q_from], 1)} to {pct(s[q_to], 1)}{unit}{tail}"

    year = int(q_to[:4]) - int(q_from[:4]) == 1 and q_to[-2:] == q_from[-2:]
    span = " in a year" if year else f" ({theme.qname(q_from)} → {theme.qname(q_to)})"
    title = (f"Technology's expansion {change('Technology (all)', ' of starting MRR' + span)}; "
             f"non-Tech plan upgrades {change('Non-Tech plan upgrades')}")
    tr = industry_rates("Technology")
    sub = ("Expansion MRR ÷ company MRR at the start of the quarter, by driver; label = sum of the drivers"
           + ("; hatched = the one-off rollout, set apart" if one.sum() > 0 else ""))
    if {q_from, q_to} <= set(tr.index):
        sub += (f". Technology on its own MRR: {pct(tr.loc[q_from, 'expansion_rate'], 1)} → "
                f"{pct(tr.loc[q_to, 'expansion_rate'], 1)}")
    return _apply(fig, title, sub, height=440)


def _end_label(fig: go.Figure, x, y: float, text: str, color: str) -> None:
    fig.add_trace(go.Scatter(x=[x], y=[y], mode="markers+text", text=[text], textposition="middle right",
                             textfont=dict(color=theme.INK, size=12),
                             marker=dict(size=9, color=color, line=dict(color=theme.SURFACE, width=2)),
                             showlegend=False, hoverinfo="skip", cliponaxis=False))


def fig_nrr_trend() -> go.Figure:
    n = marts.load("rpt_nrr_grr_ttm")
    nx = marts.load("metrics__nrr_ex_one_off")
    n = n.merge(nx, on="month_end", how="left")
    fig = go.Figure()
    rep, early = n[n["is_reportable"]], n[~n["is_reportable"]]
    last = rep.iloc[-1]
    x_last = theme.month_x(pd.Series([last["month_end"]])).iloc[0]
    # early points (base < 1,000 customers) in light gray, reportable points in colour; each line labelled at its end
    for col, name, color in (("nrr", "NRR", theme.ACCENT), ("grr", "GRR", GRR_COLOR)):
        fig.add_trace(go.Scatter(x=theme.month_x(early["month_end"]), y=early[col], mode="lines",
                                 line=dict(color=theme.DEEMPH, width=2), name="Base under 1,000 customers",
                                 showlegend=(col == "nrr"), legendgroup="early",
                                 hovertemplate="%{x|%b %Y}<br>" + name + " %{y:.1%} (small base)<extra></extra>"))
        joined = pd.concat([early.tail(1), rep])
        fig.add_trace(go.Scatter(x=theme.month_x(joined["month_end"]), y=joined[col], mode="lines",
                                 line=dict(color=color, width=2), name=f"{name} (TTM)", customdata=joined["base_customers"],
                                 hovertemplate="%{x|%b %Y}<br>" + name + " %{y:.1%}<br>base %{customdata:,} customers<extra></extra>"))
    fig.add_trace(go.Scatter(x=theme.month_x(rep["month_end"]), y=rep["nrr_ex"], mode="lines",
                             line=dict(color=theme.ACCENT, width=1.5, dash="dot"), name="NRR excl. one-off rollout",
                             hovertemplate="%{x|%b %Y}<br>NRR excl. one-off %{y:.1%}<extra></extra>"))
    _end_label(fig, x_last, last["nrr"], f"NRR {pct(last['nrr'])}", theme.ACCENT)
    _end_label(fig, x_last, last["nrr_ex"], f"excl. one-off {pct(last['nrr_ex'])}", theme.ACCENT)
    _end_label(fig, x_last, last["grr"], f"GRR {pct(last['grr'])}", GRR_COLOR)
    fig.add_hline(y=1.0, line=dict(color=theme.AXIS, width=1))
    fig.update_yaxes(tickformat=".0%")
    xs = theme.month_x(n["month_end"])
    fig.update_xaxes(tickformat="%b %Y", tickvals=theme.quarter_ticks(n["month_end"]),
                     range=[xs.min() - pd.Timedelta(days=20), xs.max() + pd.Timedelta(days=150)])
    # title: the change since the previous quarter-end, with and without the one-off
    prev = n[n["month_end"] == last["month_end"] - pd.offsets.MonthEnd(3)].iloc[0]

    def move(d: float) -> str:
        return "held" if abs(d) < 0.0005 else f"{'fell' if d < 0 else 'rose'} {pts(abs(d)).lstrip('+')}"

    title = (f"12-month NRR {move(last['nrr'] - prev['nrr'])} since {prev['month_end']:%B} to {pct(last['nrr'])} "
             f"({pct(last['nrr_ex'])} excl. the one-off rollout); GRR {move(last['grr'] - prev['grr'])} to {pct(last['grr'])}")
    return theme.apply(fig, title, "Same customers as 12 months earlier, point-to-point; light gray = base under 1,000 "
                                   "customers; dotted = NRR without the one-off rollout", height=420)


def fig_nrr_runrate() -> go.Figure:
    full = marts.load("rpt_nrr_quarterly_runrate")
    rr = full[full["quarter_label"] >= "2024-Q4"]
    colors = [theme.ACCENT if q == Q_CUR else theme.DEEMPH for q in rr["quarter_label"]]
    fig = go.Figure(go.Bar(x=rr["quarter_label"].map(theme.qname), y=rr["nrr_runrate_annualized"] - 1, base=1, marker=dict(color=colors),
                           text=[pct(v, 1) for v in rr["nrr_runrate_annualized"]], textposition="outside",
                           textfont=dict(color=theme.INK), cliponaxis=False,
                           hovertemplate="%{x}<br>run-rate NRR %{text}<extra></extra>"))
    fig.add_hline(y=1.0, line=dict(color=theme.AXIS, width=1))
    fig.update_yaxes(tickformat=".0%")
    fig.update_layout(showlegend=False, bargap=0.45)
    v = full.set_index("quarter_label")["nrr_runrate_annualized"]
    first_below = v[Q_CUR] < 1 and bool((v[v.index < Q_CUR] >= 1).all())
    lead = (f"{theme.qname(Q_CUR)} run-rate NRR fell below 100% for the first time" if first_below
            else f"{theme.qname(Q_CUR)} run-rate NRR")
    return theme.apply(fig, f"{lead}: {pct(v[Q_CUR])} vs {pct(v[Q_LY])} a year earlier",
                       "Quarterly same-customer retention, annualised (^4); a leading view — noisier than TTM",
                       height=360)


def dumbbell_title(df: pd.DataFrame, label: str, ci: pd.DataFrame | None = None) -> str:
    """Lead with the biggest fall (or, if nothing fell, the biggest rise), then the lowest group now.
    df: group, nrr, nrr_year_ago. ci (optional, from nrr_change_interval): says whether the lead change is
    clear of sampling noise (its 95% bootstrap interval excludes zero)."""
    s = df.dropna(subset=["nrr_year_ago"]).assign(chg=lambda d: d["nrr"] - d["nrr_year_ago"])
    if s.empty:
        if df.empty:
            return f"12-month NRR by {label}: no group has 30 base customers"
        low = df.loc[df["nrr"].astype(float).idxmin()]
        return (f"12-month NRR by {label}: lowest is {low['group']} at {pct(low['nrr'], 0)} (no comparison: the base 12 "
                "months earlier was too small)")
    low = df.loc[df["nrr"].astype(float).idxmin()]          # lowest of every group drawn, compared or not
    mover = s.loc[s["chg"].idxmin()] if (s["chg"] < 0).any() else s.loc[s["chg"].idxmax()]
    fell = mover["chg"] < 0
    only = len(s) == 1
    lead = (f"{mover['group']} {'fell' if fell else 'rose'}{'' if only else ' most'}, {pct(mover['nrr_year_ago'], 0)} → "
            f"{pct(mover['nrr'], 0)}")
    notes = ["the only group with 30 base customers a year earlier"] if only else []
    if ci is not None and len(ci):
        c = ci.set_index("group")
        clear = c.index[c["change_hi"] < 0] if fell else c.index[c["change_lo"] > 0]
        if mover["group"] in clear and len(clear) == 1 and not only:
            lead += f" — the only {'fall' if fell else 'rise'} clear of sampling noise"
        elif mover["group"] in c.index and mover["group"] not in clear:
            notes.append("within sampling noise")
    if notes:
        lead += f" ({'; '.join(notes)})"
    tail = " and is now the lowest" if low["group"] == mover["group"] else f"; lowest now is {low['group']} at {pct(low['nrr'], 0)}"
    return f"12-month NRR by {label}: {lead}{tail}"


def label_groups(s: pd.Series, dimension: str) -> pd.Series:
    """Display names for group values (plan ids, billing periods)."""
    m = GROUP_LABELS.get(dimension)
    return s.map(lambda v: m.get(v, v)) if m else s


def fig_segment_dumbbell(dimension: str = "industry", month_end: pd.Timestamp = AS_OF) -> go.Figure:
    s = segment_nrr_compare(month_end)
    s = s[(s["dimension"] == dimension) & (s["base_customers"] >= MIN_GROUP_BASE)].copy()
    s.loc[s["base_customers_year_ago"] < MIN_GROUP_BASE, "nrr_year_ago"] = np.nan
    ci = nrr_change_interval(month_end, dimension)
    s = s.merge(ci[["group", "change_lo", "change_hi"]], left_on="dimension_value", right_on="group", how="left")
    s["group"] = label_groups(s["dimension_value"], dimension)
    ci = ci.assign(group=label_groups(ci["group"], dimension))
    label = dimension.replace("_at_start", " at start").replace("_", " ")
    return fig_dumbbell(s, dumbbell_title(s, label, ci),
                        "Gray = 12 months earlier, blue = now; groups with at least 30 base customers; 'sampling noise' = "
                        "95% bootstrap interval of the change includes zero", f"{month_end:%b %Y}")


def _renewal_title(m: pd.DataFrame, metric: str) -> str:
    short = {"nrr": "NRR", "grr": "GRR", "logo": "customers"}[metric]
    s = renewal_steps(m, 12)
    if s.empty:
        m6 = m.drop(columns="customers", errors="ignore")
        m6 = m6[6].dropna() if 6 in m6.columns else pd.Series(dtype=float)
        if len(m6) >= 2:
            return (f"At month 6, cohorts range from {pct(m6.min(), 0)} ({m6.idxmin()}) to {pct(m6.max(), 0)} "
                    f"({m6.idxmax()}); no cohort has a full year of history yet")
        return "Too few complete cohorts to compare yet"
    n, down = len(s), int((s["step"] < 0).sum())
    worst = s["step"].idxmin()
    size = int(m["customers"].get(worst, 0)) if "customers" in m.columns else 0
    small = f" (n = {size:,})" if 0 < size < 100 else ""
    if down == 0:
        return f"None of the {n} cohorts that reached month 12, the first annual renewal, lose {short} there"
    who = f"all {n} cohorts that got there lose" if down == n else f"{down} of the {n} cohorts that got there lose"
    return (f"At month 12, the first annual renewal, {who} {short} (median {pts(s['step'].median())} vs "
            f"{pts(s['typical_step'].median()).replace(' pts', '')} in an ordinary month); {worst} falls furthest, "
            f"to {pct(s.loc[worst, 'at'], 0)}{small}")


def fig_cohort_heatmap(metric: str = "nrr", grain: str = "quarter", filters: dict | None = None,
                       max_k: int = 24) -> go.Figure:
    m = cohort_matrix(metric, grain, filters, max_k)
    title = _renewal_title(m, metric)
    sizes = m.pop("customers")
    z = m.values
    text = np.where(np.isnan(z), "", np.vectorize(lambda v: f"{v * 100:.0f}")(np.nan_to_num(z)))
    if metric == "nrr":
        colorscale, zmid, zmin, zmax = theme.DIVERGING_SCALE, 1.0, 0.6, 1.4
    else:
        colorscale, zmid, zmin, zmax = theme.SEQ_SCALE, None, 0.3, 1.0
    fig = go.Figure(go.Heatmap(
        z=z, x=[str(c) for c in m.columns], y=[f"{c} (n={int(sizes.get(c, 0)):,})" for c in m.index],
        colorscale=colorscale, zmid=zmid, zmin=zmin, zmax=zmax, xgap=2, ygap=2,
        text=text, texttemplate="%{text}", textfont=dict(size=10),
        colorbar=dict(tickformat=".0%", thickness=12, outlinewidth=0, title=dict(text="")),
        hovertemplate="cohort %{y}<br>month %{x}<br>%{z:.1%}<extra></extra>"))
    fig.update_yaxes(autorange="reversed", showgrid=False, automargin=True)
    fig.update_xaxes(title_text="Months since first paid month", showgrid=False, dtick=3)
    fig.update_layout(margin=dict(l=150))
    names = {"nrr": "Net revenue retention", "grr": "Gross revenue retention", "logo": "Logo retention"}
    return theme.apply(fig, title,
                       f"{names[metric]} (%) by cohort and months since first payment. Each row = customers whose first "
                       "paid month falls in that period; blank = not yet observed for the whole cohort",
                       height=max(380, 120 + 26 * len(m)))


# =============================================================================== interactive helpers
FILTER_COLUMNS = ("segment", "industry", "acquisition_channel", "region")


def _apply_filters(df: pd.DataFrame, filters: dict | None) -> pd.DataFrame:
    if not filters:
        return df
    for col, vals in filters.items():
        if vals:
            df = df[df[col].isin(vals)]
    return df


def monthly_waterfall(filters: dict | None = None) -> pd.DataFrame:
    """Recompute the monthly waterfall for a filtered set of customers (same rules as sql/23)."""
    mv = _apply_filters(marts.load("fct_mrr_movements"), filters)
    g = mv.groupby("month_end").agg(
        mrr_start=("mrr_prev", "sum"), new_mrr=("new_mrr", "sum"), expansion_mrr=("expansion_mrr", "sum"),
        reactivation_mrr=("reactivation_mrr", "sum"), contraction_mrr=("contraction_mrr", "sum"),
        churn_mrr=("churn_mrr", "sum"), mrr_end=("mrr", "sum"),
        customers_end=("mrr", lambda s: int((s > 0).sum())))
    g = g.reset_index()
    g["quarter_label"] = quarter_of(g["month_end"])
    g["arr_end"] = g["mrr_end"] * 12
    return g


def quarterly_from_monthly(wf: pd.DataFrame) -> pd.DataFrame:
    q = wf.sort_values("month_end").groupby("quarter_label").agg(
        mrr_start_of_quarter=("mrr_start", "first"), mrr_end_of_quarter=("mrr_end", "last"),
        new_mrr=("new_mrr", "sum"), expansion_mrr=("expansion_mrr", "sum"), reactivation_mrr=("reactivation_mrr", "sum"),
        contraction_mrr=("contraction_mrr", "sum"), churn_mrr=("churn_mrr", "sum"))
    for c in ("new", "expansion", "contraction", "churn", "reactivation"):
        q[f"{c}_rate"] = q[f"{c}_mrr"] / q["mrr_start_of_quarter"].replace(0, np.nan)
    return q


def ttm_retention(as_of: pd.Timestamp, group_col: str | None = None, filters: dict | None = None,
                  min_base: int = MIN_GROUP_BASE) -> pd.DataFrame:
    """TTM NRR / GRR / logo retention at any month-end, optionally by a grouping column
    (same rules as sql/25-26: base = paying at as_of - 12 months; groups use the base month's attributes)."""
    f = _apply_filters(marts.load("fct_customer_mrr_monthly"), filters)
    t0 = as_of - pd.offsets.MonthEnd(12)
    base = f[(f["month_end"] == t0) & (f["mrr"] > 0)]
    now = f[f["month_end"] == as_of].set_index("customer_id")["mrr"]
    if base.empty:            # e.g. the window before the first full year of data: nothing to compare
        return pd.DataFrame({"group": pd.Series(dtype=object),
                             **{c: pd.Series(dtype=float) for c in ("base_customers", "base_mrr", "retained", "capped",
                                                                    "logo", "nrr", "grr")}})
    b = base.assign(mrr_now=base["customer_id"].map(now).fillna(0.0))
    b["capped"] = np.minimum(b["mrr_now"], b["mrr"])
    keys = [group_col] if group_col else []
    if not keys:
        b = b.assign(_all="All customers")
        keys = ["_all"]
    g = b.groupby(keys).agg(base_customers=("customer_id", "size"), base_mrr=("mrr", "sum"),
                            retained=("mrr_now", "sum"), capped=("capped", "sum"),
                            logo=("mrr_now", lambda s: (s > 0).mean())).reset_index()
    g["nrr"] = g["retained"] / g["base_mrr"]
    g["grr"] = g["capped"] / g["base_mrr"]
    g = g.rename(columns={keys[0]: "group"})
    return g[g["base_customers"] >= min_base].sort_values("nrr")


def fig_dumbbell(df: pd.DataFrame, title: str, subtitle: str, now_label: str) -> go.Figure:
    """Before -> after per group. df columns: group, nrr, nrr_year_ago, base_customers
    (optional change_lo / change_hi: the 95% interval of the change, shown on hover)."""
    s = df.dropna(subset=["nrr"]).astype({"nrr": float, "nrr_year_ago": float}).sort_values("nrr")
    has_ci = {"change_lo", "change_hi"} <= set(s.columns)
    fig = go.Figure()
    for r in s.dropna(subset=["nrr_year_ago"]).itertuples():
        fig.add_trace(go.Scatter(x=[r.nrr_year_ago, r.nrr], y=[r.group] * 2, mode="lines",
                                 line=dict(color=theme.AXIS, width=2), showlegend=False, hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=s["nrr_year_ago"], y=s["group"], mode="markers", name="12 months earlier",
                             marker=dict(size=10, color=theme.DEEMPH, line=dict(color=theme.SURFACE, width=2)),
                             hovertemplate="%{y}<br>a year earlier %{x:.1%}<extra></extra>"))
    custom = np.c_[s["base_customers"], s["nrr"] - s["nrr_year_ago"],
                   s["change_lo"] if has_ci else np.full(len(s), np.nan), s["change_hi"] if has_ci else np.full(len(s), np.nan)]
    hover = "%{y}<br>%{x:.1%}<br>base %{customdata[0]:,} customers<br>change %{customdata[1]:+.1%}"
    if has_ci:
        hover += " (95%: %{customdata[2]:+.1%} to %{customdata[3]:+.1%})"
    side = ["middle left" if a < b else "middle right" for a, b in zip(s["nrr"], s["nrr_year_ago"])]
    fig.add_trace(go.Scatter(x=s["nrr"], y=s["group"], mode="markers+text", name=now_label,
                             text=[pct(v, 0) for v in s["nrr"]], textposition=side,
                             textfont=dict(color=theme.INK, size=11), customdata=custom,
                             marker=dict(size=10, color=theme.ACCENT, line=dict(color=theme.SURFACE, width=2)),
                             hovertemplate=hover + "<extra></extra>"))
    fig.add_vline(x=1.0, line=dict(color=theme.AXIS, width=1))
    fig.update_xaxes(tickformat=".0%", showgrid=True, gridcolor=theme.GRID)
    fig.update_yaxes(showgrid=False, automargin=True)
    fig.update_layout(margin=dict(t=90, l=40, r=60))
    return theme.apply(fig, title, subtitle, height=max(320, 90 + 34 * len(s)))
