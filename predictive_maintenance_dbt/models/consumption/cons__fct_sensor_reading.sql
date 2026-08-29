{{ config(materialized='dynamic_table', target_lag='1 hour', schema='cons', snowflake_warehouse='snowcomotive_wh', tags=['consumption']) }}

-- Lean pass-through of STD.SENSOR_READING (FR-PL-05) -- no aggregation, no
-- rolling windows, no normalization (those live in FEAST, out of scope here).
-- reading_id is kept (an additive, non-breaking column vs. Module 1's spec)
-- so SH-26's not_null test has a stable key column to anchor to.
SELECT
    reading_id,
    equipment_id,
    reading_ts,
    sensor_type,
    reading_value,
    hours_since_last_service,
    hours_since_install
FROM {{ ref('std__sensor_reading') }}
