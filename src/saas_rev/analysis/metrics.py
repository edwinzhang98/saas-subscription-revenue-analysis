"""Core revenue metrics: MRR trend, waterfall, NRR / GRR, the Q3 2026 expansion bridges,
segment and cohort retention.

Answers the VP's first two questions (02_metric_definitions.md §4–§8, scope Q1–Q2):
  * what are NRR / GRR and how have they trended;
  * why did expansion slow in Q3 2026 — one-off vs structural, which drivers.

Every number is computed from the SQL marts (fct_* / rpt_* tables); nothing here redefines a metric.
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
BUCKET_COLORS = {
    "Technology (all)": theme.SERIES[1],
    "Non-Tech seat expansion": theme.SERIES[0],
    "Non-Tech plan upgrades": theme.SERIES[2],
    "Non-Tech other": theme.DEEMPH,
}


def quarter_of(ts: pd.Series) -> pd.Series:
    return ts.dt.year.astype(str) + "-Q" + ts.dt.quarter.astype(str)


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


def identify_one_offs(quarter: str, share_threshold: float = 0.10) -> pd.DataFrame:
    """Single-account expansions above `share_threshold` of the quarter's expansion."""
    b = expansion_buckets()
    qb = b[b["quarter_label"] == quarter]
    by_cust = qb.groupby("customer_id", as_index=False)["expansion_mrr"].sum()
    total = by_cust["expansion_mrr"].sum()
    out = by_cust[by_cust["expansion_mrr"] >= share_threshold * total].copy()
    out["share_of_quarter"] = out["expansion_mrr"] / total
    cust = marts.load("stg_customers")[["customer_id", "company_name", "segment", "industry"]]
    return out.merge(cust, on="customer_id").sort_values("expansion_mrr", ascending=False)


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
    rows = [{"step": f"{q_from} expansion", "kind": "total", "value": exp_f}]
    if exclude_one_offs and one_off:
        rows.append({"step": "Remove one-off rollout", "kind": "delta", "value": -one_off})
    rows.append({"step": f"Base growth (starting MRR {pct(boq_t / boq_f - 1, 0, signed=True)})", "kind": "delta",
                 "value": base_effect})
    for k in BUCKETS:
        r_f = f_ex.loc[f_ex["bucket"] == k, "expansion_mrr"].sum() / boq_f
        r_t = t.loc[t["bucket"] == k, "expansion_mrr"].sum() / boq_t
        rows.append({"step": k, "kind": "delta", "value": (r_t - r_f) * boq_t, "rate_from": r_f, "rate_to": r_t})
    rows.append({"step": f"{q_to} expansion", "kind": "total", "value": t["expansion_mrr"].sum()})
    out = pd.DataFrame(rows)
    out.insert(0, "bridge", f"{q_from} -> {q_to}")
    gap = out.loc[out.kind == "total", "value"].iloc[0] + out.loc[out.kind == "delta", "value"].sum() \
        - out.loc[out.kind == "total", "value"].iloc[-1]
    assert abs(gap) < 0.01, f"bridge does not tie out: {gap}"
    return out


def expansion_rate_by_bucket() -> pd.DataFrame:
    """Quarterly expansion rate (expansion / starting MRR) split by driver bucket."""
    b = expansion_buckets()
    qb = quarterly_base()["mrr_start_of_quarter"]
    g = b.groupby(["quarter_label", "bucket"], as_index=False)["expansion_mrr"].sum()
    g["mrr_start_of_quarter"] = g["quarter_label"].map(qb)
    g["rate"] = g["expansion_mrr"] / g["mrr_start_of_quarter"]
    return g[g["mrr_start_of_quarter"] > 0]


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


def top_movers(quarter: str, n: int = 10) -> pd.DataFrame:
    mv = marts.load("fct_mrr_movements")
    q = mv[mv["quarter_label"] == quarter]
    agg = q.groupby("customer_id", as_index=False).agg(
        expansion=("expansion_mrr", "sum"), contraction=("contraction_mrr", "sum"), churn=("churn_mrr", "sum"),
        mrr_start=("mrr_prev", "first"), mrr_end=("mrr", "last"))
    cust = marts.load("stg_customers")[["customer_id", "company_name", "segment", "industry", "cs_coverage", "csm_owner"]]
    agg = agg.merge(cust, on="customer_id")
    out = []
    for kind in ("expansion", "contraction", "churn"):
        top = agg[agg[kind] > 0].nlargest(n, kind).copy()
        top.insert(0, "mover_type", kind)
        top["amount"] = top[kind]
        out.append(top)
    return pd.concat(out, ignore_index=True)[[
        "mover_type", "customer_id", "company_name", "segment", "industry", "csm_owner", "mrr_start", "mrr_end", "amount"]]


