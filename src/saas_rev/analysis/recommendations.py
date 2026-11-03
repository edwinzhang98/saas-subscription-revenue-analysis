"""Recommendations: seven plays, each with an owner, a target list, an action, an expected NRR impact
sized from our data plus stated assumptions, a measurement plan and risks (scope Q6).

How the impact is sized. For every play:
    addressable MRR    — measured: MRR of the accounts the play targets today, or the MRR flow it acts on
    expected outcome   — measured: share of that MRR lost (or added) over the next 12 months, from history
                         (12-month MRR loss = 1 − GRR of comparable account-months: churn counts as 100% lost,
                         each account capped at its starting MRR)
    effect of the play — an ASSUMPTION (low / base / high), set wide on purpose; replaced by the effect measured
                         in the play's holdout
    reach              — share of the target list actually worked (1.0 = all of it)
    impact (MRR)       = addressable MRR × expected outcome × effect × reach   (MRR in 12 months)
    impact (NRR pts)   = impact ÷ MRR of customers paying on 30 Sep 2026 (the base of next year's NRR)

Accounts in P1–P4 and P6 are assigned to one play only. P5 and P7 act on future signups and failed charges,
which can overlap those accounts (involuntary churn is also inside the loss rates P1 and P2 are sized on),
so the total is a simple sum and may count a little twice. Plays that act on new signups (P5 test cell, most of P6)
barely move next year's NRR — new customers are not in its base — and are sized only on what reaches existing
customers within 12 months.
The dashboard's Recommendations page lets you change reach and effect for each play and recomputes everything.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from .. import marts, theme
from ..config import get_settings
from ..theme import money, pct

TOPIC = "recommendations"
AS_OF = pd.Timestamp("2026-09-30")
LY = pd.Timestamp("2025-09-30")
CSM_CAPACITY = 120                  # CSM reviews per quarter (assumption A9)
CSM_COVERAGE = ("named_csm", "pooled_csm")
UPGRADE_LIST = 100                  # Basic accounts worked per quarter by sales-assist (expansion slice capacity)
TECH_RENEWAL_DAYS = 183
ROLLOUT_MONTHS_IN_WINDOW = 2        # packaging: test on new signups Jan–Jun 2027, read in Jul, roll-out Aug–Sep 2027


@dataclass
class Play:
    id: str
    title: str
    owner: str
    who: str
    what: str
    evidence: str
    measure: str
    risk: str
    addressable_mrr: float          # measured
    outcome_rate: float             # measured: share of addressable MRR lost / added over 12 months
    effect: tuple[float, float, float]   # assumption: (low, base, high)
    reach: float = 1.0              # default share of the target list worked
    accounts: int = 0
    effect_label: str = "share of the expected loss prevented"
    outcome_label: str = "expected 12-month MRR loss"
    addressable_label: str = "MRR of the accounts on the list"
    sizing_note: str = ""
    run_rate_mrr_per_year: float | None = None   # full-year value once rolled out (plays that start late in the window)
    notes: list[str] = field(default_factory=list)

    def impact_mrr(self, reach: float | None = None, effect: float | None = None) -> float:
        r = self.reach if reach is None else reach
        e = self.effect[1] if effect is None else effect
        return float(self.addressable_mrr * self.outcome_rate * e * r)

    def sizing_text(self, reach: float | None = None, effect: float | None = None) -> str:
        """One line: how the impact is computed, with measured and assumed parts labelled."""
        r = self.reach if reach is None else reach
        e = self.effect[1] if effect is None else effect
        parts = [f"{self.addressable_label} {money(self.addressable_mrr)} (from data)"]
        if abs(self.outcome_rate - 1.0) > 1e-9:
            parts.append(f"{self.outcome_label} {pct(self.outcome_rate, 1)} (from data)")
        parts.append(f"{self.effect_label} {pct(e, 0)} (assumption; range {pct(self.effect[0], 0)}–{pct(self.effect[2], 0)})")
        if abs(r - 1.0) > 1e-9:
            parts.append(f"reach {pct(r, 0)}")
        return " × ".join(parts) + f" = {money(self.impact_mrr(r, e))} MRR within 12 months"


def _k(topic: str) -> dict:
    return marts.read_key_numbers(topic)


def _safe(name: str) -> pd.DataFrame | None:
    try:
        return marts.load(name).copy()
    except FileNotFoundError:
        return None


def forward_base_mrr() -> float:
    f = marts.load("fct_customer_mrr_monthly")
    return float(f.loc[f["month_end"] == AS_OF, "mrr"].sum())


# =============================================================================== measured loss rates
def _loss_12m(rows: pd.DataFrame) -> tuple[float, int]:
    """Share of starting MRR lost 12 months later for (customer_id, month_end, mrr) rows: churn = 100% lost,
    expansion does not offset (each row capped at its starting MRR) — i.e. 1 − GRR of the rows."""
    mrr = marts.load("fct_customer_mrr_monthly")[["customer_id", "month_end", "mrr"]]
    later = mrr.assign(month_end=mrr["month_end"] - pd.offsets.MonthEnd(12)).rename(columns={"mrr": "mrr_12m"})
    j = rows[["customer_id", "month_end", "mrr"]].merge(later, on=["customer_id", "month_end"], how="left").fillna({"mrr_12m": 0.0})
    if j["mrr"].sum() <= 0:
        return float("nan"), 0
    return float(1 - np.minimum(j["mrr_12m"], j["mrr"]).sum() / j["mrr"].sum()), int(len(j))


def historical_12m_loss(mask_fn) -> tuple[float, int]:
    """12-month MRR loss share for account-months selected by mask_fn at month-ends Oct 2024 – Sep 2025
    (complete 12-month windows)."""
    feat = marts.load("fct_account_month_features")
    x = feat[(feat["month_end"] >= "2024-10-31") & (feat["month_end"] <= LY)]
    return _loss_12m(x[mask_fn(x)])


def ttm_loss(mask_fn) -> tuple[float, int]:
    """12-month MRR loss share over the last 12 months (customers paying on 30 Sep 2025 → 30 Sep 2026)."""
    feat = marts.load("fct_account_month_features")
    x = feat[feat["month_end"] == LY]
    return _loss_12m(x[mask_fn(x)])


@lru_cache(maxsize=1)
def loss_rates() -> dict:
    """Measured 12-month MRR loss shares for each risk bucket (history Oct 2024 – Sep 2025, complete windows)."""
    from . import silent_churn as SC
    u, w = SC.rule_thresholds()

    def elig(d):
        return (~d["cancel_requested_open"]) & (d["contraction_months_3m"] == 0) & (d["weeks_13w"] == 13)

    def rule(d):
        f = d["utilization_13w"] < u
        if w is not None:
            f &= d["wau_trend"] <= w
        return f.fillna(False)

    def csm(d):
        return d["cs_coverage"].isin(CSM_COVERAGE)

    out = {}
    for key, fn in {
        "all_paying": lambda d: d["mrr"] > 0,
        "eligible_not_flagged": lambda d: elig(d) & ~rule(d),
        "not_flagged_csm": lambda d: elig(d) & ~rule(d) & csm(d),
        "not_flagged_digital": lambda d: elig(d) & ~rule(d) & ~csm(d),
        "early_warning_csm": lambda d: elig(d) & rule(d) & csm(d),
        "early_warning_digital": lambda d: elig(d) & rule(d) & ~csm(d),
    }.items():
        out[key], out[f"{key}_n"] = historical_12m_loss(fn)
    fl = marts.load("silent_churn__flags")
    fl = fl[(fl["month_end"] >= "2024-10-31") & (fl["month_end"] <= LY)]
    for cls in ("silent", "saved_re_risk"):
        out[cls], out[f"{cls}_n"] = _loss_12m(fl[fl["contraction_class"] == cls])
    tech = lambda d: d["industry"] == "Technology"
    annual = lambda d: d["current_period_type"] == "annual"
    for key, (a, b) in {"apr_sep_2026": ("2026-03-31", "2026-08-31"), "apr_sep_2025": ("2025-03-31", "2025-08-31")}.items():
        for lab, fn in {"tech": tech, "non_tech": lambda d: ~tech(d)}.items():
            r = renewal_loss(a, b, fn)
            out[f"renewal_loss_{lab}_{key}"], out[f"renewal_loss_{lab}_{key}_n"] = r["loss"], r["n"]
            out[f"renewal_loss_{lab}_{key}_lo"], out[f"renewal_loss_{lab}_{key}_hi"] = r["lo"], r["hi"]
            out[f"renewal_event_rate_{lab}_{key}"] = r["event_rate"]
    out["rule"] = (u, w)
    return out


def _boot_ratio(num: np.ndarray, den: np.ndarray, draws: int = 1000) -> tuple[float, float]:
    """95% percentile interval of sum(num) / sum(den) resampling rows (each row is one account)."""
    if len(den) == 0 or den.sum() <= 0:
        return float("nan"), float("nan")
    rng = np.random.default_rng(get_settings().seed)
    idx = rng.integers(0, len(den), (draws, len(den)))
    r = num[idx].sum(axis=1) / den[idx].sum(axis=1)
    return float(np.percentile(r, 2.5)), float(np.percentile(r, 97.5))


def renewal_loss(first_month_end: str, last_month_end: str, mask_fn) -> dict:
    """MRR lost at annual renewal: annual terms ending in the month after month-end t (t in the window), MRR at t vs
    MRR one month-end later (churn = 0, each account capped at its pre-renewal MRR). Each account renews at most once
    in a 6-month window, so resampling rows is an account-level bootstrap. Also the share of renewals that lost
    >= 10% of MRR or churned (the hit-rate used for ARR at stake on the CS list)."""
    f = marts.load("fct_account_month_features")
    mrr = marts.load("fct_customer_mrr_monthly")[["customer_id", "month_end", "mrr"]]
    x = f[(f["month_end"] >= first_month_end) & (f["month_end"] <= last_month_end) & (f["current_period_type"] == "annual")
          & (f["current_period_end"] > f["month_end"]) & (f["current_period_end"] <= f["month_end"] + pd.offsets.MonthEnd(1))]
    x = x[mask_fn(x)].sort_values(["customer_id", "month_end"])
    nxt = mrr.assign(month_end=mrr["month_end"] - pd.offsets.MonthEnd(1)).rename(columns={"mrr": "mrr_next"})
    j = x[["customer_id", "month_end", "mrr"]].merge(nxt, on=["customer_id", "month_end"], how="left").fillna({"mrr_next": 0.0})
    if j["mrr"].sum() <= 0:
        return {"loss": float("nan"), "n": 0, "lo": float("nan"), "hi": float("nan"), "event_rate": float("nan")}
    den = j["mrr"].to_numpy(float)
    lost = den - np.minimum(j["mrr_next"].to_numpy(float), den)
    lo, hi = _boot_ratio(lost, den)
    return {"loss": float(lost.sum() / den.sum()), "n": int(len(j)), "lo": lo, "hi": hi,
            "event_rate": float((j["mrr_next"] <= 0.9 * j["mrr"]).mean())}


def tech_quarter_rates(quarter: str) -> dict:
    """Technology expansion and contraction in a quarter as a share of Technology's own starting MRR."""
    mv = marts.load("fct_mrr_movements")
    q = marts.load("rpt_mrr_quarterly").set_index("quarter_label")
    first = pd.Timestamp(q.loc[quarter, "first_month_end"])
    x = mv[(mv["quarter_label"] == quarter) & (mv["industry"] == "Technology")]
    start = float(mv[(mv["month_end"] == first) & (mv["industry"] == "Technology")]["mrr_prev"].sum())
    return {"start_mrr": start, "expansion_rate": float(x["expansion_mrr"].sum()) / start,
            "contraction_rate": float(x["contraction_mrr"].sum()) / start}


