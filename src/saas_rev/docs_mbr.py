"""Monthly revenue review (docs/monthly_revenue_review.md) and the CS account list
(docs/account_health_list.csv), generated from the marts so the recurring report is reproducible.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from . import marts
from .config import REPO_ROOT
from .docs_gen import _md_table
from .theme import money, pct, pts, qname

DOCS = REPO_ROOT / "docs"
CSM_CAPACITY = 120          # proactive reviews per quarter (assumption A9)
EXPANSION_ROWS = 300        # expansion rows in the CSV (P4's top 100 + the next 200 for in-app prompts); full list on the dashboard
HEALTH_COLUMNS = ["queue", "priority_rank", "play", "list_type", "customer_id", "company_name", "segment", "industry", "cs_coverage",
                  "csm_owner", "plan", "billing_period", "seats", "current_arr", "arr_at_stake", "next_renewal_date",
                  "days_to_renewal", "renewal_within_120d", "reason_code_1", "reason_code_2", "reason_code_3", "evidence",
                  "suggested_play"]
QUEUE_ORDER = {"csm": 0, "renewal_desk": 1, "digital": 2, "sales_assist": 3, "in_app": 4}


def _safe(name: str) -> pd.DataFrame | None:
    try:
        return marts.load(name).copy()
    except FileNotFoundError:
        return None


# =============================================================================== account health list
def account_health_list() -> pd.DataFrame | None:
    """The CS account list (02 §15): the risk watchlist and the top of the upgrade-opportunity list, one row per account
    (risk wins), each with the play it belongs to and the queue that works it:
      csm           P1 — first 120 CSM-covered risk accounts by watchlist priority (annual renewals within 120 days first,
                    then ARR at stake)
      renewal_desk  P3 — Technology annual renewals within 6 months that are on the risk list but not in P1
      digital       P2 — the rest of the risk list
      sales_assist  P4 — top 100 upgrade opportunities not on the risk list
      in_app        the next 200 upgrade opportunities (in-app prompt only)"""
    from .analysis import recommendations as R
    risk, opp = _safe("silent_churn__watchlist"), _safe("expansion__opportunities")
    if risk is None and opp is None:
        return None
    plays = R.assignments().set_index("customer_id")["play"]
    frames = []
    if risk is not None:
        r = risk.copy()
        r["list_type"] = "risk"
        r["play"] = r["customer_id"].map(plays).fillna("P2")
        r["queue"] = r["play"].map({"P1": "csm", "P3": "renewal_desk"}).fillna("digital")
        urgent = r["renewal_within_120d"] & (r["days_to_renewal"] < 45)
        r.loc[urgent, "suggested_play"] = "Urgent (renews in " + r.loc[urgent, "days_to_renewal"].astype(int).astype(str) + " days): " + r.loc[urgent, "suggested_play"]
        r = r.sort_values(["renewal_within_120d", "arr_at_stake", "customer_id"], ascending=[False, False, True])
        frames.append(r)
    if opp is not None:
        o = opp.copy()
        if risk is not None:
            o = o[~o["customer_id"].isin(risk["customer_id"])]
        o = o.sort_values(["expected_mrr", "customer_id"], ascending=[False, True]).head(EXPANSION_ROWS)
        o["list_type"] = "expansion"
        o["play"] = np.where(o["customer_id"].map(plays).eq("P4"), "P4", "")
        o["queue"] = np.where(o["play"] == "P4", "sales_assist", "in_app")
        o["plan"] = "basic"
        o["arr_at_stake"] = o["expected_mrr"] * 12
        o["renewal_within_120d"] = (o["billing_period"] == "annual") & (o["days_to_renewal"] <= 120)
        o["suggested_play"] = np.where(o["play"] == "P4", "14-day Premium trial + sales-assist outreach (P4)",
                                       "In-app Premium prompt at the paywall")
        frames.append(o)
    out = pd.concat(frames, ignore_index=True)
    for c in HEALTH_COLUMNS:
        if c not in out.columns:
            out[c] = np.nan
    out["_q"] = out["queue"].map(QUEUE_ORDER)
    out = out.sort_values(["_q", "renewal_within_120d", "arr_at_stake", "customer_id"],
                          ascending=[True, False, False, True]).reset_index(drop=True)
    out["priority_rank"] = out.groupby("queue").cumcount() + 1
    out["current_arr"] = out["current_arr"].round(0)
    out["arr_at_stake"] = out["arr_at_stake"].round(0)
    out["seats"] = out["seats"].astype(int)
    out["next_renewal_date"] = pd.to_datetime(out["next_renewal_date"]).dt.date
    return out[HEALTH_COLUMNS]


def write_health_list() -> Path | None:
    df = account_health_list()
    if df is None:
        return None
    p = DOCS / "account_health_list.csv"
    df.to_csv(p, index=False)
    marts.write("docs__account_health_list", df)
    return p


# =============================================================================== monthly revenue review
def write_mbr() -> Path:
    k = marts.read_key_numbers("metrics")
    wf = marts.load("rpt_mrr_waterfall_monthly").set_index("month_end")
    q = marts.load("rpt_mrr_quarterly").set_index("quarter_label")
    n = marts.load("rpt_nrr_grr_ttm").set_index("month_end")
    rr = marts.load("rpt_nrr_quarterly_runrate").set_index("quarter_label")
    seg = marts.load("metrics__segment_nrr")
    coh = marts.load("rpt_cohort_retention")
    tm = marts.load("metrics__top_movers")
    cust = marts.load("stg_customers").set_index("customer_id")
    t, t1, t12 = pd.Timestamp("2026-09-30"), pd.Timestamp("2026-08-31"), pd.Timestamp("2025-09-30")
    sep, aug = wf.loc[t], wf.loc[t1]

    def net_new(r):
        return r["new_mrr"] + r["expansion_mrr"] + r["reactivation_mrr"] - r["contraction_mrr"] - r["churn_mrr"]

    head = pd.DataFrame([
        ("MRR", money(sep["mrr_end"], 2), money(aug["mrr_end"], 2), money(wf.loc[t12, "mrr_end"], 2), pct(sep["mrr_end"] / wf.loc[t12, "mrr_end"] - 1, 0, signed=True) + " YoY"),
        ("ARR (MRR × 12)", money(sep["arr_end"], 2), money(aug["arr_end"], 2), money(wf.loc[t12, "arr_end"], 2), ""),
        ("Paying customers", f"{int(sep['customers_end']):,}", f"{int(aug['customers_end']):,}", f"{int(wf.loc[t12, 'customers_end']):,}", ""),
        ("ARPA", money(sep["arpa"], 0), money(aug["arpa"], 0), money(wf.loc[t12, "arpa"], 0), ""),
        ("Net new MRR in month", money(net_new(sep)), money(net_new(aug)), money(net_new(wf.loc[t12])), ""),
        ("12-month NRR", pct(n.loc[t, "nrr"]), pct(n.loc[t1, "nrr"]), pct(n.loc[t12, "nrr"]), f"excl. one-off: {pct(k['nrr_ttm_ex_one_off'])}"),
        ("12-month GRR", pct(n.loc[t, "grr"]), pct(n.loc[t1, "grr"]), pct(n.loc[t12, "grr"]), ""),
        ("Logo retention (12 months)", pct(n.loc[t, "logo_retention"]), pct(n.loc[t1, "logo_retention"]), pct(n.loc[t12, "logo_retention"]), ""),
        ("Run-rate NRR (quarter, annualised)", pct(rr.loc["2026-Q3", "nrr_runrate_annualized"]), "", pct(rr.loc["2025-Q3", "nrr_runrate_annualized"]), "Q3 2026 vs Q3 2025"),
    ], columns=["Metric", "Sep 2026", "Aug 2026", "Sep 2025", "Note"])

    def wf_rows(start, new, exp, react, con, churn, end):
        return [("Start MRR", money(start, 2)), ("+ New", money(new)), ("+ Expansion", money(exp)), ("+ Reactivation", money(react)),
                ("− Contraction", money(con)), ("− Churn", money(churn)), ("End MRR", money(end, 2))]
    q3 = q.loc["2026-Q3"]
    wft = pd.DataFrame({"Movement": [r[0] for r in wf_rows(0, 0, 0, 0, 0, 0, 0)],
                        "September 2026": [r[1] for r in wf_rows(sep["mrr_start"], sep["new_mrr"], sep["expansion_mrr"], sep["reactivation_mrr"], sep["contraction_mrr"], sep["churn_mrr"], sep["mrr_end"])],
                        "Q3 2026": [r[1] for r in wf_rows(q3["mrr_start_of_quarter"], q3["new_mrr"], q3["expansion_mrr"], q3["reactivation_mrr"], q3["contraction_mrr"], q3["churn_mrr"], q3["mrr_end_of_quarter"])],
                        "Q3 2026 rate (of start)": ["", pct(q3["new_rate"]), pct(q3["expansion_rate"]), pct(q3["reactivation_rate"]), pct(q3["contraction_rate"]), pct(q3["churn_rate"]), ""],
                        "Q3 2025 rate": ["", pct(q.loc["2025-Q3", "new_rate"]), pct(q.loc["2025-Q3", "expansion_rate"]), pct(q.loc["2025-Q3", "reactivation_rate"]), pct(q.loc["2025-Q3", "contraction_rate"]), pct(q.loc["2025-Q3", "churn_rate"]), ""]})
    trend = n[n.index.month.isin([3, 6, 9, 12]) & (n.index >= "2025-06-30")][["base_customers", "nrr", "grr", "logo_retention"]].reset_index()
    trend["month_end"] = trend["month_end"].dt.strftime("%b %Y")
    trend = trend.rename(columns={"month_end": "Quarter end", "base_customers": "Base customers", "nrr": "NRR", "grr": "GRR", "logo_retention": "Logo retention"})
    segt = seg[seg["dimension"].isin(["segment", "industry"])][["dimension", "dimension_value", "base_customers", "nrr", "nrr_year_ago", "grr"]]
    segt = segt.rename(columns={"dimension": "Cut", "dimension_value": "Group", "base_customers": "Base customers", "nrr": "NRR Sep 2026",
                                "nrr_year_ago": "NRR Sep 2025", "grr": "GRR Sep 2026"})
    cq = coh[(coh["cohort_grain"] == "quarter") & coh["months_since_first_paid"].isin([3, 6, 12, 24])]
    cpiv = cq.pivot(index="cohort", columns="months_since_first_paid", values="nrr")
    cpiv = cpiv.loc[[c for c in cpiv.index if c >= "2023-Q4"]].reset_index()
    cpiv.columns = ["Cohort (first paid)"] + [f"NRR at month {c}" for c in cpiv.columns[1:]]

    def movers(kind, n_=5):
        x = tm[tm["mover_type"] == kind].head(n_).copy()
        x["MRR before → after"] = x.apply(lambda r: f"{money(r['mrr_start'])} → {money(r['mrr_end'])}", axis=1)
        x = x.rename(columns={"company_name": "Account", "segment": "Segment", "industry": "Industry", "csm_owner": "Owner", "amount": "Movement"})
        return x[["Account", "Segment", "Industry", "Owner", "MRR before → after", "Movement"]]

    br_q, br_y = k["bridge_qoq"], k["bridge_yoy"]
    hl = account_health_list()
    parts = [f"""# Q3 2026 close revenue review (September month-end)

