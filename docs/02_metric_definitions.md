# 02 — Metric definitions (single source of truth)

**Owner:** Revenue Analytics (RevOps) · **Reviewed with:** Finance (board pack), Customer Success Ops · **Version:** 1.0 (2026-11-03)
Every number in the board pack, the monthly revenue review, the dashboard and the CS lists is computed from these definitions. The SQL implementation is in `sql/` (file per metric, listed in §15); an independent Python implementation (`src/saas_rev/crosscheck.py`) must reproduce MRR, movements, NRR and GRR exactly (results in `03_data_quality_checks.md`).

---

## 1. Conventions

| Item | Rule |
|---|---|
| Currency | USD. No FX in this data. |
| Calendar | Calendar months and quarters; fiscal year = calendar year. Dates are UTC. |
| Snapshot time | **Month-end snapshot** = the state at the end of the last day of the month, after every change effective on or before that day. |
| As-of date | 2026-09-30 (last closed month). Nothing after it is used. |
| Rounding | Stored unrounded; shown as $K / $M with one decimal, rates with one decimal. |
| Level | All revenue metrics are computed **per customer (account)**, then summed. |

## 2. Population and exclusions

- **Customer** = an account in `customers`. **Paying customer at month-end D** = customer MRR on D > 0.
- **Excluded everywhere:** internal / test accounts — `email_domain = 'ourcompany.example'` or company name matching `test`, `[internal]`, `qa sandbox`, `zz_test` (case-insensitive). See `sql/01_stg_customers.sql`.
- **Invoices:** lines with `status = 'void'` are excluded. `uncollectible` lines stay in billed revenue (they were real charges) and are reported separately.
- **Usage:** exact duplicate rows are removed; the partial week containing the as-of date is excluded from weekly trends.

## 3. MRR (Monthly Recurring Revenue)

**Subscription terms on day D.** Start from the subscription's `initial_*` terms; apply every `plan_changes` row with `effective_date ≤ D` in order of `effective_date`, then `change_id`. The last row's `to_*` values are the terms on D.

**Live on day D.** `start_date ≤ D` and (`end_date` is null or `end_date > D`). `end_date` is exclusive (the first day without service).

**Subscription MRR on D** = `seats × monthly-equivalent unit price × (1 − discount_pct)`, where the monthly-equivalent unit price is `unit_price` for monthly billing and **`unit_price ÷ 12` for annual billing** (annual prepaid contracts are spread evenly over 12 months; they are **not** counted in the month they are invoiced).

**Customer MRR at month-end** = sum of subscription MRR over subscriptions live on the last day of the month. **Total MRR** = sum over paying customers.

| Included | Excluded |
|---|---|
| Per-seat subscription fees for the plan | One-time fees (onboarding, implementation) |
| Recurring negotiated discounts (MRR is net of them) | Proration lines (billing timing of a change already reflected in MRR) |
| Customers in dunning (card retry in progress) until the subscription is canceled | Tax / VAT |
| | Void invoices, refunds and credits |
| | Internal / test accounts; subscriptions with 100% discount (MRR = 0, not "paying") |
| | Seat overage above purchased seats (not billed in this business) |

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
| Unchanged | `prev = cur` | 0 (any changes inside the month netted to zero, e.g., a "neutral" plan change) |

**Identity (checked every month):** `MRR_end = MRR_start + New + Expansion + Reactivation − Contraction − Churn`.

