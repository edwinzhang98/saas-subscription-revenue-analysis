-- =============================================================================
-- 23_rpt_mrr_waterfall.sql
-- Purpose : Monthly MRR waterfall (start -> new / expansion / reactivation / contraction /
--           churn -> end), customer counts, churn and flow-retention rates, and the gross
--           component view of expansion and contraction.
-- Inputs  : fct_mrr_movements, fct_mrr_movement_components, stg_subscriptions
-- Outputs : rpt_mrr_waterfall_monthly (one row per month)
-- Rules   : 02_metric_definitions.md §4, §4.1, §5. identity_gap must be 0 (to the cent).
-- Run     : duckdb <warehouse.duckdb> < sql/23_rpt_mrr_waterfall.sql
-- =============================================================================
CREATE OR REPLACE TABLE rpt_mrr_waterfall_monthly AS
WITH churn_reason AS (
    -- reason of the subscription whose end made the customer churn in that month
    SELECT mv.customer_id, mv.month_end, arg_max(s.cancel_reason, s.end_date) AS cancel_reason
    FROM fct_mrr_movements mv
    JOIN stg_subscriptions s
      ON s.customer_id = mv.customer_id
     AND s.end_date >  last_day(mv.month_end - INTERVAL 1 MONTH)
     AND s.end_date <= mv.month_end
    WHERE mv.movement_type = 'churn'
    GROUP BY 1, 2
),
mv AS (
    SELECT
        m.month_end,
        m.quarter_label,
        SUM(m.mrr_prev)                                                   AS mrr_start,
        SUM(m.new_mrr)                                                    AS new_mrr,
        SUM(m.expansion_mrr)                                              AS expansion_mrr,
        SUM(m.reactivation_mrr)                                           AS reactivation_mrr,
        SUM(m.contraction_mrr)                                            AS contraction_mrr,
        SUM(m.churn_mrr)                                                  AS churn_mrr,
        SUM(CASE WHEN cr.cancel_reason = 'payment_failed' THEN m.churn_mrr ELSE 0 END) AS churn_mrr_involuntary,
        SUM(m.mrr)                                                        AS mrr_end,
        COUNT(*) FILTER (WHERE m.mrr_prev > 0)                            AS customers_start,
        COUNT(*) FILTER (WHERE m.mrr > 0)                                 AS customers_end,
        COUNT(*) FILTER (WHERE m.movement_type = 'new')                   AS new_customers,
        COUNT(*) FILTER (WHERE m.movement_type = 'expansion')             AS expanding_customers,
        COUNT(*) FILTER (WHERE m.movement_type = 'contraction')           AS contracting_customers,
        COUNT(*) FILTER (WHERE m.movement_type = 'churn')                 AS churned_customers,
        COUNT(*) FILTER (WHERE m.movement_type = 'reactivation')          AS reactivated_customers
    FROM fct_mrr_movements m
    LEFT JOIN churn_reason cr ON cr.customer_id = m.customer_id AND cr.month_end = m.month_end
    GROUP BY 1, 2
),
comp AS (
    SELECT
        month_end,
        SUM(GREATEST(comp_seats, 0))          AS gross_seat_increase,
        SUM(LEAST(comp_seats, 0))             AS gross_seat_decrease,
        SUM(GREATEST(comp_plan, 0))           AS gross_plan_upgrade,
        SUM(LEAST(comp_plan, 0))              AS gross_plan_downgrade,
        SUM(GREATEST(comp_billing_period, 0)) AS gross_billing_to_monthly,
        SUM(LEAST(comp_billing_period, 0))    AS gross_billing_to_annual,
        SUM(GREATEST(comp_discount, 0))       AS gross_discount_decrease,
        SUM(LEAST(comp_discount, 0))          AS gross_discount_increase,
        SUM(comp_other)                       AS component_other
    FROM fct_mrr_movement_components
    GROUP BY 1
)
SELECT
    mv.*,
    mv.churn_mrr - mv.churn_mrr_involuntary                                        AS churn_mrr_voluntary,
    ROUND(mv.mrr_end - (mv.mrr_start + mv.new_mrr + mv.expansion_mrr + mv.reactivation_mrr
                        - mv.contraction_mrr - mv.churn_mrr), 2)                    AS identity_gap,
    mv.mrr_end * 12                                                                AS arr_end,
    mv.mrr_end / NULLIF(mv.customers_end, 0)                                       AS arpa,
    mv.churned_customers / NULLIF(mv.customers_start, 0)                           AS logo_churn_rate,
    (mv.churn_mrr + mv.contraction_mrr) / NULLIF(mv.mrr_start, 0)                  AS gross_mrr_churn_rate,
    (mv.churn_mrr + mv.contraction_mrr - mv.expansion_mrr) / NULLIF(mv.mrr_start, 0) AS net_mrr_churn_rate,
    (mv.mrr_start + mv.expansion_mrr - mv.contraction_mrr - mv.churn_mrr) / NULLIF(mv.mrr_start, 0) AS monthly_net_retention_flow,
    (mv.mrr_start - mv.contraction_mrr - mv.churn_mrr) / NULLIF(mv.mrr_start, 0)   AS monthly_gross_retention_flow,
    COALESCE(c.gross_seat_increase, 0)      AS gross_seat_increase,
    COALESCE(c.gross_seat_decrease, 0)      AS gross_seat_decrease,
    COALESCE(c.gross_plan_upgrade, 0)       AS gross_plan_upgrade,
    COALESCE(c.gross_plan_downgrade, 0)     AS gross_plan_downgrade,
    COALESCE(c.gross_billing_to_monthly, 0) AS gross_billing_to_monthly,
    COALESCE(c.gross_billing_to_annual, 0)  AS gross_billing_to_annual,
    COALESCE(c.gross_discount_decrease, 0)  AS gross_discount_decrease,
    COALESCE(c.gross_discount_increase, 0)  AS gross_discount_increase,
    COALESCE(c.component_other, 0)          AS component_other
FROM mv
LEFT JOIN comp c USING (month_end)
ORDER BY mv.month_end;
