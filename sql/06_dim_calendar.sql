-- =============================================================================
-- 06_dim_calendar.sql
-- Purpose : Month and week calendars for the reporting window.
-- Inputs  : meta_run
-- Outputs : dim_month (one row per calendar month: month_start, month_end, quarter ...)
--           dim_week  (one row per Monday-starting week: week_start, week_end, is_complete_week)
-- =============================================================================
CREATE OR REPLACE TABLE dim_month AS
SELECT
    m::DATE                                         AS month_start,
    last_day(m)::DATE                               AS month_end,
    year(m)                                         AS year,
    quarter(m)                                      AS quarter_num,
    year(m) || '-Q' || quarter(m)                   AS quarter_label,
    date_trunc('quarter', m)::DATE                  AS quarter_start,
    (date_trunc('quarter', m) + INTERVAL 3 MONTH - INTERVAL 1 DAY)::DATE AS quarter_end,
    month(m) IN (3, 6, 9, 12)                       AS is_quarter_end_month,
    date_diff('month', r.window_start, m::DATE)     AS month_index
FROM meta_run r,
     generate_series(r.window_start::TIMESTAMP, r.as_of_date::TIMESTAMP, INTERVAL 1 MONTH) AS t(m)
WHERE last_day(m) <= r.as_of_date;

CREATE OR REPLACE TABLE dim_week AS
SELECT
    w::DATE                                         AS week_start,
    (w + INTERVAL 6 DAY)::DATE                      AS week_end,
    (w + INTERVAL 6 DAY)::DATE <= r.as_of_date      AS is_complete_week,
    last_day(w + INTERVAL 6 DAY)::DATE              AS month_end_of_week_end
FROM meta_run r,
     generate_series(DATE '2023-10-02'::TIMESTAMP, r.as_of_date::TIMESTAMP, INTERVAL 7 DAY) AS t(w);
