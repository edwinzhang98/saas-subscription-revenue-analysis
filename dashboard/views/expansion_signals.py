"""Expansion Signals: which Basic accounts look like upgraders, which signals survived the packaging change,
and which accounts CS should work."""
import numpy as np
import pandas as pd
import streamlit as st

from common import data_table, finding, how_to_read, key_numbers, load, page_header, plot
from saas_rev.analysis import expansion as E
from saas_rev.theme import delta_pct, money, num, pct

page_header("Expansion signals",
            "Which Basic accounts behave like upgraders in the 90 days before a Basic → Premium upgrade, which of those "
            "signals survived the April 2026 packaging change, and which accounts should CS work this quarter?")

k = key_numbers("expansion")
pop = load("expansion__population")
sig = load("expansion__signal_table")
quint = load("expansion__quintiles")
ts = load("expansion__test_scores")
opp_all = load("expansion__opportunities")
weekly = load("expansion__drift_weekly")
drift_monthly = load("expansion__drift_monthly")
drift_dist = load("expansion__drift_distribution")
curve = load("expansion__capture_curve")
cal = load("expansion__calibration")
ors = load("expansion__odds_ratios")
reasons = load("expansion__reason_codes")
PERIODS = {"all": "All months (Jan 2024 – Jun 2026)", "pre": "Before the change (to Mar 2026)",
           "post": "After the change (Apr – Jun 2026)"}
PERIOD_SHORT = {"all": "Jan 2024 – Jun 2026", "pre": "to Mar 2026", "post": "Apr – Jun 2026"}
NUM3 = dict(format="%.3f")

# KPI tiles: labels <= ~22 characters and deltas <= ~28 so nothing truncates at 1440 px; detail lives in help=.
top = k["top_signals"][0]
c = st.columns(5)
c[0].metric("Upgraders vs stayers", f"{k['n_upgraders']} vs {k['n_stayers']}", f"{k['n_strata']} matched strata",
            delta_color="off", delta_arrow="off", border=True,
            help="One row per voluntary Basic → Premium upgrade (the last month-end before it) against one eligible "
                 "month-end of a non-upgrading, non-churning account from the same calendar month and tenure band.")
c[1].metric("Strongest signal", E.SIGNALS[top["signal"]]["tile"], f"SMD {top['smd']:.2f} · lift {top['top_quintile_lift']:.1f}×",
            delta_color="off", delta_arrow="off", border=True,
            help=f"{top['name']}. SMD = standardized mean difference between upgraders and stayers "
                 f"({top['smd']:.2f}, bootstrap 95% {top['smd_lo']:.2f}–{top['smd_hi']:.2f}); lift = 90-day upgrade rate of the "
                 f"top quintile ÷ base rate on all eligible account-months ({top['top_quintile_lift']:.1f}×, account-level "
                 f"bootstrap 95% {top['top_quintile_lift_lo']:.1f}–{top['top_quintile_lift_hi']:.1f}×).")
c[2].metric("Model AUC (test)", f"{k['auc_test']:.2f}", f"95%: {k['auc_test_lo']:.2f}–{k['auc_test_hi']:.2f}",
            delta_color="off", delta_arrow="off", border=True,
            help=f"Logistic regression {E.SPLIT_NOTE}. {E.auc_change_sentence(k)}. Intervals: account-level bootstrap.")
c[3].metric("Precision, top 100", pct(k["precision_at_100_all"]),
            f"vs base {pct(k['base_rate_test'])}", delta_color="off", delta_arrow="off", border=True,
            help=f"Share of the 100 highest-scored Basic accounts each month that upgraded within 90 days, averaged over "
                 f"the six test months (Jan – Jun 2026): {pct(k['precision_at_100_all'])}, account-level bootstrap 95% "
                 f"{pct(k['precision_at_100_all_lo'])}–{pct(k['precision_at_100_all_hi'])} (lift {k['lift_at_100_all']:.1f}×, "
                 f"{k['lift_at_100_all_lo']:.1f}–{k['lift_at_100_all_hi']:.1f}×); recall {pct(k['recall_at_100_all'], 0)} = the share "
                 f"of all upgrades those lists contain; base = the test-period 90-day upgrade rate.")
