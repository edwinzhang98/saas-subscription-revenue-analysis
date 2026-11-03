-- =============================================================================
-- 90_dq_revenue_reconciliation.sql
-- Purpose : Reconcile billed recurring revenue (invoices spread over their service periods)
--           with revenue implied by daily MRR, by month, calendar year and trailing 12 months.
-- Inputs  : stg_invoices, stg_subscriptions, int_subscription_versions, dim_month, meta_run
-- Outputs : dq_revenue_reconciliation (period_type x period: billed, daily_mrr_revenue, gap)
-- Rules   : 02_metric_definitions.md §14 — daily MRR revenue = MRR x 12 / days in the year;
--           billed = non-void recurring + proration lines spread by day over
--           [service_period_start, service_period_end) clipped to the subscription's live days;
--           the clipped remainder is "billed but not serviced". Expected |gap| < 0.3% per year.
-- Run     : duckdb <warehouse.duckdb> < sql/90_dq_revenue_reconciliation.sql
-- =============================================================================
CREATE OR REPLACE TABLE dq_revenue_reconciliation AS
WITH months AS (
    SELECT month_start, month_end, (month_end + INTERVAL 1 DAY)::DATE AS month_end_excl,
           CASE WHEN (year(month_start) % 4 = 0 AND year(month_start) % 100 <> 0) OR year(month_start) % 400 = 0
                THEN 366 ELSE 365 END AS days_in_year
    FROM dim_month
),
lines AS (
    SELECT i.invoice_line_id, i.subscription_id, i.amount_usd, i.status,
           i.service_period_start AS ps, i.service_period_end AS pe,
           date_diff('day', i.service_period_start, i.service_period_end) AS period_days,
           s.start_date, COALESCE(s.end_date, DATE '9999-12-31') AS end_excl
    FROM stg_invoices i
    JOIN stg_subscriptions s USING (subscription_id)
    WHERE i.line_type IN ('recurring', 'proration')
      AND date_diff('day', i.service_period_start, i.service_period_end) > 0
),
billed AS (
    SELECT m.month_end,
           SUM(l.amount_usd / l.period_days * GREATEST(0, date_diff('day',
                 GREATEST(l.ps, m.month_start, l.start_date), LEAST(l.pe, m.month_end_excl, l.end_excl))))  AS billed_serviced,
           SUM(l.amount_usd / l.period_days * GREATEST(0, date_diff('day',
                 GREATEST(l.ps, m.month_start), LEAST(l.pe, m.month_end_excl))))                            AS billed_gross,
           SUM(CASE WHEN l.status = 'uncollectible' THEN l.amount_usd / l.period_days * GREATEST(0, date_diff('day',
                 GREATEST(l.ps, m.month_start), LEAST(l.pe, m.month_end_excl))) ELSE 0 END)                   AS billed_uncollectible
    FROM lines l
    JOIN months m ON l.ps < m.month_end_excl AND l.pe > m.month_start
    GROUP BY 1
),
mrr_rev AS (
    SELECT m.month_end,
           SUM(v.mrr * 12.0 / m.days_in_year * GREATEST(0, date_diff('day',
                 GREATEST(v.valid_from, m.month_start), LEAST(v.valid_to, m.month_end_excl)))) AS daily_mrr_revenue
    FROM int_subscription_versions v
    JOIN months m ON v.valid_from < m.month_end_excl AND v.valid_to > m.month_start
    GROUP BY 1
),
monthly AS (
    SELECT m.month_end,
           COALESCE(b.billed_serviced, 0)      AS billed_serviced,
           COALESCE(b.billed_gross, 0)         AS billed_gross,
           COALESCE(b.billed_uncollectible, 0) AS billed_uncollectible,
           COALESCE(r.daily_mrr_revenue, 0)    AS daily_mrr_revenue
    FROM months m
    LEFT JOIN billed b USING (month_end)
    LEFT JOIN mrr_rev r USING (month_end)
),
periods AS (
    SELECT 'month' AS period_type, strftime(month_end, '%Y-%m') AS period, month_end AS period_end, * EXCLUDE (month_end)
    FROM monthly
    UNION ALL
    SELECT 'calendar_year', CAST(year(month_end) AS VARCHAR), MAX(month_end),
           SUM(billed_serviced), SUM(billed_gross), SUM(billed_uncollectible), SUM(daily_mrr_revenue)
    FROM monthly WHERE year(month_end) IN (2024, 2025)
    GROUP BY year(month_end)
    UNION ALL
    SELECT 'ttm', 'Oct 2025 - Sep 2026', MAX(month_end),
           SUM(billed_serviced), SUM(billed_gross), SUM(billed_uncollectible), SUM(daily_mrr_revenue)
    FROM monthly WHERE month_end BETWEEN DATE '2025-10-31' AND DATE '2026-09-30'
)
SELECT
    period_type,
    period,
    period_end,
    billed_serviced,
    billed_gross - billed_serviced                                  AS billed_not_serviced,
    billed_uncollectible,
    daily_mrr_revenue,
    billed_serviced - daily_mrr_revenue                             AS gap,
    (billed_serviced - daily_mrr_revenue) / NULLIF(daily_mrr_revenue, 0) AS gap_pct
FROM periods
ORDER BY CASE period_type WHEN 'month' THEN 1 WHEN 'calendar_year' THEN 2 ELSE 3 END, period_end;
