# 02 — Metric definitions (single source of truth)

**Owner:** Revenue Analytics (RevOps) · **Reviewed with:** Finance (board pack), Customer Success Ops · **Version:** 1.1 (2026-11-03, change log at the end)
Every number in the board pack, the monthly revenue review, the dashboard and the CS lists comes from these definitions. The SQL implementation is in `sql/` (one file per model, §16). An independent Python implementation (`src/saas_rev/crosscheck.py`, written from this document alone) must match MRR, movements, NRR and GRR **to the cent per customer-month**; results are in `03_data_quality_checks.md`.

---

## 1. Conventions

| Item | Rule |
|---|---|
| Currency | USD. No FX in this data. |
| Calendar | Calendar months and quarters; fiscal year = calendar year. Dates are UTC. |
| Snapshot time | **Month-end snapshot** = the state at the end of the last day of the month, after every change effective on or before that day. |
| As-of date | 2026-09-30 (last closed month). Nothing after it is used. |
| Units | `discount_pct` is a fraction in [0, 1] (0.15 = 15%) in every table. `unit_price` is per seat **per billing period** (per month for monthly, per year for annual). |
| Rounding | Subscription MRR is rounded to the cent (`ROUND(x, 2)`) before summing and before any comparison. Shown as $K / $M with one decimal; rates with one decimal. |
| Level | Revenue metrics are computed **per customer (account)**, then summed. |

## 2. Population and exclusions

- **Customer** = an account in `customers`. **Paying customer at month-end D** = customer MRR on D > 0. Customer counts in this document always mean paying customers.
- **Internal / test accounts are excluded everywhere:** `email_domain = 'ourcompany.example'` OR `company_name` matches, case-insensitively, one of the patterns `\btest\b` and `\bqa\b` (whole words), `^internal\b` (leading word), `\[internal\]` or `zz_test` (literal tags). `sql/91_dq_checks.sql` asserts the excluded set is exactly the 6 known internal accounts and lists them.
- **Duplicate CRM records** (a second `customer_id` with the same `email_domain` and no subscription) never reach revenue metrics, because only accounts with a subscription can be paying. They are counted in data-quality checks.
- **Invoices:** `status = 'void'` lines are excluded. `uncollectible` lines stay in billed revenue (they were real charges) and are reported separately.
- **Usage:** exact duplicate rows are removed; the partial week containing the as-of date is excluded from weekly trends.

## 3. MRR (Monthly Recurring Revenue)

**Subscription terms on day D.** Start from the subscription's `initial_*` terms and apply its `plan_changes` rows with `effective_date ≤ D`, ordered by `effective_date`, then `requested_date`, then `change_id`. The terms on D are the `to_*` values of the last applied row.
**Chaining invariant** (checked in `sql/91_dq_checks.sql`, must have 0 violations): in that order, the first row's `from_*` equals `initial_*` and each row's `from_*` equals the previous row's `to_*`.
**Valid rows:** `start_date ≤ effective_date < end_date` (or `end_date` null). Rows outside that range (e.g., a change keyed in after a cancellation) are dropped in `stg_plan_changes` and counted.

**Live on day D.** `start_date ≤ D` and (`end_date` is null or `end_date > D`). `end_date` is exclusive (the first day without service).

**Subscription MRR on D** = `ROUND(seats × monthly-equivalent unit price × (1 − discount_pct), 2)`, where the monthly-equivalent unit price is `unit_price` for monthly billing and **`unit_price ÷ 12` for annual billing** (annual prepaid contracts are spread evenly over 12 months; they are **not** counted in the month they are invoiced).

**Customer MRR at month-end** = sum of subscription MRR over the customer's subscriptions live on the last day of the month. **Total MRR** = sum over customers.

**Billing-neutral MRR (`mrr_norm`).** `seats × list price per seat-month of the plan on monthly billing × (1 − discount_pct)`, with Basic $15, Premium $35, Enterprise $50 (annual list ÷ 12, since Enterprise is annual-only). It removes the 20% annual-billing discount so that a monthly → annual switch does not look like a 20% cut. Used only where a *drop in what the customer buys* is the question (silent contraction §9, early-warning outcome §9.1). Never reported as revenue.

