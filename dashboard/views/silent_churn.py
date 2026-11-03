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
arr_risk = float(cur["current_arr"].sum())
lost = float(cur["lost_arr"].sum())
tr_tot = d["trend"].groupby("month_end")[["accounts", "current_arr"]].sum()
june = tr_tot.loc[pd.Timestamp("2026-06-30")] if pd.Timestamp("2026-06-30") in tr_tot.index else None
prof = d["profile"]
tech = prof[(prof["dimension"] == "industry") & (prof["value"] == "Technology")].iloc[0]

c = st.columns(5)
c[0].metric("Silent accounts", f"{n_silent:,}", delta_pct(n_silent / june["accounts"] - 1, 0, " vs Jun 2026") if june is not None else "",
            delta_color="inverse", border=True, help="Contraction came from the customer: seat cuts, right-sizing, downgrades.")
c[1].metric("Saved by CS (re-risk)", f"{n_saved:,}", "reported separately", delta_color="off", delta_arrow="off", border=True,
            help="The account asked to cancel and was kept on smaller terms (change_source = customer_success).")
c[2].metric("ARR still at risk", money(arr_risk, 2), f"{pct(arr_risk / total_arr)} of ARR", delta_color="off", delta_arrow="off", border=True)
c[3].metric("ARR already lost", money(lost), "peak MRR − current MRR, × 12", delta_color="off", delta_arrow="off", border=True)
c[4].metric("Technology share", pct(tech["share_flagged"], 0), delta_pts(tech["share_flagged"] - tech["share_other"], 0, " vs other paying accounts"),
            delta_color="inverse", border=True)

finding(f"<b>{n_silent + n_saved:,} accounts ({money(arr_risk, 2)} ARR, {pct(arr_risk / total_arr)} of the base) are paying "
        f"{pct(cur['decline_pct_norm'].median(), 0)} less than their recent peak at the median, and {money(lost)} of ARR is already gone.</b> "
        f"Technology accounts are {tech['index']:.1f}× over-represented ({int(tech['flagged'])} of {len(cur)}): their flag rate went from "
        f"{pct(d['rate'].set_index(['month_end', 'group'])['rate'].get((pd.Timestamp('2026-06-30'), 'Technology'), float('nan')), 1)} in June to "
        f"{pct(tech['flag_rate'], 1)} in September 2026, while every other industry stayed near "
        f"{pct(d['rate'].set_index(['month_end', 'group'])['rate'].get((SC.AS_OF, 'All other industries'), float('nan')), 1)} — "
        "consistent with the hiring slowdown CSMs reported, arriving as seat cuts at billing dates.")

plot(SC.fig_flagged_trend(d["trend"], threshold, lookback))
data_table(d["trend"], "Show the monthly table")
plot(SC.fig_tech_flag_rate(d["rate"]))

st.markdown("**Who they are, and what the contraction looked like**")
cc = st.columns([1, 2])
dim = cc[0].selectbox("Profile dimension", list(SC.PROFILE_DIMS), format_func=SC.PROFILE_DIMS.get, index=1)
plot(SC.fig_overrepresentation(prof, dim))
data_table(prof[prof["dimension"] == dim][["value", "flagged", "other", "share_flagged", "share_other", "index", "flag_rate"]],
           "Show the profile table")
plot(SC.fig_contraction_mix(d["mix"]))
data_table(d["mix"], "Show the contraction-type table")

st.markdown("**What happens next**")
plot(SC.fig_followup(d["followup"]))
data_table(d["followup"][["label", "n", "churn_6m", "churn_6m_lo", "churn_6m_hi", "loss_6m", "loss_6m_lo", "loss_6m_hi"]],
           "Show the follow-up table")

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
    "that contract; recall = share of contracting account-months flagged; lift = precision ÷ base rate.</div>",
    unsafe_allow_html=True)

r = rule_for(util_thr, trend_thr)
ev, chosen = r["all"], (u0, w0)
is_chosen = abs(util_thr - u0) < 1e-9 and ((trend_thr is None and w0 is None) or (trend_thr is not None and w0 is not None and abs(trend_thr - w0) < 1e-9))
c = st.columns(5)
c[0].metric("Precision", pct(ev["precision"]), f"base rate {pct(ev['base_rate'])}", delta_color="off", delta_arrow="off", border=True)
c[1].metric("Recall", pct(ev["recall"]), f"{pct(ev['event_recall'], 0)} of contraction events caught", delta_color="off", delta_arrow="off",
            border=True)
