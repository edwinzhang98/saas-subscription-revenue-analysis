# Data model and synthetic data generation

> **This dataset is synthetic.** Public, row-level SaaS billing data with plan changes, seat changes and product usage does not exist (the commonly used IBM Telco churn file has one row per customer, no upgrade/downgrade events and no usage). So I designed the data model a billing system (Stripe / Chargebee style) plus a product-analytics export would produce, and generated 36 months of history from documented business rules. Every rule and parameter is below; the code is `data_gen/generate.py`, parameters are in `data_gen/params.yaml` (source of truth if this page and the file ever disagree; calibration changes are logged in §9).

## 1. Business being modeled

A fictional B2B SaaS company selling a **team workflow-automation platform**, priced **per seat** in three tiers. General availability (GA) was **2 Oct 2023**; data is complete through **30 Sep 2026** (the Q3 2026 close). Customers are companies ("accounts"); each account has at most one live subscription at a time.

| Plan | Tier | Monthly price / seat | Annual price / seat / year (= per month) | Seats | What it adds |
|---|---|---|---|---|---|
| Basic | 1 | $15 | $144 ($12.00) | 1 – 50 | Core workflows, 5 integrations, simple automations |
| Premium | 2 | $35 | $336 ($28.00) | 3+ | Advanced automations*, analytics dashboards, API, unlimited integrations |
| Enterprise | 3 | annual only | $600 list ($50.00), negotiated discount 10–30% | 50+ | SSO / SCIM, audit log, sandbox, named CSM, SLA |

\* From 15 Apr 2026, advanced automations are included in Basic (packaging change, §6).

**Billing and change policies (as in most self-serve + sales-assisted SaaS):**
- Monthly plans bill on the subscription anniversary day; annual plans bill 12 months upfront. Annual is ~17–20% cheaper per seat.
- **Increases are immediate** (seat adds, upgrades) and invoiced as a **prorated** line for the rest of the current period; annual seat adds are co-termed to the annual term.
- **Decreases take effect at the end of the current period** (next monthly billing date, or the annual renewal date). No mid-term refunds.
- Monthly → annual switches take effect at the next billing date and start a new 12-month term.
- Cancellations take effect at the end of the paid period (voluntary) or after a 14-day dunning window (failed card payment).
- Enterprise and sales-led Mid-Market deals can carry a one-time onboarding fee (not recurring revenue).

## 2. Entity-relationship diagram

```mermaid
erDiagram
    customers ||--o{ subscriptions : "has (one live at a time)"
    plans ||--o{ subscriptions : "initial plan"
    subscriptions ||--o{ plan_changes : "changed by"
    plans ||--o{ plan_changes : "from / to plan"
    subscriptions ||--o{ invoices : "billed as"
    customers ||--o{ invoices : "billed to"
    customers ||--o{ usage_events : "generates (weekly)"

    customers {
        string customer_id PK
        string company_name
        string email_domain
        string industry "nullable"
        int employee_count "nullable"
        string segment "SMB / Mid-Market / Enterprise"
        string region
        string acquisition_channel
        date created_at
        bool is_design_partner
        string cs_coverage
        string csm_owner "nullable"
    }
    plans {
        string plan_id PK
        string plan_name
        int tier_rank
        float monthly_price_per_seat "nullable"
        float annual_price_per_seat
        int min_seats
        int max_seats "nullable"
    }
    subscriptions {
        string subscription_id PK
        string customer_id FK
        date start_date
        date end_date "exclusive; null = live"
        string status
        date cancel_requested_date "nullable"
        string cancel_reason "nullable"
        string initial_plan_id FK
        int initial_seats
        string initial_billing_period
        float initial_unit_price
        float initial_discount_pct
        string payment_method
    }
    plan_changes {
        string change_id PK
        string subscription_id FK
        string customer_id FK
        date requested_date
        date effective_date
        string change_type
        string change_source
        string from_plan_id
        string to_plan_id
        int from_seats
        int to_seats
        string from_billing_period
        string to_billing_period
        float from_unit_price
        float to_unit_price
        float from_discount_pct
        float to_discount_pct
    }
    invoices {
        string invoice_line_id PK
        string invoice_id
        string customer_id FK
        string subscription_id FK
        date invoice_date
        string line_type
        date service_period_start "nullable"
        date service_period_end "exclusive; nullable"
        float amount_usd
        float tax_usd
        string status
        date paid_date "nullable"
        int payment_attempts
    }
    usage_events {
        string customer_id FK
        date week_start
        string event_name
        int event_count
        int unique_users
    }
```