| Included in MRR | Excluded from MRR |
|---|---|
| Per-seat subscription fees for the plan | One-time fees (onboarding, implementation) |
| Recurring negotiated discounts (MRR is net of them) | Proration lines (billing timing of a change already reflected in MRR) |
| Customers in dunning (card retry in progress) until the subscription is canceled | Tax / VAT |
| | Void invoices, refunds and credits |
| | Internal / test accounts; subscriptions with 100% discount (MRR = 0, not "paying") |
| | Seat overage above purchased seats (tolerated, never billed) |

**ARR** = MRR × 12 (run-rate of live subscriptions at the snapshot; not GAAP revenue, not contracted-but-not-started revenue).
**ARPA** = MRR ÷ paying customers.

## 4. MRR movements (customer level, month over month)

For each customer and month *m*, compare MRR at the end of the previous month (`prev`) with MRR at the end of month *m* (`cur`):

| Movement | Condition | Amount |
|---|---|---|
| **New** | `prev = 0`, `cur > 0`, and the customer has **never** had MRR > 0 at an earlier month-end | `cur` |
| **Reactivation** | `prev = 0`, `cur > 0`, and the customer **had** MRR > 0 at some earlier month-end | `cur` |
| **Expansion** | `prev > 0`, `cur > prev` | `cur − prev` |
| **Contraction** | `prev > 0`, `0 < cur < prev` | `prev − cur` |
| **Churn** | `prev > 0`, `cur = 0` | `prev` |
| Unchanged | `prev = cur > 0` | 0 (changes inside the month netted to zero, e.g., a "neutral" plan change) |
| *Not paying* (state, not a movement) | `prev = cur = 0` | 0 — a former customer inside the panel between a churn and any win-back; labelled `not_paying` in `fct_mrr_movements` |

**Identity (checked every month, to the cent):** `MRR_end = MRR_start + New + Expansion + Reactivation − Contraction − Churn`.

**New vs Reactivation** is decided from month-end MRR history only, never from the number of subscriptions. A win-back whose first subscription never spanned a month-end is therefore New.

**Known blind spots of month-end snapshots (accepted, counted in `03_data_quality_checks.md`):**
- A customer who starts and cancels inside the same calendar month never appears ("subscribed and churned").
- Several changes inside one month are netted (e.g., +10 seats then −4 seats = Expansion of 6 seats).
- A cancellation and win-back inside the same month show only the net difference.

### 4.1 Expansion and contraction by component

Each `plan_changes` row is split into four signed components by changing one attribute at a time, in the fixed order **plan → seats → billing period → discount**:

| Component | Formula (MRR() = §3 formula) |
|---|---|
| `plan` | MRR(to_plan, from_seats, from_billing, from_discount) − MRR(from terms) |
| `seats` | MRR(to_plan, to_seats, from_billing, from_discount) − MRR(to_plan, from_seats, from_billing, from_discount) |
| `billing_period` | MRR(to_plan, to_seats, to_billing, from_discount) − MRR(to_plan, to_seats, from_billing, from_discount) |
| `discount` | MRR(to terms) − MRR(to_plan, to_seats, to_billing, from_discount) |

The plan step prices the new plan at its list `unit_price` on the **old** billing period (Enterprise has no monthly price, so its monthly-equivalent list price, annual ÷ 12 = $50, is used); if the plan did not change, the old unit price is kept. Every intermediate MRR is rounded to the cent, so the four parts sum exactly to the row's ΔMRR. For a **continuing customer** (`prev > 0` and `cur > 0`) in month *m*, sum each component over all rows with `effective_date` in [first day of *m*, last day of *m*], on any of the customer's subscriptions; `other = (cur − prev) − Σ components` (mid-month win-backs, rounding).

**Reporting.** *Gross expansion by component* = sum of positive customer-month component values (seats added, plan upgrades, discount reductions, annual → monthly switches); *gross contraction by component* = sum of negative ones. The waterfall's net movements remain the movements of record, and the component view ties to them exactly: `Σ positive components − Σ |negative components| + Σ other = Expansion − Contraction` over continuing customers.

**Billing switches are their own component in both directions.** Monthly → annual lowers MRR (annual is 20% cheaper per seat) — a deliberate retention trade, never part of an "unhappy customer" narrative. Annual → monthly raises MRR but is a *risk* signal (the customer stopped committing for a year); it is shown separately and excluded from "expansion" talking points.

## 5. Churn, retention-flow and movement rates

