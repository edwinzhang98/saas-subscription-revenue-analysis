-- =============================================================================
-- 01_stg_customers.sql
-- Purpose : Clean customer and plan dimensions; flag internal / test accounts,
--           duplicate CRM records and segment / employee-count mismatches.
-- Inputs  : raw.customers, raw.plans, raw.subscriptions
-- Outputs : stg_plans, stg_customers (one row per account)
-- Rules   : 02_metric_definitions.md §2. Internal / test accounts: internal email domain or a
--           whole-token test-style name. The CRM has no is_internal flag, so it is derived here.
-- Run     : duckdb <warehouse.duckdb> < sql/01_stg_customers.sql
-- =============================================================================
CREATE OR REPLACE TABLE stg_plans AS
SELECT
    plan_id,
    plan_name,
    tier_rank,
    monthly_price_per_seat,
    annual_price_per_seat,
    COALESCE(monthly_price_per_seat, annual_price_per_seat / 12.0) AS list_price_per_seat_month,  -- billing-neutral
    min_seats,
    max_seats
FROM raw.plans;

CREATE OR REPLACE TABLE stg_customers AS
WITH base AS (
    SELECT
        c.*,
        EXISTS (SELECT 1 FROM raw.subscriptions s WHERE s.customer_id = c.customer_id) AS has_subscription
    FROM raw.customers c
)
SELECT
    customer_id,
    company_name,
    email_domain,
    COALESCE(industry, 'Unknown')                                   AS industry,
    industry IS NULL                                                 AS industry_missing,
    employee_count,
    segment,
    region,
    acquisition_channel,
    created_at,
    is_design_partner,
    cs_coverage,
    csm_owner,
    has_subscription,
    (email_domain = 'ourcompany.example'
     OR regexp_matches(company_name, '(?i)(\btest\b|\[internal\]|^internal\b|\bqa\b|zz_test)')) AS is_internal,
    -- a second CRM record for the same company that never bought anything
    (NOT has_subscription
     AND COUNT(*) OVER (PARTITION BY email_domain) > 1)              AS is_duplicate_record,
    -- segment is the system of record; employee_count is enriched later and can drift
    (employee_count IS NOT NULL AND NOT (
        (segment = 'SMB'        AND employee_count <= 100) OR
        (segment = 'Mid-Market' AND employee_count BETWEEN 101 AND 1000) OR
        (segment = 'Enterprise' AND employee_count > 1000)))          AS employee_band_mismatch
FROM base;
