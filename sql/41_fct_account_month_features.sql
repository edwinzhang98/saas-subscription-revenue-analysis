-- =============================================================================
-- 41_fct_account_month_features.sql
-- Purpose : One row per paying account-month-end t: behaviour in the 13 complete weeks ending
--           at t, seat / MRR trends, state flags, next renewal, and FUTURE outcome labels used
--           only for back-testing (all prefixed label_).
-- Inputs  : fct_customer_mrr_monthly, fct_mrr_movements, fct_mrr_movement_components,
--           fct_account_usage_weekly, stg_plan_changes, stg_subscriptions, int_billing_periods, meta_run
-- Outputs : fct_account_month_features (customer_id x month_end)
-- Rules   : 02_metric_definitions.md §9.1 (early-warning eligibility and outcome windows),
--           §12 (usage windows), §13 (upgrade labels). Features use only data observable at t.
-- Run     : duckdb <warehouse.duckdb> < sql/41_fct_account_month_features.sql
-- =============================================================================
CREATE OR REPLACE TABLE fct_account_month_features AS
WITH base AS (
    SELECT f.*,
           LAG(f.seats, 3)    OVER w AS seats_3m_ago,
           LAG(f.mrr, 3)      OVER w AS mrr_3m_ago,
           LAG(f.mrr_norm, 3) OVER w AS mrr_norm_3m_ago,
           MAX(f.mrr_norm)    OVER (PARTITION BY f.customer_id ORDER BY f.month_end
                                    ROWS BETWEEN 6 PRECEDING AND 1 PRECEDING) AS peak_mrr_norm_6m,
           MAX(f.tier_rank)   OVER (PARTITION BY f.customer_id ORDER BY f.month_end
                                    ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS max_tier_before
    FROM fct_customer_mrr_monthly f
    WINDOW w AS (PARTITION BY f.customer_id ORDER BY f.month_end)
),
usage AS (
    -- complete weeks only; windows anchored on the week end date
    SELECT
        b.customer_id,
        b.month_end,
        SUM(u.wau)   FILTER (WHERE u.week_end >  b.month_end - INTERVAL 91 DAY)   AS wau_sum_13w,
        SUM(u.seats) FILTER (WHERE u.week_end >  b.month_end - INTERVAL 91 DAY)   AS seat_weeks_13w,
        COUNT(*)     FILTER (WHERE u.week_end >  b.month_end - INTERVAL 91 DAY)   AS weeks_13w,
        AVG(u.wau)   FILTER (WHERE u.week_end >  b.month_end - INTERVAL 28 DAY)   AS wau_avg_last4w,
        AVG(u.wau)   FILTER (WHERE u.week_end <= b.month_end - INTERVAL 28 DAY
                               AND u.week_end >  b.month_end - INTERVAL 91 DAY)   AS wau_avg_prior9w,
        SUM(u.premium_gate_hits)  FILTER (WHERE u.week_end > b.month_end - INTERVAL 91 DAY) AS gate_hits_13w,
        SUM(u.premium_gate_users) FILTER (WHERE u.week_end > b.month_end - INTERVAL 91 DAY) AS gate_user_weeks_13w,
        SUM(u.user_invites)       FILTER (WHERE u.week_end > b.month_end - INTERVAL 91 DAY) AS invites_13w,
        SUM(u.automation_runs)    FILTER (WHERE u.week_end > b.month_end - INTERVAL 91 DAY) AS automation_runs_13w,
        SUM(u.report_views)       FILTER (WHERE u.week_end > b.month_end - INTERVAL 91 DAY) AS report_views_13w,
        SUM(u.core_actions)       FILTER (WHERE u.week_end > b.month_end - INTERVAL 91 DAY) AS core_actions_13w,
        SUM(u.integration_syncs)  FILTER (WHERE u.week_end > b.month_end - INTERVAL 91 DAY) AS integration_syncs_13w,
        MAX(u.wau / NULLIF(u.seats, 0)) FILTER (WHERE u.week_end > b.month_end - INTERVAL 91 DAY) AS max_weekly_utilization_13w
    FROM base b
    JOIN fct_account_usage_weekly u
      ON u.customer_id = b.customer_id
     AND u.is_complete_week
     AND u.is_live_at_week_end
     AND u.week_end <= b.month_end
     AND u.week_end >  b.month_end - INTERVAL 91 DAY
    WHERE b.is_paying
    GROUP BY 1, 2
),
contraction_recent AS (
    SELECT b.customer_id, b.month_end,
           COUNT(c.month_end) FILTER (WHERE c.movement_type = 'contraction') AS contraction_months_3m
    FROM base b
    LEFT JOIN fct_mrr_movement_components c
      ON c.customer_id = b.customer_id
     AND c.month_end >  last_day(b.month_end - INTERVAL 3 MONTH)
     AND c.month_end <= b.month_end
    WHERE b.is_paying
    GROUP BY 1, 2
),
cancel_open AS (
    SELECT b.customer_id, b.month_end, BOOL_OR(s.cancel_requested_date <= b.month_end) AS cancel_requested_open
    FROM base b
    JOIN stg_subscriptions s
      ON s.customer_id = b.customer_id
     AND s.start_date <= b.month_end
     AND (s.end_date IS NULL OR s.end_date > b.month_end)
    WHERE b.is_paying
    GROUP BY 1, 2
),
period_at_t AS (
    -- billing period covering t: its end is the next billing date (monthly) / renewal date (annual)
    SELECT b.customer_id, b.month_end,
           MAX(bp.period_end) AS current_period_end,
           arg_max(bp.billing_period, bp.period_end) AS current_period_type
    FROM base b
    JOIN int_billing_periods bp
      ON bp.customer_id = b.customer_id
     AND bp.period_start <= b.month_end
     AND bp.period_end   >  b.month_end
    WHERE b.is_paying
    GROUP BY 1, 2
),
future AS (
    SELECT b.customer_id, b.month_end,
           MIN(f.mrr_norm) FILTER (WHERE f.month_end <= last_day(b.month_end + INTERVAL 3 MONTH)) AS min_mrr_norm_next3,
           MIN(f.mrr)      FILTER (WHERE f.month_end <= last_day(b.month_end + INTERVAL 3 MONTH)) AS min_mrr_next3,
           COUNT(*)        FILTER (WHERE f.month_end <= last_day(b.month_end + INTERVAL 3 MONTH)) AS months_observed_next3
    FROM base b
    JOIN fct_customer_mrr_monthly f
      ON f.customer_id = b.customer_id
     AND f.month_end >  b.month_end
     AND f.month_end <= last_day(b.month_end + INTERVAL 6 MONTH)
    WHERE b.is_paying
    GROUP BY 1, 2
),
future_to_renewal AS (
    -- annual accounts: window runs to the first renewal month-end after t, if within t+6
    SELECT b.customer_id, b.month_end,
           MIN(f.mrr_norm) AS min_mrr_norm_to_renewal,
           COUNT(*)        AS months_observed_to_renewal
    FROM base b
    JOIN period_at_t p ON p.customer_id = b.customer_id AND p.month_end = b.month_end
    JOIN fct_customer_mrr_monthly f
      ON f.customer_id = b.customer_id
     AND f.month_end >  b.month_end
     AND f.month_end <= last_day(p.current_period_end)
    WHERE b.is_paying AND p.current_period_type = 'annual'
    GROUP BY 1, 2
),
upgrades AS (
    SELECT b.customer_id, b.month_end,
           BOOL_OR(pc.change_source <> 'system') AS label_voluntary_upgrade_90d,
           BOOL_OR(pc.change_source =  'system') AS label_forced_upgrade_90d
    FROM base b
    JOIN stg_plan_changes pc
      ON pc.customer_id = b.customer_id
     AND pc.from_plan_id = 'basic' AND pc.to_tier > pc.from_tier
     AND pc.effective_date >  b.month_end
     AND pc.effective_date <= b.month_end + INTERVAL 90 DAY
    WHERE b.is_paying
    GROUP BY 1, 2
)
SELECT
    b.customer_id,
    b.month_end,
    b.quarter_label,
    b.mrr, b.arr, b.mrr_norm, b.plan_id, b.tier_rank, b.seats, b.billing_period, b.discount_pct,
    b.months_since_first_paid AS tenure_months,
    b.cohort_month_end, b.segment, b.industry, b.acquisition_channel, b.region, b.cs_coverage, b.is_design_partner,
    b.seats_3m_ago, b.mrr_3m_ago, b.mrr_norm_3m_ago, b.peak_mrr_norm_6m,
    b.seats / NULLIF(b.seats_3m_ago, 0) - 1                                   AS seat_growth_3m,
    COALESCE(b.max_tier_before, 0) > 1                                        AS prior_higher_tier,
    u.weeks_13w,
    u.wau_sum_13w / NULLIF(u.seat_weeks_13w, 0)                               AS utilization_13w,
    u.wau_avg_last4w,
    u.wau_avg_prior9w,
    u.wau_avg_last4w / NULLIF(u.wau_avg_prior9w, 0) - 1                       AS wau_trend,
    u.max_weekly_utilization_13w,
    u.gate_hits_13w / NULLIF(u.wau_sum_13w, 0)                                AS gate_hits_per_wau,
    u.gate_user_weeks_13w / NULLIF(u.wau_sum_13w, 0)                          AS gate_user_share,
    u.invites_13w / NULLIF(u.seat_weeks_13w / NULLIF(u.weeks_13w, 0), 0)      AS invites_per_seat_13w,
    u.automation_runs_13w / NULLIF(u.wau_sum_13w, 0)                          AS automation_per_wau,
    u.report_views_13w / NULLIF(u.wau_sum_13w, 0)                             AS reports_per_wau,
    u.core_actions_13w / NULLIF(u.wau_sum_13w, 0)                             AS core_actions_per_wau,
    u.integration_syncs_13w / NULLIF(u.weeks_13w, 0)                          AS integration_syncs_per_week,
    COALESCE(cr.contraction_months_3m, 0)                                     AS contraction_months_3m,
    COALESCE(co.cancel_requested_open, FALSE)                                 AS cancel_requested_open,
    p.current_period_end,
    p.current_period_type,
    date_diff('day', b.month_end, p.current_period_end)                       AS days_to_period_end,
    -- ---------------- labels (future; never use as features) ----------------
    COALESCE(up.label_voluntary_upgrade_90d, FALSE)                           AS label_voluntary_upgrade_90d,
    COALESCE(up.label_forced_upgrade_90d, FALSE)                              AS label_forced_upgrade_90d,
    (b.month_end + INTERVAL 90 DAY)::DATE <= r.as_of_date                     AS label_upgrade_window_complete,
    COALESCE(fu.min_mrr_next3, 1) = 0 AND COALESCE(fu.months_observed_next3, 0) > 0 AS label_churn_3m,
    CASE WHEN COALESCE(p.current_period_type, b.billing_period) = 'annual'
         THEN last_day(p.current_period_end) ELSE last_day(b.month_end + INTERVAL 3 MONTH) END AS label_window_end,
    CASE
        WHEN COALESCE(p.current_period_type, b.billing_period) = 'annual'
            THEN p.current_period_end IS NOT NULL
             AND last_day(p.current_period_end) <= last_day(b.month_end + INTERVAL 6 MONTH)
             AND last_day(p.current_period_end) <= r.as_of_date
        ELSE last_day(b.month_end + INTERVAL 3 MONTH) <= r.as_of_date
    END                                                                       AS label_window_complete,
    CASE
        WHEN COALESCE(p.current_period_type, b.billing_period) = 'annual'
            THEN COALESCE(fr.min_mrr_norm_to_renewal, b.mrr_norm) <= 0.90 * b.mrr_norm
        ELSE COALESCE(fu.min_mrr_norm_next3, b.mrr_norm) <= 0.90 * b.mrr_norm
    END                                                                       AS label_contraction10_or_churn
FROM base b
CROSS JOIN meta_run r
LEFT JOIN usage u               ON u.customer_id = b.customer_id AND u.month_end = b.month_end
LEFT JOIN contraction_recent cr ON cr.customer_id = b.customer_id AND cr.month_end = b.month_end
LEFT JOIN cancel_open co        ON co.customer_id = b.customer_id AND co.month_end = b.month_end
LEFT JOIN period_at_t p         ON p.customer_id = b.customer_id AND p.month_end = b.month_end
LEFT JOIN future fu             ON fu.customer_id = b.customer_id AND fu.month_end = b.month_end
LEFT JOIN future_to_renewal fr  ON fr.customer_id = b.customer_id AND fr.month_end = b.month_end
LEFT JOIN upgrades up           ON up.customer_id = b.customer_id AND up.month_end = b.month_end
WHERE b.is_paying;