def segment_nrr_compare(month_end: pd.Timestamp = AS_OF, dimensions: tuple = ("segment", "industry",
                        "acquisition_channel", "plan_at_start", "billing_at_start", "region")) -> pd.DataFrame:
    seg = marts.load("rpt_segment_retention_ttm")
    prev = month_end - pd.offsets.MonthEnd(12)
    a = seg[(seg["month_end"] == month_end) & seg["dimension"].isin(dimensions)]
    b = seg[(seg["month_end"] == prev) & seg["dimension"].isin(dimensions)][["dimension", "dimension_value", "nrr", "grr"]]
    out = a.merge(b, on=["dimension", "dimension_value"], how="left", suffixes=("", "_year_ago"))
    out["nrr_change"] = out["nrr"] - out["nrr_year_ago"]
    return out.sort_values(["dimension", "base_mrr"], ascending=[True, False])


def cohort_matrix(metric: str = "nrr", grain: str = "quarter", filters: dict | None = None,
                  max_k: int = 24) -> pd.DataFrame:
    """Cohort x months-since-first-paid matrix computed from fct_customer_mrr_monthly in pandas
    (same rules as sql/27; used interactively with filters). Incomplete cells are NaN."""
    f = marts.load("fct_customer_mrr_monthly")
    if filters:
        for col, vals in filters.items():
            if vals:
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
    t, t_prev, t_ly = AS_OF, pd.Timestamp("2026-06-30"), pd.Timestamp("2025-09-30")
    one_q2 = one[one["quarter"] == Q_PREV].iloc[0]
    q2_ex_rate = (q.loc[Q_PREV, "expansion_mrr"] - one_q2["expansion_mrr"]) / q.loc[Q_PREV, "mrr_start_of_quarter"]
    qoq = br[br["bridge"] == f"{Q_PREV} -> {Q_CUR}"].set_index("step")
    yoy = br[br["bridge"] == f"{Q_LY} -> {Q_CUR}"].set_index("step")
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
        "grr_ttm_2025_09": float(nrr.loc[t_ly, "grr"]),
        "nrr_ttm_ex_one_off": float(nx.loc[t, "nrr_ex"]), "nrr_ttm_2026_06_ex_one_off": float(nx.loc[t_prev, "nrr_ex"]),
        "nrr_base_customers": int(nrr.loc[t, "base_customers"]),
        "nrr_runrate_q3_2026": float(rr.loc[Q_CUR, "nrr_runrate_annualized"]),
        "nrr_runrate_q3_2025": float(rr.loc[Q_LY, "nrr_runrate_annualized"]),
        "nrr_runrate_q2_2026": float(rr.loc[Q_PREV, "nrr_runrate_annualized"]),
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
        "bridge_qoq": {k: float(v) for k, v in qoq["value"].items()},
        "bridge_yoy": {k: float(v) for k, v in yoy["value"].items()},
        "seasonality_q2_q3_change_pts": {int(r.year): float(r.change_pts) for r in seas.itertuples()},
    }


# =============================================================================== figures
def _month_axis(fig: go.Figure, months: pd.Series | None = None) -> None:
    fig.update_xaxes(tickformat="%b %Y", dtick="M3")
    if months is not None and len(months):
        x = theme.month_x(pd.Series(months))
        fig.update_xaxes(range=[x.min() - pd.Timedelta(days=20), x.max() + pd.Timedelta(days=20)])


def fig_mrr_trend(start: str | None = None) -> go.Figure:
    wf = marts.load("rpt_mrr_waterfall_monthly")
    if start:
        wf = wf[wf["month_end"] >= start]
    last = wf.iloc[-1]
    fig = go.Figure(go.Scatter(
        x=theme.month_x(wf["month_end"]), y=wf["mrr_end"], mode="lines", line=dict(color=theme.ACCENT, width=2),
        fill="tozeroy", fillcolor="rgba(42,120,214,0.10)", name="MRR",
        customdata=np.c_[wf["arr_end"], wf["customers_end"]],
        hovertemplate="%{x|%b %Y}<br>MRR %{y:$,.0f}<br>ARR %{customdata[0]:$,.0f}<br>%{customdata[1]:,} paying customers<extra></extra>"))
    fig.add_trace(go.Scatter(x=theme.month_x(pd.Series([last["month_end"]])), y=[last["mrr_end"]], mode="markers+text",
                             marker=dict(color=theme.ACCENT, size=9, line=dict(color=theme.SURFACE, width=2)),
                             text=[f"{money(last['mrr_end'])}"], textposition="top left",
                             textfont=dict(color=theme.INK), hoverinfo="skip", showlegend=False))
    fig.update_yaxes(tickprefix="$", tickformat="~s", rangemode="tozero")
    _month_axis(fig, wf["month_end"])
    fig.update_layout(showlegend=False)
    return theme.apply(fig, f"MRR reached {money(last['mrr_end'])} (ARR {money(last['arr_end'])}) by Sep 2026",
                       "Month-end MRR, USD; annual contracts counted as 1/12 per month", height=360)


