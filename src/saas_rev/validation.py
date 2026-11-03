"""Known-answer tests: planted effects (generator answer key) vs what the metric layer recovers.

This is QA of the method, run after the analysis. The analysis modules never read the answer
key; this step is the only place that does. Results go to reports/key_numbers/answer_key.json
(shown on the dashboard's Data Model page and in docs/03_data_quality_checks.md).
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import yaml

from . import marts
from .config import REPO_ROOT, get_settings
from .theme import money, pct, pts


def _truth() -> dict:
    return json.loads((get_settings().raw_dir / "_scenario_truth.json").read_text())


def _params() -> dict:
    return yaml.safe_load((REPO_ROOT / "data_gen" / "params.yaml").read_text())


def _rate(num: float, den: float) -> float:
    return float(num) / float(den) if den else float("nan")


def recovered() -> dict:
    """Recovered estimates computed from the marts (the same tables the analysis uses)."""
    out: dict = {}
    # S5 one-off
    one = marts.load("metrics__one_offs")
    q2 = one[one["quarter"] == "2026-Q2"].iloc[0]
    out["s5"] = {"customer_id": q2["customer_id"], "company": q2["company_name"], "expansion_mrr": float(q2["expansion_mrr"]),
                 "share_of_q2": float(q2["share_of_quarter"]), "only_flag_in_any_quarter": int(len(one)) == 1}
    # S4 Technology
    rb = marts.load("metrics__expansion_rate_by_bucket")
    tech = rb[rb["bucket"] == "Technology (all)"].set_index("quarter_label")["rate"]
    mv = marts.load("fct_mrr_movements")
    q = marts.load("rpt_mrr_quarterly").set_index("quarter_label")
    def seg_rate(quarter: str, industry: str, col: str) -> float:
        """Movement MRR of one industry in a quarter ÷ that industry's own starting MRR."""
        x = mv[(mv["quarter_label"] == quarter) & (mv["industry"] == industry)]
        start = mv[(mv["month_end"] == pd.Timestamp(q.loc[quarter, "first_month_end"])) & (mv["industry"] == industry)]["mrr_prev"].sum()
        return _rate(x[col].sum(), start)

    def seg_contraction_rate(quarter: str, industry: str) -> float:
        return seg_rate(quarter, industry, "contraction_mrr")
    seg = marts.load("rpt_segment_retention_ttm")
    tnrr = seg[(seg["dimension"] == "industry") & (seg["dimension_value"] == "Technology")].set_index("month_end")["nrr"]
    out["s4"] = {"tech_expansion_rate_q3_2025": float(tech.get("2025-Q3", np.nan)),        # ÷ company starting MRR (bridge base)
                 "tech_expansion_rate_q3_2026": float(tech.get("2026-Q3", np.nan)),
                 "tech_expansion_rate_own_base_q3_2025": seg_rate("2025-Q3", "Technology", "expansion_mrr"),   # ÷ Tech starting MRR
                 "tech_expansion_rate_own_base_q3_2026": seg_rate("2026-Q3", "Technology", "expansion_mrr"),
                 "tech_contraction_rate_q3_2025": seg_contraction_rate("2025-Q3", "Technology"),
                 "tech_contraction_rate_q3_2026": seg_contraction_rate("2026-Q3", "Technology"),
                 "tech_nrr_2025_09": float(tnrr.get(pd.Timestamp("2025-09-30"), np.nan)),
                 "tech_nrr_2026_09": float(tnrr.get(pd.Timestamp("2026-09-30"), np.nan))}
    # S3 packaging: voluntary Basic upgrade rate and paywall hits before / after 2026-04-15
    up = marts.load("fct_basic_upgrade_monthly")
    pre = up[(up["month_end"] >= "2025-04-30") & (up["month_end"] <= "2026-03-31")]
    post = up[(up["month_end"] >= "2026-05-31") & (up["month_end"] <= "2026-09-30")]
    pre_np, post_np = pre[pre["acquisition_channel"] != "Paid Social"], post[post["acquisition_channel"] != "Paid Social"]
    uw = marts.load("fct_account_usage_weekly")
    basic = uw[(uw["plan_id"] == "basic") & uw["is_live_at_week_end"] & uw["is_complete_week"]]
    gate_pre = basic[(basic["week_start"] >= "2025-10-06") & (basic["week_start"] < "2026-04-06")]
    gate_post = basic[(basic["week_start"] >= "2026-05-04") & (basic["week_start"] <= "2026-09-21")]
    gate_ratio = _rate(gate_post["premium_gate_hits"].sum() / gate_post["wau"].sum(),
                       gate_pre["premium_gate_hits"].sum() / gate_pre["wau"].sum())
    auto_ratio = _rate(gate_post["automation_runs"].sum() / gate_post["wau"].sum(),
                       gate_pre["automation_runs"].sum() / gate_pre["wau"].sum())
    # Basic monthly logo churn (non-Paid-Social), May-Sep 2026 vs May-Sep 2025
    def basic_churn(a: str, b: str) -> tuple[float, int]:
        x = mv[(mv["month_end"] >= a) & (mv["month_end"] <= b) & (mv["plan_id_prev"] == "basic")
               & (mv["mrr_prev"] > 0) & (mv["acquisition_channel"] != "Paid Social")]
        return float((x["movement_type"] == "churn").mean()), int(len(x))
    c25, n25 = basic_churn("2025-05-31", "2025-09-30")
    c26, n26 = basic_churn("2026-05-31", "2026-09-30")
    def np_rate(a: str, b: str) -> float:
        x = up[(up["month_end"] >= a) & (up["month_end"] <= b) & (up["acquisition_channel"] != "Paid Social")]
        return float(x["voluntary_upgrade"].mean())
    out["s3_timing"] = {"non_ps_q3_2025": np_rate("2025-07-31", "2025-09-30"), "non_ps_jan_mar_2026": np_rate("2026-01-31", "2026-03-31"),
                        "non_ps_may_jul_2026": np_rate("2026-05-31", "2026-07-31")}
    out["s3"] = {"upgrade_rate_pre": float(pre["voluntary_upgrade"].mean()), "upgrade_rate_post": float(post["voluntary_upgrade"].mean()),
                 "upgrade_rate_pre_non_paid_social": float(pre_np["voluntary_upgrade"].mean()),
                 "upgrade_rate_post_non_paid_social": float(post_np["voluntary_upgrade"].mean()),
                 "gate_hits_per_wau_ratio_post_pre": gate_ratio, "automation_per_wau_ratio_post_pre": auto_ratio,
                 "basic_churn_2025": c25, "basic_churn_2026": c26, "basic_churn_n_2025": n25, "basic_churn_n_2026": n26}
    # S2 Paid Social
    f = marts.load("fct_customer_mrr_monthly")
    first = f[f["months_since_first_paid"] == 0]
    newq = first.assign(q=first["month_end"].dt.to_period("Q").astype(str))
    ps_share = newq.groupby("q")["acquisition_channel"].apply(lambda s: (s == "Paid Social").mean())
    k6 = f[f["months_since_first_paid"] == 6].merge(first[["customer_id", "mrr"]], on="customer_id", suffixes=("", "_0"))
    k6 = k6[k6["cohort_month_end"].between("2025-09-30", "2026-03-31")]
    def grr6(mask):
        x = k6[mask]
        return _rate(np.minimum(x["mrr"], x["mrr_0"]).sum(), x["mrr_0"].sum())
    out["s2"] = {"paid_social_share_new_logos_2025Q2": float(ps_share.get("2025Q2", np.nan)),
                 "paid_social_share_new_logos_2026Q2": float(ps_share.get("2026Q2", np.nan)),
                 "grr_6m_paid_social": grr6(k6["acquisition_channel"] == "Paid Social"),
                 "grr_6m_other_smb": grr6((k6["acquisition_channel"] != "Paid Social") & (k6["segment"] == "SMB"))}
    # S1 early cohorts
    coh = marts.load("rpt_cohort_retention")
    cq = coh[(coh["cohort_grain"] == "quarter") & coh["is_complete"]]
    def cell(cohort: str, k: int) -> float:
        x = cq[(cq["cohort"] == cohort) & (cq["months_since_first_paid"] == k)]
        return float(x["nrr"].iloc[0]) if len(x) else float("nan")
    later = cq[(cq["months_since_first_paid"] == 12) & cq["cohort"].between("2024-Q1", "2025-Q3")]["nrr"]
    later24 = cq[(cq["months_since_first_paid"] == 24) & cq["cohort"].between("2024-Q1", "2025-Q3")]["nrr"]
    out["s1"] = {"nrr_2023Q4_m12": cell("2023-Q4", 12), "nrr_later_cohorts_m12_median": float(later.median()),
                 "nrr_later_cohorts_m12_p75": float(later.quantile(0.75)), "nrr_later_cohorts_m12_max": float(later.max()),
                 "nrr_2023Q4_m24": cell("2023-Q4", 24),
                 "nrr_later_cohorts_m24_max": float(later24.max()) if len(later24) else float("nan"),
                 "later_cohorts_m24_n": int(len(later24))}
    # S6 seasonality
    s = marts.load("metrics__seasonality")
    out["s6"] = {int(r.year): float(r.change_pts) for r in s.itertuples()}
    return out