def cohort_grr_6m() -> dict:
    """6-month GRR of first-paid cohorts Sep 2025 – Mar 2026 (complete 6-month windows): Paid Social vs other SMB
    signups. Churned customers count at MRR 0; each customer is capped at its first-month MRR."""
    f = marts.load("fct_customer_mrr_monthly")
    first = f[f["months_since_first_paid"] == 0][["customer_id", "mrr"]]
    k6 = f[f["months_since_first_paid"] == 6].merge(first, on="customer_id", suffixes=("", "_0"))
    k6 = k6[k6["cohort_month_end"].between("2025-09-30", "2026-03-31")].sort_values("customer_id")
    out = {}
    for key, mask in {"paid_social": k6["acquisition_channel"] == "Paid Social",
                      "other_smb": (k6["acquisition_channel"] != "Paid Social") & (k6["segment"] == "SMB")}.items():
        x = k6[mask]
        den = x["mrr_0"].to_numpy(float)
        kept = np.minimum(x["mrr"].to_numpy(float), den)
        lo, hi = _boot_ratio(kept, den)
        out[key] = {"grr": float(kept.sum() / den.sum()), "lo": lo, "hi": hi, "n": int(len(x))}
    return out


def expected_loss_share(wl: pd.DataFrame, rates: dict) -> pd.Series:
    """Per watchlist account: the measured 12-month MRR loss share of its bucket (an account in two buckets takes
    the higher)."""
    cls = wl["contraction_class"].map({"silent": rates["silent"], "saved_re_risk": rates["saved_re_risk"]})
    ew = np.where(wl["queue"] == "csm", rates["early_warning_csm"], rates["early_warning_digital"])
    ew = pd.Series(np.where(wl["early_warning"], ew, np.nan), index=wl.index)
    return pd.concat([cls, ew], axis=1).max(axis=1)


