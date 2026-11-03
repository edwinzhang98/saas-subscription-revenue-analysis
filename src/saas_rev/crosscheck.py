"""Independent Python cross-check of the SQL revenue-metric layer.

The SQL models in ``sql/`` are the production implementation of
``docs/02_metric_definitions.md``. This module re-implements the core revenue
metrics **from that document and the raw source tables only** (it was written
without reading the SQL) and compares its numbers with the warehouse tables,
row by row.

Re-implemented here (definition section in brackets):

* internal / test-account exclusions ............................ [§2]
* subscription terms on any day and subscription MRR .............. [§3]
* customer MRR at every month-end (dense panel) ................... [§3]
* MRR movements: new, reactivation, expansion, contraction, churn . [§4]
* per-attribute split of plan changes into components ............. [§4.1]
* monthly waterfall and flow rates ................................ [§4, §5]
* TTM NRR / GRR / logo retention and quarterly run-rate NRR ....... [§7, §8]
* cohort NRR / GRR / logo retention by month index ................ [§10]

Implementation notes
--------------------
* **Money is computed in integer cents.** Every unit price in the data is a
  whole number of cents and every discount a whole number of basis points
  (checked at run time), so subscription MRR is an exact fraction, rounded
  half-up to the cent (the ``ROUND(x, 2)`` of §1 / §3) with integer arithmetic.
  Equality tests such as "Unchanged: prev = cur" are therefore exact. Frames are
  converted to dollars only at the output boundary (:class:`Metrics`).
* **Month-end snapshots are a matrix** ``cents[customer, month_end]``. Movements,
  retention and cohorts are column operations on it, which keeps the code short
  and the whole run to a few seconds.
* **Comparisons** outer-join each Python frame with its warehouse table on the
  natural keys and report rows found on one side only, the largest absolute
  difference, the rows outside tolerance and a few examples. Where SQL differs
  from a careful reading of the definitions, the difference is explained under
  ``discrepancies`` (section, both values, a minimal example); sentences that a
  second analyst could read differently are listed under ``ambiguities``.

Usage::

    uv run python -m saas_rev.crosscheck [--json OUT]
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from saas_rev.config import Settings, get_settings

DOC = "docs/02_metric_definitions.md"
DOC_VERSION = "1.1"

# §2: internal / test accounts
INTERNAL_EMAIL_DOMAIN = "ourcompany.example"
INTERNAL_NAME_PATTERNS = (r"\btest\b", r"\[internal\]", r"^internal\b", r"\bqa\b", r"zz_test")
EXPECTED_INTERNAL_ACCOUNTS = 6

# §3: subscription terms and MRR
TERM_ATTRS = ("plan_id", "seats", "billing_period", "unit_price", "discount_pct")
MONTHS_PER_PERIOD = {"monthly": 1, "annual": 12}

# §4: movement labels (order of the definitions table) and amount columns
MOVEMENT_TYPES = ("new", "reactivation", "expansion", "contraction", "churn", "unchanged")
AMOUNT_COLUMNS = ("new_mrr", "reactivation_mrr", "expansion_mrr", "contraction_mrr", "churn_mrr")

# §4.1: components in the fixed decomposition order, and the waterfall columns
# holding the sum of their positive / negative customer-month values
COMPONENTS = ("plan", "seats", "billing_period", "discount")
COMPONENT_COLUMNS = tuple(f"comp_{c}" for c in COMPONENTS)
GROSS_COMPONENT_COLUMNS = {
    "comp_plan": ("gross_plan_upgrade", "gross_plan_downgrade"),
    "comp_seats": ("gross_seat_increase", "gross_seat_decrease"),
    "comp_billing_period": ("gross_billing_to_monthly", "gross_billing_to_annual"),
    "comp_discount": ("gross_discount_decrease", "gross_discount_increase"),
}
GROSS_COLUMNS = tuple(col for pair in GROSS_COMPONENT_COLUMNS.values() for col in pair)

# §5: churned MRR by cancel_reason of the subscription that ended
CHURN_REASON_COLUMNS = {"voluntary": "churn_mrr_voluntary", "payment_failed": "churn_mrr_involuntary"}
CHURN_NO_REASON = "churn_mrr_no_reason"

# §7: TTM points whose base has fewer customers are shown greyed
REPORTABLE_BASE_CUSTOMERS = 1_000

# Comparison tolerances: money in dollars, ratios absolute, counts and labels exact
MONEY_TOL = 0.01
RATIO_TOL = 1e-9
TOLERANCE = {"money": MONEY_TOL, "ratio": RATIO_TOL, "count": 0.0}
N_EXAMPLES = 5

ONE_DAY = np.timedelta64(1, "D")
ONE_MONTH = np.timedelta64(1, "M")


# ---------------------------------------------------------------------------
# 1. Sources and exclusions (§2)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Sources:
    """Raw source tables with dates parsed; no exclusions applied yet."""

    plans: pd.DataFrame
    customers: pd.DataFrame
    subscriptions: pd.DataFrame
    plan_changes: pd.DataFrame


def load_sources(raw_dir: Path) -> Sources:
    """Read the four source tables the revenue metrics are built from."""

    def read(name: str) -> pd.DataFrame:
        return pd.read_parquet(raw_dir / f"{name}.parquet")

    subscriptions, plan_changes = read("subscriptions"), read("plan_changes")
    for frame, cols in ((subscriptions, ("start_date", "end_date")),
                        (plan_changes, ("requested_date", "effective_date"))):
        for col in cols:
            frame[col] = pd.to_datetime(frame[col]).astype("datetime64[ns]")
    return Sources(read("plans"), read("customers"), subscriptions, plan_changes)


def internal_accounts(customers: pd.DataFrame) -> pd.DataFrame:
    """§2: internal / test accounts, excluded from every metric.

    An account is internal if its email domain is the company's own, or if its
    name matches one of the whole-token patterns (case-insensitive). Whole tokens
    matter: a plain substring test for "test" would also drop real customers
    such as "Granitestack Data".
    """
    name_rx = re.compile("|".join(INTERNAL_NAME_PATTERNS), flags=re.IGNORECASE)
    by_domain = customers["email_domain"].eq(INTERNAL_EMAIL_DOMAIN).to_numpy(dtype=bool)
    by_name = np.array([isinstance(n, str) and bool(name_rx.search(n)) for n in customers["company_name"]])
    return customers.loc[by_domain | by_name, ["customer_id", "company_name", "email_domain"]].reset_index(drop=True)


# ---------------------------------------------------------------------------
# 2. Subscription terms and MRR (§3)
# ---------------------------------------------------------------------------


def _exact_int(values: Any, scale: int, name: str) -> np.ndarray:
    """``values × scale`` as int64; refuses values that are not whole multiples of 1/scale."""
    scaled = np.asarray(values, dtype=float) * scale
    rounded = np.round(scaled)
    if np.any(np.abs(scaled - rounded) > 1e-6):
        raise ValueError(f"{name} is not a whole multiple of 1/{scale}: exact cent arithmetic does not apply")
    return rounded.astype(np.int64)


def mrr_fraction(seats: Any, unit_price: Any, billing_period: Any,
                 discount_pct: Any) -> tuple[np.ndarray, np.ndarray]:
    """Unrounded §3 subscription MRR in cents, as an exact fraction (numerator, denominator).

    seats × monthly-equivalent unit price × (1 − discount_pct), where the
    monthly-equivalent price is ``unit_price`` for monthly billing and
    ``unit_price / 12`` for annual billing. With the price in cents and the
    discount in basis points this is

        seats × price_cents × (10_000 − discount_bp) / (10_000 × months_per_period)
    """
    seats = np.asarray(seats, dtype=np.int64)
    price_cents = _exact_int(unit_price, 100, "unit_price")
    discount_bp = _exact_int(discount_pct, 10_000, "discount_pct")
    if np.any(seats < 0) or np.any(price_cents < 0) or np.any((discount_bp < 0) | (discount_bp > 10_000)):
        raise ValueError("negative seats or price, or discount_pct outside [0, 1] (§1)")
    months = pd.Series(np.asarray(billing_period, dtype=object)).map(MONTHS_PER_PERIOD)
    if months.isna().any():
        raise ValueError(f"unknown billing period(s): {sorted(set(np.asarray(billing_period)[months.isna()]))}")
    return seats * price_cents * (10_000 - discount_bp), 10_000 * months.to_numpy(dtype=np.int64)


def subscription_mrr_cents(seats: Any, unit_price: Any, billing_period: Any, discount_pct: Any) -> np.ndarray:
    """§3 subscription MRR = ROUND(seats × monthly-equivalent price × (1 − discount), 2), in cents.

    The exact fraction from :func:`mrr_fraction` is rounded half-up to a whole cent.
    """
    numerator, denominator = mrr_fraction(seats, unit_price, billing_period, discount_pct)
    return (2 * numerator + denominator) // (2 * denominator)  # half-up; all terms are >= 0


def valid_plan_changes(plan_changes: pd.DataFrame, subscriptions: pd.DataFrame,
                       as_of: pd.Timestamp) -> tuple[pd.DataFrame, pd.DataFrame]:
    """§3: the plan_changes rows that apply, in application order, and the dropped rows.

    A row applies when ``start_date <= effective_date < end_date`` (null end_date =
    open) and ``effective_date <= as_of``. Rows are applied in order of
    effective_date, requested_date, change_id. Joining to the already-filtered
    subscriptions drops rows of internal accounts; ``customer_id`` is taken from
    the subscription (the row's own copy is kept as ``customer_id_row``).
    """
    subs = subscriptions[["subscription_id", "customer_id", "start_date", "end_date"]]
    rows = plan_changes.merge(subs, on="subscription_id", how="inner", suffixes=("_row", ""),
                              validate="many_to_one")
    applies = ((rows["effective_date"] >= rows["start_date"])
               & (rows["end_date"].isna() | (rows["effective_date"] < rows["end_date"]))
               & (rows["effective_date"] <= as_of))
    valid = (rows[applies]
             .sort_values(["subscription_id", "effective_date", "requested_date", "change_id"], kind="mergesort")
             .reset_index(drop=True))
    return valid, rows[~applies].reset_index(drop=True)


def chaining_violations(valid_changes: pd.DataFrame, subscriptions: pd.DataFrame) -> int:
    """§3 chaining invariant: in application order, the first row's ``from_*`` equals
    the subscription's ``initial_*`` and every later row's ``from_*`` equals the previous
    row's ``to_*``. Returns the number of rows that break it (expected 0)."""
    by_sub = valid_changes.groupby("subscription_id", sort=False)
    first = (by_sub.cumcount() == 0).to_numpy()
    initial = subscriptions.set_index("subscription_id")
    broken = np.zeros(len(valid_changes), dtype=bool)
    for attr in TERM_ATTRS:
        expected = np.where(first,
                            valid_changes["subscription_id"].map(initial[f"initial_{attr}"]).astype(object),
                            by_sub[f"to_{attr}"].shift(1).astype(object))
        broken |= valid_changes[f"from_{attr}"].astype(object).to_numpy() != expected
    return int(broken.sum())


def subscription_versions(subscriptions: pd.DataFrame, valid_changes: pd.DataFrame) -> pd.DataFrame:
    """End-of-day subscription terms: one row per (subscription, day the terms changed).

    Version 0 is the initial terms from ``start_date``; every valid change row
    starts a version on its effective_date with the row's ``to_*`` terms. The
    snapshot convention (§1) is the state at the end of the day, so when several
    rows share an effective_date only the last one in application order is kept.
    A version is in force on ``[valid_from, valid_to)``: until the next version
    starts or the subscription ends (``end_date`` is exclusive; NaT = still live).
    """
    initial = pd.DataFrame({
        "subscription_id": subscriptions["subscription_id"].to_numpy(),
        "customer_id": subscriptions["customer_id"].to_numpy(),
        "valid_from": subscriptions["start_date"].to_numpy(),
        "seq": 0,
        **{attr: subscriptions[f"initial_{attr}"].to_numpy() for attr in TERM_ATTRS},
    })
    changed = pd.DataFrame({
        "subscription_id": valid_changes["subscription_id"].to_numpy(),
        "customer_id": valid_changes["customer_id"].to_numpy(),
        "valid_from": valid_changes["effective_date"].to_numpy(),
        "seq": valid_changes.groupby("subscription_id", sort=False).cumcount().to_numpy() + 1,
        **{attr: valid_changes[f"to_{attr}"].to_numpy() for attr in TERM_ATTRS},
    })
    versions = (pd.concat([initial, changed], ignore_index=True)
                .sort_values(["subscription_id", "valid_from", "seq"], kind="mergesort")
                .drop_duplicates(["subscription_id", "valid_from"], keep="last")
                .reset_index(drop=True))
    end_date = versions["subscription_id"].map(subscriptions.set_index("subscription_id")["end_date"])
    versions["valid_to"] = versions.groupby("subscription_id")["valid_from"].shift(-1).fillna(end_date)
    versions["mrr_cents"] = subscription_mrr_cents(versions["seats"], versions["unit_price"],
                                                   versions["billing_period"], versions["discount_pct"])
    return versions


def month_end_calendar(first_day: pd.Timestamp, as_of: pd.Timestamp) -> np.ndarray:
    """Last day of every calendar month from ``first_day``'s month to ``as_of`` (day precision)."""
    months = np.arange(np.datetime64(first_day, "M"), np.datetime64(as_of, "M") + ONE_MONTH)
    ends = (months + ONE_MONTH).astype("datetime64[D]") - ONE_DAY
    return ends[ends <= np.datetime64(as_of, "D")]


def _offsets_within_runs(lengths: np.ndarray) -> np.ndarray:
    """For run lengths [2, 3] return [0, 1, 0, 1, 2] (position inside each run)."""
    starts = np.cumsum(lengths) - lengths
    return np.arange(lengths.sum()) - np.repeat(starts, lengths)


@dataclass(frozen=True)
class MrrPanel:
    """Customer MRR at every month-end, as a matrix (§3).

    ``cents[c, j]`` is the MRR of ``customer_ids[c]`` at ``month_ends[j]`` in integer
    cents: the sum of subscription MRR over the subscriptions live on that day.
    Only customers paying at one month-end or more are kept; ``first_paid[c]`` is
    the index of the first such month-end. ``sub_months`` is the long form per
    live subscription (used to split churn by cancel reason).
    """

    customer_ids: np.ndarray
    month_ends: np.ndarray
    cents: np.ndarray
    first_paid: np.ndarray
    sub_months: pd.DataFrame

    @property
    def in_panel(self) -> np.ndarray:
        """Dense panel mask: every month-end from the first paid one to the as-of date."""
        return np.arange(len(self.month_ends))[None, :] >= self.first_paid[:, None]

    @property
    def labels(self) -> np.ndarray:
        """Month-ends as ISO date strings (the join key used with the warehouse)."""
        return np.datetime_as_string(self.month_ends, unit="D")


def build_mrr_panel(versions: pd.DataFrame, as_of: pd.Timestamp) -> MrrPanel:
    """§3: customer MRR at each month-end, from the subscription versions.

    A version covers the month-ends D with ``valid_from <= D < valid_to``; this is
    "live on D" (start_date <= D < end_date) restricted to the days on which the
    version's terms are in force.
    """
    month_ends = month_end_calendar(versions["valid_from"].min(), as_of)
    valid_from = versions["valid_from"].to_numpy().astype("datetime64[D]")
    valid_to = versions["valid_to"].to_numpy().astype("datetime64[D]")
    valid_to = np.where(np.isnat(valid_to), np.datetime64(as_of, "D") + ONE_DAY, valid_to)

    first = np.searchsorted(month_ends, valid_from, side="left")  # first month-end on/after valid_from
    stop = np.searchsorted(month_ends, valid_to, side="left")     # first month-end on/after valid_to
    n_covered = np.maximum(stop - first, 0)
    version_row = np.repeat(np.arange(len(versions)), n_covered)
    sub_months = pd.DataFrame({
        "subscription_id": versions["subscription_id"].to_numpy()[version_row],
        "customer_id": versions["customer_id"].to_numpy()[version_row],
        "j": np.repeat(first, n_covered) + _offsets_within_runs(n_covered),
        "mrr_cents": versions["mrr_cents"].to_numpy()[version_row],
    })

    totals = sub_months.groupby(["customer_id", "j"])["mrr_cents"].sum().unstack("j", fill_value=0)
    totals = totals.reindex(columns=range(len(month_ends)), fill_value=0)
    totals = totals[(totals > 0).any(axis=1)].sort_index()
    cents = totals.to_numpy(dtype=np.int64)
    return MrrPanel(customer_ids=totals.index.to_numpy(dtype=object), month_ends=month_ends, cents=cents,
                    first_paid=(cents > 0).argmax(axis=1), sub_months=sub_months)


def customer_mrr_frame(panel: MrrPanel) -> pd.DataFrame:
    """Long dense panel: one row per customer and month-end from the first paid month-end (cents)."""
    ci, j = np.nonzero(panel.in_panel)
    first = panel.first_paid[ci]
    return pd.DataFrame({
        "customer_id": panel.customer_ids[ci],
        "month_end": panel.labels[j],
        "mrr": panel.cents[ci, j],
        "is_paying": panel.cents[ci, j] > 0,
        "months_since_first_paid": j - first,
        "cohort_month_end": panel.labels[first],
    })


# ---------------------------------------------------------------------------
# 3. Movements (§4) and components (§4.1)
# ---------------------------------------------------------------------------


def mrr_movements(panel: MrrPanel) -> pd.DataFrame:
    """§4: classify every customer-month of the panel by comparing prev and cur MRR (cents).

    New vs Reactivation is decided from month-end history only: Reactivation if
    the customer was paying at any earlier month-end.
    """
    cur = panel.cents
    prev = np.zeros_like(cur)
    prev[:, 1:] = cur[:, :-1]
    paying = cur > 0
    paid_earlier = np.zeros_like(paying)
    paid_earlier[:, 1:] = np.logical_or.accumulate(paying, axis=1)[:, :-1]

    conditions = {  # one entry per row of the §4 table; mutually exclusive and exhaustive
        "new": (prev == 0) & paying & ~paid_earlier,
        "reactivation": (prev == 0) & paying & paid_earlier,
        "expansion": (prev > 0) & (cur > prev),
        "contraction": (prev > 0) & paying & (cur < prev),
        "churn": (prev > 0) & ~paying,
        "unchanged": prev == cur,
    }
    assert np.all(np.sum(list(conditions.values()), axis=0) == 1), "movement rules must partition the panel"
    movement = np.select(list(conditions.values()), list(conditions), default="")
    amounts = {
        "new_mrr": np.where(conditions["new"], cur, 0),
        "reactivation_mrr": np.where(conditions["reactivation"], cur, 0),
        "expansion_mrr": np.where(conditions["expansion"], cur - prev, 0),
        "contraction_mrr": np.where(conditions["contraction"], prev - cur, 0),
        "churn_mrr": np.where(conditions["churn"], prev, 0),
    }

    ci, j = np.nonzero(panel.in_panel)
    frame = pd.DataFrame({
        "customer_id": panel.customer_ids[ci],
        "month_end": panel.labels[j],
        "j": j,
        "movement_type": movement[ci, j],
        "mrr_prev": prev[ci, j],
        "mrr": cur[ci, j],
        **{name: values[ci, j] for name, values in amounts.items()},
    })
    frame["net_change"] = frame["mrr"] - frame["mrr_prev"]
    return frame


def list_unit_prices(plans: pd.DataFrame) -> dict[tuple[str, str], float]:
    """List ``unit_price`` per seat per billing period, keyed by (plan_id, billing_period).

    Enterprise has no monthly list price (annual only); where one is needed (the
    plan step of §4.1 for a monthly subscription moving to Enterprise) it is the
    annual list ÷ 12, as in the billing-neutral MRR of §3.
    """
    prices = {}
    for plan in plans.itertuples():
        monthly = plan.monthly_price_per_seat
        prices[(plan.plan_id, "annual")] = float(plan.annual_price_per_seat)
        prices[(plan.plan_id, "monthly")] = float(monthly) if pd.notna(monthly) else plan.annual_price_per_seat / 12
    return prices


def change_components(valid_changes: pd.DataFrame, plans: pd.DataFrame) -> pd.DataFrame:
    """§4.1: split every valid plan_changes row into plan / seats / billing / discount (cents).

    One attribute changes at a time, in the fixed order plan → seats → billing
    period → discount. Every intermediate MRR is the §3 formula rounded to the
    cent, so the four parts telescope exactly to the row's ΔMRR.

    Unit price of the intermediate states: the plan step uses the list price of
    the new plan on the old billing period (the old unit price when the plan is
    unchanged); once plan and billing period are both the new ones, the row's
    ``to_unit_price`` applies.
    """
    c = valid_changes
    prices = list_unit_prices(plans)
    new_plan_old_period = np.array([prices[key] for key in zip(c["to_plan_id"], c["from_billing_period"])])
    plan_step_price = np.where(c["to_plan_id"] != c["from_plan_id"], new_plan_old_period, c["from_unit_price"])

    mrr = subscription_mrr_cents
    states = [  # MRR(seats, unit price, billing period, discount) after each step
        mrr(c["from_seats"], c["from_unit_price"], c["from_billing_period"], c["from_discount_pct"]),  # from terms
        mrr(c["from_seats"], plan_step_price, c["from_billing_period"], c["from_discount_pct"]),      # + plan
        mrr(c["to_seats"], plan_step_price, c["from_billing_period"], c["from_discount_pct"]),        # + seats
        mrr(c["to_seats"], c["to_unit_price"], c["to_billing_period"], c["from_discount_pct"]),       # + billing
        mrr(c["to_seats"], c["to_unit_price"], c["to_billing_period"], c["to_discount_pct"]),         # to terms
    ]
    out = c[["change_id", "subscription_id", "customer_id", "effective_date", "change_type", "change_source"]].copy()
    for col, before, after in zip(COMPONENT_COLUMNS, states[:-1], states[1:]):
        out[col] = after - before
    out["delta_mrr"] = states[-1] - states[0]
    return out


def customer_month_components(movements: pd.DataFrame, components: pd.DataFrame,
                              panel: MrrPanel) -> pd.DataFrame:
    """§4.1: components per continuing customer-month (prev > 0 and cur > 0), in cents.

    Each component is summed over all of the customer's change rows effective in
    the month (any subscription); ``comp_other`` = (cur − prev) − Σ components
    (mid-month win-backs, rounding).
    """
    month_index = np.searchsorted(panel.month_ends, components["effective_date"].to_numpy().astype("datetime64[D]"))
    per_month = (components.assign(j=month_index)
                 .groupby(["customer_id", "j"])
                 .agg(**{col: (col, "sum") for col in COMPONENT_COLUMNS}, change_rows=("change_id", "size"))
                 .reset_index())
    continuing = movements.loc[(movements["mrr_prev"] > 0) & (movements["mrr"] > 0),
                               ["customer_id", "month_end", "j", "movement_type", "mrr_prev", "mrr", "net_change"]]
    out = continuing.merge(per_month, on=["customer_id", "j"], how="left", validate="one_to_one")
    filled = [*COMPONENT_COLUMNS, "change_rows"]
    out[filled] = out[filled].fillna(0).astype(np.int64)
    out["comp_other"] = out["net_change"] - out[list(COMPONENT_COLUMNS)].sum(axis=1)
    return out


# ---------------------------------------------------------------------------
# 4. Waterfall and flow rates (§4, §5)
# ---------------------------------------------------------------------------


def churn_by_reason(movements: pd.DataFrame, panel: MrrPanel, subscriptions: pd.DataFrame) -> pd.DataFrame:
    """§5: churned MRR by ``cancel_reason`` of the subscription that ended (cents, by month index).

    The churned amount is the MRR at the previous month-end, i.e. the MRR of the
    subscription(s) live then, so each is attributed to its own cancel_reason.
    """
    churned = movements.loc[movements["movement_type"] == "churn", ["customer_id", "j"]]
    live_before = panel.sub_months.assign(j=panel.sub_months["j"] + 1)  # previous month-end, keyed by month m
    lost = (churned.merge(live_before, on=["customer_id", "j"])
            .merge(subscriptions[["subscription_id", "cancel_reason"]], on="subscription_id"))
    bucket = lost["cancel_reason"].map(CHURN_REASON_COLUMNS).fillna(CHURN_NO_REASON)
    return (lost.assign(bucket=bucket)
            .pivot_table(index="j", columns="bucket", values="mrr_cents", aggfunc="sum", fill_value=0)
            .reindex(columns=[*CHURN_REASON_COLUMNS.values(), CHURN_NO_REASON], fill_value=0))


def mrr_waterfall(movements: pd.DataFrame, components: pd.DataFrame, churn_reasons: pd.DataFrame,
                  panel: MrrPanel) -> pd.DataFrame:
    """Monthly MRR waterfall (§4) with customer counts, component totals (§4.1) and rates (§3, §5).

    Money columns are integer cents; rates are plain ratios (undefined without a start base).
    """
    flags = movements.assign(
        paying_start=movements["mrr_prev"] > 0,
        paying_end=movements["mrr"] > 0,
        **{f"is_{t}": movements["movement_type"].eq(t) for t in MOVEMENT_TYPES},
    )
    wf = flags.groupby("j").agg(
        mrr_start=("mrr_prev", "sum"),
        **{col: (col, "sum") for col in AMOUNT_COLUMNS},
        mrr_end=("mrr", "sum"),
        customers_start=("paying_start", "sum"),
        customers_end=("paying_end", "sum"),
        new_customers=("is_new", "sum"),
        expanding_customers=("is_expansion", "sum"),
        contracting_customers=("is_contraction", "sum"),
        churned_customers=("is_churn", "sum"),
        reactivated_customers=("is_reactivation", "sum"),
    )
    wf["identity_gap"] = wf["mrr_end"] - (wf["mrr_start"] + wf["new_mrr"] + wf["expansion_mrr"]
                                          + wf["reactivation_mrr"] - wf["contraction_mrr"] - wf["churn_mrr"])
    wf = wf.join(churn_reasons).fillna(0)

    # §4.1 reporting: per month, the sum of positive and of negative customer-month values
    for col, (positive, negative) in GROSS_COMPONENT_COLUMNS.items():
        wf[positive] = components[col].clip(lower=0).groupby(components["j"]).sum()
        wf[negative] = components[col].clip(upper=0).groupby(components["j"]).sum()
    wf["component_other"] = components.groupby("j")["comp_other"].sum()
    money = ["mrr_start", *AMOUNT_COLUMNS, "mrr_end", "identity_gap", *churn_reasons.columns, *GROSS_COLUMNS,
             "component_other"]
    wf[money] = wf[money].fillna(0).astype(np.int64)

    wf["arr_end"] = 12 * wf["mrr_end"]
    start = wf["mrr_start"].where(wf["mrr_start"] > 0)
    wf["arpa"] = wf["mrr_end"] / 100 / wf["customers_end"].where(wf["customers_end"] > 0)
    wf["logo_churn_rate"] = wf["churned_customers"] / wf["customers_start"].where(wf["customers_start"] > 0)
    wf["gross_mrr_churn_rate"] = (wf["churn_mrr"] + wf["contraction_mrr"]) / start
    wf["net_mrr_churn_rate"] = (wf["churn_mrr"] + wf["contraction_mrr"] - wf["expansion_mrr"]) / start
    wf["monthly_net_retention_flow"] = (wf["mrr_start"] + wf["expansion_mrr"] - wf["contraction_mrr"]
                                        - wf["churn_mrr"]) / start
    wf["monthly_gross_retention_flow"] = (wf["mrr_start"] - wf["contraction_mrr"] - wf["churn_mrr"]) / start
    wf.insert(0, "month_end", panel.labels[wf.index.to_numpy()])
    return wf.reset_index()


# ---------------------------------------------------------------------------
# 5. Retention: TTM NRR / GRR (§7, §8), quarterly run-rate NRR (§7)
# ---------------------------------------------------------------------------


def _point_to_point(panel: MrrPanel, t: int, lag: int) -> dict[str, Any]:
    """Retention at month-end ``t`` of the customers paying at ``t − lag`` (money in cents)."""
    base = panel.cents[:, t - lag] > 0
    start, now = panel.cents[base, t - lag], panel.cents[base, t]
    return {
        "base_customers": int(base.sum()),
        "base_mrr": int(start.sum()),
        "retained_mrr": int(now.sum()),
        "retained_mrr_capped": int(np.minimum(now, start).sum()),  # each customer capped at its start MRR
        "logo_retention": float((now > 0).mean()) if base.any() else math.nan,
    }


def ttm_retention(panel: MrrPanel) -> pd.DataFrame:
    """§7 / §8: NRR, GRR and logo retention over 12 months, for every month-end with a base."""
    rows = []
    for t in range(12, len(panel.month_ends)):
        r = _point_to_point(panel, t, 12)
        if r["base_customers"] == 0:
            continue
        rows.append({"month_end": panel.labels[t], "base_month_end": panel.labels[t - 12], **r,
                     "nrr": r["retained_mrr"] / r["base_mrr"],
                     "grr": r["retained_mrr_capped"] / r["base_mrr"],
                     "is_reportable": r["base_customers"] >= REPORTABLE_BASE_CUSTOMERS})
    return pd.DataFrame(rows)


def quarterly_runrate_nrr(panel: MrrPanel) -> pd.DataFrame:
    """§7 quarterly run-rate NRR: customers paying at t − 3, MRR at t ÷ MRR at t − 3, annualised ^4.

    The quarterly GRR (capped as in §8) is not defined in the document; it is
    computed only to compare with the warehouse columns of the same name.
    """
    rows = []
    for t in range(3, len(panel.month_ends)):
        if panel.month_ends[t].astype(object).month % 3:
            continue  # not a quarter-end
        r = _point_to_point(panel, t, 3)
        if r["base_customers"] == 0:
            continue
        nrr_q, grr_q = r["retained_mrr"] / r["base_mrr"], r["retained_mrr_capped"] / r["base_mrr"]
        rows.append({"quarter_end": panel.labels[t], "base_customers": r["base_customers"],
                     "base_mrr": r["base_mrr"], "nrr_quarter": nrr_q, "nrr_runrate_annualized": nrr_q ** 4,
                     "grr_quarter": grr_q, "grr_runrate_annualized": grr_q ** 4})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 6. Cohorts (§10)
# ---------------------------------------------------------------------------


def cohort_retention(panel: MrrPanel) -> pd.DataFrame:
    """§10: cohort NRR / GRR / logo retention by month index k, for monthly and quarterly cohorts.

    k counts months from each customer's own first paid month-end. A cell exists
    once at least one member has reached k; it is complete when every member has.
    Per §10 only complete cells carry ``nrr`` / ``grr`` / ``logo_retention``
    (partial cells are blank: NaN). The ``*_observed`` columns hold the same
    ratios over the members that have reached k, for information.
    """
    first = panel.first_paid
    horizon = len(panel.month_ends) - 1 - first          # last k each customer has reached
    member = np.repeat(np.arange(len(first)), horizon + 1)
    k = _offsets_within_runs(horizon + 1)
    mrr_k = panel.cents[member, first[member] + k]
    mrr_0 = panel.cents[member, first[member]]
    start_mrr = panel.cents[np.arange(len(first)), first]  # per customer, MRR at its first paid month-end

    first_month = panel.month_ends[first].astype("datetime64[M]").astype(object)  # datetime.date
    cohort_of = {
        "month": np.array([f"{d.year}-{d.month:02d}" for d in first_month]),
        "quarter": np.array([f"{d.year}-Q{(d.month - 1) // 3 + 1}" for d in first_month]),
    }
    frames = []
    for grain, cohort in cohort_of.items():
        cells = (pd.DataFrame({"cohort": cohort[member], "months_since_first_paid": k, "mrr_k": mrr_k,
                               "mrr_0": mrr_0, "capped": np.minimum(mrr_k, mrr_0), "paying": mrr_k > 0})
                 .groupby(["cohort", "months_since_first_paid"])
                 .agg(customers_observed=("mrr_k", "size"), mrr_k=("mrr_k", "sum"),
                      base_mrr_observed=("mrr_0", "sum"), capped=("capped", "sum"), paying=("paying", "sum"))
                 .reset_index())
        cells.insert(0, "cohort_grain", grain)
        cells["cohort_customers"] = cells["cohort"].map(pd.Series(cohort).value_counts())
        cells["base_mrr"] = cells["cohort"].map(pd.Series(start_mrr).groupby(cohort).sum())
        cells["is_complete"] = cells["customers_observed"] == cells["cohort_customers"]
        cells["nrr_observed"] = cells["mrr_k"] / cells["base_mrr_observed"]
        cells["grr_observed"] = cells["capped"] / cells["base_mrr_observed"]
        cells["logo_retention_observed"] = cells["paying"] / cells["customers_observed"]
        for col in ("nrr", "grr", "logo_retention"):  # §10: partial cells are blank, not zero
            cells[col] = cells[f"{col}_observed"].where(cells["is_complete"])
        frames.append(cells.drop(columns=["capped", "paying"]))
    return pd.concat(frames, ignore_index=True)


# ---------------------------------------------------------------------------
# 7. The whole chain
# ---------------------------------------------------------------------------


@dataclass
class Metrics:
    """Everything the cross-check computes. Money columns are in dollars."""

    customer_mrr: pd.DataFrame
    movements: pd.DataFrame
    change_components: pd.DataFrame
    movement_components: pd.DataFrame
    waterfall: pd.DataFrame
    ttm: pd.DataFrame
    runrate: pd.DataFrame
    cohorts: pd.DataFrame
    data_checks: dict[str, Any]


def _to_dollars(frame: pd.DataFrame, columns: Any) -> pd.DataFrame:
    return frame.assign(**{col: frame[col] / 100 for col in columns})


def _off_list_prices(changes: pd.DataFrame, plans: pd.DataFrame) -> int:
    """Number of from / to terms on change rows whose unit price is not the list price."""
    prices = list_unit_prices(plans)
    return sum(int(sum(price != prices[(plan, period)] for plan, period, price in
                       zip(changes[f"{side}_plan_id"], changes[f"{side}_billing_period"],
                           changes[f"{side}_unit_price"])))
               for side in ("from", "to"))


def compute_metrics(settings: Settings | None = None) -> Metrics:
    """Run the metric chain of the definitions document on the raw data."""
    settings = settings or get_settings()
    as_of = pd.Timestamp(settings.as_of_date)
    src = load_sources(settings.raw_dir)

    # §2 exclusions, then §3 terms and MRR (nothing after the as-of date is used)
    internal = internal_accounts(src.customers)
    subscriptions = src.subscriptions[~src.subscriptions["customer_id"].isin(internal["customer_id"])
                                      & (src.subscriptions["start_date"] <= as_of)].reset_index(drop=True)
    changes, dropped = valid_plan_changes(src.plan_changes, subscriptions, as_of)
    versions = subscription_versions(subscriptions, changes)
    panel = build_mrr_panel(versions, as_of)

    # §4 movements and §4.1 components
    movements = mrr_movements(panel)
    row_components = change_components(changes, src.plans)
    components = customer_month_components(movements, row_components, panel)

    # §5 waterfall, §7 / §8 retention, §10 cohorts
    waterfall = mrr_waterfall(movements, components, churn_by_reason(movements, panel, subscriptions), panel)
    ttm, runrate, cohorts = ttm_retention(panel), quarterly_runrate_nrr(panel), cohort_retention(panel)

    numerator, denominator = mrr_fraction(versions["seats"], versions["unit_price"], versions["billing_period"],
                                          versions["discount_pct"])
    tie_out = (waterfall[list(GROSS_COLUMNS)].sum(axis=1) + waterfall["component_other"]
               - (waterfall["expansion_mrr"] - waterfall["contraction_mrr"]))
    data_checks = {
        "internal_accounts_excluded": internal.to_dict("records"),
        "internal_accounts_ok": len(internal) == EXPECTED_INTERNAL_ACCOUNTS,
        "plan_change_rows": {"total": len(src.plan_changes), "valid": len(changes),
                             "dropped": dropped["change_id"].tolist()},
        "plan_change_customer_id_mismatches": int((changes["customer_id_row"] != changes["customer_id"]).sum()),
        "chaining_violations": chaining_violations(changes, subscriptions),
        "same_day_change_groups": int((changes.groupby(["subscription_id", "effective_date"]).size() > 1).sum()),
        "change_terms_off_list_price": _off_list_prices(changes, src.plans),
        "subscription_versions_with_fractional_cent_mrr": int((numerator % denominator != 0).sum()),
        "component_rows_sum_to_delta_mrr": bool((row_components[list(COMPONENT_COLUMNS)].sum(axis=1)
                                                 == row_components["delta_mrr"]).all()),
        "change_rows_outside_continuing_months": int(len(row_components) - components["change_rows"].sum()),
        "waterfall_identity_max_gap_cents": int(waterfall["identity_gap"].abs().max()),
        "component_tie_out_max_gap_cents": int(tie_out.abs().max()),
        "churn_without_cancel_reason_cents": int(waterfall[CHURN_NO_REASON].sum()),
        "customers_in_panel": len(panel.customer_ids),
        "customer_months": int(panel.in_panel.sum()),
        "month_ends": [str(panel.labels[0]), str(panel.labels[-1])],
    }

    return Metrics(
        customer_mrr=_to_dollars(customer_mrr_frame(panel), ["mrr"]),
        movements=_to_dollars(movements, ["mrr_prev", "mrr", *AMOUNT_COLUMNS, "net_change"]),
        change_components=_to_dollars(row_components, [*COMPONENT_COLUMNS, "delta_mrr"]),
        movement_components=_to_dollars(components, ["mrr_prev", "mrr", "net_change", *COMPONENT_COLUMNS,
                                                     "comp_other"]),
        waterfall=_to_dollars(waterfall, ["mrr_start", *AMOUNT_COLUMNS, "mrr_end", "identity_gap", "arr_end",
                                          *CHURN_REASON_COLUMNS.values(), CHURN_NO_REASON, *GROSS_COLUMNS,
                                          "component_other"]),
        ttm=_to_dollars(ttm, ["base_mrr", "retained_mrr", "retained_mrr_capped"]),
        runrate=_to_dollars(runrate, ["base_mrr"]),
        cohorts=_to_dollars(cohorts, ["mrr_k", "base_mrr", "base_mrr_observed"]),
        data_checks=data_checks,
    )


# ---------------------------------------------------------------------------
# 8. Comparison with the warehouse
# ---------------------------------------------------------------------------

WAREHOUSE_QUERIES = {
    "customer_mrr": """SELECT customer_id, month_end, mrr, is_paying, months_since_first_paid, cohort_month_end
                       FROM fct_customer_mrr_monthly""",
    "movements": """SELECT customer_id, month_end, movement_type, mrr_prev, mrr, new_mrr, reactivation_mrr,
                           expansion_mrr, contraction_mrr, churn_mrr, net_change
                    FROM fct_mrr_movements""",
    "waterfall": "SELECT * FROM rpt_mrr_waterfall_monthly",
    "ttm": "SELECT * FROM rpt_nrr_grr_ttm",
    "runrate": "SELECT * FROM rpt_nrr_quarterly_runrate",
    "movement_components": """SELECT customer_id, month_end, movement_type, mrr_prev, mrr, net_change, comp_plan,
                                     comp_seats, comp_billing_period, comp_discount, comp_other, change_rows
                              FROM fct_mrr_movement_components""",
    "cohorts": """SELECT cohort_grain, cohort, months_since_first_paid, cohort_customers, customers_observed,
                         base_mrr, mrr_k, nrr, grr, logo_retention, is_complete
                  FROM rpt_cohort_retention""",
}


def load_warehouse(path: Path) -> dict[str, pd.DataFrame]:
    """Read the SQL output tables (read-only); dates become ISO strings to match the Python keys."""
    import duckdb  # only used to read the SQL results, never to compute

    con = duckdb.connect(str(path), read_only=True)
    try:
        tables = {name: con.execute(sql).df() for name, sql in WAREHOUSE_QUERIES.items()}
    finally:
        con.close()
    for frame in tables.values():
        for col in frame.columns:
            if pd.api.types.is_datetime64_any_dtype(frame[col]):
                frame[col] = frame[col].dt.strftime("%Y-%m-%d")
    return tables


def _plain(value: Any) -> Any:
    """JSON-friendly scalar (numpy types unwrapped, NaN / NaT → None)."""
    if isinstance(value, np.generic):
        value = value.item()
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, (pd.Timestamp, datetime, date)):
        return value.isoformat()
    return value


