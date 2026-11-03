-- =============================================================================
-- 22_fct_mrr_movement_components.sql
-- Purpose : Split every plan change into signed components (plan, seats, billing period,
--           discount) and attribute continuing customers' monthly net change to them.
-- Inputs  : stg_plan_changes, stg_plans, fct_mrr_movements
-- Outputs : fct_change_components (one row per plan change),
--           fct_mrr_movement_components (one row per continuing customer-month)
-- Rules   : 02_metric_definitions.md §4.1 — change one attribute at a time in the order
--           plan -> seats -> billing period -> discount; every step rounded to cents, so the
--           four parts sum exactly to the change's delta MRR. other = net change - components.
-- Run     : duckdb <warehouse.duckdb> < sql/22_fct_mrr_movement_components.sql
-- =============================================================================
CREATE OR REPLACE TABLE fct_change_components AS
WITH steps AS (
    SELECT
        pc.*,
        last_day(pc.effective_date) AS month_end,
        -- unit price of the new plan on the OLD billing period (Enterprise has no monthly price:
        -- its monthly-equivalent list price annual / 12 is used); unchanged plan keeps its own price
        CASE
            WHEN pc.to_plan_id = pc.from_plan_id THEN pc.from_unit_price
            WHEN pc.from_billing_period = 'annual' THEN pt.annual_price_per_seat
            ELSE COALESCE(pt.monthly_price_per_seat, pt.annual_price_per_seat / 12.0)
        END AS step_unit_price
    FROM stg_plan_changes pc
    JOIN stg_plans pt ON pt.plan_id = pc.to_plan_id
),
m AS (
    SELECT
        *,
        from_mrr                                                                                   AS m0,
        ROUND(mrr_of(from_seats, from_billing_period, step_unit_price, from_discount_pct), 2)       AS m1,
        ROUND(mrr_of(to_seats,   from_billing_period, step_unit_price, from_discount_pct), 2)       AS m2,
        ROUND(mrr_of(to_seats,   to_billing_period,   to_unit_price,   from_discount_pct), 2)       AS m3,
        to_mrr                                                                                     AS m4
    FROM steps
)
SELECT
    change_id, subscription_id, customer_id, effective_date, month_end, change_type, change_source,
    from_plan_id, to_plan_id, from_seats, to_seats, from_billing_period, to_billing_period,
    from_discount_pct, to_discount_pct, from_mrr, to_mrr,
    to_mrr - from_mrr AS delta_mrr,
    m1 - m0 AS comp_plan,
    m2 - m1 AS comp_seats,
    m3 - m2 AS comp_billing_period,
    m4 - m3 AS comp_discount
FROM m;

CREATE OR REPLACE TABLE fct_mrr_movement_components AS
WITH comp AS (
    SELECT customer_id, month_end,
           SUM(comp_plan)           AS comp_plan,
           SUM(comp_seats)          AS comp_seats,
           SUM(comp_billing_period) AS comp_billing_period,
           SUM(comp_discount)       AS comp_discount,
           COUNT(*)                 AS change_rows,
           BOOL_OR(change_source = 'customer_success') AS has_cs_save,
           BOOL_OR(change_source = 'system')           AS has_forced_upgrade,
           BOOL_OR(change_source = 'renewal')          AS has_renewal_change
    FROM fct_change_components
    GROUP BY 1, 2
)
SELECT
    mv.customer_id, mv.month_end, mv.quarter_label, mv.movement_type,
    mv.mrr_prev, mv.mrr, mv.net_change,
    COALESCE(c.comp_plan, 0)            AS comp_plan,
    COALESCE(c.comp_seats, 0)           AS comp_seats,
    COALESCE(c.comp_billing_period, 0)  AS comp_billing_period,
    COALESCE(c.comp_discount, 0)        AS comp_discount,
    ROUND(mv.net_change - COALESCE(c.comp_plan, 0) - COALESCE(c.comp_seats, 0)
          - COALESCE(c.comp_billing_period, 0) - COALESCE(c.comp_discount, 0), 2) AS comp_other,
    COALESCE(c.change_rows, 0)          AS change_rows,
    COALESCE(c.has_cs_save, FALSE)      AS has_cs_save,
    COALESCE(c.has_forced_upgrade, FALSE) AS has_forced_upgrade,
    COALESCE(c.has_renewal_change, FALSE) AS has_renewal_change,
    mv.segment, mv.industry, mv.acquisition_channel, mv.region, mv.cs_coverage
FROM fct_mrr_movements mv
LEFT JOIN comp c ON c.customer_id = mv.customer_id AND c.month_end = mv.month_end
WHERE mv.mrr_prev > 0 AND mv.mrr > 0;
