-- =============================================================================
-- 24_rpt_mrr_quarterly.sql
-- Purpose : Quarterly MRR movements and movement rates (denominator = MRR at the end of the
--           month before the quarter), plus Basic -> higher upgrade rates.
-- Inputs  : rpt_mrr_waterfall_monthly, fct_customer_mrr_monthly, stg_plan_changes, meta_run
-- Outputs : rpt_mrr_quarterly (one row per quarter),
--           fct_basic_upgrade_monthly (one row per customer on Basic at the end of m-1),
--           rpt_upgrade_rate_monthly (one row per month)
-- Rules   : 02_metric_definitions.md §5 (quarterly rates), §6 (upgrade rate; voluntary vs forced).
-- Run     : duckdb <warehouse.duckdb> < sql/24_rpt_mrr_quarterly.sql
-- =============================================================================
CREATE OR REPLACE TABLE rpt_mrr_quarterly AS
SELECT
    quarter_label,
    MIN(month_end)                                   AS first_month_end,
    MAX(month_end)                                   AS quarter_end,
    COUNT(*)                                         AS months_in_quarter,
    arg_min(mrr_start, month_end)                    AS mrr_start_of_quarter,
    arg_max(mrr_end, month_end)                      AS mrr_end_of_quarter,
    SUM(new_mrr)                                     AS new_mrr,
    SUM(expansion_mrr)                               AS expansion_mrr,
    SUM(reactivation_mrr)                            AS reactivation_mrr,
    SUM(contraction_mrr)                             AS contraction_mrr,
    SUM(churn_mrr)                                   AS churn_mrr,
    SUM(churn_mrr_involuntary)                       AS churn_mrr_involuntary,
    SUM(gross_seat_increase)                         AS gross_seat_increase,
    SUM(gross_seat_decrease)                         AS gross_seat_decrease,
    SUM(gross_plan_upgrade)                          AS gross_plan_upgrade,
    SUM(gross_plan_downgrade)                        AS gross_plan_downgrade,
    SUM(gross_billing_to_monthly)                    AS gross_billing_to_monthly,
    SUM(gross_billing_to_annual)                     AS gross_billing_to_annual,
    SUM(gross_discount_decrease)                     AS gross_discount_decrease,
    SUM(gross_discount_increase)                     AS gross_discount_increase,
    SUM(component_other)                             AS component_other,
    SUM(new_mrr)          / NULLIF(arg_min(mrr_start, month_end), 0) AS new_rate,
    SUM(expansion_mrr)    / NULLIF(arg_min(mrr_start, month_end), 0) AS expansion_rate,
    SUM(contraction_mrr)  / NULLIF(arg_min(mrr_start, month_end), 0) AS contraction_rate,
    SUM(churn_mrr)        / NULLIF(arg_min(mrr_start, month_end), 0) AS churn_rate,
    SUM(reactivation_mrr) / NULLIF(arg_min(mrr_start, month_end), 0) AS reactivation_rate,
    SUM(new_customers)                               AS new_customers,
    SUM(churned_customers)                           AS churned_customers
FROM rpt_mrr_waterfall_monthly
GROUP BY 1
ORDER BY 2;

CREATE OR REPLACE TABLE fct_basic_upgrade_monthly AS
WITH basic_base AS (
    SELECT
        f.customer_id,
        f.month_end                                           AS base_month_end,
        last_day(f.month_end + INTERVAL 1 MONTH)              AS month_end,
        f.seats, f.billing_period, f.mrr, f.months_since_first_paid,
        f.segment, f.industry, f.acquisition_channel, f.region, f.cohort_month_end
    FROM fct_customer_mrr_monthly f
    WHERE f.is_paying AND f.plan_id = 'basic'
),
ups AS (
    SELECT customer_id, last_day(effective_date) AS month_end,
           BOOL_OR(change_source <> 'system') AS voluntary_upgrade,
           BOOL_OR(change_source = 'system')  AS forced_upgrade
    FROM stg_plan_changes
    WHERE from_plan_id = 'basic' AND to_tier > from_tier
    GROUP BY 1, 2
)
SELECT
    b.*,
    COALESCE(u.voluntary_upgrade, FALSE)                          AS voluntary_upgrade,
    COALESCE(u.forced_upgrade AND NOT u.voluntary_upgrade, FALSE) AS forced_upgrade_only
FROM basic_base b
CROSS JOIN meta_run r
LEFT JOIN ups u ON u.customer_id = b.customer_id AND u.month_end = b.month_end
WHERE b.month_end <= r.as_of_date;

CREATE OR REPLACE TABLE rpt_upgrade_rate_monthly AS
SELECT
    month_end,
    COUNT(*)                                       AS basic_customers_start,
    COUNT(*) FILTER (WHERE voluntary_upgrade)      AS voluntary_upgrades,
    COUNT(*) FILTER (WHERE forced_upgrade_only)    AS forced_upgrades,
    AVG(voluntary_upgrade::INT)                    AS voluntary_upgrade_rate
FROM fct_basic_upgrade_monthly
GROUP BY 1
ORDER BY 1;
