{{ config(
    materialized='table',
    schema='feast',
    snowflake_warehouse='snowcomotive_wh',
    tags=['feast']
) }}

-- RUL training set (S-RUL-1/S-RUL-2, LLD Module 4 §4a, Module 5 §2) -- spine
-- equi-joined against the frozen feature snapshot (now including the anomaly
-- model's own output), plus a time-based dataset_split (FR-FS-03).
--
-- SH-86: replaced ASOF JOIN with equi-join on (equipment_id, reading_ts) --
-- the spine now carries per-tick reading_ts (see SH-86 design doc §1/§3),
-- eliminating the end-of-cycle snapshot leakage. The grouped window_anomaly_agg
-- CTE is replaced with sliding window functions matching
-- cons__fct_rul_prediction.sql's anomaly_history pattern (ROWS BETWEEN 287
-- PRECEDING AND CURRENT ROW), which is correct at per-tick grain and also
-- consistent with how inference already computes these aggregates.
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

WITH split_anchor AS (
    SELECT DATEADD('month', -6, MAX(cycle_end_ts)) AS cutoff_ts
    FROM {{ ref('feast__spine_maintenance_cycle') }}
),

feat_with_anomaly AS (
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
)

SELECT
    spine.equipment_id,
    spine.reading_ts,
    spine.cycle_end_ts,
    spine.y_lower,
    spine.y_upper,
    feat.* EXCLUDE (equipment_id, reading_ts),
    MAX(CASE WHEN feat.is_anomaly THEN 1 ELSE 0 END) OVER (
        PARTITION BY feat.equipment_id ORDER BY feat.reading_ts
        ROWS BETWEEN 287 PRECEDING AND CURRENT ROW
    ) > 0 AS any_anomaly_flagged_72h,
    MIN(feat.anomaly_score) OVER (
        PARTITION BY feat.equipment_id ORDER BY feat.reading_ts
        ROWS BETWEEN 287 PRECEDING AND CURRENT ROW
    ) AS min_anomaly_score_72h,
    AVG(CASE WHEN feat.is_anomaly THEN 1.0 ELSE 0.0 END) OVER (
        PARTITION BY feat.equipment_id ORDER BY feat.reading_ts
        ROWS BETWEEN 287 PRECEDING AND CURRENT ROW
    ) AS pct_anomalous_ticks_72h,
    CASE WHEN spine.cycle_end_ts <= split_anchor.cutoff_ts
         THEN 'train' ELSE 'test' END AS dataset_split
FROM {{ ref('feast__spine_maintenance_cycle') }} spine
CROSS JOIN split_anchor
JOIN feat_with_anomaly feat
    ON feat.equipment_id = spine.equipment_id
    AND feat.reading_ts = spine.reading_ts
