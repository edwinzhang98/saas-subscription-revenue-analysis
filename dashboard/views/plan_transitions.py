"""Plan Transitions: where customers go after they sign up, who upgrades from Basic and who downgrades,
and why Basic upgrades fell in 2026 (packaging change vs Paid Social mix)."""
import pandas as pd
import streamlit as st

from common import data_table, finding, how_to_read, key_numbers, load, md_escape, multiselect_filters, page_header, plot
from saas_rev.analysis import transitions as T
from saas_rev.theme import delta_pts, money, pct, pts

NUM, PCT, USD = st.column_config.NumberColumn, "percent", "dollar"
PTS = "%+.2f pts"                      # rate differences, stored ×100 below
MONTH = st.column_config.DateColumn("Month", format="MMM YYYY")
STAGE_LABELS = {"start": "First paid month", "12m": "12 months later", "24m": "24 months later"}


def as_pts(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    """Rate differences (fractions) -> percentage points, for tables that show them with PTS."""
    return df.assign(**{c: df[c] * 100 for c in cols})


page_header("Plan transitions",
            "Where do customers go after they sign up, who upgrades from Basic and who downgrades — and did Basic upgrades "
            "fall in 2026 because of the April packaging change or because the base shifted toward Paid Social signups?")

k = key_numbers("transitions")
src12 = k["downgrades_last_12m_by_source"]
c = st.columns(5)
c[0].metric("Basic upgrade rate", f"{pct(k['upgrade_rate_post'], 2)} / mo", delta_pts(k["upgrade_rate_change_pts"], 2, suffix=" vs pre"),
            border=True,
            help=f"Voluntary Basic → Premium / Enterprise upgrades per Basic account-month, {k['post_window']}, against "
                 f"{pct(k['upgrade_rate_pre'], 2)} in {k['pre_window']} (definitions §6). Forced seat-cap upgrades excluded.")
c[1].metric("Excl. Paid Social", f"{pct(k['upgrade_rate_non_ps_post'], 2)} / mo",
            delta_pts(k["upgrade_rate_non_ps_change_pts"], 2, suffix=" vs pre"), border=True,
            help=f"The same rate with Paid Social signups left out of the Basic base: {pct(k['upgrade_rate_non_ps_pre'], 2)} → "
                 f"{pct(k['upgrade_rate_non_ps_post'], 2)}. 95% interval of the change: {pts(k['upgrade_rate_non_ps_change_lo'], 2)} to "
                 f"{pts(k['upgrade_rate_non_ps_change_hi'], 2)} (Newcombe).")
c[2].metric("Paid Social % of Basic", pct(k["paid_social_share_basic_base_post"], 1),
            delta_pts(k["paid_social_share_basic_base_post"] - k["paid_social_share_basic_base_pre"], 1, suffix=" vs pre"),
            delta_color="off", border=True,
            help=f"Share of Basic account-months from Paid Social signups, {k['post_window']} vs "
                 f"{pct(k['paid_social_share_basic_base_pre'], 1)} in {k['pre_window']}.")
c[3].metric("Basic logo churn", f"{pct(k['basic_churn_non_ps_may_sep_2026'], 2)} / mo",
            delta_pts(k["basic_churn_non_ps_change_pts"], 2, suffix=" vs 2025"), delta_color="inverse", border=True,
            help="Customers on Basic at the previous month-end who churned in the month, Paid Social excluded, May–Sep 2026 vs "
                 f"{pct(k['basic_churn_non_ps_may_sep_2025'], 2)} in May–Sep 2025. 95% interval of the change: "
                 f"{pts(k['basic_churn_non_ps_change_lo'], 2)} to {pts(k['basic_churn_non_ps_change_hi'], 2)}.")
c[4].metric("Downgrades (12 mo)", f"{k['downgrades_last_12m']}", f"{src12.get('customer_success', 0)} CS saves",
            delta_color="off", delta_arrow="off", border=True,
            help=md_escape(f"Plan downgrades effective Oct 2025 – Sep 2026: {src12.get('customer_success', 0)} CS saves, "
                           f"{src12.get('self_serve', 0)} self-serve, {src12.get('renewal', 0)} at renewal; "
                           f"{money(k['downgrades_last_12m_mrr_delta'])} of MRR in total."))

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
    data_table(links.assign(stage_from=links["stage_from"].map(STAGE_LABELS), stage_to=links["stage_to"].map(STAGE_LABELS),
                            state_from=links["state_from"].map(T.PLAN_LABELS), state_to=links["state_to"].map(T.PLAN_LABELS))
               [["stage_from", "state_from", "stage_to", "state_to", "customers", "share_of_from"]], "Show the Sankey flows",
               column_config={"stage_from": "From", "state_from": "Plan", "stage_to": "To", "state_to": "State",
                              "customers": NUM("Customers", format="localized"),
                              "share_of_from": NUM("Share of the from-plan", format=PCT)})
plot(T.fig_transition_heatmap(horizon, flt))
tm = T.transition_matrix(flt, horizons=(horizon,))
ssv = T.signup_states(flt, cohort_window).dropna(subset=[f"state_{horizon}"])
sb = ssv[ssv["plan_0"] == "basic"][f"state_{horizon}"].value_counts(normalize=True)
mb = tm[tm["from_plan"] == "basic"].set_index("to_state")["share_of_from_plan"]
if len(sb) and len(mb):
    st.caption(f"**Why the two churn figures differ.** The Sankey follows customers from their first paid month: "
               f"{pct(sb.get('churned', 0), 0)} of Basic signups had churned {horizon} months later. The matrix counts every paying "
               f"Basic month-end as a start, so accounts already past their first year — which churn less — are in it too, and "
               f"{pct(mb.get('churned', 0), 0)} of Basic account-months are churned {horizon} months on. The signup-window slider "
               "applies to the Sankey only.")
data_table(tm.assign(from_plan=tm["from_plan"].map(T.PLAN_LABELS), to_state=tm["to_state"].map(T.PLAN_LABELS))
           [["horizon_months", "from_plan", "to_state", "customer_months", "share_of_from_plan", "from_mrr", "to_mrr"]],
           "Show the transition matrix",
           column_config={"horizon_months": "Horizon (months)", "from_plan": "From plan", "to_state": f"State {horizon} months later",
                          "customer_months": NUM("Account-months", format="localized"),
                          "share_of_from_plan": NUM("Share of the from-plan", format=PCT),
                          "from_mrr": NUM("MRR at the start", format=USD), "to_mrr": NUM(f"MRR {horizon} months later", format=USD)})

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
orderings = (f"{pts(min(summary['mix_at_pre_rates'], summary['mix_at_post_rates']), 2)} to "
             f"{pts(max(summary['mix_at_pre_rates'], summary['mix_at_post_rates']), 2)}")
c = st.columns(4)
c[0].metric("Upgrade rate, pre", f"{pct(summary['rate_pre'], 2)} / mo", f"{summary['upgrades_pre']:,} upgrades",
            delta_color="off", delta_arrow="off", border=True,
            help=f"Pooled over {summary['pre_window']}: {summary['upgrades_pre']:,} voluntary upgrades / {summary['n_pre']:,} "
                 "Basic account-months.")
c[1].metric("Upgrade rate, post", f"{pct(summary['rate_post'], 2)} / mo", delta_pts(summary["change_pts"], 2, suffix=" vs pre"),
            border=True,
            help=f"Pooled over {summary['post_window']}: {summary['upgrades_post']:,} voluntary upgrades / {summary['n_post']:,} "
                 "Basic account-months.")
c[2].metric("Mix effect", pts(summary["mix_shapley"], 2), f"{pct(summary['mix_share_of_change'], 0)} of the change",
            delta_color="off", delta_arrow="off", border=True,
            help=f"What the change in the Basic base's mix by {'channel' if by == 'acquisition_channel' else 'channel group × Technology'} "
                 f"alone would have done to the pooled rate (Shapley split). The two orderings give {orderings}.")
c[3].metric("Rate effect", pts(summary["rate_shapley"], 2),
            f"{pct(summary['rate_shapley'] / summary['change_pts'], 0)} of the change" if summary["change_pts"] else "",
            delta_color="off", delta_arrow="off", border=True,
            help="What the within-cell upgrade rates alone did to the pooled rate (Shapley split)."
                 + (f" Paid Social excluded, the rate went {pct(np_['rate_pre'], 2)} → {pct(np_['rate_post'], 2)}." if np_ else ""))
plot(T.fig_mix_rate(summary))
data_table(as_pts(table, ["mix_shapley", "rate_shapley", "mix_at_pre_rates", "mix_at_post_rates", "rate_at_post_mix", "rate_at_pre_mix"])
           [["cell", "n_pre", "up_pre", "w_pre", "r_pre", "n_post", "up_post", "w_post", "r_post", "mix_shapley", "rate_shapley",
             "mix_at_pre_rates", "mix_at_post_rates", "rate_at_post_mix", "rate_at_pre_mix"]], "Show the decomposition by cell",
           column_config={"cell": "Cell", "n_pre": NUM("Account-months, pre", format="localized"), "up_pre": NUM("Upgrades, pre", format="%d"),
                          "w_pre": NUM("Share of base, pre", format=PCT), "r_pre": NUM("Upgrade rate, pre", format=PCT),
                          "n_post": NUM("Account-months, post", format="localized"), "up_post": NUM("Upgrades, post", format="%d"),
                          "w_post": NUM("Share of base, post", format=PCT), "r_post": NUM("Upgrade rate, post", format=PCT),
                          "mix_shapley": NUM("Mix effect (Shapley)", format=PTS), "rate_shapley": NUM("Rate effect (Shapley)", format=PTS),
                          "mix_at_pre_rates": NUM("Mix at pre rates", format=PTS), "mix_at_post_rates": NUM("Mix at post rates", format=PTS),
                          "rate_at_post_mix": NUM("Rate at post mix", format=PTS), "rate_at_pre_mix": NUM("Rate at pre mix", format=PTS)})
plot(T.fig_upgrade_rate_monthly(pre=pre, post=post))
sd = T.step_vs_drift(pre, post)
data_table(T.upgrade_rate_monthly("channel_group").query("month_end >= '2025-01-31'")
           [["month_end", "group", "n", "upgrades", "forced", "rate", "lo", "hi"]], "Show the monthly rates",
           column_config={"month_end": MONTH, "group": "Channel group", "n": NUM("Basic accounts at the start", format="localized"),
                          "upgrades": "Voluntary upgrades", "forced": "Forced (seat cap)", "rate": NUM("Upgrade rate", format=PCT),
                          "lo": NUM("95% low", format=PCT), "hi": NUM("95% high", format=PCT)})
pp_ch = T.upgrade_rate_pre_post(("acquisition_channel",), pre, post)
n_down = int((pp_ch["change_pts"] < 0).sum())
raw, step = sd["raw_change_pts"], sd["step_net_of_trend_pts"]
beyond = (f"{abs(step) * 100:.2f} of the {abs(raw) * 100:.2f}-point fall is beyond that trend" if raw < step < 0
          else f"the step against that trend is {pts(step, 2)}")
finding(
    f"<b>Timing.</b> Within non-Paid-Social channels the rate was already drifting {'down' if sd['slope_pts_per_month'] < 0 else 'up'} "
    f"inside the pre window "
    f"({pts(sd['slope_pts_per_month'], 3)} per month). Extending that trend gives {pct(sd['extrapolated_post'], 2)} for the post "
    f"window against an actual {pct(sd['actual_post'], 2)}: {beyond} ({pts(raw, 2)} against the pre-window average). The three "
    f"months before April vs the three after: {pct(sd['rate_jan_mar_2026'], 2)} → {pct(sd['rate_may_jul_2026'], 2)} "
    f"(95%: {pts(sd['step_3m_lo'], 2)} to {pts(sd['step_3m_hi'], 2)}). Channel mix is a small part of it "
    f"({pts(summary['mix_shapley'], 2)}); the rate is lower in {n_down} of {len(pp_ch)} channels. This is a before / after "
    "comparison: the fall lines up in time with the packaging change, but only a holdout — re-gating advanced automations for a "
    "random share of new Basic signups — can measure the change's own effect.")

st.markdown("**Who upgrades — pre vs post, by group**")
dim = st.selectbox("Break down by", ["acquisition_channel", "segment", "industry", "tenure_band", "billing_period"],
                   format_func=T.DIMENSION_LABELS.get, key="pt_dim")
plot(T.fig_upgrade_rate_pre_post(dim, pre, post))
pp = pp_ch if dim == "acquisition_channel" else T.upgrade_rate_pre_post((dim,), pre, post)
data_table(as_pts(pp, ["change_pts", "change_lo", "change_hi"])
           [["group", "n_pre", "upgrades_pre", "forced_pre", "rate_pre", "lo_pre", "hi_pre", "n_post", "upgrades_post", "forced_post",
             "rate_post", "lo_post", "hi_post", "change_pts", "change_lo", "change_hi", "rel_change"]], "Show the table",
           column_config={"group": T.DIMENSION_LABELS[dim], "n_pre": NUM("Account-months, pre", format="localized"),
                          "upgrades_pre": "Upgrades, pre", "forced_pre": "Forced, pre", "rate_pre": NUM("Rate, pre", format=PCT),
                          "lo_pre": NUM("95% low, pre", format=PCT), "hi_pre": NUM("95% high, pre", format=PCT),
                          "n_post": NUM("Account-months, post", format="localized"), "upgrades_post": "Upgrades, post",
                          "forced_post": "Forced, post", "rate_post": NUM("Rate, post", format=PCT),
                          "lo_post": NUM("95% low, post", format=PCT), "hi_post": NUM("95% high, post", format=PCT),
                          "change_pts": NUM("Change", format=PTS), "change_lo": NUM("Change, 95% low", format=PTS),
                          "change_hi": NUM("Change, 95% high", format=PTS), "rel_change": NUM("Relative change", format=PCT)})

# ------------------------------------------------------------------------------- 3. packaging upside
st.subheader("3. Basic retention since the change, and the size of the trade-off")
plot(T.fig_basic_churn_monthly())
net = T.net_mrr_effect(pre, post)
ch = T.basic_churn_compare()
finding(
    f"<b>Sizing the trade-off, if the before / after differences are put down to the packaging change</b> (non-Paid-Social Basic "
    f"base, about {net['basic_base_non_ps_per_month']:,.0f} accounts at the start of a month). Fewer upgrades: "
    f"{pts(net['upgrade_rate_change_pts'], 2)} a month × {net['basic_base_non_ps_per_month']:,.0f} accounts × "
    f"{money(net['mrr_per_upgrade'])} per upgrade = <b>{money(net['foregone_upgrade_mrr_per_month'])} of new MRR a month</b> "
    f"(95%: {money(net['foregone_upgrade_mrr_lo'])} to {money(net['foregone_upgrade_mrr_hi'])}; "
    f"{money(net['foregone_upgrade_mrr_trend_adjusted'])} if the pre-window drift is assumed to have continued). "
    f"Lower churn: Basic logo churn {pct(ch['churn_a'], 2)} → {pct(ch['churn_b'], 2)} ({pts(ch['change_pts'], 2)}, "
    f"95%: {pts(ch['change_lo'], 2)} to {pts(ch['change_hi'], 2)}) × {money(net['mrr_per_basic_account'])} per account = "
    f"<b>{money(net['retained_basic_mrr_per_month'])} of MRR a month</b> kept (95%: {money(net['retained_basic_mrr_lo'])} to "
    f"{money(net['retained_basic_mrr_hi'])}). Net: <b>{money(net['net_mrr_per_month'])} a month</b> "
    f"(range {money(net['net_mrr_lo'])} to {money(net['net_mrr_hi'])}; {money(net['net_mrr_per_month_trend_adjusted'])} trend-adjusted), "
    f"about {money(net['net_mrr_12m_run_rate'])} of run-rate after 12 months if nothing changes. The churn change points the hoped-for "
    "way but its interval includes zero; trial → paid conversion is outside the billing data, so this understates the upside if "
    "conversion rose.")
data_table(T.basic_churn_monthly(True).query("month_end >= '2025-01-31'"), "Show Basic monthly churn (non-Paid-Social)",
           column_config={"month_end": MONTH, "n": NUM("Basic accounts at the start", format="localized"), "churned": "Churned",
                          "rate": NUM("Churn rate", format=PCT), "lo": NUM("95% low", format=PCT), "hi": NUM("95% high", format=PCT)})

# ------------------------------------------------------------------------------- 4. downgrades
st.subheader("4. Who downgrades, and what happens after a CS save")
ds = T.downgrade_summary()
st.caption("Plan downgrades effective Oct 2025 – Sep 2026, by flow and by who initiated them; the last two columns cover the "
           "downgrades with six months of follow-up.")
# a share with no downgrade followed for six months yet is undefined: shown as "—" (text), not as Streamlit's "None"
ds = ds.assign(paying_6m_share=ds["paying_6m_share"].map(lambda v: "—" if pd.isna(v) else f"{v * 100:.2f}".rstrip("0").rstrip(".") + "%"))
st.dataframe(ds[["flow", "source_label", "downgrades", "customers", "mrr_before", "mrr_delta", "mrr_retained_share", "seats_cut_share",
                 "observed_6m", "paying_6m_share"]], hide_index=True, width="stretch",
             column_config={"flow": "Downgrade", "source_label": "Initiated by", "downgrades": "Downgrades",
                            "customers": "Accounts", "mrr_before": NUM("MRR before", format=USD),
                            "mrr_delta": NUM("MRR change", format=USD),
                            "mrr_retained_share": NUM("MRR kept", format=PCT),
                            "seats_cut_share": NUM("Seats cut too", format=PCT),
                            "observed_6m": "Followed 6 mo",
                            "paying_6m_share": st.column_config.TextColumn("Paying at 6 mo", alignment="right")})
plot(T.fig_downgrades_quarterly())
plot(T.fig_downgrade_survival(6))
ddim = st.selectbox("Who downgrades from Premium — break down by", ["segment", "industry", "acquisition_channel", "tenure_band"],
                    format_func=T.DIMENSION_LABELS.get, key="pt_ddim")
plot(T.fig_downgrade_rate(ddim))
data_table(T.downgrade_rate_by(ddim)[["group", "n", "downgrades", "rate", "lo", "hi"]], "Show the downgrade rate table",
           column_config={"group": T.DIMENSION_LABELS[ddim], "n": NUM("Premium account-months", format="localized"),
                          "downgrades": "Downgrades", "rate": NUM("Downgrade rate", format=PCT),
                          "lo": NUM("95% low", format=PCT), "hi": NUM("95% high", format=PCT)})
sv = T.downgrade_survival(6)
data_table(as_pts(sv, ["gap_vs_comparable_pts"])
           [["source_label", "downgrades", "paying", "paying_share", "lo", "hi", "comparable_paying", "gap_vs_comparable_pts",
             "mrr_retained_share"]], "Show survival after a downgrade (6 months)",
           column_config={"source_label": "Initiated by", "downgrades": "Followed 6 mo",
                          "paying": NUM("Still paying", format="%d"),
                          "paying_share": NUM("Share still paying", format=PCT), "lo": NUM("95% low", format=PCT),
                          "hi": NUM("95% high", format=PCT), "comparable_paying": NUM("Comparable, paying", format=PCT),
                          "gap_vs_comparable_pts": NUM("Gap vs comparable", format="%+.1f pts"),
                          "mrr_retained_share": NUM("MRR kept", format=PCT)})
dg = T.downgrade_events()
dg = dg[dg["month_end"] >= "2025-10-31"].assign(
    from_plan_id=lambda d: d["from_plan_id"].map(T.PLAN_LABELS), to_plan_id=lambda d: d["to_plan_id"].map(T.PLAN_LABELS),
    state_6m=lambda d: d["state_6m"].map(T.PLAN_LABELS).fillna("—"))
data_table(dg[["effective_date", "customer_id", "company_name", "segment", "industry", "source_label", "from_plan_id", "to_plan_id",
               "from_seats", "to_seats", "from_mrr", "to_mrr", "state_6m"]].sort_values("effective_date", ascending=False),
           "Show the downgrade events (last 12 months)",
           column_config={"effective_date": st.column_config.DateColumn("Effective", format="D MMM YYYY"), "customer_id": "Customer",
                          "company_name": "Company", "segment": "Segment", "industry": "Industry", "source_label": "Initiated by",
                          "from_plan_id": "From plan", "to_plan_id": "To plan", "from_seats": "Seats before", "to_seats": "Seats after",
                          "from_mrr": NUM("MRR before", format=USD), "to_mrr": NUM("MRR after", format=USD),
                          "state_6m": st.column_config.TextColumn("6 months later", help="Plan or Churned six months after the "
                                                                  "downgrade; — = not observed yet")})

# ------------------------------------------------------------------------------- 5. time to upgrade
st.subheader("5. How long upgrades take")
plot(T.fig_time_to_upgrade("acquisition_channel"))
ttu = load("transitions__time_to_upgrade")
ttu = ttu[(ttu["by"] == "acquisition_channel") & ttu["k"].isin([6, 12, 24])]
ttu_t = ttu.pivot(index="group", columns="k", values=["cif_upgrade", "cif_churn"]).reset_index()
ttu_t.columns = ["channel"] + [f"{m}_{int(kk)}m" for m, kk in ttu_t.columns[1:]]
data_table(ttu_t, "Show cumulative upgrade and churn at 6 / 12 / 24 months",
           column_config={"channel": "Acquisition channel",
                          **{f"cif_upgrade_{m}m": NUM(f"Upgraded by month {m}", format=PCT) for m in (6, 12, 24)},
                          **{f"cif_churn_{m}m": NUM(f"Churned by month {m}", format=PCT) for m in (6, 12, 24)}})

trig = k["product_trigger_threshold_rel"]
n_past = sum(v <= trig for v in (k["non_ps_step_net_of_trend_rel"], k["upgrade_rate_non_ps_rel_change"]))
trigger_note = {2: f"both past the Product trigger of a {pct(-trig, 0)} fall", 1: f"only one end past the Product trigger of a {pct(-trig, 0)} fall",
                0: f"neither past the Product trigger of a {pct(-trig, 0)} fall"}[n_past]
how_to_read(f"""
**Sankey and transition matrix.** The Sankey places each customer by the plan on their first paid month-end, then by their
state 12 (and 24) months later; *Churned* means no paying subscription at that month-end, and link width is customers. The
matrix counts every paying month-end as a start (account-months), so it also includes accounts that are already past their
first year — those churn less, which is why the matrix's churn share is lower than the Sankey's. The horizon and the filters
recompute both from the monthly MRR table; the signup-window slider applies to the Sankey only, and a 24-month view only
includes customers who signed up at least 24 months before the as-of date, which is why it has fewer customers.

**Upgrade rate.** The monthly rate is voluntary Basic → Premium / Enterprise upgrades divided by customers on Basic at the end of the
previous month (definitions §6). Forced seat-cap upgrades are excluded and counted separately in the tables. Shaded bands and
whiskers are 95% intervals: Paid Social is a small base, so its band is wide — do not read its month-to-month moves.

**Mix vs rate.** The pooled rate is a weighted average of channel rates. It can fall because the base shifted toward low-upgrading
channels (*mix*, the Paid Social program) or because every channel upgrades less (*rate*). The split depends on whether you
hold rates or weights fixed first, so both orderings are shown and the headline is their midpoint (Shapley). Move the windows to
test how sensitive the split is. The dotted trend on the monthly chart shows that part of the decline predates April 2026: the part
of the fall that lines up with the change is a range, from the step net of trend ({pts(k['non_ps_step_net_of_trend_pts'], 2)},
{pct(k['non_ps_step_net_of_trend_rel'], 0)} against the trend) to the raw change ({pts(k['upgrade_rate_non_ps_change_pts'], 2)},
{pct(k['upgrade_rate_non_ps_rel_change'], 0)}) — {trigger_note}, and neither end is a measured effect of the change.

**Retention and the trade-off.** The same month-level logic for churn, before and after the change, with an interval; the sizing
multiplies the rate changes by the base and the MRR per account, so it inherits every caveat above. **Downgrades** are grouped by
who initiated them (CS saves are drawn in the hatched gray used for saved accounts across the dashboard); the survival chart compares
downgraded accounts with accounts on the same plan that did not downgrade — a benchmark, not a counterfactual. **Time to upgrade**
is a cumulative-incidence curve: churn competes with upgrading, so a channel that churns fast (Paid Social, in pink) has little chance
to upgrade; the other channels share one colour and are named at the end of each line.
""")
