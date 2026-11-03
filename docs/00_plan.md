# 00 — Project plan (one page)

**Project:** SaaS subscription revenue analysis — why expansion slowed, who is quietly downgrading, where Customer Success (CS) should focus.
**Role:** Revenue Analyst, Revenue Operations / Product Analytics. **Stakeholders:** VP of Revenue (owns the board narrative), Head of Customer Success (owns the Q4 account plan).
**Date:** 2026-11-03 · **Data as of:** 2026-09-30 (Q3 2026 close) · **Status:** approved to start (hosted mode, decisions logged below)

## 1. Data
- **Source:** synthetic. Public row-level SaaS billing data with upgrades, downgrades and usage does not exist (the IBM Telco churn file has no plan-change events and no usage). The generator is written as a data-model exercise: five source tables like a billing system and a product analytics export would produce — `customers`, `subscriptions`, `plan_changes`, `invoices`, `usage_events` (+ `plans` catalog).
- **Shape:** about 6,000 B2B accounts acquired over 36 months (Oct 2023 general availability → Sep 2026), seat-based pricing, 3 tiers (Basic / Premium / Enterprise), monthly and annual billing, proration, seat and plan changes, voluntary and payment-failure churn, win-backs, seasonality, and segment differences by size, industry, channel and cohort.
- **Rules first, data second:** the generation logic and every parameter are written in `docs/data_model.md` before the first run. Fixed seed. Generated files live outside the repo (`config.yaml → data_dir`).
- **Honesty:** some business events are built into the generator (documented in `docs/data_model.md §6`). The value of the project is the metric layer, the diagnosis method and the CS / Sales / Product actions — not the "discovery" itself. Known-answer tests check that the metric layer recovers what was built in.

## 2. Analysis outline (mapped to the VP's three questions)
| VP question | Analysis | Output |
|---|---|---|
| "Board wants NRR" | Metric layer in SQL (MRR, ARR, NRR, GRR, movements), cross-checked by an independent Python implementation | `docs/02_metric_definitions.md`, `sql/`, NRR / GRR trend, cohort heatmap |
| "Expansion slowed last quarter — why?" | Monthly MRR waterfall; Q3 vs Q2 vs Q3-last-year bridge; expansion split by component (seats / upgrades / billing switches) and by segment; one-off vs structural; mix vs rate | `notebooks/01_metrics`, `02_plan_transitions` |
| "Who is quietly downgrading?" | Silent-churn definition (spend down ≥ X% without cancelling), profile, leading-indicator rule back-tested for precision / recall / lead time | `notebooks/04_silent_churn`, watchlist |
| "Which accounts should CS prioritize?" | Upgrade signals (90 days before upgrade vs matched non-upgraders), simple interpretable propensity model; combined account health list with reason codes | `notebooks/03_expansion_signals`, `docs/account_health_list.csv` |
| Turn findings into actions | ≥ 5 plays: who, what, expected NRR impact (sized from data + stated assumptions), how to measure (holdout), risks | `notebooks/05_recommendations`, `docs/readout.md` |

## 3. Deliverables
Company-style documents: `docs/01_request_and_scope.md`, `02_metric_definitions.md`, `03_data_quality_checks.md`, `data_model.md`, `assumptions.md`, `monthly_revenue_review.md` (Sep 2026 MBR), `readout.md` (one-page board revenue review), `account_health_list.csv`.
Showcase: multi-page Streamlit app (`dashboard/app.py`), standalone `reports/report.html`. One command (`make all`) regenerates everything.

## 4. What I borrow from references, and what I add
- **Borrow:** ChartMogul's MRR movement taxonomy (new / expansion / contraction / churn / reactivation, plus "neutral" changes and the "subscribed and churned within the period" blind spot); Stripe / ChartMogul NRR and GRR formulas (same-customer base 12 months ago; GRR excludes expansion and caps at 100%); dbt-style layering and naming (`stg_` → `int_` → `fct_` / `dim_` → `rpt_`). The reference repos (RiyaSingla08, aaryaya, bansal1996shubham-cmd, Liudmyla2667) all cover MRR trend, churn and cohorts — kept as the base layer.
- **Add (gaps in every reference):** precise definitions doc as single source of truth; SQL vs Python reconciliation; invoice-to-MRR reconciliation; plan transition flows; expansion / contraction decomposition; silent-churn detection with a back-tested rule; leakage-safe pre-upgrade signal analysis; recommendations sized in NRR points with a measurement plan; a monthly business review and a board one-pager in the formats a RevOps team actually ships.

## 5. Decisions taken in hosted mode (no check-in needed, reasons logged)
1. **Synthetic data over IBM Telco** — Telco cannot answer the expansion / downgrade questions, which are the point of the project.
2. **MRR = month-end snapshot of contracted recurring price** (annual ÷ 12, net of recurring discounts, excluding one-time fees, prorations and tax) — the industry norm; it reconciles to invoices within a documented tolerance.
3. **Headline NRR = trailing-12-month, same-customer, point-to-point** — what boards and investors expect; monthly-flow NRR is shown as a diagnostic.
4. **Interpretable models only** (logistic regression, rules) — CS needs reasons it can say to a customer; the brief asks for signals, not tuning.
5. **Data lives outside git** (`~/projects/data-projects/datasets/p5-saas-synthetic` by default, configurable); the dashboard reads a parquet cache, never recomputes the warehouse.

## 6. Phases
1 Scope + definitions + data model → 2 generator + sanity checks → 3 SQL metric layer + data quality + Python cross-check → 4 analyses (notebooks 01–05) → 5 Streamlit + HTML report → 6 MBR, readout, CS list, README → 7 acceptance checklist, clean clone run, push.

## 7. Risks
Synthetic data can look too clean → realistic noise, overlapping drivers and injected data-quality issues. Over-claiming causality → correlational language, intervals, and holdouts as the validation path.