# =============================================================================== target lists (one play per account)
@lru_cache(maxsize=1)
def assignments() -> pd.DataFrame:
    """Every targeted account with its play, MRR today and expected 12-month loss share (risk plays)."""
    rates = loss_rates()
    feat = marts.load("fct_account_month_features")
    now = feat[feat["month_end"] == AS_OF].set_index("customer_id")
    wl = marts.load("silent_churn__watchlist").copy()
    wl["mrr"] = wl["customer_id"].map(now["mrr"])
    wl["loss_share"] = expected_loss_share(wl, rates)
    wl = wl.sort_values(["queue", "priority_rank"])
    p1 = wl[(wl["queue"] == "csm") & (wl["priority_rank"] <= CSM_CAPACITY)].assign(play="P1")
    taken = set(p1["customer_id"])
    # P3: Technology annual renewals in the next 6 months not on the CSM desk
    tech = now[(now["industry"] == "Technology") & (now["current_period_type"] == "annual")
               & (now["days_to_period_end"] <= TECH_RENEWAL_DAYS)].reset_index()
    tech = tech[~tech["customer_id"].isin(taken)]
    on_wl = wl.set_index("customer_id")["loss_share"]
    tech["loss_share"] = np.maximum(tech["customer_id"].map(on_wl).fillna(0.0), rates["renewal_loss_tech_apr_sep_2026"])
    p3 = tech[["customer_id", "mrr", "loss_share"]].assign(play="P3")
    taken |= set(p3["customer_id"])
    p2 = wl[~wl["customer_id"].isin(taken)].assign(play="P2")
    taken |= set(p2["customer_id"])
    # P4: top Basic upgrade opportunities not on any risk list
    opp = _safe("expansion__opportunities")
    p4 = pd.DataFrame(columns=["customer_id", "mrr", "play"])
    if opp is not None and len(opp):
        o = opp[~opp["customer_id"].isin(taken)].sort_values("expected_mrr", ascending=False).head(UPGRADE_LIST)
        p4 = o[["customer_id", "mrr", "propensity", "incremental_mrr_if_upgrade", "expected_mrr"]].assign(play="P4")
        taken |= set(p4["customer_id"])
    # P6: paying accounts from the Paid Social program cohorts (first paid Sep 2025 or later) not targeted above
    ps = now[(now["acquisition_channel"] == "Paid Social") & (now["cohort_month_end"] >= LY)].reset_index()
    p6 = ps[~ps["customer_id"].isin(taken)][["customer_id", "mrr"]].assign(play="P6")
    cols = ["customer_id", "play", "mrr", "loss_share"]
    parts = [d.reindex(columns=cols) for d in (p1, p2, p3, p4, p6)]
    return pd.concat(parts, ignore_index=True)


