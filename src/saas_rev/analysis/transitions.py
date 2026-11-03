"""Plan transitions: where customers go after they sign up, who upgrades from Basic, why Basic
upgrades fell in 2026, what the April 2026 packaging change bought in Basic retention, who
downgrades (and what happens after a CS save), and how long upgrades take.

Scope: 01_request_and_scope.md Q2 (d)(e) and the Product / Pricing and Growth Marketing decisions
(§3). Definitions: 02_metric_definitions.md §6 (voluntary vs forced upgrades, monthly upgrade rate)
and §11 (plan at month-end, transition matrix, Sankey). Every number is computed from the marts;
the generator's parameters are never an input.

Conventions used throughout
  * "pre" window = outcome months Apr 2025 - Mar 2026, "post" = May - Sep 2026. April 2026 is the
    transition month (the packaging change is dated 2026-04-15) and sits in neither window.
  * Intervals are Wilson 95% for a rate and Newcombe (hybrid score) 95% for a difference of rates.
  * The decomposition and the before / after comparisons are observational. The clean test of the
    packaging change is a holdout (re-gate advanced automations for a random share of new Basic
    signups); every causal-sounding sentence here is a hypothesis for that test.
"""
from __future__ import annotations

from functools import lru_cache

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from .. import marts, theme
from ..theme import PLAN_COLORS, PLAN_LABELS, pct, pts

TOPIC = "transitions"
AS_OF = pd.Timestamp("2026-09-30")
PACKAGING_DATE = pd.Timestamp("2026-04-15")
PAID_SOCIAL_START = pd.Timestamp("2025-09-01")
PRE_WINDOW = (pd.Timestamp("2025-04-30"), pd.Timestamp("2026-03-31"))      # outcome month-ends
POST_WINDOW = (pd.Timestamp("2026-05-31"), pd.Timestamp("2026-09-30"))
CHURN_WINDOW_LY = (pd.Timestamp("2025-05-31"), pd.Timestamp("2025-09-30"))
CHURN_WINDOW_CUR = (pd.Timestamp("2026-05-31"), pd.Timestamp("2026-09-30"))
TIER = {"basic": 1, "premium": 2, "enterprise": 3}
STATES = ["basic", "premium", "enterprise", "churned"]
PLANS = ["basic", "premium", "enterprise"]
PAID_SOCIAL = "Paid Social"
GROUP_PS, GROUP_OTHER = "Paid Social", "Other channels"
GROUP_COLORS = {GROUP_PS: theme.PAID_SOCIAL, GROUP_OTHER: theme.OTHER}
TENURE_BINS = [-1, 2, 6, 12, 24, 10_000]
TENURE_LABELS = ["0-2 months", "3-6 months", "7-12 months", "13-24 months", "25+ months"]
SOURCE_LABELS = {"self_serve": "Self-serve", "renewal": "At renewal", "customer_success": "CS save",
                 "sales": "Sales-assisted", "system": "Seat cap (system)"}
# Who initiated a downgrade. The Plan Transitions page also draws the plan colours (Basic blue, Premium violet,
# Enterprise green, Churned gray), so the initiators avoid all four: the two customer-initiated kinds take the
# contraction amber and red, and a CS save wears the shared saved-by-CS gray (theme.SAVED) with a hatch, so it
# never reads as the solid Churned gray of the Sankey above it.
SOURCE_COLORS = {"self_serve": theme.SERIES[3], "renewal": theme.SERIES[7], "customer_success": theme.SAVED}
SOURCE_PATTERNS = {"customer_success": dict(shape="/", fgcolor=theme.MUTED, size=8, solidity=0.22)}
SOURCE_TEXT = {"self_serve": theme.INK, "renewal": "#ffffff", "customer_success": theme.INK}   # label ink inside each fill
DIMENSION_LABELS = {"segment": "Segment", "acquisition_channel": "Acquisition channel", "industry": "Industry",
                    "tenure_band": "Tenure at start of month", "billing_period": "Billing period",
                    "channel_group": "Channel group"}
FILTER_COLUMNS = ("segment", "acquisition_channel", "industry", "region")
Z = 1.959964


