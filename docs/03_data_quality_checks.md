# 03 — Data quality checks

**Run:** every pipeline build (`python run.py`), seed 20261103, data as of 2026-09-30. **Result:** 36 checks pass, 0 fail,
13 are monitored quantities (known issues found and handled). This page is generated from `sql/91_dq_checks.sql`,
`sql/90_dq_revenue_reconciliation.sql`, `src/saas_rev/crosscheck.py`, `src/saas_rev/validation.py` and
`data_gen/sanity_checks.py`; do not edit it by hand.

## Summary for reviewers

- **Sources reconcile row by row** from raw to staging (every excluded row is accounted for: internal accounts, void invoice lines,
  rejected plan changes, double-loaded usage rows).
- **Keys are unique, no orphans, no nulls in keys or dates, nothing outside the GA → as-of window.**
- **The metric layer is internally consistent:** the monthly waterfall identity and the movement-component tie-out close to the cent;
  the plan-change chaining invariant has 0 violations.
- **Billing agrees with MRR:** invoices spread over their service periods match daily-MRR revenue within
  0.08% per calendar year and
  0.01% over the last 12 months.
- **Two implementations agree:** an independent Python re-implementation written from `02_metric_definitions.md` alone matches the SQL
  to the cent on every customer-month (16/16 core comparisons match, max money difference ≤ $0.00).
- **Known-answer tests:** the injected data-quality issues are found with the exact injected counts, and the planted business effects are
  recovered by the metric layer (table at the end).

## Source volumes

| Table | Rows |
|---|---|
| customers | 5,282 |
| subscriptions | 5,394 |
| plan_changes | 6,729 |
| invoices | 47,337 |
| usage_events | 1,506,967 |
| plans | 3 |

## Row-count reconciliation

| ID | Check | Observed | Expected | Status | Note |
|---|---|---|---|---|---|
| RC01 | raw customers = staged customers | 0 | 0 | pass |  |
| RC02 | raw subscriptions - internal subscriptions - staged subscriptions | 0 | 0 | pass |  |
| RC03 | raw plan_changes - staged - rejected | 0 | 0 | pass |  |
| RC04 | raw invoice lines - void - internal non-void - staged | 0 | 0 | pass |  |
| RC05 | staged usage rows = distinct (customer, week, event) of real customers | 0 | 0 | pass |  |

## Primary keys

| ID | Check | Observed | Expected | Status | Note |
|---|---|---|---|---|---|
| PK01 | customers.customer_id duplicates | 0 | 0 | pass |  |
| PK02 | subscriptions.subscription_id duplicates | 0 | 0 | pass |  |
| PK03 | plan_changes.change_id duplicates | 0 | 0 | pass |  |
| PK04 | invoices.invoice_line_id duplicates | 0 | 0 | pass |  |
| PK05 | raw usage rows duplicated on (customer, week, event) — removed in staging | 6,004 | ~0.4% of rows | info | pipeline double-loads; de-duplicated in 05_stg_usage_events |
| PK06 | fct_customer_mrr_monthly (customer, month) duplicates | 0 | 0 | pass |  |
| PK07 | fct_account_usage_weekly (customer, week) duplicates | 0 | 0 | pass |  |

## Nulls

| ID | Check | Observed | Expected | Status | Note |
|---|---|---|---|---|---|
| NL01 | null keys or dates in subscriptions (id, customer, start, plan, seats) | 0 | 0 | pass |  |
| NL02 | null keys or dates in plan_changes | 0 | 0 | pass |  |
| NL03 | null keys or amounts in invoices | 0 | 0 | pass |  |
| NL04 | customers with missing industry (bucketed as Unknown) | 47 | ~2% of self-serve SMB | info | not asked at self-serve signup |
| NL05 | customers with missing employee_count | 147 | ~3% | info | segment is always present and is used instead |

## Referential integrity

| ID | Check | Observed | Expected | Status | Note |
|---|---|---|---|---|---|
| RI01 | subscriptions without a customer | 0 | 0 | pass |  |
| RI02 | plan_changes without a subscription | 0 | 0 | pass |  |
| RI03 | invoice lines without a subscription | 0 | 0 | pass |  |
| RI04 | usage rows for unknown customers | 0 | 0 | pass |  |
| RI05 | plan_changes customer_id differs from its subscription's customer | 0 | 0 | pass |  |

