"""MRR Waterfall: where MRR came from and went, month by month, and why expansion slowed."""
import pandas as pd
import streamlit as st

from common import data_table, how_to_read, key_numbers, load, multiselect_filters, page_header, plot
from saas_rev.analysis import metrics as M
from saas_rev.theme import delta_pct, delta_pts, money, pct, pts, qname

page_header("MRR waterfall",
            "Month by month, how much MRR came from new customers, expansion and win-backs, and how much was lost "
            "to contraction and churn — and why did expansion slow in Q3 2026?")

mv_all = load("fct_mrr_movements")
filters = multiselect_filters(mv_all, {"segment": "Segment", "industry": "Industry",
                                       "acquisition_channel": "Acquisition channel"}, key="wf")
wf = M.monthly_waterfall(filters)
q = M.quarterly_from_monthly(wf)
quarters = [x for x in q.index if x >= "2024-Q1"]

sel_q = st.select_slider("Quarter for the waterfall and top movers", options=quarters, value="2026-Q3",
                         format_func=qname)
r = q.loc[sel_q]
c = st.columns(5)
c[0].metric("MRR at quarter end", money(r["mrr_end_of_quarter"]),
            delta_pct(r["mrr_end_of_quarter"] / r["mrr_start_of_quarter"] - 1, 1, suffix=" in the quarter"), border=True)
c[1].metric("New MRR", money(r["new_mrr"]), f"{pct(r['new_rate'])} of start", delta_color="off", delta_arrow="off", border=True)
c[2].metric("Expansion MRR", money(r["expansion_mrr"]), f"{pct(r['expansion_rate'])} of start", delta_color="off",
            delta_arrow="off", border=True)
c[3].metric("Contraction MRR", money(r["contraction_mrr"]), f"{pct(r['contraction_rate'])} of start",
            delta_color="off", delta_arrow="off", border=True)
c[4].metric("Churned MRR", money(r["churn_mrr"]), f"{pct(r['churn_rate'])} of start", delta_color="off", delta_arrow="off", border=True)

title = None if not any(filters.values()) else "Monthly MRR movements — filtered customers"
plot(M.fig_monthly_movements(wf, start="2024-10-31", title=title))
plot(M.fig_quarter_waterfall(sel_q, q))
data_table(wf[wf["month_end"] >= "2024-10-31"][["month_end", "mrr_start", "new_mrr", "expansion_mrr", "reactivation_mrr",
                                                "contraction_mrr", "churn_mrr", "mrr_end", "customers_end"]],
           "Show the monthly waterfall table")

st.subheader("Why did expansion slow in Q3 2026?")
k = key_numbers("metrics")
cc = st.columns([1, 1, 2])
which = cc[0].radio("Compare Q3 2026 with", ["Q2 2026 (sequential)", "Q3 2025 (same quarter last year)"], index=0)
excl = cc[1].toggle("Remove the one-off rollout", value=True,
                    help=f"{k['one_off_company']} tripled seats in May 2026 (+{money(k['one_off_expansion_mrr'])} MRR, "
                         f"{pct(k['one_off_share_of_q2'], 0)} of Q2 expansion). Sales confirmed it is not repeatable.")
cc[2].markdown(
    f"<div class='note'>Seasonality check (n = 2): the expansion rate moved "
    f"{pts(k['seasonality_q2_q3_change_pts']['2024'])} from Q2 to Q3 in 2024 and "
    f"{pts(k['seasonality_q2_q3_change_pts']['2025'])} in 2025 — too few years to separate a summer effect from noise, "
    "which is why the year-over-year bridge is the primary view.</div>", unsafe_allow_html=True)
b_key = "qoq" if which.startswith("Q2") else "yoy"
plot(M.fig_bridge(b_key, exclude_one_offs=excl))
plot(M.fig_expansion_rate_by_bucket())
bt = M.bridge(M.Q_PREV if b_key == "qoq" else M.Q_LY, M.Q_CUR, exclude_one_offs=excl)
data_table(bt.assign(rate_from=bt["rate_from"].map(lambda v: pct(v, 2) if pd.notna(v) else ""),
                     rate_to=bt["rate_to"].map(lambda v: pct(v, 2) if pd.notna(v) else ""))
           [["step", "value", "rate_from", "rate_to"]], "Show the bridge table")

st.subheader(f"Top movers, {qname(sel_q)}")
tm = M.top_movers(sel_q, n=10)
t1, t2, t3 = st.tabs(["Expansion", "Contraction", "Churn"])
for tab, kind in zip((t1, t2, t3), ("expansion", "contraction", "churn")):
    with tab:
        st.dataframe(tm[tm["mover_type"] == kind].drop(columns="mover_type"), hide_index=True, width="stretch",
                     column_config={"mrr_start": st.column_config.NumberColumn("MRR at start", format="$%.0f"),
                                    "mrr_end": st.column_config.NumberColumn("MRR at end", format="$%.0f"),
                                    "amount": st.column_config.NumberColumn("Movement", format="$%.0f")})

how_to_read("""
**The monthly chart.** Each bar is one month. Above zero: MRR added by **new** customers (blue), **expansion** of
existing customers (green: more seats, plan upgrades) and **reactivation** (purple: past customers coming back).
Below zero: **contraction** (amber: customers paying less but staying) and **churn** (red: customers who left).
The black line is net new MRR — the sum. A healthy SaaS business grows mostly through new + expansion; when the amber
and red bars grow, the existing base is leaking.

**The quarter waterfall.** Start with MRR on 1 July, add each movement, end with MRR on 30 September. The bars float
because each starts where the last ended.

**The expansion bridge** explains the change in expansion between two quarters, step by step:
1. remove the one-off company-wide rollout (it will not repeat);
2. *base growth*: the company is bigger, so the same expansion *rate* would produce more dollars;
3. each driver's change in expansion *rate* × this quarter's starting MRR. Drivers do not overlap (every expanding
   account is counted once: Technology accounts, or non-Tech by what grew most — seats or plan), so the steps add up
   exactly to the end bar.

Read the **sequential** bridge for "what happened this quarter" and the **year-over-year** bridge for "what is
structural" (same season, no summer effect). The filters at the top change the monthly chart, the waterfall and the
top movers; the bridge always uses the whole company.
""")
