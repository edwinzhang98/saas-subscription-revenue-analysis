"""Report sections for the slice analyses (plan transitions, expansion signals, silent churn, recommendations).

Imported by report.py at the end of the module; each builder registers itself in report.SLICE_SECTIONS, and the
summary bullets for silent churn and recommendations go into report.SUMMARY_EXTRA. The static PNGs for the README
are registered in figures.EXTRA_FIGURES. Every number is read from reports/key_numbers/*.json or the marts.
"""
from __future__ import annotations

import pandas as pd

from . import figures, marts, report
from .report import fig, finding, kpis, section, table
from .theme import money, pct, pts


def _k(topic: str) -> dict:
    return marts.read_key_numbers(topic)


# =============================================================================== plan transitions
def s_transitions() -> dict:
    from .analysis import transitions as T
    k = _k("transitions")
    if not k:
        return section("transitions", "Plan transitions", "<p>Not built.</p>")
    t12 = k["transition_12m_basic"]
    body = kpis([
        ("Basic → higher, per month", pct(k["upgrade_rate_post"], 2), f"{pts(k['upgrade_rate_change_pts'], 2)} vs Apr 2025 – Mar 2026"),
        ("Same, Paid Social excluded", pct(k["upgrade_rate_non_ps_post"], 2),
         f"95%: {pts(k['upgrade_rate_non_ps_change_lo'], 2)} to {pts(k['upgrade_rate_non_ps_change_hi'], 2)}"),
        ("Share of the fall from channel mix", pct(k["mix_share_of_change"], 0), "Paid Social share of Basic base "
         f"{pct(k['paid_social_share_basic_base_pre'], 0)} → {pct(k['paid_social_share_basic_base_post'], 0)}"),
        ("Net MRR effect of the change", f"{money(k['net_mrr_per_month'])} / month",
         f"range {money(k['net_mrr_lo'])} to {money(k['net_mrr_hi'])}"),
    ])
    body += finding(
        f"<b>Basic → Premium upgrades halved after the April packaging change, and the shift toward Paid Social signups explains "
        f"only {pct(k['mix_share_of_change'], 0)} of it.</b> The voluntary upgrade rate fell from {pct(k['upgrade_rate_pre'], 2)} to "
        f"{pct(k['upgrade_rate_post'], 2)} of Basic accounts a month ({k['voluntary_upgrades_pre']} upgrades in 12 months before, "
        f"{k['voluntary_upgrades_post']} in 5 months after). It fell in every channel, segment and tenure band. The timing points at "
        f"the packaging change, but the rate was already drifting down before April ({pts(k['non_ps_pre_trend_slope_pts_per_month'], 3)} "
        f"a month): net of that drift the step is {pts(k['non_ps_step_net_of_trend_pts'], 2)} ({pct(k['non_ps_step_net_of_trend_rel'], 0)}), "
        f"so the packaging effect is a range, not a point. This is a before / after comparison, not an experiment.")
    body += fig(T.fig_upgrade_rate_monthly(), "<b>Reading:</b> each point is the share of Basic accounts (Paid Social excluded) that upgraded "
                "that month, with its 95% band; the two flat lines are the pooled before and after rates, and the dotted line "
                "extends the trend fitted on the before window.")
    summary, tab = T.mix_rate_decomposition()
    body += fig(T.fig_mix_rate(summary), "<b>Mix vs rate:</b> if the channel mix had changed and nothing else, the upgrade rate would "
                f"have moved {pts(k['mix_effect_pts_shapley'], 2)}; the within-channel rate change accounts for "
                f"{pts(k['rate_effect_pts_shapley'], 2)} (average of both orderings).")
    body += finding(
        f"<b>The retention upside the change was meant to buy is too small to see.</b> Basic logo churn outside Paid Social went from "
        f"{pct(k['basic_churn_non_ps_may_sep_2025'], 2)} to {pct(k['basic_churn_non_ps_may_sep_2026'], 2)} a month (May–Sep, year over year; "
        f"95% interval of the change {pts(k['basic_churn_non_ps_change_lo'], 2)} to {pts(k['basic_churn_non_ps_change_hi'], 2)}, i.e. "
        f"possibly zero). Net for the Basic base: about {money(-k['foregone_upgrade_mrr_per_month'])} a month of upgrade MRR foregone vs "
        f"{money(k['retained_basic_mrr_per_month'])} kept by lower churn — <b>{money(k['net_mrr_per_month'])} a month</b> "
        f"({money(k['net_mrr_per_month_trend_adjusted'])} if the earlier drift would have continued anyway). The trial-to-paid "
        "conversion goal cannot be measured from billing data.")
    body += fig(T.fig_basic_churn_monthly())
    body += fig(T.fig_sankey(12), f"<b>Where customers go:</b> of Basic accounts, {pct(t12['basic'], 0)} are still on Basic 12 months "
                f"later, {pct(t12['premium'], 0)} moved up and {pct(t12['churned'], 0)} churned (all account-months with a complete "
                "12-month window).")
    body += finding(
        f"<b>Downgrades are rare and CS saves mostly hold.</b> {k['downgrades_last_12m']} plan downgrades in the last 12 months "
        f"({money(k['downgrades_last_12m_mrr_delta'])} MRR; {k['downgrades_last_12m_by_source'].get('customer_success', 0)} were CS saves of "
        f"accounts that asked to cancel). {pct(k['cs_save_paying_6m_share'], 0)} of saved accounts were still paying 6 months later "
        f"(95%: {pct(k['cs_save_paying_6m_lo'], 0)}–{pct(k['cs_save_paying_6m_hi'], 0)}, n = {k['cs_save_downgrades_observed_6m']}) vs "
        f"{pct(k['cs_save_comparable_paying_6m_share'], 0)} of comparable accounts that never downgraded.")
    ttu = k["time_to_upgrade_12m_by_channel"]
    others = [v for c, v in ttu.items() if c != "Paid Social"]
    body += fig(T.fig_time_to_upgrade("acquisition_channel"),
                f"<b>Channel quality:</b> 12 months after signup, {pct(ttu['Paid Social'], 0)} of Paid Social signups had upgraded vs "
                f"{pct(min(others), 0)}–{pct(max(others), 0)} for other channels, and {pct(k['churn_12m_by_channel']['Paid Social'], 0)} had churned.")
    return section("transitions", "Plan transitions: who upgrades, who downgrades", body)