# =============================================================================== small helpers
def wilson(k, n, z: float = Z):
    """Wilson 95% interval for k successes in n trials (vectorised; empty groups give NaN)."""
    k, n = np.asarray(k, dtype=float), np.asarray(n, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        p = k / n
        denom = 1 + z ** 2 / n
        centre = (p + z ** 2 / (2 * n)) / denom
        half = z * np.sqrt(p * (1 - p) / n + z ** 2 / (4 * n ** 2)) / denom
    return np.clip(centre - half, 0, 1), np.clip(centre + half, 0, 1)      # clip float noise (e.g. -1e-18 at k = 0)


def newcombe_diff(k1, n1, k2, n2) -> tuple[float, float, float]:
    """(p2 - p1) with a Newcombe hybrid-score 95% interval: the standard interval for a
    difference of two independent proportions when either count is small."""
    p1, p2 = float(k1) / float(n1), float(k2) / float(n2)
    l1, u1 = (float(v) for v in wilson(k1, n1))
    l2, u2 = (float(v) for v in wilson(k2, n2))
    d = p2 - p1
    lo = d - float(np.sqrt((p2 - l2) ** 2 + (u1 - p1) ** 2))
    hi = d + float(np.sqrt((u2 - p2) ** 2 + (p1 - l1) ** 2))
    return d, lo, hi


def _apply_filters(df: pd.DataFrame, filters: dict | None) -> pd.DataFrame:
    if not filters:
        return df
    for col, vals in filters.items():
        if vals:
            df = df[df[col].isin(vals)]
    return df


def _in_window(df: pd.DataFrame, window: tuple, col: str = "month_end") -> pd.DataFrame:
    lo, hi = pd.Timestamp(window[0]), pd.Timestamp(window[1])
    return df[(df[col] >= lo) & (df[col] <= hi)]


def window_label(window: tuple) -> str:
    lo, hi = pd.Timestamp(window[0]), pd.Timestamp(window[1])
    return f"{lo:%b %Y} - {hi:%b %Y}" if lo != hi else f"{lo:%b %Y}"


def _rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"


def _label(state: str) -> str:
    return PLAN_LABELS.get(state, state)


# =============================================================================== 1. flows
@lru_cache(maxsize=1)
def _panel() -> pd.DataFrame:
    f = marts.load("fct_customer_mrr_monthly")
    cols = ["customer_id", "month_end", "mrr", "plan_id", "tier_rank", "cohort_month_end", "months_since_first_paid",
            "segment", "industry", "acquisition_channel", "region"]
    p = f[cols].copy()
    p["state"] = np.where(p["mrr"] > 0, p["plan_id"], "churned")
    return p


def signup_states(filters: dict | None = None, cohort_window: tuple | None = None) -> pd.DataFrame:
    """One row per customer: plan at the first paid month-end and state 12 / 24 months later
    (NaN where the horizon is not yet observed). Filters use static attributes; the cohort window
    bounds the first paid month-end."""
    p = _apply_filters(_panel(), filters)
    if cohort_window:
        p = _in_window(p, cohort_window, "cohort_month_end")
    k0 = p[p["months_since_first_paid"] == 0][["customer_id", "state", "cohort_month_end", "segment", "industry",
                                                 "acquisition_channel", "region"]].rename(columns={"state": "plan_0"})
    out = k0
    for h in (12, 24):
        s = p[p["months_since_first_paid"] == h][["customer_id", "state"]].rename(columns={"state": f"state_{h}"})
        out = out.merge(s, on="customer_id", how="left")
        out.loc[out["cohort_month_end"] + pd.offsets.MonthEnd(h) > AS_OF, f"state_{h}"] = np.nan
    return out


def sankey_links(horizon: int = 12, filters: dict | None = None, cohort_window: tuple | None = None) -> pd.DataFrame:
    """Flows for the Sankey: start -> 12 months (-> 24 months when horizon = 24), counted on the
    customers whose full horizon is observed."""
    s = signup_states(filters, cohort_window)
    s = s.dropna(subset=[f"state_{horizon}"])
    rows = []
    stages = [("start", "plan_0"), ("12m", "state_12")] + ([("24m", "state_24")] if horizon == 24 else [])
    for (st_a, col_a), (st_b, col_b) in zip(stages[:-1], stages[1:]):
        g = s.groupby([col_a, col_b], observed=True).size().reset_index(name="customers")
        g.columns = ["state_from", "state_to", "customers"]
        g.insert(0, "stage_to", st_b)
        g.insert(0, "stage_from", st_a)
        tot = g.groupby("state_from")["customers"].transform("sum")
        g["share_of_from"] = g["customers"] / tot
        rows.append(g)
    out = pd.concat(rows, ignore_index=True)
    out["customers_in_view"] = len(s)
    return out


def transition_matrix(filters: dict | None = None, horizons: tuple = (3, 6, 12, 24)) -> pd.DataFrame:
    """Share of customers paying at month-end s that is in each state at s + h, pooled over every
    s with s + h <= as-of (02 §11; same rules as sql/30, recomputed here so filters work)."""
    p = _apply_filters(_panel(), filters)
    a = p[p["mrr"] > 0][["customer_id", "month_end", "plan_id", "mrr"]]
    b = p[["customer_id", "month_end", "state", "mrr"]].rename(columns={"month_end": "target", "state": "to_state",
                                                                         "mrr": "to_mrr"})
    rows = []
    for h in horizons:
        x = a.assign(target=a["month_end"] + pd.offsets.MonthEnd(h))
        x = x[x["target"] <= AS_OF].merge(b, on=["customer_id", "target"], how="inner")
        g = x.groupby(["plan_id", "to_state"]).agg(customer_months=("customer_id", "size"), from_mrr=("mrr", "sum"),
                                                   to_mrr=("to_mrr", "sum")).reset_index()
        g["share_of_from_plan"] = g["customer_months"] / g.groupby("plan_id")["customer_months"].transform("sum")
        g.insert(0, "horizon_months", h)
        rows.append(g.rename(columns={"plan_id": "from_plan"}))
    return pd.concat(rows, ignore_index=True)


def transition_shares(h: int = 12, from_plan: str = "basic", filters: dict | None = None) -> dict:
    m = transition_matrix(filters, horizons=(h,))
    m = m[m["from_plan"] == from_plan].set_index("to_state")["share_of_from_plan"]
    return {s: float(m.get(s, 0.0)) for s in STATES}


# =============================================================================== 2. who upgrades
@lru_cache(maxsize=1)
def _basic_base() -> pd.DataFrame:
    """Customers on Basic at the end of m-1 with the outcome in m (fct_basic_upgrade_monthly, 02 §6),
    plus the grouping columns used everywhere here."""
    b = marts.load("fct_basic_upgrade_monthly").copy()
    b["channel_group"] = np.where(b["acquisition_channel"] == PAID_SOCIAL, GROUP_PS, GROUP_OTHER)
    b["industry_group"] = np.where(b["industry"] == "Technology", "Technology", "Non-Technology")
    b["channel_tech"] = b["channel_group"] + " / " + b["industry_group"]
    b["tenure_band"] = pd.cut(b["months_since_first_paid"], TENURE_BINS, labels=TENURE_LABELS).astype(str)
    b["quarter_label"] = b["month_end"].dt.year.astype(str) + "-Q" + b["month_end"].dt.quarter.astype(str)
    return b


def basic_base(window: tuple | None = None, filters: dict | None = None) -> pd.DataFrame:
    b = _apply_filters(_basic_base(), filters)
    return _in_window(b, window) if window else b


def _rate_frame(g: pd.DataFrame) -> pd.DataFrame:
    """Attach rate + Wilson interval to a grouped frame with columns n, upgrades."""
    g = g.copy()
    g["rate"] = g["upgrades"] / g["n"]
    g["lo"], g["hi"] = wilson(g["upgrades"], g["n"])
    return g


def upgrade_rate_by(dim: str, window: tuple, filters: dict | None = None, min_n: int = 100) -> pd.DataFrame:
    """Voluntary Basic -> higher upgrade rate by one dimension, pooled over the window's outcome
    months (account-month grain). Forced seat-cap upgrades are counted separately."""
    b = basic_base(window, filters)
    g = b.groupby(dim, observed=True).agg(n=("customer_id", "size"), upgrades=("voluntary_upgrade", "sum"),
                                          forced=("forced_upgrade_only", "sum")).reset_index()
    g = g.rename(columns={dim: "group"})
    g.insert(0, "dimension", dim)
    return _rate_frame(g[g["n"] >= min_n])


def upgrade_rate_pre_post(dims: tuple = ("segment", "acquisition_channel", "industry", "tenure_band", "billing_period"),
                          pre: tuple = PRE_WINDOW, post: tuple = POST_WINDOW, min_n: int = 100) -> pd.DataFrame:
    out = []
    for d in dims:
        a = upgrade_rate_by(d, pre, min_n=min_n)
        c = upgrade_rate_by(d, post, min_n=min_n)
        m = a.merge(c, on=["dimension", "group"], how="inner", suffixes=("_pre", "_post"))
        m["change_pts"] = m["rate_post"] - m["rate_pre"]
        m["rel_change"] = m["rate_post"] / m["rate_pre"] - 1
        d_lo, d_hi = [], []
        for r in m.itertuples():
            _, lo, hi = newcombe_diff(r.upgrades_pre, r.n_pre, r.upgrades_post, r.n_post)
            d_lo.append(lo)
            d_hi.append(hi)
        m["change_lo"], m["change_hi"] = d_lo, d_hi
        out.append(m)
    return pd.concat(out, ignore_index=True)


def upgrade_rate_quarterly(group_col: str = "channel_group", filters: dict | None = None) -> pd.DataFrame:
    b = basic_base(None, filters)
    g = b.groupby(["quarter_label", group_col], observed=True).agg(
        n=("customer_id", "size"), upgrades=("voluntary_upgrade", "sum"), forced=("forced_upgrade_only", "sum"),
        basic_customers_avg=("customer_id", lambda s: len(s) / 3)).reset_index()
    return _rate_frame(g.rename(columns={group_col: "group"}))


def upgrade_rate_monthly(group_col: str | None = "channel_group", filters: dict | None = None) -> pd.DataFrame:
    b = basic_base(None, filters)
    keys = ["month_end"] + ([group_col] if group_col else [])
    g = b.groupby(keys, observed=True).agg(n=("customer_id", "size"), upgrades=("voluntary_upgrade", "sum"),
                                           forced=("forced_upgrade_only", "sum")).reset_index()
    if group_col:
        g = g.rename(columns={group_col: "group"})
    else:
        g.insert(1, "group", "All Basic customers")
    return _rate_frame(g)


# =============================================================================== 3. why upgrades fell
def mix_rate_decomposition(pre: tuple = PRE_WINDOW, post: tuple = POST_WINDOW, by: str = "acquisition_channel",
                           filters: dict | None = None) -> tuple[dict, pd.DataFrame]:
    """Shift-share decomposition of the change in the pooled voluntary upgrade rate between two
    windows into a MIX effect (the Basic base's composition by `by`) and a RATE effect (within-cell
    rates). Both orderings are reported because they overlap; the Shapley value (midpoint) is the
    headline split. Cells with no pre or post rows contribute through the other term only."""
    a, c = basic_base(pre, filters), basic_base(post, filters)
    ga = a.groupby(by, observed=True).agg(n_pre=("customer_id", "size"), up_pre=("voluntary_upgrade", "sum"))
    gc = c.groupby(by, observed=True).agg(n_post=("customer_id", "size"), up_post=("voluntary_upgrade", "sum"))
    t = ga.join(gc, how="outer").fillna(0)
    t["w_pre"], t["w_post"] = t["n_pre"] / t["n_pre"].sum(), t["n_post"] / t["n_post"].sum()
    with np.errstate(divide="ignore", invalid="ignore"):
        t["r_pre"] = np.where(t["n_pre"] > 0, t["up_pre"] / t["n_pre"], np.nan)
        t["r_post"] = np.where(t["n_post"] > 0, t["up_post"] / t["n_post"], np.nan)
    r_pre_f, r_post_f = t["r_pre"].fillna(t["r_post"]), t["r_post"].fillna(t["r_pre"])
    dw, dr = t["w_post"] - t["w_pre"], r_post_f - r_pre_f
    t["mix_at_pre_rates"] = dw * r_pre_f                 # ordering A: mix first
    t["rate_at_post_mix"] = t["w_post"] * dr
    t["mix_at_post_rates"] = dw * r_post_f               # ordering B: rate first
    t["rate_at_pre_mix"] = t["w_pre"] * dr
    t["mix_shapley"] = dw * (r_pre_f + r_post_f) / 2
    t["rate_shapley"] = (t["w_pre"] + t["w_post"]) / 2 * dr
    t = t.reset_index().rename(columns={by: "cell"})
    R0, R1 = a["voluntary_upgrade"].mean(), c["voluntary_upgrade"].mean()
    summary = {
        "by": by, "pre_window": window_label(pre), "post_window": window_label(post),
        "n_pre": int(len(a)), "n_post": int(len(c)), "upgrades_pre": int(a["voluntary_upgrade"].sum()),
        "upgrades_post": int(c["voluntary_upgrade"].sum()),
        "rate_pre": float(R0), "rate_post": float(R1), "change_pts": float(R1 - R0),
        "rel_change": float(R1 / R0 - 1) if R0 else np.nan,
        "mix_at_pre_rates": float(t["mix_at_pre_rates"].sum()), "rate_at_post_mix": float(t["rate_at_post_mix"].sum()),
        "mix_at_post_rates": float(t["mix_at_post_rates"].sum()), "rate_at_pre_mix": float(t["rate_at_pre_mix"].sum()),
        "mix_shapley": float(t["mix_shapley"].sum()), "rate_shapley": float(t["rate_shapley"].sum()),
        "forced_pre": int(a["forced_upgrade_only"].sum()), "forced_post": int(c["forced_upgrade_only"].sum()),
    }
    summary["mix_share_of_change"] = summary["mix_shapley"] / summary["change_pts"] if summary["change_pts"] else np.nan
    # the same change within the non-Paid-Social base, with an interval (the 'controlled' rate change)
    for key, mask_a, mask_c in (("non_ps", a["channel_group"] == GROUP_OTHER, c["channel_group"] == GROUP_OTHER),
                                ("non_ps_non_tech", (a["channel_group"] == GROUP_OTHER) & (a["industry_group"] != "Technology"),
                                 (c["channel_group"] == GROUP_OTHER) & (c["industry_group"] != "Technology")),
                                ("ps", a["channel_group"] == GROUP_PS, c["channel_group"] == GROUP_PS)):
        ka, na = int(a.loc[mask_a, "voluntary_upgrade"].sum()), int(mask_a.sum())
        kc, nc = int(c.loc[mask_c, "voluntary_upgrade"].sum()), int(mask_c.sum())
        if na and nc:
            d, lo, hi = newcombe_diff(ka, na, kc, nc)
            summary[key] = {"n_pre": na, "n_post": nc, "rate_pre": ka / na, "rate_post": kc / nc, "change_pts": d,
                            "change_lo": lo, "change_hi": hi, "rel_change": (kc / nc) / (ka / na) - 1 if ka else np.nan}
    summary["paid_social_share_pre"] = float((a["channel_group"] == GROUP_PS).mean())
    summary["paid_social_share_post"] = float((c["channel_group"] == GROUP_PS).mean())
    return summary, t


def _monthly_group(group: str = GROUP_OTHER) -> pd.DataFrame:
    """Monthly upgrade rate of one channel group with a month index t (0 = first month in the data), the
    x of the linear trend fitted in step_vs_drift and drawn by fig_upgrade_rate_monthly."""
    m = upgrade_rate_monthly("channel_group")
    m = m[m["group"] == group].sort_values("month_end").reset_index(drop=True)
    m["t"] = np.arange(len(m))
    return m


def step_vs_drift(pre: tuple = PRE_WINDOW, post: tuple = POST_WINDOW, group: str = GROUP_OTHER) -> dict:
    """Was the 2026 fall a step at the packaging date or a drift that was already under way?
    (1) a weighted linear trend fitted to the monthly rate over the pre window and extrapolated to
    the post months: the gap between actual and extrapolated is the step net of drift; (2) the three
    outcome months before April 2026 vs the three after it, with an interval."""
    m = _monthly_group(group)
    a, c = _in_window(m, pre), _in_window(m, post)
    slope, intercept = np.polyfit(a["t"], a["rate"], 1, w=np.sqrt(a["n"]))
    pred = intercept + slope * c["t"]
    pred_post = float(np.average(pred, weights=c["n"]))
    actual_post = float(c["upgrades"].sum() / c["n"].sum())
    pre_mean = float(a["upgrades"].sum() / a["n"].sum())
    pre_end = float(intercept + slope * a["t"].iloc[-1])
    b3 = m[(m["month_end"] >= "2026-01-31") & (m["month_end"] <= "2026-03-31")]
    a3 = m[(m["month_end"] >= "2026-05-31") & (m["month_end"] <= "2026-07-31")]
    d, lo, hi = newcombe_diff(b3["upgrades"].sum(), b3["n"].sum(), a3["upgrades"].sum(), a3["n"].sum())
    low = m.loc[m["rate"].idxmin()]
    h1 = m[(m["month_end"] >= "2025-04-30") & (m["month_end"] <= "2025-09-30")]
    h2 = m[(m["month_end"] >= "2025-10-31") & (m["month_end"] <= "2026-03-31")]
    return {"group": group, "slope_pts_per_month": float(slope), "trend_intercept": float(intercept), "pre_mean": pre_mean,
            "step_from_pre_april_level_pts": actual_post - pre_end,     # if the rate had held at its (trend) level at the end of March
            "rate_apr_sep_2025": float(h1["upgrades"].sum() / h1["n"].sum()),
            "rate_oct_2025_mar_2026": float(h2["upgrades"].sum() / h2["n"].sum()),
            "pre_trend_level_at_end_of_pre": pre_end, "extrapolated_post": pred_post, "actual_post": actual_post,
            "raw_change_pts": actual_post - pre_mean, "raw_change_rel": actual_post / pre_mean - 1,
            "step_net_of_trend_pts": actual_post - pred_post, "step_net_of_trend_rel": actual_post / pred_post - 1,
            "rate_jan_mar_2026": float(b3["upgrades"].sum() / b3["n"].sum()),
            "rate_may_jul_2026": float(a3["upgrades"].sum() / a3["n"].sum()),
            "step_3m_pts": d, "step_3m_lo": lo, "step_3m_hi": hi,
            "lowest_month": str(pd.Timestamp(low["month_end"]).date()), "lowest_month_rate": float(low["rate"])}


def drift_check(early: tuple = (pd.Timestamp("2025-04-30"), pd.Timestamp("2025-09-30")),
                late: tuple = (pd.Timestamp("2025-10-31"), pd.Timestamp("2026-03-31")), dim: str = "segment",
                min_n: int = 500) -> pd.DataFrame:
    """The drift inside the pre window, by one dimension: the non-Paid-Social upgrade rate in the first half of
    the pre window vs the second half (both before the packaging change). A fall within every group means the
    drift is not a shift in the base's composition along that dimension."""
    b = basic_base((early[0], late[1]))
    b = b[b["channel_group"] == GROUP_OTHER]
    out = []
    for lab, w in (("early", early), ("late", late)):
        x = _in_window(b, w)
        g = x.groupby(dim, observed=True).agg(n=("customer_id", "size"), upgrades=("voluntary_upgrade", "sum")).reset_index()
        out.append(g.rename(columns={dim: "group"}).assign(half=lab))
    t = pd.concat(out, ignore_index=True)
    t = t.pivot(index="group", columns="half", values=["n", "upgrades"])
    t.columns = [f"{a}_{b}" for a, b in t.columns]
    t = t.reset_index()
    t = t[(t["n_early"] >= min_n) & (t["n_late"] >= min_n)].copy()
    t["rate_early"], t["rate_late"] = t["upgrades_early"] / t["n_early"], t["upgrades_late"] / t["n_late"]
    t.insert(0, "dimension", dim)
    t["early_window"], t["late_window"] = window_label(early), window_label(late)
    return t


# =============================================================================== 4. packaging upside
@lru_cache(maxsize=1)
def _basic_churn_base() -> pd.DataFrame:
    """Customers on Basic at the end of m-1 (paying) with a churn flag for m (02 §5 logo churn,
    restricted to the Basic base)."""
    mv = marts.load("fct_mrr_movements")
    b = mv[(mv["mrr_prev"] > 0) & (mv["plan_id_prev"] == "basic")][
        ["customer_id", "month_end", "movement_type", "mrr_prev", "segment", "industry", "acquisition_channel",
         "region", "months_since_first_paid"]].copy()
    b["churned"] = b["movement_type"] == "churn"
    b["channel_group"] = np.where(b["acquisition_channel"] == PAID_SOCIAL, GROUP_PS, GROUP_OTHER)
    b["industry_group"] = np.where(b["industry"] == "Technology", "Technology", "Non-Technology")
    return b


def basic_churn_compare(window_a: tuple = CHURN_WINDOW_LY, window_b: tuple = CHURN_WINDOW_CUR,
                        exclude_paid_social: bool = True, exclude_technology: bool = False) -> dict:
    b = _basic_churn_base()
    if exclude_paid_social:
        b = b[b["channel_group"] == GROUP_OTHER]
    if exclude_technology:
        b = b[b["industry_group"] != "Technology"]
    x, y = _in_window(b, window_a), _in_window(b, window_b)
    d, lo, hi = newcombe_diff(x["churned"].sum(), len(x), y["churned"].sum(), len(y))
    return {"window_a": window_label(window_a), "window_b": window_label(window_b),
            "exclude_paid_social": exclude_paid_social, "exclude_technology": exclude_technology,
            "n_a": int(len(x)), "churn_a": float(x["churned"].mean()), "n_b": int(len(y)), "churn_b": float(y["churned"].mean()),
            "change_pts": d, "change_lo": lo, "change_hi": hi, "rel_change": float(y["churned"].mean() / x["churned"].mean() - 1),
            "avg_basic_mrr_b": float(y["mrr_prev"].mean())}


def basic_churn_monthly(exclude_paid_social: bool = True) -> pd.DataFrame:
    b = _basic_churn_base()
    if exclude_paid_social:
        b = b[b["channel_group"] == GROUP_OTHER]
    g = b.groupby("month_end").agg(n=("customer_id", "size"), churned=("churned", "sum")).reset_index()
    g["rate"] = g["churned"] / g["n"]
    g["lo"], g["hi"] = wilson(g["churned"], g["n"])
    return g


def upgrade_mrr_delta(window: tuple = PRE_WINDOW) -> dict:
    """Average MRR added by a voluntary Basic -> higher upgrade (the size of a foregone upgrade)."""
    cc = marts.load("fct_change_components")
    u = cc[(cc["from_plan_id"] == "basic") & (cc["to_plan_id"].map(TIER) > 1) & (cc["change_source"] != "system")]
    u = _in_window(u, window)
    return {"n": int(len(u)), "mean_delta_mrr": float(u["delta_mrr"].mean()), "median_delta_mrr": float(u["delta_mrr"].median())}


def net_mrr_effect(pre: tuple = PRE_WINDOW, post: tuple = POST_WINDOW) -> dict:
    """Run-rate MRR per month: foregone upgrade MRR (rate change x base x MRR per upgrade) against
    retained Basic MRR (churn change x base x MRR per Basic account), both on the non-Paid-Social
    Basic base so the Paid Social mix shift cannot enter. Ranges come from the 95% intervals of the
    two rate changes; the MRR-per-upgrade and MRR-per-account factors are treated as fixed."""
    s, _ = mix_rate_decomposition(pre, post)
    ch = basic_churn_compare(CHURN_WINDOW_LY, CHURN_WINDOW_CUR, exclude_paid_social=True)
    delta = upgrade_mrr_delta(pre)
    post_base = basic_base(post)
    base = float(post_base[post_base["channel_group"] == GROUP_OTHER].groupby("month_end").size().mean())
    up = s["non_ps"]
    sd = step_vs_drift(pre, post)
    per_upgrade = delta["mean_delta_mrr"]
    upgrade_point = up["change_pts"] * base * per_upgrade
    upgrade_lo, upgrade_hi = up["change_lo"] * base * per_upgrade, up["change_hi"] * base * per_upgrade
    upgrade_trend_adj = sd["step_net_of_trend_pts"] * base * per_upgrade      # if the pre-window drift had continued
    upgrade_pre_april = sd["step_from_pre_april_level_pts"] * base * per_upgrade   # if the rate had held at its pre-April level
    ret_point = -ch["change_pts"] * base * ch["avg_basic_mrr_b"]
    ret_lo, ret_hi = -ch["change_hi"] * base * ch["avg_basic_mrr_b"], -ch["change_lo"] * base * ch["avg_basic_mrr_b"]
    return {"basic_base_non_ps_per_month": base, "mrr_per_upgrade": per_upgrade,
            "mrr_per_basic_account": ch["avg_basic_mrr_b"],
            "upgrade_rate_change_pts": up["change_pts"], "upgrade_rate_step_net_of_trend_pts": sd["step_net_of_trend_pts"],
            "churn_change_pts": ch["change_pts"],
            "foregone_upgrade_mrr_per_month": upgrade_point, "foregone_upgrade_mrr_lo": upgrade_lo, "foregone_upgrade_mrr_hi": upgrade_hi,
            "foregone_upgrade_mrr_trend_adjusted": upgrade_trend_adj,
            "foregone_upgrade_mrr_from_pre_april_level": upgrade_pre_april,
            "retained_basic_mrr_per_month": ret_point, "retained_basic_mrr_lo": ret_lo, "retained_basic_mrr_hi": ret_hi,
            "net_mrr_per_month": upgrade_point + ret_point, "net_mrr_lo": upgrade_lo + ret_lo, "net_mrr_hi": upgrade_hi + ret_hi,
            "net_mrr_per_month_trend_adjusted": upgrade_trend_adj + ret_point,
            "net_mrr_12m_run_rate": 12 * (upgrade_point + ret_point),
            "net_mrr_12m_run_rate_trend_adjusted": 12 * (upgrade_trend_adj + ret_point)}


# =============================================================================== 5. downgrades
@lru_cache(maxsize=1)
def downgrade_events() -> pd.DataFrame:
    """Every plan_changes row that moves to a lower tier, with the account's attributes, what the
    change did (seats cut? MRR kept?) and the account's state 6 and 12 months later. Comparable
    accounts = same from-plan, same segment, paying in the same month, no downgrade that month."""
    cc = marts.load("fct_change_components")
    d = cc[cc["from_plan_id"].map(TIER) > cc["to_plan_id"].map(TIER)].copy()
    cust = marts.load("stg_customers")[["customer_id", "company_name", "segment", "industry", "acquisition_channel",
                                        "cs_coverage"]]
    d = d.merge(cust, on="customer_id", how="left")
    d["quarter_label"] = d["month_end"].dt.year.astype(str) + "-Q" + d["month_end"].dt.quarter.astype(str)
    d["source_label"] = d["change_source"].map(SOURCE_LABELS).fillna(d["change_source"])
    d["seats_cut"] = d["to_seats"] < d["from_seats"]
    d["mrr_retained_share"] = d["to_mrr"] / d["from_mrr"]
    p = _panel()
    piv = p.pivot(index="customer_id", columns="month_end", values="mrr")
    for h in (6, 12):
        t = d["month_end"] + pd.offsets.MonthEnd(h)
        vals = []
        for cid, tt in zip(d["customer_id"], t):
            vals.append(np.nan if tt > AS_OF else float(piv.at[cid, tt] > 0))
        d[f"paying_{h}m"] = vals
    # state 6 months later and a benchmark at each horizon: comparable accounts' share paying h months later
    st = p[["customer_id", "month_end", "state"]].rename(columns={"month_end": "t6", "state": "state_6m"})
    d["t6"] = d["month_end"] + pd.offsets.MonthEnd(6)
    d = d.merge(st, on=["customer_id", "t6"], how="left").drop(columns=["t6"])
    dn_keys = set(zip(d["customer_id"], d["month_end"]))
    paying = p[p["mrr"] > 0][["customer_id", "month_end", "plan_id", "segment"]]
    paying = paying[[(c, m) not in dn_keys for c, m in zip(paying["customer_id"], paying["month_end"])]]
    later = p[["customer_id", "month_end", "mrr"]].rename(columns={"month_end": "t_h", "mrr": "mrr_h"})
    for h in (6, 12):
        x = paying.assign(t_h=paying["month_end"] + pd.offsets.MonthEnd(h))
        x = x[x["t_h"] <= AS_OF].merge(later, on=["customer_id", "t_h"], how="left")
        bench = x.groupby(["month_end", "plan_id", "segment"]).agg(
            **{f"comparable_n_{h}m": ("customer_id", "size"),
               f"comparable_paying_{h}m": ("mrr_h", lambda s: float((s > 0).mean()))}).reset_index()
        d = d.merge(bench, left_on=["month_end", "from_plan_id", "segment"], right_on=["month_end", "plan_id", "segment"],
                    how="left").drop(columns=["plan_id"])
    d = d.rename(columns={"comparable_n_6m": "comparable_n"}).drop(columns=["comparable_n_12m"])
    return d[["change_id", "customer_id", "company_name", "effective_date", "month_end", "quarter_label", "change_source",
              "source_label", "from_plan_id", "to_plan_id", "from_seats", "to_seats", "seats_cut", "from_billing_period",
              "to_billing_period", "from_discount_pct", "to_discount_pct", "from_mrr", "to_mrr", "delta_mrr",
              "mrr_retained_share", "segment", "industry", "acquisition_channel", "cs_coverage", "state_6m",
              "paying_6m", "paying_12m", "comparable_n", "comparable_paying_6m", "comparable_paying_12m"]].sort_values("effective_date")


def downgrade_summary(window: tuple = (pd.Timestamp("2025-10-31"), AS_OF)) -> pd.DataFrame:
    d = _in_window(downgrade_events(), window)
    g = d.groupby(["from_plan_id", "to_plan_id", "change_source"]).agg(
        downgrades=("change_id", "size"), customers=("customer_id", "nunique"), mrr_delta=("delta_mrr", "sum"),
        mrr_before=("from_mrr", "sum"), seats_cut_share=("seats_cut", "mean"),
        observed_6m=("paying_6m", "count"), paying_6m_share=("paying_6m", "mean")).reset_index()
    g["mrr_retained_share"] = 1 + g["mrr_delta"] / g["mrr_before"]
    g["source_label"] = g["change_source"].map(SOURCE_LABELS)
    g["flow"] = g["from_plan_id"].map(_label) + " → " + g["to_plan_id"].map(_label)
    return g.sort_values(["from_plan_id", "change_source"], ascending=[False, True])


def downgrade_rate_by(dim: str = "segment", window: tuple = (pd.Timestamp("2025-10-31"), AS_OF), from_plan: str = "premium",
                      min_n: int = 100) -> pd.DataFrame:
    """Who downgrades: downgrades from `from_plan` effective in month m ÷ customers on that plan at the
    end of m-1 (same construction as the upgrade rate, 02 §6), by one dimension, pooled over the
    window's outcome months, with a Wilson interval."""
    p = _panel()
    base = p[(p["plan_id"] == from_plan) & (p["mrr"] > 0)][["customer_id", "month_end", "segment", "industry",
                                                            "acquisition_channel", "region", "months_since_first_paid"]].copy()
    base["outcome_month"] = base["month_end"] + pd.offsets.MonthEnd(1)
    base = _in_window(base, window, "outcome_month")
    base["tenure_band"] = pd.cut(base["months_since_first_paid"], TENURE_BINS, labels=TENURE_LABELS).astype(str)
    d = downgrade_events()
    keys = set(zip(d.loc[d["from_plan_id"] == from_plan, "customer_id"], d.loc[d["from_plan_id"] == from_plan, "month_end"]))
    base["downgraded"] = [(c, m) in keys for c, m in zip(base["customer_id"], base["outcome_month"])]
    g = base.groupby(dim, observed=True).agg(n=("customer_id", "size"), downgrades=("downgraded", "sum")).reset_index()
    g = g.rename(columns={dim: "group"})
    g.insert(0, "dimension", dim)
    g = g[g["n"] >= min_n].copy()
    g["rate"] = g["downgrades"] / g["n"]
    g["lo"], g["hi"] = wilson(g["downgrades"], g["n"])
    g["from_plan"] = from_plan
    return g.sort_values("rate", ascending=False)


def downgrade_survival(h: int = 6) -> pd.DataFrame:
    """Share of downgraded accounts still paying h months later (h = 6 or 12), by source, with a Wilson
    interval, next to the comparable-account benchmark at the same horizon (same plan, segment and month,
    no downgrade)."""
    d = downgrade_events().dropna(subset=[f"paying_{h}m"])
    g = d.groupby("change_source").agg(downgrades=("change_id", "size"), paying=(f"paying_{h}m", "sum"),
                                       comparable_paying=(f"comparable_paying_{h}m", "mean"),
                                       mrr_retained_share=("mrr_retained_share", "mean")).reset_index()
    g["paying_share"] = g["paying"] / g["downgrades"]
    g["lo"], g["hi"] = wilson(g["paying"], g["downgrades"])
    g["gap_vs_comparable_pts"] = g["paying_share"] - g["comparable_paying"]
    g["source_label"] = g["change_source"].map(SOURCE_LABELS)
    g.insert(0, "horizon_months", h)
    return g.sort_values("downgrades", ascending=False)


# =============================================================================== 6. time to upgrade
def time_to_upgrade(by: str = "acquisition_channel", max_k: int = 24, cohort_window: tuple | None = None,
                    min_customers: int = 100) -> pd.DataFrame:
    """Cumulative incidence of a first voluntary upgrade by months since the first paid month-end,
    for customers who started on Basic, with churn as a competing event (Aalen-Johansen on the
    monthly panel). Forced seat-cap upgrades are censored at the upgrade month. Customers still on
    Basic at the as-of date are censored."""
    p = _panel()
    if cohort_window:
        p = _in_window(p, cohort_window, "cohort_month_end")
    k0 = p[(p["months_since_first_paid"] == 0) & (p["plan_id"] == "basic")]
    ids = k0["customer_id"]
    q = p[p["customer_id"].isin(ids)].sort_values(["customer_id", "months_since_first_paid"])
    cc = marts.load("fct_change_components")
    forced = cc[(cc["from_plan_id"] == "basic") & (cc["to_plan_id"].map(TIER) > 1) & (cc["change_source"] == "system")]
    forced_keys = set(zip(forced["customer_id"], forced["month_end"]))
    is_forced = np.array([(c, m) in forced_keys for c, m in zip(q["customer_id"], q["month_end"])])
    q = q.assign(event=np.select([q["mrr"] == 0, (q["tier_rank"] > 1) & is_forced, q["tier_rank"] > 1],
                                 ["churn", "forced", "upgrade"], "none"))
    first_ev = q[q["event"] != "none"].groupby("customer_id").first()[["months_since_first_paid", "event"]]
    last_k = q.groupby("customer_id")["months_since_first_paid"].max()
    base = k0.set_index("customer_id")[[by]].join(first_ev).join(last_k.rename("last_k"))
    base["T"] = base["months_since_first_paid"].fillna(base["last_k"]).astype(int)
    base["kind"] = base["event"].fillna("censored")
    rows = []
    for grp, g in base.groupby(by, observed=True):
        if len(g) < min_customers:
            continue
        S, cu, cc_ = 1.0, 0.0, 0.0
        T, kind = g["T"].values, g["kind"].values
        for k in range(1, max_k + 1):
            at_risk = int((T >= k).sum())
            if at_risk == 0:
                break
            du = int(((T == k) & (kind == "upgrade")).sum())
            dc = int(((T == k) & (kind == "churn")).sum())
            cu += S * du / at_risk
            cc_ += S * dc / at_risk
            S *= 1 - (du + dc) / at_risk
            rows.append({"group": grp, "k": k, "at_risk": at_risk, "customers": len(g), "cif_upgrade": cu,
                         "cif_churn": cc_, "still_basic": S})
    out = pd.DataFrame(rows)
    out.insert(0, "by", by)
    return out


# =============================================================================== 7. sizing the packaging holdout
def holdout_power(alpha: float = 0.05, power: float = 0.80, test_months: int = 6, horizon: int = 6) -> dict:
    """Size of the holdout that would measure the packaging change (re-gate advanced automations for a random
    half of new Basic signups for `test_months` months; primary metric = share of a signup cohort with a voluntary
    upgrade within `horizon` months). Old-packaging rate = Basic signups Apr-Sep 2025, whose first six months end
    before April 2026; new-packaging rate = old rate x (1 + the non-Paid-Social relative change in the monthly
    rate), an approximation. Two-sided z-test for two proportions at the stated alpha and power."""
    from statistics import NormalDist
    sig = signup_states()
    recent = sig[(sig["plan_0"] == "basic") & (sig["cohort_month_end"] >= POST_WINDOW[0])]
    per_month = len(recent) / recent["cohort_month_end"].nunique()
    n_arm = per_month * test_months / 2
    old = time_to_upgrade("plan_id", max_k=horizon, cohort_window=(PRE_WINDOW[0], pd.Timestamp("2025-09-30")))
    p_old = float(old.loc[old["k"] == horizon, "cif_upgrade"].iloc[0])
    s, _ = mix_rate_decomposition()
    p_new = p_old * (1 + s["non_ps"]["rel_change"])
    var = p_old * (1 - p_old) + p_new * (1 - p_new)
    z_a, z_b = NormalDist().inv_cdf(1 - alpha / 2), NormalDist().inv_cdf(power)
    gap = p_old - p_new
    return {"basic_signups_per_month": per_month, "n_per_arm": n_arm, "p_old": p_old, "p_new": p_new, "gap_pts": gap,
            "n_per_arm_needed": float((z_a + z_b) ** 2 * var / gap ** 2), "mde_pts": float((z_a + z_b) * np.sqrt(var / n_arm)),
            "power_at_n": float(NormalDist().cdf(abs(gap) / np.sqrt(var / n_arm) - z_a)), "alpha": alpha, "target_power": power}


def downgrade_rate_yoy(window: tuple = (pd.Timestamp("2025-10-31"), AS_OF),
                       prior: tuple = (pd.Timestamp("2024-10-31"), pd.Timestamp("2025-09-30"))) -> dict:
    """Downgrades per Premium / Enterprise account-month (customers paying on a higher plan at the end of m-1),
    last 12 months vs the 12 before: did downgrades rise faster than the base that can downgrade?"""
    d, p = downgrade_events(), _panel()
    base = p[p["plan_id"].isin(["premium", "enterprise"]) & (p["mrr"] > 0)][["customer_id", "month_end"]]
    base = base.assign(outcome_month=base["month_end"] + pd.offsets.MonthEnd(1))
    out = {}
    for lab, w in (("last", window), ("prior", prior)):
        n_dn, n_base = len(_in_window(d, w)), len(_in_window(base, w, "outcome_month"))
        lo, hi = wilson(n_dn, n_base)
        out.update({f"downgrades_{lab}": n_dn, f"account_months_{lab}": n_base, f"rate_{lab}": n_dn / n_base,
                    f"rate_{lab}_lo": float(lo), f"rate_{lab}_hi": float(hi)})
    return out


# =============================================================================== build / key numbers
def build() -> dict[str, pd.DataFrame]:
    """Derived tables written to the parquet cache (topic prefix `transitions__`)."""
    dec_summary, dec_table = mix_rate_decomposition()
    dec_table.insert(0, "by", "acquisition_channel")
    dec_ct_summary, dec_ct_table = mix_rate_decomposition(by="channel_tech")
    dec_ct_table.insert(0, "by", "channel_tech")
    tables = {
        "transitions__signup_states": signup_states(),
        "transitions__sankey_links": pd.concat([sankey_links(12).assign(horizon=12), sankey_links(24).assign(horizon=24)],
                                               ignore_index=True),
        "transitions__transition_matrix": transition_matrix(),
        "transitions__upgrade_rate_pre_post": upgrade_rate_pre_post(),
        "transitions__upgrade_rate_quarterly": upgrade_rate_quarterly(),
        "transitions__upgrade_rate_monthly": pd.concat([upgrade_rate_monthly(None), upgrade_rate_monthly("channel_group")],
                                                       ignore_index=True),
        "transitions__mix_rate_decomposition": pd.concat([dec_table, dec_ct_table], ignore_index=True),
        "transitions__basic_churn_monthly": pd.concat([basic_churn_monthly(True).assign(population="Non-Paid-Social Basic"),
                                                       basic_churn_monthly(False).assign(population="All Basic")],
                                                      ignore_index=True),
        "transitions__downgrades": downgrade_events(),
        "transitions__downgrade_summary_12m": downgrade_summary(),
        "transitions__downgrade_rate_12m": pd.concat([downgrade_rate_by(d) for d in ("segment", "acquisition_channel", "industry",
                                                                                      "tenure_band")], ignore_index=True),
        "transitions__downgrade_survival": pd.concat([downgrade_survival(6), downgrade_survival(12)], ignore_index=True),
        "transitions__time_to_upgrade": pd.concat([time_to_upgrade("acquisition_channel"), time_to_upgrade("segment")],
                                                  ignore_index=True),
    }
    for name, df in tables.items():
        marts.write(name, df)
    return tables


def key_numbers() -> dict:
    s, _ = mix_rate_decomposition()
    s_ct, _ = mix_rate_decomposition(by="channel_tech")
    sd = step_vs_drift()
    ch = basic_churn_compare()
    ch_nt = basic_churn_compare(exclude_technology=True)
    net = net_mrr_effect()
    t12_basic, t12_prem, t24_basic = transition_shares(12, "basic"), transition_shares(12, "premium"), transition_shares(24, "basic")
    ss = signup_states()
    sv = downgrade_survival(6).set_index("change_source")
    d12 = _in_window(downgrade_events(), (pd.Timestamp("2025-10-31"), AS_OF))
    ttu = time_to_upgrade("acquisition_channel")
    ttu12 = ttu[ttu["k"] == 12].set_index("group")
    base_all = _basic_base()
    controlled = s_ct["non_ps_non_tech"]
    dy = downgrade_rate_yoy()
    hp = holdout_power()
    return {
        "as_of": str(AS_OF.date()), "packaging_change_date": str(PACKAGING_DATE.date()),
        "pre_window": s["pre_window"], "post_window": s["post_window"],
        # headline upgrade rates (02 §6; account-month grain, pooled over the window)
        "upgrade_rate_pre": s["rate_pre"], "upgrade_rate_post": s["rate_post"],
        "upgrade_rate_change_pts": s["change_pts"], "upgrade_rate_rel_change": s["rel_change"],
        "basic_account_months_pre": s["n_pre"], "basic_account_months_post": s["n_post"],
        "voluntary_upgrades_pre": s["upgrades_pre"], "voluntary_upgrades_post": s["upgrades_post"],
        "forced_upgrades_pre": s["forced_pre"], "forced_upgrades_post": s["forced_post"],
        "forced_upgrades_total": int(base_all["forced_upgrade_only"].sum()),
        "upgrade_rate_non_ps_pre": s["non_ps"]["rate_pre"], "upgrade_rate_non_ps_post": s["non_ps"]["rate_post"],
        "upgrade_rate_non_ps_change_pts": s["non_ps"]["change_pts"], "upgrade_rate_non_ps_change_lo": s["non_ps"]["change_lo"],
        "upgrade_rate_non_ps_change_hi": s["non_ps"]["change_hi"], "upgrade_rate_non_ps_rel_change": s["non_ps"]["rel_change"],
        "upgrade_rate_ps_pre": s["ps"]["rate_pre"], "upgrade_rate_ps_post": s["ps"]["rate_post"],
        "upgrade_rate_controlled_pre": controlled["rate_pre"], "upgrade_rate_controlled_post": controlled["rate_post"],
        "upgrade_rate_controlled_rel_change": controlled["rel_change"],
        "upgrade_rate_controlled_change_lo": controlled["change_lo"], "upgrade_rate_controlled_change_hi": controlled["change_hi"],
        "product_trigger_threshold_rel": -0.20, "product_trigger_met": bool(controlled["rel_change"] <= -0.20),
        # mix vs rate
        "paid_social_share_basic_base_pre": s["paid_social_share_pre"], "paid_social_share_basic_base_post": s["paid_social_share_post"],
        "mix_effect_pts_shapley": s["mix_shapley"], "rate_effect_pts_shapley": s["rate_shapley"],
        "mix_effect_pts_at_pre_rates": s["mix_at_pre_rates"], "rate_effect_pts_at_post_mix": s["rate_at_post_mix"],
        "mix_effect_pts_at_post_rates": s["mix_at_post_rates"], "rate_effect_pts_at_pre_mix": s["rate_at_pre_mix"],
        "mix_share_of_change": s["mix_share_of_change"],
        "mix_effect_pts_shapley_channel_x_tech": s_ct["mix_shapley"], "rate_effect_pts_shapley_channel_x_tech": s_ct["rate_shapley"],
        # timing
        "non_ps_pre_trend_slope_pts_per_month": sd["slope_pts_per_month"], "non_ps_extrapolated_post_rate": sd["extrapolated_post"],
        "non_ps_step_net_of_trend_pts": sd["step_net_of_trend_pts"], "non_ps_step_net_of_trend_rel": sd["step_net_of_trend_rel"],
        "non_ps_rate_jan_mar_2026": sd["rate_jan_mar_2026"],
        "non_ps_step_from_pre_april_level_pts": sd["step_from_pre_april_level_pts"],
        "non_ps_pre_trend_level_end_of_march": sd["pre_trend_level_at_end_of_pre"],
        "non_ps_rate_apr_sep_2025": sd["rate_apr_sep_2025"], "non_ps_rate_oct_2025_mar_2026": sd["rate_oct_2025_mar_2026"],
        "non_ps_rate_may_jul_2026": sd["rate_may_jul_2026"], "non_ps_step_3m_pts": sd["step_3m_pts"],
        "non_ps_step_3m_lo": sd["step_3m_lo"], "non_ps_step_3m_hi": sd["step_3m_hi"],
        "non_ps_lowest_month": sd["lowest_month"], "non_ps_lowest_month_rate": sd["lowest_month_rate"],
        # packaging upside: Basic logo churn
        "basic_churn_non_ps_may_sep_2025": ch["churn_a"], "basic_churn_non_ps_may_sep_2026": ch["churn_b"],
        "basic_churn_non_ps_change_pts": ch["change_pts"], "basic_churn_non_ps_change_lo": ch["change_lo"],
        "basic_churn_non_ps_change_hi": ch["change_hi"], "basic_churn_non_ps_rel_change": ch["rel_change"],
        "basic_churn_non_ps_n_2025": ch["n_a"], "basic_churn_non_ps_n_2026": ch["n_b"],
        "basic_churn_non_ps_non_tech_change_pts": ch_nt["change_pts"], "basic_churn_non_ps_non_tech_change_lo": ch_nt["change_lo"],
        "basic_churn_non_ps_non_tech_change_hi": ch_nt["change_hi"],
        # net MRR effect (run-rate per month, non-Paid-Social Basic base)
        "net_effect_basic_base_non_ps": net["basic_base_non_ps_per_month"], "net_effect_mrr_per_upgrade": net["mrr_per_upgrade"],
        "net_effect_mrr_per_basic_account": net["mrr_per_basic_account"],
        "foregone_upgrade_mrr_per_month": net["foregone_upgrade_mrr_per_month"], "foregone_upgrade_mrr_lo": net["foregone_upgrade_mrr_lo"],
        "foregone_upgrade_mrr_hi": net["foregone_upgrade_mrr_hi"], "foregone_upgrade_mrr_trend_adjusted": net["foregone_upgrade_mrr_trend_adjusted"],
        "foregone_upgrade_mrr_from_pre_april_level": net["foregone_upgrade_mrr_from_pre_april_level"],
        "retained_basic_mrr_per_month": net["retained_basic_mrr_per_month"], "retained_basic_mrr_lo": net["retained_basic_mrr_lo"],
        "retained_basic_mrr_hi": net["retained_basic_mrr_hi"],
        "net_mrr_per_month": net["net_mrr_per_month"], "net_mrr_lo": net["net_mrr_lo"], "net_mrr_hi": net["net_mrr_hi"],
        "net_mrr_per_month_trend_adjusted": net["net_mrr_per_month_trend_adjusted"],
        "net_mrr_12m_run_rate": net["net_mrr_12m_run_rate"], "net_mrr_12m_run_rate_trend_adjusted": net["net_mrr_12m_run_rate_trend_adjusted"],
        "basic_mrr_sep_2026": float(_panel().pipe(lambda p: p[(p["month_end"] == AS_OF) & (p["plan_id"] == "basic")]["mrr"].sum())),
        # flows
        "transition_12m_basic": t12_basic, "transition_12m_premium": t12_prem, "transition_24m_basic": t24_basic,
        "signups_total": int(len(ss)), "signups_basic": int((ss["plan_0"] == "basic").sum()),
        "signups_observed_12m": int(ss["state_12"].notna().sum()), "signups_observed_24m": int(ss["state_24"].notna().sum()),
        "basic_signups_observed_12m": int(ss[ss["plan_0"] == "basic"]["state_12"].notna().sum()),
        "basic_signups_state_12m": {k: float(v) for k, v in ss[ss["plan_0"] == "basic"]["state_12"].value_counts(normalize=True).items()},
        "basic_signups_state_24m": {k: float(v) for k, v in ss[ss["plan_0"] == "basic"]["state_24"].value_counts(normalize=True).items()},
        # downgrades
        "downgrades_last_12m": int(len(d12)), "downgrades_last_12m_mrr_delta": float(d12["delta_mrr"].sum()),
        "downgrades_last_12m_by_source": {k: int(v) for k, v in d12["change_source"].value_counts().items()},
        "downgrades_last_12m_premium_to_basic": int(((d12["from_plan_id"] == "premium") & (d12["to_plan_id"] == "basic")).sum()),
        "downgrades_last_12m_enterprise_to_premium": int(((d12["from_plan_id"] == "enterprise") & (d12["to_plan_id"] == "premium")).sum()),
        "downgrades_last_12m_seats_cut_share": float(d12["seats_cut"].mean()),
        "premium_downgrade_rate_last_12m_by_segment": {r.group: float(r.rate) for r in downgrade_rate_by("segment").itertuples()},
        "premium_downgrade_rate_last_12m_by_industry": {r.group: float(r.rate) for r in downgrade_rate_by("industry").itertuples()},
        "downgrades_last_12m_mrr_retained_share": float(1 + d12["delta_mrr"].sum() / d12["from_mrr"].sum()),
        # downgrades per Premium / Enterprise account-month, last 12 months vs the 12 before
        "downgrades_prior_12m": dy["downgrades_prior"], "downgrade_rate_last_12m": dy["rate_last"],
        "downgrade_rate_prior_12m": dy["rate_prior"],
        "cs_save_downgrades_observed_6m": int(sv.loc["customer_success", "downgrades"]),
        "cs_save_paying_6m_share": float(sv.loc["customer_success", "paying_share"]),
        "cs_save_paying_6m_lo": float(sv.loc["customer_success", "lo"]), "cs_save_paying_6m_hi": float(sv.loc["customer_success", "hi"]),
        "cs_save_comparable_paying_6m_share": float(sv.loc["customer_success", "comparable_paying"]),
        "cs_save_mrr_retained_share": float(sv.loc["customer_success", "mrr_retained_share"]),
        "self_serve_downgrade_paying_6m_share": float(sv.loc["self_serve", "paying_share"]),
        "self_serve_downgrades_observed_6m": int(sv.loc["self_serve", "downgrades"]),
        "renewal_downgrade_paying_6m_share": float(sv.loc["renewal", "paying_share"]),
        "renewal_downgrades_observed_6m": int(sv.loc["renewal", "downgrades"]),
        # time to upgrade (cumulative incidence at 12 months, churn competing)
        "time_to_upgrade_12m_by_channel": {g: float(r["cif_upgrade"]) for g, r in ttu12.iterrows()},
        "churn_12m_by_channel": {g: float(r["cif_churn"]) for g, r in ttu12.iterrows()},
        "basic_signups_by_channel": {g: int(r["customers"]) for g, r in ttu12.iterrows()},
        # sizing the packaging holdout (P5): two quarters of new Basic signups, 50 / 50, 6-month upgrade share
        "holdout_basic_signups_per_month": hp["basic_signups_per_month"], "holdout_n_per_arm": hp["n_per_arm"],
        "holdout_p_old_6m": hp["p_old"], "holdout_p_new_6m": hp["p_new"], "holdout_gap_pts": hp["gap_pts"],
        "holdout_n_per_arm_needed_80_power": hp["n_per_arm_needed"], "holdout_power_at_n": hp["power_at_n"],
        "holdout_mde_pts_80_power": hp["mde_pts"],
    }


# =============================================================================== figures
def fig_sankey(horizon: int = 12, filters: dict | None = None, cohort_window: tuple | None = None) -> go.Figure:
    links = sankey_links(horizon, filters, cohort_window)
    if links.empty:
        fig = go.Figure()
        fig.add_annotation(x=0.5, y=0.5, xref="paper", yref="paper", showarrow=False, font=dict(color=theme.INK_2, size=14),
                           text=f"No customers with {horizon} months of history in this selection — widen the signup window or the filters")
        fig.update_xaxes(visible=False)
        fig.update_yaxes(visible=False)
        return theme.apply(fig, "Nothing to show for this selection", None, height=300)
    n_view = int(links["customers_in_view"].iloc[0])
    stages = ["start", "12m"] + (["24m"] if horizon == 24 else [])
    stage_title = {"start": "First paid month", "12m": "12 months later", "24m": "24 months later"}
    present = set(zip(links["stage_from"], links["state_from"])) | set(zip(links["stage_to"], links["state_to"]))
    nodes = [(st, s) for st in stages for s in STATES if (st, s) in present]      # only states that occur
    idx = {n: i for i, n in enumerate(nodes)}
    size = {}
    for keys, g in links.groupby(["stage_from", "state_from"]):
        size[keys] = int(g["customers"].sum())
    for keys, g in links.groupby(["stage_to", "state_to"]):
        size[keys] = int(g["customers"].sum())
    thickness, pad = 16, 28
    xs = {"start": 0.004, "12m": 0.5 if horizon == 24 else 0.996, "24m": 0.996}   # node centres (0 is ignored by Plotly)
    fig = go.Figure(go.Sankey(
        arrangement="fixed",
        node=dict(label=[""] * len(nodes), color=[PLAN_COLORS[s] for _, s in nodes], pad=pad, thickness=thickness,
                  line=dict(color=theme.SURFACE, width=1), x=[xs[st] for st, _ in nodes], y=[0.5] * len(nodes),
                  customdata=[f"{stage_title[st]}: {_label(s)}, {size[(st, s)]:,} customers ({size[(st, s)] / n_view:.0%})"
                              for st, s in nodes],
                  hovertemplate="%{customdata}<extra></extra>"),
        link=dict(source=[idx[(r.stage_from, r.state_from)] for r in links.itertuples()],
                  target=[idx[(r.stage_to, r.state_to)] for r in links.itertuples()],
                  value=links["customers"].tolist(),
                  color=[_rgba(PLAN_COLORS[s], 0.30) for s in links["state_from"]],
                  customdata=np.c_[links["share_of_from"], [_label(s) for s in links["state_from"]],
                                   [_label(s) for s in links["state_to"]]],
                  hovertemplate="%{customdata[1]} → %{customdata[2]}: %{value:,} customers (%{customdata[0]:.1%} of %{customdata[1]})<extra></extra>")))
    ss = signup_states(filters, cohort_window).dropna(subset=[f"state_{horizon}"])
    b = ss[ss["plan_0"] == "basic"][f"state_{horizon}"].value_counts(normalize=True)
    if len(b):
        title = (f"Of Basic signups, {pct(b.get('premium', 0) + b.get('enterprise', 0), 0)} are on a higher plan {horizon} months "
                 f"later and {pct(b.get('churned', 0), 0)} have churned")
    else:     # e.g. the Enterprise segment alone: nobody in view started on Basic
        title = (f"No Basic signups in this selection; {pct((ss[f'state_{horizon}'] == 'churned').mean(), 0)} of these signups "
                 f"have churned {horizon} months later")
    sub = (f"{n_view:,} customers followed from their first paid month ({ss['cohort_month_end'].min():%b %Y} – "
           f"{ss['cohort_month_end'].max():%b %Y}) to {horizon} months later; node labels and link widths = customers")
    fig.update_layout(margin=dict(l=128, r=128, t=124, b=16), showlegend=False)
    fig = theme.apply(fig, title, sub, height=440 if horizon == 12 else 480)
    # Fixed node layout (each column stacked top-down in plan order, `pad` px apart, heights as Plotly computes them),
    # so the labels can sit outside the outer columns instead of on top of the links.
    plot_h = fig.layout.height - fig.layout.margin.t - fig.layout.margin.b
    cols = {st: [n for n in nodes if n[0] == st] for st in stages}
    ky = (plot_h - (max(len(c) for c in cols.values()) - 1) * pad) / n_view
    ys = {}
    for col in cols.values():
        top = 0.0
        for n in col:
            ys[n] = (top + size[n] * ky / 2) / plot_h
            top += size[n] * ky + pad
    fig.update_traces(node_y=[ys[n] for n in nodes])
    for st, s in nodes:
        outer = st == "start" or st == stages[-1]
        fig.add_annotation(x=xs[st], y=1 - ys[(st, s)], xref="paper", yref="paper", text=f"{_label(s)} · {size[(st, s)]:,}",
                           showarrow=False, yanchor="middle", xanchor="right" if st == "start" else "left",
                           xshift=-(thickness / 2 + 6) if st == "start" else thickness / 2 + 6,
                           font=dict(color=theme.INK, size=12),
                           bgcolor=None if outer else _rgba(theme.SURFACE, 0.85), borderpad=2)
    for st in stages:
        fig.add_annotation(x=xs[st], y=1, xref="paper", yref="paper", yanchor="bottom", yshift=8, text=stage_title[st],
                           showarrow=False, font=dict(color=theme.INK_2, size=12), xanchor="center")
    return fig


def fig_transition_heatmap(horizon: int = 12, filters: dict | None = None) -> go.Figure:
    m = transition_matrix(filters, horizons=(horizon,))
    piv = m.pivot(index="from_plan", columns="to_state", values="share_of_from_plan").reindex(index=PLANS, columns=STATES).fillna(0)
    n = m.groupby("from_plan")["customer_months"].sum().reindex(PLANS).fillna(0)
    z = piv.values
    fig = go.Figure(go.Heatmap(
        z=z, x=[_label(s) for s in STATES], y=[f"{_label(p)} (n = {int(n[p]):,})" for p in PLANS],
        colorscale=theme.SEQ_SCALE, zmin=0, zmax=1, xgap=3, ygap=3,
        text=np.vectorize(lambda v: f"{v * 100:.1f}%")(z), texttemplate="%{text}", textfont=dict(size=12),
        colorbar=dict(tickformat=".0%", thickness=12, outlinewidth=0),
        hovertemplate="from %{y}<br>to %{x} after " + str(horizon) + " months: %{z:.1%}<extra></extra>"))
    fig.update_yaxes(autorange="reversed", showgrid=False, automargin=True)
    fig.update_xaxes(showgrid=False, side="top")
    b = piv.loc["basic"]
    return theme.apply(fig, f"{horizon} months on: {pct(b['basic'], 0)} of Basic account-months still on Basic, "
                            f"{pct(b['premium'] + b['enterprise'], 0)} moved up, {pct(b['churned'], 0)} churned",
                       f"Share of the account-months on each plan that are in each state {horizon} months later. Every paying "
                       "month-end counts as a start, so accounts already past their first year sit alongside new ones — unlike "
                       "the Sankey, which follows signups from their first paid month; pooled over all start months with the "
                       "horizon observed; n = account-months", height=320)


def fig_upgrade_rate_quarterly(filters: dict | None = None, start: str = "2024-Q4") -> go.Figure:
    g = upgrade_rate_quarterly("channel_group", filters)
    g = g[g["quarter_label"] >= start]
    fig = go.Figure()
    for grp in (GROUP_OTHER, GROUP_PS):
        s = g[g["group"] == grp]
        c = GROUP_COLORS[grp]
        fig.add_trace(go.Scatter(x=pd.concat([s["quarter_label"], s["quarter_label"][::-1]]),
                                 y=pd.concat([s["hi"], s["lo"][::-1]]), fill="toself", mode="lines", fillcolor=_rgba(c, 0.15),
                                 line=dict(color="rgba(0,0,0,0)"), hoverinfo="skip", showlegend=False, name=f"{grp} 95% interval"))
        fig.add_trace(go.Scatter(x=s["quarter_label"], y=s["rate"], mode="lines+markers", name=grp,
                                 line=dict(color=c, width=2), marker=dict(size=8, color=c, line=dict(color=theme.SURFACE, width=2)),
                                 customdata=np.c_[s["n"], s["upgrades"], s["lo"], s["hi"]],
                                 hovertemplate="%{x} · " + grp + "<br>%{y:.2%} per month (95%: %{customdata[2]:.2%} – %{customdata[3]:.2%})"
                                               "<br>%{customdata[1]:,} upgrades / %{customdata[0]:,} Basic account-months<extra></extra>"))
    fig.add_vline(x="2026-Q2", line=dict(color=theme.AXIS, width=1, dash="dot"))
    fig.add_annotation(x="2026-Q2", y=1.0, yref="paper", text="Packaging change (15 Apr 2026)", showarrow=False,
                       xanchor="left", xshift=6, font=dict(color=theme.MUTED, size=11))
    fig.update_yaxes(tickformat=".1%", range=[0, max(0.045, float(g[g["group"] == GROUP_OTHER]["hi"].max()) * 1.1)])
    last_o = g[(g["group"] == GROUP_OTHER)].iloc[-1]
    ly = g[(g["group"] == GROUP_OTHER) & (g["quarter_label"] == "2025-Q3")]["rate"]
    ly_o = float(ly.iloc[0]) if len(ly) else float(g[g["group"] == GROUP_OTHER]["rate"].iloc[0])
    return theme.apply(fig, f"Basic upgrade rate fell in both channel groups: non-Paid-Social {pct(ly_o, 2)} → {pct(last_o['rate'], 2)} "
                            f"per month (Q3 2025 → Q3 2026); Paid Social was low throughout",
                       "Voluntary Basic → higher-plan upgrades per Basic account-month, by quarter, with Wilson 95% intervals "
                       "(wide for Paid Social, a small base); forced seat-cap upgrades excluded", height=400)


def fig_upgrade_rate_pre_post(dim: str = "acquisition_channel", pre: tuple = PRE_WINDOW, post: tuple = POST_WINDOW) -> go.Figure:
    t = upgrade_rate_pre_post((dim,), pre, post)
    if dim == "tenure_band":
        t = t.set_index("group").reindex([x for x in TENURE_LABELS if x in set(t["group"])]).reset_index()
    else:
        t = t.sort_values("rate_pre")
    fig = go.Figure()
    for r in t.itertuples():
        fig.add_trace(go.Scatter(x=[r.rate_pre, r.rate_post], y=[r.group] * 2, mode="lines", line=dict(color=theme.AXIS, width=2),
                                 showlegend=False, hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=t["rate_pre"], y=t["group"], mode="markers", name=f"Pre ({window_label(pre)})",
                             marker=dict(size=10, color=theme.DEEMPH, line=dict(color=theme.SURFACE, width=2)),
                             error_x=dict(type="data", symmetric=False, array=t["hi_pre"] - t["rate_pre"], arrayminus=t["rate_pre"] - t["lo_pre"],
                                          color=theme.DEEMPH, thickness=1, width=0),
                             customdata=np.c_[t["n_pre"], t["upgrades_pre"]],
                             hovertemplate="%{y}<br>pre %{x:.2%} per month (%{customdata[1]:,} / %{customdata[0]:,})<extra></extra>"))
    fig.add_trace(go.Scatter(x=t["rate_post"], y=t["group"], mode="markers+text", name=f"Post ({window_label(post)})",
                             text=[pct(v, 2) for v in t["rate_post"]], textposition="middle right", textfont=dict(color=theme.INK, size=11),
                             marker=dict(size=10, color=theme.ACCENT, line=dict(color=theme.SURFACE, width=2)),
                             error_x=dict(type="data", symmetric=False, array=t["hi_post"] - t["rate_post"], arrayminus=t["rate_post"] - t["lo_post"],
                                          color=theme.ACCENT, thickness=1, width=0),
                             customdata=np.c_[t["n_post"], t["upgrades_post"], t["rel_change"]],
                             hovertemplate="%{y}<br>post %{x:.2%} per month (%{customdata[1]:,} / %{customdata[0]:,})<br>change %{customdata[2]:+.0%}<extra></extra>"))
    fig.update_xaxes(tickformat=".1%", showgrid=True, gridcolor=theme.GRID, rangemode="tozero")
    fig.update_yaxes(showgrid=False, automargin=True, autorange="reversed" if dim == "tenure_band" else True)
    fig.update_layout(margin=dict(l=40, r=70))
    hi, lo = t.sort_values("rate_pre").iloc[-1], t.sort_values("rate_pre").iloc[0]
    n_down = int((t["change_pts"] < 0).sum())
    tail = "every group is lower after it" if n_down == len(t) else f"{n_down} of {len(t)} groups are lower after it"
    return theme.apply(fig, f"By {DIMENSION_LABELS[dim].lower()}: {hi.group} upgraded most before the change ({pct(hi.rate_pre, 2)} per month), "
                            f"{lo.group} least ({pct(lo.rate_pre, 2)}); {tail}",
                       "Monthly voluntary Basic → higher upgrade rate, pooled over each window; bars = Wilson 95% intervals; groups with ≥ 100 account-months",
                       height=max(300, 110 + 36 * len(t)))


def fig_mix_rate(summary: dict, table: pd.DataFrame | None = None) -> go.Figure:
    """Bridge: pooled rate pre -> mix effect -> rate effect -> pooled rate post (Shapley split)."""
    steps = [("Pooled rate, pre", summary["rate_pre"], "total"),
             ("Mix: Basic base by channel", summary["mix_shapley"], "delta"),
             ("Rate: within-channel upgrade rates", summary["rate_shapley"], "delta"),
             ("Pooled rate, post", summary["rate_post"], "total")]
    x, y, base, colors, text = [], [], [], [], []
    level = 0.0
    for name, v, kind in steps:
        x.append(name)
        if kind == "total":
            base.append(0)
            y.append(v)
            level = v
            colors.append(theme.INK_2)
            text.append(pct(v, 2))
        else:
            base.append(level if v >= 0 else level + v)
            y.append(abs(v))
            level += v
            colors.append(theme.MUTED)        # sign is in the label and the bar's direction; red is an initiator colour on this page
            text.append(pts(v, 2))
    fig = go.Figure(go.Bar(x=x, y=y, base=base, marker=dict(color=colors), text=text, textposition="outside",
                           textfont=dict(color=theme.INK), cliponaxis=False, hovertemplate="%{x}: %{text}<extra></extra>"))
    fig.update_yaxes(tickformat=".1%", rangemode="tozero")
    fig.update_layout(showlegend=False, bargap=0.45)
    rng = f"{pts(min(summary['mix_at_pre_rates'], summary['mix_at_post_rates']), 2)} to {pts(max(summary['mix_at_pre_rates'], summary['mix_at_post_rates']), 2)}"
    share = summary["mix_share_of_change"]
    return theme.apply(fig, f"Channel mix explains about {pct(share, 0)} of the {pts(summary['change_pts'], 2)} fall in the Basic upgrade rate; "
                            f"within-channel rates explain the rest",
                       f"Monthly voluntary upgrade rate, {summary['pre_window']} vs {summary['post_window']}; Shapley split "
                       f"(mix effect across the two orderings: {rng})", height=380)


def fig_upgrade_rate_monthly(start: str = "2025-01-31", pre: tuple = PRE_WINDOW, post: tuple = POST_WINDOW) -> go.Figure:
    """Timing within the non-Paid-Social base (the Paid Social series is too small for a monthly
    read; it is on the quarterly chart): a step at the packaging date vs the drift already under way."""
    allm = _monthly_group(GROUP_OTHER)
    s = allm[allm["month_end"] >= start]
    c = GROUP_COLORS[GROUP_OTHER]
    x = theme.month_x(s["month_end"])
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=pd.concat([x, x[::-1]]), y=pd.concat([s["hi"], s["lo"][::-1]]), fill="toself", mode="lines",
                             fillcolor=_rgba(c, 0.12), line=dict(color="rgba(0,0,0,0)"), hoverinfo="skip", showlegend=False))
    fig.add_trace(go.Scatter(x=x, y=s["rate"], mode="lines+markers", name="Non-Paid-Social Basic customers", line=dict(color=c, width=2),
                             marker=dict(size=8, color=c, line=dict(color=theme.SURFACE, width=2)),
                             customdata=np.c_[s["n"], s["upgrades"]],
                             hovertemplate="%{x|%b %Y}<br>%{y:.2%} (%{customdata[1]:,} upgrades / %{customdata[0]:,} account-months)<extra></extra>"))
    # every number drawn here and in the title comes from the same step_vs_drift() call (same windows, same fit)
    sd = step_vs_drift(pre, post)
    slope, intercept = sd["slope_pts_per_month"], sd["trend_intercept"]
    ext = allm[(allm["month_end"] >= pre[0]) & (allm["month_end"] <= post[1])]
    fig.add_trace(go.Scatter(x=theme.month_x(ext["month_end"]), y=intercept + slope * ext["t"], mode="lines",
                             name="Pre-window trend, extended", line=dict(color=theme.INK_2, width=1.5, dash="dot"),
                             hovertemplate="%{x|%b %Y}<br>trend %{y:.2%}<extra></extra>"))
    for w, lab, rate, col in ((pre, "before", sd["pre_mean"], theme.DEEMPH), (post, "after", sd["actual_post"], theme.INK_2)):
        x0 = theme.month_x(pd.Series([w[0]])).iloc[0]
        x1 = theme.month_x(pd.Series([w[1]])).iloc[0] + pd.Timedelta(days=27)
        fig.add_shape(type="line", x0=x0, x1=x1, y0=rate, y1=rate, line=dict(color=col, width=2))
        fig.add_annotation(x=x1, y=rate, text=f"{lab} {pct(rate, 2)}", showarrow=False, xanchor="left", xshift=4,
                           font=dict(color=theme.INK_2, size=11))
    # the trend's average over the post window (what the step is measured against), labelled at the window's middle month
    pm = _in_window(allm, post).reset_index(drop=True)
    mid = pm.iloc[len(pm) // 2]
    fig.add_annotation(x=theme.month_x(pd.Series([mid["month_end"]])).iloc[0], y=intercept + slope * mid["t"], yshift=11,
                       text=f"trend {pct(sd['extrapolated_post'], 2)}", showarrow=False, font=dict(color=theme.INK_2, size=11))
    fig.add_vline(x=PACKAGING_DATE, line=dict(color=theme.AXIS, width=1, dash="dot"))
    fig.add_annotation(x=PACKAGING_DATE, y=1.0, yref="paper", text="15 Apr 2026", showarrow=False, xanchor="left",
                       xshift=4, font=dict(color=theme.MUTED, size=11))
    fig.update_yaxes(tickformat=".1%", rangemode="tozero")
    fig.update_xaxes(tickformat="%b %Y", dtick="M3", range=[x.min() - pd.Timedelta(days=15), x.max() + pd.Timedelta(days=110)])
    raw, step = sd["raw_change_pts"], sd["step_net_of_trend_pts"]
    title = (f"Non-Paid-Social Basic upgrades {'fell' if raw < 0 else 'rose'} from {pct(sd['pre_mean'], 2)} to "
             f"{pct(sd['actual_post'], 2)} of accounts a month")
    if raw < step < 0 and sd["slope_pts_per_month"] < 0:
        title += f"; {abs(step) * 100:.2f} of the {abs(raw) * 100:.2f}-point fall is beyond the downtrend already under way"
    else:
        title += f"; {pts(step, 2)} against the pre-window trend extended"
    return theme.apply(fig, title,
                       f"Voluntary Basic → higher upgrades per Basic account-month, Paid Social excluded, Wilson 95% bands; flat lines = "
                       f"pooled rates, {window_label(pre)} (before) and {window_label(post)} (after); dotted = linear trend fitted on "
                       "the before window (weighted by base size), extended", height=420)


def fig_basic_churn_monthly(start: str = "2025-01-31") -> go.Figure:
    g = basic_churn_monthly(True)
    g = g[g["month_end"] >= start]
    x = theme.month_x(g["month_end"])
    ch = basic_churn_compare()
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=pd.concat([x, x[::-1]]), y=pd.concat([g["hi"], g["lo"][::-1]]), fill="toself", mode="lines",
                             fillcolor=_rgba(theme.OTHER, 0.12), line=dict(color="rgba(0,0,0,0)"), hoverinfo="skip", showlegend=False))
    fig.add_trace(go.Scatter(x=x, y=g["rate"], mode="lines+markers", name="Basic monthly logo churn (non-Paid-Social)",
                             line=dict(color=theme.OTHER, width=2), marker=dict(size=8, color=theme.OTHER, line=dict(color=theme.SURFACE, width=2)),
                             customdata=np.c_[g["n"], g["churned"]],
                             hovertemplate="%{x|%b %Y}<br>%{y:.2%} (%{customdata[1]:,} / %{customdata[0]:,})<extra></extra>"))
    # pooled rate over each comparison window: drawn as flat lines and named in the legend (a label beside the
    # 2025 line would sit on the monthly series)
    for w, rate, c in ((CHURN_WINDOW_LY, ch["churn_a"], theme.DEEMPH), (CHURN_WINDOW_CUR, ch["churn_b"], theme.INK_2)):
        x0, x1 = theme.month_x(pd.Series([w[0]])).iloc[0], theme.month_x(pd.Series([w[1]])).iloc[0] + pd.Timedelta(days=27)
        fig.add_trace(go.Scatter(x=[x0, x1], y=[rate, rate], mode="lines", line=dict(color=c, width=2),
                                 name=f"{window_label(w)}: {pct(rate, 2)} pooled", hoverinfo="skip"))
    fig.add_vline(x=PACKAGING_DATE, line=dict(color=theme.AXIS, width=1, dash="dot"))
    fig.add_annotation(x=PACKAGING_DATE, y=1.0, yref="paper", text="15 Apr 2026", showarrow=False, xanchor="left",
                       xshift=4, font=dict(color=theme.MUTED, size=11))
    fig.update_yaxes(tickformat=".1%", rangemode="tozero")
    fig.update_xaxes(tickformat="%b %Y", dtick="M3", range=[x.min() - pd.Timedelta(days=15), x.max() + pd.Timedelta(days=15)])
    d = ch["change_pts"]
    title = (f"Basic logo churn was {abs(d) * 100:.2f} pts a month {'lower' if d < 0 else 'higher'} in {ch['window_b']} than "
             f"a year earlier (95%: {pts(ch['change_lo'], 2)} to {pts(ch['change_hi'], 2)})")
    if ch["change_lo"] < 0 < ch["change_hi"]:
        title += ", a change not distinguishable from zero"
    return theme.apply(fig, title,
                       "Customers on Basic at the previous month-end who churned in the month ÷ customers on Basic, Paid Social excluded; "
                       "Wilson 95% intervals", height=400)


