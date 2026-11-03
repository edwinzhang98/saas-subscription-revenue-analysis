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
curve = load("expansion__capture_curve")
cal = load("expansion__calibration")
ors = load("expansion__odds_ratios")
drift_dist = load("expansion__drift_distribution")
reasons = load("expansion__reason_codes")
PERIODS = {"all": "All months (Jan 2024 – Jun 2026)", "pre": "Before the change (to Mar 2026)",
           "post": "After the change (Apr – Jun 2026)"}

top = k["top_signals"][0]
c = st.columns(5)
c[0].metric("Upgraders vs stayers", f"{k['n_upgraders']} vs {k['n_stayers']}",
            f"matched in {k['n_strata']} month × tenure strata", delta_color="off", border=True,
            help="One row per voluntary Basic → Premium upgrade (the last month-end before it) against one eligible "
                 "month-end of a non-upgrading, non-churning account from the same calendar month and tenure band.")
c[1].metric("Strongest signal", E.SIGNALS[top["signal"]]["short"],
            f"SMD {top['smd']:.2f} · top-quintile lift {top['top_quintile_lift']:.1f}×", delta_color="off", border=True,
            help=top["name"] + ". SMD = standardized mean difference between upgraders and stayers.")
c[2].metric("Model AUC (test)", f"{k['auc_test']:.2f}", f"{k['auc_test_pre']:.2f} before April · {k['auc_test_post']:.2f} after",
            delta_color="off", border=True, help="Logistic regression trained on month-ends to Dec 2025, tested on Jan – Jun 2026.")
c[3].metric("Precision at 100 accounts / month", pct(k["precision_at_100_all"]),
            f"recall {pct(k['recall_at_100_all'], 0)} · base rate {pct(k['base_rate_test'])}", delta_color="off", border=True,
            help="Share of the 100 highest-scored Basic accounts each month that upgraded within 90 days (test period).")
c[4].metric("Paywall hits per active user", f"{k['gate_hits_mean_post']:.2f}",
            delta_pct(k["gate_hits_drift"], 0, suffix=" after April"), border=True,
            help="Mean over Basic account-months, Jul – Sep 2026 vs Oct 2025 – Mar 2026. The packaging change moved "
                 "advanced automations into Basic, so the paywall fires less often.")

finding(f"<b>Upgraders are the accounts bumping into the Premium paywall and automating heavily.</b> In the 13 weeks "
        f"before an upgrade, {pct(top['median_upgraders'], 0)} of a typical upgrader's active users hit the paywall vs "
        f"{pct(top['median_stayers'], 0)} for a matched stayer (SMD {top['smd']:.2f}, 95% interval {top['smd_lo']:.2f}–"
        f"{top['smd_hi']:.2f}). Seat growth, utilization and invitations separate the groups far less. After April the "
        f"paywall fires {pct(-k['gate_hits_drift'], 0)} less often, so thresholds must be relative (top decile this month), "
        f"not absolute.")

# ------------------------------------------------------------------ 1. upgraders vs stayers
st.subheader("1. How upgraders differ from matched stayers")
plot(E.fig_smd_dotplot(sig))
data_table(sig[["name", "n_upgraders", "n_stayers", "median_upgraders", "median_stayers", "smd", "smd_lo", "smd_hi",
                "top_quintile_rate", "top_quintile_lift", "top_quintile_lift_lo", "top_quintile_lift_hi"]],
           "Show the signal table",
           column_config={"smd": st.column_config.NumberColumn("SMD", format="%.2f"),
                          "smd_lo": st.column_config.NumberColumn("SMD low", format="%.2f"),
                          "smd_hi": st.column_config.NumberColumn("SMD high", format="%.2f"),
                          "median_upgraders": st.column_config.NumberColumn("Median, upgraders", format="%.3f"),
                          "median_stayers": st.column_config.NumberColumn("Median, stayers", format="%.3f"),
                          "top_quintile_rate": st.column_config.NumberColumn("Top-bin upgrade rate", format="%.1f%%"),
                          "top_quintile_lift": st.column_config.NumberColumn("Top-bin lift", format="%.2fx"),
                          "top_quintile_lift_lo": st.column_config.NumberColumn("Lift low", format="%.2fx"),
                          "top_quintile_lift_hi": st.column_config.NumberColumn("Lift high", format="%.2fx")})