# =============================================================================== the plays
def build_plays() -> list[Play]:
    met, tr, ex, sc = _k("metrics"), _k("transitions"), _k("expansion"), _k("silent_churn")
    rates = loss_rates()
    a = assignments()
    wf = marts.load("rpt_mrr_waterfall_monthly")
    last12 = wf[wf["month_end"] > AS_OF - pd.offsets.MonthEnd(12)]
    rule = sc.get("rule", {})
    plays: list[Play] = []

    def grp(pid):
        g = a[a["play"] == pid]
        m = float(g["mrr"].sum())
        share = float((g["mrr"] * g["loss_share"]).sum() / m) if m and g["loss_share"].notna().any() else float("nan")
        return g, m, share

    # ---- P1 CSM renewal reviews -----------------------------------------------------------------
    g1, m1, s1 = grp("P1")
    wl = marts.load("silent_churn__watchlist")
    top = wl[(wl["queue"] == "csm") & (wl["priority_rank"] <= CSM_CAPACITY)]
    plays.append(Play(
        id="P1", title="CSM renewal reviews for the top 120 at-risk accounts", owner="Head of CS (named + pooled CSMs)",
        who=f"The first {len(g1)} accounts of the CSM queue in docs/account_health_list.csv (CS capacity: {CSM_CAPACITY} reviews a "
            f"quarter): annual renewals in under 45 days first, soonest first ({int((top['priority_tier'] == 1).sum())} of them), then "
            f"renewals within 120 days ({int((top['priority_tier'] == 2).sum())}) and the rest by ARR at stake; {money(m1 * 12)} ARR",
        what="Usage review 60–90 days before renewal: re-activate idle seats (admin training, re-onboard teams that stopped "
             "logging in), agree a right-sized renewal before procurement asks, line up an executive sponsor where the "
             "champion left; for Technology accounts a headcount check-in before the next billing date; for the "
             f"{int(top['suggested_play'].str.contains('Usage is growing', regex=False).sum())} accounts whose weekly active users are rising 20%+ "
             "into idle seats, confirm team size and seat count instead (no discount)",
        evidence=(f"CSM-covered accounts the utilization rule flagged lost {pct(rates['early_warning_csm'], 0)} of their MRR over the "
                  f"next 12 months vs {pct(rates['not_flagged_csm'], 0)} for unflagged CSM-covered accounts (history, Oct 2024 – Sep 2025 "
                  f"month-ends); back-test precision {pct(rule.get('precision'), 0)} vs a {pct(rule.get('base_rate'), 0)} base rate, median "
                  f"lead {rule['by_billing_period']['monthly']['lead_median']:.0f} month-ends for monthly-billed and "
                  f"{rule['by_billing_period']['annual']['lead_median']:.0f} for annual-billed accounts (the most the back-test window allows). The {sc.get('flagged_accounts', 0)} silently contracting accounts have already "
                  f"lost {money(sc.get('lost_arr', 0))} of ARR, but they showed no clear excess risk of churning or shrinking again vs comparable accounts "
                  f"({pct(sc.get('followup', {}).get('silent_loss_6m'), 0)} vs {pct(sc.get('followup', {}).get('unflagged_matched_loss_6m'), 0)} "
                  "within 6 months), so the desk ranks by the early-warning flag and the renewal date"),
        measure="Hold out 1 in 5 eligible accounts at random each quarter (business as usual); compare 6- and 12-month GRR "
                "(billing-neutral contraction + churn) of worked vs held-out accounts; report monthly to the Head of CS",
        risk="CSM time (the cap is the constraint); proactive right-sizing lowers MRR now to keep the logo; without the "
             "holdout CSMs pick the easy saves and the effect is overstated",
        addressable_mrr=m1, outcome_rate=s1, effect=(0.10, 0.20, 0.30), accounts=len(g1),
        sizing_note="Today's top 120 only; later quarters' lists are not counted (conservative)."))

    # ---- P2 digital play for the rest of the risk list ------------------------------------------
    g2, m2, s2 = grp("P2")
    plays.append(Play(
        id="P2", title="Digital re-engagement for the rest of the risk list", owner="Lifecycle Marketing + Product",
        who=f"The {len(g2):,} other accounts on the risk list (digital-coverage accounts and CSM accounts beyond the first "
            f"{CSM_CAPACITY}); {money(m2 * 12)} ARR",
        what="In-app and email nudges to admins (\"N seats unused — reassign or invite teammates\", automation templates for "
             "the account's use case); a pre-renewal email sequence 60 days before annual renewals; seat right-size prompt "
             "instead of a silent cut",
        evidence=f"Digital-coverage accounts the utilization rule flagged lost {pct(rates['early_warning_digital'], 0)} of their MRR over "
                 f"the next 12 months vs {pct(rates['not_flagged_digital'], 0)} for unflagged digital accounts (history, n = "
                 f"{rates['early_warning_digital_n']:,} flagged account-months); most are SMB self-serve accounts on monthly billing",
        measure="50/50 randomised by account; KPI = 90-day and 12-month billing-neutral contraction + churn rate",
        risk="Email programmes usually move behaviour a little; message fatigue; needs Product time for the in-app prompts",
        addressable_mrr=m2, outcome_rate=s2, effect=(0.03, 0.07, 0.12), accounts=len(g2)))

    # ---- P3 Technology renewals -----------------------------------------------------------------
    g3, m3, s3 = grp("P3")
    t25, t26 = tech_quarter_rates("2025-Q3"), tech_quarter_rates("2026-Q3")
    seg = marts.load("rpt_segment_retention_ttm")
    tnrr = seg[(seg["dimension"] == "industry") & (seg["dimension_value"] == "Technology")].set_index("month_end")["nrr"]
    r26 = rates
    plays.append(Play(
        id="P3", title="Technology renewal desk: flexible seat bands", owner="Sales (renewal AEs) + Customer Success",
        who=f"The {len(g3):,} Technology accounts on annual contracts renewing in the next 6 months that are not on the CSM desk "
            f"({money(m3 * 12)} ARR)",
        what="Offer a seat band at renewal (commit to about 80% of today's seats, add seats back at the same price during the "
             "term) in exchange for renewing annually, instead of waiting for a cut-or-churn decision; route Technology "
             "renewals to the renewal desk 90 days out",
        evidence=(f"Technology annual renewals in Apr–Sep 2026 lost {pct(r26['renewal_loss_tech_apr_sep_2026'], 0)} of their MRR at renewal "
                  f"(seat right-sizing + churn; 95% {pct(r26['renewal_loss_tech_apr_sep_2026_lo'], 0)}–{pct(r26['renewal_loss_tech_apr_sep_2026_hi'], 0)}, "
                  f"n = {r26['renewal_loss_tech_apr_sep_2026_n']}) vs {pct(r26['renewal_loss_non_tech_apr_sep_2026'], 0)} "
                  f"({pct(r26['renewal_loss_non_tech_apr_sep_2026_lo'], 0)}–{pct(r26['renewal_loss_non_tech_apr_sep_2026_hi'], 0)}) for other "
                  f"industries and {pct(r26['renewal_loss_tech_apr_sep_2025'], 0)} ({pct(r26['renewal_loss_tech_apr_sep_2025_lo'], 0)}–"
                  f"{pct(r26['renewal_loss_tech_apr_sep_2025_hi'], 0)}) for Technology a year earlier — the direction CSMs report, "
                  f"not a gap the intervals separate. Across all Technology accounts, expansion fell from {pct(t25['expansion_rate'], 1)} "
                  f"to {pct(t26['expansion_rate'], 1)} of their starting MRR a quarter and contraction rose from "
                  f"{pct(t25['contraction_rate'], 1)} to {pct(t26['contraction_rate'], 1)} (Q3 2025 → Q3 2026); their 12-month NRR "
                  f"is {pct(float(tnrr.get(AS_OF, np.nan)), 1)} ({pct(float(tnrr.get(LY, np.nan)), 1)} a year earlier)"),
        measure="Randomise the offer by renewal month (alternate months) or by account where volume allows; KPI = renewal GRR "
                "of offered vs not-offered Technology accounts; report the seats given back separately",
        risk="The band gives back MRR that some accounts would have kept; if the sector recovers the band looks expensive — "
             "limit it to one renewal cycle. Assumes the next 6 months of Technology renewals look like the last 6",
        addressable_mrr=m3, outcome_rate=s3, effect=(0.10, 0.20, 0.35), accounts=len(g3),
        effect_label="net share of the renewal loss avoided (after seats given back)", outcome_label="expected MRR loss at renewal"))

    # ---- P4 upgrade outreach ---------------------------------------------------------------------
    g4 = a[a["play"] == "P4"]
    per_list = float(ex.get("mrr_covered_per_month_at_100_all", 0.0)) if ex else 0.0
    plays.append(Play(
        id="P4", title="Premium trial for top Basic accounts by expected MRR", owner="Sales-assist + Product",
        who=f"Each quarter, the top {UPGRADE_LIST} Basic accounts on the upgrade-opportunity list that are not on the risk list "
            f"(today: {len(g4)} accounts, {money(g4['mrr'].sum() * 12)} ARR)",
        what="Unlock a 14-day Premium trial in-app, with sales-assist outreach for Mid-Market; lead with the behaviour that put "
             "the account on the list (Premium paywall hits, automation use, team growth)",
        evidence=(f"In the purged time-split back-test of the propensity ranking (Oct–Dec 2025 held out), the top {UPGRADE_LIST} accounts by propensity upgraded at {pct(ex.get('precision_at_100_all'), 1)} "
                  f"(95% {pct(ex.get('precision_at_100_all_lo'), 1)}–{pct(ex.get('precision_at_100_all_hi'), 1)}) within 90 days "
                  f"vs a {pct(ex.get('base_rate_test'), 1)} base rate ({ex.get('lift_at_100_all', float('nan')):.1f}× lift, "
                  f"{ex.get('lift_at_100_all_lo', float('nan')):.1f}–{ex.get('lift_at_100_all_hi', float('nan')):.1f}×; test AUC "
                  f"{ex.get('auc_test', float('nan')):.2f}); one propensity-ranked list of 100 brought about {money(per_list)} of upgrade MRR within 90 days on its own (average over the test months); "
                  "the P4 list orders by expected upgrade MRR (propensity × upgrade size), which was not back-tested separately, "
                  "so this is a benchmark for it, not a measured result. "
                  "Basic → Premium upgrades are small (about $200 MRR each), so this is a cheap play, not a big one") if ex else
                 "Upgrade signals precede upgrades",
        measure="Randomised: 70% of each quarter's list gets the trial + outreach, 30% holdout; KPI = 90-day voluntary upgrade "
                "rate and 6-month retention of upgraders (incremental upgrades, not raw upgrades)",
        risk="Pulling forward upgrades that would have happened anyway (only the holdout shows the increment); discount "
             "expectations if Sales leads with price",
        addressable_mrr=4 * per_list, outcome_rate=1.0, effect=(0.15, 0.30, 0.50), accounts=len(g4),
        effect_label="relative lift in the upgrade rate",
        addressable_label="upgrade MRR four quarterly lists bring without the play (propensity-ranking back-test benchmark)",
        sizing_note="The P4 list orders by expected upgrade MRR (propensity × upgrade size), which was not back-tested separately; "
                    "the propensity-ranking back-test is a benchmark for it, not a measured result."))

    # ---- P5 packaging follow-up ------------------------------------------------------------------
    # foregone upgrade MRR a month if the April step is due to the change, measured from the pre-April level (the raw
    # fall against the 12-month pre-window average includes the earlier drift and would overstate it)
    foregone = -float(tr.get("foregone_upgrade_mrr_from_pre_april_level", 0.0)) if tr else 0.0
    foregone_trend = -float(tr.get("foregone_upgrade_mrr_trend_adjusted", 0.0)) if tr else 0.0
    plays.append(Play(
        id="P5", title="Basic packaging: test a capped version before deciding", owner="Product / Pricing",
        who="New Basic signups from Jan 2027 (50/50 test for two quarters); then, if the test wins, every Basic account from Aug 2027",
        what="Keep advanced automations in Basic but cap them (e.g. a monthly run limit), or move analytics dashboards behind "
             "the Premium gate; decide in Jul 2027 on the signups with a complete 90-day window (the Jan–Apr cohorts), "
             "confirm on 6-month revenue per new Basic signup in Dec 2027",
        evidence=(f"Outside Paid Social the voluntary Basic → Premium rate was already drifting down before April "
                  f"({pct(tr.get('non_ps_rate_apr_sep_2025'), 1)} a month in Apr–Sep 2025, {pct(tr.get('non_ps_rate_oct_2025_mar_2026'), 1)} in "
                  f"Oct 2025 – Mar 2026) and stepped down to {pct(tr.get('upgrade_rate_non_ps_post'), 2)} after it; "
                  f"{abs(tr.get('non_ps_step_net_of_trend_pts', 0)) * 100:.1f}–{abs(tr.get('non_ps_step_from_pre_april_level_pts', 0)) * 100:.1f} "
                  f"points of that coincide with the change (drift continued vs rate held at its pre-April level). Channel mix explains "
                  f"{pct(tr.get('mix_share_of_change'), 0)} of the fall and the retention upside is not distinguishable from zero, so the "
                  f"step is worth about {money(foregone_trend)}–{money(foregone)} of upgrade MRR a month — if it is due to the change") if tr else
                 "Upgrades fell around the April change",
        measure="Provisional read in Jul 2027 on the 90-day upgrade rate and early churn of the Jan–Apr 2027 signups in each arm; full "
                "read on 6-month revenue per new Basic signup (upgrade MRR + retained Basic MRR) in Dec 2027; guardrail: trial → "
                "paid conversion of the test cell",
        risk="Losing the conversion / retention gain that motivated the change; backlash if existing Basic accounts lose a "
             "feature — test on new signups first. New signups are not in next year's NRR base, so only the roll-out "
             "months count here",
        addressable_mrr=foregone * ROLLOUT_MONTHS_IN_WINDOW, outcome_rate=1.0, effect=(0.20, 0.40, 0.60), accounts=0,
        effect_label="share of the foregone upgrade MRR recovered",
        addressable_label=(f"foregone upgrade MRR in the {ROLLOUT_MONTHS_IN_WINDOW} roll-out months inside the window ({money(foregone)} a "
                           f"month from the pre-April level; {money(foregone_trend)} if the drift would have continued)"),
        run_rate_mrr_per_year=foregone * 12 * 0.40,
        sizing_note=f"Small inside next year's NRR window because the test comes first; once rolled out it is worth about "
                    f"{money(foregone * 12 * 0.40)} of MRR a year at the base effect."))

    # ---- P6 Paid Social cohorts ------------------------------------------------------------------
    g6 = a[a["play"] == "P6"]
    cg = cohort_grr_6m()
    gap = max(cg["other_smb"]["grr"] - cg["paid_social"]["grr"], 0.0)
    ttu = tr.get("time_to_upgrade_12m_by_channel", {}) if tr else {}
    plays.append(Play(
        id="P6", title="Paid Social cohorts: activation and annual offer", owner="Growth Marketing + Lifecycle",
        who=f"The {len(g6):,} paying accounts from the Paid Social program cohorts (first paid Sep 2025 or later; "
            f"{money(g6['mrr'].sum() * 12)} ARR) and every new Paid Social signup",
        what="Activation sequence in the first 30 days (first automation, invite teammates), annual-plan offer at day 30, and "
             "exclusion of the audiences with the weakest 6-month revenue retention from targeting",
        evidence=(f"6-month GRR of the Sep 2025 – Mar 2026 Paid Social cohorts {pct(cg['paid_social']['grr'], 0)} "
                  f"(95% {pct(cg['paid_social']['lo'], 0)}–{pct(cg['paid_social']['hi'], 0)}, n = {cg['paid_social']['n']}) vs "
                  f"{pct(cg['other_smb']['grr'], 0)} ({pct(cg['other_smb']['lo'], 0)}–{pct(cg['other_smb']['hi'], 0)}) for other SMB "
                  f"signups of the same months; 12 months after signup {pct(ttu.get('Paid Social'), 0)} had upgraded vs "
                  f"{pct(min([v for c, v in ttu.items() if c != 'Paid Social'] or [0]), 0)}–"
                  f"{pct(max([v for c, v in ttu.items() if c != 'Paid Social'] or [0]), 0)} for other channels"),
        measure="Alternate signup weeks get the new onboarding (holdout by week); KPI = 6-month revenue retained per $1 of "
                "first-month MRR by channel cohort, which Marketing weighs against CAC",
        risk="Tighter targeting lowers signup volume; the budget trade-off needs CAC (not in this data). Most of the value "
             "lands on future cohorts, outside next year's NRR base",
        addressable_mrr=float(g6["mrr"].sum()), outcome_rate=gap, effect=(0.20, 0.35, 0.50), accounts=len(g6),
        effect_label="share of the retention gap to other SMB closed",
        outcome_label="excess 6-month MRR loss vs other SMB signups",
        addressable_label="MRR of program-cohort accounts paying today",
        sizing_note=("The 0→6-month gap of fresh cohorts stands in for the survivors' 12-month excess loss (most program cohorts are "
                     "not 12 months old yet). It is a proxy, not a floor: excess churn is front-loaded, so it may overstate.")))

    # ---- P7 dunning ------------------------------------------------------------------------------
    invol = float(last12["churn_mrr_involuntary"].sum())
    rec_rate = _dunning_recovery_rate()
    plays.append(Play(
        id="P7", title="Payment-failure recovery (dunning)", owner="Billing Operations / RevOps",
        who="Monthly card-paying accounts whose renewal charge fails",
        what="Smart retry schedule, automatic card updater, in-app banner and admin email before the 14-day cut-off",
        evidence=f"Failed card payments cost {money(invol)} MRR of churn in the last 12 months "
                 f"({pct(invol / max(last12['churn_mrr'].sum(), 1e-9), 0)} of churned MRR); {pct(rec_rate, 0)} of failed "
                 "recurring charges are recovered within the dunning window today (invoices, last 12 months)",
        measure="A/B test of the retry schedule; KPI = recovery rate within the dunning window",
        risk="Low; card-network fees for the updater",
        addressable_mrr=invol, outcome_rate=1.0, effect=(0.15, 0.30, 0.45), accounts=0,
        effect_label="share of payment-failure churn recovered",
        addressable_label="involuntary churn MRR in the last 12 months"))
    return plays


