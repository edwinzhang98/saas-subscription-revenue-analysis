-- =============================================================================
-- 00_load_raw.sql
-- Purpose : Load the six synthetic source tables into the warehouse `raw` schema,
--           record the reporting as-of date, and define the MRR formula once.
-- Inputs  : <raw_dir>/{plans,customers,subscriptions,plan_changes,invoices,usage_events}.parquet
--           DuckDB variables: raw_dir (required), as_of_date (optional, default 2026-09-30)
-- Outputs : raw.plans, raw.customers, raw.subscriptions, raw.plan_changes, raw.invoices,
--           raw.usage_events, meta_run, macro mrr_of(...)
-- Run     : duckdb <warehouse.duckdb> -cmd "SET VARIABLE raw_dir='<data_dir>/raw'" < sql/00_load_raw.sql
--           (or: python -m saas_rev.warehouse, which sets the variables from config.yaml)
-- =============================================================================
CREATE SCHEMA IF NOT EXISTS raw;

CREATE OR REPLACE TABLE raw.plans         AS SELECT * FROM read_parquet(getvariable('raw_dir') || '/plans.parquet');
CREATE OR REPLACE TABLE raw.customers     AS SELECT * FROM read_parquet(getvariable('raw_dir') || '/customers.parquet');
CREATE OR REPLACE TABLE raw.subscriptions AS SELECT * FROM read_parquet(getvariable('raw_dir') || '/subscriptions.parquet');
CREATE OR REPLACE TABLE raw.plan_changes  AS SELECT * FROM read_parquet(getvariable('raw_dir') || '/plan_changes.parquet');
CREATE OR REPLACE TABLE raw.invoices      AS SELECT * FROM read_parquet(getvariable('raw_dir') || '/invoices.parquet');
CREATE OR REPLACE TABLE raw.usage_events  AS SELECT * FROM read_parquet(getvariable('raw_dir') || '/usage_events.parquet');

-- Reporting parameters (one row). Downstream files read the as-of date from here,
-- so they can be run on their own without session variables.
CREATE OR REPLACE TABLE meta_run AS
SELECT
    COALESCE(TRY_CAST(getvariable('as_of_date') AS DATE), DATE '2026-09-30') AS as_of_date,
    DATE '2023-10-01'                                                       AS window_start;

-- The MRR formula (02_metric_definitions.md §3), defined once and reused everywhere:
-- seats x monthly-equivalent unit price x (1 - discount). Annual unit prices are per year -> / 12.
CREATE OR REPLACE MACRO mrr_of(seats, billing_period, unit_price, discount_pct) AS
    seats * CASE WHEN billing_period = 'annual' THEN unit_price / 12.0 ELSE unit_price END * (1 - discount_pct);
