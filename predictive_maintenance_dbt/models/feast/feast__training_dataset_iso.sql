{{ config(
    materialized='table',
    schema='feast',
    snowflake_warehouse='snowcomotive_wh',
    tags=['feast']
) }}

-- Label-free training set for IsolationForest (FR-FS-01, Module 4 §4b) --
-- just the frozen feature snapshot with a time-based train/test split flag
-- (FR-FS-03), no spine/ASOF join needed since this model is unsupervised.
SELECT *,
    CASE WHEN reading_ts <= DATEADD('month', -6, CURRENT_DATE() - 30)
         THEN 'train' ELSE 'test' END AS dataset_split
FROM {{ ref('feast__fct_sensor_features_train') }}
