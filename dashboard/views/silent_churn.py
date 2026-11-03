"""Silent Churn Watchlist: who is shrinking without cancelling, can we see it coming, and which accounts
CS should work first. The decline / lookback sliders recompute the §9 flags; the early-warning sliders
recompute the back-test and the current flags (02_metric_definitions.md §9, §9.1, §15)."""
import pandas as pd
import streamlit as st

from common import data_table, finding, how_to_read, key_numbers, load, page_header, plot
from saas_rev.analysis import silent_churn as SC
from saas_rev.theme import delta_pct, delta_pts, money, pct

page_header("Silent churn watchlist",
            "Which paying customers are shrinking without cancelling, how much ARR is at risk, can we see it coming early "
            "enough to act, and which accounts should Customer Success work first?")

k = key_numbers("silent_churn")
km = key_numbers("metrics")
total_arr = km.get("arr", 1.0)
NumCol, TextCol, DateCol = st.column_config.NumberColumn, st.column_config.TextColumn, st.column_config.DateColumn
PCT = {"format": "percent"}


@st.cache_data(show_spinner=False)
def flags_for(threshold: float, lookback: int) -> pd.DataFrame:
    return SC.silent_flags(threshold, lookback)


@st.cache_data(show_spinner=False)
def derived_for(threshold: float, lookback: int) -> dict:
    fl = SC.silent_flags(threshold, lookback)
    return {"trend": SC.flag_trend(fl), "rate": SC.flag_rate_by_industry_group(fl), "profile": SC.profile(fl),
            "mix": SC.contraction_mix(fl, lookback=lookback), "followup": SC.followup(fl)}


@st.cache_data(show_spinner=False)
def rule_for(util_thr: float, trend_thr: float | None) -> dict:
    rows = load("silent_churn__backtest_rows")
    out = {"all": SC.evaluate_rule(rows, util_thr, trend_thr)}
    for bp in ("monthly", "annual"):
        out[bp] = SC.evaluate_rule(rows[rows["billing_period"] == bp], util_thr, trend_thr)
    out["leads"] = SC.lead_times(rows, util_thr, trend_thr)
    out["current"] = SC.current_flags(util_thr, trend_thr)
    out["by_quarter"] = SC.rule_by_quarter(rows, util_thr, trend_thr)
    out["bootstrap"] = SC.bootstrap_rule(rows, util_thr, trend_thr).set_index("billing_period")
    return out


@st.cache_data(show_spinner=False)
def watchlist_for(threshold: float, lookback: int, util_thr: float, trend_thr: float | None) -> pd.DataFrame:
    return SC.build_watchlist(threshold, lookback, util_thr, trend_thr)


# ------------------------------------------------------------------ 1. who is shrinking (02 §9)
st.subheader("1. Who is shrinking without cancelling?")
cc = st.columns([2, 1, 3])
threshold = cc[0].slider("Spend decline vs recent peak (billing-neutral MRR)", min_value=10, max_value=40, value=20, step=5,
                         format="%d%%", help="02 §9 default 20%. Lower = more accounts flagged for smaller cuts.") / 100
lookback = cc[1].select_slider("Lookback (months)", options=[3, 6, 9], value=6,
                               help="Peak window and contraction window (02 §9 default 6 months).")
is_default = abs(threshold - SC.DECLINE_THRESHOLD) < 1e-9 and lookback == SC.LOOKBACK_MONTHS
cc[2].markdown(
    f"<div class='note'>Definition (02 §9): paying at month-end, no open cancellation, billing-neutral MRR at least "
    f"{pct(threshold, 0)} below its peak of the previous {lookback} month-ends, and at least one seat, plan or discount "
    f"contraction in the last {lookback} months. A monthly → annual switch never qualifies. "
    + ("The pandas implementation reproduces the SQL table row for row at 20% / 6 months." if is_default else
       "Non-default parameters: a sensitivity of the same definition, not a different one.") + "</div>",
    unsafe_allow_html=True)