c[2].metric("Lift", f"{ev['lift']:.2f}×", "target ≥ 2×", delta_color="normal" if ev["lift"] >= 2 else "inverse",
            delta_arrow="off", border=True)
c[3].metric("Flags per month", f"{ev['flags_per_month']:.0f}",
            f"{ev['csm_accounts_per_quarter']:.0f} CSM-covered accounts / quarter (capacity {SC.CSM_CAPACITY_PER_QUARTER})",
            delta_color="normal" if ev["csm_accounts_per_quarter"] <= SC.CSM_CAPACITY_PER_QUARTER else "inverse", delta_arrow="off",
            border=True)
c[4].metric("Median lead time", f"{ev['lead_median']:.0f} month-ends", f"{pct(ev['share_lead_ge2'], 0)} flagged ≥ 2 ahead",
            delta_color="off", delta_arrow="off", border=True)
finding((f"<b>Chosen rule (02 §9.1 v1.2): 13-week seat utilization below {pct(u0, 0)}" + ("" if w0 is None else f" and WAU trend ≤ {pct(w0, 0)}")
         + ".</b> " if is_chosen else f"<b>Slider setting, not the chosen rule</b> (chosen: utilization &lt; {pct(u0, 0)}"
         + ("" if w0 is None else f" and WAU trend ≤ {pct(w0, 0)}") + "). ")
        + f"Criterion: the highest recall among threshold pairs with precision ≥ 2× the base rate and no more than "
        f"{SC.CSM_CAPACITY_PER_QUARTER} distinct CSM-covered accounts flagged per quarter. The v1.0 draft (utilization &lt; 60% and WAU "
        f"trend ≤ −15%) has lift {k['doc_default_rule']['lift']:.2f}× and recall {pct(k['doc_default_rule']['recall'], 0)}: requiring a WAU drop "
        "removes flags without making the remaining ones more accurate, so the v1.2 rule uses utilization only; the WAU drop stays as a "
        "reason code. Annual-billed accounts flag with higher precision "
        f"({pct(r['annual']['precision'], 0)} vs {pct(r['monthly']['precision'], 0)}) and longer lead (median {r['annual']['lead_median']:.0f} vs "
        f"{r['monthly']['lead_median']:.0f} month-ends), because they can only contract at renewal.")
plot(SC.fig_pr_tradeoff(grid, chosen, (util_thr, trend_thr)))
data_table(grid[["utilization_threshold", "wau_trend_threshold", "flagged_rows", "precision", "recall", "lift", "flags_per_month",
                 "csm_accounts_per_quarter", "event_recall", "lead_median", "share_lead_ge2", "passes_precision", "passes_capacity", "chosen"]],
           "Show the full grid")
plot(SC.fig_lead_time(r["leads"], (util_thr, trend_thr)))
bp_tbl = pd.DataFrame([r["monthly"] | {"billing_period": "monthly"}, r["annual"] | {"billing_period": "annual"}, ev | {"billing_period": "all"}])
data_table(bp_tbl[["billing_period", "eligible_rows", "base_rate", "flagged_rows", "precision", "recall", "lift", "flags_per_month",
                   "csm_flags_per_month", "event_recall", "account_recall", "lead_median", "lead_p25", "lead_p75", "share_lead_ge2"]],
           "Show the back-test by billing period")

# ------------------------------------------------------------------ 3. watchlist (02 §15)
st.subheader("3. The watchlist")
wl = watchlist_for(threshold, lookback, util_thr, trend_thr)
bp_rates = wl.attrs.get("bucket_precision", {})
csm_q = wl[wl["queue"] == "csm"]
top = csm_q.head(SC.CSM_CAPACITY_PER_QUARTER)
c = st.columns(5)
c[0].metric("Accounts on the list", f"{len(wl):,}", f"{len(csm_q):,} CSM-covered, {int((wl['queue'] == 'digital').sum()):,} digital",
            delta_color="off", delta_arrow="off", border=True)
c[1].metric("Current ARR on the list", money(wl["current_arr"].sum()), f"{pct(wl['current_arr'].sum() / total_arr)} of ARR", delta_color="off",
            delta_arrow="off", border=True)
c[2].metric("ARR at stake", money(wl["arr_at_stake"].sum()), "current ARR × historical hit rate of the flag", delta_color="off",
            delta_arrow="off", border=True)
