-- =============================================================================
-- 03_stg_plan_changes.sql
-- Purpose : Valid plan / seat / billing / discount changes for real customers, with MRR
--           before and after each change; rejected rows are kept apart for data quality.
-- Inputs  : raw.plan_changes, stg_subscriptions, stg_plans, meta_run, macro mrr_of
-- Outputs : stg_plan_changes (one row per valid change), stg_plan_changes_rejected
-- Rules   : 02_metric_definitions.md §3 — a change is valid only if
--           start_date <= effective_date < end_date (or end_date null) and effective_date <= as-of.
-- Run     : duckdb <warehouse.duckdb> < sql/03_stg_plan_changes.sql
-- =============================================================================
CREATE OR REPLACE TABLE stg_plan_changes_rejected AS
SELECT
    pc.*,
    CASE
        WHEN s.subscription_id IS NULL                             THEN 'internal_or_unknown_subscription'
        WHEN pc.effective_date < s.start_date                      THEN 'before_start'
        WHEN s.end_date IS NOT NULL AND pc.effective_date >= s.end_date THEN 'on_or_after_end_date'
        WHEN pc.effective_date > r.as_of_date                      THEN 'after_as_of'
    END AS reject_reason
FROM raw.plan_changes pc
LEFT JOIN stg_subscriptions s USING (subscription_id)
CROSS JOIN meta_run r
WHERE s.subscription_id IS NULL
   OR pc.effective_date < s.start_date
   OR (s.end_date IS NOT NULL AND pc.effective_date >= s.end_date)
   OR pc.effective_date > r.as_of_date;

CREATE OR REPLACE TABLE stg_plan_changes AS
SELECT
    pc.change_id,
    pc.subscription_id,
    pc.customer_id,
    pc.requested_date,
    pc.effective_date,
    pc.change_type,
    pc.change_source,
    pc.from_plan_id,
    pc.to_plan_id,
    pf.tier_rank AS from_tier,
    pt.tier_rank AS to_tier,
    pc.from_seats,
    pc.to_seats,
    pc.from_billing_period,
    pc.to_billing_period,
    pc.from_unit_price,
    pc.to_unit_price,
    pc.from_discount_pct,
    pc.to_discount_pct,
    ROUND(mrr_of(pc.from_seats, pc.from_billing_period, pc.from_unit_price, pc.from_discount_pct), 2) AS from_mrr,
    ROUND(mrr_of(pc.to_seats,   pc.to_billing_period,   pc.to_unit_price,   pc.to_discount_pct),   2) AS to_mrr
FROM raw.plan_changes pc
JOIN stg_subscriptions s USING (subscription_id)
JOIN stg_plans pf ON pf.plan_id = pc.from_plan_id
JOIN stg_plans pt ON pt.plan_id = pc.to_plan_id
CROSS JOIN meta_run r
WHERE pc.effective_date >= s.start_date
  AND (s.end_date IS NULL OR pc.effective_date < s.end_date)
  AND pc.effective_date <= r.as_of_date;