c[4].metric("Paywall hits / user", f"{k['gate_hits_mean_post']:.2f}", delta_pct(k["gate_hits_drift"], 0, suffix=" after April"),
            border=True,
            help=f"Premium-feature paywall hits per active user per week, mean over Basic account-months (13-week rate at "
                 f"each month-end): {k['gate_hits_mean_pre']:.2f} in Oct 2025 – Mar 2026 vs {k['gate_hits_mean_post']:.2f} in "
                 f"Jul – Sep 2026, the months whose windows lie entirely after the change. The packaging change moved "
                 f"advanced automations into Basic, so the paywall fires less often.")

finding(f"<b>Upgraders are the accounts bumping into the Premium paywall and automating heavily.</b> In the 13 weeks "
        f"before an upgrade, {pct(top['median_upgraders'], 0)} of a typical upgrader's active users hit the paywall vs "
        f"{pct(top['median_stayers'], 0)} for a matched stayer (SMD {top['smd']:.2f}, 95% interval {top['smd_lo']:.2f}–"
        f"{top['smd_hi']:.2f}). Seat growth, utilization and invitations separate the groups far less. After April the "
        f"paywall fires {pct(-k['gate_hits_drift'], 0)} less often, so thresholds must be relative (a top-percentile cut each month, e.g. the top 20% used below), "
        f"not absolute.")

# ------------------------------------------------------------------ 1. upgraders vs stayers
st.subheader("1. How upgraders differ from matched stayers")
plot(E.fig_smd_dotplot(sig))
data_table(sig[["name", "n_upgraders", "n_stayers", "median_upgraders", "median_stayers", "smd", "smd_lo", "smd_hi",
                "top_quintile_rate", "top_quintile_lift", "top_quintile_lift_lo", "top_quintile_lift_hi"]],
           "Show the signal table",
           column_config={"name": "Signal", "n_upgraders": "Upgraders", "n_stayers": "Stayers",
                          "smd": st.column_config.NumberColumn("SMD", format="%.2f"),
                          "smd_lo": st.column_config.NumberColumn("SMD low", format="%.2f"),
                          "smd_hi": st.column_config.NumberColumn("SMD high", format="%.2f"),
                          "median_upgraders": st.column_config.NumberColumn("Median, upgraders", **NUM3),
                          "median_stayers": st.column_config.NumberColumn("Median, stayers", **NUM3),
                          "top_quintile_rate": st.column_config.NumberColumn("Top-bin upgrade rate", format="percent"),
                          "top_quintile_lift": st.column_config.NumberColumn("Top-bin lift", format="%.2fx"),
                          "top_quintile_lift_lo": st.column_config.NumberColumn("Lift low", format="%.2fx"),
                          "top_quintile_lift_hi": st.column_config.NumberColumn("Lift high", format="%.2fx")})

# ------------------------------------------------------------------ 2. one signal in depth: threshold rule
st.subheader("2. One signal in depth: a CS rule and the upgrade rate by quintile")
cc = st.columns([2, 2, 3])
# default = the strongest signal, so the block opens on the same signal and lift as the headline tile
signal = cc[0].selectbox("Signal", list(E.SIGNALS), format_func=lambda s: E.SIGNALS[s]["name"],
                         index=list(E.SIGNALS).index(top["signal"]), key="es_signal")
period = cc[1].radio("Period", list(PERIODS), format_func=PERIODS.get, index=0, key="es_period", horizontal=False)
share = cc[2].slider("Rule: the signal is in the top … of account-months", min_value=5, max_value=50, value=20, step=5,
                     format="%d%%", key="es_top_share",
                     help="The default (top 20%) is the top bin of the chart below. Rows tied at the threshold count as "
                          "above it; when the threshold value is a mass point (zero seat growth, 100% peak utilization) the "
                          "rule starts just above it, so the share captured can differ from the slider.")
sub = pop[~pop["forced"]]
if period != "all":
    sub = sub[sub["period"] == period]