def answer_key_rows(rec: dict) -> list[dict]:
    P, T = _params(), _truth()
    sc = P["scenario"]
    s1, s2, s3, s4, s5, s6 = (rec[k] for k in ("s1", "s2", "s3", "s4", "s5", "s6"))
    pk, tf, ro = sc["packaging_change"], sc["tech_freeze"], sc["rollout"]
    rows = [
        {"event": "S5 one-off rollout (2026-05-12)",
         "planted": f"one Financial Services Enterprise account × {ro['seat_mult']} seats",
         "recovered": f"{s5['company']} +{money(s5['expansion_mrr'])} MRR in May = {pct(s5['share_of_q2'], 0)} of Q2 expansion; "
                      f"the only single-account expansion above 10% of any quarter",
         "naive_cut": "Q2 → Q3 expansion −33% reads as a collapse",
         "verdict": "recovered" if s5["customer_id"] == T["S5_rollout_customer_id"] and s5["only_flag_in_any_quarter"] else "check"},
        {"event": "S4 Technology hiring freeze + layoffs (from 2026-04)",
         "planted": f"team growth {tf['team_growth_shift'] * 100:+.2f} pt/week; {pct(tf['layoff_wave']['prob'], 0)} of Tech accounts "
                    f"lose {pct(1 - tf['layoff_wave']['team_mult'][1], 0)}–{pct(1 - tf['layoff_wave']['team_mult'][0], 0)} of the team in Jun–Jul",
         "recovered": f"Technology expansion {pct(s4['tech_expansion_rate_own_base_q3_2025'], 1)} → {pct(s4['tech_expansion_rate_own_base_q3_2026'], 1)} "
                      f"and contraction {pct(s4['tech_contraction_rate_q3_2025'], 1)} → {pct(s4['tech_contraction_rate_q3_2026'], 1)} of "
                      f"Technology starting MRR (Q3 2025 → Q3 2026); Technology 12-month NRR {pct(s4['tech_nrr_2025_09'], 0)} → "
                      f"{pct(s4['tech_nrr_2026_09'], 0)}",
         "naive_cut": "company-wide averages hide that one industry drives most of the change",
         "verdict": "recovered" if s4["tech_expansion_rate_q3_2026"] < 0.5 * s4["tech_expansion_rate_q3_2025"] else "check"},
        {"event": "S3 Basic packaging change (2026-04-15)",
         "planted": f"upgrade hazard × {pk['upgrade_mult']}, paywall hits × {pk['gate_mult']}; upside: Basic churn × {pk['basic_churn_mult']}",
         "recovered": f"voluntary Basic upgrade rate {pct(s3['upgrade_rate_pre'], 2)} → {pct(s3['upgrade_rate_post'], 2)} per month "
                      f"(non-Paid-Social {pct(s3['upgrade_rate_pre_non_paid_social'], 2)} → {pct(s3['upgrade_rate_post_non_paid_social'], 2)}; "
                      f"in two steps: {pct(rec['s3_timing']['non_ps_q3_2025'], 2)} in Q3 2025 → {pct(rec['s3_timing']['non_ps_jan_mar_2026'], 2)} "
                      f"in Jan–Mar 2026, before the change, → {pct(rec['s3_timing']['non_ps_may_jul_2026'], 2)} in May–Jul 2026); "
                      f"paywall hits per active user × {s3['gate_hits_per_wau_ratio_post_pre']:.2f}; automation runs × "
                      f"{s3['automation_per_wau_ratio_post_pre']:.2f}; Basic monthly churn {pct(s3['basic_churn_2025'], 2)} → {pct(s3['basic_churn_2026'], 2)}",
         "naive_cut": "a before/after on the pooled Basic upgrade rate also picks up the Paid Social mix shift",
         "verdict": "recovered" if s3["upgrade_rate_post_non_paid_social"] < 0.75 * s3["upgrade_rate_pre_non_paid_social"] else "check"},
        {"event": "S2 Paid Social program (from 2025-09)",
         "planted": "extra SMB self-serve accounts with lower product fit and fewer power users",
         "recovered": f"Paid Social share of new logos {pct(s2['paid_social_share_new_logos_2025Q2'], 0)} (Q2 2025) → "
                      f"{pct(s2['paid_social_share_new_logos_2026Q2'], 0)} (Q2 2026); 6-month GRR {pct(s2['grr_6m_paid_social'], 0)} vs "
                      f"{pct(s2['grr_6m_other_smb'], 0)} for other SMB signups (Sep 2025 – Mar 2026 cohorts)",
         "naive_cut": "new-logo counts look strong while the cohorts retain worse",
         "verdict": "recovered" if s2["grr_6m_paid_social"] < s2["grr_6m_other_smb"] else "check"},
        {"event": "S1 design partners and early adopters (Oct–Dec 2023)",
         "planted": f"{T['S1_design_partners']} design partners; early adopters with higher product fit",
         "recovered": f"Q4 2023 cohort NRR at month 12 {pct(s1['nrr_2023Q4_m12'], 0)} vs a median of {pct(s1['nrr_later_cohorts_m12_median'], 0)} "
                      f"(top quartile from {pct(s1['nrr_later_cohorts_m12_p75'], 0)}, best {pct(s1['nrr_later_cohorts_m12_max'], 0)}) for the "
                      f"2024 – Q3 2025 cohorts; at month 24 {pct(s1['nrr_2023Q4_m24'], 0)} vs up to {pct(s1['nrr_later_cohorts_m24_max'], 0)} "
                      f"for the {s1['later_cohorts_m24_n']} later cohorts observed that long",
         "naive_cut": "early TTM NRR points (small base) overstate the steady state",
         "verdict": ("recovered" if s1["nrr_2023Q4_m12"] > s1["nrr_later_cohorts_m12_p75"]
                     and s1["nrr_2023Q4_m24"] > s1["nrr_later_cohorts_m24_max"]
                     else "weak — right direction at month 12, not distinctive"
                     if s1["nrr_2023Q4_m12"] > s1["nrr_later_cohorts_m12_median"] else "not recovered")},
        {"event": "S6 seasonality",
         "planted": "summer hiring lull (Jul–Aug team growth −0.10 pt/week), holiday usage dip",
         "recovered": f"Q2 → Q3 expansion-rate change {pts(s6.get(2024))} (2024), {pts(s6.get(2025))} (2025)",
         "naive_cut": "a single year's Q2 → Q3 change is read as the seasonal effect",
         "verdict": "not separable from noise with n = 2 (as expected)"},
    ]
    return rows


