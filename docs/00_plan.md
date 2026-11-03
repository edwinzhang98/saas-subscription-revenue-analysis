# 00 — Project plan (one page)

**Project:** SaaS subscription revenue analysis — why expansion slowed, who is quietly downgrading, where Customer Success (CS) should focus.
**Role:** Revenue Analyst, Revenue Operations / Product Analytics. **Stakeholders:** VP of Revenue (owns the board narrative), Head of Customer Success (owns the Q1 2027 coverage plan).
**Date:** 2026-11-03 · **Data as of:** 2026-09-30 (Q3 2026 close) · **Status:** scope agreed with VP Revenue on 2026-11-03

## 1. Data
- **Source:** synthetic. Public row-level SaaS billing data with upgrades, downgrades and usage does not exist (the IBM Telco churn file has no plan-change events and no usage). The generator is written as a data-model exercise: five source tables like a billing system and a product analytics export would produce — `customers`, `subscriptions`, `plan_changes`, `invoices`, `usage_events` (+ `plans` catalog).
- **Shape:** ≈ 5,300 B2B accounts acquired over 36 months (Oct 2023 general availability → Sep 2026; 4,117 paying at the cutoff, MRR ≈ $2.1M), seat-based pricing, 3 tiers (Basic / Premium / Enterprise), monthly and annual billing, proration, seat and plan changes, voluntary and payment-failure churn, win-backs, seasonality, and segment differences by size, industry, channel and cohort.
- **Rules first, data second:** the generation logic and every parameter are written in `docs/data_model.md` before the first run; calibration runs are logged there (§9) and 18 sanity checks gate the published dataset (§8). Fixed seed. Generated files live outside the repo (`config.yaml → data_dir`).
- **Honesty:** the business events are built into the generator and listed as an answer key (`docs/data_model.md §6`); in the scope doc they appear as inputs from Sales, Product, CS and Finance, the way a real team would know them. The value of the project is the metric layer, the diagnosis method and the CS / Sales / Product actions — not the "discovery" itself. Known-answer tests check that the method recovers what was built in, and the report shows where a naive cut gets it wrong.