def fig_downgrades_quarterly(start: str = "2024-Q1") -> go.Figure:
    d = downgrade_events()
    g = d.groupby(["quarter_label", "change_source"]).size().reset_index(name="downgrades")
    g = g[g["quarter_label"] >= start]
    quarters = sorted(g["quarter_label"].unique())
    fig = go.Figure()
    for src in ("self_serve", "renewal", "customer_success"):
        s = g[g["change_source"] == src].set_index("quarter_label")["downgrades"].reindex(quarters).fillna(0)
        fig.add_trace(go.Bar(x=quarters, y=s.values, name=SOURCE_LABELS[src],
                             marker=dict(color=SOURCE_COLORS[src], pattern=SOURCE_PATTERNS.get(src, {}),
                                         line=dict(color=theme.SURFACE, width=1)),
                             hovertemplate="%{x}<br>" + SOURCE_LABELS[src] + ": %{y} downgrades<extra></extra>"))
    fig.update_layout(barmode="stack", bargap=0.45)
    fig.update_yaxes(rangemode="tozero", title_text="Downgrades")
    last4 = g[g["quarter_label"] >= "2025-Q4"]
    return theme.apply(fig, f"{int(last4['downgrades'].sum())} plan downgrades in the last four quarters, "
                            f"{pct(last4[last4['change_source'] == 'customer_success']['downgrades'].sum() / max(last4['downgrades'].sum(), 1), 0)} of them CS saves",
                       "Plan changes to a lower tier per quarter (Premium → Basic, Enterprise → Premium), by who initiated them", height=360)