## Value ranges and outliers

| ID | Check | Observed | Expected | Status | Note |
|---|---|---|---|---|---|
| VR01 | versions with seats < 1 or discount outside [0, 1) | 0 | 0 | pass |  |
| VR02 | versions whose unit price differs from the list price for plan + billing period | 0 | 0 | pass | all deals use list price; negotiation is in discount_pct |
| VR03 | negative invoice amounts | 0 | 0 | pass |  |
| VR04 | largest single customer share of MRR at as-of (%) | 2.347 | < 5% | pass | concentration check |
| VR05 | account-weeks with WAU above seats (%) | 0.1878 | rare (< 1%) | info | overage tolerated up to 10%, never billed — kept as a signal |
| VR06 | account-weeks with WAU above 110% of seats | 0 | 0 | pass | beyond the tolerated overage |
| VR07 | customers with employee_count outside their segment band | 149 | ~3% | info | segment (set by Sales Ops at signup) is the system of record |

## Time ranges

| ID | Check | Observed | Expected | Status | Note |
|---|---|---|---|---|---|
| TR01 | subscriptions starting before GA (2023-10-02) | 0 | 0 | pass |  |
| TR02 | events after the as-of date (subscriptions, changes, invoices) | 0 | 0 | pass |  |
| TR03 | usage weeks outside [GA week, as-of week] | 0 | 0 | pass |  |
| TR04 | partial final usage week rows (excluded from trends) | 1.799e+04 | info | info | week of 2026-09-28 has 3 of 7 days |
| TR05 | usage rows in weeks after the subscription ended (grace period) | 2,211 | info | info | kept in usage, never used for MRR |

## Model logic and identities

| ID | Check | Observed | Expected | Status | Note |
|---|---|---|---|---|---|
| ML01 | plan-change chaining invariant violations | 0 | 0 | pass | 02 §3 |
| ML02 | plan changes rejected (effective on/after end date or before start) | 13 | ~0.2% of rows | info | keyed in after cancellation; dropped |
| ML03 | customers with overlapping live subscriptions | 0 | 0 | pass | one live subscription per account |
| ML04 | waterfall identity: max |end - (start + movements)| over months ($) | 0 | 0.00 | pass | 02 §4 |
| ML05 | components tie-out: |sum(components + other) - (expansion - contraction)| ($) | 0 | 0.00 | pass | 02 §4.1 |
| ML06 | change rows whose four components do not sum to delta MRR | 0 | 0 | pass |  |
| ML07 | negative MRR customer-months | 0 | 0 | pass |  |
| ML08 | billing reconciliation gap, calendar 2025 (%) | -0.0311 | |gap| < 0.3% | pass | 02 §14 |
| ML09 | billing reconciliation gap, trailing 12 months (%) | -0.0076 | |gap| < 0.3% | pass | 02 §14 |

## Exclusions

| ID | Check | Observed | Expected | Status | Note |
|---|---|---|---|---|---|
| EX01 | internal / test accounts excluded | 6 | 6 | pass | 02 §2 (domain + whole-token name rule) |
| EX02 | MRR the internal accounts would add at as-of if not excluded ($) | 1.088e+04 | info | info | includes a 200-seat sandbox at list price |
| EX03 | duplicate CRM records (same domain, no subscription) | 16 | ~0.3% of accounts | info | never paying, so no MRR impact |
| EX04 | void invoice lines excluded | 273 | ~0.5% + internal | info |  |

## Known blind spots of month-end snapshots

| ID | Check | Observed | Expected | Status | Note |
|---|---|---|---|---|---|
| BS01 | subscriptions that started and ended inside one calendar month (never on a month-end) | 4 | info | info | invisible to month-end MRR ("subscribed and churned") |
| BS02 | customer-months with a same-month win-back or mid-month residual (component "other") | 19 | info | info |  |

## Billing reconciliation (`02_metric_definitions.md §14`)

