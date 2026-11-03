"""NRR / GRR Cohorts: how much revenue the existing base keeps and grows, by segment and cohort."""
import pandas as pd
import streamlit as st

from common import data_table, how_to_read, key_numbers, load, multiselect_filters, page_header, plot
from saas_rev.analysis import metrics as M
from saas_rev.theme import delta_pct, delta_pts, pct, pts

page_header("NRR / GRR and cohorts",
            "How much of last year's revenue do we keep and grow — overall, by segment, and by signup cohort?")

k = key_numbers("metrics")
c = st.columns(4)
c[0].metric("12-month NRR", pct(k["nrr_ttm"]), delta_pts(k["nrr_ttm"] - k["nrr_ttm_2025_09"], suffix=" vs Sep 2025"), border=True)
c[1].metric("12-month GRR", pct(k["grr_ttm"]), delta_pts(k["grr_ttm"] - k["grr_ttm_2025_09"], suffix=" vs Sep 2025"), border=True)
c[2].metric("Logo retention", pct(k["logo_retention_ttm"]), f"base {k['nrr_base_customers']:,} customers",
            delta_color="off", delta_arrow="off", border=True)
c[3].metric("Q3 run-rate NRR", pct(k["nrr_runrate_q3_2026"]),
            delta_pts(k["nrr_runrate_q3_2026"] - k["nrr_runrate_q3_2025"], suffix=" vs Q3 2025"), border=True,
            help="Quarterly same-customer retention, annualised. Leading but noisy.")

plot(M.fig_nrr_trend())
plot(M.fig_nrr_runrate())

st.subheader("Which customers retain best?")
nrr = load("rpt_nrr_grr_ttm")
months = nrr[nrr["is_reportable"]]["month_end"].dt.date.tolist()
cc = st.columns([2, 2, 3])
as_of = cc[0].select_slider("As of month-end", options=months, value=months[-1],
                            format_func=lambda d: pd.Timestamp(d).strftime("%b %Y"))
dims = {"industry": "Industry", "segment": "Segment", "acquisition_channel": "Acquisition channel",
        "plan_id": "Plan at start of window", "billing_period": "Billing period at start", "region": "Region"}
dim = cc[1].selectbox("Group by", list(dims), format_func=dims.get)
as_of = pd.Timestamp(as_of)
now = M.ttm_retention(as_of, dim)
prev = M.ttm_retention(as_of - pd.offsets.MonthEnd(12), dim)
cmp = now.merge(prev[["group", "nrr"]].rename(columns={"nrr": "nrr_year_ago"}), on="group", how="left")
worst = cmp.sort_values("nrr").iloc[0]
plot(M.fig_dumbbell(cmp, f"12-month NRR by {dims[dim].lower()}: lowest is {worst['group']} at {pct(worst['nrr'], 0)}",
                    "Gray = the same calculation 12 months earlier, blue = selected month; groups with ≥ 30 base customers",
                    as_of.strftime("%b %Y")))
data_table(cmp[["group", "base_customers", "base_mrr", "nrr", "grr", "logo", "nrr_year_ago"]], "Show the table")

st.subheader("Cohorts")
mrr = load("fct_customer_mrr_monthly")
cc = st.columns([1, 1, 3])
metric = cc[0].radio("Metric", ["nrr", "grr", "logo"], format_func={"nrr": "NRR", "grr": "GRR", "logo": "Logo retention"}.get)
grain = cc[1].radio("Cohort grain", ["quarter", "month"], format_func=str.title)
with cc[2]:
    f = multiselect_filters(mrr, {"segment": "Segment", "acquisition_channel": "Channel"}, key="coh")
plot(M.fig_cohort_heatmap(metric, grain, f, max_k=24 if grain == "quarter" else 18))

how_to_read("""
**NRR vs GRR.** Take everyone who was paying 12 months ago. **NRR** = what they pay today ÷ what they paid then
(expansion counts, churn counts as zero). **GRR** caps each customer at what they paid then, so it only measures
what was kept. A business with NRR 104% and GRR 85% loses 15% of its base revenue a year and wins back 19% through
expansion — so expansion slowing hits NRR directly.

**Run-rate NRR** does the same over one quarter and annualises it. It reacts faster than the 12-month figure but
jumps around more — use it as an early warning, not as the board number.

**The dumbbell chart** shows each group's NRR now (blue) and at the same point a year earlier (gray). Long lines
moving left are the groups getting worse. Change the month or the grouping and everything is recalculated.

**The cohort heatmap.** Each row is the group of customers that first paid in a given quarter (or month); each
column is months since then. A cell is that cohort's revenue retention at that age. Blue = above 100% (the cohort
grew), red = below. Blank cells are not observed yet for the whole cohort. Read across a row to see how one cohort
ages; read down a column to compare cohorts at the same age (are newer cohorts worse?). Notice the dip at month 12:
that is the first annual renewal, where customers right-size seats or leave.
""")
