{{ config(
    materialized='table',
    schema='feast',
    snowflake_warehouse='snowcomotive_wh',
    tags=['feast']
) }}

-- Frozen snapshot, same macro as the inference table (FR-FS-01, Module 4 §2).
-- Rebuilt as its own explicit pipeline step (FR-OPS-02a, scheduled Task),
-- never incrementally -- training needs a fixed point-in-time input, not a
-- table that keeps moving mid-run.
{{ sensor_rolling_features(
    source_relation=ref('cons__fct_sensor_reading'),
    baseline_relation=ref('cons__dim_sensor_baseline')
) }}
