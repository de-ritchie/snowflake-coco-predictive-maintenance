{% set default_version = 'UNKNOWN' %}
{% if execute %}
    {% set show_models_sql %}
        SHOW MODELS LIKE 'rul_aft_model' IN SCHEMA {{ target.database }}.cons
    {% endset %}
    {% set results = run_query(show_models_sql) %}
    {% if results.rows | length > 0 %}
        {% set default_version = results.columns['default_version_name'].values()[0] %}
    {% endif %}
{% endif %}

{{ config(
    materialized='dynamic_table',
    target_lag='1 hour',
    schema='cons',
    snowflake_warehouse='snowcomotive_wh',
    refresh_mode='incremental',
    initialize='on_create',
    tags=['inference']
) }}

-- RUL (remaining-useful-life) inference Consumption fact (S-RUL-5,
-- docs/designs/SH-45-rul-inference-dynamic-table.md) -- the live-inference
-- counterpart to cons__fct_anomaly_result, one persisted row per new sensor
-- tick per machine.
--
-- Deliberately reuses cons.fct_anomaly_result's own already-persisted
-- is_anomaly/anomaly_score rather than re-calling isolation_forest_model --
-- see design doc §2 for why this is correct, not just convenient. The 3
-- 72h-windowed aggregates are computed here via a ROWS BETWEEN 287 PRECEDING
-- AND CURRENT ROW frame (72h / 15-min cadence = 288 ticks) over that same
-- persisted history, mirroring feast's own rolling-feature window pattern
-- one abstraction layer up (windowing over model output, not raw sensor
-- input).
--
-- Column order in the !predict() call is a hard correctness requirement --
-- must match scripts/06_train_rul_model.sql's feature_cols list exactly,
-- position-for-position. is_anomaly/any_anomaly_flagged_72h are explicitly
-- cast ::int to match the int-cast dtype 06_train_rul_model.sql's DMatrix
-- construction used (train_pdf[...].astype(int)) -- native BOOLEAN was not
-- assumed to coerce identically, per design doc §3's explicit call to verify.
--
-- Deviation from design doc §3's literal SQL: BOOLOR_AGG does not support a
-- sliding ROWS BETWEEN frame ("Sliding window frame unsupported for function
-- BOOLOR_AGG", confirmed empirically at build time) -- MAX() over the same
-- 0/1 CASE expression already used for pct_anomalous_ticks_72h computes the
-- identical "any true in window" result and does support the sliding frame,
-- so it's used here instead, then compared > 0 to expose the same BOOLEAN
-- type on the output column any_anomaly_flagged_72h.
WITH anomaly_history AS (
    SELECT
        equipment_id,
        reading_ts,
        is_anomaly,
        anomaly_score,
        MAX(CASE WHEN is_anomaly THEN 1 ELSE 0 END) OVER (
            PARTITION BY equipment_id ORDER BY reading_ts
            ROWS BETWEEN 287 PRECEDING AND CURRENT ROW
        ) > 0 AS any_anomaly_flagged_72h,
        MIN(anomaly_score) OVER (
            PARTITION BY equipment_id ORDER BY reading_ts
            ROWS BETWEEN 287 PRECEDING AND CURRENT ROW
        ) AS min_anomaly_score_72h,
        AVG(CASE WHEN is_anomaly THEN 1.0 ELSE 0.0 END) OVER (
            PARTITION BY equipment_id ORDER BY reading_ts
            ROWS BETWEEN 287 PRECEDING AND CURRENT ROW
        ) AS pct_anomalous_ticks_72h
    FROM {{ ref('cons__fct_anomaly_result') }}
)

SELECT
    feat.equipment_id,
    feat.reading_ts,
    MODEL({{ target.database }}.cons.rul_aft_model, DEFAULT)!predict(
        feat.vibration_z, feat.vibration_rolling_1h_z, feat.vibration_rolling_8h_z, feat.vibration_rolling_24h_z, feat.vibration_rolling_7d_z,
        feat.temperature_z, feat.temperature_rolling_1h_z, feat.temperature_rolling_8h_z, feat.temperature_rolling_24h_z, feat.temperature_rolling_7d_z,
        feat.rpm_z, feat.rpm_rolling_1h_z, feat.rpm_rolling_8h_z, feat.rpm_rolling_24h_z, feat.rpm_rolling_7d_z,
        feat.hours_since_last_service, feat.hours_since_install,
        ah.is_anomaly::int, ah.anomaly_score,
        ah.any_anomaly_flagged_72h::int, ah.min_anomaly_score_72h, ah.pct_anomalous_ticks_72h
    ):"output_feature_0"::float AS predicted_rul_hours,
    ah.is_anomaly,
    ah.anomaly_score,
    ah.any_anomaly_flagged_72h,
    ah.min_anomaly_score_72h,
    ah.pct_anomalous_ticks_72h,
    '{{ default_version }}' AS model_version
FROM {{ ref('feast__fct_sensor_features_inference') }} feat
JOIN anomaly_history ah
    ON ah.equipment_id = feat.equipment_id AND ah.reading_ts = feat.reading_ts