report.SLICE_SECTIONS["transitions"] = s_transitions
figures.EXTRA_FIGURES += [
    ("plan_sankey", "transitions", "fig_sankey", {"horizon": 12}, 1200),
    ("upgrade_rate_monthly", "transitions", "fig_upgrade_rate_monthly", {}, 1200),
]


# =============================================================================== expansion signals
def s_signals() -> dict:
    from .analysis import expansion as E
    k = _k("expansion")
    if not k:
        return section("signals", "Expansion signals", "<p>Not built.</p>")
    top = k["top_signals"]
    body = kpis([
        ("Upgraders vs stayers compared", f"{k['n_upgraders']} vs {k['n_stayers']}", f"matched on month × tenure ({k['n_strata']} strata)"),
        ("Strongest signal (SMD)", f"{top[0]['smd']:.2f}", f"95%: {top[0]['smd_lo']:.2f}–{top[0]['smd_hi']:.2f}"),
        ("Ranking model, test AUC", f"{k['auc_test']:.2f}", f"before / after April: {k['auc_test_pre']:.2f} / {k['auc_test_post']:.2f}"),
        ("Top 100 a month, 90-day upgrade rate", pct(k["precision_at_100_all"], 1), f"vs {pct(k['base_rate_test'], 1)} base rate"),
    ])
    body += finding(
        f"<b>Basic accounts that go on to upgrade are the ones already hitting the Premium paywall.</b> In the 13 weeks before upgrading, "
        f"a median {pct(top[0]['median_upgraders'], 0)} of their active users hit a Premium-only feature vs {pct(top[0]['median_stayers'], 0)} "
        f"for comparable accounts that stayed on Basic; accounts in the top fifth on this signal upgraded within 90 days at "
        f"{top[0]['top_quintile_lift']:.1f}× the average rate. Automation use per active user is the next signal (SMD {top[2]['smd']:.2f}). "
        "Seat growth, utilization, invitations and integrations separate upgraders only weakly. These are associations: they say "
        "where to look, not what makes accounts upgrade.")
    body += fig(E.fig_smd_dotplot(), "<b>Reading:</b> each dot is the standardized difference between upgraders and stayers (0 = no "
                "difference; 0.5 = half a standard deviation); bars are bootstrap 95% intervals; gray = interval includes zero.")
    body += finding(
        f"<b>The April packaging change moved the signal itself.</b> Paywall hits per active user fell {pct(-k['gate_hits_drift'], 0)} "
        f"after April because the gated features moved into Basic, so a fixed threshold (e.g. \"5 paywall hits a week\") would have "
        f"silently stopped firing. Ranked within each month, the signal keeps its lift ({k['signal_lift_pre_post']['gate_user_share']['pre']:.1f}× "
        f"before, {k['signal_lift_pre_post']['gate_user_share']['post']:.1f}× after), which is why the model and the CS list use "
        "within-month percentiles.")
    body += fig(E.fig_gate_drift())
    body += finding(
        f"<b>A simple model ranks accounts usefully but modestly.</b> Trained on month-ends to Dec 2025 and tested on Jan–Jun 2026, "
        f"logistic regression (no tuning) reaches AUC {k['auc_test']:.2f}. Working the top 100 accounts a month would reach "
        f"{pct(k['recall_at_100_all'], 0)} of upgrades at {pct(k['precision_at_100_all'], 1)} precision ({k['lift_at_100_all']:.1f}× the "
        f"{pct(k['base_rate_test'], 1)} base rate). The top decile is over-confident (predicted probabilities above the observed rate), "
        f"so expected values on the opportunity list are ceilings. Today's list: {k['n_opportunities']:,} Basic accounts scored, "
        f"{k['opportunities_expected_upgrades']:.0f} expected upgrades in 90 days.")
    body += fig(E.fig_capture_curve(100))
    return section("signals", "Expansion signals: who is about to upgrade", body)