| Metric | Formula (month *m*) |
|---|---|
| Logo churn rate | customers with a Churn movement in *m* ÷ paying customers at end of *m − 1* |
| Gross MRR churn rate | (Churn + Contraction) ÷ MRR at end of *m − 1* |
| Net MRR churn rate | (Churn + Contraction − Expansion) ÷ MRR at end of *m − 1* (reactivation excluded) |
| Monthly net retention (flow) | (MRR_{m−1} + Expansion − Contraction − Churn) ÷ MRR_{m−1} |
| Monthly gross retention (flow) | (MRR_{m−1} − Contraction − Churn) ÷ MRR_{m−1} |
| Voluntary / involuntary split | by `subscriptions.cancel_reason` of the subscription that ended |

Monthly flow retention is a **diagnostic only, never annualised or compared with NRR §7**: it nets month by month within customers, includes movements of customers who became New after *t − 12*, and leaves out win-backs.

**Quarterly movement rates** (used for "expansion slowed last quarter"): expansion rate = the quarter's three monthly Expansion amounts ÷ MRR at the end of the month before the quarter. Contraction, churn, new and reactivation rates use the same denominator. Numerators include movements of customers who became New or Reactivated earlier in the same quarter (consistent with the waterfall). Year-over-year comparisons use the same quarter of the prior year to remove seasonality.

## 6. Upgrades and downgrades

An **upgrade** is a `plan_changes` row whose `to_plan` tier is higher than `from_plan`; a **downgrade** is lower. A **voluntary upgrade** has `change_source ≠ 'system'`; **forced (seat-cap) upgrades** (`change_source = 'system'`: a Basic account asked for more than 50 seats) are reported separately.
**Monthly upgrade rate (Basic → higher)** = customers on Basic at the end of *m − 1* with a voluntary upgrade effective in *m* ÷ customers on Basic at the end of *m − 1*. Customers who start on Basic inside month *m* are in neither numerator nor denominator.

## 7. NRR — Net Revenue Retention (headline, trailing 12 months)

For month-end *t*:
- **Base** `B(t)` = customers with MRR > 0 at month-end *t − 12*.
- **NRR(t)** = `Σ_{c ∈ B} MRR_c(t)  ÷  Σ_{c ∈ B} MRR_c(t − 12)`.

| Question | Answer |
|---|---|
| Numerator | Today's MRR of the same customers: includes their expansion, contraction and churn (churned = 0). |
| Denominator | Their MRR 12 months ago. |
| New customers since *t − 12*? | Excluded (not in the base). |
| Base customer churned and came back within the year? | Included at today's MRR (same customer). |
| Customer who churned before *t − 12* and came back? | Excluded (not in the base). |
| Point-to-point or compounded? | **Point-to-point.** A customer who contracts in March and recovers in June shows no loss. The month-by-month flow view is §5. |
| Board label | "12-month NRR at 30 Sep 2026 (base: customers paying at 30 Sep 2025)". |
| History | Undefined before 2024-10-31 (no base before GA). Points whose base has fewer than 1,000 customers (before 2025-09) are shown greyed: small bases with many early adopters. |
| Segment NRR | Partition `B(t)` by the customer's segment / industry / channel / region (static attributes) or by plan / billing period **at t − 12** (state at the start, to avoid survivor bias). |

**Quarterly run-rate NRR (leading diagnostic).** For a quarter-end *t*: `NRR_q(t) = Σ_{c paying at t−3} MRR_c(t) ÷ Σ MRR_c(t−3)`, annualised as `NRR_q⁴`; the quarterly GRR uses the §8 cap per customer and is annualised the same way. It reacts within one quarter, while TTM NRR takes up to four quarters to show a change; it is noisier and seasonal, so it is always shown next to the same quarter of the prior year.

## 8. GRR — Gross Revenue Retention (trailing 12 months)

`GRR(t) = Σ_{c ∈ B} min(MRR_c(t), MRR_c(t − 12)) ÷ Σ_{c ∈ B} MRR_c(t − 12)`.
Each customer is capped at its own starting MRR, so GRR excludes expansion and cannot exceed 100%. **Logo retention (TTM)** = share of `B(t)` still paying at *t*.
**NRR decomposition** (published with NRR): `NRR = 1 + expansion contribution + contraction contribution + churn contribution`, where expansion = Σ max(MRR_t − MRR_{t−12}, 0), contraction = −Σ (MRR_{t−12} − MRR_t) over customers still paying with lower MRR, churn = −Σ MRR_{t−12} over customers at 0 — each ÷ base MRR.

