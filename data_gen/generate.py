"""Generate the synthetic SaaS billing + product-usage dataset.

Implements the rules in docs/data_model.md with parameters from data_gen/params.yaml.
Writes six source tables (parquet) plus a manifest and the planted-effects answer key:

    plans, customers, subscriptions, plan_changes, invoices, usage_events

Usage:
    python data_gen/generate.py                 # writes to <data_dir>/raw from config.yaml
    python data_gen/generate.py --out /tmp/raw  # custom output folder

Determinism: one master seed (config.yaml) -> numpy SeedSequence children. Every account
is simulated with its own child stream, so results do not depend on processing order.
"""
from __future__ import annotations

import argparse
import calendar as pycal
import hashlib
import json
import math
import sys
import time
from collections import deque
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from saas_rev.config import get_settings  # noqa: E402

GENERATOR_VERSION = "1.1"
PLAN_TIER = {"basic": 1, "premium": 2, "enterprise": 3}
TIER_PLAN = {v: k for k, v in PLAN_TIER.items()}
SEGMENTS = ["SMB", "Mid-Market", "Enterprise"]
CHANNELS = ["Organic", "Paid Search", "Paid Social", "Partner", "Outbound Sales", "Events"]
INDUSTRIES = [
    "Technology", "Professional Services", "Financial Services", "Retail & E-commerce",
    "Healthcare", "Manufacturing", "Media & Marketing", "Education & Non-profit",
]
REGIONS = ["North America", "EMEA", "APAC"]
COVERAGE = {"SMB": "digital", "Mid-Market": "pooled_csm", "Enterprise": "named_csm"}
SEGMENT_BANDS = {"SMB": (1, 100), "Mid-Market": (101, 1000), "Enterprise": (1001, 10**7)}
EVENT_NAMES = [
    "session", "core_action", "automation_run", "report_view",
    "integration_sync", "user_invite", "premium_feature_gate",
]


# --------------------------------------------------------------------------- helpers
def d(s: str) -> date:
    return date.fromisoformat(str(s))


def add_months(day: date, n: int, anchor_day: int) -> date:
    """Same billing anchor day n months later, clipped to the month's last day (29-31 -> month end)."""
    y, m0 = divmod(day.month - 1 + n, 12)
    y += day.year
    m = m0 + 1
    return date(y, m, min(anchor_day, pycal.monthrange(y, m)[1]))


def sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def month_starts(first: date, last: date) -> list[date]:
    out, cur = [], date(first.year, first.month, 1)
    while cur <= last:
        out.append(cur)
        cur = add_months(cur, 1, 1)
    return out


def quarter_factor(month: int, f: dict) -> float:
    """Sales-led seasonality: deals cluster in the last month of each quarter."""
    if month in (3, 6, 9, 12):
        return f["end"]
    if month in (1, 4, 7, 10):
        return f["start"]
    return 1.0


# --------------------------------------------------------------------------- calendar
class Calendar:
    """Weekly simulation clock with precomputed seasonal / scenario multipliers."""

    def __init__(self, P: dict):
        self.ga = d(P["calendar"]["ga_date"])
        self.cutoff = d(P["calendar"]["cutoff_date"])
        self.week_starts: list[date] = []
        ws = self.ga
        while ws <= self.cutoff:
            self.week_starts.append(ws)
            ws += timedelta(days=7)
        self.n_weeks = len(self.week_starts)
        S = P["seasonality"]
        sc = P["scenario"]
        self.pkg_date = d(sc["packaging_change"]["date"])
        fz_start = d(sc["tech_freeze"]["start"])
        fz_weeks = sc["tech_freeze"]["phase_in_weeks"]

        self.month = np.array([w.month for w in self.week_starts])
        # the packaging change applies from the first week whose majority falls after the change date
        self.pkg_after = np.array([(w + timedelta(days=3)) >= self.pkg_date for w in self.week_starts])
        self.freeze = np.array(
            [min(max((w - fz_start).days / (7 * fz_weeks), 0.0), 1.0) if w >= fz_start else 0.0
             for w in self.week_starts]
        )
        qf = P["dynamics"]["sales_quarter_factor"]
        self.qfactor = np.array([quarter_factor((w + timedelta(days=3)).month, qf) for w in self.week_starts])
        self.usage_season: dict[str, np.ndarray] = {}
        self.hiring: dict[str, np.ndarray] = {}
        for ind in INDUSTRIES:
            us, hi = [], []
            for w in self.week_starts:
                days = [w + timedelta(days=k) for k in range(7)]
                holiday = any((x.month == 12 and x.day >= 24) or (x.month == 1 and x.day == 1) for x in days)
                mid = w + timedelta(days=3)
                if holiday:
                    f = S["holiday_usage"]
                elif ind == "Education & Non-profit" and ((mid.month == 6 and mid.day >= 15) or mid.month in (7, 8)):
                    f = S["education_summer_usage"]
                elif mid.month in (7, 8):
                    f = S["summer_usage"]
                elif ind == "Retail & E-commerce" and mid.month in (11, 12):
                    f = S["retail_q4_usage"]
                else:
                    f = 1.0
                us.append(f)
                h = S["hiring_adj_by_month"].get(mid.month, 0.0)
                if ind == "Retail & E-commerce":
                    h += S["retail_hiring"].get(mid.month, 0.0)
                if ind == "Education & Non-profit":
                    h += S["education_hiring"].get(mid.month, 0.0)
                hi.append(h)
            self.usage_season[ind] = np.array(us)
            self.hiring[ind] = np.array(hi)

    def week_of(self, day: date) -> int:
        return (day - self.ga).days // 7


# --------------------------------------------------------------------------- names
_PREFIX = ["North", "Blue", "Bright", "Silver", "Cedar", "Iron", "Maple", "Summit", "Harbor", "Pine",
           "Clear", "Stone", "Swift", "Golden", "Red", "Oak", "River", "Lake", "Sun", "Star", "Peak",
           "Prime", "True", "Open", "Next", "Bold", "Keen", "Vivid", "Ever", "Arc", "Copper", "Granite",
           "Willow", "Aspen", "Coral", "Echo", "Falcon", "Juniper", "Lumen", "Meridian"]
_CORE = ["field", "line", "view", "path", "point", "ridge", "wave", "gate", "bridge", "works", "mark",
         "craft", "light", "forge", "stack", "loop", "shift", "scale", "grid", "hub", "port", "vale",
         "crest", "stream", "wood", "spark", "frame", "nest", "bay", "rock"]
_SUFFIX = {
    "Technology": ["Labs", "Software", "Systems", "AI", "Cloud", "Data", "Technologies"],
    "Professional Services": ["Consulting", "Advisory", "Group", "Associates", "Legal", "Partners"],
    "Financial Services": ["Capital", "Financial", "Insurance", "Credit Union", "Wealth", "Payments"],
    "Retail & E-commerce": ["Goods", "Supply", "Outfitters", "Market", "Commerce", "Brands"],
    "Healthcare": ["Health", "Clinics", "Medical", "Care", "Bio", "Therapeutics"],
    "Manufacturing": ["Industries", "Manufacturing", "Components", "Fabrication", "Materials"],
    "Media & Marketing": ["Media", "Studios", "Creative", "Agency", "Publishing"],
    "Education & Non-profit": ["Academy", "Learning", "Foundation", "Institute", "Schools"],
}


def make_names(industries: list, rng: np.random.Generator) -> list[str]:
    used: set[str] = set()
    out = []
    for ind in industries:
        for attempt in range(60):
            name = (f"{_PREFIX[rng.integers(len(_PREFIX))]}{_CORE[rng.integers(len(_CORE))]} "
                    f"{_SUFFIX[ind][rng.integers(len(_SUFFIX[ind]))]}")
            if attempt > 25:
                name += f" {rng.integers(2, 99)}"
            if name not in used:
                used.add(name)
                out.append(name)
                break
    return out


def domain_of(name: str) -> str:
    return "".join(ch for ch in name.lower() if ch.isalnum())[:40] + ".example"


