"""Generator sanity checks: does the generated data match the design targets in docs/data_model.md §8?

Runs right after generation, before the SQL warehouse exists, using a small self-contained
pandas MRR calculation. The official metric layer is sql/ (reconciled against
src/saas_rev/crosscheck.py); these checks only answer "did the generator do what the spec says?".

Usage:
    python data_gen/sanity_checks.py                       # reads <data_dir>/raw
    python data_gen/sanity_checks.py --raw DIR --json out.json
Exit code 1 if any check fails.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from saas_rev.config import get_settings  # noqa: E402

INTERNAL_PATTERN = r"(?i)(?:\btest\b|\[internal\]|^internal\b|\bqa\b|zz_test)"
LIST_MONTHLY = {"basic": 15.0, "premium": 35.0, "enterprise": 50.0}   # billing-neutral price per seat-month


def load(raw: Path) -> dict[str, pd.DataFrame]:
    return {n: pd.read_parquet(raw / f"{n}.parquet") for n in
            ["plans", "customers", "subscriptions", "plan_changes", "invoices", "usage_events"]}


def internal_ids(cust: pd.DataFrame) -> set:
    m = cust["email_domain"].eq("ourcompany.example") | cust["company_name"].str.contains(INTERNAL_PATTERN, regex=True)
    return set(cust.loc[m, "customer_id"])


def versions(t: dict[str, pd.DataFrame], cutoff: date) -> pd.DataFrame:
    """Subscription terms over time (SCD2), MRR rounded to cents, orphan rows dropped."""
    excl = internal_ids(t["customers"])
    subs = t["subscriptions"][~t["subscriptions"]["customer_id"].isin(excl)].copy()
    ch = t["plan_changes"].merge(subs[["subscription_id", "start_date", "end_date"]], on="subscription_id")
    ch = ch[(ch["effective_date"] >= ch["start_date"])
            & (ch["end_date"].isna() | (ch["effective_date"] < ch["end_date"]))
            & (ch["effective_date"] <= cutoff)]
    cols = ["subscription_id", "customer_id", "eff", "plan", "seats", "billing", "unit_price", "discount"]
    v0 = subs.rename(columns={"start_date": "eff", "initial_plan_id": "plan", "initial_seats": "seats",
                              "initial_billing_period": "billing", "initial_unit_price": "unit_price",
                              "initial_discount_pct": "discount"})[cols].assign(req=pd.NaT, ord="")
    v1 = ch.rename(columns={"effective_date": "eff", "to_plan_id": "plan", "to_seats": "seats",
                            "to_billing_period": "billing", "to_unit_price": "unit_price",
                            "to_discount_pct": "discount", "change_id": "ord", "requested_date": "req"})
    v1 = v1[cols + ["req", "ord"]]
    v = pd.concat([v0, v1], ignore_index=True)
    v["eff"] = pd.to_datetime(v["eff"])
    v["req"] = pd.to_datetime(v["req"])
    v["is_initial"] = v["ord"] == ""
    v = v.sort_values(["subscription_id", "eff", "is_initial", "req", "ord"], ascending=[True, True, False, True, True])
    v = v.reset_index(drop=True)
    v["next_eff"] = v.groupby("subscription_id")["eff"].shift(-1)
    ends = pd.to_datetime(subs.set_index("subscription_id")["end_date"])
    far = pd.Timestamp("2100-01-01")
    v["valid_to"] = np.minimum(v["next_eff"].fillna(far).values,
                               v["subscription_id"].map(ends).fillna(far).values)
    v = v[v["valid_to"] > v["eff"]].copy()
    monthly_equiv = np.where(v["billing"] == "annual", v["unit_price"] / 12.0, v["unit_price"])
    v["mrr"] = np.round(v["seats"] * monthly_equiv * (1 - v["discount"]), 2)
    v["mrr_norm"] = np.round(v["seats"] * v["plan"].map(LIST_MONTHLY) * (1 - v["discount"]), 2)
    return v


def panel_from_versions(v: pd.DataFrame, cutoff: date) -> pd.DataFrame:
    me = pd.date_range("2023-10-31", pd.Timestamp(cutoff), freq="ME")
    me_arr = me.values
    lo = np.searchsorted(me_arr, v["eff"].values, side="left")
    hi = np.searchsorted(me_arr, v["valid_to"].values, side="left")
    n = np.maximum(hi - lo, 0)
    rows = np.repeat(np.arange(len(v)), n)
    offs = np.concatenate([np.arange(k) for k in n]) if n.sum() else np.array([], dtype=int)
    m_idx = np.repeat(lo, n) + offs
    tier = v["plan"].map({"basic": 1, "premium": 2, "enterprise": 3}).values
    out = pd.DataFrame({"customer_id": v["customer_id"].values[rows], "month_end": me_arr[m_idx],
                        "mrr": v["mrr"].values[rows], "mrr_norm": v["mrr_norm"].values[rows],
                        "tier": tier[rows], "seats": v["seats"].values[rows]})
    out = out.groupby(["customer_id", "month_end"], as_index=False).agg(
        mrr=("mrr", "sum"), mrr_norm=("mrr_norm", "sum"), tier=("tier", "max"), seats=("seats", "sum"))
    first = out[out["mrr"] > 0].groupby("customer_id")["month_end"].min()
    grid = [(cid, m) for cid, f in first.items() for m in me[me >= f]]
    panel = pd.DataFrame(grid, columns=["customer_id", "month_end"])
    panel = panel.merge(out, on=["customer_id", "month_end"], how="left")
    panel[["mrr", "mrr_norm", "seats"]] = panel[["mrr", "mrr_norm", "seats"]].fillna(0.0)
    panel["tier"] = panel["tier"].fillna(0).astype(int)
    return panel.sort_values(["customer_id", "month_end"]).reset_index(drop=True)


def movements(panel: pd.DataFrame) -> pd.DataFrame:
    p = panel.copy()
    g = p.groupby("customer_id")
    p["prev"] = g["mrr"].shift(1).fillna(0.0)
    p["prev_tier"] = g["tier"].shift(1).fillna(0).astype(int)
    p["paid_before"] = g["mrr"].transform(lambda s: (s > 0).astype(int).cummax().shift(1, fill_value=0)).astype(bool)
    cur, prev = p["mrr"], p["prev"]
    p["new"] = np.where((prev == 0) & (cur > 0) & ~p["paid_before"], cur, 0.0)
    p["reactivation"] = np.where((prev == 0) & (cur > 0) & p["paid_before"], cur, 0.0)
    p["expansion"] = np.where((prev > 0) & (cur > prev), cur - prev, 0.0)
    p["contraction"] = np.where((prev > 0) & (cur > 0) & (cur < prev), prev - cur, 0.0)
    p["churn"] = np.where((prev > 0) & (cur == 0), prev, 0.0)
    return p


def ttm(panel: pd.DataFrame, t: str) -> tuple[float, float]:
    T = pd.Timestamp(t)
    t0 = T - pd.offsets.MonthEnd(12)
    a = panel[panel["month_end"] == t0].set_index("customer_id")["mrr"]
    b = panel[panel["month_end"] == T].set_index("customer_id")["mrr"]
    base = a[a > 0]
    now = b.reindex(base.index).fillna(0.0)
    return float(now.sum() / base.sum()), float(np.minimum(now, base).sum() / base.sum())


def quarter_sum(total: pd.DataFrame, months: list[str]) -> pd.Series:
    return total.loc[[pd.Timestamp(m) for m in months]].sum()


def run(raw: Path) -> dict:
    s = get_settings()
    t = load(raw)
    cutoff = s.as_of_date
    v = versions(t, cutoff)
    panel = panel_from_versions(v, cutoff)
    mv = movements(panel)
    T = pd.Timestamp(cutoff)
    total = mv.groupby("month_end")[["mrr", "new", "expansion", "contraction", "churn", "reactivation"]].sum()
    cust = t["customers"]
    excl = internal_ids(cust)
    subs = t["subscriptions"][~t["subscriptions"]["customer_id"].isin(excl)]
    paying_ids = set(subs["customer_id"])
    real = cust[cust["customer_id"].isin(paying_ids)]
    first_subs = subs.sort_values("start_date").groupby("customer_id").head(1)
    seg_of = real.set_index("customer_id")["segment"]
    ind_of = real.set_index("customer_id")["industry"]
    ch_of = real.set_index("customer_id")["acquisition_channel"]

    m = mv.copy()
    m["segment"] = m["customer_id"].map(seg_of)
    m["industry"] = m["customer_id"].map(ind_of)
    m["channel"] = m["customer_id"].map(ch_of)

    # --- monthly logo churn by segment (customer level)
    def churn_rate(mask):
        x = m[mask & (m["prev"] > 0)]
        return float((x["churn"] > 0).mean())
    seg_churn = {sg: churn_rate(m["segment"] == sg) for sg in ["SMB", "Mid-Market", "Enterprise"]}

    # --- quarters
    q3_26 = quarter_sum(total, ["2026-07-31", "2026-08-31", "2026-09-30"])
    q2_26 = quarter_sum(total, ["2026-04-30", "2026-05-31", "2026-06-30"])
    q3_25 = quarter_sum(total, ["2025-07-31", "2025-08-31", "2025-09-30"])
    boq = total["mrr"]
    rate_q3_26 = q3_26["expansion"] / boq.loc[pd.Timestamp("2026-06-30")]
    rate_q2_26 = q2_26["expansion"] / boq.loc[pd.Timestamp("2026-03-31")]
    rate_q3_25 = q3_25["expansion"] / boq.loc[pd.Timestamp("2025-06-30")]

    # --- Basic -> higher upgrades per Basic account-month (voluntary only)
    ch = t["plan_changes"].merge(subs[["subscription_id"]], on="subscription_id")
    ch["eff"] = pd.to_datetime(ch["effective_date"])
    ch["month_end"] = ch["eff"] + pd.offsets.MonthEnd(0)
    vol_up = ch[(ch["from_plan_id"] == "basic") & (ch["to_plan_id"] != "basic") & (ch["change_source"] != "system")]
    basic_prev = m[(m["prev_tier"] == 1) & (m["prev"] > 0)][["customer_id", "month_end"]]
    up_m = vol_up[["customer_id", "month_end"]].drop_duplicates().assign(up=1)
    bm = basic_prev.merge(up_m, on=["customer_id", "month_end"], how="left").fillna({"up": 0})
    bm["q"] = bm["month_end"].dt.to_period("Q")
    up_rate = bm.groupby("q")["up"].mean()

    # --- Tech seat-add events per Tech paying account-month, before vs after the freeze
    adds = ch[(ch["to_seats"] > ch["from_seats"])]
    adds = adds.assign(industry=adds["customer_id"].map(ind_of))
    tech_m = m[(m["industry"] == "Technology") & (m["mrr"] > 0)]
    pre_adds = adds[(adds.industry == "Technology") & (adds.eff >= "2025-04-01") & (adds.eff < "2026-04-01")].shape[0]
    pre_am = tech_m[(tech_m.month_end >= "2025-04-30") & (tech_m.month_end <= "2026-03-31")].shape[0]
    post_adds = adds[(adds.industry == "Technology") & (adds.eff >= "2026-06-01") & (adds.eff <= "2026-09-30")].shape[0]
    post_am = tech_m[(tech_m.month_end >= "2026-06-30") & (tech_m.month_end <= "2026-09-30")].shape[0]
    tech_change = (post_adds / post_am) / (pre_adds / pre_am) - 1

    # --- silent contraction at cutoff (billing-neutral MRR, non-billing contraction, excl. open cancellations)
    pn = panel.copy()
    pn["norm_prev"] = pn.groupby("customer_id")["mrr_norm"].shift(1)
    pn["norm_drop"] = (pn["norm_prev"] > 0) & (pn["mrr_norm"] > 0) & (pn["mrr_norm"] < pn["norm_prev"] - 0.005)
    win = pn[(pn.month_end > T - pd.offsets.MonthEnd(7)) & (pn.month_end < T)]
    peak = win.groupby("customer_id")["mrr_norm"].max()
    drops = pn[(pn.month_end > T - pd.offsets.MonthEnd(6)) & pn["norm_drop"]]["customer_id"].unique()
    now = pn[pn.month_end == T].set_index("customer_id")
    open_cancel = set(subs[subs["cancel_requested_date"].notna() & (subs["end_date"].isna())]["customer_id"])
    cand = now[(now["mrr"] > 0) & (now["mrr_norm"] <= 0.8 * peak.reindex(now.index).fillna(0))
               & now.index.isin(drops) & ~now.index.isin(open_cancel)]
    saves = set(ch[(ch["change_source"] == "customer_success") & (ch["eff"] > T - pd.offsets.MonthEnd(6))]["customer_id"])
    silent = cand[~cand.index.isin(saves)]
    silent_share = float(silent["mrr"].sum() / now["mrr"].sum())

    # --- S3 upside: Basic monthly logo churn, May-Sep 2026 vs May-Sep 2025, excluding Paid Social
    def basic_churn(a, b):
        x = m[(m["prev_tier"] == 1) & (m["prev"] > 0) & (m["channel"] != "Paid Social")
              & (m["month_end"] >= a) & (m["month_end"] <= b)]
        return float((x["churn"] > 0).mean())
    basic_churn_change = basic_churn("2026-05-31", "2026-09-30") / basic_churn("2025-05-31", "2025-09-30") - 1

    nrr_25_09, grr_25_09 = ttm(panel, "2025-09-30")
    nrr_25_12, grr_25_12 = ttm(panel, "2025-12-31")
    nrr_26_06, grr_26_06 = ttm(panel, "2026-06-30")
    nrr_26_09, grr_26_09 = ttm(panel, "2026-09-30")
    plan_mix = first_subs["initial_plan_id"].value_counts(normalize=True).round(4).to_dict()
    annual_share = float((first_subs["initial_billing_period"] == "annual").mean())
    mrr_now = float(now["mrr"].sum())

    checks = [
        ("Accounts ever paying (excl. internal)", len(paying_ids), 5000, 7000),
        ("Paying accounts at 2026-09-30", int((now["mrr"] > 0).sum()), 3000, 5000),
        ("MRR at 2026-09-30 ($)", round(mrr_now), 1_200_000, 2_600_000),
        ("Signup plan mix: Basic share", plan_mix.get("basic", 0), 0.60, 0.72),
        ("Signup plan mix: Enterprise share", plan_mix.get("enterprise", 0), 0.01, 0.06),
        ("Signup annual-billing share", round(annual_share, 4), 0.25, 0.45),
        ("Monthly logo churn, SMB", round(seg_churn["SMB"], 4), 0.02, 0.05),
        ("Monthly logo churn, Mid-Market", round(seg_churn["Mid-Market"], 4), 0.003, 0.015),
        ("Monthly logo churn, Enterprise", round(seg_churn["Enterprise"], 4), 0.001, 0.010),
        ("TTM GRR at 2025-09-30", round(grr_25_09, 4), 0.82, 0.92),
        ("TTM NRR at 2025-09-30", round(nrr_25_09, 4), 1.02, 1.18),
        ("TTM NRR change, 2026-06 -> 2026-09 (pts)", round((nrr_26_09 - nrr_26_06) * 100, 2), -10, -1.5),
        ("Expansion MRR, Q3 2026 vs Q2 2026", round(float(q3_26["expansion"] / q2_26["expansion"] - 1), 4), -0.60, -0.15),
        ("Expansion rate, Q3 2026 vs Q3 2025 (relative)", round(float(rate_q3_26 / rate_q3_25 - 1), 4), -0.60, -0.15),
        ("Tech seat-add events per account-month, Jun-Sep 2026 vs prior 12 months", round(tech_change, 4), -0.90, -0.40),
        ("Voluntary Basic upgrade rate, Q3 2026 vs Q3 2025 (relative)",
         round(float(up_rate.get(pd.Period("2026Q3")) / up_rate.get(pd.Period("2025Q3")) - 1), 4), -0.70, -0.30),
        ("Basic monthly logo churn (non-Paid-Social), May-Sep 2026 vs 2025", round(basic_churn_change, 4), -0.35, -0.05),
        ("Silent contraction at 2026-09-30, share of MRR", round(silent_share, 4), 0.02, 0.12),
    ]
    check_rows = [{"check": c, "value": v, "min": lo, "max": hi, "pass": bool(lo <= v <= hi)} for c, v, lo, hi in checks]

    return {
        "checks": check_rows,
        "all_pass": all(r["pass"] for r in check_rows),
        "detail": {
            "segment_monthly_logo_churn": seg_churn,
            "plan_mix_at_signup": plan_mix,
            "ttm": {"nrr_2025_09": nrr_25_09, "grr_2025_09": grr_25_09, "nrr_2025_12": nrr_25_12,
                    "nrr_2026_06": nrr_26_06, "nrr_2026_09": nrr_26_09, "grr_2026_09": grr_26_09},
            "expansion_rate": {"q3_25": float(rate_q3_25), "q2_26": float(rate_q2_26), "q3_26": float(rate_q3_26)},
            "q2_26": q2_26.round(0).to_dict(), "q3_26": q3_26.round(0).to_dict(), "q3_25": q3_25.round(0).to_dict(),
            "basic_upgrade_rate_by_quarter": {str(k): round(float(x), 4) for k, x in up_rate.items()},
            "silent_contraction_accounts": int(len(silent)),
            "saved_by_cs_last_6m_flagged": int(cand.index.isin(saves).sum()),
            "mrr_by_quarter_end": {str(k.date()): round(float(x), 0) for k, x in total["mrr"].items() if k.month in (3, 6, 9, 12)},
        },
    }


def main() -> None:
    s = get_settings()
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", type=Path, default=s.raw_dir)
    ap.add_argument("--json", type=Path, default=None)
    args = ap.parse_args()
    res = run(args.raw)
    for r in res["checks"]:
        print(f"{'PASS' if r['pass'] else 'FAIL'}  {r['check']:<75} {r['value']!s:>12}   [{r['min']}, {r['max']}]")
    txt = json.dumps(res, indent=2, default=str)
    if args.json:
        args.json.write_text(txt)
    else:
        print(json.dumps(res["detail"], indent=2, default=str))
    sys.exit(0 if res["all_pass"] else 1)


if __name__ == "__main__":
    main()