def fig_downgrade_rate(dim: str = "segment", window: tuple = (pd.Timestamp("2025-10-31"), AS_OF), from_plan: str = "premium") -> go.Figure:
    g = downgrade_rate_by(dim, window, from_plan)
    if dim == "tenure_band":
        g = g.set_index("group").reindex([x for x in TENURE_LABELS if x in set(g["group"])]).reset_index()
    else:
        g = g.sort_values("rate")
    c = PLAN_COLORS[from_plan]           # identity follows the entity: these are Premium (or Enterprise) account-months
    fig = go.Figure(go.Scatter(x=g["rate"], y=g["group"], mode="markers+text", name=f"{_label(from_plan)} downgrade rate",
                               text=[pct(v, 2) for v in g["rate"]], textposition="middle right", textfont=dict(color=theme.INK, size=11),
                               marker=dict(size=10, color=c, line=dict(color=theme.SURFACE, width=2)),
                               error_x=dict(type="data", symmetric=False, array=g["hi"] - g["rate"], arrayminus=g["rate"] - g["lo"],
                                            color=c, thickness=1, width=0),
                               customdata=np.c_[g["n"], g["downgrades"]],
                               hovertemplate="%{y}<br>%{x:.2%} per month (%{customdata[1]:,} downgrades / %{customdata[0]:,} account-months)<extra></extra>"))
    fig.update_xaxes(tickformat=".1%", showgrid=True, gridcolor=theme.GRID, rangemode="tozero")
    fig.update_yaxes(showgrid=False, automargin=True, autorange="reversed" if dim == "tenure_band" else True)
    fig.update_layout(showlegend=False, margin=dict(l=40, r=70))
    top, bot = g.sort_values("rate").iloc[-1], g.sort_values("rate").iloc[0]
    return theme.apply(fig, f"{_label(from_plan)} → lower plan, by {DIMENSION_LABELS[dim].lower()}: {top.group} downgrades most "
                            f"({pct(top.rate, 2)} per month), {bot.group} least ({pct(bot.rate, 2)})",
                       f"Downgrades per {_label(from_plan)} account-month, {window_label(window)}; whiskers = Wilson 95% intervals; "
                       "groups with ≥ 100 account-months", height=max(280, 110 + 36 * len(g)))


