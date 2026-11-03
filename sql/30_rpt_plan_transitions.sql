-- =============================================================================
-- 30_rpt_plan_transitions.sql
-- Purpose : Where customers on each plan are h months later (transition matrix), and the
--           flow of plan changes (upgrades / downgrades) by quarter and source.
-- Inputs  : fct_customer_mrr_monthly, stg_plan_changes, dim_month
-- Outputs : rpt_plan_transition_matrix (horizon x from_plan x to_state),
--           rpt_plan_change_flows (quarter x from_plan x to_plan x change_source)
-- Rules   : 02_metric_definitions.md §6, §11. Pooled over all start months with s + h <= as-of.
-- Run     : duckdb <warehouse.duckdb> < sql/30_rpt_plan_transitions.sql
-- =============================================================================
CREATE OR REPLACE TABLE rpt_plan_transition_matrix AS
WITH h AS (SELECT * FROM (VALUES (3), (6), (12), (24)) t(horizon_months)),
pairs AS (
    SELECT
        h.horizon_months,
        a.customer_id,
        a.plan_id AS from_plan,
        a.mrr     AS from_mrr,
        CASE WHEN COALESCE(b.mrr, 0) = 0 THEN 'churned' ELSE b.plan_id END AS to_state,
        COALESCE(b.mrr, 0) AS to_mrr
    FROM fct_customer_mrr_monthly a
    CROSS JOIN h
    JOIN fct_customer_mrr_monthly b
      ON b.customer_id = a.customer_id
     AND b.month_end = last_day(a.month_end + to_months(h.horizon_months))
    WHERE a.mrr > 0
)
SELECT
    horizon_months,
    from_plan,
    to_state,
    COUNT(*)                                                               AS customer_months,
    COUNT(*) / SUM(COUNT(*)) OVER (PARTITION BY horizon_months, from_plan)  AS share_of_from_plan,
    SUM(from_mrr)                                                          AS from_mrr,
    SUM(to_mrr)                                                            AS to_mrr
FROM pairs
GROUP BY 1, 2, 3
ORDER BY 1, 2, 3;

CREATE OR REPLACE TABLE rpt_plan_change_flows AS
SELECT
    m.quarter_label,
    pc.from_plan_id,
    pc.to_plan_id,
    CASE WHEN pc.to_tier > pc.from_tier THEN 'upgrade' ELSE 'downgrade' END AS direction,
    pc.change_source,
    COUNT(*)                             AS changes,
    COUNT(DISTINCT pc.customer_id)       AS customers,
    SUM(pc.to_mrr - pc.from_mrr)         AS mrr_delta
FROM stg_plan_changes pc
JOIN dim_month m ON m.month_end = last_day(pc.effective_date)
WHERE pc.to_tier <> pc.from_tier
GROUP BY 1, 2, 3, 4, 5
ORDER BY 1, 2, 3, 5;
