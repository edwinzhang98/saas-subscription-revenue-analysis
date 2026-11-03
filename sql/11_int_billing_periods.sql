-- =============================================================================
-- 11_int_billing_periods.sql
-- Purpose : Billing periods per subscription from recurring invoice lines, and the next
--           renewal / billing date of every subscription live at the as-of date.
-- Inputs  : stg_invoices, stg_subscriptions, meta_run
-- Outputs : int_billing_periods (one row per recurring period),
--           int_next_renewal (one row per subscription live at as-of: next_renewal_date)
-- Notes   : the period covering the as-of date ends on the next billing date (monthly) or the
--           annual renewal date (annual). Used by the CS account health list (02 §15).
-- Run     : duckdb <warehouse.duckdb> < sql/11_int_billing_periods.sql
-- =============================================================================
CREATE OR REPLACE TABLE int_billing_periods AS
SELECT
    i.subscription_id,
    i.customer_id,
    i.service_period_start AS period_start,
    i.service_period_end   AS period_end,
    CASE WHEN date_diff('day', i.service_period_start, i.service_period_end) > 40 THEN 'annual' ELSE 'monthly' END
                           AS billing_period,
    i.amount_usd,
    i.status
FROM stg_invoices i
WHERE i.line_type = 'recurring';

CREATE OR REPLACE TABLE int_next_renewal AS
SELECT
    s.subscription_id,
    s.customer_id,
    MAX(bp.period_end) AS next_renewal_date,
    arg_max(bp.billing_period, bp.period_end) AS current_billing_period
FROM stg_subscriptions s
JOIN int_billing_periods bp USING (subscription_id)
CROSS JOIN meta_run r
WHERE s.end_date IS NULL
  AND bp.period_start <= r.as_of_date
GROUP BY 1, 2;
