-- =============================================================================
-- 03_stg_plan_changes.sql
-- Purpose : Plan / seat / billing / discount changes for real customers, with the MRR
--           before and after each change (using the mrr_of macro).
-- Inputs  : raw.plan_changes, stg_subscriptions, stg_plans, meta_run, macro mrr_of
-- Outputs : stg_plan_changes (one row per change)
-- =============================================================================
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
    mrr_of(pc.from_seats, pc.from_billing_period, pc.from_unit_price, pc.from_discount_pct) AS from_mrr,
    mrr_of(pc.to_seats,   pc.to_billing_period,   pc.to_unit_price,   pc.to_discount_pct)   AS to_mrr,
    mrr_of(pc.to_seats,   pc.to_billing_period,   pc.to_unit_price,   pc.to_discount_pct)
      - mrr_of(pc.from_seats, pc.from_billing_period, pc.from_unit_price, pc.from_discount_pct) AS mrr_delta
FROM raw.plan_changes pc
JOIN stg_subscriptions s USING (subscription_id)
JOIN stg_plans pf ON pf.plan_id = pc.from_plan_id
JOIN stg_plans pt ON pt.plan_id = pc.to_plan_id
CROSS JOIN meta_run r
WHERE pc.effective_date <= r.as_of_date;