threshold = E.rule_threshold(sub[signal], share / 100)
r = E.threshold_rule(pop, signal, threshold, period)
t = st.columns(5)
t[0].metric("Rule threshold", f"≥ {E.fmt_value(signal, threshold)}", f"{pct(r['share_above'], 0)} of rows",
            delta_color="off", delta_arrow="off", border=True, help=f"{E.SIGNALS[signal]['name']}: {r['n_above']:,} account-months at or above the "
                                                 f"threshold ({pct(r['share_above'])}).")
t[1].metric("Upgrade rate above", pct(r["rate_above"]), f"95% {pct(r['rate_above_lo'])} – {pct(r['rate_above_hi'])}",
            delta_color="off", delta_arrow="off", border=True, help="90-day voluntary upgrade rate of the rows at or above the threshold; "
                                                 "interval = account-level bootstrap.")
t[2].metric("Upgrade rate below", pct(r["rate_below"]), f"base rate {pct(r['base_rate'])}", delta_color="off", delta_arrow="off", border=True)
t[3].metric("Lift vs base rate", f"{r['lift']:.2f}×" if pd.notna(r["lift"]) else "–",
            f"95% {r['lift_lo']:.1f}–{r['lift_hi']:.1f}×" if pd.notna(r["lift"]) else "", delta_color="off", delta_arrow="off", border=True,
            help="Upgrade rate above the threshold ÷ base rate of the same rows; interval = account-level bootstrap.")
t[4].metric("Upgrades captured", pct(r["recall"], 0), f"{r['upgrades_above']} of {r['upgrades_total']} upgrades",
            delta_color="off", delta_arrow="off", border=True)
pop_note = (f"; the page's headline population is {k['population_rows']:,} rows at a {pct(k['base_rate_all'])} base rate"
            if period == "all" else "")
st.markdown(f"<div class='note'>Population of this rule: {r['n']:,} account-months ({PERIOD_SHORT[period]}, {r['n_accounts']:,} accounts) "
            f"with a value for this signal — {r['n_missing']} without one (no active users in the window) cannot be placed on "
            f"either side and are left out{pop_note}. A row tied at the threshold counts as above it.</div>",
            unsafe_allow_html=True)
q = quint[(quint["signal"] == signal) & (quint["period"] == period)]
plot(E.fig_quintile_lift(signal, period, q=q))
data_table(q[["bin", "quintile_span", "n", "upgrades", "rate", "rate_lo", "rate_hi", "lift", "lift_lo", "lift_hi", "share_of_rows"]],
           "Show the bins",
           column_config={"bin": "Signal range", "quintile_span": "Quintiles", "n": "Account-months", "upgrades": "Upgrades",
                          "rate": st.column_config.NumberColumn("Upgrade rate", format="percent"),
                          "rate_lo": st.column_config.NumberColumn("Rate low", format="percent"),
                          "rate_hi": st.column_config.NumberColumn("Rate high", format="percent"),
                          "lift": st.column_config.NumberColumn("Lift", format="%.2fx"),
                          "lift_lo": st.column_config.NumberColumn("Lift low", format="%.2fx"),
                          "lift_hi": st.column_config.NumberColumn("Lift high", format="%.2fx"),
                          "share_of_rows": st.column_config.NumberColumn("Share of rows", format="percent")})

# ------------------------------------------------------------------ 3. drift
st.subheader("3. Did the signals survive the April 2026 packaging change?")
plot(E.fig_gate_drift(weekly, drift_dist, drift_monthly))
data_table(drift_dist[["period", "name", "account_months", "mean", "p10", "p25", "p50", "p75", "p90", "share_zero"]],
           "Show the before / after distributions",
           column_config={"period": "Period", "name": "Signal", "account_months": "Account-months",
                          "mean": st.column_config.NumberColumn("Mean", **NUM3), "p10": st.column_config.NumberColumn("P10", **NUM3),
                          "p25": st.column_config.NumberColumn("P25", **NUM3), "p50": st.column_config.NumberColumn("Median", **NUM3),
                          "p75": st.column_config.NumberColumn("P75", **NUM3), "p90": st.column_config.NumberColumn("P90", **NUM3),
                          "share_zero": st.column_config.NumberColumn("Share at zero", format="percent")})
