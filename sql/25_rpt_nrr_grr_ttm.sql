-- =============================================================================
-- 25_rpt_nrr_grr_ttm.sql
-- Purpose : Headline trailing-12-month NRR, GRR and logo retention (same-customer,
--           point-to-point), with the decomposition of NRR into expansion / contraction / churn;
--           plus the quarterly run-rate NRR (3-month point-to-point, annualised).
-- Inputs  : fct_customer_mrr_monthly, dim_month
-- Outputs : rpt_nrr_grr_ttm (one row per month-end with a 12-month base),
--           rpt_nrr_quarterly_runrate (one row per quarter-end)
-- Rules   : 02_metric_definitions.md §7, §8. Base = customers with MRR > 0 at t-12.
--           is_reportable = base of at least 1,000 customers.
-- Run     : duckdb <warehouse.duckdb> < sql/25_rpt_nrr_grr_ttm.sql
-- =============================================================================
CREATE OR REPLACE TABLE rpt_nrr_grr_ttm AS
WITH pairs AS (
    SELECT
        m.month_end,
        b.customer_id,
        b.mrr                    AS mrr_base,
        COALESCE(c.mrr, 0)       AS mrr_now
    FROM dim_month m
    JOIN fct_customer_mrr_monthly b
      ON b.month_end = last_day(m.month_end - INTERVAL 12 MONTH) AND b.mrr > 0
    LEFT JOIN fct_customer_mrr_monthly c
      ON c.customer_id = b.customer_id AND c.month_end = m.month_end
)
SELECT
    month_end,
    last_day(month_end - INTERVAL 12 MONTH)                                      AS base_month_end,
    COUNT(*)                                                                     AS base_customers,
    SUM(mrr_base)                                                                AS base_mrr,
    SUM(mrr_now)                                                                 AS retained_mrr,
    SUM(LEAST(mrr_now, mrr_base))                                                AS retained_mrr_capped,
    SUM(mrr_now) / SUM(mrr_base)                                                 AS nrr,
    SUM(LEAST(mrr_now, mrr_base)) / SUM(mrr_base)                                AS grr,
    AVG(CASE WHEN mrr_now > 0 THEN 1.0 ELSE 0.0 END)                             AS logo_retention,
    SUM(GREATEST(mrr_now - mrr_base, 0)) / SUM(mrr_base)                         AS expansion_contribution,
    -SUM(CASE WHEN mrr_now > 0 AND mrr_now < mrr_base THEN mrr_base - mrr_now ELSE 0 END) / SUM(mrr_base)
                                                                                 AS contraction_contribution,
    -SUM(CASE WHEN mrr_now = 0 THEN mrr_base ELSE 0 END) / SUM(mrr_base)         AS churn_contribution,
    COUNT(*) >= 1000                                                             AS is_reportable
FROM pairs
GROUP BY 1
ORDER BY 1;

CREATE OR REPLACE TABLE rpt_nrr_quarterly_runrate AS
WITH pairs AS (
    SELECT
        m.month_end,
        m.quarter_label,
        b.customer_id,
        b.mrr               AS mrr_base,
        COALESCE(c.mrr, 0)  AS mrr_now
    FROM dim_month m
    JOIN fct_customer_mrr_monthly b
      ON b.month_end = last_day(m.month_end - INTERVAL 3 MONTH) AND b.mrr > 0
    LEFT JOIN fct_customer_mrr_monthly c
      ON c.customer_id = b.customer_id AND c.month_end = m.month_end
    WHERE m.is_quarter_end_month
)
SELECT
    month_end AS quarter_end,
    quarter_label,
    COUNT(*)                                          AS base_customers,
    SUM(mrr_base)                                     AS base_mrr,
    SUM(mrr_now) / SUM(mrr_base)                      AS nrr_quarter,
    SUM(LEAST(mrr_now, mrr_base)) / SUM(mrr_base)     AS grr_quarter,
    POWER(SUM(mrr_now) / SUM(mrr_base), 4)            AS nrr_runrate_annualized,
    POWER(SUM(LEAST(mrr_now, mrr_base)) / SUM(mrr_base), 4) AS grr_runrate_annualized
FROM pairs
GROUP BY 1, 2
ORDER BY 1;