## 3. Tables and fields

Grain, keys and expected volumes are checked in `docs/03_data_quality_checks.md`.

### `plans` — plan catalog (3 rows)
| Field | Type | Description |
|---|---|---|
| `plan_id` | string, PK | `basic`, `premium`, `enterprise` |
| `plan_name` | string | Display name |
| `tier_rank` | int | 1 = Basic, 2 = Premium, 3 = Enterprise. A change to a higher rank is an upgrade. |
| `monthly_price_per_seat` | float | List price per seat per month (USD); null for Enterprise (annual only) |
| `annual_price_per_seat` | float | List price per seat per year (USD) |
| `min_seats` / `max_seats` | int | Seat limits; `max_seats` null = unlimited |
| `included_features` | string | Short feature summary |

### `customers` — one row per account (≈ 6,000)
| Field | Type | Description |
|---|---|---|
| `customer_id` | string, PK | `CUS-000001`, assigned in signup order |
| `company_name` | string | Generated name (fictional) |
| `email_domain` | string | Billing contact domain (`*.example`). Internal / test accounts use `ourcompany.example`. |
| `industry` | string, nullable | 8 industries; null for ~2% of self-serve SMB signups (not asked at signup) |
| `employee_count` | int, nullable | Company size; null for ~3% |
| `segment` | string | `SMB` (≤ 100 employees), `Mid-Market` (101–1,000), `Enterprise` (1,000+). Set by Sales Ops at signup; static. |
| `region` | string | North America / EMEA / APAC |
| `acquisition_channel` | string | Organic, Paid Search, Paid Social, Partner, Outbound Sales, Events |
| `created_at` | date | Account created (trial start for self-serve; first meeting for sales-led) |
| `is_design_partner` | bool | Private-beta design partner converted at GA |
| `cs_coverage` | string | `digital` (SMB, tech-touch), `pooled_csm` (Mid-Market), `named_csm` (Enterprise) |
| `csm_owner` | string, nullable | CSM book code (`CSM-01`…) for pooled / named coverage |

### `subscriptions` — one row per subscription (≈ 6,300; a win-back creates a new subscription)
| Field | Type | Description |
|---|---|---|
| `subscription_id` | string, PK | `SUB-000001` |
| `customer_id` | string, FK | Owning account |
| `start_date` | date | First day of service (first paid day) |
| `end_date` | date, nullable | **Exclusive** end of service: the subscription is live on days `[start_date, end_date)`. Null = live at cutoff. |
| `status` | string | `active` or `canceled` as of 2026-09-30 |
| `cancel_requested_date` | date, nullable | When the customer asked to cancel (voluntary) or dunning started (payment failure) |
| `cancel_reason` | string, nullable | `voluntary` or `payment_failed` |
| `initial_plan_id`, `initial_seats`, `initial_billing_period`, `initial_unit_price`, `initial_discount_pct` | | Terms at start. `unit_price` is per seat **per billing period** (monthly: per month; annual: per year). Later terms come from `plan_changes`. |
| `payment_method` | string | `card` (self-serve monthly) or `invoice` (annual / sales-led) |

### `plan_changes` — one row per change to a live subscription (≈ 30,000)
| Field | Type | Description |
|---|---|---|
| `change_id` | string, PK | `CHG-0000001` |
| `subscription_id`, `customer_id` | string, FK | |
| `requested_date` | date | When the change was requested |
| `effective_date` | date | When new terms apply (same day for increases; end of period for decreases) |
| `change_type` | string | Primary type, by precedence: `upgrade` / `downgrade` (tier changed) → `seat_increase` / `seat_decrease` → `billing_period_change` → `discount_change` |
| `change_source` | string | `self_serve`, `sales`, `customer_success`, `renewal`, `system` |
| `from_*` / `to_*` | | Plan, seats, billing period, unit price, discount before and after. A single row can change several attributes (e.g., a renewal that downgrades and removes seats). |

Renewals with unchanged terms produce no row. MRR is **not** stored anywhere in the source data; it is derived in `sql/` from these terms (see `02_metric_definitions.md`).

