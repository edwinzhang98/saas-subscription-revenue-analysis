-- =============================================================================
-- 50_rpt_silent_contraction.sql
-- Purpose : Accounts with material contraction without cancellation ("quiet downgrades"),
--           flagged at every month-end, with the evidence CS needs.
-- Inputs  : fct_customer_mrr_monthly, fct_mrr_movement_components, stg_subscriptions
-- Outputs : rpt_silent_contraction (customer_id x month_end, flagged rows only)
-- Rules   : 02_metric_definitions.md §9 — billing-neutral MRR down >= 20% vs the max of the
--           previous 6 month-ends, >= 1 contraction in t-5..t with a negative plan / seats /
--           discount component, no cancellation requested on or before t. Class
--           'saved_re_risk' if any contraction in the window was a CS save offer.
-- Run     : duckdb <warehouse.duckdb> < sql/50_rpt_silent_contraction.sql
-- =============================================================================
CREATE OR REPLACE TABLE rpt_silent_contraction AS
WITH p AS (
    SELECT
        f.*,
        MAX(f.mrr_norm) OVER w6 AS peak_mrr_norm_6m,
        MAX(f.mrr)      OVER w6 AS peak_mrr_6m,
        MAX(f.seats)    OVER w6 AS peak_seats_6m,
        MAX(f.tier_rank) OVER w6 AS peak_tier_6m
    FROM fct_customer_mrr_monthly f
    WINDOW w6 AS (PARTITION BY f.customer_id ORDER BY f.month_end ROWS BETWEEN 6 PRECEDING AND 1 PRECEDING)
),
steps AS (
    SELECT
        customer_id,
        month_end,
        (LEAST(comp_plan, 0) + LEAST(comp_seats, 0) + LEAST(comp_discount, 0)) < 0 AS is_real_contraction,
        has_cs_save,
        has_renewal_change,
        LEAST(comp_plan, 0) < 0 AS has_plan_downgrade
    FROM fct_mrr_movement_components
    WHERE movement_type = 'contraction'
),
win AS (
    SELECT
        p.customer_id,
        p.month_end,
        COUNT(s.month_end) FILTER (WHERE s.is_real_contraction)                    AS contraction_steps_6m,
        MAX(s.month_end)   FILTER (WHERE s.is_real_contraction)                    AS last_contraction_month,
        BOOL_OR(s.has_cs_save AND s.is_real_contraction)                          AS any_cs_save,
        BOOL_OR(s.has_renewal_change AND s.is_real_contraction)                   AS any_renewal_contraction,
        BOOL_OR(s.has_plan_downgrade)                                             AS any_plan_downgrade
    FROM p
    LEFT JOIN steps s
      ON s.customer_id = p.customer_id
     AND s.month_end >  last_day(p.month_end - INTERVAL 6 MONTH)
     AND s.month_end <= p.month_end
    WHERE p.mrr > 0
    GROUP BY 1, 2
),
cancel_open AS (
    SELECT p.customer_id, p.month_end, BOOL_OR(s.cancel_requested_date <= p.month_end) AS cancel_requested_open
    FROM p
    JOIN stg_subscriptions s
      ON s.customer_id = p.customer_id AND s.start_date <= p.month_end
     AND (s.end_date IS NULL OR s.end_date > p.month_end)
    WHERE p.mrr > 0
    GROUP BY 1, 2
)
SELECT
    p.customer_id,
    p.month_end,
    p.segment, p.industry, p.acquisition_channel, p.region, p.cs_coverage,
    p.plan_id, p.billing_period, p.seats,
    p.peak_seats_6m,
    p.mrr,
    p.mrr * 12                                              AS current_arr,
    p.peak_mrr_6m,
    12 * GREATEST(p.peak_mrr_6m - p.mrr, 0)                  AS lost_arr,
    p.mrr_norm,
    p.peak_mrr_norm_6m,
    1 - p.mrr_norm / p.peak_mrr_norm_6m                     AS decline_pct_norm,
    w.contraction_steps_6m,
    w.last_contraction_month,
    date_diff('month', w.last_contraction_month, p.month_end) AS months_since_last_contraction,
    w.any_plan_downgrade,
    w.any_renewal_contraction,
    CASE WHEN w.any_cs_save THEN 'saved_re_risk' ELSE 'silent' END AS contraction_class
FROM p
JOIN win w ON w.customer_id = p.customer_id AND w.month_end = p.month_end
LEFT JOIN cancel_open c ON c.customer_id = p.customer_id AND c.month_end = p.month_end
WHERE p.mrr > 0
  AND p.peak_mrr_norm_6m > 0
  AND p.mrr_norm <= 0.80 * p.peak_mrr_norm_6m
  AND w.contraction_steps_6m >= 1
  AND NOT COALESCE(c.cancel_requested_open, FALSE);
