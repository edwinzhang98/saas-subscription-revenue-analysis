-- =============================================================================
-- 10_int_subscription_versions.sql
-- Purpose : Subscription terms over time (SCD type 2): one row per period during which a
--           subscription's plan, seats, billing period, unit price and discount were constant.
-- Inputs  : stg_subscriptions, stg_plan_changes, stg_plans, macro mrr_of
-- Outputs : int_subscription_versions (subscription_id, version_no, valid_from, valid_to (exclusive),
--           terms, mrr, mrr_norm)
-- Rules   : 02_metric_definitions.md §3 — rows applied in order (effective_date, requested_date,
--           change_id); MRR rounded to cents; zero-length versions (several changes on one day,
--           or a change on the start date) are dropped.
-- Run     : duckdb <warehouse.duckdb> < sql/10_int_subscription_versions.sql
-- =============================================================================
CREATE OR REPLACE TABLE int_subscription_versions AS
WITH events AS (
    SELECT s.subscription_id, s.customer_id, s.start_date AS valid_from,
           s.initial_plan_id AS plan_id, s.initial_seats AS seats, s.initial_billing_period AS billing_period,
           s.initial_unit_price AS unit_price, s.initial_discount_pct AS discount_pct,
           0 AS sort_group, NULL::DATE AS requested_date, NULL::VARCHAR AS change_id, 'start' AS version_reason,
           NULL::VARCHAR AS change_source
    FROM stg_subscriptions s
    UNION ALL
    SELECT pc.subscription_id, pc.customer_id, pc.effective_date,
           pc.to_plan_id, pc.to_seats, pc.to_billing_period, pc.to_unit_price, pc.to_discount_pct,
           1, pc.requested_date, pc.change_id, pc.change_type, pc.change_source
    FROM stg_plan_changes pc
),
ordered AS (
    SELECT *,
           ROW_NUMBER() OVER (PARTITION BY subscription_id
                              ORDER BY valid_from, sort_group, requested_date, change_id) AS version_no
    FROM events
),
bounded AS (
    SELECT o.*,
           LEAD(o.valid_from) OVER (PARTITION BY o.subscription_id ORDER BY o.version_no) AS next_valid_from,
           s.end_date
    FROM ordered o
    JOIN stg_subscriptions s USING (subscription_id)
)
SELECT
    b.subscription_id,
    b.customer_id,
    b.version_no,
    b.version_reason,
    b.change_id,
    b.change_source,
    b.valid_from,
    LEAST(COALESCE(b.next_valid_from, DATE '9999-12-31'), COALESCE(b.end_date, DATE '9999-12-31')) AS valid_to,
    b.plan_id,
    p.tier_rank,
    b.seats,
    b.billing_period,
    b.unit_price,
    b.discount_pct,
    ROUND(mrr_of(b.seats, b.billing_period, b.unit_price, b.discount_pct), 2)        AS mrr,
    ROUND(b.seats * p.list_price_per_seat_month * (1 - b.discount_pct), 2)            AS mrr_norm
FROM bounded b
JOIN stg_plans p USING (plan_id)
WHERE b.valid_from < LEAST(COALESCE(b.next_valid_from, DATE '9999-12-31'), COALESCE(b.end_date, DATE '9999-12-31'));
