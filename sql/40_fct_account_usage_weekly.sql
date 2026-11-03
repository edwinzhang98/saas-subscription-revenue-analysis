-- =============================================================================
-- 40_fct_account_usage_weekly.sql
-- Purpose : One row per account-week: weekly active users (WAU), feature usage, seats and plan
--           on the subscription terms at the end of the week, seat utilization.
-- Inputs  : stg_usage_events, int_subscription_versions, dim_week
-- Outputs : fct_account_usage_weekly (customer_id x week_start)
-- Rules   : 02_metric_definitions.md §12 — WAU = unique users with a session; seats = terms at
--           the end of the week (Sunday); utilization = WAU / seats (can exceed 1: tolerated
--           overage). Weeks with usage but no live subscription (grace period) keep seats = NULL.
-- Run     : duckdb <warehouse.duckdb> < sql/40_fct_account_usage_weekly.sql
-- =============================================================================
CREATE OR REPLACE TABLE fct_account_usage_weekly AS
WITH ev AS (
    SELECT
        customer_id,
        week_start,
        MAX(CASE WHEN event_name = 'session'              THEN unique_users END) AS wau,
        SUM(CASE WHEN event_name = 'session'              THEN event_count  END) AS sessions,
        SUM(CASE WHEN event_name = 'core_action'          THEN event_count  END) AS core_actions,
        SUM(CASE WHEN event_name = 'automation_run'       THEN event_count  END) AS automation_runs,
        SUM(CASE WHEN event_name = 'report_view'          THEN event_count  END) AS report_views,
        SUM(CASE WHEN event_name = 'integration_sync'     THEN event_count  END) AS integration_syncs,
        SUM(CASE WHEN event_name = 'user_invite'          THEN event_count  END) AS user_invites,
        SUM(CASE WHEN event_name = 'premium_feature_gate' THEN event_count  END) AS premium_gate_hits,
        MAX(CASE WHEN event_name = 'premium_feature_gate' THEN unique_users END) AS premium_gate_users
    FROM stg_usage_events
    GROUP BY 1, 2
),
terms AS (
    SELECT
        v.customer_id,
        w.week_start,
        SUM(v.seats)                       AS seats,
        arg_max(v.plan_id, v.tier_rank)    AS plan_id,
        MAX(v.tier_rank)                   AS tier_rank,
        SUM(v.mrr)                         AS mrr
    FROM dim_week w
    JOIN int_subscription_versions v ON v.valid_from <= w.week_end AND v.valid_to > w.week_end
    GROUP BY 1, 2
)
SELECT
    COALESCE(t.customer_id, e.customer_id)   AS customer_id,
    COALESCE(t.week_start, e.week_start)     AS week_start,
    w.week_end,
    w.is_complete_week,
    t.seats,
    t.plan_id,
    t.tier_rank,
    t.mrr,
    t.customer_id IS NOT NULL                AS is_live_at_week_end,
    COALESCE(e.wau, 0)                       AS wau,
    COALESCE(e.sessions, 0)                  AS sessions,
    COALESCE(e.core_actions, 0)              AS core_actions,
    COALESCE(e.automation_runs, 0)           AS automation_runs,
    COALESCE(e.report_views, 0)              AS report_views,
    COALESCE(e.integration_syncs, 0)         AS integration_syncs,
    COALESCE(e.user_invites, 0)              AS user_invites,
    COALESCE(e.premium_gate_hits, 0)         AS premium_gate_hits,
    COALESCE(e.premium_gate_users, 0)        AS premium_gate_users,
    COALESCE(e.wau, 0) / NULLIF(t.seats, 0)  AS seat_utilization
FROM terms t
FULL OUTER JOIN ev e ON e.customer_id = t.customer_id AND e.week_start = t.week_start
JOIN dim_week w ON w.week_start = COALESCE(t.week_start, e.week_start);
