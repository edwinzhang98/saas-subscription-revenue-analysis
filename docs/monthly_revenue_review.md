# Q3 2026 close revenue review (September month-end)

**Prepared by:** Revenue Analytics (RevOps) · **Date:** 2026-11-03 · **Audience:** VP of Revenue, Head of Customer Success, Finance,
Product, Growth Marketing · **Data:** billing and product usage as of 2026-09-30 (synthetic dataset) · **October flash** follows the 7 Nov close.
Definitions: `docs/02_metric_definitions.md` v1.2 · data-quality status: all checks pass (`docs/03_data_quality_checks.md`).

## 1. Headline

| Metric | Sep 2026 | Aug 2026 | Sep 2025 | Note |
|---|---|---|---|---|
| MRR | $2.06M | $1.98M | $1.17M | +76% YoY |
| ARR (MRR × 12) | $24.75M | $23.75M | $14.09M |  |
| Paying customers | 4,117 | 3,927 | 2,377 |  |
| ARPA | $501 | $504 | $494 |  |
| Net new MRR in month | $83.6K | $67.1K | $68.5K |  |
| 12-month NRR | 103.5% | 104.9% | 104.8% | excl. one-off: 101.0% |
| 12-month GRR | 84.6% | 85.4% | 85.9% |  |
| Logo retention (12 months) | 81.9% | 82.3% | 81.1% |  |
| Run-rate NRR (quarter, annualised) | 92.9% |  | 103.6% | Q3 2026 vs Q3 2025 |

**In one paragraph.** MRR grew 9.9% in the quarter to $2.1M,
still carried by new logos. The existing base weakened: expansion fell to 3.4% of starting MRR (Q3 2025:
4.9%), contraction rose to 2.6% (1.4%).
12-month NRR is 103.5%, down 3.5 pts from June; excluding the
Lakewave Payments rollout it is 101.0%.

## 2. MRR waterfall

| Movement | September 2026 | Q3 2026 | Q3 2026 rate (of start) | Q3 2025 rate |
|---|---|---|---|---|
| Start MRR | $1.98M | $1.88M |  |  |
| + New | $102.7K | $220.6K | 11.8% | 15.2% |
| + Expansion | $27.8K | $63.7K | 3.4% | 4.9% |
| + Reactivation | $869 | $2.3K | 0.1% | 0.1% |
| − Contraction | $22.5K | $49.1K | 2.6% | 1.4% |
| − Churn | $25.2K | $52.0K | 2.8% | 2.9% |
| End MRR | $2.06M | $2.06M |  |  |

Chart: `reports/figures/mrr_waterfall.png` (monthly movements since Oct 2024) and the dashboard's *MRR Waterfall* page.

## 3. Retention

**12-month NRR / GRR by quarter-end**

| Quarter end | Base customers | NRR | GRR | Logo retention | Note |
|---|---|---|---|---|---|
| Jun 2025 | 861 | 105.7% | 87.4% | 80.7% | base < 1,000 customers: greyed in charts, read with care (scope §3) |
| Sep 2025 | 1,118 | 104.8% | 85.9% | 81.1% |  |
| Dec 2025 | 1,405 | 105.2% | 85.9% | 81.9% |  |
| Mar 2026 | 1,738 | 103.1% | 84.8% | 80.9% |  |
| Jun 2026 | 2,054 | 107.0% | 85.8% | 82.4% |  |
| Sep 2026 | 2,377 | 103.5% | 84.6% | 81.9% |  |

**By segment and industry (12-month NRR, base = customers paying a year earlier)**

| Cut | Group | Base customers | NRR Sep 2026 | NRR Sep 2025 | GRR Sep 2026 |
|---|---|---|---|---|---|
| industry | Technology | 617 | 95.5% | 109.5% | 81.8% |
| industry | Financial Services | 260 | 122.5% | 115.5% | 89.3% |
| industry | Healthcare | 233 | 108.5% | 112.3% | 85.7% |
| industry | Manufacturing | 238 | 108.8% | 101.8% | 90.2% |
| industry | Professional Services | 358 | 102.0% | 99.8% | 82.7% |
| industry | Retail & E-commerce | 300 | 98.4% | 95.5% | 83.9% |
| industry | Media & Marketing | 216 | 91.4% | 101.0% | 77.0% |
| industry | Education & Non-profit | 139 | 95.6% | 86.5% | 84.5% |
| industry | Unknown | 16 | 84.7% | 57.6% | 81.1% |
| segment | Mid-Market | 734 | 106.3% | 108.1% | 87.3% |
| segment | Enterprise | 112 | 110.6% | 107.5% | 88.8% |
| segment | SMB | 1,531 | 84.9% | 91.4% | 71.1% |