### `invoices` — one row per invoice line (≈ 130,000)
| Field | Type | Description |
|---|---|---|
| `invoice_line_id` | string, PK | `INV-0000001-1` |
| `invoice_id` | string | Invoice header id |
| `customer_id`, `subscription_id` | string, FK | |
| `invoice_date` | date | Issue date |
| `line_type` | string | `recurring` (period charge), `proration` (mid-period increase), `one_time` (onboarding fee) |
| `service_period_start` / `service_period_end` | date, nullable | Service period covered (`end` exclusive); null for one-time lines |
| `amount_usd` | float | Pre-tax, after discount |
| `tax_usd` | float | Sales tax / VAT (excluded from all revenue metrics) |
| `status` | string | `paid`, `uncollectible` (dunning failed), `void` (issued in error) |
| `paid_date` | date, nullable | |
| `payment_attempts` | int | > 1 when a card retry was needed |

### `usage_events` — account × week × event (≈ 3 million rows)
Weekly aggregates exported from the product-analytics tool. One row per (`customer_id`, `week_start`, `event_name`) with `event_count` > 0.

| `event_name` | Meaning | `unique_users` |
|---|---|---|
| `session` | Product sessions | **Weekly active users (WAU)** — users with ≥ 1 session |
| `core_action` | Work items created / updated | Users doing it |
| `automation_run` | Automation executions | Users who built / triggered |
| `report_view` | Analytics dashboard views | Users viewing |
| `api_call` | API requests (Premium+) | 0 (service accounts) |
| `integration_sync` | Integration sync jobs | 0 |
| `user_invite` | Invitations sent to new teammates | Admins sending |
| `premium_feature_gate` | A Basic user hit a Premium-only feature (paywall) | Users who hit it |

## 4. How the data is generated — overview

1. **Acquire accounts** month by month (Poisson draws per segment × channel, with growth, seasonality and quarter-end effects).
2. **Simulate each account week by week** from signup to the cutoff, independently, with its own random stream (`numpy.random.SeedSequence(seed).spawn`) so results do not depend on processing order. Each week updates latent health and team size, produces usage, and may trigger events; billing-boundary weeks apply renewals, decreases and churn decisions.
3. **Write source tables** (subscriptions, plan changes, invoices, usage).
4. **Inject data-quality issues** that a real pipeline has (§7), so the data-quality checks have something to catch.
5. **Run sanity checks** against the targets in §8.

Fixed seed: `config.yaml → seed` (20261103). Two runs produce identical files.

## 5. Generation rules

### 5.1 Acquisition (new accounts)
- Expected new accounts in month *m* (0 = Oct 2023): `100 × 1.022^m × season(month) × segment share × channel share`, Poisson-drawn per cell; sales-led channels (Outbound Sales, Partner, Events) get ×1.30 in quarter-end months and ×0.85 in quarter-start months.
- New-logo seasonality: Jan 1.00, Feb 1.00, Mar 1.08, Apr 0.98, May 1.00, Jun 1.05, Jul 0.85, Aug 0.85, Sep 1.05, Oct 1.05, Nov 1.05, Dec 0.95.
- Segment share: SMB 68%, Mid-Market 26%, Enterprise 6%.
- Channel share within segment:

| Channel | SMB | Mid-Market | Enterprise |
|---|---|---|---|
| Organic | 40% | 20% | 5% |
| Paid Search | 30% | 18% | 5% |
| Paid Social | 10% | 4% | 0% |
| Partner | 10% | 18% | 25% |
| Outbound Sales | 3% | 28% | 50% |
| Events | 7% | 12% | 15% |

- **Design partners:** +150 accounts in the first two weeks of Oct 2023 (60% SMB, 35% Mid-Market, 5% Enterprise; Organic; higher product fit).
- **Paid Social program:** extra SMB Paid Social accounts of +15 (Sep 2025), +25 (Oct 2025), then +35 per month.
- Industry mix (SMB / Mid-Market / Enterprise, %): Technology 28/26/22, Professional Services 16/15/10, Financial Services 10/12/18, Retail & E-commerce 13/12/10, Healthcare 9/10/14, Manufacturing 8/10/14, Media & Marketing 10/8/5, Education & Non-profit 6/7/7.
- Region: North America 55%, EMEA 30%, APAC 15% (no behavioral differences by design — a deliberate null).
- Employee count (log-normal median): SMB 25, Mid-Market 300, Enterprise 3,000, clipped to the segment band.

