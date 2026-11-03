"""Overview: the board numbers and the three things to know about Q3 2026."""
import streamlit as st

from common import finding, how_to_read, key_numbers, page_header, plot
from saas_rev.analysis import metrics as M
from saas_rev.theme import delta_pct, delta_pts, money, pct, pts

page_header("Revenue review — Q3 2026",
            "Where do recurring revenue and retention stand at the Q3 close, and what changed in the quarter?")

k = key_numbers("metrics")
sc = key_numbers("silent_churn")

c = st.columns(5)
c[0].metric("ARR", money(k["arr"]), delta_pct(k['mrr_yoy_growth'], 0, suffix=" vs Sep 2025"), border=True,
            help="MRR × 12 at 30 Sep 2026. Run-rate of live subscriptions, not GAAP revenue.")
c[1].metric("12-month NRR", pct(k["nrr_ttm"]), delta_pts(k["nrr_ttm"] - k["nrr_ttm_2026_06"], suffix=" vs Jun 2026"),
            border=True, help="Base: customers paying at 30 Sep 2025. Same-customer, point-to-point (definitions §7).")
c[2].metric("12-month GRR", pct(k["grr_ttm"]), delta_pts(k["grr_ttm"] - k["grr_ttm_2025_09"], suffix=" vs Sep 2025"),
            border=True, help="Each customer capped at its starting MRR; excludes expansion (definitions §8).")
c[3].metric("Q3 expansion rate", pct(k["expansion_rate_q3_2026"]),
            delta_pts(k["expansion_rate_q3_2026"] - k["expansion_rate_q3_2025"], suffix=" vs Q3 2025"), border=True,
            help="Expansion MRR in the quarter ÷ MRR at the start of the quarter.")
c[4].metric("Paying customers", f"{k['paying_customers']:,}", f"ARPA {money(k['arpa'], 0)}", delta_color="off",
            delta_arrow="off", border=True)

st.subheader("What to know")
finding(f"<b>Expansion did slow, but a third of the headline drop is a one-off.</b> Q3 expansion was "
        f"{money(k['expansion_q3_2026'])}, {pct(k['expansion_change_qoq'], 0, signed=True)} vs Q2. Q2 included a "
        f"{money(k['one_off_expansion_mrr'])} company-wide rollout at {k['one_off_company']}. Like for like, the expansion "
        f"rate fell from {pct(k['expansion_rate_q2_2026_ex_one_off'])} to {pct(k['expansion_rate_q3_2026'])} "
        f"(and from {pct(k['expansion_rate_q3_2025'])} a year ago).")
finding(f"<b>Two structural drivers:</b> Technology accounts stopped adding seats "
        f"({money(abs(k['bridge_yoy']['Technology (all)']))} less expansion than their Q3 2025 rate implies), and "
        f"plan upgrades from Basic halved after the April packaging change "
        f"({money(abs(k['bridge_yoy']['Non-Tech plan upgrades']))} less). Non-Tech seat expansion is healthy.")
if sc:
    prof, rule = sc.get("profile", {}), sc.get("rule", {})
    finding(f"<b>Quiet downgrades are growing and concentrated in Technology.</b> {sc.get('flagged_accounts', 0):,} paying "
            f"accounts cut spend ≥ 20% in six months without cancelling ({money(sc.get('arr_at_risk', 0), 2)} ARR still on the "
            f"books), up from {sc.get('flagged_accounts_2025_09', 0):,} a year ago; {pct(prof.get('technology_share_flagged'), 0)} "
            f"are Technology vs {pct(prof.get('technology_share_other'), 0)} of other accounts. In the back-test, low seat "
            f"utilization flagged {pct(rule.get('account_recall'), 0)} of the accounts that later contracted or churned, a median "
            f"{rule.get('lead_median', 0):.0f} months ahead — enough to rank the CSM queue, not to predict every loss.")
else:
    finding(f"<b>Contraction doubled year over year</b> ({pct(k['contraction_rate_q3_2025'])} → "
            f"{pct(k['contraction_rate_q3_2026'])} of starting MRR), mostly seat cuts at Technology accounts.")

plot(M.fig_nrr_trend())
plot(M.fig_mrr_trend())

st.caption(f"TTM NRR excluding the one-off rollout: {pct(k['nrr_ttm_ex_one_off'])}. Q3 run-rate NRR (annualised): "
           f"{pct(k['nrr_runrate_q3_2026'])} vs {pct(k['nrr_runrate_q3_2025'])} in Q3 2025 — TTM NRR will keep falling "
           "if Q3 repeats.")

how_to_read("""
**What this page is.** The one-screen summary a VP of Revenue would take into the board pack.

- **ARR** is MRR × 12 at the quarter close. MRR counts each live subscription's monthly price after discounts;
  annual contracts count 1/12 of the annual price every month (not the full amount when invoiced).
- **12-month NRR** takes every customer who was paying a year ago and compares what they pay today with what they
  paid then. Above 100% means existing customers grew (more seats, upgrades) more than others shrank or left.
  New customers are *not* included — NRR isolates the existing base.
- **GRR** is the same calculation but nobody can count for more than they paid a year ago, so it measures only
  what was *kept*. NRR − GRR ≈ how much expansion added.
- **Expansion rate** = extra MRR from existing customers in the quarter ÷ MRR at the start of the quarter.
  Comparing rates (not dollars) removes the effect of the company simply being bigger.
- In the NRR chart, the gray part of the line is when the base was under 1,000 customers — too small to read
  trends from. The dotted line removes the one-off enterprise rollout so you can see the underlying level.

**Why it matters.** The board sees one NRR number. This page shows whether that number is a trend or an accident,
which is the difference between "hold the forecast" and "re-forecast and act".
""")