**Cohort NRR (quarterly cohorts; blank = not yet observed for the whole cohort)**

| Cohort (first paid) | NRR at month 3 | NRR at month 6 | NRR at month 12 | NRR at month 24 |
|---|---|---|---|---|
| 2023-Q4 | 100.6% | 105.1% | 100.9% | 115.5% |
| 2024-Q1 | 99.8% | 102.9% | 108.8% | 116.7% |
| 2024-Q2 | 100.9% | 105.0% | 104.2% | 126.0% |
| 2024-Q3 | 101.3% | 102.1% | 96.3% | 91.4% |
| 2024-Q4 | 99.6% | 101.8% | 92.6% |  |
| 2025-Q1 | 100.3% | 101.8% | 98.5% |  |
| 2025-Q2 | 101.7% | 104.4% | 101.9% |  |
| 2025-Q3 | 97.9% | 99.0% | 85.7% |  |
| 2025-Q4 | 99.9% | 103.2% |  |  |
| 2026-Q1 | 97.4% | 94.6% |  |  |
| 2026-Q2 | 98.2% |  |  |  |

Every cohort dips at month 12 (first annual renewal). Cohorts acquired after the Paid Social launch (Q4 2025 onward) start weaker.

## 4. Expansion: what changed in Q3

| Step | vs Q2 2026 | vs Q3 2025 |
|---|---|---|
| Starting point (expansion MRR in the earlier quarter) | $94.9K | $49.6K |
| One-off rollout (Lakewave Payments, May) | −$26.4K | — |
| Bigger base (same rate on more MRR) | $11.0K | $42.3K |
| Technology accounts | −$15.7K | −$22.3K |
| Non-Tech seat expansion | −$2.6K | $9.4K |
| Non-Tech plan upgrades | $2.4K | −$15.6K |
| Other | $221 | $336 |
| **Q3 2026 expansion MRR** | **$63.7K** | **$63.7K** |

Decision trigger (scope §3): Q3 2026 expansion rate excluding the one-off is 3.4% vs 4.9%
in Q3 2025 — −1.5 pts, beyond the 1.0-pt threshold → **re-forecast Q4 expansion and assign owners to the structural drivers.**

## 5. Top movers, Q3 2026

**Expansion**

| Account | Segment | Industry | Owner | MRR before → after | Movement |
|---|---|---|---|---|---|
| Redframe Wealth | Mid-Market | Financial Services | CSM-08 | $2.9K → $5.5K | $2.5K |
| Primespark Insurance | Mid-Market | Financial Services | CSM-09 | $8.0K → $9.2K | $1.2K |
| Coppergate Studios | SMB | Media & Marketing |  | $720 → $1.6K | $1.1K |
| Falconrock Care | Enterprise | Healthcare | CSM-02 | $5.9K → $7.0K | $1.1K |
| Truelight Components | Mid-Market | Manufacturing | CSM-08 | $5.9K → $6.8K | $935 |

**Contraction**

| Account | Segment | Industry | Owner | MRR before → after | Movement |
|---|---|---|---|---|---|
| Peakcrest Health | Enterprise | Healthcare | CSM-04 | $5.6K → $3.3K | $2.2K |
| Meridianridge Legal | Enterprise | Professional Services | CSM-04 | $4.7K → $3.0K | $1.6K |
| Brightview Schools | Enterprise | Education & Non-profit | CSM-01 | $2.0K → $476 | $1.5K |
| Brightgrid Labs | Enterprise | Technology | CSM-02 | $3.6K → $2.0K | $1.5K |
| Vividwood Academy | Enterprise | Education & Non-profit | CSM-02 | $4.0K → $2.6K | $1.4K |

**Churn**