def _records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    return [{k: _plain(v) for k, v in row.items()} for row in frame.to_dict("records")]


def compare_frames(name: str, python: pd.DataFrame, sql: pd.DataFrame, keys: list[str],
                   columns: dict[str, str], description: str = "", informational: bool = False) -> dict[str, Any]:
    """Outer-join on ``keys`` and compare each column.

    ``columns`` maps a column to its kind: ``money`` (dollars), ``ratio``,
    ``count`` or ``label``. Per column, ``n_over_tol`` counts rows differing by
    more than $0.01 (money) or 1e-9 (ratios); ``n_mismatch`` counts rows that
    differ at the check's resolution: by one cent or more for money (a plain
    "> $0.01" test is at the mercy of float noise for a one-cent difference),
    beyond 1e-9 for ratios, at all for counts and labels. A value missing on one
    side only is a mismatch; missing on both sides counts as equal.
    """
    cols = list(columns)
    joined = python[keys + cols].merge(sql[keys + cols], on=keys, how="outer",
                                       suffixes=("_py", "_sql"), indicator=True)
    both = joined[joined["_merge"] == "both"].reset_index(drop=True)
    only_py = joined.loc[joined["_merge"] == "left_only", keys]
    only_sql = joined.loc[joined["_merge"] == "right_only", keys]

    per_column, bad, over_tol = {}, {}, {}
    for col, kind in columns.items():
        a, b = both[f"{col}_py"], both[f"{col}_sql"]
        if kind == "label":
            a, b = a.astype(object), b.astype(object)
            bad[col] = over_tol[col] = ~((a == b) | (a.isna() & b.isna())).to_numpy(dtype=bool)
            per_column[col] = {"kind": kind, "n_mismatch": int(bad[col].sum())}
            continue
        x, y = pd.to_numeric(a).astype(float).to_numpy(), pd.to_numeric(b).astype(float).to_numpy()
        diff = np.abs(x - y)
        over_tol[col] = (diff > TOLERANCE[kind]) | (np.isnan(x) != np.isnan(y))
        if kind == "money":  # compare whole cents, whatever the float noise in the difference
            bad[col] = over_tol[col] | (~np.isnan(diff) & (np.round(x * 100) != np.round(y * 100)))
        else:
            bad[col] = over_tol[col]
        per_column[col] = {"kind": kind, "max_abs_diff": float(np.nanmax(diff)) if np.isfinite(diff).any() else 0.0,
                           "n_over_tol": int(over_tol[col].sum()), "n_mismatch": int(bad[col].sum())}

    row_bad = np.logical_or.reduce(list(bad.values()))
    examples = []
    for i in np.flatnonzero(row_bad)[:N_EXAMPLES]:
        example = {k: _plain(both.at[i, k]) for k in keys}
        example.update({col: {"python": _plain(both.at[i, f"{col}_py"]), "sql": _plain(both.at[i, f"{col}_sql"])}
                        for col in cols if bad[col][i]})
        examples.append(example)

    def largest(kinds: tuple[str, ...]) -> float | None:
        diffs = [v["max_abs_diff"] for v in per_column.values() if v["kind"] in kinds]
        return max(diffs) if diffs else None

    n_bad = int(row_bad.sum())
    return {
        "name": name,
        "description": description,
        "informational": informational,
        "keys": keys,
        "rows_compared": len(both),
        "rows_only_python": len(only_py),
        "rows_only_sql": len(only_sql),
        "max_abs_diff": largest(("money", "ratio", "count")),
        "max_abs_diff_money": largest(("money",)),
        "max_abs_diff_ratio": largest(("ratio",)),
        "n_over_tol_rows": int(np.logical_or.reduce(list(over_tol.values())).sum()),
        "n_mismatch_rows": n_bad,
        "status": "match" if n_bad == 0 and only_py.empty and only_sql.empty else "mismatch",
        "columns": per_column,
        "examples": examples,
        "examples_only_python": _records(only_py.head(N_EXAMPLES)),
        "examples_only_sql": _records(only_sql.head(N_EXAMPLES)),
    }


