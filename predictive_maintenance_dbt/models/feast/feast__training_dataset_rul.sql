{{ config(
    materialized='table',
    schema='feast',
    snowflake_warehouse='snowcomotive_wh',
    tags=['feast']
) }}

-- RUL training set (S-RUL-1/S-RUL-2, LLD Module 4 §4a, Module 5 §2) -- spine
-- ASOF-joined against the frozen feature snapshot (now including the
-- anomaly model's own output), plus a time-based dataset_split (FR-FS-03).
--
-- Anomaly columns are computed here directly via MODEL(isolation_forest_model)
-- against FEAST.FCT_SENSOR_FEATURES_TRAIN (frozen) -- deliberately NOT read
-- from cons.fct_anomaly_result / FCT_SENSOR_FEATURES_INFERENCE, which are
-- inference-only, live-refreshing artifacts (Module 4 §5's train/inference
-- asymmetry: training must not depend on inference). See
-- docs/designs/SH-50-anomaly-into-rul-features.md §2 for the full leakage
-- analysis -- a single static split is sufficient here (no walk-forward
-- retraining) because isolation_forest_model is trained only on
-- feast.training_dataset_iso's 'train' split, and that split's cutoff is
-- now anchored to the same MAX(reading_ts) source this model's own cutoff
-- uses (see feast__training_dataset_iso.sql), so iso_cutoff == rul_cutoff
-- by construction -- the anomaly model never sees a tick from this table's
-- own test period during its training.
--
-- NOTE: bare `ASOF JOIN` (no LEFT/INNER prefix) required on this account,
-- matching std__sensor_reading.sql / SH-49's existing convention.
--
-- Requires isolation_forest_model to already exist -- manage.py's phase-1
-- dbt invocation excludes this model and builds it in a second pass after
-- 05_train_models.sql (docs/designs/SH-50-anomaly-into-rul-features.md §4).

WITH split_anchor AS (
    SELECT DATEADD('month', -6, MAX(cycle_end_ts)) AS cutoff_ts
    FROM {{ ref('feast__spine_maintenance_cycle') }}
),

feat_with_anomaly AS (
    -- Extends the frozen feature snapshot with the anomaly model's per-tick
    -- output before anything else joins to it -- so the existing ASOF join
    -- below picks up is_anomaly/anomaly_score "for free" via feat.* EXCLUDE,
    -- with zero risk of the point-in-time-attached tick disagreeing with
    -- whatever tick the window aggregation (below) independently selects,
    -- since both read from this exact same CTE.
    SELECT
        feat.*,
        (MODEL({{ target.database }}.cons.isolation_forest_model, DEFAULT)!predict(
            vibration_z, vibration_rolling_1h_z, vibration_rolling_8h_z, vibration_rolling_24h_z, vibration_rolling_7d_z,
            temperature_z, temperature_rolling_1h_z, temperature_rolling_8h_z, temperature_rolling_24h_z, temperature_rolling_7d_z,
            rpm_z, rpm_rolling_1h_z, rpm_rolling_8h_z, rpm_rolling_24h_z, rpm_rolling_7d_z
        ):"output_feature_0"::int = -1) AS is_anomaly,
        MODEL({{ target.database }}.cons.isolation_forest_model, DEFAULT)!decision_function(
            vibration_z, vibration_rolling_1h_z, vibration_rolling_8h_z, vibration_rolling_24h_z, vibration_rolling_7d_z,
            temperature_z, temperature_rolling_1h_z, temperature_rolling_8h_z, temperature_rolling_24h_z, temperature_rolling_7d_z,
            rpm_z, rpm_rolling_1h_z, rpm_rolling_8h_z, rpm_rolling_24h_z, rpm_rolling_7d_z
        ):"output_feature_0"::float AS anomaly_score
    FROM {{ ref('feast__fct_sensor_features_train') }} feat
),

window_anomaly_agg AS (
    -- Backward-only 72h lookback per cycle -- never reads a tick with
    -- reading_ts > spine.cycle_end_ts, so this can't peek into that same
    -- cycle's own future.
    SELECT
        spine.equipment_id,
        spine.cycle_end_ts,
        BOOLOR_AGG(fa.is_anomaly)               AS any_anomaly_flagged_72h,
        MIN(fa.anomaly_score)                   AS min_anomaly_score_72h,
        AVG(CASE WHEN fa.is_anomaly THEN 1.0 ELSE 0.0 END) AS pct_anomalous_ticks_72h
    FROM {{ ref('feast__spine_maintenance_cycle') }} spine
    JOIN feat_with_anomaly fa
        ON fa.equipment_id = spine.equipment_id
        AND fa.reading_ts > DATEADD('hour', -72, spine.cycle_end_ts)
        AND fa.reading_ts <= spine.cycle_end_ts
    GROUP BY spine.equipment_id, spine.cycle_end_ts
)

SELECT
    spine.equipment_id,
    spine.cycle_end_ts,
    spine.y_lower,
    spine.y_upper,
    feat.* EXCLUDE (equipment_id, reading_ts),   -- now includes is_anomaly, anomaly_score
    wa.any_anomaly_flagged_72h,
    wa.min_anomaly_score_72h,
    wa.pct_anomalous_ticks_72h,
    CASE WHEN spine.cycle_end_ts <= split_anchor.cutoff_ts
         THEN 'train' ELSE 'test' END AS dataset_split
FROM {{ ref('feast__spine_maintenance_cycle') }} spine
CROSS JOIN split_anchor
ASOF JOIN feat_with_anomaly feat
    MATCH_CONDITION (spine.cycle_end_ts >= feat.reading_ts)
    ON spine.equipment_id = feat.equipment_id
JOIN window_anomaly_agg wa
    ON wa.equipment_id = spine.equipment_id AND wa.cycle_end_ts = spine.cycle_end_ts