plot(E.fig_lift_pre_post(sig))
data_table(sig[["name", "top_quintile_lift_pre", "top_quintile_lift_pre_lo", "top_quintile_lift_pre_hi", "top_quintile_lift_post",
                "top_quintile_lift_post_lo", "top_quintile_lift_post_hi", "top_quintile_n_post", "top_quintile_upgrades_post"]],
           "Show the lift table",
           column_config={"name": "Signal",
                          "top_quintile_lift_pre": st.column_config.NumberColumn("Lift before", format="%.2fx"),
                          "top_quintile_lift_pre_lo": st.column_config.NumberColumn("Before, low", format="%.2fx"),
                          "top_quintile_lift_pre_hi": st.column_config.NumberColumn("Before, high", format="%.2fx"),
                          "top_quintile_lift_post": st.column_config.NumberColumn("Lift after", format="%.2fx"),
                          "top_quintile_lift_post_lo": st.column_config.NumberColumn("After, low", format="%.2fx"),
                          "top_quintile_lift_post_hi": st.column_config.NumberColumn("After, high", format="%.2fx"),
                          "top_quintile_n_post": "Top-bin rows after", "top_quintile_upgrades_post": "Top-bin upgrades after"})
holds = [E.SIGNALS[s]["short"] for s in k["signals_lift_holds_post"]]
st.markdown(f"<div class='note'>Signals whose top-bin lift stays above 1× with 95% confidence in Apr – Jun 2026: "
            f"{', '.join(holds)}. The other signals were weak before the change as well, or three months of post-change "
            f"data ({k['test_upgrades_post']} upgrades, {k['base_rate_test_post'] * 100:.1f}% base rate) are too few to tell. "
            f"Intervals are account-level bootstraps, so an account's ~10 month-end rows do not count as independent evidence.</div>",
            unsafe_allow_html=True)

# ------------------------------------------------------------------ 4. propensity model and CS capacity
st.subheader("4. Propensity model: what CS capacity buys")
cap = st.slider("CS capacity: Basic accounts worked per month", min_value=25, max_value=300, value=100, step=25, key="es_capacity")
pre, post = E.capacity_metrics(ts, cap, "pre"), E.capacity_metrics(ts, cap, "post")
m = st.columns(5)
m[0].metric("Precision, pre / post", f"{pct(pre['precision'], 1)} / {pct(post['precision'], 1)}",
            f"base {pct(pre['base_rate'])} / {pct(post['base_rate'])}", delta_color="off", delta_arrow="off", border=True,
            help="Share of the worked accounts that upgraded within 90 days, averaged over the test months before "
                 "(Jan – Mar 2026) and after (Apr – Jun 2026) the packaging change.")
m[1].metric("Recall, pre / post", f"{pct(pre['recall'], 0)} / {pct(post['recall'], 0)}",
            f"lift {pre['lift']:.1f}× / {post['lift']:.1f}×", delta_color="off", delta_arrow="off", border=True,
            help="Share of all upgrades in the month that the worked list contained; lift = precision ÷ base rate.")
m[2].metric("Upgrades found / month", f"{pre['upgrades_per_month']:.1f} / {post['upgrades_per_month']:.1f}",
            f"of {pre['upgrades_total_per_month']:.0f} / {post['upgrades_total_per_month']:.0f} a month", delta_color="off", delta_arrow="off",
            border=True)
m[3].metric("MRR found / month", f"{money(pre['mrr_covered_per_month'], 1)} / {money(post['mrr_covered_per_month'], 1)}".replace("$", "\\$"),
            "list-price gap", delta_color="off", delta_arrow="off", border=True,
            help="Seats × (Premium − Basic) list price per seat-month, summed over the upgrades the list contained.")
m[4].metric("AUC, pre / post", f"{k['auc_test_pre']:.2f} / {k['auc_test_post']:.2f}",
            f"{k['auc_test_pre_lo']:.2f}–{k['auc_test_pre_hi']:.2f} / {k['auc_test_post_lo']:.2f}–{k['auc_test_post_hi']:.2f}",
            delta_color="off", delta_arrow="off", border=True, help="95% account-level bootstrap intervals. " + E.auc_change_sentence(k) + ".")