fl = flags_for(threshold, lookback)
d = derived_for(threshold, lookback)
cur = fl[fl["month_end"] == SC.AS_OF]
by_cls = cur.groupby("contraction_class").agg(n=("customer_id", "size"), arr=("current_arr", "sum"), lost=("lost_arr", "sum"))
n_silent = int(by_cls["n"].get("silent", 0))
n_saved = int(by_cls["n"].get("saved_re_risk", 0))
n_flagged = n_silent + n_saved
arr_risk = float(cur["current_arr"].sum())
lost = float(cur["lost_arr"].sum())
prof = d["profile"]
tech = prof[(prof["dimension"] == "industry") & (prof["value"] == "Technology")].iloc[0]
rate = d["rate"].set_index(["month_end", "group"])["rate"]
JUNE = pd.Timestamp("2026-06-30")
jun = d["trend"][d["trend"]["month_end"] == JUNE]
june_total = int(jun["accounts"].sum())
june_silent = int(jun.loc[jun["contraction_class"] == "silent", "accounts"].sum())
june_saved = int(jun.loc[jun["contraction_class"] == "saved_re_risk", "accounts"].sum())

# KPI tiles: labels ≤ ~22 characters and deltas ≤ ~21 so nothing truncates at 1440 px; detail lives in help=
c = st.columns(5)
c[0].metric("Flagged accounts", f"{n_flagged:,}", f"{n_silent:,} silent + {n_saved:,} saved", delta_color="off", delta_arrow="off", border=True,
            help="Accounts flagged at 30 Sep 2026 under the §9 definition. Silent = the contraction came from the customer (seat cuts, "
                 "right-sizing, downgrades); saved = the account asked to cancel and was kept on smaller terms by a CS save offer "
                 "(change_source = customer_success), reported separately.")
c[1].metric("Since June 2026", delta_pct(n_flagged / june_total - 1, 0) if june_total else "–",
            f"{june_total:,} → {n_flagged:,} flagged" if june_total else "", delta_color="inverse", border=True,
            help=f"All flagged accounts, 30 Jun 2026 → 30 Sep 2026, same definition and sliders. Silent only: {june_silent:,} → {n_silent:,} "
                 f"({delta_pct(n_silent / june_silent - 1, 0) if june_silent else '–'}); saved by CS: {june_saved:,} → {n_saved:,}.")
c[2].metric("ARR still at risk", money(arr_risk, 2), f"{pct(arr_risk / total_arr)} of ARR", delta_color="off", delta_arrow="off", border=True,
            help="What the flagged accounts pay today (12 × current MRR), as a share of total ARR at 30 Sep 2026.")
c[3].metric("ARR already lost", money(lost), "gap to 6-month peak", delta_color="off", delta_arrow="off", border=True,
            help=f"12 × (peak MRR over the previous {lookback} month-ends − current MRR), summed over the flagged accounts.")
c[4].metric("Technology share", pct(tech["share_flagged"], 0), delta_pts(tech["share_flagged"] - tech["share_other"], 0, " vs others"),
            delta_color="inverse", border=True,
            help=f"Share of flagged accounts in Technology vs the Technology share of the other paying accounts "
                 f"({pct(tech['share_other'], 0)}); over-representation index {tech['index']:.1f}×.")

finding(f"<b>{n_flagged:,} accounts ({money(arr_risk, 2)} ARR, {pct(arr_risk / total_arr)} of the base) are paying "
        f"{pct(cur['decline_pct_norm'].median(), 0)} less than their recent peak at the median, and {money(lost)} of ARR is already gone.</b> "
        f"Technology accounts are {tech['index']:.1f}× over-represented ({int(tech['flagged'])} of {len(cur)}): their flag rate went from "
        f"{pct(rate.get((JUNE, 'Technology'), float('nan')), 1)} in June to {pct(tech['flag_rate'], 1)} in September 2026, while every other "
        f"industry stayed near {pct(rate.get((SC.AS_OF, 'All other industries'), float('nan')), 1)} — consistent with the hiring slowdown CSMs "
        "reported, arriving as seat cuts at billing dates (a sector correlation: there is no account-level headcount data).")

