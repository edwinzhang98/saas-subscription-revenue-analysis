"""Silent churn: paying accounts that shrink without cancelling.

Answers scope questions Q3 and Q4 (02_metric_definitions.md §9 and §9.1):
  * how many accounts are quietly contracting, how much ARR is still at risk, how much is already lost;
  * who they are (over-representation vs other paying accounts) and what the contraction looked like;
  * what happens to them next (6-month churn vs comparable accounts, from history);
  * an early-warning rule back-tested at the (account, month-end) grain, with the operating point
    chosen by an explicit criterion (precision >= 2x base rate, CSM volume within capacity, max recall);
  * the CS watchlist (02 §15, list_type = 'risk'): reason codes, evidence, ARR at stake, priority.

The §9 definition is re-implemented here in pandas with parameters (decline threshold, lookback) so the
dashboard can show the sensitivity; `verify_definition()` checks that 20% / 6 months reproduces the SQL
table `rpt_silent_contraction` row for row. Everything reads the parquet marts only.
"""
from __future__ import annotations

from functools import lru_cache

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from .. import marts, theme
from ..config import get_settings
from ..theme import money, pct

TOPIC = "silent_churn"
AS_OF = pd.Timestamp("2026-09-30")

# §9 defaults (the SQL definition); the dashboard varies them as a sensitivity
DECLINE_THRESHOLD = 0.20
LOOKBACK_MONTHS = 6
# §9.1 early-warning rule: defaults = the operating point chosen in build() (see choose_operating_point)
UTIL_THRESHOLD = 0.50
WAU_TREND_THRESHOLD: float | None = None          # None = utilization-only rule
DOC_DEFAULT_RULE = (0.60, -0.15)                  # the v1.0 draft in 02 §9.1, kept for comparison
WAU_DOWN_CODE = -0.15                             # reason-code threshold for "WAU is falling" (context only)
WAU_UP_CODE = 0.20                                # reason-code threshold for "usage is growing into the seats" (context only)
OUTCOME_DROP = 0.10                               # outcome = billing-neutral MRR down >= 10% or churn (02 §9.1)
FOLLOWUP_MONTHS = 6
HISTORY_START, HISTORY_END = pd.Timestamp("2025-01-31"), pd.Timestamp("2026-03-31")   # complete 6-month windows
BACKTEST_START, BACKTEST_END = pd.Timestamp("2025-07-31"), pd.Timestamp("2026-06-30")  # flags-per-month window
BACKTEST_QUARTERS = ["2025-Q3", "2025-Q4", "2026-Q1", "2026-Q2"]
# per-quarter stability check of the chosen rule: the quarters with >= 1,500 eligible rows and complete windows for both
# billing periods (Q3 2026 only has annual rows whose renewal fell before the as-of date)
STABILITY_QUARTERS = ["2024-Q3", "2024-Q4", "2025-Q1", "2025-Q2", "2025-Q3", "2025-Q4", "2026-Q1", "2026-Q2"]
N_BOOT_RULE = 1000                                # account-level bootstrap resamples for the rule's precision / recall intervals
CSM_COVERAGE = ("named_csm", "pooled_csm")
CSM_CAPACITY_PER_QUARTER = 120                    # assumption A9
RENEWAL_SOON_DAYS = 120
URGENT_RENEWAL_DAYS = 45                          # priority tier 1: annual renewals this close are worked by date, soonest first
UTIL_GRID = [round(x, 2) for x in np.arange(0.40, 0.801, 0.05)]
TREND_GRID = [None] + [round(x, 2) for x in np.arange(0.0, -0.401, -0.05)]

CLASS_LABELS = {"silent": "Silent", "saved_re_risk": "Saved by CS (re-risk)"}
CLASS_COLORS = {"silent": theme.ACCENT, "saved_re_risk": theme.SAVED}
PROFILE_DIMS = {
    "segment": "Segment", "industry": "Industry", "acquisition_channel": "Acquisition channel", "plan_id": "Plan",
    "billing_period": "Billing period", "tenure_band": "Tenure (months)", "cs_coverage": "CS coverage",
    "utilization_band": "Seat utilization, 13 weeks", "wau_trend_band": "WAU trend, last 4 vs prior 9 weeks",
}
KIND_ORDER = ["Seat cut at a monthly billing date", "Seat right-sizing at renewal", "Plan downgrade (self-serve)",
              "Plan downgrade at renewal", "CS save offer", "Price concession", "Other"]
BAND_ORDER = {
    "tenure_band": ["0-6", "7-12", "13-24", "25+"],
    "utilization_band": ["under 40%", "40-60%", "60-80%", "80% and above", "no 13-week history"],
    "wau_trend_band": ["down 30%+", "down 15-30%", "down 0-15%", "up 0-15%", "up 15%+", "no 13-week history"],
}
REASON_CODES = {
    "SPEND_DOWN_20PCT_6M": "billing-neutral MRR is at least 20% below its peak of the previous 6 month-ends, after a seat, "
                           "plan or discount contraction (the §9 silent-contraction flag)",
    "SEATS_CUT_20PCT_6M": "seats are at least 20% below the peak seat count of the previous 6 month-ends",
    "DOWNGRADED_6M": "the plan tier was lowered in the last 6 months",
    "RENEWAL_RIGHTSIZED_6M": "the contraction was taken at an annual renewal (seats or plan)",
    "SAVED_BY_CS_6M": "the contraction was a CS save offer: the account asked to cancel and was kept on smaller terms",
    "UTIL_BELOW_50": "seat utilization over the last 13 complete weeks is below 50% (the early-warning rule)",
    "WAU_DOWN_15PCT": "weekly active users in the last 4 weeks are at least 15% below the previous 9 weeks (context)",
    "WAU_UP_20PCT": "weekly active users in the last 4 weeks are at least 20% above the previous 9 weeks — usage is growing into "
                    "the seats (context; with an early-warning flag the play is a seat-count conversation, not an idle-seat save)",
    "RENEWAL_WITHIN_120D": "the annual term ends within 120 days",
}
CODE_PRIORITY = ["SAVED_BY_CS_6M", "DOWNGRADED_6M", "SEATS_CUT_20PCT_6M", "RENEWAL_RIGHTSIZED_6M", "SPEND_DOWN_20PCT_6M",
                 "UTIL_BELOW_50", "WAU_DOWN_15PCT", "WAU_UP_20PCT", "RENEWAL_WITHIN_120D"]