# --------------------------------------------------------------------------- acquisition
def _initial_plan(seg: str, seats: int, rng: np.random.Generator, I: dict) -> str:
    """Plan conditional on seats (data_model.md §5.2)."""
    rules = I["plan_by_seats"][seg]
    for band in rules:
        if seats <= band["max_seats"]:
            mix = band["mix"]
            return str(rng.choice(list(mix.keys()), p=list(mix.values())))
    raise ValueError(f"no plan band for {seg} {seats}")


def sample_accounts(P: dict, cal: Calendar, rng: np.random.Generator) -> pd.DataFrame:
    A, I = P["acquisition"], P["initial"]
    L = P["latent"]
    pk = P["scenario"]["packaging_change"]
    rows = []
    months = month_starts(cal.ga, cal.cutoff)
    ps_start = d(A["paid_social_program"]["start"])
    conv_from = d(pk["new_logo_uplift"]["from_month"] + "-01")
    for m_idx, ms in enumerate(months):
        trend = A["base_new_per_month"] * (1 + A["monthly_growth"]) ** m_idx * A["season"][ms.month]
        q_pos = (ms.month - 1) % 3
        for seg in SEGMENTS:
            for ch in CHANNELS:
                share = A["segment_share"][seg] * A["channel_share"][seg][ch]
                if share <= 0:
                    continue
                mult = 1.0
                if ch in A["sales_led_channels"]:
                    mult = A["quarter_end_mult"] if q_pos == 2 else (A["quarter_start_mult"] if q_pos == 0 else 1.0)
                if (ms >= conv_from and seg in pk["new_logo_uplift"]["segments"]
                        and ch in pk["new_logo_uplift"]["channels"]):
                    mult *= pk["new_logo_uplift"]["mult"]   # S3 upside: better trial -> paid conversion
                for _ in range(rng.poisson(trend * share * mult)):
                    rows.append({"month": ms, "segment": seg, "channel": ch, "is_design_partner": False})
        if ms >= ps_start:
            extra = A["paid_social_program"]["extra_per_month"]
            lam = extra.get(ms.strftime("%Y-%m"), extra["default"])
            for _ in range(rng.poisson(lam)):
                rows.append({"month": ms, "segment": "SMB", "channel": "Paid Social", "is_design_partner": False})
    dp = A["design_partners"]
    dp_segs = rng.choice(SEGMENTS, size=dp["count"], p=[dp["segment_share"][s] for s in SEGMENTS])
    for seg in dp_segs:
        rows.append({"month": months[0], "segment": seg, "channel": "Organic", "is_design_partner": True})

    df = pd.DataFrame(rows)
    n = len(df)
    # signup day (sales-led deals close on business days, clustered at quarter end)
    signup = []
    for ms, ch, is_dp in zip(df["month"], df["channel"], df["is_design_partner"]):
        lo = max(ms, cal.ga)
        hi = min(add_months(ms, 1, 1) - timedelta(days=1), cal.cutoff)
        if is_dp:
            hi = cal.ga + timedelta(days=13)
        sales_led = ch in A["sales_led_channels"]
        if sales_led and ms.month % 3 == 0 and rng.random() < A["quarter_end_last_week_share"]:
            lo = max(lo, hi - timedelta(days=6))
        day = lo + timedelta(days=int(rng.integers(0, (hi - lo).days + 1)))
        if sales_led:
            while day.weekday() >= 5 and day - timedelta(days=1) >= lo:
                day -= timedelta(days=1)
        signup.append(day)
    df["signup_date"] = signup

    # firmographics
    df["industry"] = [str(rng.choice(INDUSTRIES, p=[A["industry_share"][s][i] for i in INDUSTRIES]))
                      for s in df["segment"]]
    df["region"] = [str(x) for x in rng.choice(REGIONS, size=n, p=[A["region_share"][r] for r in REGIONS])]
    emp = []
    for s in df["segment"]:
        e = A["employees"][s]
        emp.append(int(np.clip(round(e["median"] * math.exp(e["sigma"] * rng.standard_normal())), e["min"], e["max"])))
    df["employee_count"] = emp

    # latent traits
    ea = A["early_adopters"]
    ea_months = {d(m + "-01") for m in ea["months"]}
    df["fit"] = [
        L["fit_mean"] + L["fit_channel_adj"][ch] + (dp["fit_adj"] if dpf else 0.0)
        + (ea["fit_adj"] if (ms in ea_months and not dpf) else 0.0)
        + L["fit_sd"] * rng.standard_normal()
        for ch, dpf, ms in zip(df["channel"], df["is_design_partner"], df["month"])
    ]
    df["power"] = [
        rng.standard_normal() + L["power_segment_adj"][s] + L["power_channel_adj"].get(ch, 0.0)
        for s, ch in zip(df["segment"], df["channel"])
    ]
    df["growth"] = [rng.standard_normal() + L["growth_industry_adj"].get(i, 0.0) for i in df["industry"]]

    # initial subscription terms: seats first, then plan conditional on seats
    plans = P["plans"]
    seats, plan, billing, disc, fee, pay = [], [], [], [], [], []
    for s, ch in zip(df["segment"], df["channel"]):
        sp = I["seats"][s]
        med = I["paid_social_smb_seat_median"] if (s == "SMB" and ch == "Paid Social") else sp["median"]
        st = int(np.clip(round(med * math.exp(sp["sigma"] * rng.standard_normal())), sp["min"], sp["max"]))
        p = _initial_plan(s, st, rng, I)
        st = max(st, plans[p]["min_seats"])
        if plans[p]["max_seats"]:
            st = min(st, plans[p]["max_seats"])
        if p == "enterprise":
            b = "annual"
        else:
            ann = I["paid_social_annual_share"] if (s == "SMB" and ch == "Paid Social") else I["annual_share"][s]
            b = "annual" if rng.random() < ann else "monthly"
        sales_led = ch in A["sales_led_channels"]
        dsc = 0.0
        if p == "enterprise":
            lo, hi = I["enterprise_discount"]
            dsc = round(float(rng.uniform(lo, hi)), 2)
        elif p == "premium" and b == "annual" and sales_led and s in ("Mid-Market", "Enterprise"):
            if rng.random() < I["sales_led_premium_discount"]["prob"]:
                lo, hi = I["sales_led_premium_discount"]["range"]
                dsc = round(float(rng.uniform(lo, hi)), 2)
        f = 0.0
        if p == "enterprise":
            lo, hi = I["onboarding_fee"]["enterprise_plan"]
            f = round(float(rng.uniform(lo, hi)) / 500) * 500
        elif s == "Mid-Market" and sales_led and p == "premium" and b == "annual":
            if rng.random() < I["onboarding_fee"]["mid_market_sales_led_premium"]["prob"]:
                lo, hi = I["onboarding_fee"]["mid_market_sales_led_premium"]["range"]
                f = round(float(rng.uniform(lo, hi)) / 100) * 100
        seats.append(st)
        plan.append(p)
        billing.append(b)
        disc.append(dsc)
        fee.append(f)
        pay.append("card" if (b == "monthly" and not sales_led) else "invoice")
    df["initial_seats"], df["initial_plan"], df["initial_billing"] = seats, plan, billing
    df["initial_discount"], df["onboarding_fee"], df["payment_method"] = disc, fee, pay

    # account metadata
    created = []
    for sd, ch in zip(df["signup_date"], df["channel"]):
        lo, hi = A["trial_days"]["sales_led" if ch in A["sales_led_channels"] else "self_serve"]
        created.append(sd - timedelta(days=int(rng.integers(lo, hi + 1))))
    df["created_at"] = created
    df["cs_coverage"] = df["segment"].map(COVERAGE)
    owners = []
    for cov in df["cs_coverage"]:
        if cov == "named_csm":
            owners.append(f"CSM-{int(rng.integers(1, 5)):02d}")
        elif cov == "pooled_csm":
            owners.append(f"CSM-{int(rng.integers(5, 11)):02d}")
        else:
            owners.append(None)
    df["csm_owner"] = owners
    tx = I["tax_rate"]
    df["tax_rate"] = [
        0.0 if (r == "North America" and rng.random() < I["tax_exempt_share_na"]) else tx[r] for r in df["region"]
    ]
    df["role"] = "customer"

    # S5: designate the one-off rollout account (the largest-seat Financial Services Enterprise account
    # signed in the window; its terms are set so the rollout is large relative to quarterly expansion)
    ro = P["scenario"]["rollout"]
    w_lo, w_hi = d(ro["signup_window"][0]), d(ro["signup_window"][1])
    cand = df[(df["segment"] == "Enterprise") & (df["signup_date"] >= w_lo) & (df["signup_date"] <= w_hi)]
    fs = cand[cand["industry"] == ro["industry"]].sort_values(["initial_seats", "signup_date"], ascending=[False, True])
    pick = fs.index[0] if len(fs) else cand.sort_values("signup_date").index[0]
    df.loc[pick, ["industry", "initial_plan", "initial_billing", "initial_seats", "initial_discount",
                  "payment_method", "onboarding_fee", "role"]] = [
        ro["industry"], "enterprise", "annual", ro["initial_seats"], ro["discount"], "invoice", 15000.0, "rollout"]
    df.loc[pick, "fit"] = 2.0

    # DQ: internal / test accounts
    internal = []
    for ia in P["dq"]["internal_accounts"]:
        sd = cal.ga + timedelta(days=int(rng.integers(20, (cal.cutoff - cal.ga).days - 90)))
        seg = "Enterprise" if ia["plan"] == "enterprise" else "SMB"
        internal.append({
            "month": date(sd.year, sd.month, 1), "segment": seg, "channel": "Organic", "is_design_partner": False,
            "signup_date": sd, "industry": "Technology", "region": "North America", "employee_count": 250,
            "fit": 1.5, "power": 1.0, "growth": 0.0, "initial_seats": ia["seats"], "initial_plan": ia["plan"],
            "initial_billing": "annual" if ia["plan"] == "enterprise" else "monthly",
            "initial_discount": ia["discount"], "onboarding_fee": 0.0, "payment_method": "invoice",
            "created_at": sd - timedelta(days=1), "cs_coverage": "digital", "csm_owner": None, "tax_rate": 0.0,
            "role": "internal", "company_name": ia["name"], "email_domain": P["dq"]["internal_domain"],
        })
    df = pd.concat([df, pd.DataFrame(internal)], ignore_index=True)

    # names and domains for real accounts
    real = df["role"] != "internal"
    df.loc[real, "company_name"] = make_names(list(df.loc[real, "industry"]), rng)
    df.loc[real, "email_domain"] = df.loc[real, "company_name"].map(domain_of)

    # DQ: missing and inconsistent firmographics
    Q = P["dq"]
    self_serve_smb = (df["segment"] == "SMB") & (~df["channel"].isin(A["sales_led_channels"])) & real
    miss_ind = self_serve_smb & (rng.random(len(df)) < Q["missing_industry_share_smb_self_serve"])
    df["industry_reported"] = df["industry"].where(~miss_ind, None)
    emp_rep = df["employee_count"].astype("Int64").copy()
    mismatch = real & (rng.random(len(df)) < Q["segment_employee_mismatch_share"])
    for i in np.flatnonzero(mismatch.to_numpy()):
        seg = df.at[i, "segment"]
        # employee count updated later by enrichment and now sits in the neighbouring band
        if seg == "SMB":
            emp_rep.iloc[i] = int(rng.integers(101, 180))
        elif seg == "Mid-Market":
            emp_rep.iloc[i] = int(rng.integers(60, 100)) if rng.random() < 0.5 else int(rng.integers(1001, 1400))
        else:
            emp_rep.iloc[i] = int(rng.integers(700, 1000))
    miss_emp = real & (rng.random(len(df)) < Q["missing_employee_count_share"])
    df["employee_count_reported"] = emp_rep.where(~miss_emp, pd.NA)

    df = df.sort_values(["signup_date", "company_name"]).reset_index(drop=True)
    df["customer_id"] = [f"CUS-{i + 1:06d}" for i in range(len(df))]
    return df