# ------------------------------------------------------------------ 2. one signal in depth: threshold rule
st.subheader("2. One signal in depth: upgrade rate above vs below a threshold")
cc = st.columns([2, 2, 3])
signal = cc[0].selectbox("Signal", list(E.SIGNALS), format_func=lambda s: E.SIGNALS[s]["name"], index=0, key="es_signal")
period = cc[1].radio("Period", list(PERIODS), format_func=PERIODS.get, index=0, key="es_period", horizontal=False)
sub = pop[~pop["forced"]]
if period != "all":
    sub = sub[sub["period"] == period]
x = sub[signal].dropna()
lo, hi, default = float(x.quantile(0.02)), float(x.quantile(0.98)), float(x.quantile(0.80))
hi = max(hi, lo + 1e-6)
step = max((hi - lo) / 100, 1e-6)
threshold = cc[2].slider(f"Threshold: {E.SIGNALS[signal]['short']} ≥", min_value=lo, max_value=hi, value=min(max(default, lo), hi),
                         step=step, format="%.3f", key="es_threshold")
r = E.threshold_rule(pop, signal, threshold, period)
t = st.columns(5)
t[0].metric("Accounts at or above", f"{r['n_above']:,}", f"{pct(r['share_above'], 0)} of {r['n']:,} account-months",
            delta_color="off", border=True)
t[1].metric("Upgrade rate above", pct(r["rate_above"]), f"95% {pct(r['rate_above_lo'])} – {pct(r['rate_above_hi'])}",
            delta_color="off", border=True)
t[2].metric("Upgrade rate below", pct(r["rate_below"]), f"base rate {pct(r['base_rate'])}", delta_color="off", border=True)
t[3].metric("Lift vs base rate", f"{r['lift']:.2f}×" if pd.notna(r["lift"]) else "–", "upgrade rate above ÷ base rate",
            delta_color="off", border=True)
t[4].metric("Upgrades captured", pct(r["recall"], 0), f"{r['upgrades_above']} of {r['upgrades_total']} upgrades",
            delta_color="off", border=True)
q = quint[(quint["signal"] == signal) & (quint["period"] == period)]
plot(E.fig_quintile_lift(signal, period, q=q))
data_table(q[["bin", "quintile_span", "n", "upgrades", "rate", "rate_lo", "rate_hi", "lift", "share_of_rows"]], "Show the bins",
           column_config={"rate": st.column_config.NumberColumn("Upgrade rate", format="%.2f%%"),
                          "rate_lo": st.column_config.NumberColumn("Rate low", format="%.2f%%"),
                          "rate_hi": st.column_config.NumberColumn("Rate high", format="%.2f%%"),
                          "lift": st.column_config.NumberColumn("Lift", format="%.2fx"),
                          "share_of_rows": st.column_config.NumberColumn("Share of rows", format="%.0f%%")})

# ------------------------------------------------------------------ 3. drift
st.subheader("3. Did the signals survive the April 2026 packaging change?")
plot(E.fig_gate_drift(weekly))
data_table(drift_dist[["period", "name", "account_months", "mean", "p10", "p25", "p50", "p75", "p90", "share_zero"]],
           "Show the before / after distributions",
           column_config={"share_zero": st.column_config.NumberColumn("Share at zero", format="%.0f%%")})
plot(E.fig_lift_pre_post(sig))
data_table(sig[["name", "top_quintile_lift_pre", "top_quintile_lift_pre_lo", "top_quintile_lift_pre_hi", "top_quintile_lift_post",
                "top_quintile_lift_post_lo", "top_quintile_lift_post_hi", "top_quintile_n_post", "top_quintile_upgrades_post"]],
           "Show the lift table")