### 5.2 Initial subscription
- **Seats** (log-normal, median / σ / clip): SMB 6 / 0.65 / 1–45 (Paid Social median 4); Mid-Market 28 / 0.60 / 8–250; Enterprise 120 / 0.60 / 50–1,500.
- **Plan:** SMB Basic 80% / Premium 20%; Mid-Market Basic 40% / Premium 52% / Enterprise 8% (Enterprise only if ≥ 50 seats); Enterprise segment Premium 40% / Enterprise 60%. Seats are clipped to plan limits.
- **Billing period:** annual share SMB 22% (Paid Social 8%), Mid-Market 60%, Enterprise segment 95%; the Enterprise plan is always annual.
- **Discounts:** Enterprise plan 10–30% (uniform, whole %); sales-led (Outbound / Partner / Events) annual Premium deals in Mid-Market and Enterprise: 50% chance of 5–15%; otherwise none.
- **One-time onboarding fee:** Enterprise plan $5,000–$20,000; sales-led annual Mid-Market Premium 30% chance of $1,500–$3,000.
- **Payment method:** card for monthly SMB / Mid-Market self-serve, invoice otherwise.

### 5.3 Latent account traits (not exported)
Each account draws three hidden traits that drive behavior:
- **Fit** `μ` (long-run health): Normal(1.0 + channel adj + design-partner adj, 0.6). Channel adj: Organic 0, Paid Search −0.10, Paid Social −0.70, Partner +0.25, Outbound 0, Events −0.05; design partner +0.40.
- **Power-user propensity** `power`: Normal(0, 1) + segment adj (Mid-Market +0.3, Enterprise +0.4) + channel adj (Paid Social −0.6). Drives demand for Premium features.
- **Growth propensity** `growth`: Normal(0, 1) + industry adj (Technology +0.4, Media +0.2, Manufacturing −0.2, Education −0.3).

### 5.4 Weekly dynamics
- **Health** `H` mean-reverts to fit: `H ← H + 0.06·(μ_t − H) + 0.12·ε`. New accounts start 0.3 below fit and ramp up over 6 weeks (onboarding). Shocks: champion leaves (0.25% / week, H −0.8 to −1.6); new team adopts (0.30% / week Mid-Market and Enterprise, 0.15% SMB; H +0.3 to +0.8 and 8 weeks of faster team growth).
- **Team size** `N` (people who want access): `N ← N·exp(r)` with weekly growth `r` = segment base (SMB 0.12%, Mid-Market 0.18%, Enterprise 0.20%) + 0.20%·growth + 0.15%·(H − 1) + hiring seasonality + noise (σ 1%).
- **Engagement** = `sigmoid(0.3 + 0.9·H)` × usage seasonality; **WAU** ~ Binomial(min(N, 1.10 × seats), engagement). Up to 10% seat overage is tolerated (true-up policy), so WAU can exceed seats.
- **Seat additions** (immediate, prorated): when team size exceeds seats by > 2%, with probability 35% per week (25% for Mid-Market / Enterprise, procurement friction) × `sigmoid(H)`; the account buys up to `N × U(1.00, 1.10)`. A Basic account that would exceed 50 seats is upgraded to Premium instead ("seat-cap upgrade").
- **Seat reductions** (at period end): if 4-week average WAU × 1.4 < seats (utilization below ~70%), probability 30% at each monthly billing date or 65% at an annual renewal; new seats = average WAU × U(1.05, 1.25), not below the plan minimum. Plus 2% random small reductions at monthly billing dates (people leaving).
- **Basic → Premium upgrade** (immediate): weekly hazard `0.0025 × exp(0.9·power + 0.4·(H − 1)) × (2.5 if team grew > 10% in the last 13 weeks else 1) × packaging factor` (1.00 before 15 Apr 2026, 0.55 after).
- **Premium → Enterprise** (sales-assisted): accounts with ≥ 60 seats in Mid-Market / Enterprise and H > 0.5, weekly hazard 0.12% × exp(0.5·power); annual accounts upgrade immediately, monthly at the next billing date (moving to annual).
- **Downgrades** (at period end): Premium → Basic at a monthly billing date with probability `1.0% × exp(−0.8·(H − 1)) × exp(−0.5·power)` (×8 at an annual renewal), only if seats ≤ 50; Enterprise → Premium at renewal `10% × exp(−0.8·(H − 1))`.
- **Billing switches:** monthly → annual at a billing date with probability 2% (SMB 1.5%) if tenure ≥ 3 months and H > 0.8; annual → monthly at renewal 20% if H < 0.3. Because annual is cheaper per seat, a switch to annual **lowers** MRR (counted as contraction — see definitions).
- **Renewal price changes** (annual Enterprise / Mid-Market): H < 0.5 → 40% chance of an extra 3–10 pt discount (renewal concession); H > 1.5 → 20% chance the discount is reduced by 2–5 pts (price uplift).
- **Voluntary churn:** at each monthly billing date with probability `base × channel × industry × tenure × season × exp(−1.0·(H − 1))` (base SMB 2.0%, Mid-Market 0.9%, Enterprise 0.5%; capped 60%); at annual renewal `base × channel × industry × exp(−1.1·(H − 1))` (SMB 14%, Mid-Market 8%, Enterprise 5%; capped 80%). Multipliers — channel: Organic 1.0, Paid Search 1.1, Paid Social 1.25, Partner 0.8, Outbound 0.95, Events 1.0; industry: Financial Services 0.85, Healthcare 0.8, Retail 1.15, Manufacturing 0.9, Media 1.2, Education 0.9, others 1.0; tenure: months 1–3 ×1.5, 4–12 ×1.0, 13+ ×0.8; season: Dec–Jan ×1.15.
- **Downgrade-to-save:** when a Premium or Enterprise account decides to churn, CS saves it with a one-tier downgrade (and seats right-sized to WAU × 1.1) with probability 35% (pooled / named CSM) or 10% (digital).
- **Payment-failure churn** (card, monthly): a renewal charge fails with probability 3.5% (SMB) / 1.2% (Mid-Market); 65% recover within 1–14 days, the rest are canceled 14 days after the failed charge.
- **Win-backs (reactivation):** after a voluntary churn, weekly probability 0.12% × exp(0.3·(μ − 1)) during weeks 4–104; after a payment failure 6% per week in the first 8 weeks, then 0.15%. A win-back starts a **new subscription**: same plan (65%) or one tier lower, 60–100% of the previous seats, monthly billing (80%).