## 9. Silent contraction ("quiet downgrades")

**Material contraction without cancellation, last 6 months.** An account is flagged at month-end *t* if all hold:
1. it is paying at *t* and has no cancellation requested on or before *t* (`cancel_requested_date` null or > *t*);
2. `mrr_norm_t ≤ 0.80 × max(mrr_norm at month-ends t−6 … t−1)` — billing-neutral spend is down at least 20% from its recent peak (ties flag);
3. at least one Contraction movement in months *t−5 … t* whose components (§4.1) include a negative `plan`, `seats` or `discount` part (a monthly → annual switch alone never qualifies).

**Classes.** `silent` — the contraction came from the customer (self-serve seat removals, renewal right-sizing, plan downgrades). `saved — re-risk` — the contraction was a CS save offer (`change_source = 'customer_success'`): the account asked to cancel and was kept on a smaller plan; it is reported separately, not as "silent".
**Output fields:** current ARR (`12 × MRR_t`, the revenue still at risk), lost ARR (`12 × (peak MRR − MRR_t)`), decline % (billing-neutral), contraction steps (months with Contraction in *t−5 … t*), months since last contraction, contraction source, plan / seats now vs peak.
**Parameters:** 20% threshold and 6-month window are the defaults; the dashboard lets you change them (sensitivity, not a different definition).

### 9.1 Early-warning rule (leading indicators)

| Item | Definition |
|---|---|
| Eligible (account, *t*) | Paying at *t*; no cancellation requested on or before *t*; no Contraction movement in *t−2 … t* (so the flag is early); outcome window fully observed (*t* ≤ 2026-06-30 for monthly-billed; renewal month-end ≤ 2026-09-30 for annual-billed); 13 complete weeks of usage before *t* (the rule needs the full window). |
| Outcome window | Monthly-billed at *t*: month-ends *t+1 … t+3*. Annual-billed at *t*: month-ends *t+1* … the first renewal month-end after *t*, and only if that renewal is within *t+6* (otherwise the row is not eligible — annual customers cannot shrink before renewal). |
| Outcome = 1 | `min(mrr_norm over the window) ≤ 0.90 × mrr_norm_t` (churn, MRR = 0, qualifies). |
| Flag (v1.2) | Seat utilization over the 13 complete weeks ending at *t* **< 50%** (no WAU condition). |
| How the threshold was chosen | Grid of utilization thresholds 40–80% (step 5 pts) × WAU-trend conditions (none, or last 4 weeks vs the 9 before ≤ 0%, −5% … −40%), scored on every eligible (account, *t*). Criterion, fixed before looking at the grid: **highest recall among rules with precision ≥ 2× the base rate and on average ≤ 120 distinct CSM-covered accounts flagged per quarter** (CS capacity, assumption A9; quarters Q3 2025 – Q2 2026); precision breaks ties. `notebooks/04_silent_churn.ipynb`. |
| Back-test result | Base rate 17.0% of eligible account-months; flagged precision 39.5% (lift 2.3), recall 30%; 36% of accounts that later contracted or churned were flagged before it; median lead 4 month-ends (monthly-billed 3, annual-billed 6). The v1.0 draft (utilization < 60% AND WAU ≤ −15%) scored precision 32% (lift 1.9), recall 15%, median lead 2; adding a WAU condition never raised recall at this capacity. Most outcomes are **not** caught — the rule is a prioritisation aid for the CSM queue, not a churn predictor. |
| Metrics | Precision, recall and lift (precision ÷ base rate) at the (account, *t*) grain; account-level recall (share of accounts with an outcome flagged at least once before it) also reported. |
| Lead time | Month-ends from the first flag of the uninterrupted flag run that ends at the outcome month, to the outcome month; reported as a distribution. |
| Watchlist | The rule scored at *t* = 2026-09-30 (no outcome yet), plus the §9 flags. |

## 10. Cohorts

- **Cohort** = month (or quarter) of the customer's **first month-end with MRR > 0**.
- **Month index k** = months since the customer's own first paid month-end (k = 0 is that month-end).
- **Cohort NRR(k)** = `Σ MRR_c(first_c + k) ÷ Σ MRR_c(first_c)`; **cohort GRR(k)** caps each customer at `MRR_c(first_c)`; **logo retention(k)** = share still paying. Win-backs count in their original cohort.
- **Right-censoring:** a cell is shown only if every member of the cohort has reached month *k* by the as-of date. Partial cells are blank, not zero.

