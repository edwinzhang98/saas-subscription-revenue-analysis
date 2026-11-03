-- =============================================================================
-- 20_fct_customer_mrr_monthly.sql
-- Purpose : Customer MRR at every month-end from the first paid month-end to the as-of date
--           (dense panel: months with no live subscription have MRR = 0).
-- Inputs  : int_subscription_versions, dim_month, stg_customers
-- Outputs : fct_customer_mrr_monthly (customer_id x month_end)
-- Rules   : 02_metric_definitions.md §3 (snapshot at end of the last day of the month;
--           live if valid_from <= month_end < valid_to). Annual already / 12 in mrr.
-- Run     : duckdb <warehouse.duckdb> < sql/20_fct_customer_mrr_monthly.sql
-- =============================================================================
CREATE OR REPLACE TABLE fct_customer_mrr_monthly AS
WITH live AS (
    SELECT
        v.customer_id,
        m.month_end,
        SUM(v.mrr)                          AS mrr,
        SUM(v.mrr_norm)                     AS mrr_norm,
        SUM(v.seats)                        AS seats,
        MAX(v.tier_rank)                    AS tier_rank,
        arg_max(v.plan_id, v.tier_rank)     AS plan_id,
        arg_max(v.billing_period, v.mrr)    AS billing_period,
        AVG(v.discount_pct)                 AS discount_pct,
        COUNT(*)                            AS live_subscriptions
    FROM int_subscription_versions v
    JOIN dim_month m ON v.valid_from <= m.month_end AND v.valid_to > m.month_end
    GROUP BY 1, 2
),
first_paid AS (
    SELECT customer_id, MIN(month_end) AS first_paid_month_end
    FROM live WHERE mrr > 0
    GROUP BY 1
)
SELECT
    f.customer_id,
    m.month_end,
    m.quarter_label,
    COALESCE(l.mrr, 0)                                           AS mrr,
    COALESCE(l.mrr, 0) * 12                                      AS arr,
    COALESCE(l.mrr_norm, 0)                                      AS mrr_norm,
    COALESCE(l.mrr, 0) > 0                                       AS is_paying,
    l.plan_id,
    COALESCE(l.tier_rank, 0)                                     AS tier_rank,
    COALESCE(l.seats, 0)                                         AS seats,
    l.billing_period,
    l.discount_pct,
    f.first_paid_month_end                                       AS cohort_month_end,
    date_trunc('quarter', f.first_paid_month_end)::DATE          AS cohort_quarter_start,
    date_diff('month', f.first_paid_month_end, m.month_end)      AS months_since_first_paid,
    c.segment,
    c.industry,
    c.acquisition_channel,
    c.region,
    c.cs_coverage,
    c.is_design_partner
FROM first_paid f
JOIN dim_month m ON m.month_end >= f.first_paid_month_end
LEFT JOIN live l ON l.customer_id = f.customer_id AND l.month_end = m.month_end
JOIN stg_customers c ON c.customer_id = f.customer_id;