plot(SC.fig_flagged_trend(d["trend"], threshold, lookback))
data_table(d["trend"], "Show the monthly table",
           column_config={"month_end": DateCol("Month-end", format="YYYY-MM-DD"), "contraction_class": TextCol("Class"),
                          "accounts": NumCol("Accounts", format="localized"), "current_arr": NumCol("ARR at risk", format="dollar"),
                          "lost_arr": NumCol("ARR already lost", format="dollar"), "technology_accounts": NumCol("Technology accounts")})
plot(SC.fig_tech_flag_rate(d["rate"]))

st.markdown("**Who they are, and what the contraction looked like**")
cc = st.columns([1, 2])
dim = cc[0].selectbox("Profile dimension", list(SC.PROFILE_DIMS), format_func=SC.PROFILE_DIMS.get, index=1)
plot(SC.fig_overrepresentation(prof, dim))
data_table(prof[prof["dimension"] == dim][["value", "flagged", "other", "share_flagged", "share_other", "index", "flag_rate"]],
           "Show the profile table",
           column_config={"value": TextCol(SC.PROFILE_DIMS[dim]), "flagged": NumCol("Flagged"), "other": NumCol("Other paying"),
                          "share_flagged": NumCol("Share of flagged", **PCT), "share_other": NumCol("Share of other", **PCT),
                          "index": NumCol("Index", format="%.2f×"), "flag_rate": NumCol("Flag rate", **PCT)})
plot(SC.fig_contraction_mix(d["mix"]))
data_table(d["mix"], "Show the contraction-type table",
           column_config={"kind": TextCol("Contraction type"), "group": TextCol("Industry group"), "accounts": NumCol("Accounts"),
                          "mrr_lost": NumCol("MRR lost", format="dollar")})

st.markdown("**What happens next**")
plot(SC.fig_followup(d["followup"]))
data_table(d["followup"][["label", "n", "churn_6m", "churn_6m_lo", "churn_6m_hi", "loss_6m", "loss_6m_lo", "loss_6m_hi"]],
           "Show the follow-up table",
           column_config={"label": TextCol("Group", width="large"), "n": NumCol("Rows", format="localized"),
                          "churn_6m": NumCol("Churned, 6 mo", **PCT), "churn_6m_lo": NumCol("Low (95%)", **PCT), "churn_6m_hi": NumCol("High (95%)", **PCT),
                          "loss_6m": NumCol("Churned or contracted, 6 mo", **PCT), "loss_6m_lo": NumCol("Low (95%) ", **PCT),
                          "loss_6m_hi": NumCol("High (95%) ", **PCT)})
st.markdown("<div class='note'>Intervals are Wilson intervals on the rows: one row per newly flagged account for the flagged groups, but "
            "account-months for the unflagged comparison, whose interval therefore ignores within-account correlation and is narrower "
            "than an account-level interval would be.</div>", unsafe_allow_html=True)

# ------------------------------------------------------------------ 2. early warning (02 §9.1)
st.subheader("2. Can we see it coming?")
grid = load("silent_churn__backtest_grid")
u0, w0 = SC.rule_thresholds()
cc = st.columns([2, 2, 3])
util_thr = cc[0].slider("Flag when 13-week seat utilization is below", min_value=40, max_value=80, value=int(round(u0 * 100)), step=5,
                        format="%d%%") / 100
use_trend = cc[1].toggle("Also require a WAU drop", value=w0 is not None,
                         help="Average WAU in the last 4 complete weeks vs the 9 weeks before them.")
trend_thr = None
if use_trend:
    trend_thr = cc[1].slider("WAU trend at or below", min_value=-40, max_value=0, value=int(round((w0 if w0 is not None else -0.15) * 100)),
                             step=5, format="%d%%") / 100
