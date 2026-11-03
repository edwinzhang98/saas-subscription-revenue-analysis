# Q3 2026 revenue readout: NRR, the expansion slowdown, and where CS should focus

**To:** VP of Revenue · **cc:** Head of Customer Success, Finance, Product, Growth Marketing
**From:** Revenue Analytics (RevOps) · **Date:** 3 Nov 2026 · **Data:** billing and product usage to 30 Sep 2026 (synthetic dataset, see the note at the end)

## Bottom line

1. **For the board: 12-month NRR is 103.5%** (GRR 84.6%). One customer's company-wide rollout in May props it up: without it NRR is 101.0%. Q3 on its own, annualised, is 92.9%: if Q4 and Q1 look like Q3, the 12-month figure keeps falling.
2. **Expansion did slow, and the underlying part will not bounce back by itself.** Most of the dollar drop from Q2 is that one-off rollout, which inflated Q2. But like for like, expansion is 3.4% of starting revenue in Q3 vs 4.9% a year ago — past the 1-point line we agreed for a re-forecast — and that part is structural: Technology customers all but stopped adding seats and started cutting them, and Basic → Premium upgrades, already falling before April, fell again after the packaging change.
3. **324 paying customers are quietly paying less** — $1.07M of annual revenue still with us, $649K already gone. 60% of them are Technology companies.
4. **CS should work a list of 120 accounts** that holds 66% of the revenue at stake (above the 60% we agreed for CSM-led reviews), renewals first. Seven actions across CS, Sales, Product, Marketing and Billing are worth about +1.5 NRR points next year if they work as assumed. Each comes with a control group: the retention actions read out within two quarters, the packaging test by mid-2027.

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
| Plan upgrades outside Technology (mostly Basic → Premium) | **−$15.6K** |
| Extra seats outside Technology | +$9.4K |
| Other | +$0.3K |

- **Technology:** measured on their own revenue, their expansion fell from 6.2% to 1.2% a quarter and their contraction (seat cuts and downgrades) rose from 0.9% to 5.7%; their 12-month NRR is now 95.5% (109.5% a year ago). This matches what CSMs hear about hiring freezes and layoffs, though the data shows the seat cuts, not their cause.
- **Basic upgrades:** the monthly share of Basic customers upgrading fell in two steps — from about 2% in Q3 2025 to 1.5% in January–March 2026, before anything changed, and to 0.8% after the April packaging change. It fell in every channel and customer size. The second drop lines up with the month the change shipped, but because upgrades were already drifting down, 0.4 to 0.8 points of the fall coincide with the change, depending on how much of the earlier drift would have continued — a range, not a measured effect. The lower churn the change was meant to buy is too small to see yet.
- **Not the cause:** the growing share of Paid Social signups explains only about 4% of the upgrade drop. A summer slowdown cannot be confirmed or ruled out with two years of history.

## 3. Who is quietly downgrading

- **324 accounts** (307 cutting on their own, 17 kept by CS on a smaller plan after asking to cancel) pay at least 20% less than they did at their peak in the last six months, without cancelling. Up from 162 in June and 89 a year ago.
- **How:** mostly seat cuts at monthly billing dates (240 of the 324, typically a quarter of their seats).
- **Who:** Technology companies are 60% of them, against 23% of other paying customers.
- **What happens next:** they are no more likely to leave than similar customers (12% vs 12% within six months) — the damage is mostly done. The useful early signal is **low seat use**: accounts using less than half their seats over a quarter go on to shrink or leave about 2.3 times as often as the average account, typically three months ahead for monthly-billed customers and six for annual ones (83% of the contractions the rule caught were flagged at least two month-ends in advance).

## 4. Where CS should focus

`docs/account_health_list.csv` lists the accounts to work, each with the reason, the evidence and the action:

