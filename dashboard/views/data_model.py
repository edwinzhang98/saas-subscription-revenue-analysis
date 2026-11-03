"""Data Model: what the synthetic data is, how it is generated and checked."""
import pandas as pd
import streamlit as st

from common import data_table, how_to_read, key_numbers, load, md_escape, page_header
from saas_rev.theme import money, pct

page_header("Data model and synthetic data",
            "What data is this analysis built on, how was it generated, and can its numbers be trusted?")

st.info("**This dataset is synthetic.** Public SaaS billing data with plan changes, seat changes and product usage "
        "does not exist, so the five source tables a billing system and a product-analytics export would produce were "
        "designed first and then generated from documented business rules (docs/data_model.md). Some business events "
        "were built in on purpose; the table at the bottom compares what was planted with what the analysis recovered.",
        icon=":material/science:")

ER = """
digraph ER {
  graph [rankdir=LR, bgcolor="transparent", fontname="Helvetica", nodesep=0.4, ranksep=0.9];
  node  [shape=plain, fontname="Helvetica", fontsize=11];
  edge  [color="#898781", arrowsize=0.6, fontname="Helvetica", fontsize=10, fontcolor="#52514e"];
  customers [label=<<table border="0" cellborder="1" cellspacing="0" cellpadding="4" color="#c3c2b7">
    <tr><td bgcolor="#2a78d6"><font color="white"><b>customers</b></font></td></tr>
    <tr><td align="left">customer_id (PK)</td></tr><tr><td align="left">segment · industry · region</td></tr>
    <tr><td align="left">acquisition_channel</td></tr><tr><td align="left">cs_coverage · csm_owner</td></tr></table>>];
  plans [label=<<table border="0" cellborder="1" cellspacing="0" cellpadding="4" color="#c3c2b7">
    <tr><td bgcolor="#52514e"><font color="white"><b>plans</b></font></td></tr>
    <tr><td align="left">plan_id (PK) · tier_rank</td></tr><tr><td align="left">monthly / annual price per seat</td></tr></table>>];
  subscriptions [label=<<table border="0" cellborder="1" cellspacing="0" cellpadding="4" color="#c3c2b7">
    <tr><td bgcolor="#2a78d6"><font color="white"><b>subscriptions</b></font></td></tr>
    <tr><td align="left">subscription_id (PK) · customer_id (FK)</td></tr>
    <tr><td align="left">start_date · end_date (exclusive)</td></tr>
    <tr><td align="left">initial plan / seats / billing / price / discount</td></tr>
    <tr><td align="left">status · cancel_reason</td></tr></table>>];
  plan_changes [label=<<table border="0" cellborder="1" cellspacing="0" cellpadding="4" color="#c3c2b7">
    <tr><td bgcolor="#2a78d6"><font color="white"><b>plan_changes</b></font></td></tr>
    <tr><td align="left">change_id (PK) · subscription_id (FK)</td></tr>
    <tr><td align="left">requested_date · effective_date</td></tr>
    <tr><td align="left">from_* / to_* plan, seats, billing, price, discount</td></tr>
    <tr><td align="left">change_type · change_source</td></tr></table>>];
  invoices [label=<<table border="0" cellborder="1" cellspacing="0" cellpadding="4" color="#c3c2b7">
    <tr><td bgcolor="#2a78d6"><font color="white"><b>invoices</b></font></td></tr>
    <tr><td align="left">invoice_line_id (PK) · subscription_id (FK)</td></tr>
    <tr><td align="left">line_type: recurring / proration / one_time</td></tr>
    <tr><td align="left">service period · amount · status</td></tr></table>>];
  usage_events [label=<<table border="0" cellborder="1" cellspacing="0" cellpadding="4" color="#c3c2b7">
    <tr><td bgcolor="#1baf7a"><font color="white"><b>usage_events</b></font></td></tr>
    <tr><td align="left">customer_id · week_start · event_name</td></tr>
    <tr><td align="left">event_count · unique_users</td></tr></table>>];
  customers -> subscriptions [label="1 : n"];
  plans -> subscriptions [label="initial plan"];
  subscriptions -> plan_changes [label="1 : n"];
  subscriptions -> invoices [label="1 : n"];
  customers -> usage_events [label="weekly"];
}
"""
ROW_PX = 35                                     # st.dataframe row height (header row included)