def fig_monthly_movements(wf: pd.DataFrame | None = None, start: str | None = "2024-10-31",
                          title: str | None = None) -> go.Figure:
    """Stacked monthly movements: new / expansion / reactivation above zero, contraction / churn below."""
    if wf is None:
        wf = marts.load("rpt_mrr_waterfall_monthly")
    if start:
        wf = wf[wf["month_end"] >= start]
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
    q3 = wf[wf["month_end"] >= "2026-07-31"]
    title = title or (f"Q3 2026: expansion fell while contraction and churn rose — net new MRR "
                      f"{money(q3['new_mrr'].sum() + q3['expansion_mrr'].sum() + q3['reactivation_mrr'].sum() - q3['contraction_mrr'].sum() - q3['churn_mrr'].sum())} for the quarter")
    return theme.apply(fig, title, "Monthly MRR movements, USD (positive above zero, losses below)", height=430)


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
    lo = min(b for b in base if b > 0) * 0.92
    fig.update_yaxes(range=[lo, max(b + h for b, h in zip(base, y)) * 1.04], tickprefix="$", tickformat="~s")
    fig.update_layout(showlegend=False, bargap=0.45)
    net = r["mrr_end_of_quarter"] - r["mrr_start_of_quarter"]
    return theme.apply(fig, f"{quarter}: MRR {money(r['mrr_start_of_quarter'])} → {money(r['mrr_end_of_quarter'])} "
                            f"({money(net)} net, {pct(net / r['mrr_start_of_quarter'], 1, signed=True)})",
                       "Quarter MRR bridge, USD; y-axis starts above zero to show the movements", height=400)


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
            colors.append(theme.ACCENT if r.value >= 0 else "#e34948")
            text.append(("+" if r.value >= 0 else "−") + money(abs(r.value)))
    fig = go.Figure(go.Bar(y=y, x=x, base=base, orientation="h", marker=dict(color=colors),
                           text=[theme.esc(s) for s in text], textposition="outside", textfont=dict(color=theme.INK),
                           cliponaxis=False, hovertemplate="%{y}: %{text}<extra></extra>"))
    fig.update_xaxes(tickprefix="$", tickformat="~s", rangemode="tozero", showgrid=True, gridcolor=theme.GRID)
    fig.update_yaxes(autorange="reversed", showgrid=False, automargin=True)
    fig.update_layout(showlegend=False, bargap=0.35, margin=dict(l=40, r=70))
    start, end = b["value"].iloc[0], b["value"].iloc[-1]
    label = "vs Q2 2026 (sequential)" if which == "qoq" else "vs Q3 2025 (same quarter last year)"
    return theme.apply(fig, f"Expansion bridge {label}: {money(start)} → {money(end)}",
                       "Expansion MRR per quarter, USD. Blue adds, red takes away; driver steps = rate change × this "
                       "quarter's starting MRR", height=420)


def fig_expansion_rate_by_bucket() -> go.Figure:
    g = expansion_rate_by_bucket()
    g = g[g["quarter_label"] >= "2024-Q4"]
    fig = go.Figure()
    for k in BUCKETS:
        s = g[g["bucket"] == k]
        fig.add_trace(go.Bar(x=s["quarter_label"], y=s["rate"], name=k,
                             marker=dict(color=BUCKET_COLORS[k], line=dict(color=theme.SURFACE, width=1)),
                             hovertemplate="%{x}<br>" + k + " %{y:.2%} of starting MRR<extra></extra>"))
    tot = g.groupby("quarter_label")["rate"].sum()
    fig.add_trace(go.Scatter(x=tot.index, y=tot.values, mode="text", text=[pct(v, 1) for v in tot.values],
                             textposition="top center", textfont=dict(color=theme.INK), showlegend=False,
                             hoverinfo="skip"))
    fig.update_layout(barmode="stack", bargap=0.45)
    fig.update_yaxes(tickformat=".0%", rangemode="tozero")
    return theme.apply(fig, "Technology expansion collapsed in 2026; non-Tech plan upgrades also slowed",
                       "Quarterly expansion rate (expansion MRR ÷ MRR at start of quarter), by driver", height=420)