- **CSM reviews (120 accounts):** the 120 CSM-covered accounts that matter most — renewals in under 45 days first, soonest first (17 of them), then renewals in the next 120 days (45 more), then the most revenue at stake. They hold **66% of the revenue at stake** on the whole list, above the 60% threshold, so the plan is CSM-led reviews for them.
- **Renewal desk (190 accounts):** every Technology customer renewing an annual contract in the next six months that is not already with a CSM — 71 flagged by the risk rules, 119 from the renewal calendar — for the seat-band offer, soonest renewals first.
- **Digital program (798 accounts):** in-app and email nudges about unused seats, plus a renewal email series 60 days out.

*Revenue at stake* ranks the list: each account's annual revenue × how often accounts with the same warning sign went on to shrink or leave. It is a priority weight, not a forecast of the loss; the sizes in section 5 use the measured share of revenue lost instead. About a fifth of the CSM list (23 of the 120) shows rising usage on under-used seats — weekly active users up 20% or more while fewer than half the seats are used; those are right-size conversations (confirm team size and seat count, no discount), not saves.

## 5. What we propose, and what it is worth

| # | Action | Owner | Next year's NRR | How we will know | Main risk |
|---|---|---|---|---|---|
| P1 | CSM renewal reviews for the top 120 at-risk accounts | Head of CS | +0.56 pts | 1 in 5 accounts left as a control group | CSM time; right-sizing lowers revenue now to keep the customer |
| P7 | Recover more failed card payments (retries, card updater) | Billing / RevOps | +0.30 pts | A/B test of the retry schedule | Low; card-updater fees |
| P3 | Flexible seat bands for Technology renewals | Sales + CS | +0.26 pts | Offer in alternate renewal months | Gives back seats some customers would have kept; one renewal cycle only |
| P2 | Digital program for the rest of the risk list | Lifecycle Marketing | +0.16 pts | 50/50 split | Emails move behaviour only a little |
| P5 | Test a capped Basic package before deciding to revert | Product / Pricing | +0.12 pts (about +0.7 pts a year once rolled out) | 50/50 on new signups | Loses the conversion gain the change was meant to buy |
| P4 | Premium trial for the Basic accounts most likely to upgrade | Sales-assist | +0.07 pts | 30% control group | Pulls forward upgrades that would have happened anyway |
| P6 | Better onboarding and an annual offer for Paid Social signups | Growth Marketing | +0.04 pts (most value lands after next year) | Alternate signup weeks | Fewer signups; needs acquisition cost data |
| | **Total** | | **≈ +1.5 pts (range +0.7 to +2.4)** | | |

Each figure = revenue on the target list today × how much similar accounts lost (or added) in the past × the share of that we expect the action to change. The last part is an assumption — none of these has been tried here — which is why every action has a control group and a read-out date (`notebooks/05_recommendations.ipynb`). Each account counts in one action only.

## 6. Decisions needed

| Decision | Who | By when |
|---|---|---|
| Re-forecast Q4 and FY2027 expansion at the Q3 rate, not the Q2 rate | VP Revenue + Finance | before the board pack (12 Nov) |
| Staff the 120 CSM reviews and agree the 1-in-5 control group | Head of CS | 20 Nov, ahead of the Dec–Mar renewal window |
| Approve seat bands for Technology renewals (one renewal cycle) | VP Revenue + Finance | Nov 2026 |
| Run the packaging test instead of a straight revert | Pricing council | Dec 2026 |

## How much to trust this

- **Synthetic data.** Real billing data with this level of detail is not public, so the company, its customers and its history were simulated from documented rules (`docs/data_model.md`); six business events were built in on purpose; the analysis recovered four of them clearly and a fifth (stronger early-adopter cohorts) only weakly, and the sixth (a small summer seasonality) cannot be separated from noise with two years of history, as expected (`docs/03_data_quality_checks.md`). The numbers describe a fictional company; the definitions, method and actions are what carry over.
- **Before / after, not experiments.** The packaging and Technology findings line up in time with known events; they are the best explanation, not proof. The proposed tests are how we get proof.
- **Effects of the actions are assumptions** with wide ranges; plan on the low end until the control groups report.