def fit(n_rows: int, cap: int | None = None) -> int:
    """Height that shows every row (no empty grid below a 2-row table); `cap` rows at most, then it scrolls."""
    return ROW_PX * (min(n_rows, cap or n_rows) + 1) + 3


st.subheader("Source tables")
st.graphviz_chart(ER, width="stretch")

rc = load("datamodel__row_counts")
run = load("meta_run").iloc[0]
months = load("dim_month")
plans = load("stg_plans").sort_values("tier_rank")
tiers = [f"{p.plan_name} ${p.monthly_price_per_seat:,.0f} per seat-month" if pd.notna(p.monthly_price_per_seat)
         else f"{p.plan_name} ${p.annual_price_per_seat:,.0f} per seat-year" for p in plans.itertuples()]
left, right = st.columns([2, 3])
with left:
    st.subheader("Volumes")
    st.dataframe(rc.rename(columns={"table": "Table", "rows": "Rows"}), hide_index=True, width="stretch",
                 height=fit(len(rc)), column_config={"Rows": st.column_config.NumberColumn(format="localized")})
with right:
    st.subheader("Scope")
    cutoff = pd.Timestamp(run["as_of_date"])
    st.markdown(md_escape(f"""
- {len(months)} months: {pd.Timestamp(run['window_start']):%b %Y} (general availability) → data cutoff {cutoff.day} {cutoff:%b %Y}
- seat-based pricing, {len(plans)} tiers at list price ({'; '.join(tiers)}); discounts negotiated per deal
- monthly and annual billing, prorated increases, decreases at period end, card dunning, win-backs
- MRR is never stored: it is derived in SQL from the terms on each subscription
"""))

st.subheader("Look inside a table")
tbl = st.selectbox("Table", ["subscriptions", "plan_changes", "invoices", "usage_events", "customers", "plans"],
                   index=0, help="Sample rows for the one-off rollout account and one ordinary account")
cols = load("datamodel__columns")
cols = cols[cols["table"] == tbl][["column_name", "column_type"]]
sample = load(f"datamodel__sample_{tbl}")
c1, c2 = st.columns([1, 3])
with c1:
    st.dataframe(cols, hide_index=True, width="stretch", height=fit(len(cols), cap=12),
                 column_config={"column_name": st.column_config.TextColumn("Column"),
                                "column_type": st.column_config.TextColumn("Type")})
with c2:
    st.dataframe(sample, hide_index=True, width="stretch", height=fit(len(sample)))
    st.caption(f"{len(sample)} raw rows as delivered (all values as text); None = null in the source.")

st.subheader("How the data is generated")
st.markdown("""
Each account is simulated **week by week** with its own random stream (fixed seed, so every run is identical):
a hidden *health* score drifts and takes shocks (champion leaves, new team adopts); *team size* grows or shrinks with
hiring and adoption; **seats follow team size** (bought when the team outgrows them, cut at billing dates when there
is surplus); usage (weekly active users, feature events, paywall hits) comes from health and seats; upgrades,
downgrades, billing switches and cancellations are probability rules that depend on health, plan, segment and channel.
All rules and parameters: `docs/data_model.md` and `data_gen/params.yaml`.
""")


def check_value(name: str, v) -> str:
    """Generator checks are stored as text; show each in its own unit (count, $, share, relative change, pts)."""
    try:
        x = float(v)
    except (TypeError, ValueError):
        return str(v)
    if "($)" in name:
        return money(x)
    if "(pts)" in name:
        return f"{x:+.2f} pts".replace("-", "−")
    if name.startswith(("Accounts", "Paying accounts")):
        return f"{x:,.0f}"
    if " vs " in name or "relative" in name:
        return pct(x, 1, signed=True)
    return pct(x, 2 if abs(x) < 0.1 else 1)