**Known blind spots of month-end snapshots (accepted, quantified in data-quality checks):**
- A customer who starts and cancels inside the same calendar month never appears ("subscribed and churned" in ChartMogul's terms).
- Several changes inside one month are netted (e.g., +10 seats then −4 seats = expansion of 6 seats).
- A customer who cancels and wins back inside the same month shows only the net difference.

### 4.1 Expansion and contraction by component
Each `plan_changes` row has `ΔMRR = MRR(to terms) − MRR(from terms)`, labeled by its `change_type`: **plan upgrade / downgrade** (tier changed — includes any seats changed in the same row), **seat increase / decrease**, **billing period change**, **discount change**.
For a customer-month classified as Expansion with net amount *E*, component *k* receives `E × (sum of positive ΔMRR of type k) ÷ (sum of all positive ΔMRR)` among rows effective inside the month. Contraction is allocated the same way using negative ΔMRR. If there are no change rows (e.g., a same-month win-back on different terms), the amount goes to `other`. By construction the components sum exactly to Expansion and Contraction.

**Monthly → annual switches are contraction** (annual is cheaper per seat, so the run-rate falls). This is a deliberate retention trade, so it is always reported as its own component (`billing_period_change`) and never mixed into "customers unhappy" narratives.

## 5. Churn and movement rates

| Metric | Formula (month *m*) |
|---|---|
| Logo churn rate | customers with a Churn movement in *m* ÷ paying customers at end of *m − 1* |
| Gross MRR churn rate | (Churn + Contraction) ÷ MRR at end of *m − 1* |
| Net MRR churn rate | (Churn + Contraction − Expansion) ÷ MRR at end of *m − 1* (reactivation excluded) |
| Voluntary / involuntary split | by `subscriptions.cancel_reason` of the subscription that ended |

**Quarterly movement rates** (used for "expansion slowed last quarter"): expansion rate = sum of the quarter's three monthly Expansion amounts ÷ MRR at the end of the month before the quarter. Contraction, churn and new rates use the same denominator. Year-over-year comparisons use the same quarter of the prior year to remove seasonality.

## 6. Upgrade and downgrade counts
An **upgrade** is a `plan_changes` row whose `to_plan` tier is higher than `from_plan`; a **downgrade** is lower. **Monthly upgrade rate (Basic → higher)** = Basic customers with an upgrade effective in month *m* ÷ customers on Basic at the end of *m − 1*.

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
| Point-to-point or compounded? | **Point-to-point.** A customer who contracts in March and recovers in June shows no loss. The flow-based view is §5. |
| Board quote | NRR at the quarter-end month, e.g., "NRR (TTM) at 30 Sep 2026". |
| Segment NRR | Partition `B(t)` by the customer's segment / industry / channel (static attributes) or by plan / billing period **at t − 12** (state at the start, to avoid survivor bias). |

## 8. GRR — Gross Revenue Retention (trailing 12 months)

`GRR(t) = Σ_{c ∈ B} min(MRR_c(t), MRR_c(t − 12)) ÷ Σ_{c ∈ B} MRR_c(t − 12)`.
Each customer is capped at its own starting MRR, so GRR excludes expansion, cannot exceed 100%, and one customer's growth cannot hide another's shrinkage. **Logo retention (TTM)** = share of `B(t)` still paying at *t*.

## 9. Silent contraction ("silent churn")

An account is **silently contracting** at month-end *t* if all hold:
1. it is paying at *t* and has no cancellation requested;
2. `MRR_t ≤ 80% × max(MRR at month-ends t−6 … t−1)` — spend down at least 20% from its recent peak;
3. it had at least one Contraction movement in months *t−5 … t*.

Reported as: number of accounts, **current ARR** (`12 × MRR_t`, the revenue still at risk) and its share of total ARR, and **lost ARR** (`12 × (peak − MRR_t)`). The 20% threshold and 6-month window are defaults; the dashboard lets you change them.

**Early-warning rule (leading indicators).** Defined and back-tested in `notebooks/04_silent_churn.ipynb`: at month-end *t* flag accounts with low seat utilization and falling weekly active users that have **not yet** contracted in the last 3 months. **Outcome:** contraction ≥ 10% of MRR or churn within the next 3 month-ends. **Precision** = flagged with outcome ÷ flagged; **recall** = flagged with outcome ÷ all accounts with outcome; **lift** = precision ÷ base rate; **lead time** = months from first flag to outcome.

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
- **Feature intensity** = events per WAU per week (e.g., premium-feature gate hits per active user).
- Windows use complete weeks only; a "90-day window" = the 13 complete weeks ending on or before the index date.

## 13. Upgrade-signal analysis (leakage rules)
- **Population:** month-ends *t* at which the customer is paying on Basic with ≥ 3 months tenure.
- **Label:** an upgrade (Basic → Premium / Enterprise) effective in `(t, t + 90 days]`.
- **Features:** only data observable at *t* — usage in the 13 complete weeks ending at *t*, seats and terms at *t* and *t − 3 months*, static attributes. Nothing after *t*.
- **Upgraders vs non-upgraders:** upgraders are measured at the last month-end before their upgrade; non-upgraders at month-ends with label 0, matched on tenure band.
- **Model validation:** time-based split (train on earlier index months, test on later), never a random row split.

## 14. Reconciliation to billing
**Billed recurring revenue** for a period = non-void `recurring` + `proration` invoice lines, spread **by day** over their service period. **Daily-MRR revenue** = Σ over days of `MRR_day × 12 ÷ 365`. Over a full year the two should agree within 1%; the gap is explained by day-count conventions (monthly periods of 28–31 days) and is reported in `03_data_quality_checks.md`. One-time fees and tax are excluded from both.

## 15. Where each metric is implemented

| Metric | SQL file | Output table |
|---|---|---|
| Clean sources & exclusions | `sql/01`–`05_stg_*.sql` | `stg_*` |
| Subscription terms over time | `sql/10_int_subscription_versions.sql` | `int_subscription_versions` |
| Customer MRR at month-end | `sql/20_fct_customer_mrr_monthly.sql` | `fct_customer_mrr_monthly` |
| MRR movements | `sql/21_fct_mrr_movements.sql` | `fct_mrr_movements` |
| Movement components | `sql/22_fct_mrr_movement_components.sql` | `fct_mrr_movement_components` |
| Monthly waterfall & rates | `sql/23_rpt_mrr_waterfall.sql` | `rpt_mrr_waterfall_monthly` |
| Quarterly movement rates | `sql/24_rpt_mrr_quarterly.sql` | `rpt_mrr_quarterly` |
| NRR / GRR / logo retention (TTM) | `sql/25_rpt_nrr_grr_ttm.sql` | `rpt_nrr_grr_ttm` |
| Segment NRR / GRR | `sql/26_rpt_segment_retention.sql` | `rpt_segment_retention_ttm` |
| Cohort retention | `sql/27_rpt_cohort_retention.sql` | `rpt_cohort_retention` |
| Plan transitions | `sql/30_rpt_plan_transitions.sql` | `rpt_plan_transition_matrix` |
| Weekly usage & utilization | `sql/40_fct_account_usage_weekly.sql` | `fct_account_usage_weekly` |
| Account-month features | `sql/41_fct_account_month_features.sql` | `fct_account_month_features` |
| Silent contraction | `sql/50_rpt_silent_contraction.sql` | `rpt_silent_contraction` |
| Billing reconciliation | `sql/90_dq_revenue_reconciliation.sql` | `dq_revenue_reconciliation` |
| Data-quality checks | `sql/91_dq_checks.sql` | `dq_check_results` |

## 16. Questions people ask (and our answers)

- **"Why not just sum invoices for MRR?"** Annual contracts are invoiced once a year; invoice totals spike in renewal months. MRR spreads them evenly. §14 shows billing and MRR agree.
- **"Why month-end and not average MRR?"** Movements need two clean snapshots to classify a customer. Daily MRR is used only for the billing reconciliation.
- **"Does reactivation count in NRR?"** Only for customers who were paying 12 months ago (they are in the base). Win-backs of customers who left earlier are reported as Reactivation in the waterfall, not in NRR.
- **"Is a downgrade-to-save a win?"** It avoids churn but is still Contraction. Saved accounts are tracked in the silent-contraction list.
- **"Do failed payments count as churn immediately?"** No — only when the subscription is canceled after the 14-day dunning window.
- **"Segment changed during the year?"** Segments are static in this data. Plan and billing period are taken at the start of the measurement window.
- **"Why is GRR capped per customer instead of (start − churn − contraction) ÷ start?"** The capped point-to-point version is what investors compare across companies and cannot be flattered by temporary contraction-then-recovery. The flow-based rate is in §5.