def run_comparisons(metrics: Metrics, warehouse: dict[str, pd.DataFrame]) -> list[dict[str, Any]]:
    """All Python-vs-SQL comparisons, in the order of the definitions document."""
    money, ratio, count, label = "money", "ratio", "count", "label"
    cm_keys, cohort_keys = ["customer_id", "month_end"], ["cohort_grain", "cohort", "months_since_first_paid"]
    mov_py, mov_sql = metrics.movements, warehouse["movements"]
    # The §4 table has no "not paying" state (prev = cur = 0 satisfies "Unchanged: prev = cur");
    # the mapped variant isolates real classification differences from that label difference.
    mov_sql_mapped = mov_sql.assign(movement_type=mov_sql["movement_type"].replace({"not_paying": "unchanged"}))
    coh_py, coh_sql = metrics.cohorts, warehouse["cohorts"]
    coh_py_partial = coh_py[~coh_py["is_complete"]].assign(
        base_mrr=lambda d: d["base_mrr_observed"], nrr=lambda d: d["nrr_observed"],
        grr=lambda d: d["grr_observed"], logo_retention=lambda d: d["logo_retention_observed"])
    cohort_values = {"base_mrr": money, "mrr_k": money, "nrr": ratio, "grr": ratio, "logo_retention": ratio}

    checks = [
        compare_frames("customer_mrr", metrics.customer_mrr, warehouse["customer_mrr"], cm_keys,
                       {"mrr": money, "is_paying": label, "months_since_first_paid": count,
                        "cohort_month_end": label},
                       "§3 customer MRR at every month-end of the dense panel (cent level)"),
        compare_frames("movement_type", mov_py, mov_sql, cm_keys, {"movement_type": label},
                       "§4 movement type per customer-month, labels as published"),
        compare_frames("movement_type_mapped", mov_py, mov_sql_mapped, cm_keys, {"movement_type": label},
                       "§4 movement type, reading SQL 'not_paying' (prev = cur = 0) as 'unchanged'"),
        compare_frames("movement_prev_cur", mov_py, mov_sql, cm_keys,
                       {"mrr_prev": money, "mrr": money, "net_change": money},
                       "§4 prev / cur MRR and net change per customer-month (cent level)"),
    ]
    checks += [compare_frames(f"movement_{col}", mov_py, mov_sql, cm_keys, {col: money},
                              f"§4 {col} per customer-month (cent level)") for col in AMOUNT_COLUMNS]
    checks += [
        compare_frames("waterfall_totals", metrics.waterfall, warehouse["waterfall"], ["month_end"],
                       {**{c: money for c in ("mrr_start", *AMOUNT_COLUMNS, "mrr_end", "identity_gap", "arr_end",
                                              *CHURN_REASON_COLUMNS.values())},
                        **{c: count for c in ("customers_start", "customers_end", "new_customers",
                                              "expanding_customers", "contracting_customers", "churned_customers",
                                              "reactivated_customers")}},
                       "§4 / §5 monthly waterfall totals, voluntary / involuntary churn, customer counts"),
        compare_frames("waterfall_rates", metrics.waterfall, warehouse["waterfall"], ["month_end"],
                       {c: ratio for c in ("arpa", "logo_churn_rate", "gross_mrr_churn_rate", "net_mrr_churn_rate",
                                           "monthly_net_retention_flow", "monthly_gross_retention_flow")},
                       "§3 ARPA and §5 monthly churn and flow-retention rates"),
        compare_frames("waterfall_components", metrics.waterfall, warehouse["waterfall"], ["month_end"],
                       {c: money for c in (*GROSS_COLUMNS, "component_other")},
                       "§4.1 gross expansion / contraction by component per month"),
        compare_frames("nrr_grr_ttm", metrics.ttm, warehouse["ttm"], ["month_end"],
                       {"base_month_end": label, "base_customers": count, "base_mrr": money,
                        "retained_mrr": money, "retained_mrr_capped": money, "nrr": ratio, "grr": ratio,
                        "logo_retention": ratio, "is_reportable": label},
                       "§7 / §8 TTM NRR, GRR and logo retention per month-end"),
        compare_frames("nrr_runrate_quarterly", metrics.runrate, warehouse["runrate"], ["quarter_end"],
                       {"base_customers": count, "base_mrr": money, "nrr_quarter": ratio,
                        "nrr_runrate_annualized": ratio},
                       "§7 quarterly run-rate NRR and its annualisation (^4)"),
        compare_frames("grr_runrate_quarterly", metrics.runrate, warehouse["runrate"], ["quarter_end"],
                       {"grr_quarter": ratio, "grr_runrate_annualized": ratio},
                       "Quarterly GRR: not defined in the document; §8 capping over the §7 quarterly window",
                       informational=True),
        compare_frames("movement_components", metrics.movement_components, warehouse["movement_components"],
                       cm_keys,
                       {**{c: money for c in (*COMPONENT_COLUMNS, "comp_other", "net_change")},
                        "change_rows": count, "movement_type": label},
                       "§4.1 components per continuing customer-month (cent level)"),
        compare_frames("cohort_cells", coh_py, coh_sql, cohort_keys,
                       {"is_complete": label, "cohort_customers": count, "customers_observed": count},
                       "§10 cohort cells: which cells exist, members, members observed, completeness flag"),
        compare_frames("cohort_values_complete", coh_py[coh_py["is_complete"]], coh_sql[coh_sql["is_complete"]],
                       cohort_keys, cohort_values, "§10 cohort NRR / GRR / logo retention on complete cells"),
        compare_frames("cohort_values_partial", coh_py_partial, coh_sql[~coh_sql["is_complete"]], cohort_keys,
                       cohort_values,
                       "Partial cells (§10: blank); compared over the members observed at k, for information",
                       informational=True),
    ]
    return checks