# --------------------------------------------------------------------------- account simulation
class AccountSim:
    """Week-by-week lifecycle of one account (see docs/data_model.md §5.4)."""

    def __init__(self, acc: dict, P: dict, cal: Calendar, rng: np.random.Generator):
        self.a, self.P, self.cal, self.rng = acc, P, cal, rng
        self.D = P["dynamics"]
        self.S = P["scenario"]
        self.plans = P["plans"]
        self.seg, self.ind, self.ch = acc["segment"], acc["industry"], acc["channel"]
        self.cov = acc["cs_coverage"]
        self.mu, self.power, self.growth = acc["fit"], acc["power"], acc["growth"]
        self.role = acc["role"]
        self.protect = self.role in ("rollout", "internal")
        self.frozen = self.role == "internal"
        self.is_tech = self.ind == self.S["tech_freeze"]["industry"]
        self.sales_led = self.ch in P["acquisition"]["sales_led_channels"]
        # outputs
        self.subs: list[dict] = []
        self.changes: list[dict] = []
        self.invoices: list[dict] = []
        self.weeks: list[tuple] = []
        # state
        self.active = False
        self.sub: dict | None = None
        self.dunning_end: date | None = None
        self.dunning_start: date | None = None
        self.churn_week: int | None = None
        self.churn_reason: str | None = None
        self.last_terms: dict | None = None
        self.grace_left = 0
        self.H = self.mu - self.D["onboarding_dip"]
        self.N = 1.0
        self.burst = 0
        self.integrations = int(rng.poisson(self.D["integrations_start"][self.seg]))
        self.n_hist: deque = deque(maxlen=14)
        self.wau_hist: deque = deque(maxlen=4)
        self.pending_ent: date | None = None      # requested date of a pending Premium -> Enterprise upgrade
        self.inv_seq = 0
        # S4: one-time layoff for a share of Technology accounts during the layoff wave
        self.layoff_week = None
        lw = self.S["tech_freeze"]["layoff_wave"]
        if self.is_tech and not self.protect and rng.random() < lw["prob"]:
            lo, hi = d(lw["start"]), d(lw["end"])
            self.layoff_week = cal.week_of(lo + timedelta(days=int(rng.integers(0, (hi - lo).days + 1))))

    # ---- pricing helpers
    def unit_price(self, plan: str, billing: str) -> float:
        p = self.plans[plan]
        return float(p["annual"] if billing == "annual" else p["monthly"])

    @staticmethod
    def period_price(t: dict) -> float:
        return t["seats"] * t["unit_price"] * (1.0 - t["discount"])

    def terms(self) -> dict:
        s = self.sub
        return {k: s[k] for k in ("plan", "seats", "billing", "unit_price", "discount")}

    # ---- invoices
    def _invoice(self, day: date, line_type: str, amount: float, ps: date | None, pe: date | None,
                 desc: str, card: bool, fail_prob: float = 0.0) -> str:
        """Create an invoice line; returns the payment outcome ('paid' / 'failed_recovered' / 'failed')."""
        self.inv_seq += 1
        outcome, status, paid, attempts = "paid", "paid", None, 1
        rng = self.rng
        if card:
            if fail_prob > 0 and rng.random() < fail_prob:
                if rng.random() < self.D["payment_failure"]["recovery_prob"]:
                    outcome, attempts = "failed_recovered", int(rng.integers(2, 5))
                    paid = day + timedelta(days=int(rng.integers(1, 15)))
                else:
                    outcome, status, attempts = "failed", "uncollectible", 4
            else:
                paid = day
        else:
            paid = day + timedelta(days=int(rng.integers(5, 46)))
        if paid is not None and paid > self.cal.cutoff:
            status, paid = "open", None
        if self.frozen and amount > 0:
            status, paid = "void", None   # internal accounts are never charged
        self.invoices.append({
            "customer_id": self.a["customer_id"], "sub_key": self.sub["key"], "invoice_date": day,
            "line_type": line_type, "description": desc, "service_period_start": ps, "service_period_end": pe,
            "amount_usd": round(amount, 2), "tax_usd": round(amount * self.a["tax_rate"], 2), "status": status,
            "paid_date": paid, "payment_attempts": attempts, "_seq": self.inv_seq,
        })
        return outcome

    def _recurring_invoice(self, start: date, end: date) -> str:
        s = self.sub
        amount = self.period_price(self.terms())
        card = s["payment_method"] == "card"
        fp = (self.D["payment_failure"].get(self.seg, 0.0)
              if (card and s["billing"] == "monthly" and not self.protect) else 0.0)
        if start == s["start_date"]:
            fp = 0.0  # first charge is taken at checkout
        desc = f"{self.plans[s['plan']]['name']} x {s['seats']} seats ({s['billing']})"
        return self._invoice(start, "recurring", amount, start, end, desc, card, fp)

    # ---- subscription lifecycle
    def start_subscription(self, day: date, plan: str, seats: int, billing: str, discount: float,
                           payment_method: str, onboarding_fee: float = 0.0) -> None:
        anchor = day.day
        period_end = add_months(day, 12 if billing == "annual" else 1, anchor)
        self.sub = {
            "key": (self.a["customer_id"], len(self.subs) + 1), "start_date": day, "plan": plan, "seats": seats,
            "billing": billing, "unit_price": self.unit_price(plan, billing), "discount": discount,
            "period_start": day, "period_end": period_end, "anchor": anchor, "payment_method": payment_method,
            "term_end": period_end if billing == "annual" else None,
        }
        self.subs.append({
            "sub_key": self.sub["key"], "customer_id": self.a["customer_id"], "start_date": day, "end_date": None,
            "status": "active", "cancel_requested_date": None, "cancel_reason": None, "initial_plan_id": plan,
            "initial_seats": seats, "initial_billing_period": billing,
            "initial_unit_price": self.sub["unit_price"], "initial_discount_pct": discount,
            "payment_method": payment_method,
        })
        self.active = True
        self._recurring_invoice(day, period_end)
        if onboarding_fee > 0:
            self._invoice(day, "one_time", onboarding_fee, None, None, "Onboarding & implementation", False)

    def end_subscription(self, day: date, reason: str, requested: date) -> None:
        rec = self.subs[-1]
        rec.update(end_date=day, status="canceled", cancel_requested_date=requested, cancel_reason=reason)
        self.last_terms = self.terms()
        self.active = False
        self.churn_week = self.cal.week_of(day)
        self.churn_reason = reason
        self.grace_left = len(self.P["usage"]["grace_weeks_usage"])
        self.dunning_end = self.dunning_start = None
        self.pending_ent = None   # a pending upgrade dies with the subscription

    def apply_change(self, day: date, new: dict, source: str, requested: date | None = None) -> None:
        old = self.terms()
        if all(old[k] == new[k] for k in old):
            return
        s = self.sub
        ps, pe = s["period_start"], s["period_end"]
        # prorated charge for increases inside the current period (co-termed for annual)
        if ps <= day < pe and new["billing"] == old["billing"]:
            delta = self.period_price(new) - self.period_price(old)
            if delta > 0.005:
                frac = (pe - day).days / (pe - ps).days
                s.update(new)
                self._invoice(day, "proration", delta * frac, day, pe,
                              f"Proration: {old['plan']} x{old['seats']} -> {new['plan']} x{new['seats']}",
                              s["payment_method"] == "card")
        tier_old, tier_new = PLAN_TIER[old["plan"]], PLAN_TIER[new["plan"]]
        if tier_new > tier_old:
            ctype = "upgrade"
        elif tier_new < tier_old:
            ctype = "downgrade"
        elif new["seats"] > old["seats"]:
            ctype = "seat_increase"
        elif new["seats"] < old["seats"]:
            ctype = "seat_decrease"
        elif new["billing"] != old["billing"]:
            ctype = "billing_period_change"
        else:
            ctype = "discount_change"
        self.changes.append({
            "sub_key": s["key"], "customer_id": self.a["customer_id"],
            "requested_date": min(requested or day, day), "effective_date": day, "change_type": ctype,
            "change_source": source,
            "from_plan_id": old["plan"], "to_plan_id": new["plan"],
            "from_seats": old["seats"], "to_seats": new["seats"],
            "from_billing_period": old["billing"], "to_billing_period": new["billing"],
            "from_unit_price": old["unit_price"], "to_unit_price": new["unit_price"],
            "from_discount_pct": old["discount"], "to_discount_pct": new["discount"],
        })
        s.update(new)
        if new["seats"] < old["seats"]:
            self.N = min(self.N, float(new["seats"]))   # removed users lose access: no immediate re-buy

    def _fit_plan(self, t: dict) -> dict:
        """Clip seats to plan limits and set the list unit price for plan + billing."""
        p = self.plans[t["plan"]]
        t["seats"] = max(int(t["seats"]), p["min_seats"])
        if p["max_seats"]:
            t["seats"] = min(t["seats"], p["max_seats"])
        t["unit_price"] = self.unit_price(t["plan"], t["billing"])
        return t

    def _needed_seats(self) -> float:
        """Team size (people who need access), averaged over the last 4 weeks."""
        recent = list(self.n_hist)[-4:]
        return float(np.mean(recent)) if recent else float(self.sub["seats"])

    # ---- weekly step
    def step_week(self, w: int, ws: date, we: date, u: np.ndarray, eps: np.ndarray) -> None:
        D, cal, tf = self.D, self.cal, self.S["tech_freeze"]
        fz = cal.freeze[w] if self.is_tech else 0.0
        weeks_in = (ws - self.sub["start_date"]).days / 7.0
        mu_t = self.mu + fz * tf["fit_shift"]
        pk = self.S["packaging_change"]
        if cal.pkg_after[w] and self.sub["plan"] == "basic" and self.power > 0:
            mu_t += pk["power_user_fit_lift"]           # S3 upside: automation users get more value from Basic
        if weeks_in < D["onboarding_weeks"]:
            mu_t -= D["onboarding_dip"] * (1 - max(weeks_in, 0) / D["onboarding_weeks"])
        if not self.frozen:
            if u[0] < D["champion_loss"]["prob"]:
                lo, hi = D["champion_loss"]["drop"]
                self.H -= lo + (hi - lo) * u[1]
            if u[2] < D["new_team"]["prob"][self.seg]:
                lo, hi = D["new_team"]["lift"]
                self.H += lo + (hi - lo) * u[3]
                self.burst = D["new_team"]["burst_weeks"]
        self.H += D["health_reversion"] * (mu_t - self.H) + D["health_noise"] * eps[0]
        if self.protect:
            self.H = max(self.H, 1.5)
        # adoption: healthy accounts roll out to more users; unhealthy ones shed users faster (asymmetric)
        h_slope = D["team_growth_health"] if self.H >= 1.0 else D["team_growth_health_low"]
        r = (D["team_growth_base"][self.seg] + D["team_growth_trait"] * self.growth
             + h_slope * (self.H - 1.0) + cal.hiring[self.ind][w]
             + fz * tf["team_growth_shift"] + (D["new_team"]["burst_growth"] if self.burst > 0 else 0.0)
             + D["team_growth_noise"] * eps[1])
        if self.frozen:
            r = 0.0
        n_prev = self.N
        self.N = max(1.0, self.N * math.exp(r))
        if not self.protect:
            tc = D["team_cut"]
            if u[16] < tc["prob"]:                    # reorg / budget cut
                lo, hi = tc["size"]
                self.N = max(1.0, self.N * (1 - (lo + (hi - lo) * u[17])))
            if self.layoff_week == w:                 # S4 layoff wave
                lo, hi = tf["layoff_wave"]["team_mult"]
                self.N = max(1.0, self.N * float(self.rng.uniform(lo, hi)))
        self.burst = max(0, self.burst - 1)
        self.n_hist.append(self.N)
        # integrations
        cap = D["basic_integration_cap"] if self.sub["plan"] == "basic" else D["integration_cap"]
        if u[4] < D["integrations_add_prob"] * sigmoid(self.H) and self.integrations < cap:
            self.integrations += 1
        elif self.H < D["integrations_drop_health"] and u[5] < D["integrations_drop_prob"] and self.integrations > 0:
            self.integrations -= 1
        self.integrations = min(self.integrations, cap)

        engagement = sigmoid(D["engagement_intercept"] + D["engagement_slope"] * self.H) * cal.usage_season[self.ind][w]
        seats_start = self.sub["seats"]
        capacity = min(self.N, seats_start * (1 + D["seat_overage_tolerance"]))
        self.wau_hist.append(capacity * engagement)

        # ---- events this week
        if self.dunning_end is not None:
            if self.dunning_end <= we:
                self.record_week(w, ws, we, engagement, n_prev, active_to=self.dunning_end)
                self.end_subscription(self.dunning_end, "payment_failed", self.dunning_start)
                return
            self.record_week(w, ws, we, engagement, n_prev)
            return
        active_from = max(ws, self.sub["start_date"])
        days = [active_from + timedelta(days=k) for k in range((we - active_from).days + 1)]
        biz = [x for x in days if x.weekday() < 5] or days
        d_evt = biz[min(int(u[6] * len(biz)), len(biz) - 1)]
        pe = self.sub["period_end"]
        boundary = pe if (ws <= pe <= we) else None
        churned_on = None
        if boundary is not None and d_evt >= boundary:
            churned_on = self.boundary(boundary, w, u)
            if self.active:
                self.immediate(d_evt, w, u)
        else:
            self.immediate(d_evt, w, u)
            if boundary is not None:
                churned_on = self.boundary(boundary, w, u)
        self.record_week(w, ws, we, engagement, n_prev, active_to=churned_on, seats_override=seats_start)

    def record_week(self, w, ws, we, engagement, n_prev, active_to=None, seats_override=None) -> None:
        start = max(ws, self.sub["start_date"])
        end_excl = active_to if active_to is not None else we + timedelta(days=1)
        active_days = max(0, (end_excl - start).days)
        if active_days == 0:
            return
        seats = self.sub["seats"] if (seats_override is None or active_to is None) else seats_override
        self.weeks.append((
            self.a["idx"], w, active_days, seats, PLAN_TIER[self.sub["plan"]], self.H, engagement, self.N,
            max(0.0, self.N - n_prev), self.integrations, 1.0,
        ))

    def record_grace(self, w: int, we: date) -> None:
        fades = self.P["usage"]["grace_weeks_usage"]
        k = len(fades) - self.grace_left
        t = self.last_terms
        days = min(7, (we - self.cal.week_starts[w]).days + 1)
        self.weeks.append((
            self.a["idx"], w, days, t["seats"], PLAN_TIER[t["plan"]], self.H, 0.6, self.N, 0.0,
            self.integrations, fades[k],
        ))
        self.grace_left -= 1

    def immediate(self, day: date, w: int, u: np.ndarray) -> None:
        """Increases take effect immediately (prorated)."""
        if self.frozen or not self.active:
            return
        D, s = self.D, self.sub
        ro = self.S["rollout"]
        if self.role == "rollout":
            rd = d(ro["date"])
            if self.cal.week_starts[w] <= rd <= self.cal.week_starts[w] + timedelta(days=6):
                new = self.terms()
                new["seats"] = int(round(s["seats"] * ro["seat_mult"]))
                self.N = max(self.N, new["seats"] * 1.03)
                self.apply_change(rd, self._fit_plan(new), "sales")
                return
        qf = self.cal.qfactor[w] if self.seg != "SMB" else 1.0
        # seat additions when the team outgrows its seats
        if self.N > s["seats"] * D["seat_add_trigger"]:
            if u[7] < D["seat_add_prob"][self.seg] * sigmoid(self.H) * qf:
                lo, hi = D["seat_add_buffer"]
                target = max(s["seats"] + 1, math.ceil(self.N * (lo + (hi - lo) * u[8])))
                new = self.terms()
                new["seats"] = target
                source = "self_serve" if self.seg == "SMB" else "sales"
                if new["plan"] == "basic" and target > self.plans["basic"]["max_seats"]:
                    new["plan"], source = "premium", "system"   # seat-cap upgrade (forced by plan limit)
                self.apply_change(day, self._fit_plan(new), source)
                return
        # Basic -> Premium (behavior-driven)
        if s["plan"] == "basic":
            ub = D["upgrade_basic"]
            grew = len(self.n_hist) >= 14 and self.N / self.n_hist[0] > 1 + ub["team_growth_threshold"]
            lam = ub["base"] * math.exp(ub["power"] * self.power + ub["health"] * (self.H - 1.0))
            lam *= ub["team_growth_mult"] if grew else 1.0
            if self.cal.pkg_after[w]:
                lam *= self.S["packaging_change"]["upgrade_mult"]
            if u[9] < 1 - math.exp(-lam):
                new = self.terms()
                new["plan"] = "premium"
                self.apply_change(day, self._fit_plan(new), "self_serve" if self.seg == "SMB" else "sales")
                return
        # Premium -> Enterprise (sales-assisted): agreed now, effective at the next billing boundary;
        # annual accounts only in the 12 weeks before renewal
        ue = D["upgrade_enterprise"]
        if (s["plan"] == "premium" and self.seg != "SMB" and s["seats"] >= ue["min_seats"]
                and self.H > ue["min_health"] and self.pending_ent is None):
            in_window = s["billing"] == "monthly" or (s["term_end"] - day).days <= ue["annual_window_days"]
            lam = ue["base"] * math.exp(ue["power"] * self.power) * qf
            if in_window and u[10] < 1 - math.exp(-lam):
                self.pending_ent = day

    def _tenure_mult(self, day: date) -> float:
        months = (day - self.sub["start_date"]).days / 30.4
        tm = self.D["churn_tenure_mult"]
        return tm["first_3_months"] if months <= 3 else (tm["months_4_12"] if months <= 12 else tm["after_12"])

    def _try_save(self, day: date, req_days: tuple[int, int]) -> bool:
        """Downgrade-to-save when a Premium/Enterprise account decides to cancel (CS-negotiated)."""
        s, D = self.sub, self.D
        if PLAN_TIER[s["plan"]] == 1 or self.rng.random() >= D["save_by_downgrade_prob"][self.cov]:
            return False
        new = self.terms()
        target_seats = max(1, math.ceil(self._needed_seats() * float(self.rng.uniform(1.0, 1.05))))
        new["seats"] = min(new["seats"], target_seats)
        lower = TIER_PLAN[PLAN_TIER[s["plan"]] - 1]
        if lower == "basic" and new["seats"] > self.plans["basic"]["max_seats"]:
            lo, hi = D["save_retention_discount"]          # too big for Basic: retention discount instead
            new["discount"] = round(min(0.40, new["discount"] + float(self.rng.uniform(lo, hi))), 2)
        else:
            new["plan"] = lower
            new["discount"] = 0.0
        new = self._fit_plan(new)
        req = day - timedelta(days=int(self.rng.integers(*req_days)))
        self.apply_change(day, new, "customer_success", requested=max(req, s["period_start"]))
        self.H += 0.2
        return True

    def boundary(self, pe: date, w: int, u: np.ndarray) -> date | None:
        """Billing-period end. Returns the churn date if the subscription ends here."""
        s = self.sub
        if self.frozen:
            self._roll_period(pe)
            return None
        if s["billing"] == "annual" and s["term_end"] == pe:
            return self.renewal(pe, w, u)
        return self.monthly_boundary(pe, w, u)

    def _roll_period(self, pe: date) -> None:
        s = self.sub
        months = 12 if s["billing"] == "annual" else 1
        nxt = add_months(pe, months, s["anchor"])
        s["period_start"], s["period_end"] = pe, nxt
        if s["billing"] == "annual":
            s["term_end"] = nxt
        outcome = self._recurring_invoice(pe, nxt)
        if outcome == "failed":
            self.dunning_start = pe
            self.dunning_end = pe + timedelta(days=self.D["payment_failure"]["dunning_days"])

    def _churn_prob(self, day: date, annual: bool) -> float:
        D = self.D
        if self.protect:
            return 0.0
        mult = D["churn_channel_mult"][self.ch] * D["churn_industry_mult"].get(self.ind, 1.0)
        if annual:
            p = D["churn_annual_base"][self.seg] * mult * math.exp(-D["churn_health_slope_annual"] * (self.H - 1.0))
            return min(p, D["churn_cap_annual"])
        p = (D["churn_monthly_base"][self.seg] * mult * self._tenure_mult(day)
             * D["churn_season_mult"].get(day.month, 1.0)
             * math.exp(-D["churn_health_slope_monthly"] * (self.H - 1.0)))
        if self.sub["plan"] == "basic" and day >= self.cal.pkg_date:
            p *= self.S["packaging_change"]["basic_churn_mult"]   # S3 upside
        return min(p, D["churn_cap_monthly"])

    def _apply_pending_enterprise(self, new: dict) -> dict:
        lo, hi = self.D["enterprise_upgrade_discount"]
        new.update(plan="enterprise", billing="annual", discount=round(float(self.rng.uniform(lo, hi)), 2))
        return new

    def monthly_boundary(self, pe: date, w: int, u: np.ndarray) -> date | None:
        """Order: (1) voluntary churn -> save; (2) pending upgrade, or decreases / switches; (3) charge."""
        D, s, rng = self.D, self.sub, self.rng
        if u[12] < self._churn_prob(pe, annual=False):
            if not self._try_save(pe, (1, 21)):
                req = max(pe - timedelta(days=int(rng.integers(1, 21))), s["period_start"])
                self.end_subscription(pe, "voluntary", req)
                return pe
            self._roll_period(pe)
            return None
        new = self.terms()
        source = "self_serve" if self.seg == "SMB" else "customer_success"
        req = max(pe - timedelta(days=int(rng.integers(1, 21))), s["period_start"])
        if self.pending_ent is not None:
            new = self._apply_pending_enterprise(new)
            source, req = "sales", self.pending_ent
            self.pending_ent = None
        elif not self.protect:
            need = self._needed_seats()
            fz = self.cal.freeze[w] if self.is_tech else 0.0
            p_red = D["seat_reduce_prob"]["monthly"] * (1 + (self.S["tech_freeze"]["seat_reduce_mult"] - 1) * fz)
            if s["seats"] > need * D["seat_surplus_trigger"]["monthly"] and u[13] < p_red:
                lo, hi = D["seat_reduce_target"]
                new["seats"] = max(1, math.ceil(need * float(rng.uniform(lo, hi))))
            elif rng.random() < D["random_small_reduction_prob"] and s["seats"] > 2:
                new["seats"] = s["seats"] - max(1, round(s["seats"] * D["random_small_reduction_size"]))
            dp = D["downgrade_premium"]
            if (s["plan"] == "premium" and new["seats"] <= self.plans["basic"]["max_seats"]
                    and u[14] < dp["monthly_base"] * math.exp(-dp["health"] * (self.H - 1)) * math.exp(-dp["power"] * self.power)):
                new["plan"], new["discount"] = "basic", 0.0
            bs = D["billing_switch"]["to_annual"]
            tenure_m = (pe - s["start_date"]).days / 30.4
            p_sw = bs["SMB"] if self.seg == "SMB" else bs["other"]
            if (new["billing"] == "monthly" and tenure_m >= bs["min_tenure_months"] and self.H > bs["min_health"]
                    and u[15] < p_sw):
                new["billing"] = "annual"
                req = pe
        new = self._fit_plan(new)
        if new["billing"] == "annual" and s["billing"] == "monthly":
            s["anchor"] = pe.day
            if self.seg != "SMB":
                s["payment_method"] = "invoice"
        self.apply_change(pe, new, source, requested=req)
        self._roll_period(pe)
        return None

    def renewal(self, pe: date, w: int, u: np.ndarray) -> date | None:
        """Annual renewal: churn -> save; else pending upgrade or renewal changes (one combined row)."""
        D, s, rng = self.D, self.sub, self.rng
        if u[12] < self._churn_prob(pe, annual=True):
            if not self._try_save(pe, (15, 61)):
                req = pe - timedelta(days=int(rng.integers(15, 61)))
                self.end_subscription(pe, "voluntary", req)
                return pe
            self._roll_period(pe)
            return None
        new = self.terms()
        source, req = "renewal", pe - timedelta(days=int(rng.integers(15, 46)))
        if self.pending_ent is not None:
            new = self._apply_pending_enterprise(new)
            source, req = "sales", self.pending_ent
            self.pending_ent = None
        elif not self.protect:
            need = self._needed_seats()
            fz = self.cal.freeze[w] if self.is_tech else 0.0
            p_rs = D["seat_reduce_prob"]["renewal"] * (1 + (self.S["tech_freeze"]["seat_reduce_mult"] - 1) * fz)
            if s["seats"] > need * D["seat_surplus_trigger"]["renewal"] and u[13] < min(p_rs, 0.95):
                lo, hi = D["seat_reduce_target"]
                new["seats"] = max(1, math.ceil(need * float(rng.uniform(lo, hi))))
            de = D["downgrade_enterprise_renewal"]
            if s["plan"] == "enterprise":
                if new["seats"] < self.plans["enterprise"]["min_seats"]:
                    if rng.random() < de["low_usage_prob"]:
                        new["plan"], new["discount"] = "premium", 0.0
                elif rng.random() < de["base"] * math.exp(-de["health"] * (self.H - 1)):
                    new["plan"], new["discount"] = "premium", 0.0
            dp = D["downgrade_premium"]
            if (new["plan"] == "premium" and s["plan"] == "premium" and new["seats"] <= self.plans["basic"]["max_seats"]
                    and u[14] < dp["monthly_base"] * dp["renewal_mult"] * math.exp(-dp["health"] * (self.H - 1))
                    * math.exp(-dp["power"] * self.power)):
                new["plan"], new["discount"] = "basic", 0.0
            tm = D["billing_switch"]["to_monthly_at_renewal"]
            if new["plan"] != "enterprise" and self.H < tm["max_health"] and rng.random() < tm["prob"]:
                new["billing"] = "monthly"
            rp = D["renewal_price"]
            if new["plan"] == "enterprise" or new["discount"] > 0:
                if self.H < rp["concession"]["max_health"] and rng.random() < rp["concession"]["prob"]:
                    lo, hi = rp["concession"]["pts"]
                    new["discount"] = round(min(0.40, new["discount"] + float(rng.uniform(lo, hi))), 2)
                elif self.H > rp["uplift"]["min_health"] and new["discount"] > 0 and rng.random() < rp["uplift"]["prob"]:
                    lo, hi = rp["uplift"]["pts"]
                    new["discount"] = round(max(0.0, new["discount"] - float(rng.uniform(lo, hi))), 2)
        new = self._fit_plan(new)
        if new["billing"] == "monthly" and s["billing"] == "annual":
            s["anchor"] = pe.day
        self.apply_change(pe, new, source, requested=req)
        self._roll_period(pe)
        return None

    def maybe_winback(self, w: int, ws: date, we: date, u: np.ndarray) -> None:
        if self.frozen or self.churn_week is None or self.last_terms is None:
            return
        W = self.D["winback"]
        k = w - self.churn_week
        if self.churn_reason == "voluntary":
            v = W["voluntary"]
            if not (v["start_week"] <= k <= v["end_week"]):
                return
            p = v["weekly_prob"] * math.exp(v["fit_slope"] * (self.mu - 1.0))
        else:
            v = W["payment_failed"]
            if k > v["end_week"]:
                return
            p = v["weekly_prob_early"] if k <= v["early_weeks"] else v["weekly_prob_late"]
        if u[0] >= p:
            return
        rng = self.rng
        lt = self.last_terms
        plan = lt["plan"]
        if rng.random() >= W["same_plan_prob"]:
            plan = TIER_PLAN[max(1, PLAN_TIER[plan] - 1)]
        lo, hi = W["seat_share"]
        seats = max(1, round(lt["seats"] * float(rng.uniform(lo, hi))))
        if plan == "enterprise" and seats < self.plans["enterprise"]["min_seats"]:
            plan = "premium"
        if plan == "basic" and seats > self.plans["basic"]["max_seats"]:
            plan = "premium"
        billing = "annual" if plan == "enterprise" else ("monthly" if rng.random() < W["monthly_prob"] else "annual")
        disc = round(float(rng.uniform(*self.P["initial"]["enterprise_discount"])), 2) if plan == "enterprise" else 0.0
        t = self._fit_plan({"plan": plan, "seats": seats, "billing": billing, "unit_price": 0.0, "discount": disc})
        days = [ws + timedelta(days=x) for x in range((we - ws).days + 1)]
        biz = [x for x in days if x.weekday() < 5] or days
        day = biz[int(rng.integers(0, len(biz)))]
        pay = "card" if (billing == "monthly" and not self.sales_led) else "invoice"
        self.H = self.mu - W["restart_health_gap"]
        self.N = t["seats"] * float(rng.uniform(0.9, 1.05))
        self.n_hist.clear()
        self.wau_hist.clear()
        self.start_subscription(day, t["plan"], t["seats"], t["billing"], t["discount"], pay)
        self.churn_week = None

    def run(self) -> None:
        a, cal = self.a, self.cal
        w0 = cal.week_of(a["signup_date"])
        n = cal.n_weeks - w0
        U = self.rng.random((n, 18))
        E = self.rng.standard_normal((n, 2))
        self.N = a["initial_seats"] * float(self.rng.uniform(*self.D["initial_team_vs_seats"]))
        self.start_subscription(a["signup_date"], a["initial_plan"], int(a["initial_seats"]), a["initial_billing"],
                                float(a["initial_discount"]), a["payment_method"], float(a["onboarding_fee"]))
        for k in range(n):
            w = w0 + k
            ws = cal.week_starts[w]
            we = min(ws + timedelta(days=6), cal.cutoff)
            if self.active:
                self.step_week(w, ws, we, U[k], E[k])
            else:
                if self.grace_left > 0:
                    self.record_grace(w, we)
                self.maybe_winback(w, ws, we, U[k])
        if self.active and self.dunning_end is not None:
            self.subs[-1]["status"] = "past_due"