### 5.5 Seasonality
- Usage: weeks touching 24 Dec – 1 Jan ×0.70; July–August ×0.93; Education 15 Jun – 31 Aug ×0.65; Retail Nov–Dec ×1.05.
- Team growth (weekly r): Jan–Mar +0.08 pt, Sep +0.06 pt, Dec −0.08 pt; Retail Oct–Nov +0.6 pt and Jan–Feb −0.6 pt (seasonal staff); Education Aug–Sep +0.5 pt and Jun–Jul −0.4 pt.
- Churn: Dec–Jan ×1.15 (budget reviews).

### 5.6 Usage events (from the weekly state)
Per active account-week (plus up to 2 weeks of fading usage after a churn — a grace period):
- `session`: Poisson(WAU × 4.0 × e^{0.15(H−1)}), `unique_users` = WAU.
- `core_action`: Poisson(WAU × 15 × e^{0.2(H−1)}).
- `automation_run`: Poisson(WAU × rate × e^{0.4·power}), rate = Basic 0.6 (1.8 after 15 Apr 2026), Premium / Enterprise 2.4.
- `report_view`: Poisson(WAU × 0.8 / 1.6 / 2.0) for Basic / Premium / Enterprise.
- `api_call` (Premium+): Poisson(integrations × 120 × e^{0.5·power}); `integration_sync`: Poisson(integrations × 40). Active integrations start at Poisson(0.8 / 2.3 / 3.8) and grow with health (Basic capped at 5).
- `user_invite`: Poisson(1.1 × team growth that week + 0.015 × seats) — a leading indicator of seat additions.
- `premium_feature_gate` (Basic only): Poisson(WAU × 0.20 × e^{0.6·power + 0.3(H−1)}), ×0.45 after 15 Apr 2026 (the automation paywall disappears).

## 6. Built-in business events (the "scenario")

These are deliberate, so the analysis has realistic, overlapping drivers to untangle. The known-answer tests in `03_data_quality_checks.md` check that the metric layer recovers them.