def fig_nrr_trend() -> go.Figure:
    n = marts.load("rpt_nrr_grr_ttm")
    nx = marts.load("metrics__nrr_ex_one_off")
    n = n.merge(nx, on="month_end", how="left")
    fig = go.Figure()
    rep, early = n[n["is_reportable"]], n[~n["is_reportable"]]
    # early points (base < 1,000 customers) in gray, reportable points in color
    for col, name, color in (("nrr", "NRR (TTM)", theme.ACCENT), ("grr", "GRR (TTM)", theme.SERIES[1])):
        fig.add_trace(go.Scatter(x=theme.month_x(early["month_end"]), y=early[col], mode="lines", line=dict(color=theme.DEEMPH, width=2),
                                 name=f"{name}, base < 1,000", showlegend=(col == "nrr"), legendgroup="early",
                                 hovertemplate="%{x|%b %Y}<br>" + name + " %{y:.1%} (small base)<extra></extra>"))
        joined = pd.concat([early.tail(1), rep])
        fig.add_trace(go.Scatter(x=theme.month_x(joined["month_end"]), y=joined[col], mode="lines", line=dict(color=color, width=2),
                                 name=name, customdata=joined["base_customers"],
                                 hovertemplate="%{x|%b %Y}<br>" + name + " %{y:.1%}<br>base %{customdata:,} customers<extra></extra>"))
        last = rep.iloc[-1]
        fig.add_trace(go.Scatter(x=theme.month_x(pd.Series([last["month_end"]])), y=[last[col]], mode="markers+text", text=[pct(last[col], 1)],
                                 textposition="middle right", textfont=dict(color=theme.INK),
                                 marker=dict(size=9, color=color, line=dict(color=theme.SURFACE, width=2)),
                                 showlegend=False, hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=theme.month_x(rep["month_end"]), y=rep["nrr_ex"], mode="lines", line=dict(color=theme.ACCENT, width=1.5, dash="dot"),
                             name="NRR excl. one-off rollout",
                             hovertemplate="%{x|%b %Y}<br>NRR excl. one-off %{y:.1%}<extra></extra>"))
    fig.add_hline(y=1.0, line=dict(color=theme.AXIS, width=1))
    fig.update_yaxes(tickformat=".0%")
    fig.update_xaxes(tickformat="%b %Y", dtick="M3", range=[theme.month_x(n["month_end"]).min() - pd.Timedelta(days=20),
                                                           theme.month_x(n["month_end"]).max() + pd.Timedelta(days=80)])
    last = rep.iloc[-1]
    return theme.apply(fig, f"12-month NRR {pct(last['nrr'])} at Sep 2026, GRR {pct(last['grr'])}",
                       "Same-customer, point-to-point, base = customers paying 12 months earlier; gray = base under 1,000 customers",
                       height=420)


def fig_nrr_runrate() -> go.Figure:
    rr = marts.load("rpt_nrr_quarterly_runrate")
    rr = rr[rr["quarter_label"] >= "2024-Q4"]
    colors = [theme.ACCENT if q == Q_CUR else theme.DEEMPH for q in rr["quarter_label"]]
    fig = go.Figure(go.Bar(x=rr["quarter_label"], y=rr["nrr_runrate_annualized"] - 1, base=1, marker=dict(color=colors),
                           text=[pct(v, 1) for v in rr["nrr_runrate_annualized"]], textposition="outside",
                           textfont=dict(color=theme.INK), cliponaxis=False,
                           hovertemplate="%{x}<br>run-rate NRR %{text}<extra></extra>"))
    fig.add_hline(y=1.0, line=dict(color=theme.AXIS, width=1))
    fig.update_yaxes(tickformat=".0%")
    fig.update_layout(showlegend=False, bargap=0.45)
    v = rr.set_index("quarter_label")["nrr_runrate_annualized"]
    return theme.apply(fig, f"Q3 2026 run-rate NRR {pct(v[Q_CUR])} vs {pct(v[Q_LY])} a year earlier",
                       "Quarterly same-customer retention, annualised (^4); a leading view — noisier than TTM",
                       height=360)


