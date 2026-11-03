-- =============================================================================
-- 27_rpt_cohort_retention.sql
-- Purpose : Cohort NRR, GRR and logo retention by months since the first paid month-end,
--           for monthly and quarterly signup cohorts.
-- Inputs  : fct_customer_mrr_monthly
-- Outputs : rpt_cohort_retention (cohort_grain x cohort x months_since_first_paid)
-- Rules   : 02_metric_definitions.md §10 — k = months since each customer's own first paid
--           month-end; is_complete = every cohort member observed at k (right-censoring).
-- Run     : duckdb <warehouse.duckdb> < sql/27_rpt_cohort_retention.sql
-- =============================================================================
CREATE OR REPLACE TABLE rpt_cohort_retention AS
WITH c AS (
    SELECT
        customer_id,
        cohort_month_end,
        cohort_quarter_start,
        months_since_first_paid AS k,
        mrr,
        FIRST_VALUE(mrr) OVER (PARTITION BY customer_id ORDER BY month_end) AS mrr_0
    FROM fct_customer_mrr_monthly
),
monthly AS (
    SELECT 'month' AS cohort_grain, strftime(cohort_month_end, '%Y-%m') AS cohort, cohort_month_end AS cohort_date,
           k, customer_id, mrr, mrr_0
    FROM c
),
quarterly AS (
    SELECT 'quarter' AS cohort_grain, year(cohort_quarter_start) || '-Q' || quarter(cohort_quarter_start) AS cohort,
           cohort_quarter_start AS cohort_date, k, customer_id, mrr, mrr_0
    FROM c
),
unioned AS (SELECT * FROM monthly UNION ALL SELECT * FROM quarterly),
sizes AS (
    SELECT cohort_grain, cohort, COUNT(DISTINCT customer_id) AS cohort_customers
    FROM unioned WHERE k = 0 GROUP BY 1, 2
)
SELECT
    u.cohort_grain,
    u.cohort,
    MIN(u.cohort_date)                                        AS cohort_date,
    u.k                                                       AS months_since_first_paid,
    s.cohort_customers,
    COUNT(*)                                                  AS customers_observed,
    SUM(u.mrr_0)                                              AS base_mrr,
    SUM(u.mrr)                                                AS mrr_k,
    -- right-censoring (02 §10): published values only where every cohort member is observed at k
    CASE WHEN COUNT(*) = s.cohort_customers THEN SUM(u.mrr) / NULLIF(SUM(u.mrr_0), 0) END                 AS nrr,
    CASE WHEN COUNT(*) = s.cohort_customers THEN SUM(LEAST(u.mrr, u.mrr_0)) / NULLIF(SUM(u.mrr_0), 0) END AS grr,
    CASE WHEN COUNT(*) = s.cohort_customers THEN AVG(CASE WHEN u.mrr > 0 THEN 1.0 ELSE 0.0 END) END       AS logo_retention,
    SUM(u.mrr) / NULLIF(SUM(u.mrr_0), 0)                      AS nrr_observed_members,
    COUNT(*) = s.cohort_customers                             AS is_complete
FROM unioned u
JOIN sizes s USING (cohort_grain, cohort)
GROUP BY u.cohort_grain, u.cohort, u.k, s.cohort_customers
ORDER BY 1, 2, 4;
