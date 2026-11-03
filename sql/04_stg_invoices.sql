-- =============================================================================
-- 04_stg_invoices.sql
-- Purpose : Invoice lines for real customers, excluding voided lines.
-- Inputs  : raw.invoices, stg_customers
-- Outputs : stg_invoices (one row per non-void invoice line)
-- Notes   : `uncollectible` lines stay (they were real charges; reported separately).
--           Amounts are pre-tax and net of discount; tax is excluded from every revenue metric.
-- =============================================================================
CREATE OR REPLACE TABLE stg_invoices AS
SELECT
    i.invoice_line_id,
    i.invoice_id,
    i.customer_id,
    i.subscription_id,
    i.invoice_date,
    i.line_type,
    i.description,
    i.service_period_start,
    i.service_period_end,
    i.amount_usd,
    i.tax_usd,
    i.status,
    i.paid_date,
    i.payment_attempts
FROM raw.invoices i
JOIN stg_customers c USING (customer_id)
WHERE NOT c.is_internal
  AND i.status <> 'void';
