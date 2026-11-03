-- =============================================================================
-- 26_rpt_segment_retention.sql
-- Purpose : TTM NRR / GRR / logo retention by customer group, for every month-end.
-- Inputs  : fct_customer_mrr_monthly, dim_month
-- Outputs : rpt_segment_retention_ttm (month_end x dimension x dimension_value)
-- Rules   : 02_metric_definitions.md §7 — static attributes (segment, industry, channel,
--           region, CS coverage) or state AT THE START of the window (plan, billing period).
-- Run     : duckdb <warehouse.duckdb> < sql/26_rpt_segment_retention.sql
-- =============================================================================
CREATE OR REPLACE TABLE rpt_segment_retention_ttm AS
WITH pairs AS (
    SELECT
        m.month_end,
        b.customer_id,
        b.mrr AS mrr_base,
        COALESCE(c.mrr, 0) AS mrr_now,
        b.segment, b.industry, b.acquisition_channel, b.region, b.cs_coverage,
        b.plan_id AS plan_at_start, b.billing_period AS billing_at_start,
        CAST(year(b.cohort_month_end) AS VARCHAR) AS cohort_year
    FROM dim_month m
    JOIN fct_customer_mrr_monthly b
      ON b.month_end = last_day(m.month_end - INTERVAL 12 MONTH) AND b.mrr > 0
    LEFT JOIN fct_customer_mrr_monthly c
      ON c.customer_id = b.customer_id AND c.month_end = m.month_end
),
long AS (
    SELECT month_end, 'segment' AS dimension, segment AS dimension_value, mrr_base, mrr_now FROM pairs
    UNION ALL SELECT month_end, 'industry',            industry,            mrr_base, mrr_now FROM pairs
    UNION ALL SELECT month_end, 'acquisition_channel', acquisition_channel, mrr_base, mrr_now FROM pairs
    UNION ALL SELECT month_end, 'region',              region,              mrr_base, mrr_now FROM pairs
    UNION ALL SELECT month_end, 'cs_coverage',         cs_coverage,         mrr_base, mrr_now FROM pairs
    UNION ALL SELECT month_end, 'plan_at_start',       plan_at_start,       mrr_base, mrr_now FROM pairs
    UNION ALL SELECT month_end, 'billing_at_start',    billing_at_start,    mrr_base, mrr_now FROM pairs
    UNION ALL SELECT month_end, 'cohort_year',         cohort_year,         mrr_base, mrr_now FROM pairs
)
SELECT
    month_end,
    dimension,
    dimension_value,
    COUNT(*)                                       AS base_customers,
    SUM(mrr_base)                                  AS base_mrr,
    SUM(mrr_now)                                   AS retained_mrr,
    SUM(mrr_now) / SUM(mrr_base)                   AS nrr,
    SUM(LEAST(mrr_now, mrr_base)) / SUM(mrr_base)  AS grr,
    AVG(CASE WHEN mrr_now > 0 THEN 1.0 ELSE 0.0 END) AS logo_retention
FROM long
GROUP BY 1, 2, 3
ORDER BY 1, 2, 3;