# --------------------------------------------------------------------------- usage events
def build_usage(weeks: np.ndarray, accts: pd.DataFrame, P: dict, cal: Calendar, rng: np.random.Generator) -> pd.DataFrame:
    U = P["usage"]
    idx = weeks[:, 0].astype(int)
    w = weeks[:, 1].astype(int)
    days = weeks[:, 2]
    seats = weeks[:, 3]
    tier = weeks[:, 4].astype(int)
    H = weeks[:, 5]
    eng = weeks[:, 6]
    N = weeks[:, 7]
    dN = weeks[:, 8]
    integ = weeks[:, 9]
    fade = weeks[:, 10]
    power = accts["power"].to_numpy()[idx]
    frac = days / 7.0 * fade
    cap = np.minimum(N, seats * (1 + P["dynamics"]["seat_overage_tolerance"]))
    p_active = np.clip(eng * np.minimum(1.0, 0.4 + 0.6 * days / 7.0) * np.where(fade < 1, fade, 1.0), 0, 1)
    wau = rng.binomial(np.round(cap).astype(int), p_active)
    pkg = cal.pkg_after[w]
    sc = P["scenario"]["packaging_change"]

    out = {}
    out["session"] = (rng.poisson(wau * U["sessions_per_wau"] * np.exp(0.15 * (H - 1)) * frac), wau)
    ca = rng.poisson(wau * U["core_actions_per_wau"] * np.exp(0.2 * (H - 1)) * frac)
    out["core_action"] = (ca, np.minimum(ca, rng.binomial(wau, 0.92)))
    ar = U["automation_per_wau"]
    a_rate = np.select([tier == 1, tier == 2], [np.where(pkg, ar["basic_after_packaging"], ar["basic"]), ar["premium"]],
                       ar["enterprise"])
    au = rng.poisson(wau * a_rate * np.exp(0.4 * power) * frac)
    out["automation_run"] = (au, np.minimum(au, rng.binomial(wau, 0.25)))
    rv = U["report_views_per_wau"]
    r_rate = np.select([tier == 1, tier == 2], [rv["basic"], rv["premium"]], rv["enterprise"])
    rvw = rng.poisson(wau * r_rate * frac)
    out["report_view"] = (rvw, np.minimum(rvw, rng.binomial(wau, 0.35)))
    sy = rng.poisson(integ * U["syncs_per_integration"] * frac)
    out["integration_sync"] = (sy, np.zeros_like(sy))
    waiting = np.maximum(0.0, N - seats)
    inv = rng.poisson((U["invites_per_new_member"] * dN + U["invites_per_seat"] * seats
                       + U["invites_per_waiting_user"] * waiting) * frac)
    out["user_invite"] = (inv, np.minimum(inv, 1 + rng.poisson(0.3, size=len(inv))))
    g_rate = (U["gate_hits_per_wau"] * np.exp(U["gate_power"] * power + U["gate_health"] * (H - 1))
              * np.where(pkg, sc["gate_mult"], 1.0))
    gh = np.where(tier == 1, rng.poisson(wau * g_rate * frac), 0)
    gu = np.minimum(gh, rng.binomial(wau, np.clip(1 - np.exp(-g_rate * frac), 0, 1)))
    out["premium_feature_gate"] = (gh, gu)

    cust = accts["customer_id"].to_numpy()[idx]
    wk = np.array(cal.week_starts, dtype=object)[w]
    frames = []
    for name in EVENT_NAMES:
        cnt, uu = out[name]
        keep = cnt > 0
        frames.append(pd.DataFrame({
            "customer_id": cust[keep], "week_start": wk[keep], "event_name": name,
            "event_count": cnt[keep].astype("int64"), "unique_users": uu[keep].astype("int64"),
        }))
    usage = pd.concat(frames, ignore_index=True)
    return usage.sort_values(["customer_id", "week_start", "event_name"]).reset_index(drop=True)


