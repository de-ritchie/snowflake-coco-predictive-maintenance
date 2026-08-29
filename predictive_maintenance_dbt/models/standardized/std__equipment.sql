{{ config(materialized='table', schema='std', tags=['standardized']) }}

-- Type/key conformance only (FR-PL-02) -- thin pass-through, no transforms.
SELECT
    equipment_id,
    equipment_name,
    line_name,
    product_id,
    variant,
    is_sensor_enabled,
    throughput_units_per_hour,
    commissioned_ts
FROM {{ source('raw', 'equipment') }}