| Account | Segment | Industry | Owner | MRR before → after | Movement |
|---|---|---|---|---|---|
| Harborview Therapeutics | Enterprise | Healthcare | CSM-02 | $4.1K → $0 | $4.1K |
| Lakefield Studios | Enterprise | Media & Marketing | CSM-02 | $4.0K → $0 | $4.0K |
| Summitline Associates | Mid-Market | Professional Services | CSM-08 | $2.6K → $0 | $2.6K |
| Oakloop Advisory | Mid-Market | Professional Services | CSM-05 | $1.6K → $0 | $1.6K |
| Lumenhub Consulting | Mid-Market | Professional Services | CSM-09 | $1.6K → $0 | $1.6K |

## 6. Who works which accounts (`docs/account_health_list.csv`)

| Queue | Accounts | ARR | ARR at stake |
|---|---|---|---|
| csm | 120 | $2.4M | $1.1M |
| renewal_desk | 71 | $130.0K | $70.1K |
| digital | 798 | $1.5M | $519.2K |
| sales_assist | 100 | $395.4K | $27.8K |
| in_app | 200 | $561.5K | $24.2K |

`csm` = the first 120 CSM-covered risk accounts (CS capacity; annual renewals within 120 days first, then ARR at stake) — play P1;
`renewal_desk` = Technology annual renewals within 6 months on the risk list (P3); `digital` = the rest of the risk list (P2);
`sales_assist` = the top 100 upgrade opportunities (P4); `in_app` = the next 200 (in-app prompt only). ARR at stake: risk = ARR × the
flag's historical hit rate; expansion = expected upgrade ARR. Reason codes: `docs/02_metric_definitions.md §15`.

**CSM queue, first 10**

| Account | Segment | Industry | Owner | Plan | ARR | ARR at stake | Renewal / next bill | Main reason | Play |
|---|---|---|---|---|---|---|---|---|---|
| Boldgate Supply | Enterprise | Retail & E-commerce | CSM-01 | enterprise | $55.9K | $30.2K | 2027-01-26 | UTIL_BELOW_50 | Pre-renewal utilization review 60 days out: activate idle seats or agree the right size before renewal |
| Stoneforge Commerce | Enterprise | Retail & E-commerce | CSM-02 | enterprise | $42.5K | $22.9K | 2026-12-22 | UTIL_BELOW_50 | Pre-renewal utilization review 60 days out: activate idle seats or agree the right size before renewal |
| Boldpath Institute | Enterprise | Education & Non-profit | CSM-04 | premium | $36.6K | $19.8K | 2026-11-27 | UTIL_BELOW_50 | Pre-renewal utilization review 60 days out: activate idle seats or agree the right size before renewal |
| Vividbridge Software | Enterprise | Technology | CSM-03 | premium | $32.3K | $17.4K | 2026-11-11 | UTIL_BELOW_50 | Urgent — renews in 42 days: Pre-renewal utilization review now: activate idle seats or agree the right size before renewal |
| Junipervale Software | Mid-Market | Technology | CSM-08 | enterprise | $30.0K | $16.2K | 2026-10-10 | UTIL_BELOW_50 | Urgent — renews in 10 days: Pre-renewal utilization review now: activate idle seats or agree the right size before renewal |
| Redforge Payments | Enterprise | Financial Services | CSM-02 | premium | $29.9K | $16.1K | 2026-12-20 | UTIL_BELOW_50 | Pre-renewal utilization review 60 days out: activate idle seats or agree the right size before renewal |
| Aspengrid Software | Enterprise | Technology | CSM-03 | premium | $27.2K | $14.7K | 2026-12-26 | UTIL_BELOW_50 | Pre-renewal utilization review 60 days out: activate idle seats or agree the right size before renewal |
| Junipershift Associates | Mid-Market | Professional Services | CSM-06 | enterprise | $25.1K | $13.5K | 2027-01-03 | UTIL_BELOW_50 | Pre-renewal utilization review 60 days out: activate idle seats or agree the right size before renewal |
| Goldenstream Bio | Enterprise | Healthcare | CSM-04 | premium | $23.2K | $12.5K | 2026-11-13 | UTIL_BELOW_50 | Urgent — renews in 44 days: Pre-renewal utilization review now: activate idle seats or agree the right size before renewal |
| Nextlight Manufacturing | Enterprise | Manufacturing | CSM-04 | premium | $22.5K | $12.1K | 2027-01-03 | UTIL_BELOW_50 | Pre-renewal utilization review 60 days out: activate idle seats or agree the right size before renewal |