cc[2].markdown(
    "<div class='note'>Back-test on every eligible account-month (paying, no open cancellation, no contraction in the last "
    "3 months, 13 complete weeks of usage, outcome window fully observed). Outcome = billing-neutral MRR down ≥ 10% or churn "
    "within 3 months (monthly-billed) or by the next renewal (annual-billed, within 6 months). Precision = share of flags "
    "that contract; recall = share of contracting account-months flagged; lift = precision ÷ base rate. Intervals: 95% "
    "account-level bootstrap (accounts resampled with replacement, their month-ends travelling together).</div>",
    unsafe_allow_html=True)

r = rule_for(util_thr, trend_thr)
ev, chosen, bs = r["all"], (u0, w0), r["bootstrap"]
is_chosen = abs(util_thr - u0) < 1e-9 and ((trend_thr is None and w0 is None) or (trend_thr is not None and w0 is not None and abs(trend_thr - w0) < 1e-9))
bq = r["by_quarter"]
c = st.columns(5)
c[0].metric("Precision", pct(ev["precision"]), f"95%: {pct(bs.loc['all', 'precision_lo'])}–{pct(bs.loc['all', 'precision_hi'])}",
            delta_color="off", delta_arrow="off", border=True,
            help=f"Share of flagged account-months that contracted ≥ 10% or churned within the outcome window; base rate {pct(ev['base_rate'])}. "
                 f"By quarter ({SC.STABILITY_QUARTERS[0]} – {SC.STABILITY_QUARTERS[-1]}): {pct(bq['precision'].min(), 0)}–{pct(bq['precision'].max(), 0)}.")
c[1].metric("Recall", pct(ev["recall"]), f"95%: {pct(bs.loc['all', 'recall_lo'])}–{pct(bs.loc['all', 'recall_hi'])}",
            delta_color="off", delta_arrow="off", border=True,
            help=f"Share of contracting account-months flagged. {pct(ev['event_recall'], 0)} of contraction events and "
                 f"{pct(ev['account_recall'], 0)} of contracting accounts were flagged at least once before the outcome. "
                 f"By quarter: {pct(bq['recall'].min(), 0)}–{pct(bq['recall'].max(), 0)}.")
c[2].metric("Lift", f"{ev['lift']:.2f}×", f"{pct(ev['base_rate'])} base · 2× bar", delta_color="normal" if ev["lift"] >= 2 else "inverse",
            delta_arrow="off", border=True, help="Precision ÷ base rate; the selection criterion requires at least 2×.")
c[3].metric("Flags per month", f"{ev['flags_per_month']:.0f}", f"{ev['csm_accounts_per_quarter']:.0f} CSM accounts / qtr",
            delta_color="normal" if ev["csm_accounts_per_quarter"] <= SC.CSM_CAPACITY_PER_QUARTER else "inverse", delta_arrow="off",
            border=True, help=f"Flagged account-months per month (Jul 2025 – Jun 2026) and distinct CSM-covered accounts flagged per quarter "
                              f"(average over Q3 2025 – Q2 2026); CS capacity {SC.CSM_CAPACITY_PER_QUARTER} reviews a quarter (assumption A9).")
c[4].metric("Median lead, monthly", f"{r['monthly']['lead_median']:.0f} month-ends", f"{r['annual']['lead_median']:.0f} for annual-billed",
            delta_color="off", delta_arrow="off", border=True,
            help=f"Month-ends from the first flag of the run to the contraction month, one run per caught event. Pooled median "
                 f"{ev['lead_median']:.0f} (it moves with the monthly / annual mix, so quote the by-billing medians); "
                 f"{pct(ev['share_lead_ge2'], 0)} of caught events were flagged at least 2 month-ends ahead.")