# ---------------------------------------------------------------------------
# 9. Explaining differences
# ---------------------------------------------------------------------------


def find_discrepancies(metrics: Metrics, warehouse: dict[str, pd.DataFrame],
                       comparisons: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Explain the differences between SQL and the document, from the data.

    Known patterns are recognised and explained; any other mismatch is listed
    as unexplained with its examples, so a regression cannot hide here.
    """
    found = []

    # §4: SQL labels prev = cur = 0 as 'not_paying'; the document's table only knows 'Unchanged'.
    mov = metrics.movements.merge(warehouse["movements"], on=["customer_id", "month_end"], suffixes=("_py", "_sql"))
    differs = mov["movement_type_py"] != mov["movement_type_sql"]
    label_only = (differs & mov["movement_type_py"].eq("unchanged") & mov["movement_type_sql"].eq("not_paying")
                  & mov["mrr_prev_py"].eq(0) & mov["mrr_py"].eq(0))
    if label_only.any():
        ex = mov[label_only].iloc[0]
        found.append({
            "id": "movement_label_not_paying",
            "section": "§4 movement table",
            "comparison": "movement_type",
            "severity": "label only: no amount, waterfall, NRR or GRR impact",
            "rows": int(label_only.sum()),
            "python": "unchanged: the §4 condition 'Unchanged | prev = cur' holds for prev = cur = 0",
            "sql": "not_paying: a movement type the document does not define",
            "example": {"customer_id": ex["customer_id"], "month_end": ex["month_end"], "mrr_prev": 0.0,
                        "mrr": 0.0, "python": ex["movement_type_py"], "sql": ex["movement_type_sql"]},
            "explanation": "Customer-months inside the panel where the customer is not paying (after a churn, "
                           "before any reactivation) are labelled 'not_paying' in fct_mrr_movements. The §4 table "
                           "defines six movements and none of them is 'not paying'; read literally, prev = cur = 0 "
                           "is 'Unchanged'. Amounts are 0 either way, but any count of 'unchanged' customer-months "
                           "differs. Fix in the document (add the state) or in SQL (use 'unchanged').",
        })

    # §10: SQL publishes values for partial (right-censored) cohort cells, flagged is_complete = false.
    partial = warehouse["cohorts"][~warehouse["cohorts"]["is_complete"]]
    filled = partial[partial[["nrr", "grr", "logo_retention"]].notna().any(axis=1)]
    if not filled.empty:
        ex = filled.sort_values(["cohort", "months_since_first_paid"]).iloc[-1]
        values_match = next(c for c in comparisons if c["name"] == "cohort_values_partial")["status"] == "match"
        found.append({
            "id": "cohort_partial_cells_published",
            "section": "§10 right-censoring",
            "comparison": "cohort_values_partial",
            "severity": "presentation: flagged by is_complete, values agree with the observed-member ratios"
                        if values_match else "presentation, and the partial values differ (see comparison)",
            "rows": int(len(filled)),
            "python": "nrr / grr / logo_retention blank (NaN) on partial cells ('Partial cells are blank, not "
                      "zero'); observed-member ratios kept in *_observed columns",
            "sql": "partial cells carry nrr / grr / logo_retention computed over the members observed at k",
            "example": {k: _plain(ex[k]) for k in ("cohort_grain", "cohort", "months_since_first_paid",
                                                   "cohort_customers", "customers_observed", "nrr", "grr",
                                                   "logo_retention")},
            "explanation": "Only quarterly cohorts have partial cells (members start in different months of the "
                           "quarter). Their values describe the earliest-starting members only, so a chart that "
                           "does not filter on is_complete would show a biased tail instead of a blank.",
        })

    # Anything else that does not match is listed with its examples, unexplained.
    explained_rows = int(label_only.sum())
    for comp in comparisons:
        if comp["status"] == "match" or comp["informational"]:
            continue
        unexplained = comp["n_mismatch_rows"] + comp["rows_only_python"] + comp["rows_only_sql"]
        if comp["name"] == "movement_type":
            unexplained -= explained_rows
        if unexplained:
            found.append({
                "id": f"unexplained_{comp['name']}",
                "section": comp["description"],
                "comparison": comp["name"],
                "severity": "unexplained",
                "rows": unexplained,
                "explanation": "Not recognised automatically; see the comparison's examples.",
                "examples": comp["examples"] or comp["examples_only_python"] or comp["examples_only_sql"],
            })
    return found


def ambiguities(metrics: Metrics) -> list[dict[str, Any]]:
    """Sentences of the definitions document that a second analyst could read differently."""
    checks = metrics.data_checks
    not_paying = int((metrics.movements["mrr_prev"].eq(0) & metrics.movements["mrr"].eq(0)).sum())
    return [
        {
            "section": "§4 movement table",
            "sentence": "Unchanged | `prev = cur` | 0 (changes inside the month netted to zero, e.g., a \"neutral\" "
                        "plan change)",
            "readings": ["Literal: every customer-month with prev = cur, including prev = cur = 0 between "
                         "subscriptions, is 'Unchanged' (this implementation).",
                         "Meant for paying customers; non-paying months get their own state (SQL: "
                         "'not_paying')."],
            "also": "The document never says which customer-months form the movement panel (from the first paid "
                    "month-end, or every month since GA); that choice changes the count of 'unchanged' rows.",
            "impact_here": f"Labels of {not_paying:,} non-paying customer-months; no amounts.",
        },
        {
            "section": "§4.1 component formulas",
            "sentence": "(The plan step uses the list `unit_price` of the new plan on the old billing period.)",
            "readings": ["Only the plan step uses a list price; the billing-period step uses the row's "
                         "to_unit_price, since MRR(to_plan, to_seats, to_billing, from_discount) shares plan and "
                         "billing period with the to terms (this implementation).",
                         "Every intermediate state is priced at list for its (plan, billing period), so an "
                         "off-list to_unit_price would land in the discount step instead.",
                         "When the plan is unchanged the plan step still re-prices 'the new plan' at list, which "
                         "turns an off-list from_unit_price into a plan component (this implementation keeps the "
                         "old unit price instead)."],
            "also": "Enterprise has no monthly list price; the sentence needs it for monthly → Enterprise moves. "
                    "Annual list ÷ 12 ($50) is used here, as §3's billing-neutral MRR does, but §4.1 does not say so.",
            "impact_here": f"None: {checks['change_terms_off_list_price']} change-row terms are off list, so all "
                           "readings give identical components.",
        },
        {
            "section": "§4.1 customer-month components",
            "sentence": "sum each component over all rows with `effective_date` in [first day of *m*, last day of "
                        "*m*], on any of the customer's subscriptions",
            "readings": ["Valid rows only (§3 drops rows outside [start_date, end_date)) (this implementation).",
                         "Literally all plan_changes rows, including the dropped ones."],
            "impact_here": f"None: the {len(checks['plan_change_rows']['dropped'])} dropped rows all fall in "
                           "churn, reactivation or non-paying months, which have no components.",
        },
        {
            "section": "§1 rounding",
            "sentence": "Subscription MRR is rounded to the cent (`ROUND(x, 2)`) before summing and before any "
                        "comparison.",
            "readings": ["Half away from zero (SQL ROUND, and the half-up integer rounding used here).",
                         "Half to even (Python round(), numpy.round), which differs on exact half-cent values."],
            "impact_here": f"None: {checks['subscription_versions_with_fractional_cent_mrr']} subscription "
                           "versions have an unrounded MRR off a whole cent (integer list prices, whole-percent "
                           "discounts, annual prices divisible by 12).",
        },
        {
            "section": "§2 exclusions",
            "sentence": "`company_name` matches, case-insensitively, one of the whole-token patterns `\\btest\\b`, "
                        "`\\[internal\\]`, `^internal\\b`, `\\bqa\\b`, `zz_test`",
            "readings": ["Apply the regexes as written: `zz_test` and `\\[internal\\]` are substring patterns "
                         "(this implementation).",
                         "'Whole-token' applies to every pattern, so `zz_test` needs boundaries (would not match "
                         "'zz_test_enterprise' if `_` counts as a separator)."],
            "impact_here": "None: exactly the 6 expected internal accounts are excluded either way; real names "
                           "such as 'Granitestack' contain 'test' and are kept.",
        },
        {
            "section": "§10 right-censoring",
            "sentence": "a cell is shown only if every member of the cohort has reached month *k* by the as-of "
                        "date. Partial cells are blank, not zero.",
            "readings": ["The cell has no value (this implementation).",
                         "The cell is stored with a value and an is_complete flag, and blanked only on display "
                         "(SQL)."],
            "impact_here": "24 partial quarterly cells; see discrepancy cohort_partial_cells_published.",
        },
        {
            "section": "§7 quarterly run-rate NRR",
            "sentence": "For a quarter-end *t*: `NRR_q(t) = Σ_{c paying at t−3} MRR_c(t) ÷ Σ MRR_c(t−3)`, "
                        "annualised as `NRR_q⁴`.",
            "readings": ["Only NRR has a quarterly definition. The warehouse also publishes grr_quarter and "
                         "grr_runrate_annualized, and rpt_nrr_grr_ttm publishes expansion / contraction / churn "
                         "contributions, none of which the document defines.",
                         "A capped quarterly GRR raised to the 4th power is not a 12-month GRR, so it should not "
                         "be read next to §8 GRR."],
            "impact_here": "Informational columns only; the quarterly GRR matches the §8-style analogue computed "
                           "here.",
        },
    ]


# ---------------------------------------------------------------------------
# 10. Entry points
# ---------------------------------------------------------------------------


def headline(metrics: Metrics) -> dict[str, Any]:
    """A few as-of numbers, so a reader of the JSON can check the scale."""
    last, ttm, rr = metrics.waterfall.iloc[-1], metrics.ttm.iloc[-1], metrics.runrate.iloc[-1]
    return {
        "as_of": last["month_end"],
        "mrr": round(float(last["mrr_end"]), 2),
        "arr": round(float(last["arr_end"]), 2),
        "paying_customers": int(last["customers_end"]),
        "ttm_nrr": float(ttm["nrr"]),
        "ttm_grr": float(ttm["grr"]),
        "ttm_logo_retention": float(ttm["logo_retention"]),
        "runrate_quarter_end": rr["quarter_end"],
        "runrate_nrr_annualized": float(rr["nrr_runrate_annualized"]),
    }


def _clean(obj: Any) -> Any:
    """Recursively make a results structure JSON-safe."""
    if isinstance(obj, dict):
        return {str(k): _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    return _plain(obj)


def run(settings: Settings | None = None) -> dict[str, Any]:
    """Compute every metric, compare it with the warehouse and return the results."""
    settings = settings or get_settings()
    started = time.perf_counter()
    metrics = compute_metrics(settings)
    seconds_compute = time.perf_counter() - started
    warehouse = load_warehouse(settings.warehouse_path)
    comparisons = run_comparisons(metrics, warehouse)
    core = [c for c in comparisons if not c["informational"]]
    results = {
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "definitions": f"{DOC} v{DOC_VERSION}",
            "as_of_date": settings.as_of_date.isoformat(),
            "raw_dir": str(settings.raw_dir),
            "warehouse": str(settings.warehouse_path),
            "tolerances": {"money_usd": MONEY_TOL, "ratio_abs": RATIO_TOL, "count": 0, "label": "exact"},
            "seconds_compute": round(seconds_compute, 2),
            "seconds_total": round(time.perf_counter() - started, 2),
        },
        "summary": {
            "checks": len(comparisons),
            "core_checks_matching": sum(c["status"] == "match" for c in core),
            "core_checks": len(core),
            "mismatching": [c["name"] for c in comparisons if c["status"] != "match"],
        },
        "headline": headline(metrics),
        "data_checks": metrics.data_checks,
        "comparisons": comparisons,
        "discrepancies": find_discrepancies(metrics, warehouse, comparisons),
        "ambiguities": ambiguities(metrics),
    }
    return _clean(results)


def format_table(results: dict[str, Any]) -> str:
    """Short text table of the comparisons."""
    def fmt(value: float | None) -> str:
        return "-" if value is None else f"{value:.1e}"

    header = (f"{'check':<27}{'rows':>7}{'only py':>9}{'only sql':>10}{'max |Δ$|':>10}{'max |Δratio|':>14}"
              f"{'> tol':>7}{'mismatch':>10}  status")
    lines = [header, "-" * (len(header) + 8)]
    for c in results["comparisons"]:
        status = c["status"] + (" (info)" if c["informational"] else "")
        lines.append(f"{c['name']:<27}{c['rows_compared']:>7}{c['rows_only_python']:>9}{c['rows_only_sql']:>10}"
                     f"{fmt(c['max_abs_diff_money']):>10}{fmt(c['max_abs_diff_ratio']):>14}"
                     f"{c['n_over_tol_rows']:>7}{c['n_mismatch_rows']:>10}  {status}")
    lines.append("> tol: rows off by more than $0.01 (money) or 1e-9 (ratios). mismatch: rows that differ by a "
                 "cent or more, beyond 1e-9, or at all (counts, labels).")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Independent Python cross-check of the SQL revenue metrics.")
    parser.add_argument("--json", type=Path, default=get_settings().reports_dir / "crosscheck_results.json",
                        help="where to write the results (default: reports/crosscheck_results.json)")
    args = parser.parse_args(argv)

    results = run()
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n")

    h, s = results["headline"], results["summary"]
    print(f"As of {h['as_of']}: MRR ${h['mrr']:,.2f} | {h['paying_customers']:,} paying | "
          f"TTM NRR {h['ttm_nrr']:.1%} | GRR {h['ttm_grr']:.1%}   ({results['meta']['seconds_total']} s)\n")
    print(format_table(results))
    print(f"\n{s['core_checks_matching']}/{s['core_checks']} core checks match. Discrepancies: "
          + (", ".join(d["id"] for d in results["discrepancies"]) or "none"))
    print(f"Results written to {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