st.markdown(f"<div class='note'>Model {E.SPLIT_NOTE}: {k['train_rows']:,} training rows ({k['train_accounts']:,} accounts), "
            f"{k['embargo_rows']:,} held-out rows, {k['test_rows']:,} test rows with {k['test_upgrades']} upgrades. "
            f"{E.auc_change_sentence(k)}.</div>", unsafe_allow_html=True)
plot(E.fig_capture_curve(cap, curve))
plot(E.fig_odds_ratios(ors))
plot(E.fig_calibration("all", cal))
data_table(ors[["label", "coef", "odds_ratio", "or_lo", "or_hi"]], "Show the model coefficients",
           column_config={"label": "Feature",
                          "coef": st.column_config.NumberColumn("Coefficient (per 1 SD)", **NUM3),
                          "odds_ratio": st.column_config.NumberColumn("Odds ratio", format="%.2f"),
                          "or_lo": st.column_config.NumberColumn("OR low", format="%.2f"),
                          "or_hi": st.column_config.NumberColumn("OR high", format="%.2f")})
data_table(cal[cal["period"] == "all"][["decile", "n", "upgrades", "predicted", "observed", "observed_lo", "observed_hi"]],
           "Show the calibration table (test period)",
           column_config={"decile": "Score decile", "n": "Rows", "upgrades": "Upgrades",
                          "predicted": st.column_config.NumberColumn("Mean predicted", format="percent"),
                          "observed": st.column_config.NumberColumn("Observed", format="percent"),
                          "observed_lo": st.column_config.NumberColumn("Observed low", format="percent"),
                          "observed_hi": st.column_config.NumberColumn("Observed high", format="percent")})

# ------------------------------------------------------------------ 5. opportunity list
st.subheader(f"5. Upgrade opportunities at 30 Sep 2026 — the top {cap} by expected MRR")
cov = st.multiselect("CS coverage", sorted(opp_all["cs_coverage"].dropna().unique().tolist()), default=[], key="es_cov",
                     placeholder="All coverage tiers")
opp = opp_all if not cov else opp_all[opp_all["cs_coverage"].isin(cov)]
s = E.opportunity_summary(opp, cap)
o = st.columns(5)
o[0].metric("Basic accounts scored", f"{s['n_scored']:,}", f"mean {pct(opp['propensity'].mean())}",
            delta_color="off", delta_arrow="off", border=True, help="Every Basic account paying at 30 Sep 2026 with 3+ months of tenure "
                                                 "(and in the selected coverage tiers), scored with the deployment model.")
o[1].metric("Expected upgrades", f"{s['expected_upgrades']:.1f}", f"of {s['total_expected_upgrades']:.0f} in full list",
            delta_color="off", delta_arrow="off", border=True, help="Sum of the 90-day propensities of the listed accounts; the full list "
                                                 "is every scored account.")
o[2].metric("Expected MRR, top list", money(s["expected_mrr"], 1), f"{pct(s['share_of_expected_mrr'], 0)} of the list",
            delta_color="off", delta_arrow="off", border=True, help="Propensity × seats × (Premium − Basic) list price per seat-month, summed.")
o[3].metric("Ceiling MRR, top list", money(s["incremental_mrr_if_all"], 1), f"if all {s['worked']} upgraded", delta_color="off", delta_arrow="off",
            border=True, help="The MRR if every listed account upgraded: a ceiling, not a forecast.")
o[4].metric("Lowest propensity", pct(s["min_propensity"]), f"post-April base {pct(k['deploy_post_base_rate'])}",
            delta_color="off", delta_arrow="off", border=True, help="The smallest 90-day propensity on the list, against the observed "
                                                 "upgrade rate of the Apr – Jun 2026 month-ends the scores are centred on.")
# Expected MRR and propensity sit right after the company, since the list is ranked by expected MRR.
show = opp.head(cap).copy()
show["propensity_pct"] = show["propensity"] * 100
show["csm_owner"] = show["csm_owner"].fillna("—")
show = show[["rank", "company_name", "expected_mrr", "propensity_pct", "incremental_mrr_if_upgrade", "segment", "cs_coverage",
             "csm_owner", "seats", "billing_period", "current_arr", "next_renewal_date", "reason_code_1", "reason_code_2",
             "reason_code_3", "evidence", "customer_id"]]