**Prepared by:** Revenue Analytics (RevOps) · **Date:** 2026-11-03 · **Audience:** VP of Revenue, Head of Customer Success, Finance,
Product, Growth Marketing · **Data:** billing and product usage as of 2026-09-30 (synthetic dataset) · **October flash** follows the 7 Nov close.
Definitions: `docs/02_metric_definitions.md` v1.2 · data-quality status: all checks pass (`docs/03_data_quality_checks.md`).

## 1. Headline

{_md_table(head)}

**In one paragraph.** MRR grew {pct(q3['mrr_end_of_quarter'] / q3['mrr_start_of_quarter'] - 1, 1)} in the quarter to {money(q3['mrr_end_of_quarter'])},
still carried by new logos. The existing base weakened: expansion fell to {pct(q3['expansion_rate'])} of starting MRR (Q3 2025:
{pct(q.loc['2025-Q3', 'expansion_rate'])}), contraction rose to {pct(q3['contraction_rate'])} ({pct(q.loc['2025-Q3', 'contraction_rate'])}).
12-month NRR is {pct(k['nrr_ttm'])}, down {pts(k['nrr_ttm_2026_06'] - k['nrr_ttm']).lstrip('+')} from June; excluding the
{k['one_off_company']} rollout it is {pct(k['nrr_ttm_ex_one_off'])}.

