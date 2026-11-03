-- =============================================================================
-- 01_stg_customers.sql
-- Purpose : Clean customer and plan dimensions; flag internal / test accounts.
-- Inputs  : raw.customers, raw.plans
-- Outputs : stg_customers (one row per account, incl. is_internal flag), stg_plans
-- Rules   : 02_metric_definitions.md §2 — internal / test accounts are identified by the
--           internal email domain or test-style names, because the CRM has no is_internal flag.
-- =============================================================================
CREATE OR REPLACE TABLE stg_plans AS
SELECT plan_id, plan_name, tier_rank, monthly_price_per_seat, annual_price_per_seat, min_seats, max_seats
FROM raw.plans;

CREATE OR REPLACE TABLE stg_customers AS
SELECT
    customer_id,
    company_name,
    email_domain,
    COALESCE(industry, 'Unknown')                    AS industry,
    industry IS NULL                                  AS industry_missing,
    employee_count,
    segment,
    region,
    acquisition_channel,
    created_at,
    is_design_partner,
    cs_coverage,
    csm_owner,
    (email_domain = 'ourcompany.example'
     OR regexp_matches(lower(company_name), '^(test|\[internal\]|internal -|qa sandbox|zz_test)')) AS is_internal
FROM raw.customers;
