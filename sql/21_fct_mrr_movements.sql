-- =============================================================================
-- 21_fct_mrr_movements.sql
-- Purpose : Month-over-month MRR movement per customer: new / expansion / contraction /
--           churn / reactivation / unchanged.
-- Inputs  : fct_customer_mrr_monthly
-- Outputs : fct_mrr_movements (customer_id x month_end, one movement type per row)
-- Rules   : 02_metric_definitions.md §4. New vs Reactivation uses month-end history only.
-- Run     : duckdb <warehouse.duckdb> < sql/21_fct_mrr_movements.sql
-- =============================================================================
CREATE OR REPLACE TABLE fct_mrr_movements AS
WITH x AS (
    SELECT
        f.*,
        COALESCE(LAG(f.mrr)       OVER w, 0)  AS mrr_prev,
        COALESCE(LAG(f.mrr_norm)  OVER w, 0)  AS mrr_norm_prev,
        COALESCE(LAG(f.tier_rank) OVER w, 0)  AS tier_rank_prev,
        LAG(f.plan_id)            OVER w      AS plan_id_prev,
        LAG(f.billing_period)     OVER w      AS billing_period_prev,
        COALESCE(LAG(f.seats)     OVER w, 0)  AS seats_prev,
        COALESCE(MAX(CASE WHEN f.mrr > 0 THEN 1 ELSE 0 END) OVER (
            PARTITION BY f.customer_id ORDER BY f.month_end
            ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING), 0) = 1 AS had_mrr_before
    FROM fct_customer_mrr_monthly f
    WINDOW w AS (PARTITION BY f.customer_id ORDER BY f.month_end)
)
SELECT
    customer_id, month_end, quarter_label,
    mrr_prev, mrr, mrr_norm_prev, mrr_norm,
    plan_id_prev, plan_id, tier_rank_prev, tier_rank, billing_period_prev, billing_period, seats_prev, seats,
    CASE
        WHEN mrr_prev = 0 AND mrr > 0 AND NOT had_mrr_before THEN 'new'
        WHEN mrr_prev = 0 AND mrr > 0                        THEN 'reactivation'
        WHEN mrr_prev > 0 AND mrr = 0                        THEN 'churn'
        WHEN mrr_prev > 0 AND mrr > mrr_prev                 THEN 'expansion'
        WHEN mrr_prev > 0 AND mrr < mrr_prev                 THEN 'contraction'
        WHEN mrr_prev > 0                                    THEN 'unchanged'
        ELSE 'not_paying'
    END AS movement_type,
    CASE WHEN mrr_prev = 0 AND mrr > 0 AND NOT had_mrr_before THEN mrr ELSE 0 END          AS new_mrr,
    CASE WHEN mrr_prev = 0 AND mrr > 0 AND had_mrr_before     THEN mrr ELSE 0 END          AS reactivation_mrr,
    CASE WHEN mrr_prev > 0 AND mrr > mrr_prev                 THEN mrr - mrr_prev ELSE 0 END AS expansion_mrr,
    CASE WHEN mrr_prev > 0 AND mrr > 0 AND mrr < mrr_prev     THEN mrr_prev - mrr ELSE 0 END AS contraction_mrr,
    CASE WHEN mrr_prev > 0 AND mrr = 0                        THEN mrr_prev ELSE 0 END     AS churn_mrr,
    mrr - mrr_prev                                                                         AS net_change,
    cohort_month_end, months_since_first_paid,
    segment, industry, acquisition_channel, region, cs_coverage, is_design_partner
FROM x;