# =============================================================================== helpers
def wilson(p: float, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson interval for a share."""
    if n == 0 or np.isnan(p):
        return (np.nan, np.nan)
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (float(c - h), float(c + h))


def _trailing_max(a: np.ndarray, lookback: int) -> np.ndarray:
    """Max over the previous `lookback` columns (t-L .. t-1), NaN where nothing precedes."""
    out = np.full_like(a, np.nan)
    filled = np.where(np.isnan(a), -np.inf, a)
    for j in range(1, a.shape[1]):
        out[:, j] = filled[:, max(0, j - lookback):j].max(axis=1)
    out[np.isinf(out)] = np.nan
    return out


def _trailing_sum(ind: np.ndarray, lookback: int) -> np.ndarray:
    """Sum over columns t-L+1 .. t (L columns including t)."""
    cs = np.cumsum(ind, axis=1)
    out = cs.copy()
    out[:, lookback:] = cs[:, lookback:] - cs[:, :-lookback]
    return out


def _last_index(ind: np.ndarray) -> np.ndarray:
    """Column index of the last positive entry at or before each column (-1 if none)."""
    idx = np.where(ind > 0, np.arange(ind.shape[1])[None, :], -1)
    return np.maximum.accumulate(idx, axis=1)


def _month_idx(s: pd.Series) -> pd.Series:
    return s.dt.year * 12 + s.dt.month


def _bands(df: pd.DataFrame) -> pd.DataFrame:
    """Tenure, utilization and WAU-trend bands (accounts without a full 13-week window get their own band)."""
    def cut(s, bins, labels):
        c = pd.cut(s, bins, labels=labels).cat.add_categories(["no 13-week history"])
        return c.fillna("no 13-week history").astype(object).astype(str)
    df = df.copy()
    df["tenure_band"] = cut(df["tenure_months"], [-1, 6, 12, 24, 999], BAND_ORDER["tenure_band"])
    df["utilization_band"] = cut(df["utilization_13w"], [-0.01, 0.4, 0.6, 0.8, 99], BAND_ORDER["utilization_band"][:-1])
    df["wau_trend_band"] = cut(df["wau_trend"], [-9, -0.3, -0.15, 0, 0.15, 999], BAND_ORDER["wau_trend_band"][:-1])
    return df


# =============================================================================== §9 in pandas
@lru_cache(maxsize=1)
def _panel() -> dict:
    """Customer x month-end matrices used by the §9 definition (built once per process)."""
    m = marts.load("fct_customer_mrr_monthly")
    norm = m.pivot(index="customer_id", columns="month_end", values="mrr_norm")
    customers, months = norm.index, list(norm.columns)

    def mat(col: str) -> np.ndarray:
        return (m.pivot(index="customer_id", columns="month_end", values=col)
                 .reindex(index=customers, columns=months).values.astype(float))

    comp = marts.load("fct_mrr_movement_components")
    c = comp[comp["movement_type"] == "contraction"]
    real = (c["comp_plan"].clip(upper=0) + c["comp_seats"].clip(upper=0) + c["comp_discount"].clip(upper=0)) < 0
    steps = c[real]

    def indicator(df: pd.DataFrame) -> np.ndarray:
        if df.empty:
            return np.zeros((len(customers), len(months)))
        return (df.assign(v=1.0).pivot_table(index="customer_id", columns="month_end", values="v", aggfunc="max")
                  .reindex(index=customers, columns=months).fillna(0.0).values)

    feat = marts.load("fct_account_month_features")
    mrr = mat("mrr")
    return {
        "customers": customers, "months": months, "month_pos": {mth: i for i, mth in enumerate(months)},
        "norm": norm.values.astype(float), "mrr": mrr, "seats": mat("seats"),
        "paying": np.nan_to_num(mrr, nan=0.0) > 0,
        "steps": indicator(steps), "saves": indicator(steps[steps["has_cs_save"]]),
        "downgrades": indicator(steps[steps["comp_plan"] < 0]), "renewals": indicator(steps[steps["has_renewal_change"]]),
        "cancel_open": indicator(feat[feat["cancel_requested_open"]]),
    }


def silent_flags(threshold: float = DECLINE_THRESHOLD, lookback: int = LOOKBACK_MONTHS) -> pd.DataFrame:
    """02 §9 at every month-end: paying, no open cancellation, billing-neutral MRR <= (1 - threshold) x the
    max of the previous `lookback` month-ends, and >= 1 contraction with a negative plan / seats / discount
    part in the last `lookback` months. Class 'saved_re_risk' when any such contraction was a CS save."""
    p = _panel()
    peak_norm = _trailing_max(p["norm"], lookback)
    peak_mrr = _trailing_max(p["mrr"], lookback)
    peak_seats = _trailing_max(p["seats"], lookback)
    steps = _trailing_sum(p["steps"], lookback)
    with np.errstate(invalid="ignore"):
        flag = (p["paying"] & (peak_norm > 0) & (p["norm"] <= (1 - threshold) * peak_norm)
                & (steps >= 1) & (p["cancel_open"] == 0))
    ci, mj = np.nonzero(flag)
    last_step = _last_index(p["steps"])
    out = pd.DataFrame({
        "customer_id": p["customers"][ci], "month_end": [p["months"][j] for j in mj],
        "peak_mrr_norm": peak_norm[ci, mj], "peak_mrr": peak_mrr[ci, mj], "peak_seats": peak_seats[ci, mj],
        "contraction_steps": steps[ci, mj].astype(int),
        "months_since_last_contraction": (mj - last_step[ci, mj]).astype(int),
        "any_cs_save": _trailing_sum(p["saves"], lookback)[ci, mj] > 0,
        "any_plan_downgrade": _trailing_sum(p["downgrades"], lookback)[ci, mj] > 0,
        "any_renewal_contraction": _trailing_sum(p["renewals"], lookback)[ci, mj] > 0,
    })
    m = marts.load("fct_customer_mrr_monthly")[[
        "customer_id", "month_end", "mrr", "mrr_norm", "seats", "plan_id", "tier_rank", "billing_period", "segment",
        "industry", "acquisition_channel", "region", "cs_coverage", "months_since_first_paid"]]
    out = out.merge(m, on=["customer_id", "month_end"], how="left")
    out["current_arr"] = 12 * out["mrr"]
    out["lost_arr"] = 12 * (out["peak_mrr"] - out["mrr"]).clip(lower=0)
    out["decline_pct_norm"] = 1 - out["mrr_norm"] / out["peak_mrr_norm"]
    out["seat_cut_pct"] = 1 - out["seats"] / out["peak_seats"]
    out["contraction_class"] = np.where(out["any_cs_save"], "saved_re_risk", "silent")
    out["threshold"], out["lookback"] = threshold, lookback
    return out.sort_values(["month_end", "customer_id"]).reset_index(drop=True)


def verify_definition() -> dict:
    """The pandas implementation at 20% / 6 months must reproduce sql/50_rpt_silent_contraction.sql exactly."""
    sql = marts.load("rpt_silent_contraction")
    mine = silent_flags(0.20, 6)
    key = ["customer_id", "month_end"]
    mg = mine.merge(sql[key + ["contraction_class", "decline_pct_norm", "contraction_steps_6m", "lost_arr", "current_arr"]],
                    on=key, how="outer", suffixes=("", "_sql"), indicator=True)
    both = mg[mg["_merge"] == "both"]
    res = {
        "sql_rows": int(len(sql)), "pandas_rows": int(len(mine)),
        "rows_only_in_sql": int((mg["_merge"] == "right_only").sum()),
        "rows_only_in_pandas": int((mg["_merge"] == "left_only").sum()),
        "class_mismatches": int((both["contraction_class"] != both["contraction_class_sql"]).sum()),
        "step_mismatches": int((both["contraction_steps"] != both["contraction_steps_6m"]).sum()),
        "max_abs_decline_diff": float((both["decline_pct_norm"] - both["decline_pct_norm_sql"]).abs().max()),
        "max_abs_lost_arr_diff": float((both["lost_arr"] - both["lost_arr_sql"]).abs().max()),
    }
    res["exact_match"] = bool(res["rows_only_in_sql"] == 0 and res["rows_only_in_pandas"] == 0 and res["class_mismatches"] == 0
                              and res["step_mismatches"] == 0 and res["max_abs_decline_diff"] < 1e-9
                              and res["max_abs_lost_arr_diff"] < 0.005)
    return res


# =============================================================================== size, profile, mix
def flag_trend(flags: pd.DataFrame, start: str = "2024-10-31") -> pd.DataFrame:
    """Flagged accounts, ARR at risk and lost ARR per month-end and class (plus the Technology count)."""
    f = flags[flags["month_end"] >= start]
    g = f.groupby(["month_end", "contraction_class"], as_index=False).agg(
        accounts=("customer_id", "size"), current_arr=("current_arr", "sum"), lost_arr=("lost_arr", "sum"),
        technology_accounts=("industry", lambda s: int((s == "Technology").sum())))
    return g.sort_values(["month_end", "contraction_class"]).reset_index(drop=True)


def flag_rate_by_industry_group(flags: pd.DataFrame, start: str = "2025-01-31") -> pd.DataFrame:
    """Share of paying accounts flagged, Technology vs every other industry, by month-end."""
    feat = marts.load("fct_account_month_features")[["customer_id", "month_end", "industry"]]
    feat = feat[feat["month_end"] >= start].copy()
    feat["group"] = np.where(feat["industry"] == "Technology", "Technology", "All other industries")
    keys = flags[["customer_id", "month_end"]].drop_duplicates().assign(flagged=True)
    feat = feat.merge(keys, on=["customer_id", "month_end"], how="left")
    feat["flagged"] = feat["flagged"].fillna(False).astype(bool)
    g = feat.groupby(["month_end", "group"], as_index=False).agg(paying=("customer_id", "size"), flagged=("flagged", "sum"))
    g["rate"] = g["flagged"] / g["paying"]
    return g


def profile(flags: pd.DataFrame, month_end: pd.Timestamp = AS_OF) -> pd.DataFrame:
    """Over-representation of flagged accounts vs the other paying accounts at `month_end`, per dimension.
    index = share among flagged / share among other paying accounts; flag_rate = flagged / all in the group."""
    base = _bands(marts.load("fct_account_month_features"))
    base = base[base["month_end"] == month_end].copy()
    base["flagged"] = base["customer_id"].isin(flags.loc[flags["month_end"] == month_end, "customer_id"])
    rows = []
    for col, label in PROFILE_DIMS.items():
        t = pd.crosstab(base[col], base["flagged"]).reindex(columns=[False, True], fill_value=0)
        t.columns = ["other", "flagged"]
        t = t.reset_index().rename(columns={col: "value"})
        t["dimension"], t["dimension_label"] = col, label
        t["share_flagged"] = t["flagged"] / max(t["flagged"].sum(), 1)
        t["share_other"] = t["other"] / max(t["other"].sum(), 1)
        t["index"] = t["share_flagged"] / t["share_other"].replace(0, np.nan)
        t["flag_rate"] = t["flagged"] / (t["flagged"] + t["other"]).replace(0, np.nan)
        t["value"] = t["value"].astype(str)
        if col in BAND_ORDER:
            t = t.sort_values("value", key=lambda s: s.map({v: i for i, v in enumerate(BAND_ORDER[col])}).fillna(99))
        else:
            t = t.sort_values("index", ascending=False)
        rows.append(t)
    out = pd.concat(rows, ignore_index=True)
    return out[["dimension", "dimension_label", "value", "flagged", "other", "share_flagged", "share_other", "index", "flag_rate"]]


def _contraction_rows(customer_ids, month_end: pd.Timestamp, lookback: int) -> pd.DataFrame:
    """Negative plan-change rows of the given customers inside the lookback window, classified."""
    cc = marts.load("fct_change_components")
    lo = month_end - pd.offsets.MonthEnd(lookback)
    w = cc[cc["customer_id"].isin(customer_ids) & (cc["month_end"] > lo) & (cc["month_end"] <= month_end)
           & (cc["delta_mrr"] < 0)].copy()
    kind = np.select(
        [(w["comp_plan"] < 0) & (w["change_source"] == "customer_success"),
         (w["comp_plan"] < 0) & (w["change_source"] == "renewal"),
         (w["comp_plan"] < 0),
         (w["comp_seats"] < 0) & (w["change_source"] == "renewal"),
         (w["comp_seats"] < 0),
         (w["comp_discount"] < 0)],
        ["CS save offer", "Plan downgrade at renewal", "Plan downgrade (self-serve)", "Seat right-sizing at renewal",
         "Seat cut at a monthly billing date", "Price concession"], default="Other")
    w["kind"] = kind
    return w


def contraction_mix(flags: pd.DataFrame, month_end: pd.Timestamp = AS_OF, lookback: int = LOOKBACK_MONTHS) -> pd.DataFrame:
    """What the contraction looked like for the accounts flagged at `month_end`: each account is assigned the
    kind that cost the most MRR in the window; counts split Technology vs other industries."""
    cur = flags[flags["month_end"] == month_end]
    w = _contraction_rows(cur["customer_id"], month_end, lookback)
    per = w.groupby(["customer_id", "kind"], as_index=False)["delta_mrr"].sum()
    dom = per.sort_values("delta_mrr").drop_duplicates("customer_id")   # most negative first
    dom = dom.merge(cur[["customer_id", "industry"]], on="customer_id")
    dom["group"] = np.where(dom["industry"] == "Technology", "Technology", "All other industries")
    g = dom.groupby(["kind", "group"], as_index=False).agg(accounts=("customer_id", "size"), mrr_lost=("delta_mrr", lambda s: -s.sum()))
    g["kind"] = pd.Categorical(g["kind"], KIND_ORDER)
    return g.sort_values(["kind", "group"]).reset_index(drop=True)


# =============================================================================== what happens next
@lru_cache(maxsize=1)
def _followup_pool() -> pd.DataFrame:
    """Every paying account-month t in the history window (no open cancellation) with its 6-month outcomes:
    churn_6m = MRR 0 at any of t+1..t+6; loss_6m = churn or billing-neutral MRR <= 90% of MRR_t at any of them."""
    p = _panel()
    feat = marts.load("fct_account_month_features")[["customer_id", "month_end", "segment", "industry", "billing_period",
                                                     "tenure_months", "cancel_requested_open", "arr"]]
    pool = feat[(feat["month_end"] >= HISTORY_START) & (feat["month_end"] <= HISTORY_END) & (~feat["cancel_requested_open"])].copy()
    ci = p["customers"].get_indexer(pool["customer_id"])
    ti = pool["month_end"].map(p["month_pos"]).values
    mrr, norm = np.nan_to_num(p["mrr"], nan=0.0), np.nan_to_num(p["norm"], nan=0.0)
    fut_mrr = np.stack([mrr[ci, ti + k] for k in range(1, FOLLOWUP_MONTHS + 1)], axis=1)
    fut_norm = np.stack([norm[ci, ti + k] for k in range(1, FOLLOWUP_MONTHS + 1)], axis=1)
    pool["churn_6m"] = (fut_mrr == 0).any(axis=1)
    pool["loss_6m"] = (fut_norm <= (1 - OUTCOME_DROP) * norm[ci, ti][:, None]).any(axis=1)
    pool["tenure_band"] = pd.cut(pool["tenure_months"], [-1, 6, 12, 24, 999], labels=["0-6", "7-12", "13-24", "25+"]).astype(str)
    return pool.reset_index(drop=True)


def followup(flags: pd.DataFrame) -> pd.DataFrame:
    """What happens in the 6 months after an account is newly flagged (first month-end of each flag run,
    t in the history window): churn and churn-or-further-contraction, by class, vs (a) every unflagged
    paying account-month and (b) the same standardised to the flagged mix of segment x billing x tenure."""
    pool = _followup_pool()
    fl = flags.sort_values(["customer_id", "month_end"]).copy()
    fl["mi"] = _month_idx(fl["month_end"])
    fl["new_run"] = fl.groupby("customer_id")["mi"].diff().ne(1)
    ev = fl[fl["new_run"] & (fl["month_end"] >= HISTORY_START) & (fl["month_end"] <= HISTORY_END)]
    ev = ev[["customer_id", "month_end", "contraction_class"]].merge(pool, on=["customer_id", "month_end"], how="inner")
    unfl = pool.merge(flags[["customer_id", "month_end"]].drop_duplicates().assign(_f=True), on=["customer_id", "month_end"], how="left")
    unfl = unfl[unfl["_f"].isna()]
    strata = ["segment", "billing_period", "tenure_band"]
    wts = ev.groupby(strata).size().rename("w")
    rates = unfl.groupby(strata)[["churn_6m", "loss_6m"]].mean().join(wts, how="inner")
    rows = []
    for cls, g in ev.groupby("contraction_class"):
        rows.append({"group": cls, "label": f"{CLASS_LABELS[cls]} (newly flagged)", "n": len(g),
                     "churn_6m": g["churn_6m"].mean(), "loss_6m": g["loss_6m"].mean()})
    rows.append({"group": "unflagged_matched", "label": "Unflagged, same segment / billing / tenure mix", "n": len(unfl),
                 "churn_6m": float((rates["churn_6m"] * rates["w"]).sum() / rates["w"].sum()),
                 "loss_6m": float((rates["loss_6m"] * rates["w"]).sum() / rates["w"].sum())})
    rows.append({"group": "unflagged_raw", "label": "Unflagged, all paying accounts", "n": len(unfl),
                 "churn_6m": unfl["churn_6m"].mean(), "loss_6m": unfl["loss_6m"].mean()})
    out = pd.DataFrame(rows)
    for c in ("churn_6m", "loss_6m"):
        ci = [wilson(p_, n_) for p_, n_ in zip(out[c], out["n"])]
        out[f"{c}_lo"], out[f"{c}_hi"] = [a for a, _ in ci], [b for _, b in ci]
    order = {"silent": 0, "saved_re_risk": 1, "unflagged_matched": 2, "unflagged_raw": 3}
    return out.sort_values("group", key=lambda s: s.map(order)).reset_index(drop=True)


# =============================================================================== §9.1 back-test
@lru_cache(maxsize=1)
def backtest_rows() -> pd.DataFrame:
    """Eligible (account, month-end) rows for the early-warning back-test (02 §9.1): paying, no open
    cancellation, no Contraction in t-2..t, outcome window fully observed, and 13 complete weeks of usage
    (the rule needs the full window). outcome_month = first month-end in the window where billing-neutral MRR
    is <= 90% of MRR_t (churn counts)."""
    p = _panel()
    f = marts.load("fct_account_month_features")
    el = f[(~f["cancel_requested_open"]) & (f["contraction_months_3m"] == 0) & f["label_window_complete"]
           & (f["weeks_13w"] == 13)].copy()
    el["billing_period"] = el["current_period_type"].fillna(el["billing_period"])
    el["outcome"] = el["label_contraction10_or_churn"].astype(bool)
    norm = np.nan_to_num(p["norm"], nan=0.0)
    pos = el[el["outcome"]]
    ci = p["customers"].get_indexer(pos["customer_id"])
    ti = pos["month_end"].map(p["month_pos"]).values
    wi = pos["label_window_end"].map(p["month_pos"]).values
    om = []
    for c, t, w, thr in zip(ci, ti, wi, (1 - OUTCOME_DROP) * pos["mrr_norm"].values):
        hit = np.nonzero(norm[c, t + 1:w + 1] <= thr)[0]
        om.append(p["months"][t + 1 + hit[0]])
    el["outcome_month"] = pd.NaT
    el.loc[pos.index, "outcome_month"] = pd.to_datetime(om)
    el["outcome_month"] = pd.to_datetime(el["outcome_month"])
    el["month_idx"] = _month_idx(el["month_end"])
    el["outcome_idx"] = _month_idx(el["outcome_month"]).astype("Int64")
    el["is_csm"] = el["cs_coverage"].isin(CSM_COVERAGE)
    cols = ["customer_id", "month_end", "quarter_label", "month_idx", "billing_period", "cs_coverage", "is_csm", "segment",
            "industry", "plan_id", "arr", "utilization_13w", "wau_trend", "outcome", "outcome_month", "outcome_idx",
            "label_churn_3m"]
    return el[cols].sort_values(["customer_id", "month_end"]).reset_index(drop=True)


def rule_flag(df: pd.DataFrame, util_thr: float, trend_thr: float | None) -> pd.Series:
    flag = df["utilization_13w"] < util_thr
    if trend_thr is not None:
        flag &= df["wau_trend"] <= trend_thr
    return flag.fillna(False)


def lead_times(rows: pd.DataFrame, util_thr: float, trend_thr: float | None) -> pd.DataFrame:
    """One row per caught outcome event (customer, outcome month): the uninterrupted run of flagged month-ends that
    ends closest to the outcome; lead = month-ends from that run's first flag to the outcome month (02 §9.1). An
    event preceded by two separate runs (flag, gap, flag again) counts once, with the later run — the outcome
    window of the earlier run's last row also contains the event, so without this step it would count twice."""
    fl = rows.loc[rule_flag(rows, util_thr, trend_thr), ["customer_id", "month_idx", "outcome", "outcome_idx",
                                                            "outcome_month", "billing_period", "is_csm"]]
    fl = fl.sort_values(["customer_id", "month_idx"])
    fl["run"] = fl.groupby("customer_id")["month_idx"].diff().ne(1).cumsum()
    runs = fl.groupby("run").agg(customer_id=("customer_id", "first"), first_idx=("month_idx", "min"), last_idx=("month_idx", "max"),
                                 billing_period=("billing_period", "last"), is_csm=("is_csm", "last"),
                                 last_outcome=("outcome", "last"), outcome_idx=("outcome_idx", "last"),
                                 outcome_month=("outcome_month", "last"))
    caught = runs[runs["last_outcome"]].copy()
    caught = (caught.sort_values(["customer_id", "outcome_idx", "last_idx"])
                    .drop_duplicates(["customer_id", "outcome_idx"], keep="last"))   # one run per event: the one ending closest to it
    caught["lead"] = (caught["outcome_idx"] - caught["first_idx"]).astype(int)
    caught["run_length"] = (caught["last_idx"] - caught["first_idx"] + 1).astype(int)
    return caught.reset_index(drop=True)


def evaluate_rule(rows: pd.DataFrame, util_thr: float, trend_thr: float | None) -> dict:
    """Precision / recall / lift at the (account, month-end) grain, event- and account-level recall, flag volume
    vs CS capacity, and the lead-time distribution, for one threshold pair."""
    flag = rule_flag(rows, util_thr, trend_thr)
    y = rows["outcome"]
    n_flag, tp, n_pos = int(flag.sum()), int((flag & y).sum()), int(y.sum())
    base = float(y.mean())
    prec = tp / n_flag if n_flag else np.nan
    rec = tp / n_pos if n_pos else np.nan
    win = rows["month_end"].between(BACKTEST_START, BACKTEST_END)
    months = rows.loc[win, "month_end"].nunique()
    fpm = int((flag & win).sum()) / max(months, 1)
    csm_fpm = int((flag & win & rows["is_csm"]).sum()) / max(months, 1)
    q = rows[flag & rows["quarter_label"].isin(BACKTEST_QUARTERS)]
    acc_q = q.groupby("quarter_label")["customer_id"].nunique().reindex(BACKTEST_QUARTERS).fillna(0).mean()
    csm_q = q[q["is_csm"]].groupby("quarter_label")["customer_id"].nunique().reindex(BACKTEST_QUARTERS).fillna(0).mean()
    events = rows.loc[y, ["customer_id", "outcome_idx"]].drop_duplicates()
    caught = lead_times(rows, util_thr, trend_thr)
    return {
        "utilization_threshold": util_thr, "wau_trend_threshold": trend_thr,
        "eligible_rows": int(len(rows)), "positives": n_pos, "base_rate": base,
        "flagged_rows": n_flag, "true_positives": tp, "precision": prec, "recall": rec,
        "lift": prec / base if base else np.nan,
        "flags_per_month": float(fpm), "csm_flags_per_month": float(csm_fpm),
        "flagged_accounts_per_quarter": float(acc_q), "csm_accounts_per_quarter": float(csm_q),
        "events": int(len(events)), "events_caught": int(len(caught)),
        "event_recall": len(caught) / len(events) if len(events) else np.nan,
        "account_recall": caught["customer_id"].nunique() / events["customer_id"].nunique() if len(events) else np.nan,
        "lead_median": float(caught["lead"].median()) if len(caught) else np.nan,
        "lead_p25": float(caught["lead"].quantile(0.25)) if len(caught) else np.nan,
        "lead_p75": float(caught["lead"].quantile(0.75)) if len(caught) else np.nan,
        "share_lead_ge2": float((caught["lead"] >= 2).mean()) if len(caught) else np.nan,
    }


def rule_by_quarter(rows: pd.DataFrame, util_thr: float, trend_thr: float | None,
                    quarters: list[str] | None = None) -> pd.DataFrame:
    """Precision, recall, base rate and volume of one threshold pair in each calendar quarter of the back-test (row
    grain). The operating point is chosen and scored on the same rows, which flatters the best cell of the grid;
    this is the check that the pooled figures hold quarter by quarter."""
    quarters = STABILITY_QUARTERS if quarters is None else quarters
    d = rows[rows["quarter_label"].isin(quarters)].copy()
    d["flag"] = rule_flag(d, util_thr, trend_thr)
    d["tp"] = d["flag"] & d["outcome"]
    d["csm_flag_id"] = d["customer_id"].where(d["flag"] & d["is_csm"])
    g = d.groupby("quarter_label").agg(eligible_rows=("outcome", "size"), positives=("outcome", "sum"), flagged_rows=("flag", "sum"),
                                       true_positives=("tp", "sum"), csm_accounts=("csm_flag_id", "nunique"))
    g = g.reindex(quarters).reset_index()
    g["base_rate"] = g["positives"] / g["eligible_rows"]
    g["precision"] = g["true_positives"] / g["flagged_rows"].replace(0, np.nan)
    g["recall"] = g["true_positives"] / g["positives"].replace(0, np.nan)
    g["lift"] = g["precision"] / g["base_rate"]
    g["utilization_threshold"], g["wau_trend_threshold"] = util_thr, trend_thr
    return g


def bootstrap_rule(rows: pd.DataFrame, util_thr: float, trend_thr: float | None, n_boot: int = N_BOOT_RULE) -> pd.DataFrame:
    """95% intervals for precision and recall from an account-level (cluster) bootstrap: accounts are resampled with
    replacement and every month-end of a drawn account travels with it, so the interval allows for the ~10 rows each
    account contributes (a row-level interval treats them as independent and is too narrow). One row per billing
    period plus 'all', computed on the same resamples."""
    d = rows[["customer_id", "billing_period", "outcome"]].copy()
    d["flag"] = rule_flag(rows, util_thr, trend_thr).to_numpy()
    d["tp"] = d["flag"] & d["outcome"]
    ids, inv = np.unique(d["customer_id"].to_numpy(), return_inverse=True)
    n = len(ids)
    counts, n_acc = {}, {}
    for bp in ("monthly", "annual", "all"):
        m = np.ones(len(d), bool) if bp == "all" else (d["billing_period"] == bp).to_numpy()
        counts[bp] = tuple(np.bincount(inv[m], weights=d[c].to_numpy()[m].astype(float), minlength=n) for c in ("tp", "flag", "outcome"))
        n_acc[bp] = int((np.bincount(inv[m], minlength=n) > 0).sum())
    rng = np.random.default_rng(get_settings().seed)
    draws = {bp: np.full((n_boot, 2), np.nan) for bp in counts}
    for b in range(n_boot):
        w = np.bincount(rng.integers(0, n, n), minlength=n).astype(float)
        for bp, (tp, fl, pos) in counts.items():
            t, f, p = w @ tp, w @ fl, w @ pos
            draws[bp][b] = (t / f if f else np.nan, t / p if p else np.nan)
    out = []
    for bp, (tp, fl, pos) in counts.items():
        plo, phi = np.nanpercentile(draws[bp][:, 0], [2.5, 97.5])
        rlo, rhi = np.nanpercentile(draws[bp][:, 1], [2.5, 97.5])
        out.append({"billing_period": bp, "accounts": n_acc[bp], "rows": int(np.bincount(inv, minlength=n).sum()) if bp == "all" else int((d["billing_period"] == bp).sum()),
                    "precision": tp.sum() / fl.sum() if fl.sum() else np.nan, "precision_lo": float(plo), "precision_hi": float(phi),
                    "recall": tp.sum() / pos.sum() if pos.sum() else np.nan, "recall_lo": float(rlo), "recall_hi": float(rhi),
                    "n_boot": n_boot, "utilization_threshold": util_thr, "wau_trend_threshold": trend_thr})
    return pd.DataFrame(out)


def backtest_grid(rows: pd.DataFrame | None = None) -> pd.DataFrame:
    rows = backtest_rows() if rows is None else rows
    res = [evaluate_rule(rows, u, w) for u in UTIL_GRID for w in TREND_GRID]
    g = pd.DataFrame(res)
    g["passes_precision"] = g["lift"] >= 2
    g["passes_capacity"] = g["csm_accounts_per_quarter"] <= CSM_CAPACITY_PER_QUARTER
    u, w = choose_operating_point(g)
    g["chosen"] = (g["utilization_threshold"] == u) & (g["wau_trend_threshold"].isna() if w is None else g["wau_trend_threshold"] == w)
    g["doc_default"] = (g["utilization_threshold"] == DOC_DEFAULT_RULE[0]) & (g["wau_trend_threshold"] == DOC_DEFAULT_RULE[1])
    return g


def choose_operating_point(grid: pd.DataFrame) -> tuple[float, float | None]:
    """Criterion: among threshold pairs with precision >= 2x the base rate (lift >= 2) and an average of at most
    CSM_CAPACITY_PER_QUARTER distinct CSM-covered accounts flagged per quarter, take the highest recall
    (precision breaks ties)."""
    ok = grid[(grid["lift"] >= 2) & (grid["csm_accounts_per_quarter"] <= CSM_CAPACITY_PER_QUARTER)]
    if ok.empty:
        return UTIL_THRESHOLD, WAU_TREND_THRESHOLD
    best = ok.sort_values(["recall", "precision"], ascending=False).iloc[0]
    w = best["wau_trend_threshold"]
    return float(best["utilization_threshold"]), (None if pd.isna(w) else float(w))


def rule_thresholds() -> tuple[float, float | None]:
    """The operating point chosen in build() (from the grid mart), or the module defaults."""
    try:
        g = marts.load(f"{TOPIC}__backtest_grid")
        r = g[g["chosen"]].iloc[0]
        w = r["wau_trend_threshold"]
        return float(r["utilization_threshold"]), (None if pd.isna(w) else float(w))
    except (FileNotFoundError, IndexError, KeyError):
        return UTIL_THRESHOLD, WAU_TREND_THRESHOLD


def util_code(util_thr: float) -> str:
    return f"UTIL_BELOW_{int(round(util_thr * 100))}"


def current_flags(util_thr: float, trend_thr: float | None, month_end: pd.Timestamp = AS_OF) -> pd.DataFrame:
    """Accounts the rule flags at `month_end` (no outcome yet): eligible = paying, no open cancellation, no
    Contraction in the last 3 months, 13 complete weeks of usage."""
    f = marts.load("fct_account_month_features")
    el = f[(f["month_end"] == month_end) & (~f["cancel_requested_open"]) & (f["contraction_months_3m"] == 0)
           & (f["weeks_13w"] == 13)].copy()
    el["billing_period"] = el["current_period_type"].fillna(el["billing_period"])
    return el[rule_flag(el, util_thr, trend_thr)].reset_index(drop=True)


# =============================================================================== watchlist (02 §15, risk)
def _play(r) -> str:
    if r["contraction_class"] == "saved_re_risk":
        return "Post-save check-in: confirm the smaller plan is in use; secure the renewal early"
    if r["early_warning"] and pd.notna(r["wau_trend"]) and r["wau_trend"] >= WAU_UP_CODE:
        # idle seats but a growing team: a seat-count conversation, not an idle-seat save
        return ("Usage is growing into the seats: confirm team size and seat count before renewal; right-size only if the admin asks, no discount"
                if r["queue"] == "csm" else
                "Usage is growing into the seats: automated seat-count check-in before the next renewal or billing date; no idle-seat nudge, no discount")
    if r["early_warning"] and r["renewal_within_120d"]:
        return ("Pre-renewal utilization review 60 days out: activate idle seats or agree the right size before renewal"
                if r["queue"] == "csm" else "Digital pre-renewal sequence 60 days out: utilization nudge + renewal email series")
    if r["early_warning"]:
        return ("Adoption play: admin outreach on idle seats, re-onboard inactive users"
                if r["queue"] == "csm" else "Digital adoption play: in-app utilization nudge + admin email sequence")
    if r["any_plan_downgrade"]:
        return "Value review of the features lost in the downgrade; targeted re-trial"
    if r["industry"] == "Technology":
        return "Headcount check-in: confirm the team plan, lock the remaining seats on annual terms"
    return "Seat right-size follow-up: confirm remaining seats are active; quarterly business review"


def build_watchlist(threshold: float = DECLINE_THRESHOLD, lookback: int = LOOKBACK_MONTHS,
                    util_thr: float | None = None, trend_thr: float | None = None,
                    month_end: pd.Timestamp = AS_OF) -> pd.DataFrame:
    """Union of the §9 flags (silent / saved) and the early-warning flags at `month_end`, with reason codes,
    evidence, ARR at stake (current ARR x the historical hit rate of the flag bucket) and a priority rank within
    each queue (CSM-covered vs digital) in three tiers: (1) annual renewals in under URGENT_RENEWAL_DAYS days, soonest
    first; (2) annual renewals within RENEWAL_SOON_DAYS days, by ARR at stake; (3) everything else, by ARR at stake."""
    if util_thr is None:
        util_thr, trend_thr = rule_thresholds()
    flags = silent_flags(threshold, lookback)
    cur = flags[flags["month_end"] == month_end].copy()
    ew = current_flags(util_thr, trend_thr, month_end)
    # --- bucket hit rates (history): §9 classes -> 6-month churn-or-further-contraction; rule -> back-test precision by billing
    fu = followup(flags).set_index("group")
    rows = backtest_rows()
    ew_prec = {bp: evaluate_rule(rows[rows["billing_period"] == bp], util_thr, trend_thr)["precision"] for bp in ("monthly", "annual")}
    bucket = {"silent": float(fu.loc["silent", "loss_6m"]) if "silent" in fu.index else np.nan,
              "saved_re_risk": float(fu.loc["saved_re_risk", "loss_6m"]) if "saved_re_risk" in fu.index else np.nan,
              "early_warning_monthly": float(ew_prec["monthly"]), "early_warning_annual": float(ew_prec["annual"])}
    # --- base attributes at month_end
    feat = marts.load("fct_account_month_features")
    base = feat[feat["month_end"] == month_end][[
        "customer_id", "segment", "industry", "cs_coverage", "plan_id", "billing_period", "seats", "arr", "tenure_months",
        "utilization_13w", "wau_trend", "weeks_13w", "current_period_type"]].copy()
    base["billing_period"] = base["current_period_type"].fillna(base["billing_period"])
    ids = pd.Index(cur["customer_id"]).union(pd.Index(ew["customer_id"]))
    w = base[base["customer_id"].isin(ids)].copy()
    cust = marts.load("stg_customers")[["customer_id", "company_name", "csm_owner"]]
    nr = marts.load("int_next_renewal")[["customer_id", "next_renewal_date"]]
    w = w.merge(cust, on="customer_id", how="left").merge(nr, on="customer_id", how="left")
    w["days_to_renewal"] = (w["next_renewal_date"] - month_end).dt.days
    w = w.merge(cur[["customer_id", "contraction_class", "decline_pct_norm", "peak_seats", "seat_cut_pct", "any_plan_downgrade",
                     "any_renewal_contraction", "lost_arr", "contraction_steps", "months_since_last_contraction"]],
                on="customer_id", how="left")
    w["early_warning"] = w["customer_id"].isin(ew["customer_id"])
    w["silent_flag"] = w["contraction_class"].notna()
    w["contraction_class"] = w["contraction_class"].fillna("none")
    for c in ("any_plan_downgrade", "any_renewal_contraction"):
        w[c] = w[c].fillna(False).astype(bool)
    # last contraction row (for the evidence string)
    last = _contraction_rows(cur["customer_id"], month_end, lookback).sort_values("effective_date").drop_duplicates("customer_id", keep="last")
    w = w.merge(last[["customer_id", "effective_date", "change_source", "from_seats", "to_seats", "from_plan_id", "to_plan_id", "kind"]],
                on="customer_id", how="left")
    w["renewal_within_120d"] = (w["billing_period"] == "annual") & (w["days_to_renewal"] <= RENEWAL_SOON_DAYS)
    w["queue"] = np.where(w["cs_coverage"].isin(CSM_COVERAGE), "csm", "digital")
    w["flag_source"] = np.select(
        [w["silent_flag"] & w["early_warning"], w["silent_flag"]],
        [w["contraction_class"] + " + early_warning", w["contraction_class"]], default="early_warning")
    # --- bucket precision & ARR at stake: an account in both buckets takes the higher rate
    class_rate = w["contraction_class"].map({"silent": bucket["silent"], "saved_re_risk": bucket["saved_re_risk"]})
    ew_rate = np.where(w["billing_period"] == "annual", bucket["early_warning_annual"], bucket["early_warning_monthly"])
    ew_rate = pd.Series(np.where(w["early_warning"], ew_rate, np.nan), index=w.index)
    w["bucket_precision"] = pd.concat([class_rate, ew_rate], axis=1).max(axis=1)
    w["current_arr"] = w["arr"]
    w["arr_at_stake"] = w["current_arr"] * w["bucket_precision"]
    # --- reason codes and evidence
    ucode = util_code(util_thr)
    codes = {
        "SAVED_BY_CS_6M": w["contraction_class"] == "saved_re_risk",
        "DOWNGRADED_6M": w["any_plan_downgrade"],
        "SEATS_CUT_20PCT_6M": w["silent_flag"] & (w["seat_cut_pct"] >= 0.20),
        "RENEWAL_RIGHTSIZED_6M": w["any_renewal_contraction"],
        "SPEND_DOWN_20PCT_6M": w["silent_flag"],
        ucode: w["early_warning"],
        "WAU_DOWN_15PCT": w["wau_trend"] <= WAU_DOWN_CODE,
        "WAU_UP_20PCT": w["wau_trend"] >= WAU_UP_CODE,
        "RENEWAL_WITHIN_120D": w["renewal_within_120d"],
    }
    priority = [c if c != "UTIL_BELOW_50" else ucode for c in CODE_PRIORITY]
    code_lists = []
    for i in w.index:
        code_lists.append([c for c in priority if bool(codes[c].get(i, False))])
    w["reason_codes"] = ["|".join(c) for c in code_lists]
    for k in range(3):
        w[f"reason_code_{k + 1}"] = [c[k] if len(c) > k else "" for c in code_lists]

    def evidence(r) -> str:
        parts = []
        if r["silent_flag"]:
            parts.append(f"billing-neutral spend {pct(-r['decline_pct_norm'], 0, signed=True)} vs {lookback}-month peak")
            if pd.notna(r["from_seats"]) and r["from_seats"] != r["to_seats"]:
                parts.append(f"seats {int(r['from_seats'])} -> {int(r['to_seats'])} ({r['effective_date']:%b %Y}, {r['change_source']})")
            if pd.notna(r["from_plan_id"]) and r["from_plan_id"] != r["to_plan_id"]:
                parts.append(f"plan {r['from_plan_id']} -> {r['to_plan_id']} ({r['effective_date']:%b %Y}, {r['change_source']})")
            parts.append(f"ARR already lost {money(r['lost_arr'], 1)}")
        if pd.notna(r["utilization_13w"]):
            parts.append(f"utilization {pct(r['utilization_13w'], 0)} over 13 weeks")
        if pd.notna(r["wau_trend"]):
            parts.append(f"WAU {pct(r['wau_trend'], 0, signed=True)} vs prior 9 weeks"
                         + (" (usage growing into the seats)" if r["early_warning"] and r["wau_trend"] >= WAU_UP_CODE else ""))
        if r["billing_period"] == "annual" and pd.notna(r["next_renewal_date"]):
            parts.append(f"annual renewal {r['next_renewal_date']:%Y-%m-%d} ({int(r['days_to_renewal'])} days)")
        elif pd.notna(r["next_renewal_date"]):
            parts.append(f"next monthly billing date {r['next_renewal_date']:%Y-%m-%d}")
        return "; ".join(parts)

    w["evidence"] = w.apply(evidence, axis=1)
    w["suggested_play"] = w.apply(_play, axis=1)
    # annual renewals in fewer than 45 days: say so in the play itself (the dashboard, the watchlist mart and the CS list
    # all show the same text)
    urgent = (w["billing_period"] == "annual") & (w["days_to_renewal"] < URGENT_RENEWAL_DAYS)
    w.loc[urgent, "suggested_play"] = ("Urgent — renews in " + w.loc[urgent, "days_to_renewal"].astype(int).astype(str) + " days: "
                                       + w.loc[urgent, "suggested_play"].str.replace(" 60 days out", " now", regex=False))
    w["list_type"] = "risk"
    w["plan"] = w["plan_id"]
    # --- priority: tier 1 = annual renewals in under 45 days, soonest first (a CSM working top-down must reach them before
    #     the date passes); tier 2 = renewals in 45-120 days by ARR at stake; tier 3 = everything else by ARR at stake
    w["priority_tier"] = np.select([w["renewal_within_120d"] & (w["days_to_renewal"] < URGENT_RENEWAL_DAYS), w["renewal_within_120d"]],
                                   [1, 2], default=3)
    w["_key"] = np.where(w["priority_tier"] == 1, w["days_to_renewal"], -w["arr_at_stake"])
    w = w.sort_values(["priority_tier", "_key", "customer_id"]).reset_index(drop=True)
    w["overall_rank"] = np.arange(1, len(w) + 1)
    w = w.sort_values(["queue", "overall_rank"])
    w["priority_rank"] = w.groupby("queue").cumcount() + 1
    cols = ["customer_id", "company_name", "segment", "industry", "cs_coverage", "csm_owner", "plan", "billing_period", "seats",
            "current_arr", "next_renewal_date", "days_to_renewal", "list_type", "flag_source", "contraction_class", "early_warning",
            "reason_code_1", "reason_code_2", "reason_code_3", "reason_codes", "evidence", "bucket_precision", "arr_at_stake",
            "lost_arr", "decline_pct_norm", "seat_cut_pct", "utilization_13w", "wau_trend", "tenure_months",
            "renewal_within_120d", "queue", "priority_tier", "priority_rank", "overall_rank", "suggested_play"]
    w["seats"] = w["seats"].astype(int)
    w["lost_arr"] = w["lost_arr"].fillna(0.0)
    out = w[cols].reset_index(drop=True)
    out.attrs["bucket_precision"] = bucket
    out.attrs["rule"] = (util_thr, trend_thr)
    return out


# =============================================================================== build / key numbers
def build() -> dict[str, pd.DataFrame]:
    """Derived tables written to the parquet cache (topic prefix `silent_churn__`)."""
    flags = silent_flags()
    check = verify_definition()
    rows = backtest_rows()
    grid = backtest_grid(rows)
    marts.write(f"{TOPIC}__backtest_grid", grid)   # build_watchlist reads the chosen point from it
    u, w = rule_thresholds()
    caught = lead_times(rows, u, w)
    by_bp = pd.DataFrame([evaluate_rule(rows[rows["billing_period"] == bp], u, w) | {"billing_period": bp} for bp in ("monthly", "annual")]
                         + [evaluate_rule(rows, u, w) | {"billing_period": "all"}])
    doc_by_bp = pd.DataFrame([evaluate_rule(rows[rows["billing_period"] == bp], *DOC_DEFAULT_RULE) | {"billing_period": bp} for bp in ("monthly", "annual")]
                             + [evaluate_rule(rows, *DOC_DEFAULT_RULE) | {"billing_period": "all"}])
    by_quarter = pd.concat([rule_by_quarter(rows, u, w).assign(rule="chosen"),
                            rule_by_quarter(rows, *DOC_DEFAULT_RULE).assign(rule="doc_default")], ignore_index=True)
    bootstrap = bootstrap_rule(rows, u, w)
    tables = {
        f"{TOPIC}__flags": flags,
        f"{TOPIC}__definition_check": pd.DataFrame([check]),
        f"{TOPIC}__flag_trend": flag_trend(flags),
        f"{TOPIC}__flag_rate_tech": flag_rate_by_industry_group(flags),
        f"{TOPIC}__profile": profile(flags),
        f"{TOPIC}__contraction_mix": contraction_mix(flags),
        f"{TOPIC}__followup": followup(flags),
        f"{TOPIC}__backtest_rows": rows,
        f"{TOPIC}__backtest_grid": grid,
        f"{TOPIC}__rule_by_billing": pd.concat([by_bp.assign(rule="chosen"), doc_by_bp.assign(rule="doc_default")], ignore_index=True),
        f"{TOPIC}__rule_by_quarter": by_quarter,
        f"{TOPIC}__rule_bootstrap": bootstrap,
        f"{TOPIC}__lead_times": caught,
        f"{TOPIC}__watchlist": build_watchlist(),
    }
    for name, df in tables.items():
        marts.write(name, df)
    return tables


def key_numbers() -> dict:
    k = marts.read_key_numbers("metrics")
    flags = marts.load(f"{TOPIC}__flags")
    cur = flags[flags["month_end"] == AS_OF]
    tr = marts.load(f"{TOPIC}__flag_trend")
    june = tr[tr["month_end"] == pd.Timestamp("2026-06-30")]
    ly = tr[tr["month_end"] == pd.Timestamp("2025-09-30")]
    prof = marts.load(f"{TOPIC}__profile").set_index(["dimension", "value"])
    rate = marts.load(f"{TOPIC}__flag_rate_tech").set_index(["month_end", "group"])["rate"]
    mix = marts.load(f"{TOPIC}__contraction_mix")
    fu = marts.load(f"{TOPIC}__followup").set_index("group")
    grid = marts.load(f"{TOPIC}__backtest_grid")
    chosen = grid[grid["chosen"]].iloc[0]
    doc = grid[grid["doc_default"]].iloc[0]
    bb = marts.load(f"{TOPIC}__rule_by_billing")
    bq = marts.load(f"{TOPIC}__rule_by_quarter")
    bq = bq[bq["rule"] == "chosen"]
    boot = marts.load(f"{TOPIC}__rule_bootstrap").set_index("billing_period")
    wl = marts.load(f"{TOPIC}__watchlist")
    check = marts.load(f"{TOPIC}__definition_check").iloc[0]
    by_cls = cur.groupby("contraction_class").agg(n=("customer_id", "size"), arr=("current_arr", "sum"), lost=("lost_arr", "sum"))
    mix_acc = mix.groupby("kind", observed=True)["accounts"].sum()
    csm_q = wl[wl["queue"] == "csm"].sort_values("priority_rank")
    top120 = csm_q.head(CSM_CAPACITY_PER_QUARTER)
    rising = wl["suggested_play"].str.contains("Usage is growing into the seats", regex=False)   # the WAU_UP_20PCT play (early warning + WAU up, not a CS save)
    bp_rows = lambda rule: {bp: {m: (None if pd.isna(v) else float(v)) for m, v in bb[(bb["rule"] == rule) & (bb["billing_period"] == bp)].iloc[0]
                                 [["base_rate", "precision", "recall", "lift", "flags_per_month", "csm_flags_per_month", "event_recall", "lead_median",
                                   "lead_p25", "lead_p75", "share_lead_ge2"]].items()} for bp in ("monthly", "annual")}
    interval = lambda bp: {f"{m}_{s}": float(boot.loc[bp, f"{m}_{s}"]) for m in ("precision", "recall") for s in ("lo", "hi")}
    by_quarter = {
        "first_quarter": theme.qname(bq["quarter_label"].iloc[0]), "last_quarter": theme.qname(bq["quarter_label"].iloc[-1]),
        "quarters": int(len(bq)), "min_eligible_rows": int(bq["eligible_rows"].min()),
        **{f"{m}_{s}": float(getattr(bq[m], s)()) for m in ("precision", "recall", "base_rate", "lift") for s in ("min", "max")},
        "note": "precision, recall and base rate of the chosen rule in each calendar quarter (row grain); selection and evaluation "
                "share these rows, so this is the check that the pooled figures are not the best grid cell by luck",
    }
    rule_dict = lambda r: {"utilization_threshold": float(r["utilization_threshold"]),
                           "wau_trend_threshold": None if pd.isna(r["wau_trend_threshold"]) else float(r["wau_trend_threshold"]),
                           **{m: float(r[m]) for m in ["base_rate", "precision", "recall", "lift", "flags_per_month", "csm_flags_per_month",
                                                      "flagged_accounts_per_quarter", "csm_accounts_per_quarter", "event_recall",
                                                      "account_recall", "lead_median", "lead_p25", "lead_p75", "share_lead_ge2"]},
                           "eligible_rows": int(r["eligible_rows"]), "positives": int(r["positives"]), "flagged_rows": int(r["flagged_rows"]),
                           "events": int(r["events"]), "events_caught": int(r["events_caught"])}
    return {
        "as_of": str(AS_OF.date()),
        "definition": {"decline_threshold": DECLINE_THRESHOLD, "lookback_months": LOOKBACK_MONTHS,
                       "pandas_reproduces_sql": bool(check["exact_match"]), "sql_rows": int(check["sql_rows"]),
                       "pandas_rows": int(check["pandas_rows"])},
        "silent_accounts": int(by_cls.loc["silent", "n"]), "saved_accounts": int(by_cls.loc["saved_re_risk", "n"]),
        "flagged_accounts": int(len(cur)),
        "arr_at_risk": float(cur["current_arr"].sum()), "arr_at_risk_share_of_arr": float(cur["current_arr"].sum() / k["arr"]),
        "silent_arr": float(by_cls.loc["silent", "arr"]), "saved_arr": float(by_cls.loc["saved_re_risk", "arr"]),
        "lost_arr": float(cur["lost_arr"].sum()), "silent_lost_arr": float(by_cls.loc["silent", "lost"]),
        "saved_lost_arr": float(by_cls.loc["saved_re_risk", "lost"]),
        "median_decline_pct": float(cur["decline_pct_norm"].median()),
        "flagged_accounts_2026_06": int(june["accounts"].sum()), "arr_at_risk_2026_06": float(june["current_arr"].sum()),
        "flagged_accounts_2025_09": int(ly["accounts"].sum()), "arr_at_risk_2025_09": float(ly["current_arr"].sum()),
        "profile": {
            "technology_share_flagged": float(prof.loc[("industry", "Technology"), "share_flagged"]),
            "technology_share_other": float(prof.loc[("industry", "Technology"), "share_other"]),
            "technology_index": float(prof.loc[("industry", "Technology"), "index"]),
            "technology_flag_rate": float(rate[(AS_OF, "Technology")]),
            "other_flag_rate": float(rate[(AS_OF, "All other industries")]),
            "technology_flag_rate_2026_06": float(rate[(pd.Timestamp("2026-06-30"), "Technology")]),
            "technology_flagged_accounts": int(prof.loc[("industry", "Technology"), "flagged"]),
            "monthly_billing_index": float(prof.loc[("billing_period", "monthly"), "index"]),
            "basic_plan_index": float(prof.loc[("plan_id", "basic"), "index"]),
            "smb_index": float(prof.loc[("segment", "SMB"), "index"]),
            "enterprise_index": float(prof.loc[("segment", "Enterprise"), "index"]),
            "utilization_under_40_index": float(prof.loc[("utilization_band", "under 40%"), "index"]),
            "wau_down_30_index": float(prof.loc[("wau_trend_band", "down 30%+"), "index"]),
        },
        "contraction_mix": {
            "seat_cut_monthly_accounts": int(mix_acc.get("Seat cut at a monthly billing date", 0)),
            "seat_rightsize_renewal_accounts": int(mix_acc.get("Seat right-sizing at renewal", 0)),
            "plan_downgrade_accounts": int(mix_acc.get("Plan downgrade (self-serve)", 0) + mix_acc.get("Plan downgrade at renewal", 0)),
            "cs_save_accounts": int(mix_acc.get("CS save offer", 0)),
            "median_seat_cut_pct": float(cur.loc[cur["seat_cut_pct"] > 0, "seat_cut_pct"].median()),
        },
        "followup": {
            "history_window": f"{HISTORY_START.date()} to {HISTORY_END.date()} (newly flagged accounts, 6-month windows complete)",
            **{f"{g}_{m}": float(fu.loc[g, m]) for g in ("silent", "saved_re_risk", "unflagged_matched", "unflagged_raw")
               for m in ("churn_6m", "churn_6m_lo", "churn_6m_hi", "loss_6m", "loss_6m_lo", "loss_6m_hi")},
            **{f"{g}_n": int(fu.loc[g, "n"]) for g in ("silent", "saved_re_risk", "unflagged_matched")},
        },
        "rule": {**rule_dict(chosen), **interval("all"),
                 "interval": f"95% account-level cluster bootstrap: {int(boot.loc['all', 'n_boot']):,} resamples of {int(boot.loc['all', 'accounts']):,} accounts "
                             "(every month-end of a drawn account travels with it)",
                 "bootstrap_accounts": int(boot.loc["all", "accounts"]), "bootstrap_resamples": int(boot.loc["all", "n_boot"]),
                 "by_billing_period": {bp: {**bp_rows("chosen")[bp], **interval(bp)} for bp in ("monthly", "annual")},
                 "by_quarter": by_quarter,
                 "lead_time_note": "one run per caught event (the flag run ending closest to the outcome); the pooled median moves with "
                                   "the monthly / annual mix, so quote the by-billing medians and share_lead_ge2",
                 "criterion": f"max recall s.t. precision >= 2x base rate and <= {CSM_CAPACITY_PER_QUARTER} distinct CSM-covered accounts flagged per quarter",
                 "eligibility": "paying, no open cancellation, no Contraction in t-2..t, outcome window complete, 13 complete weeks of usage"},
        "doc_default_rule": {**rule_dict(doc), "by_billing_period": bp_rows("doc_default")},
        "watchlist": {
            "accounts": int(len(wl)), "csm_queue": int((wl["queue"] == "csm").sum()), "digital_queue": int((wl["queue"] == "digital").sum()),
            "silent_or_saved": int(wl["contraction_class"].ne("none").sum()), "early_warning": int(wl["early_warning"].sum()),
            "both": int((wl["contraction_class"].ne("none") & wl["early_warning"]).sum()),
            "current_arr": float(wl["current_arr"].sum()), "arr_at_stake": float(wl["arr_at_stake"].sum()),
            "renewals_within_120d": int(wl["renewal_within_120d"].sum()),
            "renewal_arr_within_120d": float(wl.loc[wl["renewal_within_120d"], "current_arr"].sum()),
            "renewals_under_45d": int((wl["priority_tier"] == 1).sum()), "urgent_renewal_days": URGENT_RENEWAL_DAYS,
            "priority": f"within each queue: tier 1 = annual renewals in under {URGENT_RENEWAL_DAYS} days, soonest first; tier 2 = annual "
                        f"renewals within {RENEWAL_SOON_DAYS} days by ARR at stake; tier 3 = the rest by ARR at stake",
            "csm_tier1_under_45d": int((csm_q["priority_tier"] == 1).sum()), "csm_tier2_45_120d": int((csm_q["priority_tier"] == 2).sum()),
            "csm_tier3_rest": int((csm_q["priority_tier"] == 3).sum()),
            "top120_csm_renewals_within_120d": int(top120["renewal_within_120d"].sum()),
            "top120_csm_arr_at_stake": float(top120["arr_at_stake"].sum()),
            "top120_csm_share_of_arr_at_stake": float(top120["arr_at_stake"].sum() / wl["arr_at_stake"].sum()),
            "top120_csm_share_of_csm_arr_at_stake": float(top120["arr_at_stake"].sum() / max(csm_q["arr_at_stake"].sum(), 1e-9)),
            "rising_usage": int(rising.sum()), "csm_rising_usage": int((rising & (wl["queue"] == "csm")).sum()),
            "top120_csm_rising_usage": int(top120["suggested_play"].str.contains("Usage is growing into the seats", regex=False).sum()),
            "rising_usage_note": f"early-warning accounts (not CS saves) whose WAU rose {WAU_UP_CODE:.0%}+ (last 4 weeks vs the 9 before): "
                                 "seat-count conversation, not an idle-seat save (reason code WAU_UP_20PCT)",
            "bucket_precision": {kk: float(v) for kk, v in build_watchlist().attrs["bucket_precision"].items()},
        },
    }


# =============================================================================== figures
def _month_axis(fig: go.Figure, months: pd.Series) -> None:
    x = theme.month_x(pd.Series(months))
    fig.update_xaxes(tickformat="%b %Y", tickvals=theme.quarter_ticks(months),
                     range=[x.min() - pd.Timedelta(days=20), x.max() + pd.Timedelta(days=20)])


def fig_flagged_trend(trend: pd.DataFrame, threshold: float = DECLINE_THRESHOLD, lookback: int = LOOKBACK_MONTHS) -> go.Figure:
    """Stacked bars: current ARR of flagged accounts per month-end, silent vs saved."""
    fig = go.Figure()
    for cls in ("silent", "saved_re_risk"):
        s = trend[trend["contraction_class"] == cls]
        fig.add_trace(go.Bar(x=theme.month_x(s["month_end"]), y=s["current_arr"], name=CLASS_LABELS[cls],
                             marker=dict(color=CLASS_COLORS[cls], line=dict(color=theme.SURFACE, width=1)),
                             customdata=np.c_[s["accounts"], s["lost_arr"]],
                             hovertemplate="%{x|%b %Y}<br>" + CLASS_LABELS[cls] + ": %{customdata[0]:,} accounts<br>ARR at risk %{y:$,.0f}"
                                           "<br>ARR already lost %{customdata[1]:$,.0f}<extra></extra>"))
    fig.update_layout(barmode="stack", bargap=0.3)
    fig.update_yaxes(tickprefix="$", tickformat="~s", rangemode="tozero")
    _month_axis(fig, trend["month_end"])
    tot = trend.groupby("month_end")[["current_arr", "accounts"]].sum()
    june_me = pd.Timestamp("2026-06-30")
    last, june = tot.iloc[-1], tot.loc[june_me] if june_me in tot.index else tot.iloc[-4]
    m2 = lambda x: money(x, 2) if abs(x) >= 1e6 else money(x)      # "$1.07M" / "$571K": the precision the text pages use
    direction = "up from" if last["current_arr"] >= june["current_arr"] else "down from"
    return theme.apply(fig, f"Flagged ARR (silent + saved by CS) reached {m2(last['current_arr'])} across {int(last['accounts'])} accounts at "
                            f"{last.name:%b %Y}, {direction} {m2(june['current_arr'])} in {june.name:%b %Y}",
                       f"Current ARR (USD) of accounts whose billing-neutral spend is ≥ {pct(threshold, 0)} below its {lookback}-month peak after a "
                       "seat, plan or discount contraction (02 §9), at each month-end; silent = customer-initiated, saved = kept by a CS save offer",
                       height=400)


def fig_tech_flag_rate(rate: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    colors = {"Technology": theme.TECH, "All other industries": theme.OTHER}
    for grp in ("Technology", "All other industries"):
        s = rate[rate["group"] == grp]
        fig.add_trace(go.Scatter(x=theme.month_x(s["month_end"]), y=s["rate"], mode="lines+markers", name=grp,
                                 line=dict(color=colors[grp], width=2), marker=dict(size=8, color=colors[grp]),
                                 customdata=np.c_[s["flagged"], s["paying"]],
                                 hovertemplate="%{x|%b %Y}<br>" + grp + ": %{y:.1%} flagged (%{customdata[0]:,} of %{customdata[1]:,})<extra></extra>"))
    fig.update_yaxes(tickformat=".0%", rangemode="tozero")
    _month_axis(fig, rate["month_end"])
    r = rate.set_index(["month_end", "group"])["rate"]
    t_now, t_jun = r[(AS_OF, "Technology")], r[(pd.Timestamp("2026-06-30"), "Technology")]
    o_now = r[(AS_OF, "All other industries")]
    return theme.apply(fig, f"Technology flag rate rose from {pct(t_jun, 1)} in June to {pct(t_now, 1)} in September 2026; "
                            f"other industries stayed near {pct(o_now, 1)}",
                       "Share of paying accounts flagged as silent contraction (either class) at each month-end", height=380)


def fig_overrepresentation(prof: pd.DataFrame, dimension: str = "industry") -> go.Figure:
    s = prof[(prof["dimension"] == dimension) & (prof["flagged"] + prof["other"] >= 20)].copy()
    if dimension in BAND_ORDER:   # natural order, first band at the top (horizontal bars draw the first row at the bottom)
        s["o"] = s["value"].map({v: i for i, v in enumerate(BAND_ORDER[dimension])}).fillna(99)
        s = s.sort_values("o", ascending=False)
    else:
        s = s.sort_values("index")
    s = s[~((s["value"] == "Unknown") & (s["flagged"] == 0))]          # an empty "Unknown" row carries no information
    if dimension == "industry":     # Technology keeps its colour across the page; every other industry is "the rest"
        colors = [theme.TECH if v == "Technology" else theme.DEEMPH for v in s["value"]]
    else:                           # other dimensions: neutral emphasis (dark gray above 1×), never blue / orange
        colors = [theme.INK_2 if v >= 1 else theme.DEEMPH for v in s["index"]]
    fig = go.Figure(go.Bar(y=s["value"], x=s["index"], orientation="h", marker=dict(color=colors),
                           text=[f"{v:.1f}×  ({int(n):,})" for v, n in zip(s["index"], s["flagged"])], textposition="outside",
                           textfont=dict(color=theme.INK, size=11), cliponaxis=False,
                           customdata=np.c_[s["flagged"], s["other"], s["flag_rate"]],
                           hovertemplate="%{y}<br>%{customdata[0]:,} flagged, %{customdata[1]:,} other paying<br>"
                                         "flag rate %{customdata[2]:.1%}<br>index %{x:.2f}<extra></extra>"))
    fig.add_vline(x=1.0, line=dict(color=theme.AXIS, width=1))
    fig.update_xaxes(title_text="Over-representation index (share of flagged ÷ share of other paying accounts)", showgrid=True,
                     gridcolor=theme.GRID, rangemode="tozero")
    fig.update_yaxes(showgrid=False, automargin=True)
    fig.update_layout(showlegend=False, bargap=0.35, margin=dict(r=90))
    top = s.sort_values("index").iloc[-1]
    label = PROFILE_DIMS[dimension]
    label = label[0].lower() + label[1:] if label[1:2].islower() else label   # "industry", but keep "WAU trend"
    return theme.apply(fig, f"By {label}: {top['value']} is {top['index']:.1f}× over-represented among silently contracting accounts "
                            f"({int(top['flagged'])} accounts, flag rate {pct(top['flag_rate'], 0)})",
                       "Index 1.0 = same share as other paying accounts at 30 Sep 2026; labels show index (flagged accounts); groups with ≥ 20 accounts",
                       height=max(300, 110 + 34 * len(s)))


def fig_contraction_mix(mix: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    colors = {"Technology": theme.TECH, "All other industries": theme.OTHER}
    kinds = [k for k in KIND_ORDER if k in set(mix["kind"].astype(str))]
    for grp in ("Technology", "All other industries"):
        s = mix[mix["group"] == grp].assign(kind=lambda d: d["kind"].astype(str)).set_index("kind").reindex(kinds).fillna(0)
        fig.add_trace(go.Bar(y=kinds, x=s["accounts"], orientation="h", name=grp, marker=dict(color=colors[grp]),
                             customdata=s["mrr_lost"],
                             hovertemplate="%{y}<br>" + grp + ": %{x:,} accounts, MRR lost %{customdata:$,.0f}<extra></extra>"))
    fig.update_layout(barmode="stack", bargap=0.35)
    fig.update_xaxes(title_text="Flagged accounts", showgrid=True, gridcolor=theme.GRID)
    fig.update_yaxes(autorange="reversed", showgrid=False, automargin=True)
    tot = mix.groupby("kind", observed=True)["accounts"].sum()
    seat = int(tot.get("Seat cut at a monthly billing date", 0))
    return theme.apply(fig, f"Seat cuts at monthly billing dates account for {seat} of {int(tot.sum())} flagged accounts; plan downgrades are rare",
                       "Each flagged account assigned to the contraction type that cost the most MRR in the last 6 months; 30 Sep 2026",
                       height=360)


def fig_followup(fu: pd.DataFrame) -> go.Figure:
    s = fu[fu["group"] != "unflagged_raw"]
    fig = go.Figure()
    for col, name, color in (("churn_6m", "Churned within 6 months", theme.NESTED_BLUES[0]),
                             ("loss_6m", "Churned or contracted again (≥ 10%) within 6 months", theme.NESTED_BLUES[1])):
        fig.add_trace(go.Bar(x=s["label"], y=s[col], name=name, marker=dict(color=color),
                             error_y=dict(type="data", symmetric=False, array=s[f"{col}_hi"] - s[col], arrayminus=s[col] - s[f"{col}_lo"],
                                          color=theme.INK_2, thickness=1.5, width=6),
                             offsetgroup=col, customdata=s["n"],
                             hovertemplate="%{x}<br>" + name + ": %{y:.1%} (n = %{customdata:,})<extra></extra>"))
        # value labels sit just above the whisker, so the interval never strikes through them
        fig.add_trace(go.Scatter(x=s["label"], y=s[f"{col}_hi"], mode="text", text=[pct(v, 0) for v in s[col]],
                                 textposition="top center", textfont=dict(color=theme.INK, size=12), offsetgroup=col,
                                 showlegend=False, hoverinfo="skip", cliponaxis=False))
    fig.update_layout(barmode="group", bargap=0.35, scattermode="group")
    fig.update_yaxes(tickformat=".0%", rangemode="tozero", range=[0, float(s[["churn_6m_hi", "loss_6m_hi"]].max().max()) * 1.18])
    fig.update_xaxes(automargin=True)
    si, um = fu.set_index("group").loc["silent"], fu.set_index("group").loc["unflagged_matched"]
    return theme.apply(fig, f"Newly flagged silent accounts churned at {pct(si['churn_6m'], 1)} within 6 months vs {pct(um['churn_6m'], 1)} for comparable "
                            "unflagged accounts: the loss is mostly already booked",
                       "Accounts newly flagged Jan 2025 – Mar 2026 (complete 6-month windows); comparable = unflagged paying accounts weighted to "
                       "the flagged segment × billing × tenure mix; whiskers = 95% Wilson intervals on the rows (the unflagged groups are "
                       "account-months, so their intervals ignore within-account correlation and are narrower than account-level ones)",
                       height=420)


def fig_pr_tradeoff(grid: pd.DataFrame, chosen: tuple[float, float | None] | None = None,
                    current: tuple[float, float | None] | None = None) -> go.Figure:
    """Precision vs recall for every threshold pair; utilization-only and the two doc-style trend families in
    colour, everything else gray; the chosen operating point (and the slider point, if any) highlighted."""
    g = grid.copy()
    g["w"] = g["wau_trend_threshold"]
    if chosen is None:
        chosen = rule_thresholds()
    # one colour per family: blue = the utilization-only line (the chosen family, labelled); violet = "plus a WAU-trend
    # condition", solid for ≤ 0% and dashed for ≤ −15% (the v1.0 draft's family); thresholds for those stay in the hover
    fam = {None: ("Utilization only", theme.ACCENT, "solid", "circle"),
           0.0: ("… and WAU trend ≤ 0%", theme.SERIES[6], "solid", "square"),
           -0.15: ("… and WAU trend ≤ −15%", theme.SERIES[6], "dash", "diamond")}
    fig = go.Figure()
    other = g[~g["w"].isna() & ~g["w"].isin([-0.15, 0.0])]
    fig.add_trace(go.Scatter(x=other["recall"], y=other["precision"], mode="markers", name="Other WAU-trend thresholds",
                             marker=dict(color=theme.DEEMPH, size=7, line=dict(width=0)),
                             customdata=np.c_[other["utilization_threshold"], other["w"], other["lift"], other["csm_accounts_per_quarter"]],
                             hovertemplate="utilization < %{customdata[0]:.0%}, WAU trend ≤ %{customdata[1]:.0%}<br>precision %{y:.1%}, recall %{x:.1%}, "
                                           "lift %{customdata[2]:.2f}×<br>%{customdata[3]:.0f} CSM-covered accounts / quarter<extra></extra>"))
    for w, (name, color, dash, symbol) in fam.items():
        s = (g[g["w"].isna()] if w is None else g[g["w"] == w]).sort_values("utilization_threshold")
        labelled = w is None
        is_chosen_pt = [chosen[1] is None and abs(u - chosen[0]) < 1e-9 for u in s["utilization_threshold"]] if labelled else []
        fig.add_trace(go.Scatter(x=s["recall"], y=s["precision"], mode="lines+markers+text" if labelled else "lines+markers", name=name,
                                 line=dict(color=color, width=2, dash=dash), marker=dict(size=8, color=color, symbol=symbol),
                                 text=[f"{u:.0%}" for u in s["utilization_threshold"]] if labelled else None,
                                 textposition=["top right" if c else "top center" for c in is_chosen_pt] if labelled else None,   # clear of the ring
                                 textfont=dict(size=10, color=theme.INK_2),
                                 customdata=np.c_[s["utilization_threshold"], s["lift"], s["csm_accounts_per_quarter"]],
                                 hovertemplate="utilization < %{customdata[0]:.0%}" + ("" if w is None else f", WAU trend ≤ {w:.0%}")
                                               + "<br>precision %{y:.1%}, recall %{x:.1%}, lift %{customdata[1]:.2f}×<br>"
                                               "%{customdata[2]:.0f} CSM-covered accounts / quarter<extra></extra>"))
    base = float(g["base_rate"].iloc[0])
    fig.add_hline(y=base, line=dict(color=theme.AXIS, width=1, dash="dot"), annotation_text=f"base rate {pct(base, 1)}",
                  annotation_position="bottom right", annotation_font=dict(color=theme.MUTED, size=11))
    fig.add_hline(y=2 * base, line=dict(color=theme.AXIS, width=1, dash="dash"), annotation_text=f"2× base rate {pct(2 * base, 1)}",
                  annotation_position="top right", annotation_font=dict(color=theme.MUTED, size=11))

    def point(u, w):
        m = (g["utilization_threshold"] == u) & (g["w"].isna() if w is None else g["w"] == w)
        return g[m].iloc[0] if m.any() else None

    def rule_label(u, w):
        return f"utilization < {u:.0%}" + ("" if w is None else f" and WAU trend ≤ {w:.0%}")

    cp = point(*chosen)
    if cp is not None:
        fig.add_trace(go.Scatter(x=[cp["recall"]], y=[cp["precision"]], mode="markers", name="Chosen rule (02 §9.1 v1.2)",
                                 marker=dict(size=18, color="rgba(0,0,0,0)", line=dict(color=theme.INK, width=2.5)),
                                 hovertemplate=f"chosen: {rule_label(*chosen)}<extra></extra>"))
    dp = point(*DOC_DEFAULT_RULE)
    if dp is not None:
        # label in the empty band below the base-rate line (lower right of the point), so the arrow never runs along a line
        fig.add_annotation(x=dp["recall"], y=dp["precision"], text=f"v1.0 draft: {rule_label(*DOC_DEFAULT_RULE)}", showarrow=True,
                           arrowhead=2, arrowsize=0.8, arrowwidth=1, ax=110, ay=120, font=dict(size=11, color=theme.INK_2),
                           arrowcolor=theme.INK_2, xanchor="left")
    if current is not None and current != chosen:
        sp = point(*current)
        if sp is not None:
            fig.add_trace(go.Scatter(x=[sp["recall"]], y=[sp["precision"]], mode="markers", name="Slider setting",
                                     marker=dict(size=14, color=theme.INK, symbol="diamond"),
                                     hovertemplate=f"slider: {rule_label(*current)}<extra></extra>"))
    fig.update_xaxes(title_text="Recall (share of contracting account-months flagged)", tickformat=".0%", rangemode="tozero",
                     showgrid=True, gridcolor=theme.GRID)
    fig.update_yaxes(title_text="Precision (share of flags that contract)", tickformat=".0%", rangemode="tozero")
    fig.update_layout(legend=dict(orientation="h"), margin=dict(r=40))
    title = (f"Utilization alone beats the two-condition draft: at {rule_label(*chosen)}, precision {pct(cp['precision'], 0)} and "
             f"recall {pct(cp['recall'], 0)} (lift {cp['lift']:.1f}×)") if cp is not None else "Precision-recall trade-off of the early-warning rule"
    return theme.apply(fig, title, "Back-test on eligible account-months (02 §9.1); each point is one threshold pair; "
                       "adding a WAU-trend condition moves left (less recall) without moving up", height=480)


def fig_lead_time(caught: pd.DataFrame, rule: tuple[float, float | None] | None = None) -> go.Figure:
    c = caught.copy()
    c["bucket"] = np.where(c["lead"] >= 7, "7+", c["lead"].astype(int).astype(str))
    order = ["1", "2", "3", "4", "5", "6", "7+"]
    fig = go.Figure()
    colors = theme.BILLING_COLORS
    for bp in ("monthly", "annual"):
        s = c[c["billing_period"] == bp]
        cnt = s["bucket"].value_counts().reindex(order).fillna(0)
        fig.add_trace(go.Bar(x=order, y=cnt / max(len(s), 1), name=f"{bp.title()}-billed (n = {len(s):,})", marker=dict(color=colors[bp]),
                             customdata=cnt.values, hovertemplate="%{x} month-ends ahead<br>" + bp + ": %{y:.0%} of caught events (%{customdata:,.0f})<extra></extra>"))
    fig.update_layout(barmode="group", bargap=0.3)
    fig.update_xaxes(title_text="Lead time: month-ends from the first flag of the run to the contraction / churn month")
    fig.update_yaxes(tickformat=".0%", rangemode="tozero")
    med_m = c.loc[c["billing_period"] == "monthly", "lead"].median()
    med_a = c.loc[c["billing_period"] == "annual", "lead"].median()
    ge2 = (c["lead"] >= 2).mean() if len(c) else np.nan
    return theme.apply(fig, f"Median lead time {med_m:.0f} month-ends for monthly-billed accounts and {med_a:.0f} for annual; "
                            f"{pct(ge2, 0)} of caught events were flagged at least 2 month-ends ahead",
                       "Caught outcome events in the back-test, one per event (the flag run ending closest to it); annual windows run to the "
                       "renewal (≤ 6 months), so annual lead times stop at 6",
                       height=380)