def fig_downgrade_survival(h: int = 6) -> go.Figure:
    s = downgrade_survival(h)
    present = set(s["change_source"])
    order = [o for o in ("customer_success", "self_serve", "renewal") if o in present]
    s = s.set_index("change_source").reindex(order).reset_index()
    labels = [f"{SOURCE_LABELS[c]} (n = {int(n)})" for c, n in zip(s["change_source"], s["downgrades"])]
    fig = go.Figure()
    # Bars wear the initiator colours of the quarterly chart above them; the x labels name the initiator, so the
    # bars stay out of the legend (one swatch could not stand for three colours) and only the benchmark is listed.
    fig.add_trace(go.Bar(x=labels, y=s["paying_share"], name=f"Downgraded accounts still paying after {h} months", showlegend=False,
                         marker=dict(color=[SOURCE_COLORS[c] for c in s["change_source"]],
                                     pattern=dict(shape=[SOURCE_PATTERNS.get(c, {}).get("shape", "") for c in s["change_source"]],
                                                  fgcolor=theme.MUTED, size=8, solidity=0.22)),
                         error_y=dict(type="data", symmetric=False, array=s["hi"] - s["paying_share"], arrayminus=s["paying_share"] - s["lo"],
                                      color=theme.INK_2, thickness=1.5, width=6),
                         text=[pct(v, 0) for v in s["paying_share"]], textposition="inside", insidetextanchor="start",
                         textfont=dict(color=[SOURCE_TEXT[c] for c in s["change_source"]]),
                         hovertemplate="%{x}<br>%{y:.1%} still paying (95%: %{customdata[0]:.0%} – %{customdata[1]:.0%})<extra></extra>",
                         customdata=np.c_[s["lo"], s["hi"]]))
    fig.add_trace(go.Scatter(x=labels, y=s["comparable_paying"], mode="markers", name="Comparable accounts that did not downgrade",
                             marker=dict(symbol="diamond", size=12, color=theme.INK, line=dict(color=theme.SURFACE, width=2)),
                             hovertemplate="%{x}<br>comparable accounts %{y:.1%} still paying<extra></extra>"))
    fig.update_yaxes(tickformat=".0%", range=[0, 1.08], title_text=f"Still paying {h} months later")
    fig.update_layout(bargap=0.5)
    cs = s[s["change_source"] == "customer_success"].iloc[0]
    vs = ("in line with" if cs["lo"] <= cs["comparable_paying"] <= cs["hi"] else
          "below" if cs["comparable_paying"] > cs["hi"] else "above")          # the benchmark inside / outside the saves' interval
    return theme.apply(fig, f"{pct(cs['paying_share'], 0)} of CS-saved accounts are still paying {h} months later (95%: {pct(cs['lo'], 0)}–{pct(cs['hi'], 0)}), "
                            f"{vs} the {pct(cs['comparable_paying'], 0)} of comparable accounts that never asked to leave",
                       f"Share of downgraded accounts still paying {h} months after the downgrade, by who initiated it; whiskers = Wilson "
                       "95% intervals; diamonds = accounts on the same plan and segment in the same month that did not downgrade — a "
                       "benchmark, not a counterfactual (saved accounts had asked to cancel)", height=380)


