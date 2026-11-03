"""Overview: the board numbers and the things to know about Q3 2026."""
import pandas as pd
import streamlit as st

from common import finding, how_to_read, key_numbers, page_header, plot
from saas_rev.analysis import metrics as M
from saas_rev.theme import delta_pct, delta_pts, money, pct, pts, qname

page_header("Revenue review — Q3 2026",
            "Where do recurring revenue and retention stand at the Q3 close, and what changed in the quarter?")

k = key_numbers("metrics")
sc = key_numbers("silent_churn")
tr = key_numbers("transitions")


def gap(d: float, ref: str) -> str:
    """'3.5 pts below June' / 'level with June' (words, so the reader does not have to parse a sign)."""
    if abs(d) < 0.0005:
        return f"level with {ref}"
    return f"{pts(abs(d)).lstrip('+')} {'below' if d < 0 else 'above'} {ref}"


# every retention / rate tile compares with the same quarter a year earlier; the since-June story is told in words
c = st.columns(5)
c[0].metric("ARR", money(k["arr"]), delta_pct(k["mrr_yoy_growth"], 0, suffix=" vs Sep 2025"), border=True,
            help="MRR × 12 at 30 Sep 2026. Run-rate of live subscriptions, not GAAP revenue.")
c[1].metric("12-month NRR", pct(k["nrr_ttm"]), delta_pts(k["nrr_ttm"] - k["nrr_ttm_2025_09"], suffix=" vs Sep 2025"),
            border=True,
            help=f"Base: customers paying at 30 Sep 2025; same customers, point-to-point (definitions §7). "
                 f"Now {gap(k['nrr_ttm'] - k['nrr_ttm_2026_06'], 'June')} ({pct(k['nrr_ttm_2026_06'])}); "
                 f"{pct(k['nrr_ttm_ex_one_off'])} without the one-off rollout.")
c[2].metric("12-month GRR", pct(k["grr_ttm"]), delta_pts(k["grr_ttm"] - k["grr_ttm_2025_09"], suffix=" vs Sep 2025"),
            border=True, help="Each customer capped at its starting MRR; excludes expansion (definitions §8).")
c[3].metric("Q3 expansion rate", pct(k["expansion_rate_q3_2026"]),
            delta_pts(k["expansion_rate_q3_2026"] - k["expansion_rate_q3_2025"], suffix=" vs Q3 2025"), border=True,
            help="Expansion MRR in the quarter ÷ MRR at the start of the quarter.")
c[4].metric("Paying customers", f"{k['paying_customers']:,}", f"ARPA {money(k['arpa'], 0)}", delta_color="off",
            delta_arrow="off", border=True, help="Customers with MRR > 0 at 30 Sep 2026; ARPA = MRR ÷ paying customers.")

st.subheader("What to know")
one_off_month = pd.Timestamp(k["one_off_month"] + "-01").strftime("%B") if k.get("one_off_month") else ""
finding(f"<b>NRR is falling, with or without the one-off.</b> 12-month NRR is {pct(k['nrr_ttm'])}: "
        f"{gap(k['nrr_ttm'] - k['nrr_ttm_2026_06'], 'June')} ({pct(k['nrr_ttm_2026_06'])}) and "
        f"{gap(k['nrr_ttm'] - k['nrr_ttm_2025_09'], 'Sep 2025')}. The {one_off_month} rollout at {k['one_off_company']} is "
        f"inside both the June and the September figure; without it NRR went from {pct(k['nrr_ttm_2026_06_ex_one_off'])} to "
        f"{pct(k['nrr_ttm_ex_one_off'])} over the same three months. Q3 alone annualises to {pct(k['nrr_runrate_q3_2026'])} "
        f"({pct(k['nrr_runrate_q3_2025'])} in Q3 2025), so the 12-month figure keeps falling if Q4 looks like Q3.")

drop = k["expansion_q2_2026"] - k["expansion_q3_2026"]
below_ly = k["expansion_rate_q3_2025"] - k["expansion_rate_q3_2026"]
trigger = ("past the 1-point re-forecast line" if below_ly >= 0.01 else
           "inside the 0.5-point timing band" if below_ly <= 0.005 else "between the timing band and the 1-point re-forecast line")
finding(f"<b>Most of the drop from Q2 is a one-off, but the underlying rate is down too.</b> Q3 expansion was "
        f"{money(k['expansion_q3_2026'])}, {pct(k['expansion_change_qoq'], 0, signed=True)} vs Q2; Q2 included a "
        f"{money(k['one_off_expansion_mrr'])} company-wide rollout at {k['one_off_company']} "
        f"({pct(k['one_off_expansion_mrr'] / drop, 0)} of the {money(drop)} drop). Like for like, the expansion rate fell from "
        f"{pct(k['expansion_rate_q2_2026_ex_one_off'])} to {pct(k['expansion_rate_q3_2026'])} of starting MRR, and from "
        f"{pct(k['expansion_rate_q3_2025'])} a year ago — {trigger}.")

