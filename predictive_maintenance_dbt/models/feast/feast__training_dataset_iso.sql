{{ config(
    materialized='table',
    schema='feast',
    snowflake_warehouse='snowcomotive_wh',
    tags=['feast']
) }}

-- Label-free training set for IsolationForest (FR-FS-01, Module 4 §4b) --
-- just the frozen feature snapshot with a time-based train/test split flag
-- (FR-FS-03), no spine/ASOF join needed since this model is unsupervised.
-- Split cutoff is anchored to the data's own MAX(reading_ts), not wall-clock
-- CURRENT_DATE() -- same SH-49 fix pattern applied to training_dataset_rul's
-- own cutoff. This makes iso_cutoff == rul_cutoff exactly (both ultimately
-- derive from the same underlying feast__fct_sensor_features_train MAX
-- timestamp), which is what makes SH-50's leakage argument hold by
-- construction rather than by coincidence (docs/designs/SH-50-anomaly-into-
-- rul-features.md §2.4).
SELECT *,
    CASE WHEN reading_ts <= (
        SELECT DATEADD('month', -6, MAX(reading_ts)) FROM {{ ref('feast__fct_sensor_features_train') }}
    ) THEN 'train' ELSE 'test' END AS dataset_split
FROM {{ ref('feast__fct_sensor_features_train') }}
