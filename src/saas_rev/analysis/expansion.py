"""Expansion signals: which Basic accounts look like upgraders, and what Customer Success should look for.

Answers scope question Q5 under the leakage and bias rules of 02_metric_definitions.md §13:
  * how upgraders differ from matched stayers in the 90 days before a voluntary Basic -> Premium upgrade;
  * whether each signal survived the 2026-04-15 Basic packaging change (advanced automations moved into Basic);
  * a simple logistic propensity model, back-tested on a purged time split, and the ranked opportunity list for CS.

Every model input is observable at the index month-end t (fct_account_month_features); the label_* columns
are used only as outcomes. Nothing here reads generator parameters.

Intervals: an account contributes up to ~10 month-end rows, so rates on account-month rows are not independent.
Bin rates, lifts, the threshold rule and the test AUC therefore carry account-level (cluster) bootstrap intervals:
accounts are resampled with replacement and the statistic is recomputed on their rows (bin edges and score
deciles stay fixed). Standardized mean differences use an ordinary bootstrap of the matched rows (one row per
upgrade / per stayer account).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.preprocessing import StandardScaler

from .. import marts, theme
from ..config import get_settings
from ..theme import money, num, pct

TOPIC = "expansion"
AS_OF = pd.Timestamp("2026-09-30")
PACKAGING_CHANGE = pd.Timestamp("2026-04-15")
FIRST_POST_MONTH = pd.Timestamp("2026-04-30")      # first month-end whose 13-week window contains post-change weeks
MIN_TENURE = 3                                      # months since first paid month-end (02 §13)
HORIZON_DAYS = 90
# Purged time split. A row at month-end t carries a label window (t, t + 90 days], so the three month-ends before the
# test period are held out ("embargo"): otherwise an upgrade in Jan–Mar 2026 would be a training label (from the
# Oct–Dec 2025 rows of the same account) and a test label at once, and the back-test would look better than it is.
TRAIN_END = pd.Timestamp("2025-09-30")              # last training month-end
TEST_START, TEST_END = pd.Timestamp("2026-01-31"), pd.Timestamp("2026-06-30")
EMBARGO_MONTHS = ("2025-10-31", "2025-11-30", "2025-12-31")   # held out: neither trained on nor scored
SPLIT_NOTE = ("trained on month-ends to Sep 2025 (Oct – Dec 2025 held out, so no training label window reaches "
              "the test period), tested on Jan – Jun 2026")
MIN_BIN_SHARE = 0.05            # a signal bin thinner than this share of rows (e.g. the 1% of rows above a 100%
                                # peak-utilization mass point) is merged into its lower neighbour: a "quintile" of
                                # 250 rows is not a CS rule, and the merged bin is the rule CS would state ("≥ 100%")
MIN_UPGRADES_FOR_LIFT = 5       # a bin with fewer upgrades than this in a period is shown as "not estimable"
N_BOOT_BINS = 1000              # account-level bootstrap draws for bin rates / lifts and the threshold rule
TENURE_BAND_EDGES = [2, 6, 12, 24, 10 ** 6]
TENURE_BAND_LABELS = ["3-6", "7-12", "13-24", "25+"]
PRICE_DELTA = {"monthly": 35.0 - 15.0, "annual": (336.0 - 144.0) / 12}   # Premium - Basic list price per seat-month
CAPACITIES = (100, 200)                             # CS accounts worked per month, reported in key numbers
CAPACITY_DEFAULT = 100
INTEGRATION_CAP_SYNCS = 180.0   # Basic sync volume has a hard ceiling near 200/week (cluster 190-210 = all 5 integrations in use)
N_BOOT_SMD = 1000
N_BOOT_MODEL = 300
REGULARIZATION_C = 1.0          # sklearn default L2 strength; no tuning (the brief asks for signals, not a tuned model)

# ---------------------------------------------------------------------------------- signals
# name: full label (tables, axis titles); short: chart titles and evidence text; tile: <= 16 characters for a KPI value
SIGNALS: dict[str, dict] = {
    "gate_hits_per_wau": dict(name="Premium-feature paywall hits per active user", short="Paywall hits / active user", tile="Paywall hits", fmt="num2"),
    "gate_user_share": dict(name="Share of active users who hit the Premium paywall", short="Users hitting paywall", tile="Paywall users", fmt="pct"),
    "seat_growth_3m": dict(name="Seat growth vs 3 months ago", short="Seat growth, 3 months", tile="Seat growth", fmt="pct"),
    "invites_per_seat_13w": dict(name="Teammate invitations per seat (13 weeks)", short="Invites / seat", tile="Invites / seat", fmt="num2"),
    "utilization_13w": dict(name="Seat utilization: active users / seats (13 weeks)", short="Seat utilization", tile="Utilization", fmt="pct"),
    "max_weekly_utilization_13w": dict(name="Peak weekly seat utilization (13 weeks)", short="Peak weekly utilization", tile="Peak utilization", fmt="pct"),
    "wau_trend": dict(name="Weekly-active-user trend (last 4 weeks vs prior 9)", short="WAU trend", tile="WAU trend", fmt="pct"),
    "automation_per_wau": dict(name="Automation runs per active user", short="Automations / active user", tile="Automations", fmt="num2"),
    "reports_per_wau": dict(name="Analytics report views per active user", short="Report views / active user", tile="Report views", fmt="num2"),
    "core_actions_per_wau": dict(name="Core work actions per active user", short="Core actions / active user", tile="Core actions", fmt="num1"),
    "integration_syncs_per_week": dict(name="Integration syncs per week", short="Integration syncs / week", tile="Integrations", fmt="num0"),
    "seats": dict(name="Seats on the subscription", short="Seats", tile="Seats", fmt="num0"),
}
SIGNAL_COLS = list(SIGNALS)

# Model features. The three paywall / automation signals enter as within-month percentile ranks because the
# April 2026 packaging change moved their levels for every Basic account (paywall hits x0.4, automation x3);
# a percentile keeps the ranking information and is what CS would use ("top decile this month").
PCTL_SIGNALS = ["gate_hits_per_wau", "gate_user_share", "automation_per_wau"]
LOG_SIGNALS = ["invites_per_seat_13w", "reports_per_wau", "core_actions_per_wau", "integration_syncs_per_week", "seats"]
RAW_SIGNALS = ["utilization_13w", "max_weekly_utilization_13w", "seat_growth_3m", "wau_trend"]
STATIC_FEATURES = ["log_tenure", "prior_higher_tier", "is_annual", "is_mid_market_plus", "is_paid_social"]
FEATURE_LABELS = {
    "gate_hits_per_wau_pctl": "Paywall hits / active user (percentile in month)",
    "gate_user_share_pctl": "Users hitting paywall (percentile in month)",
    "automation_per_wau_pctl": "Automations / active user (percentile in month)",
    "log_gate_hits_per_wau": "Paywall hits / active user (log level)",
    "log_gate_user_share": "Users hitting paywall (log level)",
    "log_automation_per_wau": "Automations / active user (log level)",
    "log_invites_per_seat_13w": "Invites / seat (log)",
    "log_reports_per_wau": "Report views / active user (log)",
    "log_core_actions_per_wau": "Core actions / active user (log)",
    "log_integration_syncs_per_week": "Integration syncs / week (log)",
    "log_seats": "Seats (log)",
    "utilization_13w": "Seat utilization, 13 weeks",
    "max_weekly_utilization_13w": "Peak weekly utilization, 13 weeks",
    "seat_growth_3m": "Seat growth vs 3 months ago",
    "wau_trend": "WAU trend (4 vs 9 weeks)",
    "log_tenure": "Tenure, months (log)",
    "prior_higher_tier": "Was on Premium / Enterprise before",
    "is_annual": "Annual billing",
    "is_mid_market_plus": "Mid-Market / Enterprise segment",
    "is_paid_social": "Acquired via Paid Social",
    "post_change": "After the Apr 2026 packaging change",
}
# feature -> the signal it is built from (for reason codes); static features carry no reason code
FEATURE_SIGNAL = {**{f"{s}_pctl": s for s in PCTL_SIGNALS}, **{f"log_{s}": s for s in LOG_SIGNALS}, **{s: s for s in RAW_SIGNALS},
                  "prior_higher_tier": "prior_higher_tier"}

REASON_CODES = {
    "GATE_HITS_TOP_DECILE": "Premium-feature paywall hits per active user in the top 10% of Basic accounts this month",
    "GATE_HITS_ABOVE_MEDIAN": "Premium-feature paywall hits per active user above the Basic median this month",
    "GATE_USERS_TOP_DECILE": "Share of active users hitting the Premium paywall in the top 10% of Basic accounts this month",
    "GATE_USERS_ABOVE_MEDIAN": "Share of active users hitting the Premium paywall above the Basic median this month",
    "TEAM_GROWTH_10PCT_3M": "Seats up 10% or more vs 3 months ago",
    "TEAM_GROWING": "Seats up vs 3 months ago, by less than 10%",
    "UTIL_ABOVE_90": "Seat utilization (active users / seats) of 90% or more over the last 13 weeks",
    "UTIL_ABOVE_75": "Seat utilization of 75-90% over the last 13 weeks",
    "FULL_SEATS_WEEK": "At least one week in the last 13 with active users at or above purchased seats",
    "INVITES_TOP_DECILE": "Teammate invitations per seat in the top 10% of Basic accounts this month",
    "INVITES_ABOVE_MEDIAN": "Teammate invitations per seat above the Basic median this month",
    "WAU_UP_15PCT": "Weekly active users up 15% or more (last 4 weeks vs the 9 before)",
    "WAU_RISING": "Weekly active users up, by less than 15% (last 4 weeks vs the 9 before)",
    "AUTOMATION_TOP_DECILE": "Automation runs per active user in the top 10% of Basic accounts this month",
    "AUTOMATION_ABOVE_MEDIAN": "Automation runs per active user above the Basic median this month",
    "REPORTS_TOP_DECILE": "Analytics report views per active user in the top 10% of Basic accounts this month",
    "REPORTS_ABOVE_MEDIAN": "Analytics report views per active user above the Basic median this month",
    "CORE_ACTIONS_TOP_DECILE": "Core work actions per active user in the top 10% of Basic accounts this month",
    "CORE_ACTIONS_ABOVE_MEDIAN": "Core work actions per active user above the Basic median this month",
    "AT_BASIC_INTEGRATION_CAP": f"Integration sync volume at the Basic ceiling (>= {INTEGRATION_CAP_SYNCS:.0f} syncs/week, "
                                "consistent with all 5 Basic integrations in use)",
    "INTEGRATIONS_ABOVE_MEDIAN": "Integration syncs per week above the Basic median this month",
    "SEATS_20_PLUS": "20 or more seats on Basic (a large team on the entry plan)",
    "SEATS_ABOVE_MEDIAN": "More seats than the median Basic account this month",
    "PRIOR_PREMIUM": "Was on Premium or Enterprise before (already knows the features)",
}


def fmt_value(signal: str, v: float) -> str:
    f = SIGNALS[signal]["fmt"]
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "–"
    if f == "pct":
        return pct(v, 0)
    return num(v, int(f[-1]))


def _uniq(cols: list[str]) -> list[str]:
    return list(dict.fromkeys(cols))


def _tenure_band(t: pd.Series) -> pd.Series:
    return pd.cut(t, bins=TENURE_BAND_EDGES, labels=TENURE_BAND_LABELS).astype(str)


def _add_percentiles(df: pd.DataFrame) -> pd.DataFrame:
    for c in PCTL_SIGNALS:
        df[f"{c}_pctl"] = df.groupby("month_end")[c].rank(pct=True)
    return df


# =============================================================================== population and labels
POP_COLS = ["customer_id", "month_end", "period", "split", "tenure_months", "tenure_band", "segment", "industry",
            "acquisition_channel", "billing_period", "seats", "mrr", "prior_higher_tier", "label", "forced", "churn_in_window"]


def population() -> pd.DataFrame:
    """Eligible rows (02 §13): paying on Basic at month-end t, tenure >= 3 months, 90-day window fully observed.
    label = voluntary upgrade effective in (t, t + 90 days]. Rows whose window holds a forced (seat-cap) upgrade
    are flagged `forced` and left out of the signal tables; churn within the window stays in the ranking population
    as label 0 (it is not known at t) but never serves as a matched control."""
    f = marts.load("fct_account_month_features")
    p = f[(f["plan_id"] == "basic") & (f["tenure_months"] >= MIN_TENURE) & f["label_upgrade_window_complete"]].copy()
    p["label"] = p["label_voluntary_upgrade_90d"].astype(int)
    p["forced"] = p["label_forced_upgrade_90d"].astype(bool)
    p["churn_in_window"] = p["label_churn_3m"].astype(bool)
    p["period"] = np.where(p["month_end"] >= FIRST_POST_MONTH, "post", "pre")
    p["split"] = np.select([p["month_end"] <= TRAIN_END, (p["month_end"] >= TEST_START) & (p["month_end"] <= TEST_END)],
                           ["train", "test"], "embargo")
    p["tenure_band"] = _tenure_band(p["tenure_months"])
    p = _add_percentiles(p)
    p = p.sort_values(["customer_id", "month_end"], kind="stable")   # row order must not depend on how the mart was written
    return p[_uniq(POP_COLS + SIGNAL_COLS + [f"{c}_pctl" for c in PCTL_SIGNALS])].reset_index(drop=True)


def upgrade_events() -> pd.DataFrame:
    """Every Basic -> higher-tier plan change, with the last month-end before it and why it is (not) in the comparison."""
    cc = marts.load("fct_change_components")
    up = cc[(cc["change_type"] == "upgrade") & (cc["from_plan_id"] == "basic")].copy()
    up["voluntary"] = up["change_source"] != "system"
    up["pre_month_end"] = up["effective_date"] - pd.offsets.MonthEnd(1)   # the month-end snapshot strictly before the change
    f = marts.load("fct_account_month_features")[["customer_id", "month_end", "plan_id", "tenure_months",
                                                   "label_upgrade_window_complete"]]
    m = up.merge(f.rename(columns={"month_end": "pre_month_end"}), on=["customer_id", "pre_month_end"], how="left")
    complete = m["label_upgrade_window_complete"].fillna(False).astype(bool)
    m["exclusion"] = np.select(
        [~m["voluntary"], m["plan_id"].isna(), m["plan_id"] != "basic", m["tenure_months"] < MIN_TENURE, ~complete],
        ["forced (seat cap)", "no paid month-end before the upgrade", "not on Basic at the prior month-end",
         "tenure under 3 months", "90-day window not complete"], "eligible")
    return m[["change_id", "customer_id", "effective_date", "pre_month_end", "change_source", "voluntary", "from_seats",
              "to_seats", "from_mrr", "to_mrr", "delta_mrr", "exclusion"]].sort_values("effective_date").reset_index(drop=True)


def comparison_groups(pop: pd.DataFrame | None = None) -> pd.DataFrame:
    """Upgraders = the last month-end before each eligible voluntary upgrade (one row per upgrade).
    Stayers = one eligible month-end per account that neither upgraded nor churned in its 90-day window, drawn
    1:1 from the same calendar-month x tenure-band stratum as the upgraders (fixed seed); stayer accounts never
    appear in the upgrader group."""
    pop = population() if pop is None else pop
    ev = upgrade_events()
    ev = ev[ev["exclusion"] == "eligible"][["change_id", "customer_id", "pre_month_end"]]
    upg = ev.merge(pop, left_on=["customer_id", "pre_month_end"], right_on=["customer_id", "month_end"], how="inner")
    upg = upg.drop(columns="pre_month_end")
    upg["group"] = "upgrader"
    cand = pop[(pop["label"] == 0) & ~pop["forced"] & ~pop["churn_in_window"] & ~pop["customer_id"].isin(upg["customer_id"])]
    cand = cand.sort_values(["customer_id", "month_end"], kind="stable")
    cand = cand.sample(frac=1.0, random_state=get_settings().seed)   # one fixed shuffle; the draw takes the first rows
    need = upg.groupby(["month_end", "tenure_band"]).size()
    avail = cand.groupby(["month_end", "tenure_band"])["customer_id"].nunique()
    order = sorted(need.index, key=lambda s: (avail.get(s, 0) / need[s], s))   # scarcest strata first
    used: set = set()
    rows = []
    for s in order:
        c = cand[(cand["month_end"] == s[0]) & (cand["tenure_band"] == s[1])]
        take = c[~c["customer_id"].isin(used)].head(int(need[s]))
        used.update(take["customer_id"])
        rows.append(take)
    stay = pd.concat(rows, ignore_index=True)
    stay["group"] = "stayer"
    stay["change_id"] = None
    out = pd.concat([upg, stay], ignore_index=True)
    return out[_uniq(["group", "change_id"] + POP_COLS + SIGNAL_COLS)]


# =============================================================================== signal statistics
def _smd(u: np.ndarray, v: np.ndarray) -> float:
    return float((u.mean() - v.mean()) / np.sqrt((u.var(ddof=1) + v.var(ddof=1)) / 2))


def _wilson(k, n, z: float = 1.96):
    k, n = np.asarray(k, float), np.asarray(n, float)
    p = np.divide(k, n, out=np.zeros_like(k), where=n > 0)
    denom = 1 + z ** 2 / n
    centre = (p + z ** 2 / (2 * n)) / denom
    half = z * np.sqrt(p * (1 - p) / n + z ** 2 / (4 * n ** 2)) / denom
    return centre - half, centre + half


def _quintile_span(start: float, end: float) -> str:
    a = int(np.floor(start * 5 + 1e-9)) + 1
    b = int(np.ceil(end * 5 - 1e-9))
    return f"Q{a}" if a >= b else f"Q{a}–Q{b}"


def _bin_edges(x: pd.Series) -> np.ndarray:
    """Quintile edges of a signal, left-closed so a row tied at an edge falls in the upper bin ("signal >= threshold").
    A value that holds 20% or more of the rows (e.g. zero seat growth, 100% peak utilization) gets a bin of its own,
    so the bins above and below it keep their meaning; duplicate edges collapse into fewer bins. A bin thinner than
    MIN_BIN_SHARE (the sliver above a mass point) is merged into the bin below it (a thin bottom bin into the one above)."""
    edges = set(x.quantile([0.2, 0.4, 0.6, 0.8]).to_numpy()) | {float(x.min()), float(np.nextafter(x.max(), np.inf))}
    shares = x.value_counts(normalize=True)
    for m in shares[shares >= 0.2].index:
        edges |= {float(m), float(np.nextafter(m, np.inf))}
    edges = np.array(sorted(edges))
    while len(edges) > 2:
        share = pd.cut(x, bins=edges, right=False, labels=False).value_counts(normalize=True).reindex(range(len(edges) - 1), fill_value=0.0)
        thin = share[share < MIN_BIN_SHARE]
        if thin.empty:
            break
        i = int(thin.index[-1])                      # thinnest-from-the-top first; the loop re-checks the rest
        edges = np.delete(edges, i if i > 0 else 1)  # drop the edge below bin i (or above it, for the bottom bin)
    return edges


def _cluster_bootstrap_bins(account: np.ndarray, member: np.ndarray, label: np.ndarray, n_bins: int,
                            n_boot: int = N_BOOT_BINS) -> tuple[np.ndarray, np.ndarray]:
    """Account-level bootstrap of per-bin upgrade rates and of their lift over the pooled base rate.
    `member` is the bin index of each row (bin membership stays fixed; accounts are resampled with replacement).
    Returns two (n_boot, n_bins) arrays: rates and lifts. Vectorized: the draw is a multinomial count per account,
    so a draw costs one matrix product."""
    ids, inv = np.unique(account, return_inverse=True)          # sorted ids: the draw never depends on row order
    n_acc = len(ids)
    counts = np.zeros((n_acc, n_bins)), np.zeros((n_acc, n_bins))
    np.add.at(counts[0], (inv, member), 1.0)
    np.add.at(counts[1], (inv, member), label.astype(float))
    rng = np.random.default_rng(get_settings().seed)
    w = rng.multinomial(n_acc, np.full(n_acc, 1.0 / n_acc), size=n_boot).astype(float)   # times each account is drawn
    n_b, u_b = w @ counts[0], w @ counts[1]
    with np.errstate(divide="ignore", invalid="ignore"):
        rates = np.where(n_b > 0, u_b / n_b, np.nan)
        base = u_b.sum(axis=1, keepdims=True) / n_b.sum(axis=1, keepdims=True)
        lifts = rates / base
    return rates, lifts


def _interval(draws: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    lo, hi = np.nanpercentile(draws, [2.5, 97.5], axis=0)
    return lo, hi


def quintiles(pop: pd.DataFrame, signal: str, period: str = "all") -> pd.DataFrame:
    """90-day upgrade rate by quintile of a signal on the full eligible population (forced rows excluded).
    Signals with a mass point (e.g. 78% of rows have zero seat growth) get fewer than five bins; the `quintile_span`
    column says which quintiles a bin covers (see _bin_edges). Bin rates and lifts carry an account-level bootstrap
    95% interval (N_BOOT_BINS draws; bin edges fixed), so the ~10 rows an account contributes do not count as
    independent evidence. Rows with a missing signal value are left out (`n_missing`)."""
    d = pop[~pop["forced"]]
    if period in ("pre", "post"):
        d = d[d["period"] == period]
    n_missing = int(d[signal].isna().sum())
    d = d[d[signal].notna()]
    x = d[signal]
    edges = _bin_edges(x)
    cats = pd.cut(x, bins=edges, right=False, labels=False) if len(edges) > 2 else pd.Series([0] * len(x), index=x.index)
    g = d.groupby(cats, observed=True).agg(n=("label", "size"), upgrades=("label", "sum"), lo=(signal, "min"),
                                           hi=(signal, "max")).reset_index(drop=True)
    base = d["label"].mean()
    g["rate"] = g["upgrades"] / g["n"]
    g["lift"] = g["rate"] / base
    rates, lifts = _cluster_bootstrap_bins(d["customer_id"].to_numpy(), cats.to_numpy().astype(int), d["label"].to_numpy(), len(g))
    g["rate_lo"], g["rate_hi"] = _interval(rates)
    g["lift_lo"], g["lift_hi"] = _interval(lifts)
    g["share_of_rows"] = g["n"] / g["n"].sum()
    cum = g["share_of_rows"].cumsum()
    g["quintile_span"] = [_quintile_span(a, b) for a, b in zip(cum - g["share_of_rows"], cum)]
    g["bin"] = [f"{fmt_value(signal, lo)} – {fmt_value(signal, hi)}" for lo, hi in zip(g["lo"], g["hi"])]
    g.insert(0, "signal", signal)
    g.insert(1, "period", period)
    g["base_rate"] = base
    g["n_missing"] = n_missing
    g["estimable"] = g["upgrades"] >= MIN_UPGRADES_FOR_LIFT
    return g


def rule_threshold(x: pd.Series, top_share: float) -> float:
    """Signal value at which a 'top `top_share` of account-months' rule starts (dashboard control; rows tied at the
    threshold count as above it). When the value at that quantile is a mass point (20%+ of rows, e.g. zero seat
    growth) the rule starts just above it, so it does not sweep in the whole mass — the same convention as the
    quintile bins — unless almost nothing (< MIN_BIN_SHARE) lies above the mass point (100% peak utilization), in
    which case the mass point itself is the rule. The share actually captured is reported with the rule."""
    x = x.dropna()
    t = float(x.quantile(1 - top_share))
    if (x == t).mean() >= 0.2:
        above = x[x > t]
        if len(above) / len(x) >= MIN_BIN_SHARE:
            t = float(above.min())
    return t


def threshold_rule(pop: pd.DataFrame, signal: str, threshold: float, period: str = "all") -> dict:
    """Upgrade rate above vs below a threshold of one signal (dashboard control). Rows with a missing value for the
    signal cannot be placed on either side and are left out (`n_missing`). The rate above and the lift carry an
    account-level bootstrap 95% interval."""
    d = pop[~pop["forced"]]
    if period in ("pre", "post"):
        d = d[d["period"] == period]
    n_missing = int(d[signal].isna().sum())
    d = d[d[signal].notna()]
    above = d[signal] >= threshold
    base = d["label"].mean()
    n_above, n_up = int(above.sum()), int(d["label"].sum())
    rate_above = d.loc[above, "label"].mean() if n_above else np.nan
    rate_below = d.loc[~above, "label"].mean() if n_above < len(d) else np.nan
    rates, lifts = _cluster_bootstrap_bins(d["customer_id"].to_numpy(), above.to_numpy().astype(int), d["label"].to_numpy(), 2)
    rate_lo, rate_hi = _interval(rates[:, 1])
    lift_lo, lift_hi = _interval(lifts[:, 1])
    return dict(period=period, threshold=threshold, n=len(d), n_missing=n_missing, n_accounts=int(d["customer_id"].nunique()),
                n_above=n_above, share_above=n_above / len(d), rate_above=rate_above, rate_above_lo=float(rate_lo),
                rate_above_hi=float(rate_hi), rate_below=rate_below, base_rate=base,
                lift=rate_above / base if base else np.nan, lift_lo=float(lift_lo), lift_hi=float(lift_hi),
                upgrades_above=int(d.loc[above, "label"].sum()), upgrades_total=n_up,
                recall=d.loc[above, "label"].sum() / n_up if n_up else np.nan)


def signal_table(comp: pd.DataFrame | None = None, pop: pd.DataFrame | None = None,
                 n_boot: int = N_BOOT_SMD) -> pd.DataFrame:
    """One row per signal: medians for both groups, standardized mean difference with a bootstrap 95% interval,
    and the top-quintile lift on the full population (all / pre / post)."""
    pop = population() if pop is None else pop
    comp = comparison_groups(pop) if comp is None else comp
    rng = np.random.default_rng(get_settings().seed)
    rows = []
    for s, meta in SIGNALS.items():
        u = comp.loc[comp["group"] == "upgrader", s].dropna().to_numpy(float)
        v = comp.loc[comp["group"] == "stayer", s].dropna().to_numpy(float)
        bu = u[rng.integers(0, len(u), (n_boot, len(u)))]
        bv = v[rng.integers(0, len(v), (n_boot, len(v)))]
        boots = (bu.mean(1) - bv.mean(1)) / np.sqrt((bu.var(1, ddof=1) + bv.var(1, ddof=1)) / 2)
        lo, hi = np.percentile(boots, [2.5, 97.5])
        q = {p: quintiles(pop, s, p).iloc[-1] for p in ("all", "pre", "post")}
        rows.append(dict(
            signal=s, name=meta["name"], short=meta["short"], n_upgraders=len(u), n_stayers=len(v),
            median_upgraders=float(np.median(u)), median_stayers=float(np.median(v)),
            mean_upgraders=float(u.mean()), mean_stayers=float(v.mean()),
            smd=_smd(u, v), smd_lo=float(lo), smd_hi=float(hi),
            top_quintile_rate=float(q["all"]["rate"]), top_quintile_lift=float(q["all"]["lift"]),
            top_quintile_lift_lo=float(q["all"]["lift_lo"]), top_quintile_lift_hi=float(q["all"]["lift_hi"]),
            top_quintile_lift_pre=float(q["pre"]["lift"]), top_quintile_lift_pre_lo=float(q["pre"]["lift_lo"]),
            top_quintile_lift_pre_hi=float(q["pre"]["lift_hi"]),
            top_quintile_lift_post=float(q["post"]["lift"]), top_quintile_lift_post_lo=float(q["post"]["lift_lo"]),
            top_quintile_lift_post_hi=float(q["post"]["lift_hi"]),
            top_quintile_n_post=int(q["post"]["n"]), top_quintile_upgrades_post=int(q["post"]["upgrades"])))
    return pd.DataFrame(rows).sort_values("smd", ascending=False).reset_index(drop=True)


# =============================================================================== drift
def drift_weekly() -> pd.DataFrame:
    """Weekly Premium-paywall hits and automation runs per active user across Basic accounts (complete weeks)."""
    u = marts.load("fct_account_usage_weekly")
    b = u[(u["plan_id"] == "basic") & u["is_complete_week"] & u["is_live_at_week_end"] & (u["wau"] > 0)].copy()
    b["gate_per_wau"] = b["premium_gate_hits"] / b["wau"]
    b["auto_per_wau"] = b["automation_runs"] / b["wau"]
    g = b.groupby("week_start").agg(accounts=("customer_id", "size"), gate_hits=("premium_gate_hits", "sum"),
                                    wau=("wau", "sum"), automation_runs=("automation_runs", "sum"),
                                    gate_hits_per_wau_median=("gate_per_wau", "median"),
                                    automation_per_wau_median=("auto_per_wau", "median")).reset_index()
    g["gate_hits_per_wau"] = g["gate_hits"] / g["wau"]
    g["automation_per_wau"] = g["automation_runs"] / g["wau"]
    return g


DRIFT_PRE = ("pre: Oct 2025 – Mar 2026", "2025-10-31", "2026-03-31")
DRIFT_POST = ("post: Jul – Sep 2026", "2026-07-31", "2026-09-30")
DRIFT_SIGNALS = ("gate_hits_per_wau", "gate_user_share", "automation_per_wau")


def _drift_base() -> pd.DataFrame:
    """Basic accounts with tenure >= 3 at each month-end: the population behind the drift key numbers (every
    month-end, including those whose 90-day label window is not complete)."""
    f = marts.load("fct_account_month_features")
    return f[(f["plan_id"] == "basic") & (f["tenure_months"] >= MIN_TENURE)]


def drift_monthly() -> pd.DataFrame:
    """Mean and median of each packaging-sensitive signal (the 13-week rate per account) at every month-end."""
    b = _drift_base()
    g = b.groupby("month_end").agg(accounts=("customer_id", "size"), **{f"{s}_mean": (s, "mean") for s in DRIFT_SIGNALS},
                                   **{f"{s}_median": (s, "median") for s in DRIFT_SIGNALS}).reset_index()
    g["drift_window"] = np.select([(g["month_end"] >= DRIFT_PRE[1]) & (g["month_end"] <= DRIFT_PRE[2]),
                                   (g["month_end"] >= DRIFT_POST[1]) & (g["month_end"] <= DRIFT_POST[2])],
                                  [DRIFT_PRE[0], DRIFT_POST[0]], "")
    return g


def drift_distribution() -> pd.DataFrame:
    """Distribution of the packaging-sensitive signals for Basic accounts (tenure >= 3) before the change and in
    the months whose 13-week windows lie entirely after it. This table is the source of the gate_hits_* /
    automation_* key numbers and of the fig_gate_drift title."""
    b = _drift_base()
    rows = []
    for label, a, z in (DRIFT_PRE, DRIFT_POST):
        d = b[(b["month_end"] >= a) & (b["month_end"] <= z)]
        for s in DRIFT_SIGNALS:
            x = d[s].dropna()
            qs = x.quantile([0.1, 0.25, 0.5, 0.75, 0.9])
            rows.append(dict(period=label, signal=s, name=SIGNALS[s]["name"], account_months=len(x), mean=x.mean(),
                             p10=qs[0.1], p25=qs[0.25], p50=qs[0.5], p75=qs[0.75], p90=qs[0.9], share_zero=(x == 0).mean()))
    return pd.DataFrame(rows)


# =============================================================================== propensity model
def feature_frame(d: pd.DataFrame, fill: dict | None = None, raw_levels: bool = False) -> tuple[pd.DataFrame, dict]:
    """Model matrix from a feature-table slice. `fill` (training medians) makes scoring deterministic."""
    X = pd.DataFrame(index=d.index)
    fill = dict(fill) if fill else {}
    for c in PCTL_SIGNALS:
        if raw_levels:
            X[f"log_{c}"] = np.log1p(d[c])
        else:
            X[f"{c}_pctl"] = d[f"{c}_pctl"]
    for c in LOG_SIGNALS:
        X[f"log_{c}"] = np.log1p(d[c])
    for c in RAW_SIGNALS:
        X[c] = d[c]
    X["log_tenure"] = np.log(d["tenure_months"].astype(float))
    X["prior_higher_tier"] = d["prior_higher_tier"].astype(float)
    X["is_annual"] = (d["billing_period"] == "annual").astype(float)
    X["is_mid_market_plus"] = (d["segment"] != "SMB").astype(float)
    X["is_paid_social"] = (d["acquisition_channel"] == "Paid Social").astype(float)
    for c in X.columns:
        if c not in fill:
            fill[c] = 0.0 if c in ("seat_growth_3m", "wau_trend") else (0.5 if c.endswith("_pctl") else float(X[c].median()))
        X[c] = X[c].fillna(fill[c])
    return X, fill


def _account_weights(account: pd.Series) -> np.ndarray:
    return (1.0 / account.groupby(account).transform("size")).to_numpy(float)


def _prior_correction(logits: np.ndarray, target: float) -> float:
    """Intercept shift so that the mean predicted probability equals `target` (bisection; monotone)."""
    lo, hi = -10.0, 10.0
    for _ in range(80):
        mid = (lo + hi) / 2
        if (1 / (1 + np.exp(-(logits + mid)))).mean() < target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def fit_model(X: pd.DataFrame, y: np.ndarray, account: pd.Series, fill: dict) -> dict:
    """Standardized L2 logistic regression (C = 1, class_weight None), rows weighted 1 / rows per account.
    The weights give every account one vote, which centres the intercept on the per-account prevalence; the
    intercept is then re-centred on the row-level base rate (one parameter), since CS lists are drawn per month."""
    scaler = StandardScaler().fit(X)
    Z = scaler.transform(X)
    w = _account_weights(account)
    model = LogisticRegression(C=REGULARIZATION_C, solver="lbfgs", max_iter=2000, class_weight=None)
    model.fit(Z, y, sample_weight=w)
    logits = Z @ model.coef_[0] + model.intercept_[0]
    delta = _prior_correction(logits, float(y.mean()))
    model.intercept_[0] += delta
    return dict(scaler=scaler, model=model, columns=list(X.columns), fill=fill, prior_correction=delta,
                n_rows=len(y), n_accounts=int(account.nunique()), base_rate=float(y.mean()))


def predict(bundle: dict, X: pd.DataFrame) -> np.ndarray:
    return bundle["model"].predict_proba(bundle["scaler"].transform(X[bundle["columns"]]))[:, 1]


def contributions(bundle: dict, X: pd.DataFrame) -> pd.DataFrame:
    """Per-row contribution of each feature to the log-odds relative to the average training row (z x coef)."""
    Z = bundle["scaler"].transform(X[bundle["columns"]])
    return pd.DataFrame(Z * bundle["model"].coef_[0], index=X.index, columns=bundle["columns"])


def _cluster_indices(account: np.ndarray, n_boot: int):
    """Yield row-index arrays for `n_boot` account-level bootstrap draws (accounts resampled with replacement,
    every row of a drawn account included). Account ids are sorted, so the draw never depends on row order."""
    rng = np.random.default_rng(get_settings().seed)
    ids, inv = np.unique(account, return_inverse=True)
    order = np.argsort(inv, kind="stable")
    starts = np.searchsorted(inv[order], np.arange(len(ids)))
    ends = np.append(starts[1:], len(order))
    for _ in range(n_boot):
        samp = rng.integers(0, len(ids), len(ids))
        yield np.concatenate([order[starts[i]:ends[i]] for i in samp])


def odds_ratios(bundle: dict, X: pd.DataFrame, y: np.ndarray, account: pd.Series, n_boot: int = N_BOOT_MODEL) -> pd.DataFrame:
    """Odds ratio per 1 SD of each feature with an account-level (cluster) bootstrap 95% interval."""
    Z = bundle["scaler"].transform(X[bundle["columns"]])
    w = _account_weights(account)
    coefs = []
    for idx in _cluster_indices(account.to_numpy(), n_boot):
        m = LogisticRegression(C=REGULARIZATION_C, solver="lbfgs", max_iter=2000)
        m.fit(Z[idx], y[idx], sample_weight=w[idx])
        coefs.append(m.coef_[0])
    coefs = np.array(coefs)
    lo, hi = np.percentile(coefs, [2.5, 97.5], axis=0)
    out = pd.DataFrame({"feature": bundle["columns"], "label": [FEATURE_LABELS.get(c, c) for c in bundle["columns"]],
                        "coef": bundle["model"].coef_[0], "coef_lo": lo, "coef_hi": hi})
    out["odds_ratio"], out["or_lo"], out["or_hi"] = np.exp(out["coef"]), np.exp(out["coef_lo"]), np.exp(out["coef_hi"])
    out["sd"] = bundle["scaler"].scale_
    return out.sort_values("coef", ascending=False).reset_index(drop=True)


def capacity_metrics(ts: pd.DataFrame, k: int, period: str = "all") -> dict:
    """Precision / recall when CS works the top-k scored accounts each month, averaged over the period's months."""
    d = ts if period == "all" else ts[ts["period"] == period]
    rows = []
    for me, g in d.groupby("month_end"):
        top = g.nlargest(min(k, len(g)), "score")
        rows.append(dict(month_end=me, n=len(g), worked=len(top), upgrades_total=int(g["label"].sum()),
                         captured=int(top["label"].sum()), precision=top["label"].mean(),
                         recall=top["label"].sum() / max(g["label"].sum(), 1),
                         mrr_covered=float(top.loc[top["label"] == 1, "incremental_mrr"].sum()),
                         base_rate=g["label"].mean()))
    m = pd.DataFrame(rows)
    base = d["label"].mean()
    return dict(period=period, k=k, months=len(m), precision=float(m["precision"].mean()), recall=float(m["recall"].mean()),
                upgrades_per_month=float(m["captured"].mean()), upgrades_total_per_month=float(m["upgrades_total"].mean()),
                mrr_covered_per_month=float(m["mrr_covered"].mean()), base_rate=float(base),
                lift=float(m["precision"].mean() / base) if base else np.nan)