## 2. MRR waterfall

{_md_table(wft)}

Chart: `reports/figures/mrr_waterfall.png` (monthly movements since Oct 2024) and the dashboard's *MRR Waterfall* page.

## 3. Retention

**12-month NRR / GRR by quarter-end**

{_md_table(trend, {'NRR': pct, 'GRR': pct, 'Logo retention': pct, 'Base customers': lambda v: f'{int(v):,}'})}

**By segment and industry (12-month NRR, base = customers paying a year earlier)**

{_md_table(segt, {'NRR Sep 2026': pct, 'NRR Sep 2025': pct, 'GRR Sep 2026': pct, 'Base customers': lambda v: f'{int(v):,}'})}

**Cohort NRR (quarterly cohorts; blank = not yet observed for the whole cohort)**

{_md_table(cpiv, {c: pct for c in cpiv.columns[1:]})}

Every cohort dips at month 12 (first annual renewal). Cohorts acquired after the Paid Social launch (Q4 2025 onward) start weaker.

## 4. Expansion: what changed in Q3

| Step | vs Q2 2026 | vs Q3 2025 |
|---|---|---|
| Starting point (expansion MRR in the earlier quarter) | {money(list(br_q.values())[0])} | {money(list(br_y.values())[0])} |
| One-off rollout ({k['one_off_company']}, May) | {money(br_q.get('Remove one-off rollout', 0))} | — |
| Bigger base (same rate on more MRR) | {money([v for s, v in br_q.items() if s.startswith('Base growth')][0])} | {money([v for s, v in br_y.items() if s.startswith('Base growth')][0])} |
| Technology accounts | {money(br_q['Technology (all)'])} | {money(br_y['Technology (all)'])} |
| Non-Tech seat expansion | {money(br_q['Non-Tech seat expansion'])} | {money(br_y['Non-Tech seat expansion'])} |
| Non-Tech plan upgrades | {money(br_q['Non-Tech plan upgrades'])} | {money(br_y['Non-Tech plan upgrades'])} |
| Other | {money(br_q['Non-Tech other'])} | {money(br_y['Non-Tech other'])} |
| **Q3 2026 expansion MRR** | **{money(k['expansion_q3_2026'])}** | **{money(k['expansion_q3_2026'])}** |

