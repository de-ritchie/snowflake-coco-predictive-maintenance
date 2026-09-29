{{ config(materialized='dynamic_table', target_lag='DOWNSTREAM', schema='cons', snowflake_warehouse='snowcomotive_wh', tags=['consumption'], immutable_where='insert_time < DATEADD(hour, -1, CURRENT_TIMESTAMP())') }}

-- Lean pass-through of STD.SENSOR_READING (FR-PL-05) -- no aggregation, no
-- rolling windows, no normalization (those live in FEAST, out of scope here).
-- reading_id is kept (an additive, non-breaking column vs. Module 1's spec)
-- so SH-26's not_null test has a stable key column to anchor to.
-- immutable_where is a separate frozen-region declaration from std's (a
-- table's frozen region isn't inherited from its upstream) -- this table
-- is FULL too (cascades from std's ASOF JOIN), so it needs its own frozen
-- region for FEAST's INCREMENTAL dynamic table to consume it downstream.
-- Keyed off insert_time, not reading_ts -- see std__sensor_reading.sql's
-- header comment for the full rationale (2026-09-29).
SELECT
    reading_id,
    equipment_id,
    reading_ts,
    sensor_type,
    reading_value,
    insert_time,
    hours_since_last_service,
    hours_since_install
FROM {{ ref('std__sensor_reading') }}