## 11. Plan transitions
**Plan at month-end** = plan of the live subscription (none if not paying). **Transition matrix (horizon h):** for customers paying at month-end *s*, the share in each state {Basic, Premium, Enterprise, Churned} at *s + h*, pooled over all *s* with *s + h ≤* as-of. **Sankey:** plan at first paid month-end → plan 12 months later (→ 24 months later where observed).

## 12. Usage and seat metrics
- **WAU** (weekly active users) = `unique_users` of `session` events for the account-week, after de-duplication.
- **Seats in a week** = seats on the subscription terms at the end of that week (Sunday).
- **Seat utilization** over a window = Σ WAU ÷ Σ seats over the complete weeks in the window (can exceed 100% — tolerated overage).
- **WAU trend** = average WAU in the last 4 complete weeks ÷ average WAU in the 9 complete weeks before them − 1.
- **Feature intensity** = events per WAU per week (e.g., premium-feature gate hits per active user).
- Windows use complete weeks only; a "90-day window" = the 13 complete weeks ending on or before the index date.

## 13. Upgrade-signal analysis (leakage and bias rules)
- **Population:** month-ends *t* at which the customer is paying on Basic with ≥ 3 months since its first paid month-end.
- **Label:** a **voluntary** upgrade (Basic → Premium / Enterprise, `change_source ≠ 'system'`) effective in `(t, t + 90 days]`. Forced seat-cap upgrades are excluded from the signal table and reported separately. Churn within the window (MRR = 0 at any of *t+1 … t+3*) is a separate competing outcome.
- **Features:** only data observable at *t* — usage in the 13 complete weeks ending at *t*, seats and terms at *t* and *t − 3 months*, static attributes, `prior_higher_tier` (ever on Premium / Enterprise before *t*). Nothing after *t*.
- **Comparison table (upgraders vs stayers):** upgraders — the last month-end before the upgrade (one row per upgrade); stayers — one eligible month-end per account that did not upgrade or churn in the window, drawn from the same calendar-month × tenure-band stratum as the upgraders (tenure bands 3–6, 7–12, 13–24, 25+ months), 1 : 1, accounts disjoint from the upgraders (an account that upgrades later is never a stayer).
- **Tenure and win-backs:** tenure counts from the first paid month-end and does not reset on a win-back, so a won-back account can be eligible with fewer than 3 months on its new subscription; its `seat_growth_3m` is undefined (no seats 3 months earlier) and is treated as 0.
- **Signals that depend on packaging:** Premium paywall hits fell sharply after 2026-04-15 (features moved into Basic), so the model uses them as within-month percentiles of the Basic base rather than raw levels, and the opportunity list states thresholds relative to the current month.
- **Ranking model:** all eligible rows, weighted 1 ÷ (rows per account); time-based split — train on *t* ≤ 2025-12-31, test on 2026-01-31 … 2026-06-30, test metrics reported before and after the 2026-04-15 packaging change. Rows with *t* + 90 days > 2026-09-30 are never used for training or evaluation; they are only scored for the opportunity list at *t* = 2026-09-30.

## 14. Reconciliation to billing
- **Daily-MRR revenue** for day D = Σ subscription MRR on D (§3 applied to D) × 12 ÷ days in D's calendar year (365 or 366).
- **Billed recurring revenue** = non-void `recurring` + `proration` lines, spread evenly by day over `[service_period_start, service_period_end)` clipped to the subscription's live days `[start_date, end_date)`. The clipped remainder (mostly `uncollectible` charges after a failed card renewal, where service stopped 14 days into the period) is reported separately as *billed but not serviced*.
- **Windows:** complete calendar years 2024 and 2025, and the trailing 12 months Oct 2025 – Sep 2026. **Expected |gap| < 0.3%**; the residual is day-count convention at the window edges. Monthly values are a trend, not pass/fail: a monthly-billed price is spread over its own 28–31-day period while daily MRR uses a constant 12 ÷ 365 rate, so February runs about +1% and 31-day months slightly negative (≈ −0.5%) at total level; annual contracts reconcile exactly by construction.

## 15. CS account health list (`docs/account_health_list.csv`)

One row per account: the risk watchlist (§9, §9.1) and the top 300 upgrade opportunities (§13); an account on both is listed as risk.