# =============================================================================== silent churn
def s_silent() -> dict:
    from .analysis import silent_churn as SC
    k = _k("silent_churn")
    if not k:
        return section("silent", "Silent churn", "<p>Not built.</p>")
    prof, rule, fu, wl = k["profile"], k["rule"], k["followup"], k["watchlist"]
    rec = _k("recommendations")
    trig = rec.get("head_of_cs_trigger", {}) if rec else {}
    mix = k["contraction_mix"]
    body = kpis([
        ("Accounts shrinking without cancelling", f"{k['flagged_accounts']:,}", f"{k['silent_accounts']} silent + {k['saved_accounts']} saved by CS"),
        ("ARR still on the books", money(k["arr_at_risk"], 2), f"{pct(k['arr_at_risk_share_of_arr'])} of ARR"),
        ("ARR already lost", money(k["lost_arr"]), "peak MRR − current MRR, × 12"),
        ("Technology share", pct(prof["technology_share_flagged"], 0), f"vs {pct(prof['technology_share_other'], 0)} of other accounts"),
    ])
    body += finding(
        f"<b>{k['flagged_accounts']:,} paying accounts have cut their spend by at least 20% in six months without cancelling</b> — "
        f"{money(k['arr_at_risk'], 2)} of ARR still on the books ({pct(k['arr_at_risk_share_of_arr'])} of ARR) and {money(k['lost_arr'])} already gone. "
        f"The list more than tripled in a year ({k['flagged_accounts_2025_09']} accounts in Sep 2025, {k['flagged_accounts_2026_06']} in June). "
        f"Technology accounts are {pct(prof['technology_share_flagged'], 0)} of them vs {pct(prof['technology_share_other'], 0)} of other paying "
        f"accounts; their flag rate went from {pct(prof['technology_flag_rate_2026_06'], 1)} in June to {pct(prof['technology_flag_rate'], 1)} in "
        f"September, while other industries stayed near {pct(prof['other_flag_rate'], 1)} — consistent with the hiring slowdown CSMs reported.")
    body += fig(SC.fig_flagged_trend(marts.load("silent_churn__flag_trend")))
    body += fig(SC.fig_tech_flag_rate(marts.load("silent_churn__flag_rate_tech")))
    body += fig(SC.fig_contraction_mix(marts.load("silent_churn__contraction_mix")),
                f"<b>How the money left:</b> {mix['seat_cut_monthly_accounts']} of {k['flagged_accounts']} accounts cut seats at a monthly "
                f"billing date (median cut {pct(mix['median_seat_cut_pct'], 0)} of seats); {mix['seat_rightsize_renewal_accounts']} right-sized "
                f"at an annual renewal; {mix['plan_downgrade_accounts']} downgraded the plan.")
    body += finding(
        f"<b>Silent contraction is a loss already booked, not a churn predictor.</b> Accounts newly flagged in Jan 2025 – Mar 2026 churned "
        f"within 6 months at {pct(fu['silent_churn_6m'], 1)} (95%: {pct(fu['silent_churn_6m_lo'], 1)}–{pct(fu['silent_churn_6m_hi'], 1)}, "
        f"n = {fu['silent_n']}) vs {pct(fu['unflagged_matched_churn_6m'], 1)} for unflagged accounts with the same segment, billing and tenure "
        "mix. So the CS list does not rank by past contraction; it ranks by a usage signal that comes before it.")
    body += fig(SC.fig_followup(marts.load("silent_churn__followup")))
    chosen = SC.rule_thresholds()
    body += finding(
        f"<b>Early warning: seat utilization below {pct(rule['utilization_threshold'], 0)} over 13 weeks.</b> Chosen from a grid of "
        f"utilization and WAU-trend thresholds by a rule fixed in advance (highest recall with precision ≥ 2× the base rate and no more "
        f"CSM accounts a quarter than CS can review). Back-test: {pct(rule['precision'], 1)} of flagged account-months contracted ≥ 10% or "
        f"churned within the outcome window vs a {pct(rule['base_rate'], 1)} base rate ({rule['lift']:.1f}× lift); "
        f"{pct(rule['account_recall'], 0)} of the accounts that later contracted were flagged first, a median {rule['lead_median']:.0f} "
        f"months ahead ({rule['by_billing_period']['monthly']['lead_median']:.0f} for monthly, {rule['by_billing_period']['annual']['lead_median']:.0f} "
        "for annual accounts). Adding a WAU-trend condition lowered recall without raising precision. It misses most losses: "
        "use it to rank the CSM queue, not to forecast churn.")
    body += fig(SC.fig_pr_tradeoff(marts.load("silent_churn__backtest_grid"), chosen))
    body += fig(SC.fig_lead_time(marts.load("silent_churn__lead_times"), chosen))
    if trig:
        body += finding(
            f"<b>The CS list.</b> {wl['accounts']:,} accounts are on the risk list today ({wl['csm_queue']} with a CSM, {wl['digital_queue']} "
            f"digital). The first 120 CSM-covered accounts — annual renewals within 120 days first, then ARR at stake — hold "
            f"{pct(trig['share_by_priority'], 0)} of the list's ARR at stake ({pct(trig['share_by_arr_at_stake'], 0)} if ranked purely by ARR at "
            "stake), above the Head of CS's 60% threshold, so the plan is CSM-led reviews for those 120 and a digital play for the rest "
            "(<code>docs/account_health_list.csv</code>).")
    return section("silent", "Silent churn: who is quietly downgrading", body)


