{#
    FEAST feature macro (FR-FS-01, Module 4 §1). Rolling 1h/8h/24h/7d
    baseline-normalized averages, long-format-to-wide pivot so a downstream
    model sees vibration/temperature/rpm jointly per tick. Both relations are
    passed in (not ref()'d inline) so this macro stays reusable and dbt's
    lineage graph still sees the dependency from whichever model invokes it.
#}
{% macro sensor_rolling_features(source_relation, baseline_relation) %}
WITH normalized AS (
    SELECT
        s.equipment_id,
        s.reading_ts,
        s.sensor_type,
        (s.reading_value - b.baseline_mean) / b.baseline_std AS reading_value_z,
        (AVG(s.reading_value) OVER (
            PARTITION BY s.equipment_id, s.sensor_type ORDER BY s.reading_ts
            ROWS BETWEEN 3 PRECEDING AND CURRENT ROW            -- 1h = 4 ticks
        ) - b.baseline_mean) / b.baseline_std                    AS rolling_1h_avg_z,
        (AVG(s.reading_value) OVER (
            PARTITION BY s.equipment_id, s.sensor_type ORDER BY s.reading_ts
            ROWS BETWEEN 31 PRECEDING AND CURRENT ROW           -- 8h = 32 ticks
        ) - b.baseline_mean) / b.baseline_std                    AS rolling_8h_avg_z,
        (AVG(s.reading_value) OVER (
            PARTITION BY s.equipment_id, s.sensor_type ORDER BY s.reading_ts
            ROWS BETWEEN 95 PRECEDING AND CURRENT ROW           -- 24h = 96 ticks
        ) - b.baseline_mean) / b.baseline_std                    AS rolling_24h_avg_z,
        (AVG(s.reading_value) OVER (
            PARTITION BY s.equipment_id, s.sensor_type ORDER BY s.reading_ts
            ROWS BETWEEN 671 PRECEDING AND CURRENT ROW          -- 7d = 672 ticks
        ) - b.baseline_mean) / b.baseline_std                    AS rolling_7d_avg_z,
        s.hours_since_last_service,
        s.hours_since_install
    FROM {{ source_relation }} s
    JOIN {{ baseline_relation }} b
        ON b.equipment_id = s.equipment_id AND b.sensor_type = s.sensor_type
)
SELECT
    equipment_id,
    reading_ts,
    -- sensor_type is stored uppercase (generator's SENSOR_BASELINES keys) --
    -- lowercase literals here previously matched nothing, silently NULLing
    -- every pivoted column (confirmed empirically against real generator output).
    MAX(CASE WHEN sensor_type = 'VIBRATION'   THEN reading_value_z    END) AS vibration_z,
    MAX(CASE WHEN sensor_type = 'VIBRATION'   THEN rolling_1h_avg_z   END) AS vibration_rolling_1h_z,
    MAX(CASE WHEN sensor_type = 'VIBRATION'   THEN rolling_8h_avg_z   END) AS vibration_rolling_8h_z,
    MAX(CASE WHEN sensor_type = 'VIBRATION'   THEN rolling_24h_avg_z  END) AS vibration_rolling_24h_z,
    MAX(CASE WHEN sensor_type = 'VIBRATION'   THEN rolling_7d_avg_z   END) AS vibration_rolling_7d_z,
    MAX(CASE WHEN sensor_type = 'TEMPERATURE' THEN reading_value_z    END) AS temperature_z,
    MAX(CASE WHEN sensor_type = 'TEMPERATURE' THEN rolling_1h_avg_z   END) AS temperature_rolling_1h_z,
    MAX(CASE WHEN sensor_type = 'TEMPERATURE' THEN rolling_8h_avg_z   END) AS temperature_rolling_8h_z,
    MAX(CASE WHEN sensor_type = 'TEMPERATURE' THEN rolling_24h_avg_z  END) AS temperature_rolling_24h_z,
    MAX(CASE WHEN sensor_type = 'TEMPERATURE' THEN rolling_7d_avg_z   END) AS temperature_rolling_7d_z,
    MAX(CASE WHEN sensor_type = 'RPM'          THEN reading_value_z    END) AS rpm_z,
    MAX(CASE WHEN sensor_type = 'RPM'          THEN rolling_1h_avg_z   END) AS rpm_rolling_1h_z,
    MAX(CASE WHEN sensor_type = 'RPM'          THEN rolling_8h_avg_z   END) AS rpm_rolling_8h_z,
    MAX(CASE WHEN sensor_type = 'RPM'          THEN rolling_24h_avg_z  END) AS rpm_rolling_24h_z,
    MAX(CASE WHEN sensor_type = 'RPM'          THEN rolling_7d_avg_z   END) AS rpm_rolling_7d_z,
    MAX(hours_since_last_service) AS hours_since_last_service,   -- identical across all 3 sensor_type rows for a given tick
    MAX(hours_since_install)      AS hours_since_install
FROM normalized
GROUP BY equipment_id, reading_ts
{% endmacro %}