| Field | Definition |
|---|---|
| `queue` | Who works the account this quarter: `csm` (P1 — the first 120 CSM-covered risk accounts by priority; CS capacity, assumption A9), `renewal_desk` (P3 — Technology annual renewals within 6 months on the risk list, not in P1), `digital` (P2 — the rest of the risk list), `sales_assist` (P4 — top 100 upgrade opportunities), `in_app` (the next 200 opportunities; in-app prompt only) |
| `priority_rank` | Rank within the queue: annual renewals within 120 days first, then `arr_at_stake` |
| `play` | The recommendation the account belongs to (`notebooks/05_recommendations.ipynb`); each account is in one play only |
| `list_type` | `risk` (silent contraction §9, CS saves, or early-warning flag §9.1) or `expansion` (upgrade propensity §13) |
| `customer_id`, `company_name`, `segment`, `industry`, `cs_coverage`, `csm_owner` | From `stg_customers` |
| `plan`, `billing_period`, `seats`, `current_arr` | Terms and ARR at 2026-09-30 |
| `next_renewal_date`, `days_to_renewal`, `renewal_within_120d` | Annual: end of the current annual term. Monthly: next billing date (monthly accounts never count as "renewal within 120 days" — every month is a billing date) |
| `arr_at_stake` | Risk: current ARR × the historical hit rate of the flag that fired (silent / saved: share of newly flagged accounts that churned or contracted again within 6 months; early warning: back-test precision for the account's billing period); an account with both flags takes the higher rate. Expansion: expected upgrade ARR = 12 × (Premium − Basic price per seat-month on the account's billing period) × seats × upgrade propensity |
| `reason_code_1..3` | Risk: `SAVED_BY_CS_6M`, `DOWNGRADED_6M`, `SEATS_CUT_20PCT_6M`, `RENEWAL_RIGHTSIZED_6M`, `SPEND_DOWN_20PCT_6M`, `UTIL_BELOW_50`, `WAU_DOWN_15PCT`, `RENEWAL_WITHIN_120D` (in that priority order). Expansion: the three behaviours that contribute most to the account's upgrade score (§13), e.g. `GATE_USERS_TOP_DECILE`, `AUTOMATION_ABOVE_MEDIAN`, `TEAM_GROWTH_10PCT_3M` |
| `evidence` | The numbers behind the reasons, in plain words |
| `suggested_play` | What to do with the account (from the play); prefixed "Urgent" when an annual renewal is less than 45 days away |

## 16. Where each metric is implemented

| Metric | SQL file / Python module | Output table |
|---|---|---|
| Raw load, as-of date, MRR macros | `sql/00_load_raw.sql` | `raw.*`, `meta_run`, `mrr_of()`, `mrr_norm_of()` |
| Clean sources & exclusions | `sql/01`–`05_stg_*.sql` | `stg_*` |
| Calendars | `sql/06_dim_calendar.sql` | `dim_month`, `dim_week` |
| Subscription terms over time | `sql/10_int_subscription_versions.sql` | `int_subscription_versions` |
| Customer MRR at month-end | `sql/20_fct_customer_mrr_monthly.sql` | `fct_customer_mrr_monthly` |
| MRR movements | `sql/21_fct_mrr_movements.sql` | `fct_mrr_movements` |
| Movement components | `sql/22_fct_mrr_movement_components.sql` | `fct_change_components`, `fct_mrr_movement_components` |
| Monthly waterfall & rates | `sql/23_rpt_mrr_waterfall.sql` | `rpt_mrr_waterfall_monthly` |
| Quarterly movement rates, upgrade rates | `sql/24_rpt_mrr_quarterly.sql` | `rpt_mrr_quarterly` |
| NRR / GRR / logo retention (TTM), run-rate NRR | `sql/25_rpt_nrr_grr_ttm.sql` | `rpt_nrr_grr_ttm`, `rpt_nrr_quarterly_runrate` |
| Segment NRR / GRR | `sql/26_rpt_segment_retention.sql` | `rpt_segment_retention_ttm` |
| Cohort retention | `sql/27_rpt_cohort_retention.sql` | `rpt_cohort_retention` |
| Plan transitions | `sql/30_rpt_plan_transitions.sql` | `rpt_plan_transition_matrix`, `rpt_plan_change_flows` |
| Weekly usage & utilization | `sql/40_fct_account_usage_weekly.sql` | `fct_account_usage_weekly` |
| Account-month features & labels | `sql/41_fct_account_month_features.sql` | `fct_account_month_features` |
| Silent contraction | `sql/50_rpt_silent_contraction.sql` | `rpt_silent_contraction` |
| Billing reconciliation | `sql/90_dq_revenue_reconciliation.sql` | `dq_revenue_reconciliation` |
| Data-quality checks | `sql/91_dq_checks.sql` | `dq_check_results` |
| Expansion bridges, one-off detection, interactive waterfall / retention | `src/saas_rev/analysis/metrics.py` | `metrics__*` marts, `reports/key_numbers/metrics.json` |
| Upgrade-rate decomposition (mix vs rate), downgrades, time to upgrade | `src/saas_rev/analysis/transitions.py` | `transitions__*`, `transitions.json` |
| Upgrade signals, ranking model, opportunity list | `src/saas_rev/analysis/expansion.py` | `expansion__*`, `expansion.json` |
| Silent contraction (parameterised), early-warning back-test, risk watchlist | `src/saas_rev/analysis/silent_churn.py` | `silent_churn__*`, `silent_churn.json` |
| Play sizing (§15 lists, loss rates) | `src/saas_rev/analysis/recommendations.py` | `recommendations__plays`, `recommendations.json` |
| CS account health list | `src/saas_rev/docs_mbr.py` | `docs/account_health_list.csv` |
| Independent re-implementation (MRR → cohorts) | `src/saas_rev/crosscheck.py` | `reports/crosscheck_results.json` |