c[3].metric("Annual renewals within 120 days", f"{int(wl['renewal_within_120d'].sum()):,}",
            money(wl.loc[wl["renewal_within_120d"], "current_arr"].sum()) + " ARR", delta_color="off", delta_arrow="off", border=True)
c[4].metric(f"Top {SC.CSM_CAPACITY_PER_QUARTER} CSM accounts cover", pct(top["arr_at_stake"].sum() / max(wl["arr_at_stake"].sum(), 1e-9), 0),
            "of ARR at stake (CS decision trigger: 60%)", delta_color="off", delta_arrow="off", border=True)
st.markdown(
    f"<div class='note'>Hit rates used for ARR at stake — early-warning flag: {pct(bp_rates.get('early_warning_monthly', float('nan')), 0)} "
    f"(monthly-billed) / {pct(bp_rates.get('early_warning_annual', float('nan')), 0)} (annual-billed) contract ≥ 10% or churn within the "
    f"outcome window; silent contraction: {pct(bp_rates.get('silent', float('nan')), 0)} churn or contract again within 6 months; "
    f"CS saves: {pct(bp_rates.get('saved_re_risk', float('nan')), 0)} (small sample). Priority within each queue: annual renewals within "
    "120 days first, then ARR at stake. Monthly-billed accounts can leave at any billing date, so they carry no renewal flag.</div>",
    unsafe_allow_html=True)
cc = st.columns([1, 1, 2])
queue = cc[0].multiselect("Queue", ["csm", "digital"], default=[], placeholder="All",
                          format_func={"csm": "CSM (named + pooled)", "digital": "Digital (tech-touch)"}.get)
src = cc[1].multiselect("Flag source", sorted(wl["flag_source"].unique()), default=[], placeholder="All")
show = wl.copy()
if queue:
    show = show[show["queue"].isin(queue)]
if src:
    show = show[show["flag_source"].isin(src)]
st.dataframe(
    show[["priority_rank", "queue", "customer_id", "company_name", "segment", "industry", "cs_coverage", "csm_owner", "plan", "billing_period",
          "seats", "current_arr", "arr_at_stake", "next_renewal_date", "days_to_renewal", "flag_source", "reason_code_1", "reason_code_2",
          "reason_code_3", "evidence", "suggested_play"]],
    hide_index=True, width="stretch", height=480,
    column_config={"priority_rank": st.column_config.NumberColumn("Rank", width="small"),
                   "current_arr": st.column_config.NumberColumn("Current ARR", format="$%.0f"),
                   "arr_at_stake": st.column_config.NumberColumn("ARR at stake", format="$%.0f"),
                   "next_renewal_date": st.column_config.DateColumn("Next renewal / billing", format="YYYY-MM-DD"),
                   "days_to_renewal": st.column_config.NumberColumn("Days", width="small"),
                   "evidence": st.column_config.TextColumn("Evidence", width="large"),
                   "suggested_play": st.column_config.TextColumn("Suggested play", width="large")})
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
definition's parameters and recompute everything in this section. **ARR still at risk** is what the flagged accounts pay
today; **ARR already lost** is the gap to their recent peak. *Saved by CS* accounts asked to cancel and were kept on a
smaller plan — a good outcome, but they are watched separately.

**The flagged-ARR chart** shows the size of the problem over time; the **flag-rate chart** shows that the 2026 jump is a
Technology story. **Over-representation** compares the flagged accounts with every other paying account: an index of
2.6× means Technology is 2.6 times as common among flagged accounts as among the rest. **Contraction type** says how
the money left (mostly seats removed at a monthly billing date). **What happens next** follows accounts for six months
after they are first flagged and compares them with unflagged accounts of the same segment, billing period and tenure:
flagged accounts are not much more likely to churn than their peers, so the loss is mostly already booked.

**Early warning (section 2).** A rule that fires *before* the contraction, tested on history exactly as it would have
been used: at each month-end, could the signals visible then have flagged the accounts that contracted or churned
soon after? Precision is the share of flags that were right; recall is the share of contractions we would have
caught; lift compares precision with simply guessing. The trade-off chart shows every threshold pair; move the
sliders and the metrics, the lead-time chart and the current flags recompute. Lead time is how many month-ends
before the contraction the flag first fired. Annual-billed accounts can only shrink at renewal, so they flag earlier
and more precisely.

**The watchlist (section 3)** combines both lists for CS, ranked within each queue: annual renewals within 120 days
first, then ARR at stake (current ARR × the flag's historical hit rate). Reason codes say why the account is there;
the evidence column gives the numbers to say to the customer.
""")