finding((f"<b>Chosen rule (02 §9.1 v1.2): 13-week seat utilization below {pct(u0, 0)}" + ("" if w0 is None else f" and WAU trend ≤ {pct(w0, 0)}")
         + ".</b> " if is_chosen else f"<b>Slider setting, not the chosen rule</b> (chosen: utilization &lt; {pct(u0, 0)}"
         + ("" if w0 is None else f" and WAU trend ≤ {pct(w0, 0)}") + "). ")
        + f"Criterion: the highest recall among threshold pairs with precision ≥ 2× the base rate and no more than "
        f"{SC.CSM_CAPACITY_PER_QUARTER} distinct CSM-covered accounts flagged per quarter. The v1.0 draft (utilization &lt; 60% and WAU "
        f"trend ≤ −15%) has lift {k['doc_default_rule']['lift']:.2f}× and recall {pct(k['doc_default_rule']['recall'], 0)}: requiring a WAU drop "
        "removes flags without making the remaining ones more accurate, so the v1.2 rule uses utilization only; the WAU drop stays as a "
        "reason code. Annual-billed accounts flag with higher precision "
        f"({pct(r['annual']['precision'], 0)} vs {pct(r['monthly']['precision'], 0)}) and a longer lead (median {r['annual']['lead_median']:.0f} vs "
        f"{r['monthly']['lead_median']:.0f} month-ends), because they can only contract at renewal; {pct(ev['share_lead_ge2'], 0)} of caught events "
        f"were flagged at least 2 month-ends ahead. Selection and evaluation share the same rows, so the per-quarter check matters: precision "
        f"{pct(bq['precision'].min(), 0)}–{pct(bq['precision'].max(), 0)} and recall {pct(bq['recall'].min(), 0)}–{pct(bq['recall'].max(), 0)} in every "
        f"quarter from {SC.STABILITY_QUARTERS[0]} to {SC.STABILITY_QUARTERS[-1]}.")
plot(SC.fig_pr_tradeoff(grid, chosen, (util_thr, trend_thr)))
grid_cfg = {"utilization_threshold": NumCol("Utilization <", **PCT), "wau_trend_threshold": NumCol("WAU trend ≤", **PCT),
            "flagged_rows": NumCol("Flagged rows", format="localized"), "precision": NumCol("Precision", **PCT), "recall": NumCol("Recall", **PCT),
            "lift": NumCol("Lift", format="%.2f×"), "flags_per_month": NumCol("Flags / month", format="%.0f"),
            "csm_accounts_per_quarter": NumCol("CSM accounts / qtr", format="%.0f"), "event_recall": NumCol("Event recall", **PCT),
            "lead_median": NumCol("Median lead", format="%.0f"), "share_lead_ge2": NumCol("Lead ≥ 2", **PCT),
            "passes_precision": st.column_config.CheckboxColumn("Lift ≥ 2×"), "passes_capacity": st.column_config.CheckboxColumn("Within capacity"),
            "chosen": st.column_config.CheckboxColumn("Chosen")}
data_table(grid[["utilization_threshold", "wau_trend_threshold", "flagged_rows", "precision", "recall", "lift", "flags_per_month",
                 "csm_accounts_per_quarter", "event_recall", "lead_median", "share_lead_ge2", "passes_precision", "passes_capacity", "chosen"]],
           "Show the full grid", column_config=grid_cfg)
plot(SC.fig_lead_time(r["leads"], (util_thr, trend_thr)))
bp_tbl = pd.DataFrame([r["monthly"] | {"billing_period": "monthly"}, r["annual"] | {"billing_period": "annual"}, ev | {"billing_period": "all"}])
for col in ("precision", "recall"):
    bp_tbl[f"{col}_lo"] = bp_tbl["billing_period"].map(bs[f"{col}_lo"])
    bp_tbl[f"{col}_hi"] = bp_tbl["billing_period"].map(bs[f"{col}_hi"])