def _cs_trigger() -> dict:
    """Head of CS decision rule (scope §3): do the first 120 CSM-covered accounts hold >= 60% of the list's ARR at stake?"""
    wl = marts.load("silent_churn__watchlist")
    tot = float(wl["arr_at_stake"].sum())
    csm = wl[wl["queue"] == "csm"]
    by_priority = float(csm.sort_values("priority_rank").head(CSM_CAPACITY)["arr_at_stake"].sum()) / tot
    by_stake = float(csm.nlargest(CSM_CAPACITY, "arr_at_stake")["arr_at_stake"].sum()) / tot
    return {"threshold": 0.60, "share_by_priority": by_priority, "share_by_arr_at_stake": by_stake,
            "met": bool(min(by_priority, by_stake) >= 0.60), "arr_at_stake_total": tot}


def _dunning_recovery_rate() -> float:
    inv = marts.load("stg_invoices")
    x = inv[(inv["line_type"] == "recurring") & (inv["invoice_date"] > LY) & (inv["payment_attempts"] > 1)]
    done = x[x["status"].isin(["paid", "uncollectible"])]
    return float((done["status"] == "paid").mean()) if len(done) else float("nan")


def plays_table(plays: list[Play] | None = None, overrides: dict | None = None) -> pd.DataFrame:
    plays = plays or build_plays()
    base = forward_base_mrr()
    overrides = overrides or {}
    rows = []
    for p in plays:
        o = overrides.get(p.id, {})
        reach, eff = o.get("reach", p.reach), o.get("effect", p.effect[1])
        mrr_base = p.impact_mrr(reach, eff)
        rows.append({
            "id": p.id, "play": p.title, "owner": p.owner, "who": p.who, "what": p.what, "evidence": p.evidence,
            "measure": p.measure, "risk": p.risk, "accounts": p.accounts, "addressable_mrr": p.addressable_mrr,
            "outcome_rate": p.outcome_rate, "outcome_label": p.outcome_label, "effect_low": p.effect[0],
            "effect_base": p.effect[1], "effect_high": p.effect[2], "effect_used": eff, "reach": reach,
            "effect_label": p.effect_label, "addressable_label": p.addressable_label, "sizing_note": p.sizing_note,
            "sizing_text": p.sizing_text(reach, eff), "run_rate_mrr_per_year": p.run_rate_mrr_per_year,
            "impact_mrr_low": p.impact_mrr(reach, p.effect[0]), "impact_mrr": mrr_base,
            "impact_mrr_high": p.impact_mrr(reach, p.effect[2]),
            "nrr_pts_low": p.impact_mrr(reach, p.effect[0]) / base, "nrr_pts": mrr_base / base,
            "nrr_pts_high": p.impact_mrr(reach, p.effect[2]) / base,
        })
    return pd.DataFrame(rows)