gc = load("datamodel__generator_checks")
st.markdown(f"**Generator sanity checks:** {int(gc['pass'].astype(str).eq('True').sum())} of {len(gc)} design targets met.")
gct = pd.DataFrame({"Check": gc["check"],
                    **{lab: [check_value(n, v) for n, v in zip(gc["check"], gc[col])]
                       for col, lab in (("value", "Value"), ("min", "Target min"), ("max", "Target max"))},
                    "Met": gc["pass"].astype(str).eq("True")})
data_table(gct, "Show the generator checks", column_config={"Check": st.column_config.TextColumn(width="large"),
                                                            "Met": st.column_config.CheckboxColumn()})


def observed(name: str, x: float) -> str:
    if pd.isna(x):
        return ""
    if "($)" in name:
        return money(x)
    if "(%)" in name:                      # stored in percent units
        return f"{x:.2f}%".replace("-", "−")
    return f"{x:,.0f}"


dq = load("dq_check_results")
n_pass, n_fail, n_info = (dq["status"] == "pass").sum(), (dq["status"] == "fail").sum(), (dq["status"] == "info").sum()
st.markdown(f"**Data-quality checks on the warehouse:** {n_pass} passed, {n_fail} failed, {n_info} monitored "
            "(row-count reconciliation, primary keys, nulls, referential integrity, value ranges, time ranges, "
            "metric identities, billing reconciliation).")
dqt = dq.assign(observed=[observed(n, v) for n, v in zip(dq["check_name"], dq["observed"])],
                note=dq["note"].fillna(""))
data_table(dqt[["check_id", "category", "check_name", "observed", "expected", "status", "note"]],
           "Show all data-quality checks",
           column_config={"check_id": "ID", "category": "Category",
                          "check_name": st.column_config.TextColumn("Check", width="large"),
                          "observed": "Observed", "expected": "Expected", "status": "Status",
                          "note": st.column_config.TextColumn("Note", width="medium")})

st.subheader("Planted effects vs what the analysis recovered")
st.caption("The generator's answer key is used only to validate the method — the analysis never reads it.")
ak = key_numbers("answer_key")
if ak and ak.get("rows"):
    akt = pd.DataFrame(ak["rows"]).rename(columns={"event": "Planted event", "planted": "What was planted",
                                                   "recovered": "What the analysis recovered",
                                                   "naive_cut": "What a naive cut would say", "verdict": "Verdict"})
    st.table(akt, hide_index=True)          # static table: long text wraps instead of being cut at the column edge
else:
    st.write("Answer-key comparison is produced by the final pipeline step (`python run.py validate`).")

how_to_read("""
**Why synthetic data.** The questions here — why expansion slowed, who is quietly downgrading — need plan-change and
seat-change history plus product usage per account. No public dataset has that, so the data model a SaaS billing
system would produce was designed first, and the data generated from written rules.

**How to read the ER diagram.** Each box is a table; arrows show "one to many" (one customer has many subscriptions
over time; one subscription has many plan changes and invoices). MRR is not a column anywhere: it is computed from
the subscription's terms on a given day, which is how a RevOps team would build it from Stripe / Chargebee exports.

**Why the checks matter.** The generator checks confirm the data matches its own design (scale, churn, plan mix).
The data-quality checks are what you would run on real data: do row counts reconcile from raw to clean tables, are
keys unique, do the MRR movements add up exactly, does billing agree with MRR. "Monitored" rows are known issues
that were found and handled (test accounts, duplicate usage loads, orphan plan changes).

**The answer key.** Because some effects were built in, we can check the method recovers them — and see where a naive
cut would have been wrong. That is a test of the method, not a "discovery".
""")