# =============================================================================== recommendations
def s_recommendations() -> dict:
    from .analysis import recommendations as R
    rec = _k("recommendations")
    if not rec:
        return section("recommendations", "Recommendations", "<p>Not built.</p>")
    t = marts.load("recommendations__plays").sort_values("nrr_pts", ascending=False)
    body = ("<p>Seven plays. Each has an owner, a target list that exists today, an expected effect on next year's NRR built from "
            "measured numbers plus one stated assumption (the effect of the play, with a low–high range), a holdout that will measure "
            "that effect, and its risks. Every account counts in one play only. Plays aimed at new signups barely move next year's NRR "
            "(new customers are not in its base) and are sized only on what reaches existing customers within 12 months.</p>")
    body += fig(R.fig_impact(marts.load("recommendations__plays")))
    for r in t.itertuples():
        run_rate = (f" Once rolled out: about {money(r.run_rate_mrr_per_year)} of MRR a year."
                    if pd.notna(r.run_rate_mrr_per_year) else "")
        body += (f"<div class='play'><h3>{r.id} · {r.play}</h3>"
                 f"<p class='meta'><b>Owner:</b> {r.owner} · <b>Next year's NRR:</b> +{r.nrr_pts * 100:.2f} pts "
                 f"(range +{r.nrr_pts_low * 100:.2f} to +{r.nrr_pts_high * 100:.2f})</p>"
                 f"<p><b>Who:</b> {r.who}</p><p><b>What:</b> {r.what}</p><p><b>Why:</b> {r.evidence}</p>"
                 f"<p><b>Sizing:</b> {r.sizing_text}.{(' ' + r.sizing_note) if r.sizing_note else ''}{run_rate}</p>"
                 f"<p><b>How we will know:</b> {r.measure}</p><p><b>Risks:</b> {r.risk}</p></div>")
    return section("recommendations", "Recommendations", body)