def capacity_interval(ts: pd.DataFrame, k: int, period: str = "all", n_boot: int = N_BOOT_MODEL) -> dict:
    """Account-level bootstrap 95% interval for precision (and lift) when the top-k accounts are worked each month:
    accounts are resampled with replacement (all their month-ends together), each month's list is re-ranked on the
    resampled rows, and precision is averaged over the months as in capacity_metrics."""
    d = (ts if period == "all" else ts[ts["period"] == period]).reset_index(drop=True)
    acct, me = d["customer_id"].to_numpy(), d["month_end"].to_numpy()
    score, label = d["score"].to_numpy(), d["label"].to_numpy()
    precs, lifts = [], []
    for idx in _cluster_indices(acct, n_boot):
        m_, s_, l_ = me[idx], score[idx], label[idx]
        order = np.lexsort((-s_, m_))                    # by month, then score descending
        m_o, l_o = m_[order], l_[order]
        _, first = np.unique(m_o, return_index=True)
        bounds = list(first) + [len(m_o)]
        p_m = [l_o[a:min(a + k, b)].mean() for a, b in zip(bounds[:-1], bounds[1:])]
        base = l_.mean()
        precs.append(float(np.mean(p_m)))
        lifts.append(float(np.mean(p_m)) / base if base else np.nan)
    lo, hi = np.percentile(precs, [2.5, 97.5])
    llo, lhi = np.nanpercentile(lifts, [2.5, 97.5])
    return {"precision_lo": float(lo), "precision_hi": float(hi), "lift_lo": float(llo), "lift_hi": float(lhi)}


