# Assumptions log

One line per assumption, with its source or reason. Updated as the analysis proceeds; anything that would change a conclusion if wrong is marked **(sensitive)**.

| # | Area | Assumption | Source / reason |
|---|---|---|---|
| A1 | Data | All data is synthetic, generated from the rules in `data_model.md`; findings describe this dataset only. | No public SaaS billing data with plan changes + usage exists (see `00_plan.md`). |
| A2 | Calendar | "Last quarter" = Q3 2026 (1 Jul – 30 Sep 2026). | Request received 2026-11-03; October books close ~7 Nov, so Q3 is the last closed quarter. |
| A3 | Calendar | Data cutoff 2026-09-30; nothing after it is used, even where the generator could produce it. | Mirrors a real month-end close. |
| A4 | Metrics | Board NRR = trailing-12-month, same-customer, point-to-point (`02_metric_definitions.md §7`). | Investor / board convention; Finance agreed to footnote it. |
| A5 | Metrics | MRR = month-end snapshot; annual contracts ÷ 12; net of recurring discounts; excludes one-time fees, prorations, tax. | ChartMogul / Stripe convention; reconciled to invoices in `03_data_quality_checks.md`. |
| A6 | Metrics | Monthly → annual switches are classified as contraction (lower run-rate) and reported as their own component. | Same treatment as ChartMogul; keeps "unhappy customer" contraction separate. |
| A7 | Population | Internal / test accounts are identified by `email_domain = 'ourcompany.example'` or test-style names. | No `is_internal` flag in the CRM export — a common real-world gap. |
| A8 | Population | One live subscription per account; segments (size, industry, channel, region) are static from signup. | Simplification of the data model; documented in `data_model.md §10`. |
| A9 | CS capacity | CS can run ≈ 120 proactive account reviews per quarter (4 named CSMs × 15 + 6 pooled CSMs × 10). **(sensitive)** | Assumed team size; lists are ranked so the cut-off can move. |
| A10 | Silent churn | Default silent-contraction threshold: MRR ≥ 20% below the trailing-6-month peak with at least one contraction. **(sensitive)** | Chosen for CS actionability; sensitivity shown on the dashboard. |
| A11 | Upgrade signals | Upgrade horizon = 90 days; features use only the 13 complete weeks before the index date. | Matches a quarterly CS / Sales planning cycle; avoids look-ahead bias. |
| A12 | Pricing | List prices unchanged during Oct 2023 – Sep 2026; no usage-based charges. | Generator design (`data_model.md §1`). |
| A13 | Known events | The business-events calendar in `01_request_and_scope.md §4` is complete for company-initiated changes; anything else must be found in the data. | Provided by Marketing / Product / Sales Ops. |