## 2. Analysis outline (mapped to the VP's three questions)
| VP question | Analysis | Output |
|---|---|---|
| "Board wants NRR" | Metric layer in SQL (MRR, ARR, NRR, GRR, movements), cross-checked by an independent Python implementation | `docs/02_metric_definitions.md`, `sql/`, NRR / GRR trend, cohort heatmap |
| "Expansion slowed last quarter — why?" | Monthly MRR waterfall; two sequential bridges (Q3 2026 vs Q2 2026 = the VP's framing; Q3 2026 vs Q3 2025 = primary view for structural drivers) built in a stated removal order (one-off rollout → Technology → Basic upgrade rate → Basic base mix incl. Paid Social → seats vs plan upgrades → residual), overlapping drivers reported as ranges across orderings, seasonality labelled n = 2; expansion split by component (seats / upgrades / billing switches) and by segment | `notebooks/01_metrics`, `02_plan_transitions` |
| "Who is quietly downgrading?" | Silent-churn definition (spend down ≥ X% without cancelling), profile, leading-indicator rule back-tested for precision / recall / lead time | `notebooks/04_silent_churn`, watchlist |
| "Which accounts should CS prioritize?" | Upgrade signals (90 days before upgrade vs matched non-upgraders), simple interpretable propensity model; combined account health list with reason codes | `notebooks/03_expansion_signals`, `docs/account_health_list.csv` |
| Turn findings into actions | ≥ 5 plays: who, what, expected NRR impact (sized from data + stated assumptions), how to measure (holdout), risks | `notebooks/05_recommendations`, `docs/readout.md` |

## 3. Deliverables
Company-style documents: `docs/01_request_and_scope.md`, `02_metric_definitions.md`, `03_data_quality_checks.md`, `data_model.md`, `assumptions.md`, `monthly_revenue_review.md` (Q3 2026 close revenue review, September month-end), `readout.md` (one-page board revenue review), `account_health_list.csv` (schema in `02 §15`).
Showcase: multi-page Streamlit app (`dashboard/app.py`), standalone `reports/report.html`. One command (`make all`) regenerates everything. Spec in §3b.

## 3b. Showcase spec
- **Pages, in demo order** (each opens with a one-line question and closes with a "How to read this" expander): 1 Overview — what happened to NRR and expansion, in three numbers; 2 Data Model — what the data is, how it was generated, planted vs recovered effects; 3 MRR Waterfall — where MRR moved each month; 4 NRR / GRR Cohorts — do customers grow after they join; 5 Plan Transitions — who moves between tiers, and why; 6 Expansion Signals — which Basic accounts look like upgraders; 7 Silent Churn Watchlist — who is shrinking without cancelling; 8 Recommendations — what each play is worth in NRR points.
- **Controls that change the calculation** (not just filters): Silent Churn Watchlist — contraction threshold slider 10–40% and lookback 3 / 6 / 9 months, recomputing the flagged accounts, ARR at risk and back-test precision / recall; Expansion Signals — CS-capacity cutoff (number of accounts) recomputing precision, recall and ARR covered; Recommendations — reach % and effect-size sliders recomputing each play's NRR-point impact; NRR / GRR Cohorts — segment and cohort-grain (month / quarter) selectors.
- **Data path:** pages read only the cached parquet marts and recompute light pandas aggregates on them; they never rebuild the warehouse. First page in under 10 s.
- **One chart module** (`src/saas_rev/charts.py`, Plotly) used by Streamlit, the notebooks and `reports/report.html`, so every figure is identical in all three.
- **Screenshots:** Playwright → `reports/screenshots/*.png`, embedded in the README.
- **Notebooks:** executed with `jupyter nbconvert --execute` and exported to HTML in `reports/`; fixed template TL;DR → question → data → method → results → limitations → recommendations (+ what data I'd want next).
- **SQL:** every `sql/*.sql` header states inputs, outputs and the run command; each file runs on its own in DuckDB.
- **Git:** commit + push at the end of every phase.

## 4. What I borrow from references, and what I add
- **Borrow:** ChartMogul's MRR movement taxonomy (new / expansion / contraction / churn / reactivation, plus "neutral" changes and the "subscribed and churned within the period" blind spot); Stripe / ChartMogul NRR and GRR formulas (same-customer base 12 months ago; GRR excludes expansion and caps at 100%); dbt-style layering and naming (`stg_` → `int_` → `fct_` / `dim_` → `rpt_`). Typical public SaaS-metrics projects (MRR trend, logo churn, cohort tables) — kept as the base layer.
- **Add (gaps in every reference):** precise definitions doc as single source of truth; SQL vs Python reconciliation; invoice-to-MRR reconciliation; plan transition flows; expansion / contraction decomposition; silent-churn detection with a back-tested rule; leakage-safe pre-upgrade signal analysis; recommendations sized in NRR points with a measurement plan; a monthly business review and a board one-pager in the formats a RevOps team actually ships.

## 5. Key decisions and rationale
1. **Synthetic data over IBM Telco** — Telco cannot answer the expansion / downgrade questions, which are the point of the project.
2. **MRR = month-end snapshot of contracted recurring price** (annual ÷ 12, net of recurring discounts, excluding one-time fees, prorations and tax) — the industry norm; it reconciles to invoices within a documented tolerance.
3. **Headline NRR = trailing-12-month, same-customer, point-to-point** — what boards and investors expect; monthly flow retention and quarterly run-rate NRR (`02 §5`, `§7`) are shown as diagnostics only.
4. **Interpretable models only** (logistic regression, rules) — CS needs reasons it can say to a customer; the brief asks for signals, not tuning.
5. **Data lives outside git** (`~/projects/data-projects/datasets/p5-saas-synthetic` by default, configurable); the dashboard reads a parquet cache, never recomputes the warehouse.

## 6. Phases
1 Scope + definitions + data model → 2 generator + sanity checks → 3 SQL metric layer + data quality + Python cross-check → 4 analyses (notebooks 01–05, executed with nbconvert) → 5 Streamlit + `report.html` + screenshots → 6 MBR, readout, CS list, README → 7 clean-clone run with timing, acceptance checklist. Each phase ends with a commit + push.

## 7. Risks
Synthetic data can look too clean → realistic noise, overlapping drivers and injected data-quality issues. Over-claiming causality → correlational language, intervals, and holdouts as the validation path.
