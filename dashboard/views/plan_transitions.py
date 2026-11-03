"""Plan Transitions: where customers go after they sign up, who upgrades from Basic and who downgrades,
and why Basic upgrades fell in 2026 (packaging change vs Paid Social mix)."""
import pandas as pd
import streamlit as st

from common import data_table, finding, how_to_read, key_numbers, load, multiselect_filters, page_header, plot
from saas_rev.analysis import transitions as T
from saas_rev.theme import delta_pts, money, pct, pts

page_header("Plan transitions",
            "Where do customers go after they sign up, who upgrades from Basic and who downgrades — and did Basic upgrades "
            "fall in 2026 because of the April packaging change or because the base shifted toward Paid Social signups?")

k = key_numbers("transitions")
c = st.columns(5)
c[0].metric("Basic → higher upgrade rate", f"{pct(k['upgrade_rate_post'], 2)} / mo",
            delta_pts(k["upgrade_rate_change_pts"], 2, suffix=" vs Apr 2025–Mar 2026"), border=True,
            help="Voluntary upgrades per Basic account-month, May–Sep 2026 (02 §6). Forced seat-cap upgrades excluded.")
c[1].metric("Same, Paid Social excluded", f"{pct(k['upgrade_rate_non_ps_post'], 2)} / mo",
            delta_pts(k["upgrade_rate_non_ps_change_pts"], 2,
                      suffix=f" (95%: {pts(k['upgrade_rate_non_ps_change_lo'], 2)} to {pts(k['upgrade_rate_non_ps_change_hi'], 2)})"),
            border=True)
c[2].metric("Paid Social share of Basic base", pct(k["paid_social_share_basic_base_post"], 1),
            delta_pts(k["paid_social_share_basic_base_post"] - k["paid_social_share_basic_base_pre"], 1, suffix=" vs pre window"),
            delta_color="off", border=True)
c[3].metric("Basic logo churn, May–Sep 2026", f"{pct(k['basic_churn_non_ps_may_sep_2026'], 2)} / mo",
            delta_pts(k["basic_churn_non_ps_change_pts"], 2, suffix=" vs May–Sep 2025"), delta_color="inverse", border=True,
            help="Customers on Basic at the previous month-end who churned in the month, Paid Social excluded. "
                 f"95% interval of the change: {pts(k['basic_churn_non_ps_change_lo'], 2)} to {pts(k['basic_churn_non_ps_change_hi'], 2)}.")
c[4].metric("Plan downgrades, last 12 months", f"{k['downgrades_last_12m']}",
            f"{k['downgrades_last_12m_by_source'].get('customer_success', 0)} CS saves · {money(k['downgrades_last_12m_mrr_delta'])} MRR",
            delta_color="off", border=True)

# ------------------------------------------------------------------------------- 1. flows
st.subheader("1. Where customers end up")
mrr = load("fct_customer_mrr_monthly")
cc = st.columns([1, 2, 2])
horizon = cc[0].radio("Sankey horizon", [12, 24], format_func=lambda h: f"{h} months", horizontal=True, key="pt_horizon")
with cc[1]:
    flt = multiselect_filters(mrr, {"segment": "Segment", "acquisition_channel": "Acquisition channel"}, key="pt")
cohorts = sorted(mrr["cohort_month_end"].dt.date.unique().tolist())
cw = cc[2].select_slider("Signup cohort window (first paid month)", options=cohorts, value=(cohorts[0], cohorts[-1]),
                         format_func=lambda d: pd.Timestamp(d).strftime("%b %Y"), key="pt_cohort")
cohort_window = (pd.Timestamp(cw[0]), pd.Timestamp(cw[1]))
plot(T.fig_sankey(horizon, flt, cohort_window))
links = T.sankey_links(horizon, flt, cohort_window)
if not links.empty:
    data_table(links.assign(state_from=links["state_from"].map(T.PLAN_LABELS), state_to=links["state_to"].map(T.PLAN_LABELS))
               [["stage_from", "state_from", "stage_to", "state_to", "customers", "share_of_from"]], "Show the Sankey flows",
               column_config={"share_of_from": st.column_config.NumberColumn("Share of source", format="%.1f%%")})