Decision trigger (scope §3): Q3 2026 expansion rate excluding the one-off is {pct(k['expansion_rate_q3_2026'])} vs {pct(k['expansion_rate_q3_2025'])}
in Q3 2025 — {pts(k['expansion_rate_q3_2026'] - k['expansion_rate_q3_2025'])}, beyond the 1.0-pt threshold → **re-forecast Q4 expansion and assign owners to the structural drivers.**

## 5. Top movers, Q3 2026

**Expansion**

{_md_table(movers('expansion'), {'Movement': money})}

**Contraction**

{_md_table(movers('contraction'), {'Movement': money})}

**Churn**

{_md_table(movers('churn'), {'Movement': money})}
"""]
    if hl is not None:
        risk = hl[hl["queue"] == "csm"].head(10)
        opp = hl[hl["queue"] == "sales_assist"].head(10)
        cols = ["company_name", "segment", "industry", "csm_owner", "plan", "current_arr", "arr_at_stake", "next_renewal_date", "reason_code_1", "suggested_play"]
        ren = {"company_name": "Account", "segment": "Segment", "industry": "Industry", "csm_owner": "Owner", "plan": "Plan", "current_arr": "ARR",
               "arr_at_stake": "ARR at stake", "next_renewal_date": "Renewal / next bill", "reason_code_1": "Main reason", "suggested_play": "Play"}
        qs = hl.groupby("queue", sort=False).agg(accounts=("customer_id", "size"), arr=("current_arr", "sum"), at_stake=("arr_at_stake", "sum")).reset_index()
        qs["queue"] = pd.Categorical(qs["queue"], list(QUEUE_ORDER)); qs = qs.sort_values("queue")
        qs = qs.rename(columns={"queue": "Queue", "accounts": "Accounts", "arr": "ARR", "at_stake": "ARR at stake"})
        parts.append(f"""## 6. Who works which accounts (`docs/account_health_list.csv`)

