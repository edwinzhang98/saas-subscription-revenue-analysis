-- =============================================================================
-- 02_stg_subscriptions.sql
-- Purpose : Subscriptions of real (non-internal) customers, within the reporting window.
-- Inputs  : raw.subscriptions, stg_customers, meta_run
-- Outputs : stg_subscriptions (one row per subscription)
-- Notes   : end_date is EXCLUSIVE (first day without service). Anything after the as-of
--           date is treated as unknown: end dates after as-of are nulled (still live at as-of).
-- =============================================================================
CREATE OR REPLACE TABLE stg_subscriptions AS
SELECT
    s.subscription_id,
    s.customer_id,
    s.start_date,
    CASE WHEN s.end_date > r.as_of_date THEN NULL ELSE s.end_date END AS end_date,
    s.status,
    s.cancel_requested_date,
    s.cancel_reason,
    s.initial_plan_id,
    s.initial_seats,
    s.initial_billing_period,
    s.initial_unit_price,
    s.initial_discount_pct,
    s.payment_method,
    ROW_NUMBER() OVER (PARTITION BY s.customer_id ORDER BY s.start_date, s.subscription_id) AS subscription_seq
FROM raw.subscriptions s
JOIN stg_customers c USING (customer_id)
CROSS JOIN meta_run r
WHERE NOT c.is_internal
  AND s.start_date <= r.as_of_date;