plot(T.fig_transition_heatmap(horizon, flt))
tm = T.transition_matrix(flt, horizons=(horizon,))
data_table(tm.assign(from_plan=tm["from_plan"].map(T.PLAN_LABELS), to_state=tm["to_state"].map(T.PLAN_LABELS))
           [["horizon_months", "from_plan", "to_state", "customer_months", "share_of_from_plan", "from_mrr", "to_mrr"]],
           "Show the transition matrix", column_config={"share_of_from_plan": st.column_config.NumberColumn(format="%.1f%%")})

# ------------------------------------------------------------------------------- 2. why upgrades fell
st.subheader("2. Why did Basic upgrades fall in 2026 — channel mix or lower rates?")
months = sorted(T._basic_base()["month_end"].dt.date.unique().tolist())
fmt = lambda d: pd.Timestamp(d).strftime("%b %Y")  # noqa: E731
cc = st.columns([2, 2, 1])
pre_sel = cc[0].select_slider("Pre window (outcome months)", options=months,
                              value=(T.PRE_WINDOW[0].date(), T.PRE_WINDOW[1].date()), format_func=fmt, key="pt_pre")
post_sel = cc[1].select_slider("Post window (after the 15 Apr 2026 packaging change)", options=months,
                               value=(T.POST_WINDOW[0].date(), T.POST_WINDOW[1].date()), format_func=fmt, key="pt_post")
by = cc[2].radio("Mix dimension", ["acquisition_channel", "channel_tech"],
                 format_func={"acquisition_channel": "Acquisition channel", "channel_tech": "Channel group × Technology"}.get,
                 key="pt_by")
pre, post = (pd.Timestamp(pre_sel[0]), pd.Timestamp(pre_sel[1])), (pd.Timestamp(post_sel[0]), pd.Timestamp(post_sel[1]))
if pre[1] >= post[0]:
    st.warning("The pre window must end before the post window starts — using the default windows.")
    pre, post = T.PRE_WINDOW, T.POST_WINDOW
summary, table = T.mix_rate_decomposition(pre, post, by)
np_ = summary.get("non_ps")
c = st.columns(4)
c[0].metric("Pooled upgrade rate, pre", f"{pct(summary['rate_pre'], 2)} / mo", f"{summary['upgrades_pre']:,} upgrades / {summary['n_pre']:,} account-months",
            delta_color="off", border=True)
c[1].metric("Pooled upgrade rate, post", f"{pct(summary['rate_post'], 2)} / mo", delta_pts(summary["change_pts"], 2, suffix=" vs pre"), border=True)
c[2].metric("Mix effect (Shapley)", pts(summary["mix_shapley"], 2),
            f"{pct(summary['mix_share_of_change'], 0)} of the change; orderings {pts(min(summary['mix_at_pre_rates'], summary['mix_at_post_rates']), 2)} "
            f"to {pts(max(summary['mix_at_pre_rates'], summary['mix_at_post_rates']), 2)}", delta_color="off", border=True)
c[3].metric("Rate effect (Shapley)", pts(summary["rate_shapley"], 2),
            (f"non-Paid-Social {pct(np_['rate_pre'], 2)} → {pct(np_['rate_post'], 2)}" if np_ else ""), delta_color="off", border=True)
plot(T.fig_mix_rate(summary))
data_table(table[["cell", "n_pre", "up_pre", "w_pre", "r_pre", "n_post", "up_post", "w_post", "r_post", "mix_shapley", "rate_shapley",
                  "mix_at_pre_rates", "mix_at_post_rates", "rate_at_post_mix", "rate_at_pre_mix"]], "Show the decomposition by cell")
plot(T.fig_upgrade_rate_monthly(pre=pre, post=post))
sd = T.step_vs_drift(pre, post)
data_table(T.upgrade_rate_monthly("channel_group").query("month_end >= '2025-01-31'")
           [["month_end", "group", "n", "upgrades", "forced", "rate", "lo", "hi"]], "Show the monthly rates")
