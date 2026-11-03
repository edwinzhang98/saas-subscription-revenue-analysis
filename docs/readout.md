# Q3 2026 revenue readout: NRR, the expansion slowdown, and where CS should focus

**To:** VP of Revenue · **cc:** Head of Customer Success, Finance, Product, Growth Marketing
**From:** Revenue Analytics (RevOps) · **Date:** 3 Nov 2026 · **Data:** billing and product usage to 30 Sep 2026 (synthetic dataset, see the note at the end)

## Bottom line

1. **For the board: 12-month NRR is 103.5%** (GRR 84.6%). One customer's company-wide rollout in May props it up: without it NRR is 101.0%. Q3 on its own, annualised, is 92.9%: if Q4 and Q1 look like Q3, the 12-month figure keeps falling.
2. **Expansion did slow, and the underlying part will not bounce back by itself.** Most of the dollar drop from Q2 is that one-off rollout, which inflated Q2. But like for like, expansion is 3.4% of starting revenue in Q3 vs 4.9% a year ago — past the 1-point line we agreed for a re-forecast — and that part is structural: Technology customers stopped adding seats and started cutting them, and Basic → Premium upgrades halved after the April packaging change.
3. **324 paying customers are quietly paying less** — $1.07M of annual revenue still with us, $649K already gone. 60% of them are Technology companies.
4. **CS should work a list of 120 accounts** that holds 66% of the revenue at stake (above the 60% we agreed for CSM-led reviews), renewals first. Seven actions across CS, Sales, Product, Marketing and Billing are worth about +1.5 NRR points next year if they work as assumed; each comes with a control group so we know within two quarters.

## 1. What to tell the board about NRR

| | Sep 2026 | Jun 2026 | Sep 2025 |
|---|---|---|---|
| 12-month NRR | **103.5%** | 107.0% | 104.8% |
| 12-month NRR without the one-off rollout | 101.0% | 104.1% | — |
| 12-month GRR | 84.6% | — | 85.9% |
| Last quarter, annualised (early warning) | 92.9% (Q3) | 108.4% (Q2) | 103.6% (Q3 2025) |

Suggested footnote: *NRR = today's recurring revenue from customers who were paying 12 months ago, divided by what they paid then. It includes their upgrades, extra seats, downgrades and cancellations, and excludes new customers. GRR is the same but gives no credit for growth.* Definitions are in `docs/02_metric_definitions.md`.

## 2. Why expansion slowed

Q3 expansion was **$63.7K** of new monthly revenue from existing customers, down 33% from $94.9K in Q2 — $26.4K of that $31.2K drop is the one-off rollout in Q2 — and up from $49.6K a year ago, because the customer base is much bigger. The rate tells the real story. Compared with the same quarter last year, so the season is the same:

| What changed (monthly revenue) | |
|---|---|
| A bigger customer base, same expansion rate | +$42.3K |
| Technology customers | **−$22.3K** |
| Basic → Premium upgrades outside Technology | **−$15.6K** |
| Extra seats outside Technology | +$9.4K |
| Other | +$0.3K |

- **Technology:** their expansion fell from 1.5% to 0.3% of their revenue per quarter and their contraction (seat cuts and downgrades) rose from 0.9% to 5.7%; their 12-month NRR is now 95.5% (109.5% a year ago). This matches what CSMs hear about hiring freezes and layoffs.
- **Basic upgrades:** since mid-April the monthly share of Basic customers upgrading fell from 1.5% to 0.7%. It fell in every channel and customer size. The packaging change is the leading explanation — the drop lines up with the month it shipped — but upgrades were already drifting down before April, so the size of its effect is a range, not a number. The lower churn the change was meant to buy is too small to see yet.
- **Not the cause:** the growing share of Paid Social signups explains only about 4% of the upgrade drop. A summer slowdown cannot be confirmed or ruled out with two years of history.

## 3. Who is quietly downgrading

- **324 accounts** (307 cutting on their own, 17 kept by CS on a smaller plan after asking to cancel) pay at least 20% less than six months ago without cancelling. Up from 162 in June and 89 a year ago.
- **How:** mostly seat cuts at monthly billing dates (240 of the 324, typically a quarter of their seats).
- **Who:** Technology companies are 60% of them, against 23% of other paying customers.
- **What happens next:** they are no more likely to leave than similar customers (12% vs 12% within six months) — the damage is mostly done. The useful early signal is **low seat use**: accounts using less than half their seats over a quarter go on to shrink or leave about 2.3 times as often as the average account, typically four months later.

## 4. Where CS should focus

`docs/account_health_list.csv` lists 989 accounts at risk, each with the reason, the evidence and the action:

- **CSM reviews (120 accounts):** the 120 CSM-covered accounts that matter most — renewals in the next 120 days first (62 of them), then the most revenue at stake. They hold **66% of the revenue at stake** on the whole list, above the 60% threshold, so the plan is CSM-led reviews for them.
- **Renewal desk (71 accounts on the list):** Technology customers renewing an annual contract in the next six months (190 in all, counting the ones not flagged).
- **Digital program (798 accounts):** in-app and email nudges about unused seats, plus a renewal email series 60 days out.

## 5. What we propose, and what it is worth

| # | Action | Owner | Next year's NRR | How we will know |
|---|---|---|---|---|
| P1 | CSM renewal reviews for the top 120 at-risk accounts | Head of CS | +0.56 pts | 1 in 5 accounts left as a control group |
| P7 | Recover more failed card payments (retries, card updater) | Billing / RevOps | +0.30 pts | A/B test of the retry schedule |
| P3 | Flexible seat bands for Technology renewals | Sales + CS | +0.26 pts | Offer in alternate renewal months |
| P2 | Digital program for the rest of the risk list | Lifecycle Marketing | +0.16 pts | 50/50 split |
| P5 | Test a capped Basic package before deciding to revert | Product / Pricing | +0.12 pts (about +0.7 pts a year once rolled out) | 50/50 on new signups |
| P4 | Premium trial for the Basic accounts most likely to upgrade | Sales-assist | +0.09 pts | 30% control group |
| P6 | Better onboarding and an annual offer for Paid Social signups | Growth Marketing | +0.04 pts (most value lands after next year) | Alternate signup weeks |
| | **Total** | | **≈ +1.5 pts (range +0.8 to +2.4)** | |

Each figure = revenue on the target list today × how much similar accounts lost (or added) in the past × the share of that we expect the action to change. The last part is an assumption — none of these has been tried here — which is why every action has a control group and a read-out date (`notebooks/05_recommendations.ipynb`). Each account counts in one action only.

## 6. Decisions needed

| Decision | Who | By when |
|---|---|---|
| Re-forecast Q4 and FY2027 expansion at the Q3 rate, not the Q2 rate | VP Revenue + Finance | before the board pack (12 Nov) |
| Staff the 120 CSM reviews and agree the 1-in-5 control group | Head of CS | 20 Nov, ahead of the Dec–Mar renewal window |
| Approve seat bands for Technology renewals (one renewal cycle) | VP Revenue + Finance | Nov 2026 |
| Run the packaging test instead of a straight revert | Pricing council | Dec 2026 |

## How much to trust this

- **Synthetic data.** Real billing data with this level of detail is not public, so the company, its customers and its history were simulated from documented rules (`docs/data_model.md`); several business events were built in on purpose, and the analysis found all of them. The numbers describe a fictional company; the definitions, method and actions are what carry over.
- **Before / after, not experiments.** The packaging and Technology findings line up in time with known events; they are the best explanation, not proof. The proposed tests are how we get proof.
- **Effects of the actions are assumptions** with wide ranges; plan on the low end until the control groups report.
