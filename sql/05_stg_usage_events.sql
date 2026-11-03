-- =============================================================================
-- 05_stg_usage_events.sql
-- Purpose : De-duplicated weekly usage aggregates for real customers.
-- Inputs  : raw.usage_events, stg_customers, meta_run
-- Outputs : stg_usage_events (one row per customer x week_start x event_name)
-- Notes   : the product-analytics export occasionally double-loads a batch; exact duplicates
--           are removed. is_complete_week = the whole Mon-Sun week is on or before the as-of date.
-- Run     : duckdb <warehouse.duckdb> < sql/05_stg_usage_events.sql
-- =============================================================================
CREATE OR REPLACE TABLE stg_usage_events AS
SELECT
    u.customer_id,
    u.week_start,
    u.event_name,
    u.event_count,
    u.unique_users,
    (u.week_start + INTERVAL 6 DAY)::DATE <= r.as_of_date AS is_complete_week
FROM raw.usage_events u
JOIN stg_customers c USING (customer_id)
CROSS JOIN meta_run r
WHERE NOT c.is_internal
  AND u.week_start <= r.as_of_date
QUALIFY ROW_NUMBER() OVER (
    PARTITION BY u.customer_id, u.week_start, u.event_name
    ORDER BY u.event_count DESC, u.unique_users DESC) = 1;