finding(
    f"<b>Timing.</b> Within non-Paid-Social channels the rate was already drifting down inside the pre window "
    f"({pts(sd['slope_pts_per_month'], 3)} per month). Extending that trend gives {pct(sd['extrapolated_post'], 2)} for the post window; "
    f"the actual rate is {pct(sd['actual_post'], 2)}, a step of {pts(sd['step_net_of_trend_pts'], 2)} net of trend "
    f"({pts(sd['raw_change_pts'], 2)} against the pre-window mean). The three months before April vs the three after: "
    f"{pct(sd['rate_jan_mar_2026'], 2)} → {pct(sd['rate_may_jul_2026'], 2)} "
    f"(95%: {pts(sd['step_3m_lo'], 2)} to {pts(sd['step_3m_hi'], 2)}). Channel mix is a small part of the story "
    f"({pts(summary['mix_shapley'], 2)}); the fall is within every channel. This is observational: the packaging change is the "
    f"leading explanation, but the clean test is a holdout — re-gate advanced automations for a random share of new Basic signups.")

st.markdown("**Who upgrades — pre vs post, by group**")
dim = st.selectbox("Break down by", ["acquisition_channel", "segment", "industry", "tenure_band", "billing_period"],
                   format_func=T.DIMENSION_LABELS.get, key="pt_dim")
plot(T.fig_upgrade_rate_pre_post(dim, pre, post))
pp = T.upgrade_rate_pre_post((dim,), pre, post)
data_table(pp[["group", "n_pre", "upgrades_pre", "forced_pre", "rate_pre", "lo_pre", "hi_pre", "n_post", "upgrades_post", "forced_post",
               "rate_post", "lo_post", "hi_post", "change_pts", "change_lo", "change_hi", "rel_change"]], "Show the table")

# ------------------------------------------------------------------------------- 3. packaging upside
st.subheader("3. What the packaging change bought: Basic retention, and the net effect")
plot(T.fig_basic_churn_monthly())
net = T.net_mrr_effect(pre, post)
ch = T.basic_churn_compare()
finding(
    f"<b>Trade-off (non-Paid-Social Basic base, about {net['basic_base_non_ps_per_month']:,.0f} accounts at the start of a month).</b> "
    f"Fewer upgrades: {pts(net['upgrade_rate_change_pts'], 2)} per month × {money(net['mrr_per_upgrade'])} per upgrade = "
    f"<b>{money(net['foregone_upgrade_mrr_per_month'])} MRR per month</b> of new run-rate foregone "
    f"(95%: {money(net['foregone_upgrade_mrr_lo'])} to {money(net['foregone_upgrade_mrr_hi'])}; "
    f"{money(net['foregone_upgrade_mrr_trend_adjusted'])} if the pre-window drift is assumed to have continued). "
    f"Better retention: Basic logo churn {pct(ch['churn_a'], 2)} → {pct(ch['churn_b'], 2)} ({pts(ch['change_pts'], 2)}, "
    f"95%: {pts(ch['change_lo'], 2)} to {pts(ch['change_hi'], 2)}) × {money(net['mrr_per_basic_account'])} per account = "
    f"<b>{money(net['retained_basic_mrr_per_month'])} MRR per month</b> kept (95%: {money(net['retained_basic_mrr_lo'])} to "
    f"{money(net['retained_basic_mrr_hi'])}). Net: <b>{money(net['net_mrr_per_month'])} per month</b> "
    f"(range {money(net['net_mrr_lo'])} to {money(net['net_mrr_hi'])}; {money(net['net_mrr_per_month_trend_adjusted'])} trend-adjusted), "
    f"about {money(net['net_mrr_12m_run_rate'])} of run-rate after 12 months if nothing changes. The retention gain is real in direction "
    f"but not distinguishable from zero; trial → paid conversion is outside the billing data, so this understates the upside if conversion rose.")
data_table(T.basic_churn_monthly(True).query("month_end >= '2025-01-31'"), "Show Basic monthly churn (non-Paid-Social)")

# ------------------------------------------------------------------------------- 4. downgrades
st.subheader("4. Who downgrades, and what happens after a CS save")
ds = T.downgrade_summary()
st.dataframe(ds[["flow", "source_label", "downgrades", "customers", "mrr_before", "mrr_delta", "mrr_retained_share", "seats_cut_share",
                 "observed_6m", "paying_6m_share"]], hide_index=True, width="stretch",
             column_config={"flow": "Downgrade", "source_label": "Initiated by", "downgrades": "Downgrades (last 12 months)",
                            "customers": "Accounts", "mrr_before": st.column_config.NumberColumn("MRR before", format="$%.0f"),
                            "mrr_delta": st.column_config.NumberColumn("MRR change", format="$%.0f"),
                            "mrr_retained_share": st.column_config.NumberColumn("MRR kept", format="%.0f%%"),
                            "seats_cut_share": st.column_config.NumberColumn("Seats cut too", format="%.0f%%"),
                            "observed_6m": "Observed 6 months", "paying_6m_share": st.column_config.NumberColumn("Still paying at 6 months", format="%.0f%%")})
