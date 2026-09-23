{{ config(materialized='table', schema='cons', tags=['consumption']) }}

-- OEE fact, line x week grain (FR-PL-07/FR-PL-08, §3 Q2/Q3 of the design
-- doc). scheduled_hours is pure calendar capacity -- working days that week
-- x 24 x count of sensor-enabled equipment on the line (non-sensor
-- equipment on the same line is excluded from both numerator and
-- denominator; it never emits CMMS rows in this generator, so including it
-- would only dilute availability_pct toward 1.0). breakdown_hours sums
-- STD.CMMS_LOG.duration_hours for event_type = 'BREAKDOWN' only -- 'PM'
-- events are excluded from the loss calculation entirely (classical OEE
-- convention: planned maintenance is excluded from Planned Production Time,
-- not counted as an Availability loss; also matches this project's own
-- "unplanned downtime reduction %" demo narrative). PM hours are not
-- subtracted from scheduled_hours either -- accepted MVP simplification,
-- see design doc §3 Q3. performance_pct/quality_pct are fixed dbt vars
-- (same value applied to every line), not derived from any RAW/STD source.

WITH line_equipment AS (
    SELECT
        line_name,
        COUNT(*) AS num_sensor_machines
    FROM {{ ref('cons__dim_equipment') }}
    WHERE is_sensor_enabled
    GROUP BY line_name
),

calendar_weeks AS (
    SELECT
        DATE_TRUNC('week', calendar_date) AS period_week,
        COUNT(*) AS working_days
    FROM {{ ref('std__calendar') }}
    WHERE is_working_day
    GROUP BY DATE_TRUNC('week', calendar_date)
),

scheduled AS (
    SELECT
        le.line_name,
        cw.period_week,
        cw.working_days * 24 * le.num_sensor_machines AS scheduled_hours
    FROM line_equipment le
    CROSS JOIN calendar_weeks cw
),

breakdown AS (
    SELECT
        e.line_name,
        DATE_TRUNC('week', m.event_start_ts) AS period_week,
        SUM(m.duration_hours) AS breakdown_hours
    FROM {{ ref('std__cmms_log') }} m
    JOIN {{ ref('cons__dim_equipment') }} e
        ON e.equipment_id = m.equipment_id
    WHERE e.is_sensor_enabled
        AND m.event_type = 'BREAKDOWN'
    GROUP BY e.line_name, DATE_TRUNC('week', m.event_start_ts)
)

SELECT
    s.line_name,
    s.period_week,
    s.scheduled_hours,
    COALESCE(b.breakdown_hours, 0) AS breakdown_hours,
    1 - (COALESCE(b.breakdown_hours, 0) / s.scheduled_hours) AS availability_pct,
    {{ var('oee_performance_pct') }}::FLOAT AS performance_pct,
    {{ var('oee_quality_pct') }}::FLOAT AS quality_pct,
    (1 - (COALESCE(b.breakdown_hours, 0) / s.scheduled_hours))
        * {{ var('oee_performance_pct') }}::FLOAT
        * {{ var('oee_quality_pct') }}::FLOAT AS oee_pct
FROM scheduled s
LEFT JOIN breakdown b
    ON b.line_name = s.line_name
    AND b.period_week = s.period_week
