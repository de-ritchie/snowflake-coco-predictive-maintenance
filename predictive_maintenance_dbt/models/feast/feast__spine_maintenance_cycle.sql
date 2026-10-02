{{ config(
    materialized='table',
    schema='feast',
    snowflake_warehouse='snowcomotive_wh',
    tags=['feast']
) }}

-- Per-tick RUL survival spine (S-RUL-1, LLD Module 4 §4a). One row per sensor
-- tick per maintenance cycle, with per-tick remaining-time labels. Cycle
-- boundaries = every CMMS event (PM or BREAKDOWN) per machine, plus one
-- still-open final cycle per machine.
--
-- SH-86: changed from per-cycle to per-tick grain to eliminate label leakage
-- caused by the old 1-row-per-cycle + ASOF-join pattern, where every training
-- example was an end-of-cycle snapshot whose hours_since_last_service ≈ y_lower
-- by construction. See docs/designs/SH-86-fix-rul-training-leakage.md §1.
--
-- Censoring convention (unchanged from pre-SH-86):
--   BREAKDOWN-ended -> uncensored, y_upper = y_lower
--   PM-ended / still-open -> censored, y_upper = NULL
-- y_lower/y_upper are now PER-TICK remaining operating hours to cycle end,
-- not the cycle's total duration.

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
),

cycle_ticks AS (
    SELECT
        c.equipment_id,
        c.cycle_end_ts,
        c.event_type,
        f.reading_ts,
        ROW_NUMBER() OVER (
            PARTITION BY c.equipment_id, c.cycle_end_ts
            ORDER BY f.reading_ts
        ) AS tick_position,
        COUNT(*) OVER (
            PARTITION BY c.equipment_id, c.cycle_end_ts
        ) AS n_ticks_in_cycle
    FROM all_cycles c
    JOIN {{ ref('cons__dim_equipment') }} eq
        ON eq.equipment_id = c.equipment_id
    JOIN {{ ref('feast__fct_sensor_features_train') }} f
        ON f.equipment_id = c.equipment_id
        AND f.reading_ts > COALESCE(c.cycle_start_ts, eq.commissioned_ts)
        AND f.reading_ts <= c.cycle_end_ts
)

SELECT
    equipment_id,
    reading_ts,
    cycle_end_ts,
    (n_ticks_in_cycle - tick_position + 1) * 0.25 AS y_lower,
    CASE WHEN event_type = 'BREAKDOWN'
         THEN (n_ticks_in_cycle - tick_position + 1) * 0.25
         ELSE NULL END AS y_upper
FROM cycle_ticks