def fig_time_to_upgrade(by: str = "acquisition_channel", max_k: int = 24) -> go.Figure:
    t = time_to_upgrade(by, max_k)
    groups = t.groupby("group")["cif_upgrade"].max().sort_values(ascending=False).index.tolist()
    n_of = t.groupby("group")["customers"].first()
    by_channel = by == "acquisition_channel" and PAID_SOCIAL in groups
    if by == "segment":
        palette = {g: theme.SEGMENT_COLORS.get(g, theme.INK_2) for g in groups}
    elif by_channel:   # Paid Social against everyone else: one colour for the comparison channels, named at the line ends
        palette = {g: theme.PAID_SOCIAL if g == PAID_SOCIAL else theme.OTHER for g in groups}
    else:
        palette = {g: theme.SERIES[[0, 2, 3, 5, 7][i % 5]] for i, g in enumerate(groups)}   # no orange (Technology) / pink (Paid Social)
    fig = go.Figure()
    other_shown = False
    for g in groups:
        s = t[t["group"] == g]
        c = palette[g]
        last = s.iloc[-1]
        if by_channel and g != PAID_SOCIAL:
            name, legendgroup = f"Other channels (n = {int(n_of.drop(PAID_SOCIAL).sum()):,})", "other"
            showlegend, other_shown = not other_shown, True
        else:
            name, legendgroup, showlegend = f"{g} (n = {int(last['customers']):,})", g, True
        fig.add_trace(go.Scatter(x=np.r_[0, s["k"]], y=np.r_[0, s["cif_upgrade"]], mode="lines", name=name, legendgroup=legendgroup,
                                 showlegend=showlegend, line=dict(color=c, width=2, shape="hv"),
                                 customdata=np.c_[np.r_[0, s["cif_churn"]], np.r_[int(last["customers"]), s["at_risk"]]],
                                 hovertemplate=g + "<br>month %{x}: %{y:.1%} upgraded, %{customdata[0]:.1%} churned<br>%{customdata[1]:,} still at risk<extra></extra>"))
    fig.add_vline(x=12, line=dict(color=theme.AXIS, width=1, dash="dot"))
    ymax = float(t["cif_upgrade"].max()) * 1.08
    fig.update_xaxes(title_text="Months since first paid month-end", dtick=3, range=[0, max_k + 0.5])
    fig.update_yaxes(tickformat=".0%", range=[0, ymax])
    fig.update_layout(margin=dict(r=128, b=56))
    k12 = t[t["k"] == 12].set_index("group")["cif_upgrade"]
    if by_channel:
        rest = k12.drop(PAID_SOCIAL)
        title = (f"By month 12, {pct(k12[PAID_SOCIAL], 0)} of Paid Social Basic signups have upgraded vs "
                 f"{pct(rest.min(), 0)}–{pct(rest.max(), 0)} in the other channels")
    else:
        top, bot = k12.idxmax(), k12.idxmin()
        title = f"By month 12, {pct(k12[top], 0)} of {top} Basic signups have upgraded vs {pct(k12[bot], 0)} of {bot}"
    fig = theme.apply(fig, title, "Cumulative share of Basic signups with a first voluntary upgrade, by months since the first paid "
                                  "month-end; churn is a competing event (Aalen-Johansen); signups with less history are censored "
                                  "at the as-of date", height=420)
    # name every line at its end (identity never by colour alone); nudge labels apart so none overlap
    plot_h = fig.layout.height - fig.layout.margin.t - fig.layout.margin.b
    gap = 15 * ymax / plot_h
    ends = sorted(((g, float(t[t["group"] == g]["cif_upgrade"].iloc[-1]), int(t[t["group"] == g]["k"].iloc[-1])) for g in groups),
                  key=lambda e: -e[1])
    placed = []
    for g, y, k in ends:
        placed.append((g, min(y, placed[-1][1] - gap) if placed else y, k))
    shift = max(0.0, gap / 2 - placed[-1][1])
    for g, y, k in placed:
        fig.add_annotation(x=k, y=y + shift, text=g, showarrow=False, xanchor="left", xshift=6, yanchor="middle",
                           font=dict(color=theme.INK_2, size=11))
    return fig