holds = [E.SIGNALS[s]["short"] for s in k["signals_lift_holds_post"]]
st.markdown(f"<div class='note'>Signals whose top-bin lift stays above 1× with 95% confidence in Apr – Jun 2026: "
            f"{', '.join(holds)}. The other signals were weak before the change as well, or three months of post-change "
            f"data ({k['base_rate_test_post'] * 100:.1f}% base rate) are too few to tell.</div>", unsafe_allow_html=True)

# ------------------------------------------------------------------ 4. propensity model and CS capacity
st.subheader("4. Propensity model: what CS capacity buys")
cap = st.slider("CS capacity: Basic accounts worked per month", min_value=25, max_value=300, value=100, step=25, key="es_capacity")
pre, post = E.capacity_metrics(ts, cap, "pre"), E.capacity_metrics(ts, cap, "post")
m = st.columns(5)
m[0].metric("Precision (before / after April)", f"{pct(pre['precision'], 1)} / {pct(post['precision'], 1)}",
            f"base rate {pct(pre['base_rate'])} / {pct(post['base_rate'])}", delta_color="off", border=True)
m[1].metric("Recall (before / after April)", f"{pct(pre['recall'], 0)} / {pct(post['recall'], 0)}",
            f"lift {pre['lift']:.1f}× / {post['lift']:.1f}×", delta_color="off", border=True)
m[2].metric("Upgrades captured per month", f"{pre['upgrades_per_month']:.1f} / {post['upgrades_per_month']:.1f}",
            f"of {pre['upgrades_total_per_month']:.0f} / {post['upgrades_total_per_month']:.0f} upgrades a month", delta_color="off",
            border=True)
m[3].metric("MRR of captured upgrades / month", f"{money(pre['mrr_covered_per_month'], 1)} / {money(post['mrr_covered_per_month'], 1)}",
            "seats × (Premium − Basic) list price", delta_color="off", border=True)
m[4].metric("Model AUC (test)", f"{k['auc_test']:.2f}", f"{k['auc_test_pre']:.2f} before · {k['auc_test_post']:.2f} after",
            delta_color="off", border=True)
plot(E.fig_capture_curve(cap, curve))
cols2 = st.columns(2)
with cols2[0]:
    plot(E.fig_odds_ratios(ors))
with cols2[1]:
    plot(E.fig_calibration("all", cal))
data_table(ors[["label", "coef", "odds_ratio", "or_lo", "or_hi"]], "Show the model coefficients",
           column_config={"coef": st.column_config.NumberColumn("Coefficient (per 1 SD)", format="%.3f"),
                          "odds_ratio": st.column_config.NumberColumn("Odds ratio", format="%.2f"),
                          "or_lo": st.column_config.NumberColumn("OR low", format="%.2f"),
                          "or_hi": st.column_config.NumberColumn("OR high", format="%.2f")})
data_table(cal[cal["period"] == "all"][["decile", "n", "upgrades", "predicted", "observed", "observed_lo", "observed_hi"]],
           "Show the calibration table (test period)",
           column_config={"predicted": st.column_config.NumberColumn("Mean predicted", format="%.1f%%"),
                          "observed": st.column_config.NumberColumn("Observed", format="%.1f%%"),
                          "observed_lo": st.column_config.NumberColumn("Observed low", format="%.1f%%"),
                          "observed_hi": st.column_config.NumberColumn("Observed high", format="%.1f%%")})

# ------------------------------------------------------------------ 5. opportunity list
st.subheader(f"5. Upgrade opportunities at 30 Sep 2026 — the top {cap} by expected MRR")
cov = st.multiselect("CS coverage", sorted(opp_all["cs_coverage"].dropna().unique().tolist()), default=[], key="es_cov",
                     placeholder="All coverage tiers")
opp = opp_all if not cov else opp_all[opp_all["cs_coverage"].isin(cov)]
s = E.opportunity_summary(opp, cap)
o = st.columns(5)
o[0].metric("Basic accounts scored", f"{s['n_scored']:,}", f"tenure ≥ 3 months · mean propensity {pct(opp['propensity'].mean())}",
            delta_color="off", border=True)
