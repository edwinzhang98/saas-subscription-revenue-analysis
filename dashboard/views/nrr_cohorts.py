"""NRR / GRR Cohorts: how much revenue the existing base keeps and grows, by segment and cohort."""
import pandas as pd
import streamlit as st

from common import data_table, how_to_read, key_numbers, load, multiselect_filters, page_header, plot
from saas_rev.analysis import metrics as M
from saas_rev.theme import delta_pts, pct, pts

page_header("NRR / GRR and cohorts",
            "How much of last year's revenue do we keep and grow — overall, by segment, and by signup cohort?")

k = key_numbers("metrics")
c = st.columns(4)
c[0].metric("12-month NRR", pct(k["nrr_ttm"]), delta_pts(k["nrr_ttm"] - k["nrr_ttm_2025_09"], suffix=" vs Sep 2025"), border=True,
            help=f"Base: customers paying at 30 Sep 2025. {pct(k['nrr_ttm_2026_06'])} in June; "
                 f"{pct(k['nrr_ttm_ex_one_off'])} without the one-off rollout.")
c[1].metric("12-month GRR", pct(k["grr_ttm"]), delta_pts(k["grr_ttm"] - k["grr_ttm_2025_09"], suffix=" vs Sep 2025"), border=True)
c[2].metric("Logo retention", pct(k["logo_retention_ttm"]), f"base {k['nrr_base_customers']:,} customers",
            delta_color="off", delta_arrow="off", border=True, help="Share of the base customers still paying 12 months later.")
c[3].metric("Q3 run-rate NRR", pct(k["nrr_runrate_q3_2026"]),
            delta_pts(k["nrr_runrate_q3_2026"] - k["nrr_runrate_q3_2025"], suffix=" vs Q3 2025"), border=True,
            help="Quarterly same-customer retention, annualised. Leading but noisy.")

plot(M.fig_nrr_trend())
plot(M.fig_nrr_runrate())


@st.cache_data(show_spinner=False)
def change_interval(as_of: str, dim: str) -> pd.DataFrame:
    """95% bootstrap interval of each group's NRR change (cached: it resamples customers 1,000 times)."""
    return M.nrr_change_interval(pd.Timestamp(as_of), dim)


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
ci = change_interval(str(as_of.date()), dim)
cmp = (now.merge(prev[["group", "nrr"]].rename(columns={"nrr": "nrr_year_ago"}), on="group", how="left")
          .merge(ci[["group", "change_lo", "change_hi"]], on="group", how="left"))
cmp["nrr_change"] = cmp["nrr"] - cmp["nrr_year_ago"]
cmp["group"] = M.label_groups(cmp["group"], dim)
ci = ci.assign(group=M.label_groups(ci["group"], dim))
plot(M.fig_dumbbell(cmp, M.dumbbell_title(cmp, dims[dim].lower(), ci),
                    "Gray = the same calculation 12 months earlier, blue = selected month; groups with ≥ 30 base customers "
                    "in both windows; 'sampling noise' = 95% bootstrap interval of the change includes zero",
                    as_of.strftime("%b %Y")))
NUM = st.column_config.NumberColumn
data_table(cmp[["group", "base_customers", "base_mrr", "nrr", "grr", "logo", "nrr_year_ago", "nrr_change", "change_lo", "change_hi"]],
           "Show the table",
           column_config={"group": st.column_config.TextColumn(dims[dim]),
                          "base_customers": NUM("Base customers", format="localized"),
                          "base_mrr": NUM("Base MRR", format="dollar"),
                          "nrr": NUM("NRR", format="percent"), "grr": NUM("GRR", format="percent"),
                          "logo": NUM("Logo retention", format="percent"),
                          "nrr_year_ago": NUM("NRR 12 months earlier", format="percent"),
                          "nrr_change": NUM("Change", format="percent"),
                          "change_lo": NUM("Change, 95% low", format="percent",
                                           help="Customer-level bootstrap, 1,000 resamples of each window"),
                          "change_hi": NUM("Change, 95% high", format="percent")})

st.subheader("Cohorts")
mrr = load("fct_customer_mrr_monthly")
cc = st.columns([1, 1, 3])
metric = cc[0].radio("Metric", ["nrr", "grr", "logo"], format_func={"nrr": "NRR", "grr": "GRR", "logo": "Logo retention"}.get)
grain = cc[1].radio("Cohort grain", ["quarter", "month"], format_func=str.title)
with cc[2]:
    f = multiselect_filters(mrr, {"segment": "Segment", "acquisition_channel": "Channel"}, key="coh")
plot(M.fig_cohort_heatmap(metric, grain, f, max_k=24 if grain == "quarter" else 18))

half = ((cmp["change_hi"] - cmp["change_lo"]) / 2).median()
noise = (f"Groups here have {int(cmp['base_customers'].min()):,} to {int(cmp['base_customers'].max()):,} base customers and "
         f"the 95% interval of a group's change is typically ±{pts(half).lstrip('+')}, so the title says whether the "
         "biggest fall is clear of that noise (the table has every interval)." if pd.notna(half) else "")
ren = k.get("cohort_month12", {})
annual, monthly = k.get("cohort_month12_annual_median_step"), k.get("cohort_month12_monthly_median_step")
dip = ""
if ren.get("cohorts"):
    dip = (f"Notice month 12: {ren['cohorts_dipping']} of the {ren['cohorts']} quarterly cohorts that reached it lose NRR "
           f"there (median {pts(ren['median_step'])})")
    if annual is not None and monthly is not None:
        dip += (f", and the drop is in the customers who started on annual billing (median {pts(annual)}; monthly-billed "
                f"{pts(monthly)}). That is the first annual renewal, where annual customers right-size seats or leave.")
    else:
        dip += " — the first annual renewal, where annual customers right-size seats or leave."

how_to_read(f"""
**NRR vs GRR.** Take everyone who was paying 12 months ago. **NRR** = what they pay today ÷ what they paid then
(expansion counts, churn counts as zero). **GRR** caps each customer at what they paid then, so it only measures
what was kept. Today NRR is {pct(k['nrr_ttm'])} and GRR {pct(k['grr_ttm'])}: the base lost {pct(1 - k['grr_ttm'])} of
last year's revenue to contraction and churn and won back {pts(k['nrr_ttm'] - k['grr_ttm']).lstrip('+')} through
expansion — so expansion slowing hits NRR directly.

**Run-rate NRR** does the same over one quarter and annualises it. It reacts faster than the 12-month figure but
jumps around more — use it as an early warning, not as the board number.

**The dumbbell chart** shows each group's NRR now (blue) and at the same point a year earlier (gray). Long lines
moving left are the groups getting worse. {noise} Change the month or the grouping and everything is recalculated.

**The cohort heatmap.** Each row is the group of customers that first paid in a given quarter (or month); each
column is months since then. A cell is that cohort's revenue retention at that age. For NRR, blue = above 100% (the
cohort grew) and red = below; GRR and logo retention use one blue scale (darker = more kept). Blank cells are not
observed yet for the whole cohort. Read across a row to see how one cohort ages; read down a column to compare
cohorts at the same age. {dip}
""")