def build() -> dict[str, pd.DataFrame]:
    t = plays_table()
    marts.write(f"{TOPIC}__plays", t)
    a = assignments()
    marts.write(f"{TOPIC}__assignments", a)
    return {f"{TOPIC}__plays": t, f"{TOPIC}__assignments": a}


# share of each play's population kept as the control group in its measurement design (P5 is sized only on the roll-out
# months after its test, so no holdout applies inside the window)
HOLDOUT_SHARE = {"P1": 0.20, "P2": 0.50, "P3": 0.50, "P4": 0.30, "P5": 0.0, "P6": 0.50, "P7": 0.50}


def holdout_adjusted_totals(t: pd.DataFrame) -> dict:
    """Play sizes assume full reach. While a control group runs, its share gets no effect: this gives the total if every
    holdout is kept all year, and if each holdout reads out after two quarters and the play then reaches everyone."""
    h = t["id"].map(HOLDOUT_SHARE).fillna(0.0)
    return {"holdout_share": HOLDOUT_SHARE,
            "total_nrr_pts_holdouts_all_year": float((t["nrr_pts"] * (1 - h)).sum()),
            "total_nrr_pts_holdouts_two_quarters": float((t["nrr_pts"] * (1 - h / 2)).sum())}


def key_numbers() -> dict:
    t = marts.load(f"{TOPIC}__plays")
    r = loss_rates()
    return {
        **holdout_adjusted_totals(t),
        "forward_base_mrr": forward_base_mrr(),
        "csm_capacity_per_quarter": CSM_CAPACITY,
        "total_impact_mrr_base": float(t["impact_mrr"].sum()), "total_impact_mrr_low": float(t["impact_mrr_low"].sum()),
        "total_impact_mrr_high": float(t["impact_mrr_high"].sum()),
        "total_impact_arr_base": float(t["impact_mrr"].sum() * 12),
        "total_nrr_pts_base": float(t["nrr_pts"].sum()), "total_nrr_pts_low": float(t["nrr_pts_low"].sum()),
        "total_nrr_pts_high": float(t["nrr_pts_high"].sum()),
        "loss_rates": {k: (float(v) if isinstance(v, (int, float, np.floating, np.integer)) else None)
                       for k, v in r.items() if k != "rule"},
        "dunning_recovery_rate": _dunning_recovery_rate(),
        "head_of_cs_trigger": _cs_trigger(),
        "tech_rates": {"q3_2025": tech_quarter_rates("2025-Q3"), "q3_2026": tech_quarter_rates("2026-Q3")},
        "cohort_grr_6m": cohort_grr_6m(),
        "plays": [{k: (float(v) if isinstance(v, (int, float, np.floating, np.integer)) else v) for k, v in row.items()}
                  for row in t[["id", "play", "owner", "accounts", "addressable_mrr", "outcome_rate", "effect_low", "effect_base",
                                "effect_high", "reach", "impact_mrr", "impact_mrr_low", "impact_mrr_high", "nrr_pts",
                                "nrr_pts_low", "nrr_pts_high"]].to_dict("records")],
    }