c1, c2 = st.columns(2)
with c1:
    plot(T.fig_downgrades_quarterly())
with c2:
    plot(T.fig_downgrade_survival(6))
ddim = st.selectbox("Who downgrades from Premium — break down by", ["segment", "industry", "acquisition_channel", "tenure_band"],
                    format_func=T.DIMENSION_LABELS.get, key="pt_ddim")
plot(T.fig_downgrade_rate(ddim))
data_table(T.downgrade_rate_by(ddim)[["group", "n", "downgrades", "rate", "lo", "hi"]], "Show the downgrade rate table")
sv = T.downgrade_survival(6)
data_table(sv[["source_label", "downgrades", "paying", "paying_share", "lo", "hi", "comparable_paying", "gap_vs_comparable_pts", "mrr_retained_share"]],
           "Show survival after a downgrade (6 months)")
dg = T.downgrade_events()
data_table(dg[dg["month_end"] >= "2025-10-31"][["effective_date", "customer_id", "company_name", "segment", "industry", "source_label",
                                                 "from_plan_id", "to_plan_id", "from_seats", "to_seats", "from_mrr", "to_mrr", "state_6m"]]
           .sort_values("effective_date", ascending=False), "Show the downgrade events (last 12 months)")

# ------------------------------------------------------------------------------- 5. time to upgrade
st.subheader("5. How long upgrades take")
plot(T.fig_time_to_upgrade("acquisition_channel"))
ttu = load("transitions__time_to_upgrade")
ttu = ttu[(ttu["by"] == "acquisition_channel") & ttu["k"].isin([6, 12, 24])]
data_table(ttu.pivot(index="group", columns="k", values=["cif_upgrade", "cif_churn"]).reset_index().pipe(
    lambda d: d.set_axis(["channel"] + [f"{m}_{int(kk)}m" for m, kk in d.columns[1:]], axis=1)),
    "Show cumulative upgrade and churn at 6 / 12 / 24 months")

how_to_read("""
**Sankey and transition matrix.** Each customer is placed by the plan on their first paid month-end, then by their state 12 (and 24)
months later; *Churned* means no paying subscription at that month-end. Link width is customers. The matrix pools every
month-end start, so it has far more account-months than the Sankey has customers — the two agree in shape, not in counts. The
horizon and the filters recompute both from the monthly MRR table; a 24-month view only includes customers who signed up at least
24 months before the as-of date, which is why it has fewer customers.

**Upgrade rate.** The monthly rate is voluntary Basic → Premium / Enterprise upgrades divided by customers on Basic at the end of the
previous month (definitions §6). Forced seat-cap upgrades are excluded and counted separately in the tables. Shaded bands and
whiskers are 95% intervals: Paid Social is a small base, so its band is wide — do not read its month-to-month moves.

**Mix vs rate.** The pooled rate is a weighted average of channel rates. It can fall because the base shifted toward low-upgrading
channels (*mix*, the Paid Social program) or because every channel upgrades less (*rate*). The split depends on whether you
hold rates or weights fixed first, so both orderings are shown and the headline is their midpoint (Shapley). Move the windows to
see how sensitive the split is; the result does not change much. The dotted trend on the monthly chart shows that part of the
decline predates April 2026 — the step net of trend is the fairer estimate of the packaging effect, and it is still well past the
Product trigger of a 20% fall.

**Retention and net effect.** The same month-level logic for churn, before and after the change, with an interval; the net effect
multiplies rate changes by base and MRR per account, so it inherits every caveat above. **Downgrades** are grouped by who initiated
them; the survival chart compares saved accounts with accounts that never asked to leave — a benchmark, not a counterfactual.
**Time to upgrade** is a cumulative-incidence curve: churn competes with upgrading, so a channel that churns fast (Paid Social)
has little chance to upgrade.
""")
