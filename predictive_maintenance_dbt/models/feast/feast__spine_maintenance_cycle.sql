{{ config(
    materialized='table',
    schema='feast',
    snowflake_warehouse='snowcomotive_wh',
    tags=['feast']
) }}

-- Per-cycle RUL survival spine (S-RUL-1, LLD Module 4 §4a). Cycle boundaries =
-- every CMMS event (PM or BREAKDOWN) per machine -- both reset the wear clock
-- in the generator (Module 2 LLD §5), so both are natural cycle boundaries.
-- Censoring depends on how the cycle ended (Module 2 LLD §6, FR-DG-11):
--   BREAKDOWN-ended -> uncensored, y_upper = y_lower
--   PM-ended / still-open -> censored, y_upper = NULL
-- y_lower is TRUE OPERATING HOURS, reconstructed by counting feature-table
-- ticks inside the cycle window (0.25h/tick) -- NOT DATEDIFF on event
-- timestamps, which would wrongly include idle/weekend/holiday time that
-- never advances the simulator's own t_hours clock.

WITH events AS (
    SELECT
        equipment_id,
        event_type,
        event_end_ts,
        LAG(event_end_ts) OVER (
            PARTITION BY equipment_id ORDER BY event_start_ts
        ) AS cycle_start_ts
    FROM {{ ref('cons__fct_maintenance_event') }}
),

closed_cycles AS (
    SELECT equipment_id, cycle_start_ts, event_end_ts AS cycle_end_ts, event_type
    FROM events
),

open_cycles AS (
    -- still-in-progress final cycle per machine -- right-censored at the end
    -- of the historical window (FR-DG-11), no closing CMMS event yet
    SELECT
        e.equipment_id,
        MAX(e.event_end_ts) AS cycle_start_ts,
        (SELECT MAX(reading_ts) FROM {{ ref('feast__fct_sensor_features_train') }}) AS cycle_end_ts,
        NULL AS event_type
    FROM {{ ref('cons__fct_maintenance_event') }} e
    GROUP BY e.equipment_id
),

all_cycles AS (
    SELECT * FROM closed_cycles
    UNION ALL
    SELECT * FROM open_cycles
)

SELECT
    c.equipment_id,
    c.cycle_end_ts,
    COUNT(f.reading_ts) * 0.25                                            AS y_lower,
    CASE WHEN c.event_type = 'BREAKDOWN' THEN COUNT(f.reading_ts) * 0.25
         ELSE NULL END                                                     AS y_upper
FROM all_cycles c
JOIN {{ ref('cons__dim_equipment') }} eq ON eq.equipment_id = c.equipment_id
LEFT JOIN {{ ref('feast__fct_sensor_features_train') }} f
    ON f.equipment_id = c.equipment_id
    AND f.reading_ts > COALESCE(c.cycle_start_ts, eq.commissioned_ts)
    AND f.reading_ts <= c.cycle_end_ts
GROUP BY c.equipment_id, c.cycle_end_ts, c.event_type
