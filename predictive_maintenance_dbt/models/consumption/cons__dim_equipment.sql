{{ config(materialized='table', schema='cons', tags=['consumption']) }}

SELECT
    equipment_id,
    equipment_name,
    line_name,
    product_id,
    variant,
    is_sensor_enabled,
    throughput_units_per_hour,
    commissioned_ts
FROM {{ ref('std__equipment') }}