## 17. Questions people ask (and our answers)

- **"Why not just sum invoices for MRR?"** Annual contracts are invoiced once a year, so invoice totals spike in renewal months. MRR spreads them evenly. §14 shows billing and MRR agree.
- **"Why month-end and not average MRR?"** Movements need two clean snapshots to classify a customer. Daily MRR is used only for the billing reconciliation.
- **"A customer moved from monthly to annual and MRR fell 20% — is that churn risk?"** No. It is contraction in the waterfall (run-rate fell) but a good outcome; it is its own component and is neutralised by `mrr_norm` in the silent-contraction and early-warning logic.
- **"Does reactivation count in NRR?"** Only for customers who were paying 12 months ago (they are in the base). Win-backs of customers who left earlier are Reactivation in the waterfall, not in NRR.
- **"Is a downgrade-to-save a win?"** It avoids churn but is still Contraction. Saved accounts are tracked as `saved — re-risk`.
- **"Do failed payments count as churn immediately?"** No — only when the subscription is canceled after the 14-day dunning window.
- **"Segment changed during the year?"** Segments are static in this data. Plan and billing period are taken at the start of the measurement window.
- **"Why is GRR capped per customer instead of (start − churn − contraction) ÷ start?"** The capped point-to-point version is what investors compare across companies: one customer's expansion cannot offset another's loss. It does ignore a contraction fully recovered by *t* (NRR makes the same choice); the monthly flow rates in §5 capture those.
- **"Why is TTM NRR still above 105% if expansion slowed?"** TTM NRR averages four quarters; one weak quarter moves it by roughly a quarter of the change. The run-rate NRR in §7 shows the Q3 change directly.

## 18. Change log

| Version | Date | Change |
|---|---|---|
| 1.0 | 2026-11-03 | First version. |
| 1.1 | 2026-11-03 | After independent review: billing-neutral MRR for silent contraction and early-warning outcomes (annual list is exactly 80% of monthly, so v1.0 flagged every monthly → annual switch); sequential per-attribute component decomposition (v1.0 proportional allocation could divide by zero and hide opposite-sign changes); renewal-aware outcome windows for the early-warning back-test; voluntary vs forced upgrades and calendar-matched comparison for upgrade signals; term-chaining invariant and tie-break; cent rounding; whole-token internal-account rule; monthly flow retention and quarterly run-rate NRR defined; corrected GRR rationale and reconciliation explanation; CS list schema. |
| 1.2 | 2026-11-03 | Early-warning rule set to the back-tested operating point (utilization < 50%, no WAU condition) with the selection criterion and results in §9.1; `not_paying` state and partial cohort cells defined after the independent cross-check; CS list (§15) rebuilt around queues and plays, reason codes and `arr_at_stake` aligned with the watchlist and opportunity builds; §13 stayer sampling, win-back tenure and percentile signals written out. |
