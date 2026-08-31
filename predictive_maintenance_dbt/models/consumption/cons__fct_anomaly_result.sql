{% set default_version = 'UNKNOWN' %}
{% if execute %}
    {% set show_models_sql %}
        SHOW MODELS LIKE 'isolation_forest_model' IN SCHEMA {{ target.database }}.cons
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

-- Model-inference Consumption fact (FR-FS-00b, S-MODEL-3) -- no stored
-- procedure, no Task; predict/decision_function called directly in SQL,
-- reading FEAST's always-fresh feature table so a new sensor tick is what
-- drives this table's own refresh.
--
-- Uses the DEFAULT alias (MODEL(name, DEFAULT), confirmed valid syntax) --
-- not FRD's originally-specified LAST -- since 05_train_models.sql now
-- explicitly promotes each newly trained version to default (SH-22 §4.7);
-- DEFAULT expresses that intent directly instead of relying on "most
-- recently created" happening to coincide with "the one we want serving".
--
-- model_version has no live SQL-queryable source (no INFORMATION_SCHEMA.MODELS/
-- MODEL_VERSIONS in this account, confirmed empirically) -- resolved once at
-- dbt-compile time via SHOW MODELS above instead. Safe in this project's own
-- pipeline ordering (05_train_models.sql always runs immediately before this
-- table's dbt run, FR-OPS-02a/02b), but would go stale if the model were ever
-- retrained without a following dbt run.
SELECT
    equipment_id,
    reading_ts,
    (MODEL({{ target.database }}.cons.isolation_forest_model, DEFAULT)!predict(
        vibration_z, vibration_rolling_1h_z, vibration_rolling_8h_z, vibration_rolling_24h_z, vibration_rolling_7d_z,
        temperature_z, temperature_rolling_1h_z, temperature_rolling_8h_z, temperature_rolling_24h_z, temperature_rolling_7d_z,
        rpm_z, rpm_rolling_1h_z, rpm_rolling_8h_z, rpm_rolling_24h_z, rpm_rolling_7d_z
    ):"output_feature_0"::int = -1) AS is_anomaly,
    MODEL({{ target.database }}.cons.isolation_forest_model, DEFAULT)!decision_function(
        vibration_z, vibration_rolling_1h_z, vibration_rolling_8h_z, vibration_rolling_24h_z, vibration_rolling_7d_z,
        temperature_z, temperature_rolling_1h_z, temperature_rolling_8h_z, temperature_rolling_24h_z, temperature_rolling_7d_z,
        rpm_z, rpm_rolling_1h_z, rpm_rolling_8h_z, rpm_rolling_24h_z, rpm_rolling_7d_z
    ):"output_feature_0"::float AS anomaly_score,
    '{{ default_version }}' AS model_version
FROM {{ ref('feast__fct_sensor_features_inference') }}
