# 01 — Request and scope

**Requester:** VP of Revenue · **Also consulted:** Head of Customer Success, Finance (board pack owner), Product (pricing & packaging), Growth Marketing
**Analyst:** Revenue Analyst, RevOps / Product Analytics · **Received:** 2026-11-03 · **Board pack due:** 2026-11-12 (Q3 board meeting 2026-11-19)

## 1. The request (verbatim)

> 「董事会要看 NRR，上季度 expansion 明显慢了，为什么？哪些客户在悄悄降级？CS 团队该优先盯哪些账户？」

English: *"The board wants to see NRR. Expansion clearly slowed last quarter — why? Which customers are quietly downgrading? Which accounts should the CS team prioritize?"*

Standard outputs for this kind of request at our company: a metric definitions document (single source of truth), the monthly MRR movement report, an account health list for CS, and a one-page revenue review for the board.

## 2. Clarified analysis questions

"Last quarter" = Q3 2026 (Jul 1 – Sep 30, 2026), the last closed quarter. Comparisons are to Q2 2026 (sequential) and Q3 2025 (same season last year).

| # | Question | Metric (see `02_metric_definitions.md`) | Answer format |
|---|---|---|---|
| Q1 | What is our NRR and GRR, and how have they trended over the last 8 quarters? | TTM NRR, TTM GRR, by segment | Board number + trend line, with definition footnote |
| Q2 | How much did expansion MRR slow in Q3, and how much of the change is (a) one-off deals, (b) seasonality, (c) specific segments, (d) fewer plan upgrades vs fewer seat additions, (e) a change in customer mix? | Expansion MRR and expansion rate (expansion ÷ start-of-quarter MRR), split by component and segment | A bridge from Q2 to Q3 expansion, with the share explained by each driver |
| Q3 | Which paying customers are shrinking without cancelling ("silent churn"), how much ARR do they represent, and what do they look like? | Silent contraction definition (§9 of definitions) | Size in ARR and % of ARR, profile vs healthy accounts |
| Q4 | Can we see silent churn coming early enough for CS to act? | Back-tested early-warning rule: precision, recall, lead time | Rule + performance + watchlist |
| Q5 | Which Basic accounts are showing pre-upgrade behavior, and what are those signals? | Upgrade within 90 days; behavior in the 90 days before | Signal table + ranked opportunity list |
| Q6 | What should CS, Sales, Product and Marketing do, and what is it worth in NRR points? | Sized plays with measurement plan | ≥ 5 recommendations |

## 3. Decision makers and decisions

| Decision maker | Decision | When | What would change the decision |
|---|---|---|---|
| VP of Revenue | What NRR number and explanation go into the Q3 board pack; whether to re-forecast Q4 expansion | Board pack 2026-11-12 | If the Q3 slowdown is mostly one-off / seasonal → present as noise, keep forecast. If structural → re-forecast and present a plan. |
| Head of Customer Success | Which accounts get proactive CS time in Q4 (capacity ≈ 120 proactive account reviews per quarter across named and pooled CSMs) | Q4 account plan, 2026-11-15 | Risk concentrated in a few large accounts → named-CSM saves. Spread across many small accounts → digital / tech-touch play. |
| Head of Product / Pricing | Whether to revisit the April 2026 Basic plan packaging change | Pricing council, Dec 2026 | Evidence that upgrade triggers dropped after the change, net of other drivers |
| Growth Marketing | Paid Social budget for 2027 | 2027 planning, Dec 2026 | Cohort NRR / churn of Paid Social accounts vs other channels |

## 4. Known business events (provided by Marketing, Product, Sales)

| Date | Event | Source |
|---|---|---|
| 2023-10-02 | General availability; private-beta design partners converted to paid | Product |
| 2025-09-01 | Paid Social acquisition program launched (self-serve SMB, monthly plans) | Growth Marketing |
| 2026-04-15 | Basic plan packaging update: advanced automations included in Basic (previously Premium-only) | Product / Pricing |
| Each quarter-end | Sales-led deals cluster in the last month of the quarter | Sales Ops |

Anything else (e.g., industry-specific behavior, single large deals) has to come out of the data.

## 5. Scope

**In scope:** paying B2B accounts, Oct 2023 – Sep 2026; recurring subscription revenue; seats, plans, billing period, discounts; product usage aggregated by account-week; segment cuts by size, industry, region, acquisition channel, signup cohort, plan, billing period.

**Out of scope:** free / trial users before first payment (not in billing data); GAAP revenue recognition (MRR is a run-rate metric — reconciled to invoices, not to the general ledger); CAC / LTV and pipeline (no marketing spend or opportunity data); pricing elasticity (no price tests in the period).

## 6. Deliverables mapped to questions

| Deliverable | Answers |
|---|---|
| `docs/02_metric_definitions.md`, `sql/` | Q1 (and the "which number is right" question for every other one) |
| `docs/monthly_revenue_review.md` (Sep 2026 MBR) | Q1, Q2, top movers, risk accounts, next-month focus |
| `docs/readout.md` (one-page board revenue review) | Q1, Q2, Q6 in plain language |
| `docs/account_health_list.csv` | Q3, Q4, Q5 — one list for CS with reason codes and suggested plays |
| `notebooks/01`–`05`, `reports/report.html`, `dashboard/` | Full evidence for all questions |

## 7. Working assumptions agreed with the requester
- NRR for the board uses the trailing-12-month same-customer definition; Finance has agreed to footnote it (definition in `02_metric_definitions.md §7`).
- "Quietly downgrading" means spend is down materially while the account is still paying — not cancelled accounts (those are already in the churn report).
- CS capacity is the binding constraint, so lists are ranked and cut to what CS can work in a quarter.