def fig_segment_dumbbell(dimension: str = "industry", month_end: pd.Timestamp = AS_OF) -> go.Figure:
    s = segment_nrr_compare(month_end)
    s = s[(s["dimension"] == dimension) & (s["base_customers"] >= 30)].sort_values("nrr")
    fig = go.Figure()
    for r in s.itertuples():
        fig.add_trace(go.Scatter(x=[r.nrr_year_ago, r.nrr], y=[r.dimension_value] * 2, mode="lines",
                                 line=dict(color=theme.AXIS, width=2), showlegend=False, hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=s["nrr_year_ago"], y=s["dimension_value"], mode="markers", name="12 months earlier",
                             marker=dict(size=10, color=theme.DEEMPH, line=dict(color=theme.SURFACE, width=2)),
                             hovertemplate="%{y}<br>NRR a year earlier %{x:.1%}<extra></extra>"))
    fig.add_trace(go.Scatter(x=s["nrr"], y=s["dimension_value"], mode="markers+text", name=f"{month_end:%b %Y}",
                             text=[pct(v, 0) for v in s["nrr"]], textposition="middle right",
                             textfont=dict(color=theme.INK, size=11),
                             marker=dict(size=10, color=theme.ACCENT, line=dict(color=theme.SURFACE, width=2)),
                             customdata=s["base_customers"],
                             hovertemplate="%{y}<br>NRR %{x:.1%}<br>base %{customdata:,} customers<extra></extra>"))
    fig.add_vline(x=1.0, line=dict(color=theme.AXIS, width=1))
    fig.update_xaxes(tickformat=".0%", showgrid=True, gridcolor=theme.GRID)
    fig.update_yaxes(showgrid=False, automargin=True)
    fig.update_layout(margin=dict(t=90, l=40, r=60))
    worst = s.iloc[0]
    label = dimension.replace("_", " ")
    return theme.apply(fig, f"12-month NRR by {label}: lowest is {worst.dimension_value} at {pct(worst.nrr, 0)}",
                       "Gray = 12 months earlier, blue = now; groups with at least 30 base customers",
                       height=max(320, 60 + 34 * len(s)))


def fig_cohort_heatmap(metric: str = "nrr", grain: str = "quarter", filters: dict | None = None,
                       max_k: int = 24) -> go.Figure:
    m = cohort_matrix(metric, grain, filters, max_k)
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
    names = {"nrr": "net revenue retention", "grr": "gross revenue retention", "logo": "logo retention"}
    return theme.apply(fig, f"Cohort {names[metric]} by months since first payment",
                       "Each row = customers whose first paid month falls in that period; blank = not yet observed for the whole cohort",
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
                  min_base: int = 30) -> pd.DataFrame:
    """TTM NRR / GRR / logo retention at any month-end, optionally by a grouping column
    (same rules as sql/25-26: base = paying at as_of - 12 months; groups use the base month's attributes)."""
    f = _apply_filters(marts.load("fct_customer_mrr_monthly"), filters)
    t0 = as_of - pd.offsets.MonthEnd(12)
    base = f[(f["month_end"] == t0) & (f["mrr"] > 0)]
    now = f[f["month_end"] == as_of].set_index("customer_id")["mrr"]
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
    """Before -> after per group. df columns: group, nrr, nrr_year_ago, base_customers."""
    s = df.dropna(subset=["nrr_year_ago"]).sort_values("nrr")
    fig = go.Figure()
    for r in s.itertuples():
        fig.add_trace(go.Scatter(x=[r.nrr_year_ago, r.nrr], y=[r.group] * 2, mode="lines",
                                 line=dict(color=theme.AXIS, width=2), showlegend=False, hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=s["nrr_year_ago"], y=s["group"], mode="markers", name="12 months earlier",
                             marker=dict(size=10, color=theme.DEEMPH, line=dict(color=theme.SURFACE, width=2)),
                             hovertemplate="%{y}<br>a year earlier %{x:.1%}<extra></extra>"))
    fig.add_trace(go.Scatter(x=s["nrr"], y=s["group"], mode="markers+text", name=now_label,
                             text=[pct(v, 0) for v in s["nrr"]], textposition="middle right",
                             textfont=dict(color=theme.INK, size=11), customdata=s["base_customers"],
                             marker=dict(size=10, color=theme.ACCENT, line=dict(color=theme.SURFACE, width=2)),
                             hovertemplate="%{y}<br>%{x:.1%}<br>base %{customdata:,} customers<extra></extra>"))
    fig.add_vline(x=1.0, line=dict(color=theme.AXIS, width=1))
    fig.update_xaxes(tickformat=".0%", showgrid=True, gridcolor=theme.GRID)
    fig.update_yaxes(showgrid=False, automargin=True)
    fig.update_layout(margin=dict(t=90, l=40, r=60))
    return theme.apply(fig, title, subtitle, height=max(320, 90 + 34 * len(s)))
