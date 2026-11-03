# data_gen — synthetic SaaS billing and usage data

The analysis runs on **synthetic data**: public SaaS billing data with plan changes, seat changes and product usage does not
exist. This folder generates the six source tables a billing system (Stripe / Chargebee style) and a product-analytics export
would produce. The rules behind every parameter, the planted business effects (answer key) and the calibration history are in
[`docs/data_model.md`](../docs/data_model.md); this page is the operating manual.

| File | What it does |
|---|---|
| `generate.py` | Simulates every account week by week (own random stream per account → order-independent, reproducible) and writes `plans`, `customers`, `subscriptions`, `plan_changes`, `invoices`, `usage_events` as parquet, plus `_manifest.json` (row counts, params hash) and `_scenario_truth.json` (answer key — read only by `src/saas_rev/validation.py`, never by the analysis). |
| `params.yaml` | Every parameter (source of truth). Groups below. |
| `sanity_checks.py` | 18 design-target checks (scale, plan mix, churn by segment, NRR / GRR range, the planted effects' visibility). Exit code 1 if any fails; `run.py` stops the pipeline on failure. |

```bash
uv run python data_gen/generate.py                 # -> <data_dir>/raw  (config.yaml / SAAS_DATA_DIR)
uv run python data_gen/sanity_checks.py            # prints PASS / FAIL per check
uv run python data_gen/generate.py --out /tmp/raw --seed 7   # another seed, another folder
```

## Parameter groups in `params.yaml`

| Group | Controls | Notes |
|---|---|---|
| `calendar` | GA date (2023-10-02) and data cutoff (2026-09-30) | Weekly simulation clock; nothing after the cutoff is written |
| `plans` | List prices per seat (monthly / annual), seat limits, tier order | Basic $15 / $144, Premium $35 / $336, Enterprise annual-only $600 |
| `acquisition` | New accounts per month (base × growth × seasonality), segment / channel / industry / region mix, quarter-end clustering for sales-led deals, design partners, early adopters, the Paid Social program | Paid Social extras from Sep 2025 are scenario S2 |
| `initial` | Seats (log-normal by segment), plan conditional on seats, annual share, discounts, onboarding fees, tax rates | Plan is drawn after seats, so no Basic account starts above 45 seats |
| `latent` | Hidden traits per account: product fit (long-run health), power-user propensity, growth propensity | Never exported; they drive behaviour |
| `dynamics` | Weekly health process and shocks, team growth (hiring + adoption), seat adds / reductions, upgrades, downgrades, billing switches, renewal pricing, churn hazards, CS saves, payment failures, win-backs, integrations | Billing-boundary order is fixed (churn → save → changes → charge); see `data_model.md §5.4` |
| `seasonality` | Usage dips (holidays, summer, education summer), hiring seasonality incl. the Jul–Aug lull, retail / education staffing cycles | Scenario S6 |
| `usage` | Events per weekly active user (sessions, core actions, automations, report views, integration syncs, invites, Premium paywall hits) and grace-period usage after cancellation | `premium_feature_gate` = a Basic user hit a Premium-only feature |
| `scenario` | Planted effects: S3 Basic packaging change (downside and upside), S4 Technology hiring freeze + layoff wave, S5 one-off rollout | Answer key in `data_model.md §6`; recovered vs planted in `docs/03_data_quality_checks.md` |
| `dq` | Injected data-quality issues: internal / test accounts, duplicated usage rows, voided duplicate invoices, missing firmographics, segment vs employee-count mismatch, duplicate CRM records, orphan plan changes | Each is caught and counted by `sql/91_dq_checks.sql` |

## Reproducibility

- One master seed (`config.yaml → seed`, 20261103) → `numpy.random.SeedSequence` children: acquisition, simulation (one child
  per account), usage, data-quality injection. Two runs give identical files (`tests/test_crosscheck_and_determinism.py`).
- Changing one account's rules changes only that account's stream — which is how the one-off rollout was resized without
  moving any other number (`data_model.md §9`, run 8).
- Runtime ≈ 4 seconds for ≈ 5,300 accounts.
