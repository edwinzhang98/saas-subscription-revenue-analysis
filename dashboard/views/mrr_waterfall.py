"""MRR Waterfall: where MRR came from and went, month by month, and why expansion slowed."""
import pandas as pd
import streamlit as st

from common import data_table, how_to_read, key_numbers, load, md_escape, multiselect_filters, page_header, plot
from saas_rev.analysis import metrics as M
from saas_rev.theme import delta_pct, money, pct, pts, qname

page_header("MRR waterfall",
            "Month by month, how much MRR came from new customers, expansion and win-backs, and how much was lost "
            "to contraction and churn — and why did expansion slow in Q3 2026?")

FILTERS = {"segment": "Segment", "industry": "Industry", "acquisition_channel": "Acquisition channel"}
mv_all = load("fct_mrr_movements")
filters = multiselect_filters(mv_all, FILTERS, key="wf")
active = {col: vals for col, vals in filters.items() if vals}
scope = ("filtered: " + "; ".join(f"{FILTERS[col]} = {', '.join(vals)}" for col, vals in active.items())) if active else None
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

plot(M.fig_monthly_movements(wf, start="2024-10-31", note=scope))
plot(M.fig_quarter_waterfall(sel_q, q))
NUM = st.column_config.NumberColumn
wcols = {"month_end": "Month", "mrr_start": "MRR at start", "new_mrr": "New", "expansion_mrr": "Expansion",
         "reactivation_mrr": "Reactivation", "contraction_mrr": "Contraction", "churn_mrr": "Churn", "mrr_end": "MRR at end",
         "customers_end": "Paying customers"}
data_table(wf[wf["month_end"] >= "2024-10-31"][list(wcols)].rename(columns=wcols), "Show the monthly waterfall table",
           column_config={"Month": st.column_config.DateColumn(format="MMM YYYY"),
                          **{v: NUM(format="dollar") for v in list(wcols.values())[1:-1]},
                          "Paying customers": NUM(format="localized")})

st.subheader("Why did expansion slow in Q3 2026?")
k = key_numbers("metrics")
cc = st.columns([1, 1, 2])
which = cc[0].radio("Compare Q3 2026 with", ["Q2 2026 (sequential)", "Q3 2025 (same quarter last year)"], index=1)
one_month = pd.Timestamp(k["one_off_month"] + "-01").strftime("%b %Y") if k.get("one_off_month") else "Q2 2026"
excl = cc[1].toggle("Remove the one-off rollout", value=True, help=md_escape(
    f"{k['one_off_company']} went from {k.get('one_off_seats_before', 0):,} to {k.get('one_off_seats_after', 0):,} seats in "
    f"{one_month} (+{money(k['one_off_expansion_mrr'])} MRR, {pct(k['one_off_share_of_q2'], 0)} of Q2 expansion). Sales "
    "confirmed it is not repeatable. On: the bridge removes it as its own step and the driver chart shows it hatched, "
    "apart from the drivers."))
cc[2].markdown(
    f"<div class='note'>Seasonality check (n = 2): the expansion rate moved "
    f"{pts(k['seasonality_q2_q3_change_pts']['2024'])} from Q2 to Q3 in 2024 and "
    f"{pts(k['seasonality_q2_q3_change_pts']['2025'])} in 2025 — too few years to separate a summer effect from noise, "
    "which is why the year-over-year bridge is the primary view.</div>", unsafe_allow_html=True)
b_key = "qoq" if which.startswith("Q2") else "yoy"
plot(M.fig_bridge(b_key, exclude_one_offs=excl))
plot(M.fig_expansion_rate_by_bucket(exclude_one_offs=excl))
bt = M.bridge(M.Q_PREV if b_key == "qoq" else M.Q_LY, M.Q_CUR, exclude_one_offs=excl)
data_table(bt[["step", "value", "rate_from", "rate_to"]], "Show the bridge table",
           column_config={"step": st.column_config.TextColumn("Step", width="large"),
                          "value": NUM("Expansion MRR", format="accounting"),
                          "rate_from": NUM("Driver rate, earlier quarter", format="percent",
                                             help="Driver's expansion ÷ company starting MRR (one-off removed when the toggle is on)"),
                          "rate_to": NUM("Driver rate, Q3 2026", format="percent")})

st.subheader(f"Top movers, {qname(sel_q)}" + (" — filtered customers" if active else ""))
tm = M.top_movers(sel_q, n=10, filters=filters)
tm["csm_owner"] = tm["csm_owner"].fillna("—")
base_cfg = {"customer_id": st.column_config.TextColumn("Customer"), "company_name": st.column_config.TextColumn("Company"),
            "segment": st.column_config.TextColumn("Segment"), "industry": st.column_config.TextColumn("Industry"),
            "csm_owner": st.column_config.TextColumn("CSM owner"),
            "mrr_start": NUM("MRR before", format="dollar", help="MRR just before the account's first movement of this kind in the quarter"),
            "mrr_end": NUM("MRR at quarter end", format="dollar")}
t1, t2, t3 = st.tabs(["Expansion", "Contraction", "Churn"])
for tab, kind, label in zip((t1, t2, t3), ("expansion", "contraction", "churn"), ("Expansion", "Contraction", "Churned MRR")):
    with tab:
        st.dataframe(tm[tm["mover_type"] == kind].drop(columns="mover_type"), hide_index=True, width="stretch",
                     column_config={**base_cfg, "amount": NUM(label, format="dollar")})

how_to_read("""
**The monthly chart.** Each bar is one month. Above zero: MRR added by **new** customers (blue), **expansion** of
existing customers (green: more seats, plan upgrades) and **reactivation** (purple: past customers coming back).
Below zero: **contraction** (amber: customers paying less but staying) and **churn** (red: customers who left).
The black line is net new MRR — the sum. A healthy SaaS business grows mostly through new + expansion; when the amber
and red bars grow, the existing base is leaking.

**The quarter waterfall.** Start with MRR on the first day of the quarter, add each movement, end with MRR on the
last day. The bars float because each starts where the last ended.

**The expansion bridge** explains the change in expansion between two quarters, step by step:
1. remove the one-off company-wide rollout (it will not repeat);
2. *base growth*: the company is bigger, so the same expansion *rate* would produce more dollars;
3. each driver's change in expansion *rate* × this quarter's starting MRR. Drivers do not overlap (every expanding
   account is counted once: Technology accounts, or non-Tech by what grew most — seats or plan), so the steps add up
   exactly to the end bar. Blue steps add expansion, red steps take it away.

**The driver chart** below the bridge shows the same drivers quarter by quarter, as a share of the company's starting
MRR (orange = Technology accounts). With the one-off toggle on, the rollout is the hatched block, so each bar's label is
the like-for-like rate.

Read the **sequential** bridge for "what happened this quarter" and the **year-over-year** bridge for "what is
structural" (same season, no summer effect). The filters at the top change the monthly chart, the waterfall, the
tiles and the top movers; the bridge and the driver chart always use the whole company.
""")