data_table(bp_tbl[["billing_period", "eligible_rows", "base_rate", "flagged_rows", "precision", "precision_lo", "precision_hi", "recall", "recall_lo",
                   "recall_hi", "lift", "flags_per_month", "csm_flags_per_month", "event_recall", "account_recall", "lead_median", "lead_p25",
                   "lead_p75", "share_lead_ge2"]],
           "Show the back-test by billing period",
           column_config={"billing_period": TextCol("Billing"), "eligible_rows": NumCol("Eligible rows", format="localized"),
                          "base_rate": NumCol("Base rate", **PCT), "flagged_rows": NumCol("Flagged rows", format="localized"),
                          "precision": NumCol("Precision", **PCT), "precision_lo": NumCol("Low (95%)", **PCT), "precision_hi": NumCol("High (95%)", **PCT),
                          "recall": NumCol("Recall", **PCT), "recall_lo": NumCol("Low (95%) ", **PCT), "recall_hi": NumCol("High (95%) ", **PCT),
                          "lift": NumCol("Lift", format="%.2f×"), "flags_per_month": NumCol("Flags / month", format="%.0f"),
                          "csm_flags_per_month": NumCol("CSM flags / month", format="%.0f"), "event_recall": NumCol("Event recall", **PCT),
                          "account_recall": NumCol("Account recall", **PCT), "lead_median": NumCol("Median lead", format="%.0f"),
                          "lead_p25": NumCol("Lead p25", format="%.1f"), "lead_p75": NumCol("Lead p75", format="%.1f"),
                          "share_lead_ge2": NumCol("Lead ≥ 2", **PCT)})
data_table(bq[["quarter_label", "eligible_rows", "positives", "base_rate", "flagged_rows", "true_positives", "precision", "recall", "lift", "csm_accounts"]],
           "Show the back-test by quarter",
           column_config={"quarter_label": TextCol("Quarter"), "eligible_rows": NumCol("Eligible rows", format="localized"),
                          "positives": NumCol("With outcome", format="localized"), "base_rate": NumCol("Base rate", **PCT),
                          "flagged_rows": NumCol("Flagged rows", format="localized"), "true_positives": NumCol("True positives", format="localized"),
                          "precision": NumCol("Precision", **PCT), "recall": NumCol("Recall", **PCT), "lift": NumCol("Lift", format="%.2f×"),
                          "csm_accounts": NumCol("CSM accounts flagged")})

# ------------------------------------------------------------------ 3. watchlist (02 §15)
st.subheader("3. The watchlist")
wl = watchlist_for(threshold, lookback, util_thr, trend_thr)
bp_rates = wl.attrs.get("bucket_precision", {})
csm_q = wl[wl["queue"] == "csm"]
top = csm_q.head(SC.CSM_CAPACITY_PER_QUARTER)
n_urgent = int((wl["priority_tier"] == 1).sum())
rising = wl["suggested_play"].str.startswith("Usage is growing into the seats")   # early warning + WAU up, not a CS save
top_share = top["arr_at_stake"].sum() / max(wl["arr_at_stake"].sum(), 1e-9)
c = st.columns(5)
c[0].metric("Accounts on the list", f"{len(wl):,}", f"{len(csm_q):,} CSM · {int((wl['queue'] == 'digital').sum()):,} digital",
            delta_color="off", delta_arrow="off", border=True,
            help="Union of the §9 flags (silent / saved) and the early-warning flags at 30 Sep 2026. CSM = named or pooled CSM coverage; "
                 "digital = tech-touch accounts.")
c[1].metric("ARR on the list", money(wl["current_arr"].sum()), f"{pct(wl['current_arr'].sum() / total_arr)} of ARR", delta_color="off",
            delta_arrow="off", border=True, help="Current ARR (12 × MRR at 30 Sep 2026) of every account on the list.")
c[2].metric("ARR at stake", money(wl["arr_at_stake"].sum()), "ARR × flag hit rate", delta_color="off", delta_arrow="off", border=True,
            help=f"Current ARR × the historical hit rate of the flag that fired — early warning: {pct(bp_rates.get('early_warning_monthly', float('nan')), 0)} "
                 f"(monthly-billed) / {pct(bp_rates.get('early_warning_annual', float('nan')), 0)} (annual-billed) contract ≥ 10% or churn within the "
                 f"outcome window; silent contraction: {pct(bp_rates.get('silent', float('nan')), 0)} churn or contract again within 6 months; CS saves: "
                 f"{pct(bp_rates.get('saved_re_risk', float('nan')), 0)} (small sample). An account with both flags takes the higher rate.")