# =============================================================================== figures
def fig_impact(t: pd.DataFrame | None = None) -> go.Figure:
    """Expected NRR impact per play: base case bar with the low-high range of the effect assumption."""
    t = (t if t is not None else marts.load(f"{TOPIC}__plays")).sort_values("nrr_pts")
    labels = [f"{r.id} {r.play}" for r in t.itertuples()]
    fig = go.Figure(go.Bar(
        y=labels, x=t["nrr_pts"] * 100, orientation="h", marker=dict(color=theme.ACCENT),
        error_x=dict(type="data", symmetric=False, array=(t["nrr_pts_high"] - t["nrr_pts"]) * 100,
                     arrayminus=(t["nrr_pts"] - t["nrr_pts_low"]) * 100, color=theme.INK_2, thickness=1.5, width=4),
        customdata=np.c_[t["impact_mrr"] * 12, t["nrr_pts_low"] * 100, t["nrr_pts_high"] * 100],
        hovertemplate="%{y}<br>+%{x:.2f} NRR pts (range %{customdata[1]:.2f}–%{customdata[2]:.2f})<br>"
                      "≈ %{customdata[0]:$,.0f} ARR<extra></extra>"))
    for lab, v, h in zip(labels, t["nrr_pts"], t["nrr_pts_high"]):   # value label past the end of the whisker
        fig.add_annotation(x=h * 100, y=lab, text=f"<b>+{v * 100:.2f}</b> pts", showarrow=False, xanchor="left", xshift=8,
                           font=dict(color=theme.INK, size=13))
    hi = float((t["nrr_pts_high"] * 100).max())
    fig.update_xaxes(title_text="NRR points (next 12 months)", showgrid=True, gridcolor=theme.GRID, range=[0, hi * 1.22])
    fig.update_yaxes(showgrid=False, automargin=True)
    fig.update_layout(showlegend=False, bargap=0.45, margin=dict(l=40, r=40))
    total, lo, hi_t = t["nrr_pts"].sum(), t["nrr_pts_low"].sum(), t["nrr_pts_high"].sum()
    top2 = t.sort_values("nrr_pts", ascending=False).head(2)
    share = top2["nrr_pts"].sum() / total if total else 0
    names = " and ".join(top2["id"])
    return theme.apply(fig, f"Together the plays are worth about +{total * 100:.1f} NRR points next year (range +{lo * 100:.1f} to "
                            f"+{hi_t * 100:.1f}); {names} are {pct(share, 0)} of it",
                       "Expected impact on next year's NRR per play, base case with the low–high range of the effect assumption. "
                       "Addressable MRR and loss rates are measured; effects are assumptions until each holdout measures them",
                       height=max(380, 100 + 48 * len(t)))