def _summary_silent(sc: dict) -> str:
    rule = sc.get("rule", {})
    return finding(
        f"<b>Quiet downgrades:</b> {sc['flagged_accounts']:,} paying accounts cut spend by ≥ 20% in six months without cancelling "
        f"({money(sc['arr_at_risk'], 2)} ARR still on the books, {money(sc['lost_arr'])} already lost), up from {sc['flagged_accounts_2025_09']} a "
        f"year ago; {pct(sc['profile']['technology_share_flagged'], 0)} are Technology accounts. Their past contraction does not predict "
        f"further loss, but low seat utilization does: accounts under {pct(rule.get('utilization_threshold'), 0)} contracted or churned "
        f"{rule.get('lift', 0):.1f}× as often, a median {rule.get('lead_median', 0):.0f} months later — that is how the CS list is ranked.")


def _summary_recs(rec: dict) -> str:
    t = marts.load("recommendations__plays").sort_values("nrr_pts", ascending=False)
    top = t.head(3)
    names = "; ".join(f"{r.id} {r.play} (+{r.nrr_pts * 100:.2f})" for r in top.itertuples())
    return finding(
        f"<b>What to do:</b> seven plays worth about +{rec['total_nrr_pts_base'] * 100:.1f} NRR points next year (range "
        f"+{rec['total_nrr_pts_low'] * 100:.1f} to +{rec['total_nrr_pts_high'] * 100:.1f}), led by {names}. The effect of each play is an "
        "assumption until its holdout reads out; the lists, MRR and loss rates behind them are measured.")


report.SLICE_SECTIONS.update({"signals": s_signals, "silent": s_silent, "recommendations": s_recommendations})
report.SUMMARY_EXTRA.update({"silent": _summary_silent, "recs": _summary_recs})
def _fig_silent_trend():
    from .analysis import silent_churn as SC
    return SC.fig_flagged_trend(marts.load("silent_churn__flag_trend"))


def _fig_tradeoff():
    from .analysis import silent_churn as SC
    return SC.fig_pr_tradeoff(marts.load("silent_churn__backtest_grid"), SC.rule_thresholds())


figures.EXTRA_FIGURES += [
    ("expansion_signals_smd", "expansion", "fig_smd_dotplot", {}, 1200),
    ("silent_churn_trend", _fig_silent_trend, 1200),
    ("early_warning_tradeoff", _fig_tradeoff, 1200),
    ("recommendations_impact", "recommendations", "fig_impact", {}, 1200),
]