## 7. Upgrade opportunities (sales-assist queue, first 10)

| Account | Segment | Industry | Owner | Plan | ARR | ARR at stake | Renewal / next bill | Main reason | Play |
|---|---|---|---|---|---|---|---|---|---|
| Willowspark Group | Mid-Market | Professional Services | CSM-08 | basic | $3.2K | $448 | 2026-12-26 | CORE_ACTIONS_TOP_DECILE | 14-day Premium trial + sales-assist outreach (P4) |
| Aspenview Materials | Mid-Market | Manufacturing | CSM-09 | basic | $5.2K | $375 | 2026-12-17 | CORE_ACTIONS_ABOVE_MEDIAN | 14-day Premium trial + sales-assist outreach (P4) |
| Harborrock Credit Union | SMB | Financial Services |  | basic | $3.3K | $326 | 2026-12-07 | CORE_ACTIONS_TOP_DECILE | 14-day Premium trial + sales-assist outreach (P4) |
| Juniperstack Schools | Mid-Market | Education & Non-profit | CSM-09 | basic | $6.5K | $319 | 2026-10-18 | CORE_ACTIONS_TOP_DECILE | 14-day Premium trial + sales-assist outreach (P4) |
| Cedarbridge Associates | Mid-Market | Professional Services | CSM-06 | basic | $2.4K | $266 | 2026-11-04 | AUTOMATION_TOP_DECILE | 14-day Premium trial + sales-assist outreach (P4) |
| Starcraft Manufacturing | Mid-Market | Manufacturing | CSM-09 | basic | $6.8K | $253 | 2026-11-17 | CORE_ACTIONS_TOP_DECILE | 14-day Premium trial + sales-assist outreach (P4) |
| Falconloop Brands | Mid-Market | Retail & E-commerce | CSM-10 | basic | $2.6K | $199 | 2026-10-22 | AUTOMATION_ABOVE_MEDIAN | 14-day Premium trial + sales-assist outreach (P4) |
| Summitview Schools | Mid-Market | Education & Non-profit | CSM-08 | basic | $2.7K | $194 | 2026-12-29 | AUTOMATION_ABOVE_MEDIAN | 14-day Premium trial + sales-assist outreach (P4) |
| Vividbay Institute | Mid-Market | Education & Non-profit | CSM-08 | basic | $4.2K | $189 | 2026-10-29 | AUTOMATION_ABOVE_MEDIAN | 14-day Premium trial + sales-assist outreach (P4) |
| Coralvale Learning | Mid-Market | Education & Non-profit | CSM-08 | basic | $4.8K | $176 | 2026-12-30 | CORE_ACTIONS_ABOVE_MEDIAN | 14-day Premium trial + sales-assist outreach (P4) |

## 8. Focus for October / November

1. **Board pack (VP Revenue, by 12 Nov):** report 12-month NRR 103.5% with the definition footnote, NRR excluding the one-off
   (101.0%) and the Q3 run-rate (92.9%) as the leading indicator.
2. **Re-forecast Q4 expansion** at the Q3 like-for-like rate (3.4% of starting MRR), not the Q2 rate.
3. **CS (P1):** start the renewal desk on the `csm` queue of the account list in priority order, with a 1-in-5 holdout.
4. **Sales + Finance (P3):** approve the seat-band offer for Technology renewals in the next 6 months.
5. **Product (P5):** bring the packaging test design to the December pricing council instead of a straight revert.
6. **October flash** after the 7 Nov close: watch whether Technology contraction continues at renewals and whether the expansion rate stays near Q3.

## Data notes

- Synthetic dataset; definitions v1.2. Internal / test accounts excluded (6). MRR excludes one-time fees, prorations and tax.
- Month-end snapshots: customers who start and cancel inside a month are not visible (counted in the data-quality checks).
- Numbers on this page are generated by `python run.py docs` from the warehouse; nothing is typed by hand.