| # | Event | Mechanism in the generator | What the analyst should be able to see |
|---|---|---|---|
| S1 | GA + design partners (Oct 2023) | +150 high-fit accounts in the first cohort | First cohort retains and expands better than later ones |
| S2 | Paid Social program (from Sep 2025) | Extra SMB, mostly monthly, lower fit and power-user propensity | Lower retention and upgrade rates for those cohorts; Basic base mix shifts toward low-upgrade accounts |
| S3 | Basic packaging change (15 Apr 2026) | Premium-feature paywall hits ×0.45, upgrade hazard ×0.55, Basic automation use ×3 | Upgrade triggers and Basic → Premium upgrades drop after April |
| S4 | Technology-sector hiring freeze (from 6 Apr 2026) | Technology accounts: team growth −0.40 pt / week (phased in over 6 weeks), fit −0.35, seat reductions ×1.5 | Tech seat additions collapse; Tech contraction rises, mostly at billing dates and annual renewals |
| S5 | One-off company-wide rollout (12 May 2026) | One Enterprise-plan Financial Services account roughly triples its seats | A single top mover inflates Q2 2026 expansion, making the Q3 drop look bigger quarter-over-quarter |
| S6 | Seasonality | §5.5 | Q3 usage and expansion are seasonally softer; year-over-year comparison needed |

## 7. Injected data-quality issues

| Issue | How much | Expected handling (in `sql/01`–`05` staging models) |
|---|---|---|
| Internal / test accounts (`TEST`, `[INTERNAL]`, `QA`, `zz_test` names; `ourcompany.example` domain) | 6 accounts, 2 with non-zero prices | Excluded from all metrics |
| Duplicated usage rows (pipeline double-load) | ~0.4% of rows | De-duplicated on (customer, week, event) |
| Voided duplicate invoice lines | ~0.5% of lines | Status `void` excluded |
| Missing industry (self-serve SMB) / employee count | ~2% / ~3% of accounts | Bucketed as `Unknown`; segment is always present |
| Usage after cancellation (grace period) | ≤ 2 weeks per churned subscription | Kept in usage, never used for MRR |
| Partial final week (week of 28 Sep 2026 has 3 of 7 days) | 1 week | Excluded from weekly usage trends |
| WAU above seats (allowed overage) | a few % of account-weeks | Kept; utilization can exceed 100% (a signal, not an error) |

## 8. Calibration targets (checked by `data_gen/sanity_checks.py`)

| Check | Target range | Why this range |
|---|---|---|
| Accounts ever acquired | 5,000 – 7,000 | "Several thousand customers" |
| Paying accounts at 2026-09-30 | 3,000 – 4,500 | |
| MRR at 2026-09-30 | $1.2M – $2.6M (ARR ≈ $15M – $31M) | Series B / C scale B2B SaaS |
| Plan mix at signup (non-test) | within ±3 pt of §5.2 implied mix | Generator follows its own spec |
| Annual share at signup | within ±3 pt of §5.2 implied mix | |
| Monthly logo churn, monthly-billed SMB | 2% – 5% | Typical self-serve SMB |
| TTM GRR (2025) | 82% – 92% | Mid-market SaaS benchmark range |
| TTM NRR (2025) | 102% – 118% | Healthy seat-based SaaS |
| TTM NRR drop, Sep 2025 → Sep 2026 | ≥ 3 pts | S3 + S4 |
| Expansion MRR, Q3 2026 vs Q2 2026 | ≤ −15% | The VP's observation |
| Tech seat-add events per account-week, after vs before Apr 2026 | ≤ −40% | S4 |
| Basic → Premium upgrades per Basic account-month, Q3 2026 vs Q3 2025 | ≤ −30% | S2 + S3 |
| Silently contracting accounts (§9 of definitions), share of ARR | 3% – 12% | Material but not dominant |

## 9. Change log (calibration)

| Date | Change | Reason |
|---|---|---|
| 2026-11-03 | v1 parameters as above | Initial design |

## 10. Limits of the synthetic data
- Behavior comes from rules I wrote, so effect sizes are only as realistic as the rules. Findings describe this dataset, not any real company.
- Accounts are independent (no macro feedback, no competitor events beyond S4).
- One subscription per account at a time; no multi-product, usage-based pricing, mid-term price increases, FX or credit notes.
- The usage data is a weekly aggregate, not raw clickstream.
