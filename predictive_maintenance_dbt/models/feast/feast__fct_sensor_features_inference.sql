{{ config(
    materialized='dynamic_table',
    target_lag='1 hour',
    schema='feast',
    snowflake_warehouse='snowcomotive_wh',
    refresh_mode='incremental',
    tags=['feast']
) }}

-- Always-fresh feature table (FR-FS-01, Module 4 §2). target_lag is
-- temporarily 1 hour, matching std__sensor_reading/cons__fct_sensor_reading's
-- same credit-conservative thin/dev deviation from the 15-minute demo spec.
-- refresh_mode is pinned to INCREMENTAL (not left on AUTO) so CREATE fails
-- loudly if the macro ever stops being incrementally maintainable, instead of
-- silently falling back to a full rescore that would break the "predict only
-- the new tick" invariant this table exists to guarantee (Module 3 §3, Module 4 §3).
{{ sensor_rolling_features(
    source_relation=ref('cons__fct_sensor_reading'),
    baseline_relation=ref('cons__dim_sensor_baseline')
) }}