c[3].metric("Renewals ≤ 120 days", f"{int(wl['renewal_within_120d'].sum()):,}", f"{n_urgent:,} in under {SC.URGENT_RENEWAL_DAYS} days",
            delta_color="off", delta_arrow="off", border=True,
            help=f"Annual terms ending within 120 days of 30 Sep 2026 ({money(wl.loc[wl['renewal_within_120d'], 'current_arr'].sum())} ARR). "
                 "Monthly-billed accounts can leave at any billing date, so they carry no renewal flag.")
c[4].metric("Top 120 CSM share", pct(top_share, 0), "vs the 60% trigger", delta_color="normal" if top_share >= 0.60 else "inverse",
            delta_arrow="off", border=True,
            help=f"Share of the whole list's ARR at stake held by the first {SC.CSM_CAPACITY_PER_QUARTER} accounts of the CSM queue; the Head of CS "
                 "staffs CSM-led reviews for them if the share is at least 60% (scope doc §3).")
st.markdown(
    f"<div class='note'>Priority within each queue, in three tiers: (1) annual renewals in under {SC.URGENT_RENEWAL_DAYS} days, soonest first, "
    f"so a CSM working top-down reaches them before the date passes; (2) annual renewals in {SC.URGENT_RENEWAL_DAYS}–120 days, by ARR at stake; "
    f"(3) everything else, by ARR at stake. {int(rising.sum()):,} early-warning accounts ({int((rising & (wl['queue'] == 'csm')).sum()):,} CSM-covered) "
    f"have WAU up {pct(SC.WAU_UP_CODE, 0)} or more on under-used seats: their play is a seat-count conversation before renewal, not an idle-seat "
    "save (reason code WAU_UP_20PCT). Monthly-billed accounts can leave at any billing date, so they carry no renewal flag.</div>",
    unsafe_allow_html=True)
cc = st.columns([1, 1, 2])
queue = cc[0].multiselect("Queue", ["csm", "digital"], default=[], placeholder="All",
                          format_func={"csm": "CSM (named + pooled)", "digital": "Digital (tech-touch)"}.get)
src = cc[1].multiselect("Flag source", sorted(wl["flag_source"].unique()), default=[], placeholder="All")
show = wl.copy()
show["csm_owner"] = show["csm_owner"].fillna("—")
if queue:
    show = show[show["queue"].isin(queue)]
if src:
    show = show[show["flag_source"].isin(src)]
st.dataframe(
    show[["priority_rank", "priority_tier", "queue", "customer_id", "company_name", "segment", "industry", "cs_coverage", "csm_owner", "plan",
          "billing_period", "seats", "current_arr", "arr_at_stake", "next_renewal_date", "days_to_renewal", "utilization_13w", "wau_trend",
          "flag_source", "reason_code_1", "reason_code_2", "reason_code_3", "evidence", "suggested_play"]],
    hide_index=True, width="stretch", height=480,
    column_config={"priority_rank": NumCol("Rank", width="small"),
                   "priority_tier": NumCol("Tier", width="small", help=f"1 = annual renewal in under {SC.URGENT_RENEWAL_DAYS} days (by date); "
                                                                       "2 = renewal in 45–120 days (by ARR at stake); 3 = the rest (by ARR at stake)"),
                   "queue": TextCol("Queue", width="small"), "customer_id": TextCol("Customer"), "company_name": TextCol("Company"),
                   "segment": TextCol("Segment"), "industry": TextCol("Industry"), "cs_coverage": TextCol("CS coverage"),
                   "csm_owner": TextCol("CSM owner"), "plan": TextCol("Plan"), "billing_period": TextCol("Billing"),
                   "seats": NumCol("Seats", format="localized"),
                   "current_arr": NumCol("Current ARR", format="dollar"),
                   "arr_at_stake": NumCol("ARR at stake", format="dollar"),
                   "next_renewal_date": DateCol("Next renewal / billing", format="YYYY-MM-DD"),
                   "days_to_renewal": NumCol("Days", width="small"),
                   "utilization_13w": NumCol("Utilization, 13 wk", **PCT), "wau_trend": NumCol("WAU trend", **PCT),
                   "flag_source": TextCol("Flag source"), "reason_code_1": TextCol("Reason 1"), "reason_code_2": TextCol("Reason 2"),
                   "reason_code_3": TextCol("Reason 3"),
                   "evidence": TextCol("Evidence", width="large"),
                   "suggested_play": TextCol("Suggested play", width="large")})