def capture_curve(ts: pd.DataFrame, ks: np.ndarray | None = None) -> pd.DataFrame:
    """Share of the month's upgrades captured when the top-k accounts are worked, averaged over months per period."""
    ks = np.arange(0, 1001, 10) if ks is None else ks
    rows = []
    for period in ("pre", "post", "all"):
        d = ts if period == "all" else ts[ts["period"] == period]
        shares, precs, sizes = [], [], []
        for _, g in d.groupby("month_end"):
            s = g.sort_values("score", ascending=False)["label"].to_numpy()
            cum = np.concatenate([[0], np.cumsum(s)])
            kk = np.minimum(ks, len(s))
            shares.append(cum[kk] / max(s.sum(), 1))
            precs.append(cum[kk] / np.maximum(kk, 1))
            sizes.append(len(s))
        precision = np.mean(precs, axis=0)
        precision[ks == 0] = np.nan
        rows.append(pd.DataFrame({"period": period, "k": ks, "share_captured": np.mean(shares, axis=0),
                                  "precision": precision, "random": ks / np.mean(sizes)}))
    return pd.concat(rows, ignore_index=True)


def calibration_table(ts: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for period in ("all", "pre", "post"):
        d = ts if period == "all" else ts[ts["period"] == period]
        dec = pd.qcut(d["score"].rank(method="first"), 10, labels=False) + 1
        g = d.groupby(dec).agg(n=("label", "size"), upgrades=("label", "sum"), predicted=("score", "mean"),
                               observed=("label", "mean"), score_min=("score", "min"), score_max=("score", "max")).reset_index()
        g = g.rename(columns={"score": "decile"})
        g["observed_lo"], g["observed_hi"] = _wilson(g["upgrades"], g["n"])
        g.insert(0, "period", period)
        rows.append(g)
    return pd.concat(rows, ignore_index=True)


def auc_interval(d: pd.DataFrame, n_boot: int = N_BOOT_MODEL) -> tuple[float, float]:
    """Account-level bootstrap 95% interval of the AUC on scored rows (accounts resampled, scores fixed)."""
    y, s = d["label"].to_numpy(), d["score"].to_numpy()
    draws = []
    for idx in _cluster_indices(d["customer_id"].to_numpy(), n_boot):
        if 0 < y[idx].sum() < len(idx):
            draws.append(roc_auc_score(y[idx], s[idx]))
    lo, hi = np.percentile(draws, [2.5, 97.5])
    return float(lo), float(hi)


def model_metrics(ts: pd.DataFrame) -> pd.DataFrame:
    """Test-period metrics (all / pre / post the packaging change). AUC carries an account-level bootstrap interval;
    precision and recall at a capacity are means over the period's months (see capacity_metrics)."""
    rows = []
    for period in ("all", "pre", "post"):
        d = ts if period == "all" else ts[ts["period"] == period]
        auc_lo, auc_hi = auc_interval(d)
        r = dict(period=period, rows=len(d), accounts=int(d["customer_id"].nunique()), months=int(d["month_end"].nunique()),
                 upgrades=int(d["label"].sum()), base_rate=float(d["label"].mean()),
                 auc=float(roc_auc_score(d["label"], d["score"])), auc_lo=auc_lo, auc_hi=auc_hi,
                 mean_predicted=float(d["score"].mean()))
        for k in CAPACITIES:
            c = capacity_metrics(ts, k, period)
            ci = capacity_interval(ts, k, period)
            r.update({f"precision_at_{k}": c["precision"], f"recall_at_{k}": c["recall"], f"lift_at_{k}": c["lift"],
                      f"precision_at_{k}_lo": ci["precision_lo"], f"precision_at_{k}_hi": ci["precision_hi"],
                      f"lift_at_{k}_lo": ci["lift_lo"], f"lift_at_{k}_hi": ci["lift_hi"],
                      f"upgrades_per_month_at_{k}": c["upgrades_per_month"], f"mrr_covered_per_month_at_{k}": c["mrr_covered_per_month"]})
        rows.append(r)
    return pd.DataFrame(rows)


def auc_change_sentence(k: dict) -> str:
    """Before / after AUC with intervals and a verdict that does not exceed the evidence (shared by the page and the
    notebook, so both say the same thing about whether the ranking survived the packaging change)."""
    pre = f"{k['auc_test_pre']:.2f} ({k['auc_test_pre_lo']:.2f}–{k['auc_test_pre_hi']:.2f})"
    post = f"{k['auc_test_post']:.2f} ({k['auc_test_post_lo']:.2f}–{k['auc_test_post_hi']:.2f})"
    if k["auc_test_post"] >= k["auc_test_pre"]:
        verdict = "no sign the ranking degraded"
    elif k["auc_test_post_hi"] >= k["auc_test_pre_lo"]:
        verdict = "no clear change in the ranking"
    else:
        verdict = "the ranking weakened"
    return (f"AUC {pre} for month-ends indexed before the change and {post} after — {verdict}, though three months with "
            f"{k['test_upgrades_post']} upgrades cannot show a small change either way (the 'before' rows' 90-day label windows "
            "run into May–June, so some of their upgrades already post-date the change)")


def _scores_frame(d: pd.DataFrame, p: np.ndarray) -> pd.DataFrame:
    out = d[["customer_id", "month_end", "period", "segment", "billing_period", "seats", "label", "churn_in_window"]].copy()
    out["score"] = p
    out["incremental_mrr"] = out["seats"] * out["billing_period"].map(PRICE_DELTA)
    return out.reset_index(drop=True)


def train_and_test(pop: pd.DataFrame, raw_levels: bool = False) -> tuple[dict, pd.DataFrame]:
    """Purged time split per 02 §13: train on t <= TRAIN_END (2025-09-30), test on TEST_START .. TEST_END
    (2026-01-31 .. 2026-06-30); the Oct – Dec 2025 month-ends are an embargo equal to the 90-day label horizon, so no
    training label window reaches the test period (forced rows excluded)."""
    d = pop[~pop["forced"]]
    tr, te = d[d["split"] == "train"], d[d["split"] == "test"]
    Xtr, fill = feature_frame(tr, raw_levels=raw_levels)
    bundle = fit_model(Xtr, tr["label"].to_numpy(), tr["customer_id"], fill)
    Xte, _ = feature_frame(te, fill=fill, raw_levels=raw_levels)
    return bundle, _scores_frame(te, predict(bundle, Xte))


def model_drift_check(pop: pd.DataFrame, main_scores: pd.DataFrame) -> pd.DataFrame:
    """Why the paywall / automation signals enter as within-month percentiles: the same model on raw levels
    predicts rising propensity after April while the observed rate falls."""
    _, raw_scores = train_and_test(pop, raw_levels=True)
    a = main_scores.groupby("month_end").agg(rows=("label", "size"), observed=("label", "mean"),
                                             predicted_percentile_model=("score", "mean"))
    b = raw_scores.groupby("month_end")["score"].mean().rename("predicted_raw_level_model")
    return a.join(b).reset_index()


# =============================================================================== deployment scoring
def current_basic() -> pd.DataFrame:
    f = marts.load("fct_account_month_features")
    cur = f[(f["month_end"] == AS_OF) & (f["plan_id"] == "basic") & (f["tenure_months"] >= MIN_TENURE)].copy()
    return _add_percentiles(cur)


def deployment_model(pop: pd.DataFrame) -> dict:
    """Scorer for the opportunity list: same features and settings, refit on every labelled row (Jan 2024 – Jun 2026),
    then the intercept is re-centred once more so that the mean propensity on the post-change month-ends
    (Apr – Jun 2026) equals their observed 90-day upgrade rate. The ranking comes from the full history; the level
    comes from the months after the packaging change. Evaluation numbers always come from the time-split model."""
    d = pop[~pop["forced"]]
    X, fill = feature_frame(d)
    b = fit_model(X, d["label"].to_numpy(), d["customer_id"], fill)
    post = (d["period"] == "post").to_numpy()
    Z = b["scaler"].transform(X)
    logits = Z[post] @ b["model"].coef_[0] + b["model"].intercept_[0]
    target = float(d["label"].to_numpy()[post].mean())
    shift = _prior_correction(logits, target)
    b["model"].intercept_[0] += shift
    b["post_shift"], b["post_base_rate"] = float(shift), target
    return b


def _reason_rules(q: dict) -> dict:
    """signal -> [(code, condition on a row)], strongest first. q holds the scoring month's medians / 90th percentiles."""
    med, p90 = q["p50"], q["p90"]
    return {
        "gate_hits_per_wau": [("GATE_HITS_TOP_DECILE", lambda r: r["gate_hits_per_wau"] >= p90["gate_hits_per_wau"]),
                              ("GATE_HITS_ABOVE_MEDIAN", lambda r: r["gate_hits_per_wau"] > med["gate_hits_per_wau"])],
        "gate_user_share": [("GATE_USERS_TOP_DECILE", lambda r: r["gate_user_share"] >= p90["gate_user_share"]),
                            ("GATE_USERS_ABOVE_MEDIAN", lambda r: r["gate_user_share"] > med["gate_user_share"])],
        "seat_growth_3m": [("TEAM_GROWTH_10PCT_3M", lambda r: r["seat_growth_3m"] >= 0.10),
                           ("TEAM_GROWING", lambda r: r["seat_growth_3m"] > 0)],
        "utilization_13w": [("UTIL_ABOVE_90", lambda r: r["utilization_13w"] >= 0.90),
                            ("UTIL_ABOVE_75", lambda r: r["utilization_13w"] >= 0.75)],
        "max_weekly_utilization_13w": [("FULL_SEATS_WEEK", lambda r: r["max_weekly_utilization_13w"] >= 1.0)],
        "invites_per_seat_13w": [("INVITES_TOP_DECILE", lambda r: r["invites_per_seat_13w"] >= p90["invites_per_seat_13w"]),
                                 ("INVITES_ABOVE_MEDIAN", lambda r: r["invites_per_seat_13w"] > med["invites_per_seat_13w"])],
        "wau_trend": [("WAU_UP_15PCT", lambda r: r["wau_trend"] >= 0.15), ("WAU_RISING", lambda r: r["wau_trend"] > 0)],
        "automation_per_wau": [("AUTOMATION_TOP_DECILE", lambda r: r["automation_per_wau"] >= p90["automation_per_wau"]),
                               ("AUTOMATION_ABOVE_MEDIAN", lambda r: r["automation_per_wau"] > med["automation_per_wau"])],
        "reports_per_wau": [("REPORTS_TOP_DECILE", lambda r: r["reports_per_wau"] >= p90["reports_per_wau"]),
                            ("REPORTS_ABOVE_MEDIAN", lambda r: r["reports_per_wau"] > med["reports_per_wau"])],
        "core_actions_per_wau": [("CORE_ACTIONS_TOP_DECILE", lambda r: r["core_actions_per_wau"] >= p90["core_actions_per_wau"]),
                                 ("CORE_ACTIONS_ABOVE_MEDIAN", lambda r: r["core_actions_per_wau"] > med["core_actions_per_wau"])],
        "integration_syncs_per_week": [("AT_BASIC_INTEGRATION_CAP", lambda r: r["integration_syncs_per_week"] >= INTEGRATION_CAP_SYNCS),
                                       ("INTEGRATIONS_ABOVE_MEDIAN", lambda r: r["integration_syncs_per_week"] > med["integration_syncs_per_week"])],
        "seats": [("SEATS_20_PLUS", lambda r: r["seats"] >= 20), ("SEATS_ABOVE_MEDIAN", lambda r: r["seats"] > med["seats"])],
        "prior_higher_tier": [("PRIOR_PREMIUM", lambda r: bool(r["prior_higher_tier"]))],
    }


def _evidence(signal: str, r: pd.Series, q: dict) -> str:
    v = r[signal]
    if signal == "prior_higher_tier":
        return "previously on Premium / Enterprise"
    if signal == "seat_growth_3m":
        return f"seats {num(r['seats_3m_ago'], 0)} → {num(r['seats'], 0)} in 3 months ({pct(v, 0, signed=True)})"
    rank = (r[f"{signal}_rank"]) if f"{signal}_rank" in r else None
    tag = f", top {max(1, int(round((1 - rank) * 100)))}%" if rank is not None and rank >= 0.5 else ""
    return f"{SIGNALS[signal]['short'].lower()} {fmt_value(signal, v)} (Basic median {fmt_value(signal, q['p50'][signal])}{tag})"


def opportunities(pop: pd.DataFrame | None = None, bundle: dict | None = None) -> pd.DataFrame:
    """Every Basic account paying at 2026-09-30 with tenure >= 3 months, scored with the deployment model.
    expected_mrr = propensity x seats x (Premium - Basic list price per seat-month on the account's billing period).
    Reason codes = the 2-3 behavioural signals contributing most to the score vs the average account, each
    stated at the strongest threshold the account actually meets (definitions in REASON_CODES)."""
    pop = population() if pop is None else pop
    bundle = deployment_model(pop) if bundle is None else bundle
    cur = current_basic()
    X, _ = feature_frame(cur, fill=bundle["fill"])
    cur["propensity"] = predict(bundle, X)
    cur["incremental_mrr_if_upgrade"] = cur["seats"] * cur["billing_period"].map(PRICE_DELTA)
    cur["expected_mrr"] = cur["propensity"] * cur["incremental_mrr_if_upgrade"]
    q = {"p50": cur[SIGNAL_COLS].median().to_dict(), "p90": cur[SIGNAL_COLS].quantile(0.9).to_dict()}
    for s in SIGNAL_COLS:
        cur[f"{s}_rank"] = cur[s].rank(pct=True)
    rules = _reason_rules(q)
    contrib = contributions(bundle, X)
    coef = pd.Series(bundle["model"].coef_[0], index=bundle["columns"])
    behavioural = [c for c in bundle["columns"] if c in FEATURE_SIGNAL and coef[c] > 0]
    codes, evidence = [], []
    for i, r in cur.iterrows():
        c = contrib.loc[i, behavioural].sort_values(ascending=False)
        found, texts = [], []
        for feat, val in c.items():
            if val <= 0 or len(found) == 3:
                break
            sig = FEATURE_SIGNAL[feat]
            for code, cond in rules[sig]:
                if cond(r):
                    found.append(code)
                    texts.append(_evidence(sig, r, q))
                    break
        codes.append(found + [None] * (3 - len(found)))
        evidence.append("; ".join(texts))
    cur[["reason_code_1", "reason_code_2", "reason_code_3"]] = pd.DataFrame(codes, index=cur.index)
    cur["evidence"] = evidence
    cust = marts.load("stg_customers")[["customer_id", "company_name", "csm_owner"]]
    ren = marts.load("int_next_renewal")[["customer_id", "next_renewal_date"]]
    out = cur.merge(cust, on="customer_id", how="left").merge(ren, on="customer_id", how="left")
    out["current_arr"] = out["mrr"] * 12
    out["days_to_renewal"] = (out["next_renewal_date"] - AS_OF).dt.days
    out = out.sort_values("expected_mrr", ascending=False).reset_index(drop=True)
    out.insert(0, "rank", np.arange(1, len(out) + 1))
    cols = ["rank", "customer_id", "company_name", "segment", "industry", "acquisition_channel", "cs_coverage", "csm_owner",
            "seats", "billing_period", "mrr", "current_arr", "next_renewal_date", "days_to_renewal", "tenure_months",
            "propensity", "incremental_mrr_if_upgrade", "expected_mrr", "reason_code_1", "reason_code_2", "reason_code_3",
            "evidence"] + SIGNAL_COLS + ["seats_3m_ago"]
    return out[_uniq(cols)]


def opportunity_summary(opp: pd.DataFrame, k: int) -> dict:
    top = opp.head(k)
    return dict(k=k, n_scored=len(opp), worked=len(top), expected_upgrades=float(top["propensity"].sum()),
                expected_mrr=float(top["expected_mrr"].sum()), incremental_mrr_if_all=float(top["incremental_mrr_if_upgrade"].sum()),
                total_expected_upgrades=float(opp["propensity"].sum()), total_expected_mrr=float(opp["expected_mrr"].sum()),
                share_of_expected_mrr=float(top["expected_mrr"].sum() / opp["expected_mrr"].sum()) if len(opp) else np.nan,
                min_propensity=float(top["propensity"].min()) if len(top) else np.nan)


# =============================================================================== build / key numbers
def build() -> dict[str, pd.DataFrame]:
    pop = population()
    ev = upgrade_events()
    comp = comparison_groups(pop)
    sig = signal_table(comp, pop)
    quint = pd.concat([quintiles(pop, s, p) for s in SIGNAL_COLS for p in ("all", "pre", "post")], ignore_index=True)
    bundle, ts = train_and_test(pop)
    d_train = pop[~pop["forced"] & (pop["split"] == "train")]
    Xtr, _ = feature_frame(d_train, fill=bundle["fill"])
    ors = odds_ratios(bundle, Xtr, d_train["label"].to_numpy(), d_train["customer_id"])
    deploy = deployment_model(pop)
    opp = opportunities(pop, deploy)
    deploy_coef = pd.DataFrame({"feature": deploy["columns"], "label": [FEATURE_LABELS.get(c, c) for c in deploy["columns"]],
                                "coef": deploy["model"].coef_[0], "odds_ratio": np.exp(deploy["model"].coef_[0]),
                                "sd": deploy["scaler"].scale_})
    reasons = pd.DataFrame({"reason_code": list(REASON_CODES), "definition": list(REASON_CODES.values())})
    emb = pop[~pop["forced"] & (pop["split"] == "embargo")]
    meta = pd.DataFrame([dict(
        train_end=TRAIN_END, test_start=TEST_START, test_end=TEST_END, packaging_change=PACKAGING_CHANGE,
        embargo_start=pd.Timestamp(EMBARGO_MONTHS[0]), embargo_end=pd.Timestamp(EMBARGO_MONTHS[-1]),
        embargo_rows=len(emb), embargo_accounts=int(emb["customer_id"].nunique()), embargo_upgrades=int(emb["label"].sum()),
        prior_correction_train=bundle["prior_correction"], prior_correction_deploy=deploy["prior_correction"],
        deploy_post_shift=deploy["post_shift"], deploy_post_base_rate=deploy["post_base_rate"], regularization_c=REGULARIZATION_C,
        train_rows=bundle["n_rows"], train_accounts=bundle["n_accounts"], train_base_rate=bundle["base_rate"],
        deploy_rows=deploy["n_rows"], deploy_accounts=deploy["n_accounts"], deploy_base_rate=deploy["base_rate"],
        n_boot_bins=N_BOOT_BINS, n_boot_model=N_BOOT_MODEL, n_boot_smd=N_BOOT_SMD)])
    tables = {
        "expansion__population": pop,
        "expansion__upgrade_events": ev,
        "expansion__comparison": comp,
        "expansion__signal_table": sig,
        "expansion__quintiles": quint,
        "expansion__drift_weekly": drift_weekly(),
        "expansion__drift_monthly": drift_monthly(),
        "expansion__drift_distribution": drift_distribution(),
        "expansion__test_scores": ts,
        "expansion__model_metrics": model_metrics(ts),
        "expansion__calibration": calibration_table(ts),
        "expansion__capture_curve": capture_curve(ts),
        "expansion__odds_ratios": ors,
        "expansion__model_drift_check": model_drift_check(pop, ts),
        "expansion__deploy_coefficients": deploy_coef,
        "expansion__model_meta": meta,
        "expansion__opportunities": opp,
        "expansion__reason_codes": reasons,
    }
    for name, df in tables.items():
        marts.write(name, df)
    return tables


def key_numbers() -> dict:
    pop = marts.load("expansion__population")
    ev = marts.load("expansion__upgrade_events")
    comp = marts.load("expansion__comparison")
    sig = marts.load("expansion__signal_table")
    mm = marts.load("expansion__model_metrics").set_index("period")
    opp = marts.load("expansion__opportunities")
    dd = marts.load("expansion__drift_distribution")
    meta = marts.load("expansion__model_meta").iloc[0]
    ts = marts.load("expansion__test_scores")
    valid = pop[~pop["forced"]]
    top3 = sig.reindex(sig["smd"].abs().sort_values(ascending=False).index).head(3)
    gate = dd[dd["signal"] == "gate_hits_per_wau"].set_index("period")
    auto = dd[dd["signal"] == "automation_per_wau"].set_index("period")
    pre_k, post_k = DRIFT_PRE[0], DRIFT_POST[0]
    cap = opportunity_summary(opp, CAPACITY_DEFAULT)
    excl = ev[ev["voluntary"]]["exclusion"].value_counts().to_dict()
    out = {
        "as_of": str(AS_OF.date()),
        "horizon_days": HORIZON_DAYS,
        "population_rows": int(len(valid)), "population_accounts": int(valid["customer_id"].nunique()),
        "population_first_month": str(valid["month_end"].min().date()), "population_last_month": str(valid["month_end"].max().date()),
        "label_rows": int(valid["label"].sum()), "base_rate_all": float(valid["label"].mean()),
        "churn_in_window_rows": int(valid["churn_in_window"].sum()),
        "forced_upgrade_events": int((~ev["voluntary"]).sum()), "forced_upgrade_rows_excluded": int(pop["forced"].sum()),
        "voluntary_upgrade_events": int(ev["voluntary"].sum()),
        "upgrade_events_in_comparison": int((ev["exclusion"] == "eligible").sum()),
        "upgrade_events_excluded": {k: int(v) for k, v in excl.items() if k != "eligible"},
        "n_upgraders": int((comp["group"] == "upgrader").sum()), "n_stayers": int((comp["group"] == "stayer").sum()),
        "n_strata": int(comp[comp["group"] == "upgrader"].groupby(["month_end", "tenure_band"]).ngroups),
        "top_signals": [dict(signal=r.signal, name=r.name, smd=float(r.smd), smd_lo=float(r.smd_lo), smd_hi=float(r.smd_hi),
                             median_upgraders=float(r.median_upgraders), median_stayers=float(r.median_stayers),
                             top_quintile_lift=float(r.top_quintile_lift), top_quintile_lift_lo=float(r.top_quintile_lift_lo),
                             top_quintile_lift_hi=float(r.top_quintile_lift_hi), top_quintile_lift_pre=float(r.top_quintile_lift_pre),
                             top_quintile_lift_post=float(r.top_quintile_lift_post)) for r in top3.itertuples()],
        "signal_lift_pre_post": {r.signal: {"pre": float(r.top_quintile_lift_pre), "post": float(r.top_quintile_lift_post),
                                            "post_lo": float(r.top_quintile_lift_post_lo), "post_hi": float(r.top_quintile_lift_post_hi)}
                                 for r in sig.itertuples()},
        "signals_lift_holds_post": [r.signal for r in sig.itertuples() if r.top_quintile_lift_post_lo > 1.0],
        "interval_method": "account-level bootstrap 95% (bin rates, lifts, threshold rule, AUC); ordinary bootstrap for SMD",
        "n_boot_bins": N_BOOT_BINS, "n_boot_model": N_BOOT_MODEL, "n_boot_smd": N_BOOT_SMD,
        "train_end": str(TRAIN_END.date()), "embargo_months": list(EMBARGO_MONTHS),
        "test_start": str(TEST_START.date()), "test_end": str(TEST_END.date()),
        "train_rows": int(meta["train_rows"]), "train_accounts": int(meta["train_accounts"]), "base_rate_train": float(meta["train_base_rate"]),
        "embargo_rows": int(meta["embargo_rows"]), "embargo_accounts": int(meta["embargo_accounts"]), "embargo_upgrades": int(meta["embargo_upgrades"]),
        "test_rows": int(mm.loc["all", "rows"]), "test_accounts": int(mm.loc["all", "accounts"]), "test_upgrades": int(mm.loc["all", "upgrades"]),
        "test_upgrades_pre": int(mm.loc["pre", "upgrades"]), "test_upgrades_post": int(mm.loc["post", "upgrades"]),
        "base_rate_test": float(mm.loc["all", "base_rate"]), "base_rate_test_pre": float(mm.loc["pre", "base_rate"]),
        "base_rate_test_post": float(mm.loc["post", "base_rate"]),
        "auc_test": float(mm.loc["all", "auc"]), "auc_test_pre": float(mm.loc["pre", "auc"]), "auc_test_post": float(mm.loc["post", "auc"]),
        "auc_test_lo": float(mm.loc["all", "auc_lo"]), "auc_test_hi": float(mm.loc["all", "auc_hi"]),
        "auc_test_pre_lo": float(mm.loc["pre", "auc_lo"]), "auc_test_pre_hi": float(mm.loc["pre", "auc_hi"]),
        "auc_test_post_lo": float(mm.loc["post", "auc_lo"]), "auc_test_post_hi": float(mm.loc["post", "auc_hi"]),
        "mean_predicted_test_pre": float(mm.loc["pre", "mean_predicted"]), "mean_predicted_test_post": float(mm.loc["post", "mean_predicted"]),
        "capacity_default": CAPACITY_DEFAULT,
        "n_opportunities": int(len(opp)), "opportunities_expected_upgrades": float(opp["propensity"].sum()),
        "opportunities_expected_mrr": float(opp["expected_mrr"].sum()),
        "opportunities_incremental_mrr_if_all": float(opp["incremental_mrr_if_upgrade"].sum()),
        "mean_propensity_current": float(opp["propensity"].mean()),
        "capacity_expected_upgrades": cap["expected_upgrades"], "capacity_expected_mrr": cap["expected_mrr"],
        "capacity_incremental_mrr_if_all": cap["incremental_mrr_if_all"], "capacity_share_of_expected_mrr": cap["share_of_expected_mrr"],
        "gate_hits_mean_pre": float(gate.loc[pre_k, "mean"]), "gate_hits_mean_post": float(gate.loc[post_k, "mean"]),
        "gate_hits_median_pre": float(gate.loc[pre_k, "p50"]), "gate_hits_median_post": float(gate.loc[post_k, "p50"]),
        "gate_hits_drift": float(gate.loc[post_k, "mean"] / gate.loc[pre_k, "mean"] - 1),
        "automation_mean_pre": float(auto.loc[pre_k, "mean"]), "automation_mean_post": float(auto.loc[post_k, "mean"]),
        "automation_drift": float(auto.loc[post_k, "mean"] / auto.loc[pre_k, "mean"] - 1),
        "deploy_post_shift": float(meta["deploy_post_shift"]), "deploy_post_base_rate": float(meta["deploy_post_base_rate"]),
        "intercept_prior_correction_train": float(meta["prior_correction_train"]),
        "reason_code_counts": {k: int(v) for k, v in opp["reason_code_1"].value_counts().items()},
        "test_months_pre": [str(d.date()) for d in sorted(ts.loc[ts["period"] == "pre", "month_end"].unique())],
        "test_months_post": [str(d.date()) for d in sorted(ts.loc[ts["period"] == "post", "month_end"].unique())],
    }
    for k in CAPACITIES:
        for p in ("pre", "post", "all"):
            out[f"precision_at_{k}_{p}"] = float(mm.loc[p, f"precision_at_{k}"])
            out[f"precision_at_{k}_{p}_lo"] = float(mm.loc[p, f"precision_at_{k}_lo"])
            out[f"precision_at_{k}_{p}_hi"] = float(mm.loc[p, f"precision_at_{k}_hi"])
            out[f"lift_at_{k}_{p}_lo"] = float(mm.loc[p, f"lift_at_{k}_lo"])
            out[f"lift_at_{k}_{p}_hi"] = float(mm.loc[p, f"lift_at_{k}_hi"])
            out[f"recall_at_{k}_{p}"] = float(mm.loc[p, f"recall_at_{k}"])
            out[f"lift_at_{k}_{p}"] = float(mm.loc[p, f"lift_at_{k}"])
            out[f"upgrades_per_month_at_{k}_{p}"] = float(mm.loc[p, f"upgrades_per_month_at_{k}"])
            out[f"mrr_covered_per_month_at_{k}_{p}"] = float(mm.loc[p, f"mrr_covered_per_month_at_{k}"])
    return out


# =============================================================================== figures
PERIOD_LABEL = {"all": "Jan 2024 – Jun 2026", "pre": "before the Apr 2026 packaging change (to Mar 2026)",
                "post": "after the change (Apr – Jun 2026 month-ends)"}
# Direction encoding on this page: blue (ACCENT) = higher / raises, the red end of the theme's diverging scale =
# lower / lowers, gray = the interval includes the null. Orange is reserved for Technology accounts and not used here.
LOWER = theme.DIVERGING_SCALE[1][1]
BOOT_NOTE = "account-level bootstrap 95% interval"


def fig_smd_dotplot(sig: pd.DataFrame | None = None) -> go.Figure:
    s = (marts.load("expansion__signal_table") if sig is None else sig).sort_values("smd")
    kind = np.where(s["smd_lo"] > 0, "higher", np.where(s["smd_hi"] < 0, "lower", "unclear"))
    style = {"higher": ("Higher among upgraders", theme.ACCENT), "lower": ("Lower among upgraders", LOWER),
             "unclear": ("Interval includes zero", theme.DEEMPH)}
    fig = go.Figure()
    for key, (label, color) in style.items():
        d = s[kind == key]
        if d.empty:
            continue
        fig.add_trace(go.Scatter(
            x=d["smd"], y=d["name"], mode="markers", name=label,
            marker=dict(size=10, color=color, line=dict(color=theme.SURFACE, width=2)),
            error_x=dict(type="data", symmetric=False, array=d["smd_hi"] - d["smd"], arrayminus=d["smd"] - d["smd_lo"],
                         color=color, thickness=2, width=4),
            customdata=np.c_[d["median_upgraders"], d["median_stayers"], d["smd_lo"], d["smd_hi"]],
            hovertemplate="%{y}<br>SMD %{x:.2f} (95% %{customdata[2]:.2f} to %{customdata[3]:.2f})"
                          "<br>median upgraders %{customdata[0]:.2f} · stayers %{customdata[1]:.2f}<extra></extra>"))
    fig.add_vline(x=0, line=dict(color=theme.AXIS, width=1))
    fig.update_xaxes(title_text="Standardized mean difference (upgraders − stayers)", showgrid=True, gridcolor=theme.GRID,
                     zeroline=False)
    fig.update_yaxes(showgrid=False, automargin=True, categoryorder="array", categoryarray=s["name"].tolist())
    fig.update_layout(margin=dict(l=40, r=30))
    top = s.sort_values("smd", ascending=False).head(2)
    n_u, n_s = int(s["n_upgraders"].max()), int(s["n_stayers"].max())
    return theme.apply(fig, f"Before upgrading, accounts differ most in {top.iloc[0]['short'].lower()} (SMD {top.iloc[0]['smd']:.2f}) "
                            f"and {top.iloc[1]['short'].lower()} ({top.iloc[1]['smd']:.2f})",
                       f"Signals measured in the 13 weeks before the index month-end; {n_u} upgraders vs {n_s} stayers matched on "
                       "calendar month and tenure band; bars = bootstrap 95% interval", height=max(420, 90 + 30 * len(s)))


def fig_quintile_lift(signal: str, period: str = "all", pop: pd.DataFrame | None = None,
                      q: pd.DataFrame | None = None) -> go.Figure:
    if q is None:
        if pop is not None:
            q = quintiles(pop, signal, period)
        else:
            allq = marts.load("expansion__quintiles")
            q = allq[(allq["signal"] == signal) & (allq["period"] == period)]
    base = float(q["base_rate"].iloc[0])
    colors = [theme.ACCENT if i == len(q) - 1 else theme.DEEMPH for i in range(len(q))]
    fig = go.Figure(go.Bar(
        x=q["bin"], y=q["rate"], marker=dict(color=colors), cliponaxis=False,
        error_y=dict(type="data", symmetric=False, array=q["rate_hi"] - q["rate"], arrayminus=q["rate"] - q["rate_lo"],
                     color=theme.INK_2, thickness=1.5, width=4),
        customdata=np.c_[q["quintile_span"], q["n"], q["upgrades"], q["share_of_rows"], q["lift_lo"], q["lift_hi"]],
        hovertemplate="%{customdata[0]} · %{x}<br>upgrade rate %{y:.1%} (%{customdata[2]} of %{customdata[1]:,} account-months, "
                      "%{customdata[3]:.0%} of rows)<br>lift interval %{customdata[4]:.1f}–%{customdata[5]:.1f}×<extra></extra>"))
    # lift labels sit above the upper whisker, not on the bar top where the error cap would strike through them
    fig.add_trace(go.Scatter(x=q["bin"], y=q["rate_hi"], mode="text", text=[f"{v:.1f}×" for v in q["lift"]],
                             textposition="top center", textfont=dict(color=theme.INK, size=12), cliponaxis=False,
                             hoverinfo="skip", showlegend=False))
    fig.add_hline(y=base, line=dict(color=theme.INK_2, width=1, dash="dot"),
                  annotation_text=f"base rate {pct(base)}", annotation_position="top left",
                  annotation_font=dict(color=theme.INK_2, size=11))
    fig.update_yaxes(tickformat=".0%", rangemode="tozero", title_text="90-day voluntary upgrade rate",
                     range=[0, float(q["rate_hi"].max()) * 1.25])
    fig.update_xaxes(title_text=f"{SIGNALS[signal]['name']} — quintile bins (low → high)")
    fig.update_layout(showlegend=False, bargap=0.4)
    top = q.iloc[-1]
    return theme.apply(fig, f"{SIGNALS[signal]['short']}: the top bin upgrades at {pct(top['rate'])}, "
                            f"{top['lift']:.1f}× the base rate ({top['quintile_span']}; interval {top['lift_lo']:.1f}–{top['lift_hi']:.1f}×)",
                       f"All eligible Basic account-months {PERIOD_LABEL[period]}; bins are quintiles of the signal "
                       f"(fewer when many rows tie); whiskers = {BOOT_NOTE} of the bin rate; labels = lift vs base rate", height=400)


def fig_gate_drift(weekly: pd.DataFrame | None = None, dist: pd.DataFrame | None = None,
                   monthly: pd.DataFrame | None = None, start: str = "2025-07-01") -> go.Figure:
    """Weekly pooled paywall rate around the packaging change, with the month-end 13-week median behind the key
    numbers. The title's drop is read from expansion__drift_distribution — the same table key_numbers() uses — so the
    chart, the tiles and the documents agree (Oct 2025 – Mar 2026 vs Jul – Sep 2026 month-ends)."""
    w = (marts.load("expansion__drift_weekly") if weekly is None else weekly)
    w = w[w["week_start"] >= start]
    dd = (marts.load("expansion__drift_distribution") if dist is None else dist)
    gate = dd[dd["signal"] == "gate_hits_per_wau"].set_index("period")
    m = (marts.load("expansion__drift_monthly") if monthly is None else monthly)
    m = m[m["month_end"] >= start]
    pre_mean, post_mean = float(gate.loc[DRIFT_PRE[0], "mean"]), float(gate.loc[DRIFT_POST[0], "mean"])
    drop = post_mean / pre_mean - 1
    fig = go.Figure()
    for (label, a, z), mean in ((DRIFT_PRE, pre_mean), (DRIFT_POST, post_mean)):
        x0 = pd.Timestamp(a) - pd.offsets.MonthBegin(1)
        fig.add_vrect(x0=x0, x1=pd.Timestamp(z), fillcolor=theme.NEUTRAL_MID, opacity=0.6, layer="below", line_width=0)
        fig.add_annotation(x=x0 + (pd.Timestamp(z) - x0) / 2, y=0.98, yref="paper", yanchor="top", showarrow=False,
                           text=f"{label.split(':')[0]}: mean {mean:.2f}", font=dict(color=theme.INK_2, size=11))
    fig.add_trace(go.Scatter(x=w["week_start"], y=w["gate_hits_per_wau"], mode="lines", name="All Basic accounts pooled, by week",
                             line=dict(color=theme.ACCENT, width=2),
                             hovertemplate="week of %{x|%d %b %Y}<br>paywall hits per active user %{y:.3f}<extra></extra>"))
    fig.add_trace(go.Scatter(x=m["month_end"], y=m["gate_hits_per_wau_median"], mode="lines+markers",
                             name="Median Basic account, 13-week rate at month-end",
                             line=dict(color=theme.INK_2, width=1.5, dash="dash"), marker=dict(size=6, color=theme.INK_2),
                             hovertemplate="month-end %{x|%d %b %Y}<br>median account, 13-week rate %{y:.3f}<extra></extra>"))
    fig.add_vline(x=PACKAGING_CHANGE, line=dict(color=theme.INK_2, width=1, dash="dash"))
    fig.add_annotation(x=PACKAGING_CHANGE, y=0.86, yref="paper", text="Packaging change 15 Apr 2026", showarrow=False,
                       xanchor="left", yanchor="top", xshift=6, font=dict(color=theme.INK_2, size=11))
    fig.update_yaxes(rangemode="tozero", tickformat=".2f", title_text="Paywall hits per active user per week")
    fig.update_xaxes(tickformat="%b %Y", dtick="M2")
    return theme.apply(fig, f"Paywall hits per active user fell {pct(-drop, 0)} after the April packaging change "
                            f"({pre_mean:.2f} → {post_mean:.2f} per week)",
                       "Premium-feature gate hits ÷ weekly active users, Basic accounts with 3+ months tenure; shaded = the month-ends "
                       "behind the headline means (13-week windows entirely before / after the change); the median lags the "
                       "change by its 13-week window", height=400)


def fig_lift_pre_post(sig: pd.DataFrame | None = None) -> go.Figure:
    s = (marts.load("expansion__signal_table") if sig is None else sig).sort_values("top_quintile_lift_post")
    # a post-change top bin with fewer than MIN_UPGRADES_FOR_LIFT upgrades has no usable lift: it is listed, not plotted
    weak = s[s["top_quintile_upgrades_post"] < MIN_UPGRADES_FOR_LIFT]
    fig = go.Figure()
    for col, label, color, d in (("pre", "Before the change (to Mar 2026)", theme.DEEMPH, s),
                                 ("post", "After the change (Apr – Jun 2026)", theme.ACCENT, s.drop(weak.index))):
        fig.add_trace(go.Scatter(
            x=d[f"top_quintile_lift_{col}"], y=d["name"], mode="markers", name=label,
            marker=dict(size=10, color=color, line=dict(color=theme.SURFACE, width=2)),
            error_x=dict(type="data", symmetric=False, array=d[f"top_quintile_lift_{col}_hi"] - d[f"top_quintile_lift_{col}"],
                         arrayminus=d[f"top_quintile_lift_{col}"] - d[f"top_quintile_lift_{col}_lo"], color=color,
                         thickness=1.5, width=3),
            customdata=np.c_[d[f"top_quintile_lift_{col}_lo"], d[f"top_quintile_lift_{col}_hi"]],
            hovertemplate="%{y}<br>" + label + ": top-bin lift %{x:.2f}× (%{customdata[0]:.2f}–%{customdata[1]:.2f}×)<extra></extra>"))
    fig.add_vline(x=1.0, line=dict(color=theme.AXIS, width=1))
    fig.update_xaxes(title_text="Top-bin upgrade rate ÷ base rate (lift)", showgrid=True, gridcolor=theme.GRID,
                     rangemode="tozero")
    fig.update_yaxes(showgrid=False, automargin=True, categoryorder="array", categoryarray=s["name"].tolist())
    fig.update_layout(margin=dict(l=40, r=30))
    holds = s[s["top_quintile_lift_post_lo"] > 1.0]
    note = (f"; not estimable after the change (under {MIN_UPGRADES_FOR_LIFT} upgrades in the top bin): "
            + ", ".join(weak["short"].str.lower())) if len(weak) else ""
    return theme.apply(fig, f"{len(holds)} of {len(s)} signals still separate upgraders after the change "
                            f"(interval above 1×); the rest are too small or too noisy in three months",
                       "Lift of the highest signal bin, computed within each period so a level shift does not matter; "
                       f"bars = {BOOT_NOTE} of the bin rate ÷ base rate{note}", height=max(420, 90 + 30 * len(s)))


def fig_capture_curve(k: int = CAPACITY_DEFAULT, curve: pd.DataFrame | None = None, max_k: int = 600) -> go.Figure:
    c = (marts.load("expansion__capture_curve") if curve is None else curve)
    c = c[c["k"] <= max_k]
    fig = go.Figure()
    for period, label, color in (("pre", "Before the change (Jan – Mar 2026)", theme.DEEMPH),
                                 ("post", "After the change (Apr – Jun 2026)", theme.ACCENT)):
        d = c[c["period"] == period]
        fig.add_trace(go.Scatter(x=d["k"], y=d["share_captured"], mode="lines", name=label, line=dict(color=color, width=2),
                                 hovertemplate="top %{x} accounts/month<br>" + label + ": %{y:.0%} of upgrades<extra></extra>"))
    d = c[c["period"] == "all"]
    fig.add_trace(go.Scatter(x=d["k"], y=d["random"].clip(upper=1), mode="lines", name="Random pick",
                             line=dict(color=theme.AXIS, width=1.5, dash="dot"), hoverinfo="skip"))
    at = c[(c["k"] == (k // 10) * 10)].set_index("period")["share_captured"]
    fig.add_vline(x=k, line=dict(color=theme.INK_2, width=1, dash="dash"))
    fig.add_annotation(x=k, y=0.98, yref="paper", text=f"CS capacity: {k} accounts / month", showarrow=False, xanchor="left",
                       xshift=6, font=dict(color=theme.INK_2, size=11))
    fig.update_yaxes(tickformat=".0%", range=[0, 1.02], title_text="Share of the month's upgrades captured")
    fig.update_xaxes(title_text="Accounts contacted per month (ranked by propensity)", showgrid=True, gridcolor=theme.GRID)
    return theme.apply(fig, f"Working the top {k} accounts a month captures {pct(at.get('pre', np.nan), 0)} of upgrades before the "
                            f"change and {pct(at.get('post', np.nan), 0)} after",
                       f"Model {SPLIT_NOTE}; each test month ranked separately and averaged", height=400)


def fig_calibration(period: str = "all", cal: pd.DataFrame | None = None) -> go.Figure:
    c = (marts.load("expansion__calibration") if cal is None else cal)
    c = c[c["period"] == period]
    lim = float(max(c["predicted"].max(), c["observed_hi"].max()) * 1.1)
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=[0, lim], y=[0, lim], mode="lines", name="Perfect calibration",
                             line=dict(color=theme.AXIS, width=1, dash="dot"), hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=c["predicted"], y=c["observed"], mode="markers+lines", name="Score decile",
                             line=dict(color=theme.ACCENT, width=2),
                             marker=dict(size=9, color=theme.ACCENT, line=dict(color=theme.SURFACE, width=2)),
                             error_y=dict(type="data", symmetric=False, array=c["observed_hi"] - c["observed"],
                                          arrayminus=c["observed"] - c["observed_lo"], color=theme.ACCENT, thickness=1.5, width=3),
                             customdata=np.c_[c["decile"], c["n"], c["upgrades"]],
                             hovertemplate="decile %{customdata[0]}<br>predicted %{x:.1%} · observed %{y:.1%}"
                                           "<br>%{customdata[2]} upgrades of %{customdata[1]:,}<extra></extra>"))
    fig.update_xaxes(tickformat=".0%", title_text="Mean predicted 90-day upgrade propensity", range=[0, lim], showgrid=True,
                     gridcolor=theme.GRID)
    fig.update_yaxes(tickformat=".0%", title_text="Observed upgrade rate", range=[0, lim])
    top = c.iloc[-1]
    over = top["predicted"] > top["observed_hi"]
    span = {"all": "Jan – Jun 2026 test", "pre": "Jan – Mar 2026 test", "post": "Apr – Jun 2026 test"}.get(period, "test period")
    title = (f"The model over-predicts its top decile ({pct(top['predicted'])} predicted vs {pct(top['observed'])} observed, {span}): "
             "read propensities as a ranking, not a probability" if over else
             f"Top decile: predicted {pct(top['predicted'])}, observed {pct(top['observed'])} ({span})")
    return theme.apply(fig, title, "Test rows grouped into ten equal bins by score; whiskers = Wilson 95% interval on rows", height=400)


def fig_odds_ratios(ors: pd.DataFrame | None = None) -> go.Figure:
    o = (marts.load("expansion__odds_ratios") if ors is None else ors).sort_values("odds_ratio")
    kind = np.where(o["or_lo"] > 1, "up", np.where(o["or_hi"] < 1, "down", "unclear"))
    style = {"up": ("Raises propensity", theme.ACCENT), "down": ("Lowers propensity", LOWER),
             "unclear": ("Interval includes 1", theme.DEEMPH)}
    fig = go.Figure()
    for key, (label, color) in style.items():
        d = o[kind == key]
        if d.empty:
            continue
        fig.add_trace(go.Scatter(
            x=d["odds_ratio"], y=d["label"], mode="markers", name=label,
            marker=dict(size=10, color=color, line=dict(color=theme.SURFACE, width=2)),
            error_x=dict(type="data", symmetric=False, array=d["or_hi"] - d["odds_ratio"], arrayminus=d["odds_ratio"] - d["or_lo"],
                         color=color, thickness=2, width=4),
            hovertemplate="%{y}<br>odds ratio per 1 SD %{x:.2f}<extra></extra>"))
    fig.add_vline(x=1.0, line=dict(color=theme.AXIS, width=1))
    fig.update_xaxes(type="log", title_text="Odds ratio per 1 SD (log scale)", showgrid=True, gridcolor=theme.GRID)
    fig.update_yaxes(showgrid=False, automargin=True, categoryorder="array", categoryarray=o["label"].tolist())
    fig.update_layout(margin=dict(l=40, r=30))
    top = o.sort_values("odds_ratio", ascending=False).head(2)
    return theme.apply(fig, f"Strongest positive signals per 1 SD: {top.iloc[0]['label'].lower()} ({top.iloc[0]['odds_ratio']:.2f}×) "
                            f"and {top.iloc[1]['label'].lower()} ({top.iloc[1]['odds_ratio']:.2f}×)",
                       "Logistic regression on month-ends to Sep 2025 (Oct – Dec 2025 held out), standardized features, "
                       f"L2 (C = 1), one vote per account; bars = {BOOT_NOTE}", height=max(420, 90 + 28 * len(o)))


def fig_group_medians(sig: pd.DataFrame | None = None) -> go.Figure:
    """Companion to the SMD plot: medians of both groups as a dumbbell, one signal per row, on a shared relative scale
    (each signal divided by the stayer median so the two groups can share one axis)."""
    s = (marts.load("expansion__signal_table") if sig is None else sig).sort_values("smd")
    rel = s["median_upgraders"] / s["median_stayers"].replace(0, np.nan)
    fig = go.Figure()
    for r, v in zip(s.itertuples(), rel):
        fig.add_trace(go.Scatter(x=[1.0, v], y=[r.name] * 2, mode="lines", line=dict(color=theme.AXIS, width=2),
                                 showlegend=False, hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=[1.0] * len(s), y=s["name"], mode="markers", name="Stayers (median = 1.0)",
                             marker=dict(size=10, color=theme.DEEMPH, line=dict(color=theme.SURFACE, width=2)),
                             customdata=s["median_stayers"], hovertemplate="%{y}<br>stayer median %{customdata:.2f}<extra></extra>"))
    fig.add_trace(go.Scatter(x=rel, y=s["name"], mode="markers+text", name="Upgraders",
                             text=[f"{v:.2f}×" if pd.notna(v) else "n/a" for v in rel], textposition="middle right",
                             textfont=dict(color=theme.INK, size=11),
                             marker=dict(size=10, color=theme.ACCENT, line=dict(color=theme.SURFACE, width=2)),
                             customdata=s["median_upgraders"], hovertemplate="%{y}<br>upgrader median %{customdata:.2f}<extra></extra>"))
    fig.add_vline(x=1.0, line=dict(color=theme.AXIS, width=1))
    fig.update_xaxes(title_text="Upgrader median ÷ stayer median", showgrid=True, gridcolor=theme.GRID)
    fig.update_yaxes(showgrid=False, automargin=True, categoryorder="array", categoryarray=s["name"].tolist())
    fig.update_layout(margin=dict(l=40, r=60))
    return theme.apply(fig, "Median upgrader vs median stayer, signal by signal",
                       "Each signal scaled to the stayer median (gray = 1.0); a signal whose stayer median is zero is not shown",
                       height=max(420, 90 + 30 * len(s)))
