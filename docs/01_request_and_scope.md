# 01 — Request and scope

**Requester:** VP of Revenue · **Also consulted:** Head of Customer Success, Finance (board pack owner), Product (pricing & packaging), Growth Marketing
**Analyst:** Revenue Analyst, RevOps / Product Analytics · **Received:** 2026-11-03 · **Board pack due:** 2026-11-12 (Q3 board meeting 2026-11-19)

## 1. The request (verbatim)

> 「董事会要看 NRR，上季度 expansion 明显慢了，为什么？哪些客户在悄悄降级？CS 团队该优先盯哪些账户？」

English: *"The board wants to see NRR. Expansion clearly slowed last quarter — why? Which customers are quietly downgrading? Which accounts should the CS team prioritize?"*

Standard outputs for this kind of request at our company: a metric definitions document (single source of truth), the monthly MRR movement report, an account health list for CS, and a one-page revenue review for the board.

## 2. Clarified analysis questions

"Last quarter" = Q3 2026 (Jul 1 – Sep 30, 2026), the last closed quarter. Comparisons are to Q2 2026 (sequential — the VP's framing) and Q3 2025 (same quarter last year — the primary view for structural drivers, because it removes seasonality and because two of the known events started in Q2 2026).

| # | Question | Metric (see `02_metric_definitions.md`) | Answer format |
|---|---|---|---|
| Q1 | What is our NRR and GRR, and how have they trended over the last 6 quarters (Q2 2025 – Q3 2026)? | TTM NRR, TTM GRR, by segment | Board number + trend line, with definition footnote. TTM points whose base has fewer than 1,000 customers (every point before Sep 2025, which includes the Q2 2025 point) are shown greyed: a small base with many early adopters. |
| Q2 | How much did expansion MRR slow in Q3, and how much of the change is (a) one-off deals, (b) seasonality, (c) specific segments, (d) fewer plan upgrades vs fewer seat additions, (e) a change in customer mix? | Expansion MRR and expansion rate (expansion ÷ start-of-quarter MRR), split by component and segment | Two sequential bridges: Q3 2026 vs Q2 2026 (the VP's framing) and Q3 2026 vs Q3 2025 (primary view for structural drivers). Each is built in a stated removal order — one-off rollout → Technology segment → Basic upgrade rate (rate effect) → Basic base mix incl. Paid Social (mix effect) → seats vs plan upgrades → residual — with the residual bar shown. Because the drivers overlap, the share attributed to each is reported as a range across orderings, not a single number. Seasonality is estimated from the 2024 and 2025 Q2 → Q3 changes and labelled n = 2. |
| Q3 | Which paying customers are shrinking without cancelling ("silent churn"), how much ARR do they represent, and what do they look like? | Silent contraction definition (§9 of definitions) | Size in ARR and % of ARR, profile vs healthy accounts |
| Q4 | Can we see silent churn coming early enough for CS to act? | Back-tested early-warning rule: precision, recall, lead time | Rule + performance + watchlist |
| Q5 | Which Basic accounts are showing pre-upgrade behavior, and what are those signals? | Upgrade within 90 days; behavior in the 90 days before | Signal table + ranked opportunity list |
| Q6 | What should CS, Sales, Product and Marketing do, and what is it worth in NRR points? | Sized plays with measurement plan | ≥ 5 recommendations |

## 3. Decision makers and decisions

| Decision maker | Decision | When | What would change the decision |
|---|---|---|---|
| VP of Revenue | What NRR number and explanation go into the Q3 board pack; whether to re-forecast Q4 expansion | Board pack 2026-11-12 | If the Q3 2026 expansion rate, excluding the May rollout account, is within 0.5 pt of Q3 2025 → board message "timing, not trend" (rollout called out explicitly), Q4 expansion forecast unchanged. If it is ≥ 1.0 pt below Q3 2025 → re-forecast Q4 and FY2027 expansion, and the board pack names each structural driver with an owner and a plan. In between → forecast held with a downside case, re-checked at the October flash. |
| Head of Customer Success | Which accounts get proactive CSM reviews in the Dec 2026 – Mar 2027 renewal window (Q1 2027 coverage plan; capacity ≈ 120 proactive account reviews per quarter across named and pooled CSMs) | 2026-11-20 | If the top 120 accounts by ARR at stake cover ≥ 60% of risk ARR (silent contraction + near-renewal risk) → CSM-led reviews; the rest go to a digital play (in-app utilization nudge + pre-renewal email sequence 60 days out). If < 60% → shift capacity toward the digital play and size it. |
| Head of Product / Pricing | Keep, adjust (move a different feature, e.g. analytics dashboards or the API, behind the Premium gate) or revert the April 2026 Basic packaging change | Pricing council, Dec 2026 | Adjust or revert if the Basic → Premium upgrade rate, controlled for Paid Social mix and the Technology segment, is down ≥ 20% vs pre-change AND the conversion / retention gains do not offset it in 12-month revenue from Basic cohorts. Keep if the net 12-month Basic cohort revenue is flat or up. |
| Growth Marketing | Whether to change Paid Social targeting / onboarding for 2027 (e.g. default to the annual offer, exclude the segments with the weakest 12-month revenue retention). The budget level stays with Marketing because it needs CAC, which is out of scope here. | 2027 planning, Dec 2026 | Our input: 6- and 12-month revenue retention and upgrade rate of Paid Social cohorts vs Organic / Paid Search cohorts of the same signup months — revenue retained per $1 of first-month MRR by channel cohort — which Marketing combines with CAC. |

## 4. Known business events (inputs, provided by Product, Growth Marketing, Sales, CS and Finance)

| Date | Event | Source |
|---|---|---|
| 2023-10-02 | General availability; 40 private-beta design partners converted to paid | Product |
| 2025-09-01 | Paid Social acquisition program launched (self-serve SMB, mostly monthly plans) | Growth Marketing |
| 2026-04-15 | Basic plan packaging update: advanced automations included in Basic (previously Premium-only). Stated goal: lift Basic trial → paid conversion and early retention | Product / Pricing |
| 2026-05-12 | Company-wide rollout at a Financial Services Enterprise customer (Lakewave Payments): ≈ 2.6× seats, co-termed to the annual contract | Sales bookings |
| 2026 (ongoing) | Public reports of Technology-sector hiring slowdowns and layoffs; CSMs report Technology customers trimming seats at renewal (anecdotal, not quantified) | Customer Success |
| Each quarter-end | Sales-led deals (new logos and expansion) cluster in the last month of the quarter | Sales Ops |
| 2026-10 | Board asked for an auditable NRR after the company crossed ≈ $20M ARR, ahead of the Series C data room; Finance had been quoting a spreadsheet number | Finance |

This list says what happened, not what it did to revenue. The work is to size each event's contribution to the Q3 expansion change, separate temporary from structural, and flag anything material that is not on this list.

## 5. Scope

**In scope:** paying B2B accounts, Oct 2023 – Sep 2026; recurring subscription revenue; seats, plans, billing period, discounts; product usage aggregated by account-week; segment cuts by size, industry, region, acquisition channel, signup cohort, plan, billing period.

**Out of scope:** free / trial users before first payment (not in billing data); GAAP revenue recognition (MRR is a run-rate metric — reconciled to invoices, not to the general ledger); CAC / LTV and pipeline (no marketing spend or opportunity data); pricing elasticity (no price tests in the period).

## 6. Deliverables mapped to questions

| Deliverable | Answers |
|---|---|
| `docs/02_metric_definitions.md`, `sql/` | Q1 (and the "which number is right" question for every other one) |
| `docs/monthly_revenue_review.md` — "Q3 2026 close revenue review (September month-end)"; the October flash follows the 7 Nov close | Q1, Q2, top movers, risk accounts, next-month focus |
| `docs/readout.md` (one-page board revenue review) | Q1, Q2, Q6 in plain language |
| `docs/account_health_list.csv` (schema in `02_metric_definitions.md §15`: owner, renewal date, ARR at stake, reason codes, suggested play; top 120 CSM queue + digital queue) | Q3, Q4, Q5 — one list for CS with reason codes and suggested plays |
| `notebooks/01`–`05`, `reports/report.html`, `dashboard/` | Full evidence for all questions |

## 7. Working assumptions agreed with the requester
- NRR for the board uses the trailing-12-month same-customer definition; Finance has agreed to footnote it (definition in `02_metric_definitions.md §7`).
- "Quietly downgrading" means spend is down materially while the account is still paying — not cancelled accounts (those are already in the churn report).
- CS capacity is the binding constraint, so lists are ranked and cut to what CS can work in a quarter.