st.download_button("Download the watchlist (CSV)", wl.to_csv(index=False).encode("utf-8"),
                   file_name=f"silent_churn_watchlist_{SC.AS_OF:%Y%m%d}.csv", mime="text/csv")
with st.expander("Reason codes", expanded=False, icon=":material/info:"):
    st.markdown("\n".join(f"- `{c}` — {t}" for c, t in SC.REASON_CODES.items()
                          if c != "UTIL_BELOW_50" or SC.util_code(util_thr) == "UTIL_BELOW_50")
                + ("" if SC.util_code(util_thr) == "UTIL_BELOW_50" else
                   f"\n- `{SC.util_code(util_thr)}` — seat utilization over the last 13 complete weeks is below {pct(util_thr, 0)} (slider setting)"))

how_to_read("""
**Silent contraction (section 1).** A customer who keeps paying but pays materially less. We measure spend on a
*billing-neutral* basis (every plan priced at its monthly list price), so a customer who switches to annual billing and
pays 20% less is not flagged — only seat cuts, plan downgrades and extra discounts count. The two sliders change the
definition's parameters and recompute everything in this section. **Flagged accounts** counts both classes; *Saved by CS*
accounts asked to cancel and were kept on a smaller plan — a good outcome, but they are watched separately. **ARR still at
risk** is what the flagged accounts pay today; **ARR already lost** is the gap to their recent peak.

**The flagged-ARR chart** shows the size of the problem over time (silent and saved stacked); the **flag-rate chart** shows
that the 2026 jump is a Technology story — a sector correlation, since we have no account-level headcount data.
**Over-representation** compares the flagged accounts with every other paying account: an index of 2.6× means Technology
is 2.6 times as common among flagged accounts as among the rest. **Contraction type** says how the money left (mostly
seats removed at a monthly billing date). **What happens next** follows accounts for six months after they are first
flagged and compares them with unflagged accounts of the same segment, billing period and tenure: flagged accounts are
not much more likely to churn than their peers, so the loss is mostly already booked. The whiskers are row-level
intervals; the unflagged comparison's rows are account-months, so its whisker is narrower than an account-level one.

**Early warning (section 2).** A rule that fires *before* the contraction, tested on history exactly as it would have
been used: at each month-end, could the signals visible then have flagged the accounts that contracted or churned
soon after? Precision is the share of flags that were right; recall is the share of contractions we would have
caught; lift compares precision with simply guessing. The intervals on the tiles come from resampling accounts, not
rows, so they allow for the ~10 month-ends each account contributes. The trade-off chart shows every threshold pair;
move the sliders and the metrics, the lead-time chart and the current flags recompute. The operating point was chosen
on the same rows it is scored on, so the per-quarter table is the check that it is not a lucky grid cell. Lead time
is how many month-ends before the contraction the flag first fired, one run per caught event; quote the monthly-billed
and annual-billed medians rather than the pooled one, which moves with the mix. Annual-billed accounts can only shrink
at renewal, so they flag earlier and more precisely.

**The watchlist (section 3)** combines both lists for CS, ranked within each queue in three tiers: annual renewals in
under 45 days by date, then renewals within 120 days by ARR at stake (current ARR × the flag's historical hit rate),
then everything else by ARR at stake. Reason codes say why the account is there; the evidence column gives the numbers
to say to the customer. An early-warning account whose weekly active users rose 20% or more is growing into its seats:
its play is to confirm team size and seat count before renewal, not an idle-seat save.
""")