Billed recurring revenue (recurring + proration invoice lines, spread by day over their service period and clipped to the
subscription's live days) vs revenue implied by daily MRR (MRR × 12 ÷ days in the year).

| Window | Period | Billed (serviced days) | Daily-MRR revenue | Gap | Gap % | Billed, not serviced | Of which uncollectible |
|---|---|---|---|---|---|---|---|
| calendar_year | 2024 | $4.7M | $4.7M | $3.8K | 0.081% | $2.2K | $4.2K |
| calendar_year | 2025 | $12.1M | $12.1M | −$3.8K | −0.031% | $6.4K | $12.9K |
| ttm | Oct 2025 - Sep 2026 | $19.5M | $19.5M | −$1.5K | −0.008% | $13.0K | $24.1K |

Monthly gaps are a trend, not a test: a monthly price is spread over its own 28–31-day period while daily MRR uses a constant rate, so
February runs high and 31-day months slightly low; annual contracts reconcile exactly. "Billed, not serviced" is mostly failed card
renewals whose period was invoiced in full while service stopped 14 days in (dunning).

## SQL vs independent Python implementation

`src/saas_rev/crosscheck.py` was written by a second analyst from `02_metric_definitions.md` only (without reading `sql/`);
money is compared in whole cents, ratios to 1e-9. The comparator itself was tested by injecting a 1-cent change, a dropped row and a
relabelled movement (each was caught).

| Check | Rows | Only in one side | Max abs diff | Mismatching rows | Status |
|---|---|---|---|---|---|
| customer_mrr | 82,072 | 0 | 0 | 0 | match |
| movement_type | 82,072 | 0 | 0 | 0 | match |
| movement_prev_cur | 82,072 | 0 | 5.7e-13 | 0 | match |
| movement_new_mrr | 82,072 | 0 | 0 | 0 | match |
| movement_reactivation_mrr | 82,072 | 0 | 0 | 0 | match |
| movement_expansion_mrr | 82,072 | 0 | 5.7e-13 | 0 | match |
| movement_contraction_mrr | 82,072 | 0 | 2.3e-13 | 0 | match |
| movement_churn_mrr | 82,072 | 0 | 0 | 0 | match |
| waterfall_totals | 36 | 0 | 1.9e-08 | 0 | match |
| waterfall_rates | 36 | 0 | 4.0e-13 | 0 | match |
| waterfall_components | 36 | 0 | 1.5e-11 | 0 | match |
| nrr_grr_ttm | 24 | 0 | 7.0e-10 | 0 | match |
| nrr_runrate_quarterly | 11 | 0 | 7.0e-10 | 0 | match |
| grr_runrate_quarterly | 11 | 0 | 1.2e-15 | 0 | match (info) |
| movement_components | 63,006 | 0 | 5.7e-13 | 0 | match |
| cohort_cells | 900 | 0 | 0 | 0 | match |
| cohort_values | 900 | 0 | 8.7e-11 | 0 | match |
| cohort_values_partial_observed | 24 | 0 | 2.2e-16 | 0 | match (info) |

**Differences found and resolved.** The first comparison surfaced two documentation gaps, both fixed in definitions v1.1:
(1) customer-months between a churn and a win-back (MRR 0 both months) had no defined state — now *Not paying*;
(2) partial quarterly-cohort cells carried values in SQL although the definitions say blank — the SQL now leaves them blank and keeps the
observed-member ratio in a separate column. Neither changed any MRR, movement, NRR or GRR value.

## Generator sanity checks (design targets in `docs/data_model.md §8`)

| Check | Value | Min | Max | Pass |
|---|---|---|---|---|
| Accounts ever paying (excl. internal) | 5,260 | 5,000 | 7,000 | True |
| Paying accounts at 2026-09-30 | 4,117 | 3,000 | 5,000 | True |
| MRR at 2026-09-30 ($) | 2,062,545 | 1,200,000 | 2,600,000 | True |
| Signup plan mix: Basic share | 0.6918 | 0.6 | 0.72 | True |
| Signup plan mix: Enterprise share | 0.0291 | 0.01 | 0.06 | True |
| Signup annual-billing share | 0.3297 | 0.25 | 0.45 | True |
| Monthly logo churn, SMB | 0.0263 | 0.02 | 0.05 | True |
| Monthly logo churn, Mid-Market | 0.007 | 0.003 | 0.015 | True |
| Monthly logo churn, Enterprise | 0.0033 | 0.001 | 0.01 | True |
| TTM GRR at 2025-09-30 | 0.859 | 0.82 | 0.92 | True |
| TTM NRR at 2025-09-30 | 1.048 | 1.02 | 1.18 | True |
| TTM NRR change, 2026-06 -> 2026-09 (pts) | -3.51 | -10 | -1.5 | True |
| Expansion MRR, Q3 2026 vs Q2 2026 | -0.3285 | -0.6 | -0.15 | True |
| Expansion rate, Q3 2026 vs Q3 2025 (relative) | -0.3064 | -0.6 | -0.15 | True |
| Tech seat-add events per account-month, Jun-Sep 2026 vs prior 12 months | -0.7705 | -0.9 | -0.4 | True |
| Voluntary Basic upgrade rate, Q3 2026 vs Q3 2025 (relative) | -0.6357 | -0.7 | -0.3 | True |
| Basic monthly logo churn (non-Paid-Social), May-Sep 2026 vs 2025 | -0.0734 | -0.35 | -0.05 | True |
| Silent contraction at 2026-09-30, share of MRR | 0.0397 | 0.02 | 0.12 | True |

## Known-answer tests

Because the data is synthetic, the injected issues and planted business effects are known. These tests check that the pipeline finds
them — a test of the method, not a finding. The analysis modules never read the answer key; only `src/saas_rev/validation.py` does.

**Injected data-quality issues**

| Injected issue | Injected | Found | Verdict |
|---|---|---|---|
| Internal / test accounts excluded | 6 | 6 | match |
| Duplicate CRM records flagged | 16 | 16 | match |
| Orphan plan changes rejected | 13 | 13 | match |
| Duplicated usage rows removed | 6004 | 6004 | match |

**Planted business effects**

| Planted effect | Planted (generator) | Recovered (metric layer) | What a naive cut would say | Verdict |
|---|---|---|---|---|
| S5 one-off rollout (2026-05-12) | one Financial Services Enterprise account × 2.0 seats | Lakewave Payments +$26.4K MRR in May = 28% of Q2 expansion; the only single-account expansion above 10% of any quarter | Q2 → Q3 expansion −33% reads as a collapse | recovered |
| S4 Technology hiring freeze + layoffs (from 2026-04) | team growth -0.35 pt/week; 65% of Tech accounts lose 10%–35% of the team in Jun–Jul | Technology expansion 6.2% → 1.2% and contraction 0.9% → 5.7% of Technology starting MRR (Q3 2025 → Q3 2026); Technology 12-month NRR 109% → 96% | company-wide averages hide that one industry drives most of the change | recovered |
| S3 Basic packaging change (2026-04-15) | upgrade hazard × 0.5, paywall hits × 0.4; upside: Basic churn × 0.85 | voluntary Basic upgrade rate 1.51% → 0.69% per month (non-Paid-Social 1.60% → 0.77%; in two steps: 1.88% in Apr–Sep 2025 → 1.38% in Oct 2025 – Mar 2026, before the change (no change was planted before April, so this drift is composition or noise) → 0.77% in May–Sep 2026); paywall hits per active user × 0.39; automation runs × 2.95; Basic monthly churn 1.93% → 1.79% (×0.93, 95% change −0.62 pts to +0.31 pts: the planted upside is inside the interval but not separable from zero) | a before/after on the pooled Basic upgrade rate also picks up the Paid Social mix shift | recovered (upgrade step); retention upside not separable |
| S2 Paid Social program (from 2025-09) | extra SMB self-serve accounts with lower product fit and fewer power users | Paid Social share of new logos 7% (Q2 2025) → 21% (Q2 2026); 6-month GRR 76% vs 83% for other SMB signups (Sep 2025 – Mar 2026 cohorts) | new-logo counts look strong while the cohorts retain worse | recovered |
| S1 design partners and early adopters (Oct–Dec 2023) | 40 design partners; early adopters with higher product fit | Q4 2023 cohort NRR at month 12 101% vs a median of 99% (top quartile from 103%, best 109%) for the 2024 – Q3 2025 cohorts; at month 24 116% vs up to 126% for the 3 later cohorts observed that long | early TTM NRR points (small base) overstate the steady state | weak — right direction at month 12, not distinctive |
| S6 seasonality | summer hiring lull (Jul–Aug team growth −0.10 pt/week), holiday usage dip | Q2 → Q3 expansion-rate change −0.9 pts (2024), +0.3 pts (2025) | a single year's Q2 → Q3 change is read as the seasonal effect | not separable from noise with n = 2 (as expected) |