by = k["bridge_yoy"]
plan_when = ""
if tr.get("non_ps_rate_apr_sep_2025") is not None:
    plan_when = (f"; outside Paid Social the monthly Basic upgrade rate fell in two steps, "
                 f"{pct(tr['non_ps_rate_apr_sep_2025'], 1)} (Apr–Sep 2025) → {pct(tr['non_ps_rate_oct_2025_mar_2026'], 1)} "
                 f"(Oct 2025 – Mar 2026) before anything changed, then → {pct(tr['upgrade_rate_non_ps_post'], 1)} after the April "
                 f"packaging change")
seats = by["Non-Tech seat expansion"]
finding(f"<b>Two structural drivers (vs Q3 2025):</b> Technology accounts all but stopped expanding (expansion "
        f"{pct(k['tech_base_expansion_rate_q3_2025'])} → {pct(k['tech_base_expansion_rate_q3_2026'])} of their own MRR in the quarter) "
        f"and started cutting (contraction {pct(k['tech_base_contraction_rate_q3_2025'])} → "
        f"{pct(k['tech_base_contraction_rate_q3_2026'])}): {money(abs(by['Technology (all)']))} less expansion than their "
        f"Q3 2025 rate implies. Non-Tech plan upgrades added {money(abs(by['Non-Tech plan upgrades']))} less{plan_when}. "
        f"Non-Tech seat expansion {'grew' if seats >= 0 else 'fell'} ({money(abs(seats))} {'more' if seats >= 0 else 'less'}).")

if sc:
    prof, rule = sc.get("profile", {}), sc.get("rule", {})
    finding(f"<b>Quiet downgrades are growing and concentrated in Technology.</b> {sc.get('flagged_accounts', 0):,} paying "
            f"accounts pay at least 20% less than their peak of the last six months, after a seat, plan or discount cut, without "
            f"cancelling ({money(sc.get('arr_at_risk', 0), 2)} ARR still on the "
            f"books), up from {sc.get('flagged_accounts_2025_09', 0):,} a year ago; {pct(prof.get('technology_share_flagged'), 0)} "
            f"of them are Technology accounts, vs {pct(prof.get('technology_share_other'), 0)} of other paying accounts. In the "
            f"back-test, low seat utilization flagged {pct(rule.get('account_recall'), 0)} of the accounts that later contracted "
            f"or churned, a median {rule.get('by_billing_period', {}).get('monthly', {}).get('lead_median', 0):.0f} months ahead for "
            f"monthly-billed accounts (annual-billed: usually the full {rule.get('by_billing_period', {}).get('annual', {}).get('lead_median', 0):.0f} "
            f"months the back-test window allows) — "
            f"enough to rank the CSM queue, not to predict "
            "every loss.")
else:
    finding(f"<b>Contraction nearly doubled year over year</b> ({pct(k['contraction_rate_q3_2025'])} → "
            f"{pct(k['contraction_rate_q3_2026'])} of starting MRR); at Technology accounts it went from "
            f"{pct(k['tech_base_contraction_rate_q3_2025'])} to {pct(k['tech_base_contraction_rate_q3_2026'])} of their MRR.")

plot(M.fig_nrr_trend())
plot(M.fig_mrr_trend())

how_to_read("""
**What this page is.** The one-screen summary a VP of Revenue would take into the board pack. Every tile compares
with the same point a year earlier, so seasonality cannot flatter or hide a change.

- **ARR** is MRR × 12 at the quarter close. MRR counts each live subscription's monthly price after discounts;
  annual contracts count 1/12 of the annual price every month (not the full amount when invoiced).
- **12-month NRR** takes every customer who was paying a year ago and compares what they pay today with what they
  paid then. Above 100% means existing customers grew (more seats, upgrades) more than others shrank or left.
  New customers are *not* included — NRR isolates the existing base.
- **GRR** is the same calculation but nobody can count for more than they paid a year ago, so it measures only
  what was *kept*. NRR − GRR ≈ how much expansion added.
- **Expansion rate** = extra MRR from existing customers in the quarter ÷ MRR at the start of the quarter.
  Comparing rates (not dollars) removes the effect of the company simply being bigger.
- In the NRR chart, blue is NRR and dark gray is GRR; the light-gray start of each line is when the base was under
  1,000 customers — too small to read trends from. The dotted blue line removes the one-off enterprise rollout so you
  can see the underlying level.

**Why it matters.** The board sees one NRR number. This page shows whether that number is a trend or an accident,
which is the difference between "hold the forecast" and "re-forecast and act".
""")
