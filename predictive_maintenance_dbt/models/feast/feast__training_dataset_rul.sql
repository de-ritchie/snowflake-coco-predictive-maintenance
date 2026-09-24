{{ config(
    materialized='table',
    schema='feast',
    snowflake_warehouse='snowcomotive_wh',
    tags=['feast']
) }}

-- RUL training set (S-RUL-1, LLD Module 4 §4a) -- spine ASOF-joined against
-- the frozen feature snapshot, plus a time-based dataset_split (FR-FS-03).
-- Split cutoff is anchored to the data's own MAX(cycle_end_ts), not wall-clock
-- CURRENT_DATE(), so it stays a clean split regardless of how much time has
-- passed between data generation and this dbt run.
-- feat is already one row per (equipment_id, reading_ts) post-pivot (Module 4
-- §1) -- no per-sensor-type fanout to worry about.
-- NOTE (re-verified empirically against this account, 2026-09-24): bare
-- `ASOF JOIN` (no LEFT/INNER prefix) is required -- LEFT ASOF JOIN / INNER
-- ASOF JOIN both raise a syntax error on this account, matching the existing
-- std__sensor_reading.sql convention.

WITH split_anchor AS (
    SELECT DATEADD('month', -6, MAX(cycle_end_ts)) AS cutoff_ts
    FROM {{ ref('feast__spine_maintenance_cycle') }}
)

SELECT
    spine.equipment_id,
    spine.cycle_end_ts,
    spine.y_lower,
    spine.y_upper,
    feat.* EXCLUDE (equipment_id, reading_ts),
    CASE WHEN spine.cycle_end_ts <= split_anchor.cutoff_ts
         THEN 'train' ELSE 'test' END AS dataset_split
FROM {{ ref('feast__spine_maintenance_cycle') }} spine
CROSS JOIN split_anchor
ASOF JOIN {{ ref('feast__fct_sensor_features_train') }} feat
    MATCH_CONDITION (spine.cycle_end_ts >= feat.reading_ts)
    ON spine.equipment_id = feat.equipment_id