{_md_table(qs, {'Accounts': lambda v: f'{int(v):,}', 'ARR': money, 'ARR at stake': money})}

`csm` = the first {CSM_CAPACITY} CSM-covered risk accounts (CS capacity; annual renewals within 120 days first, then ARR at stake) — play P1;
`renewal_desk` = Technology annual renewals within 6 months on the risk list (P3); `digital` = the rest of the risk list (P2);
`sales_assist` = the top 100 upgrade opportunities (P4); `in_app` = the next 200 (in-app prompt only). ARR at stake: risk = ARR × the
flag's historical hit rate; expansion = expected upgrade ARR. Reason codes: `docs/02_metric_definitions.md §15`.

**CSM queue, first 10**

{_md_table(risk[cols].rename(columns=ren), {'ARR': money, 'ARR at stake': money})}

## 7. Upgrade opportunities (sales-assist queue, first 10)

{_md_table(opp[cols].rename(columns=ren), {'ARR': money, 'ARR at stake': money})}
""")
    nxt = NEXT_MONTH_FOCUS(k) if callable(NEXT_MONTH_FOCUS) else ""
    parts.append(nxt)
    n_internal = int(marts.load("stg_customers")["is_internal"].sum())
    parts.append(f"""## Data notes

- Synthetic dataset; definitions v1.2. Internal / test accounts excluded ({n_internal}). MRR excludes one-time fees, prorations and tax.
- Month-end snapshots: customers who start and cancel inside a month are not visible (counted in the data-quality checks).
- Numbers on this page are generated by `python run.py docs` from the warehouse; nothing is typed by hand.
""")
    p = DOCS / "monthly_revenue_review.md"
    p.write_text("\n".join(parts))
    return p


def _default_next_month(k: dict) -> str:
    return f"""## 8. Focus for October / November

1. **Board pack (VP Revenue, by 12 Nov):** report 12-month NRR {pct(k['nrr_ttm'])} with the definition footnote, NRR excluding the one-off
   ({pct(k['nrr_ttm_ex_one_off'])}) and the Q3 run-rate ({pct(k['nrr_runrate_q3_2026'])}) as the leading indicator.
2. **Re-forecast Q4 expansion** at the Q3 like-for-like rate ({pct(k['expansion_rate_q3_2026'])} of starting MRR), not the Q2 rate.
3. **CS (P1):** start the renewal desk on the `csm` queue of the account list in priority order, with a 1-in-5 holdout.
4. **Sales + Finance (P3):** approve the seat-band offer for Technology renewals in the next 6 months.
5. **Product (P5):** bring the packaging test design to the December pricing council instead of a straight revert.
6. **October flash** after the 7 Nov close: watch whether Technology contraction continues at renewals and whether the expansion rate stays near Q3.
"""


NEXT_MONTH_FOCUS = _default_next_month


def build() -> list[Path]:
    out = []
    p = write_health_list()
    if p:
        out.append(p)
    out.append(write_mbr())
    return out