# --------------------------------------------------------------------------- main
def generate(out_dir: Path, seed: int, params_path: Path) -> dict:
    t0 = time.time()
    P = yaml.safe_load(params_path.read_text())
    cal = Calendar(P)
    ss = np.random.SeedSequence(seed)
    acq_ss, sim_ss, usage_ss, dq_ss = ss.spawn(4)
    accts = sample_accounts(P, cal, np.random.default_rng(acq_ss))
    accts["idx"] = np.arange(len(accts))
    child = sim_ss.spawn(len(accts))

    subs, changes, invoices, weeks = [], [], [], []
    for rec, cs in zip(accts.to_dict("records"), child):
        sim = AccountSim(rec, P, cal, np.random.default_rng(cs))
        sim.run()
        subs += sim.subs
        changes += sim.changes
        invoices += sim.invoices
        weeks += sim.weeks
    t_sim = time.time() - t0
    dq_rng = np.random.default_rng(dq_ss)
    Q = P["dq"]

    # ---- subscriptions: ids in start order
    sub_df = pd.DataFrame(subs).sort_values(["start_date", "customer_id"]).reset_index(drop=True)
    sub_df["subscription_id"] = [f"SUB-{i + 1:06d}" for i in range(len(sub_df))]
    key_to_sub = dict(zip(sub_df["sub_key"], sub_df["subscription_id"]))
    sub_df = sub_df.drop(columns="sub_key")[[
        "subscription_id", "customer_id", "start_date", "end_date", "status", "cancel_requested_date",
        "cancel_reason", "initial_plan_id", "initial_seats", "initial_billing_period", "initial_unit_price",
        "initial_discount_pct", "payment_method"]]

    # ---- plan changes (+ DQ: orphan rows keyed in after a cancellation)
    ch_df = pd.DataFrame(changes)
    ch_df["subscription_id"] = ch_df["sub_key"].map(key_to_sub)
    ended = sub_df[sub_df["end_date"].notna()]
    n_orphan = int(round(len(ch_df) * Q["orphan_change_share"]))
    orph = ended.sample(n=min(n_orphan, len(ended)), random_state=dq_rng)
    last_terms = ch_df.sort_values("effective_date").groupby("subscription_id").tail(1).set_index("subscription_id")
    orphan_rows = []
    for _, sr in orph.iterrows():
        if sr["subscription_id"] in last_terms.index:
            lt = last_terms.loc[sr["subscription_id"]]
            plan, seats, bp, up, dc = lt["to_plan_id"], lt["to_seats"], lt["to_billing_period"], lt["to_unit_price"], lt["to_discount_pct"]
        else:
            plan, seats, bp, up, dc = (sr["initial_plan_id"], sr["initial_seats"], sr["initial_billing_period"],
                                       sr["initial_unit_price"], sr["initial_discount_pct"])
        if seats <= 1:
            continue
        eff = min(sr["end_date"] + timedelta(days=int(dq_rng.integers(0, 21))), cal.cutoff)
        orphan_rows.append({
            "sub_key": None, "customer_id": sr["customer_id"], "subscription_id": sr["subscription_id"],
            "requested_date": eff, "effective_date": eff, "change_type": "seat_decrease", "change_source": "self_serve",
            "from_plan_id": plan, "to_plan_id": plan, "from_seats": int(seats), "to_seats": int(seats) - 1,
            "from_billing_period": bp, "to_billing_period": bp, "from_unit_price": up, "to_unit_price": up,
            "from_discount_pct": dc, "to_discount_pct": dc,
        })
    ch_df = pd.concat([ch_df, pd.DataFrame(orphan_rows)], ignore_index=True)
    ch_df["_seq"] = np.arange(len(ch_df))
    ch_df = ch_df.sort_values(["effective_date", "subscription_id", "_seq"]).reset_index(drop=True)
    ch_df["change_id"] = [f"CHG-{i + 1:07d}" for i in range(len(ch_df))]
    ch_df = ch_df[["change_id", "subscription_id", "customer_id", "requested_date", "effective_date", "change_type",
                   "change_source", "from_plan_id", "to_plan_id", "from_seats", "to_seats", "from_billing_period",
                   "to_billing_period", "from_unit_price", "to_unit_price", "from_discount_pct", "to_discount_pct"]]

    # ---- invoices: one invoice per customer-day for recurring (+ one-time) lines; prorations on their own
    inv_df = pd.DataFrame(invoices)
    inv_df["subscription_id"] = inv_df["sub_key"].map(key_to_sub)
    inv_df["_order"] = np.arange(len(inv_df))
    inv_df = inv_df.sort_values(["invoice_date", "customer_id", "_order"]).reset_index(drop=True)
    grp = (inv_df["customer_id"] + "|" + inv_df["invoice_date"].astype(str) + "|"
           + np.where(inv_df["line_type"] == "proration", inv_df["_order"].astype(str), "R"))
    codes, _ = pd.factorize(grp)
    inv_df["invoice_id"] = [f"INV-{c + 1:07d}" for c in codes]
    inv_df["line_no"] = inv_df.groupby("invoice_id").cumcount() + 1
    inv_df["invoice_line_id"] = inv_df["invoice_id"] + "-" + inv_df["line_no"].astype(str)
    n_void = int(round(len(inv_df) * Q["invoice_void_duplicate_share"]))
    dup = inv_df[(inv_df["line_type"] == "recurring") & (inv_df["status"] != "void")].sample(n=n_void, random_state=dq_rng).copy()
    dup["invoice_id"] = [f"INV-V{i + 1:06d}" for i in range(len(dup))]
    dup["invoice_line_id"] = dup["invoice_id"] + "-1"
    dup["status"], dup["paid_date"], dup["payment_attempts"] = "void", None, 0
    inv_df = pd.concat([inv_df, dup], ignore_index=True).sort_values(["invoice_date", "invoice_line_id"])
    inv_df = inv_df[["invoice_line_id", "invoice_id", "customer_id", "subscription_id", "invoice_date", "line_type",
                     "description", "service_period_start", "service_period_end", "amount_usd", "tax_usd", "status",
                     "paid_date", "payment_attempts"]].reset_index(drop=True)

    # ---- usage (+ DQ: double-loaded rows)
    usage = build_usage(np.array(weeks, dtype=float), accts, P, cal, np.random.default_rng(usage_ss))
    n_dup = int(round(len(usage) * Q["usage_duplicate_share"]))
    usage = pd.concat([usage, usage.sample(n=n_dup, random_state=dq_rng)], ignore_index=True)
    usage = usage.sort_values(["customer_id", "week_start", "event_name"]).reset_index(drop=True)

    # ---- customers (+ DQ: duplicate CRM records with no subscription) & plans
    cust = accts[["customer_id", "company_name", "email_domain", "industry_reported", "employee_count_reported",
                  "segment", "region", "channel", "created_at", "is_design_partner", "cs_coverage", "csm_owner"]]
    cust = cust.rename(columns={"industry_reported": "industry", "employee_count_reported": "employee_count",
                                "channel": "acquisition_channel"}).copy()
    real_ids = accts.loc[accts["role"] == "customer", "customer_id"]
    n_dupc = int(round(len(real_ids) * Q["duplicate_customer_share"]))
    dsrc = cust[cust["customer_id"].isin(real_ids)].sample(n=n_dupc, random_state=dq_rng).copy()
    start_n = len(cust)
    dsrc["customer_id"] = [f"CUS-{start_n + i + 1:06d}" for i in range(len(dsrc))]
    dsrc["created_at"] = [min(c + timedelta(days=int(dq_rng.integers(30, 400))), cal.cutoff) for c in dsrc["created_at"]]
    dsrc["csm_owner"] = None
    cust = pd.concat([cust, dsrc], ignore_index=True)
    plans_df = pd.DataFrame([
        {"plan_id": k, "plan_name": v["name"], "tier_rank": v["tier"], "monthly_price_per_seat": v["monthly"],
         "annual_price_per_seat": v["annual"], "min_seats": v["min_seats"], "max_seats": v["max_seats"],
         "included_features": v["features"]}
        for k, v in P["plans"].items()
    ])

    # ---- write
    out_dir.mkdir(parents=True, exist_ok=True)
    tables = {"plans": plans_df, "customers": cust, "subscriptions": sub_df, "plan_changes": ch_df,
              "invoices": inv_df, "usage_events": usage}
    for name, df in tables.items():
        df.to_parquet(out_dir / f"{name}.parquet", index=False)  # python dates -> parquet DATE

    rollout = accts[accts["role"] == "rollout"].iloc[0]
    sc = P["scenario"]
    truth = {
        "generator_version": GENERATOR_VERSION,
        "note": "Answer key for method validation only. The analysis never reads this file.",
        "S1_design_partners": int(accts["is_design_partner"].sum()),
        "S1_early_adopter_months": P["acquisition"]["early_adopters"]["months"],
        "S2_paid_social_program_start": P["acquisition"]["paid_social_program"]["start"],
        "S3_packaging_change": sc["packaging_change"],
        "S4_tech_freeze": sc["tech_freeze"],
        "S5_rollout_customer_id": rollout["customer_id"],
        "S5_rollout": sc["rollout"],
        "dq_internal_customer_ids": accts.loc[accts["role"] == "internal", "customer_id"].tolist(),
        "dq_duplicate_customer_ids": dsrc["customer_id"].tolist(),
        "dq_orphan_plan_changes": len(orphan_rows),
        "dq_void_duplicate_invoice_lines": int(n_void),
        "dq_duplicate_usage_rows": int(n_dup),
    }
    (out_dir / "_scenario_truth.json").write_text(json.dumps(truth, indent=2, default=str))
    manifest = {
        "generator_version": GENERATOR_VERSION,
        "seed": seed,
        "params_sha256": hashlib.sha256(params_path.read_bytes()).hexdigest(),
        "row_counts": {k: int(len(v)) for k, v in tables.items()},
        "simulation_seconds": round(t_sim, 1),
        "total_seconds": round(time.time() - t0, 1),
    }
    (out_dir / "_manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest


def main() -> None:
    s = get_settings()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=s.raw_dir)
    ap.add_argument("--seed", type=int, default=s.seed)
    ap.add_argument("--params", type=Path, default=ROOT / "data_gen" / "params.yaml")
    args = ap.parse_args()
    m = generate(args.out, args.seed, args.params)
    print(json.dumps(m, indent=2))


if __name__ == "__main__":
    main()