def dq_known_answers() -> list[dict]:
    T = _truth()
    dq = marts.load("dq_check_results").set_index("check_id")
    cust = marts.load("stg_customers")
    rows = [
        ("Internal / test accounts excluded", len(T["dq_internal_customer_ids"]), int(cust["is_internal"].sum())),
        ("Duplicate CRM records flagged", len(T["dq_duplicate_customer_ids"]), int(cust["is_duplicate_record"].sum())),
        ("Orphan plan changes rejected", T["dq_orphan_plan_changes"], int(dq.loc["ML02", "observed"])),
        ("Duplicated usage rows removed", T["dq_duplicate_usage_rows"], int(dq.loc["PK05", "observed"])),
    ]
    return [{"check": c, "injected": int(a), "found": int(b), "verdict": "match" if a == b else "check"} for c, a, b in rows]


def run() -> dict:
    rec = recovered()
    out = {"rows": answer_key_rows(rec), "dq_known_answers": dq_known_answers(), "recovered_values": rec}
    v = [r["verdict"] for r in out["rows"]]
    out["summary"] = {"events": len(v), "recovered": sum(x == "recovered" for x in v),
                      "weak": sum(x.startswith("weak") for x in v), "not_separable": sum(x.startswith("not separable") for x in v),
                      "not_recovered": sum(x == "not recovered" or x == "check" for x in v)}
    marts.write_key_numbers("answer_key", out)
    sm = out["summary"]
    print(f"    planted effects: {sm['recovered']} recovered, {sm['weak']} weak, {sm['not_separable']} not separable (as expected), "
          f"{sm['not_recovered']} not recovered (of {sm['events']}); "
          f"DQ known answers: {sum(r['verdict'] == 'match' for r in out['dq_known_answers'])}/{len(out['dq_known_answers'])}")
    return out