o[1].metric(f"Expected upgrades in the top {s['worked']}", f"{s['expected_upgrades']:.1f}",
            f"of {s['total_expected_upgrades']:.0f} expected across the list", delta_color="off", border=True)
o[2].metric("Expected MRR, top list", money(s["expected_mrr"], 1), f"{pct(s['share_of_expected_mrr'], 0)} of the list's expected MRR",
            delta_color="off", border=True)
o[3].metric("MRR if every listed account upgraded", money(s["incremental_mrr_if_all"], 1), "the ceiling, not a forecast",
            delta_color="off", border=True)
o[4].metric("Lowest propensity in the list", pct(s["min_propensity"]), f"base rate after April {pct(k['deploy_post_base_rate'])}",
            delta_color="off", border=True)
show = opp.head(cap)[["rank", "company_name", "customer_id", "segment", "cs_coverage", "csm_owner", "seats", "billing_period",
                      "current_arr", "next_renewal_date", "propensity", "incremental_mrr_if_upgrade", "expected_mrr",
                      "reason_code_1", "reason_code_2", "reason_code_3", "evidence"]]
st.dataframe(show, hide_index=True, width="stretch",
             column_config={"current_arr": st.column_config.NumberColumn("Current ARR", format="$%.0f"),
                            "next_renewal_date": st.column_config.DateColumn("Next renewal", format="YYYY-MM-DD"),
                            "propensity": st.column_config.NumberColumn("Propensity (90 d)", format="%.1f%%"),
                            "incremental_mrr_if_upgrade": st.column_config.NumberColumn("MRR if upgraded", format="$%.0f"),
                            "expected_mrr": st.column_config.NumberColumn("Expected MRR", format="$%.0f")})
st.download_button("Download the full ranked list (CSV)", opp.to_csv(index=False).encode(), "expansion_opportunities.csv", "text/csv")
with st.expander("Reason codes, in plain words", expanded=False, icon=":material/menu_book:"):
    st.dataframe(reasons, hide_index=True, width="stretch")

how_to_read(f"""
**What this page answers.** Which Basic accounts look like they are about to upgrade, so Customer Success can spend its
limited time on them. Everything is measured at a month-end using only what was known then; the outcome is a voluntary
upgrade in the following 90 days (forced seat-cap upgrades are excluded, {k['forced_upgrade_events']} events).

**1. The dot plot** compares {k['n_upgraders']} upgraders (the last month-end before each upgrade) with {k['n_stayers']} stayers
drawn from the same calendar month and tenure band, so the comparison is not distorted by the April change or by account
age. The x-axis is the standardized mean difference: 0.2 is a small gap, 0.5 medium, 0.8 large. Bars are bootstrap
intervals; a bar crossing zero means the data cannot tell the groups apart on that signal.

**2. The threshold tool** turns one signal into a CS rule: accounts at or above the threshold upgrade at the "rate above",
and *lift* is that rate divided by the base rate. The bar chart shows the same thing by quintile; whiskers are 95%
intervals, so a tall bar with wide whiskers is a small bin. Switch the period to see whether the rule still works after
April.

**3. The drift charts** show why absolute thresholds break: advanced automations moved into Basic on 15 April 2026 and
paywall hits per active user fell {pct(-k['gate_hits_drift'], 0)}. The second chart recomputes each signal's top-quintile
lift *within* each period; signals whose interval stays above 1× after the change are still usable.

**4. The model** is a logistic regression on the signals plus tenure, segment, channel and billing period (the three
paywall / automation signals as within-month percentiles). It is trained on month-ends to December 2025 and tested on
January – June 2026. The capacity slider sets how many accounts CS can work a month: precision is the share of those
that upgrade, recall the share of all upgrades they contain. The capture curve shows the trade-off for any capacity.
The calibration chart shows the model is over-confident in its top deciles, so use it to rank, and read the expected
values as a ceiling.

**5. The opportunity list** scores every Basic account paying at 30 September 2026. Expected MRR = propensity × seats ×
the Premium − Basic price gap on the account's billing period ($20 monthly, $16 annual, per seat-month). Reason codes
name the two or three signals that pushed the score up; the glossary defines each in plain words.
""")