st.dataframe(show, hide_index=True, width="stretch",
             column_config={"rank": "Rank", "company_name": "Company",
                            "expected_mrr": st.column_config.NumberColumn("Expected MRR", format="dollar"),
                            "propensity_pct": st.column_config.NumberColumn("Propensity (90 d)", format="%.1f%%"),
                            "incremental_mrr_if_upgrade": st.column_config.NumberColumn("MRR if upgraded", format="dollar"),
                            "segment": "Segment", "cs_coverage": "CS coverage", "csm_owner": "CSM owner", "seats": "Seats",
                            "billing_period": "Billing",
                            "current_arr": st.column_config.NumberColumn("Current ARR", format="dollar"),
                            "next_renewal_date": st.column_config.DateColumn("Next renewal", format="YYYY-MM-DD"),
                            "reason_code_1": "Reason 1", "reason_code_2": "Reason 2", "reason_code_3": "Reason 3",
                            "evidence": "Evidence", "customer_id": "Customer ID"})
st.download_button("Download the full ranked list (CSV)", opp.to_csv(index=False).encode(), "expansion_opportunities.csv", "text/csv")
with st.expander("Reason codes, in plain words", expanded=False, icon=":material/menu_book:"):
    st.dataframe(reasons, hide_index=True, width="stretch", column_config={"reason_code": "Reason code", "definition": "Definition"})

how_to_read(f"""
**What this page answers.** Which Basic accounts look like they are about to upgrade, so Customer Success can spend its
limited time on them. Everything is measured at a month-end using only what was known then; the outcome is a voluntary
upgrade in the following 90 days (forced seat-cap upgrades are excluded, {k['forced_upgrade_events']} events).

**1. The dot plot** compares {k['n_upgraders']} upgraders (the last month-end before each upgrade) with {k['n_stayers']} stayers
drawn from the same calendar month and tenure band, so the comparison is not distorted by the April change or by account
age. The x-axis is the standardized mean difference: 0.2 is a small gap, 0.5 medium, 0.8 large. Bars are bootstrap
intervals; a bar crossing zero means the data cannot tell the groups apart on that signal.

**2. The rule tool** turns one signal into a CS rule — "the signal is in the top 20% of Basic account-months" — and shows
the upgrade rate at or above that threshold; *lift* is that rate divided by the base rate. The bar chart shows the same
thing by quintile (the default rule is its top bin); whiskers are 95% intervals, so a tall bar with wide whiskers is a
small bin. Switch the period to see whether the rule still works after April. Rows with no value for the signal (no
active users in the window) are left out of the rule, which is why its population is a few rows short of the headline.

**3. The drift charts** show why absolute thresholds break: advanced automations moved into Basic on 15 April 2026 and
paywall hits per active user fell {pct(-k['gate_hits_drift'], 0)} (Oct 2025 – Mar 2026 vs Jul – Sep 2026 month-ends, the
same windows as the tile above). The second chart recomputes each signal's top-bin lift *within* each period; signals whose
interval stays above 1× after the change are still usable.

**4. The model** is a logistic regression on the signals plus tenure, segment, channel and billing period (the three
paywall / automation signals as within-month percentiles). It is {E.SPLIT_NOTE}; the three held-out month-ends are an
embargo equal to the 90-day label horizon, so no training label is also a test label. The capacity slider sets how many
accounts CS can work a month: precision is the share of those that upgrade, recall the share of all upgrades they contain.
The capture curve shows the trade-off for any capacity. The calibration chart shows the model is over-confident in its top
deciles, so use it to rank, and read the expected values as a ceiling.

**5. The opportunity list** scores every Basic account paying at 30 September 2026. Expected MRR = propensity × seats ×
the Premium − Basic price gap on the account's billing period ($20 monthly, $16 annual, per seat-month). Reason codes
name the two or three signals that pushed the score up; the glossary defines each in plain words.

**Intervals.** An account contributes up to ~10 month-end rows, so rates on account-month rows are not independent
evidence: bin rates, lifts, the rule tool and the AUC carry account-level bootstrap intervals (accounts resampled, rows
kept together). The SMD intervals bootstrap the matched rows; the calibration whiskers are Wilson intervals on rows.
""")
