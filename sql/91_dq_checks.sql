-- =============================================================================
-- 91_dq_checks.sql
-- Purpose : Data-quality checks on sources and models: row-count reconciliation, primary keys,
--           nulls, referential integrity, value ranges / outliers, time ranges, metric
--           identities and the known blind spots of month-end snapshots.
-- Inputs  : raw.*, stg_*, int_subscription_versions, fct_customer_mrr_monthly, fct_mrr_movements,
--           fct_change_components, fct_mrr_movement_components, rpt_mrr_waterfall_monthly,
--           dq_revenue_reconciliation, fct_account_usage_weekly, meta_run
-- Outputs : dq_check_results (check_id, category, check_name, observed, expected, status, note)
-- Rules   : status = 'pass' / 'fail' for hard checks, 'info' for monitored quantities.
--           Written up in docs/03_data_quality_checks.md.
-- Run     : duckdb <warehouse.duckdb> < sql/91_dq_checks.sql
-- =============================================================================
CREATE OR REPLACE TABLE dq_check_results AS
WITH r AS (SELECT * FROM meta_run),
chain AS (
    -- chaining invariant: each change's from_* equals the previous terms (02 §3)
    SELECT
        pc.change_id,
        LAG(pc.to_plan_id)          OVER w AS prev_plan,
        LAG(pc.to_seats)            OVER w AS prev_seats,
        LAG(pc.to_billing_period)   OVER w AS prev_billing,
        LAG(pc.to_unit_price)       OVER w AS prev_price,
        LAG(pc.to_discount_pct)     OVER w AS prev_discount,
        ROW_NUMBER()                OVER w AS rn,
        pc.from_plan_id, pc.from_seats, pc.from_billing_period, pc.from_unit_price, pc.from_discount_pct,
        s.initial_plan_id, s.initial_seats, s.initial_billing_period, s.initial_unit_price, s.initial_discount_pct
    FROM stg_plan_changes pc
    JOIN stg_subscriptions s USING (subscription_id)
    WINDOW w AS (PARTITION BY pc.subscription_id ORDER BY pc.effective_date, pc.requested_date, pc.change_id)
),
chain_violations AS (
    SELECT COUNT(*) AS n FROM chain
    WHERE (rn = 1 AND (from_plan_id <> initial_plan_id OR from_seats <> initial_seats
                       OR from_billing_period <> initial_billing_period OR from_unit_price <> initial_unit_price
                       OR from_discount_pct <> initial_discount_pct))
       OR (rn > 1 AND (from_plan_id <> prev_plan OR from_seats <> prev_seats OR from_billing_period <> prev_billing
                       OR from_unit_price <> prev_price OR from_discount_pct <> prev_discount))
),
overlap_check AS (
    SELECT COUNT(*) AS n FROM stg_subscriptions a JOIN stg_subscriptions b
      ON a.customer_id = b.customer_id AND a.subscription_id < b.subscription_id
     AND a.start_date < COALESCE(b.end_date, DATE '9999-12-31') AND b.start_date < COALESCE(a.end_date, DATE '9999-12-31')
),
same_month AS (
    -- subscriptions that never spanned a month-end (start and end inside one calendar month)
    SELECT COUNT(*) AS n FROM stg_subscriptions
    WHERE end_date IS NOT NULL AND last_day(start_date) = last_day(end_date - INTERVAL 1 DAY)
),
list_price AS (
    SELECT COUNT(*) AS n FROM int_subscription_versions v JOIN stg_plans p USING (plan_id)
    WHERE v.unit_price <> CASE WHEN v.billing_period = 'annual' THEN p.annual_price_per_seat ELSE p.monthly_price_per_seat END
),
internal_mrr AS (
    -- MRR the internal accounts would add at the as-of date if they were not excluded
    SELECT COALESCE(SUM(ROUND(mrr_of(COALESCE(pc.to_seats, s.initial_seats),
                                      COALESCE(pc.to_billing_period, s.initial_billing_period),
                                      COALESCE(pc.to_unit_price, s.initial_unit_price),
                                      COALESCE(pc.to_discount_pct, s.initial_discount_pct)), 2)), 0) AS mrr
    FROM raw.subscriptions s
    JOIN stg_customers c ON c.customer_id = s.customer_id AND c.is_internal
    LEFT JOIN (SELECT * FROM raw.plan_changes QUALIFY ROW_NUMBER() OVER (PARTITION BY subscription_id ORDER BY effective_date DESC, change_id DESC) = 1) pc
      ON pc.subscription_id = s.subscription_id
    CROSS JOIN r
    WHERE s.start_date <= r.as_of_date AND (s.end_date IS NULL OR s.end_date > r.as_of_date)
),
checks AS (
    -- ---------------- row counts / reconciliation ----------------
    SELECT 'RC01' AS check_id, 'row_count' AS category, 'raw customers = staged customers' AS check_name,
           (SELECT COUNT(*) FROM raw.customers) - (SELECT COUNT(*) FROM stg_customers) AS observed, '0' AS expected, NULL AS note
    UNION ALL SELECT 'RC02', 'row_count', 'raw subscriptions - internal subscriptions - staged subscriptions',
           (SELECT COUNT(*) FROM raw.subscriptions)
           - (SELECT COUNT(*) FROM raw.subscriptions s JOIN stg_customers c USING (customer_id) WHERE c.is_internal)
           - (SELECT COUNT(*) FROM stg_subscriptions), '0', NULL
    UNION ALL SELECT 'RC03', 'row_count', 'raw plan_changes - staged - rejected',
           (SELECT COUNT(*) FROM raw.plan_changes) - (SELECT COUNT(*) FROM stg_plan_changes)
           - (SELECT COUNT(*) FROM stg_plan_changes_rejected), '0', NULL
    UNION ALL SELECT 'RC04', 'row_count', 'raw invoice lines - void - internal non-void - staged',
           (SELECT COUNT(*) FROM raw.invoices) - (SELECT COUNT(*) FROM raw.invoices WHERE status = 'void')
           - (SELECT COUNT(*) FROM raw.invoices i JOIN stg_customers c USING (customer_id) WHERE c.is_internal AND i.status <> 'void')
           - (SELECT COUNT(*) FROM stg_invoices), '0', NULL
    UNION ALL SELECT 'RC05', 'row_count', 'staged usage rows = distinct (customer, week, event) of real customers',
           (SELECT COUNT(*) FROM stg_usage_events)
           - (SELECT COUNT(*) FROM (SELECT DISTINCT u.customer_id, u.week_start, u.event_name FROM raw.usage_events u
                JOIN stg_customers c USING (customer_id) CROSS JOIN r WHERE NOT c.is_internal AND u.week_start <= r.as_of_date)), '0', NULL
    -- ---------------- primary keys ----------------
    UNION ALL SELECT 'PK01', 'primary_key', 'customers.customer_id duplicates',
           (SELECT COUNT(*) - COUNT(DISTINCT customer_id) FROM raw.customers), '0', NULL
    UNION ALL SELECT 'PK02', 'primary_key', 'subscriptions.subscription_id duplicates',
           (SELECT COUNT(*) - COUNT(DISTINCT subscription_id) FROM raw.subscriptions), '0', NULL
    UNION ALL SELECT 'PK03', 'primary_key', 'plan_changes.change_id duplicates',
           (SELECT COUNT(*) - COUNT(DISTINCT change_id) FROM raw.plan_changes), '0', NULL
    UNION ALL SELECT 'PK04', 'primary_key', 'invoices.invoice_line_id duplicates',
           (SELECT COUNT(*) - COUNT(DISTINCT invoice_line_id) FROM raw.invoices), '0', NULL
    UNION ALL SELECT 'PK05', 'primary_key', 'raw usage rows duplicated on (customer, week, event) — removed in staging',
           (SELECT COUNT(*) FROM raw.usage_events) - (SELECT COUNT(*) FROM (SELECT DISTINCT customer_id, week_start, event_name FROM raw.usage_events)),
           '~0.4% of rows', 'pipeline double-loads; de-duplicated in 05_stg_usage_events'
    UNION ALL SELECT 'PK06', 'primary_key', 'fct_customer_mrr_monthly (customer, month) duplicates',
           (SELECT COUNT(*) - COUNT(DISTINCT (customer_id, month_end)) FROM fct_customer_mrr_monthly), '0', NULL
    UNION ALL SELECT 'PK07', 'primary_key', 'fct_account_usage_weekly (customer, week) duplicates',
           (SELECT COUNT(*) - COUNT(DISTINCT (customer_id, week_start)) FROM fct_account_usage_weekly), '0', NULL
    -- ---------------- nulls ----------------
    UNION ALL SELECT 'NL01', 'nulls', 'null keys or dates in subscriptions (id, customer, start, plan, seats)',
           (SELECT COUNT(*) FROM raw.subscriptions WHERE subscription_id IS NULL OR customer_id IS NULL OR start_date IS NULL
                OR initial_plan_id IS NULL OR initial_seats IS NULL), '0', NULL
    UNION ALL SELECT 'NL02', 'nulls', 'null keys or dates in plan_changes',
           (SELECT COUNT(*) FROM raw.plan_changes WHERE change_id IS NULL OR subscription_id IS NULL OR effective_date IS NULL
                OR to_plan_id IS NULL OR to_seats IS NULL), '0', NULL
    UNION ALL SELECT 'NL03', 'nulls', 'null keys or amounts in invoices',
           (SELECT COUNT(*) FROM raw.invoices WHERE invoice_line_id IS NULL OR customer_id IS NULL OR amount_usd IS NULL
                OR invoice_date IS NULL), '0', NULL
    UNION ALL SELECT 'NL04', 'nulls', 'customers with missing industry (bucketed as Unknown)',
           (SELECT COUNT(*) FROM stg_customers WHERE industry_missing AND NOT is_internal), '~2% of self-serve SMB', 'not asked at self-serve signup'
    UNION ALL SELECT 'NL05', 'nulls', 'customers with missing employee_count',
           (SELECT COUNT(*) FROM stg_customers WHERE employee_count IS NULL AND NOT is_internal), '~3%', 'segment is always present and is used instead'
    -- ---------------- referential integrity ----------------
    UNION ALL SELECT 'RI01', 'referential', 'subscriptions without a customer',
           (SELECT COUNT(*) FROM raw.subscriptions s LEFT JOIN raw.customers c USING (customer_id) WHERE c.customer_id IS NULL), '0', NULL
    UNION ALL SELECT 'RI02', 'referential', 'plan_changes without a subscription',
           (SELECT COUNT(*) FROM raw.plan_changes p LEFT JOIN raw.subscriptions s USING (subscription_id) WHERE s.subscription_id IS NULL), '0', NULL
    UNION ALL SELECT 'RI03', 'referential', 'invoice lines without a subscription',
           (SELECT COUNT(*) FROM raw.invoices i LEFT JOIN raw.subscriptions s USING (subscription_id) WHERE s.subscription_id IS NULL), '0', NULL
    UNION ALL SELECT 'RI04', 'referential', 'usage rows for unknown customers',
           (SELECT COUNT(*) FROM raw.usage_events u LEFT JOIN raw.customers c USING (customer_id) WHERE c.customer_id IS NULL), '0', NULL
    UNION ALL SELECT 'RI05', 'referential', 'plan_changes customer_id differs from its subscription''s customer',
           (SELECT COUNT(*) FROM raw.plan_changes p JOIN raw.subscriptions s USING (subscription_id) WHERE p.customer_id <> s.customer_id), '0', NULL
    -- ---------------- value ranges / outliers ----------------
    UNION ALL SELECT 'VR01', 'value_range', 'versions with seats < 1 or discount outside [0, 1)',
           (SELECT COUNT(*) FROM int_subscription_versions WHERE seats < 1 OR discount_pct < 0 OR discount_pct >= 1), '0', NULL
    UNION ALL SELECT 'VR02', 'value_range', 'versions whose unit price differs from the list price for plan + billing period',
           (SELECT n FROM list_price), '0', 'all deals use list price; negotiation is in discount_pct'
    UNION ALL SELECT 'VR03', 'value_range', 'negative invoice amounts',
           (SELECT COUNT(*) FROM raw.invoices WHERE amount_usd < 0), '0', NULL
    UNION ALL SELECT 'VR04', 'value_range', 'largest single customer share of MRR at as-of (%)',
           (SELECT 100 * MAX(mrr) / SUM(mrr) FROM fct_customer_mrr_monthly WHERE month_end = (SELECT as_of_date FROM r)),
           '< 5%', 'concentration check'
    UNION ALL SELECT 'VR05', 'value_range', 'account-weeks with WAU above seats (%)',
           (SELECT 100 * AVG((wau > seats)::INT) FROM fct_account_usage_weekly WHERE is_live_at_week_end AND is_complete_week),
           'rare (< 1%)', 'overage tolerated up to 10%, never billed — kept as a signal'
    UNION ALL SELECT 'VR06', 'value_range', 'account-weeks with WAU above 110% of seats',
           (SELECT COUNT(*) FROM fct_account_usage_weekly WHERE is_live_at_week_end AND wau > 1.1 * seats + 1), '0', 'beyond the tolerated overage'
    UNION ALL SELECT 'VR07', 'value_range', 'customers with employee_count outside their segment band',
           (SELECT COUNT(*) FROM stg_customers WHERE employee_band_mismatch AND NOT is_internal), '~3%', 'segment (set by Sales Ops at signup) is the system of record'
    -- ---------------- time ranges ----------------
    UNION ALL SELECT 'TR01', 'time_range', 'subscriptions starting before GA (2023-10-02)',
           (SELECT COUNT(*) FROM raw.subscriptions WHERE start_date < DATE '2023-10-02'), '0', NULL
    UNION ALL SELECT 'TR02', 'time_range', 'events after the as-of date (subscriptions, changes, invoices)',
           (SELECT COUNT(*) FROM raw.subscriptions WHERE start_date > (SELECT as_of_date FROM r))
           + (SELECT COUNT(*) FROM raw.plan_changes WHERE effective_date > (SELECT as_of_date FROM r))
           + (SELECT COUNT(*) FROM raw.invoices WHERE invoice_date > (SELECT as_of_date FROM r)), '0', NULL
    UNION ALL SELECT 'TR03', 'time_range', 'usage weeks outside [GA week, as-of week]',
           (SELECT COUNT(*) FROM raw.usage_events WHERE week_start < DATE '2023-10-02' OR week_start > (SELECT as_of_date FROM r)), '0', NULL
    UNION ALL SELECT 'TR04', 'time_range', 'partial final usage week rows (excluded from trends)',
           (SELECT COUNT(*) FROM stg_usage_events WHERE NOT is_complete_week), 'info', 'week of 2026-09-28 has 3 of 7 days'
    UNION ALL SELECT 'TR05', 'time_range', 'usage rows in weeks after the subscription ended (grace period)',
           (SELECT COUNT(*) FROM fct_account_usage_weekly WHERE NOT is_live_at_week_end AND wau > 0), 'info', 'kept in usage, never used for MRR'
    -- ---------------- model logic ----------------
    UNION ALL SELECT 'ML01', 'model_logic', 'plan-change chaining invariant violations',
           (SELECT n FROM chain_violations), '0', '02 §3'
    UNION ALL SELECT 'ML02', 'model_logic', 'plan changes rejected (effective on/after end date or before start)',
           (SELECT COUNT(*) FROM stg_plan_changes_rejected), '~0.2% of rows', 'keyed in after cancellation; dropped'
    UNION ALL SELECT 'ML03', 'model_logic', 'customers with overlapping live subscriptions',
           (SELECT n FROM overlap_check), '0', 'one live subscription per account'
    UNION ALL SELECT 'ML04', 'model_logic', 'waterfall identity: max |end - (start + movements)| over months ($)',
           (SELECT MAX(ABS(identity_gap)) FROM rpt_mrr_waterfall_monthly), '0.00', '02 §4'
    UNION ALL SELECT 'ML05', 'model_logic', 'components tie-out: |sum(components + other) - (expansion - contraction)| ($)',
           ABS((SELECT SUM(comp_plan + comp_seats + comp_billing_period + comp_discount + comp_other) FROM fct_mrr_movement_components)
               - (SELECT SUM(expansion_mrr) - SUM(contraction_mrr) FROM fct_mrr_movements)), '0.00', '02 §4.1'
    UNION ALL SELECT 'ML06', 'model_logic', 'change rows whose four components do not sum to delta MRR',
           (SELECT COUNT(*) FROM fct_change_components WHERE ABS(delta_mrr - comp_plan - comp_seats - comp_billing_period - comp_discount) > 0.001), '0', NULL
    UNION ALL SELECT 'ML07', 'model_logic', 'negative MRR customer-months',
           (SELECT COUNT(*) FROM fct_customer_mrr_monthly WHERE mrr < 0), '0', NULL
    UNION ALL SELECT 'ML08', 'model_logic', 'billing reconciliation gap, calendar 2025 (%)',
           (SELECT 100 * gap_pct FROM dq_revenue_reconciliation WHERE period_type = 'calendar_year' AND period = '2025'), '|gap| < 0.3%', '02 §14'
    UNION ALL SELECT 'ML09', 'model_logic', 'billing reconciliation gap, trailing 12 months (%)',
           (SELECT 100 * gap_pct FROM dq_revenue_reconciliation WHERE period_type = 'ttm'), '|gap| < 0.3%', '02 §14'
    -- ---------------- exclusions ----------------
    UNION ALL SELECT 'EX01', 'exclusion', 'internal / test accounts excluded',
           (SELECT COUNT(*) FROM stg_customers WHERE is_internal), '6', '02 §2 (domain + whole-token name rule)'
    UNION ALL SELECT 'EX02', 'exclusion', 'MRR the internal accounts would add at as-of if not excluded ($)',
           (SELECT mrr FROM internal_mrr), 'info', 'includes a 200-seat sandbox at list price'
    UNION ALL SELECT 'EX03', 'exclusion', 'duplicate CRM records (same domain, no subscription)',
           (SELECT COUNT(*) FROM stg_customers WHERE is_duplicate_record), '~0.3% of accounts', 'never paying, so no MRR impact'
    UNION ALL SELECT 'EX04', 'exclusion', 'void invoice lines excluded',
           (SELECT COUNT(*) FROM raw.invoices WHERE status = 'void'), '~0.5% + internal', NULL
    -- ---------------- known blind spots (02 §4) ----------------
    UNION ALL SELECT 'BS01', 'blind_spot', 'subscriptions that started and ended inside one calendar month (never on a month-end)',
           (SELECT n FROM same_month), 'info', 'invisible to month-end MRR ("subscribed and churned")'
    UNION ALL SELECT 'BS02', 'blind_spot', 'customer-months with a same-month win-back or mid-month residual (component "other")',
           (SELECT COUNT(*) FROM fct_mrr_movement_components WHERE ABS(comp_other) > 0.005), 'info', NULL
)
SELECT
    check_id, category, check_name,
    ROUND(CAST(observed AS DOUBLE), 4) AS observed,
    expected,
    CASE
        WHEN expected = '0' THEN CASE WHEN ROUND(CAST(observed AS DOUBLE), 6) = 0 THEN 'pass' ELSE 'fail' END
        WHEN expected = '0.00' THEN CASE WHEN ABS(observed) < 0.005 THEN 'pass' ELSE 'fail' END
        WHEN expected = '6' THEN CASE WHEN observed = 6 THEN 'pass' ELSE 'fail' END
        WHEN expected = '|gap| < 0.3%' THEN CASE WHEN ABS(observed) < 0.3 THEN 'pass' ELSE 'fail' END
        WHEN expected = '< 5%' THEN CASE WHEN observed < 5 THEN 'pass' ELSE 'fail' END
        ELSE 'info'
    END AS status,
    note
FROM checks
ORDER BY check_id;
