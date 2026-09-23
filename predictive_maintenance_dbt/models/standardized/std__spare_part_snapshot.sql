{{ config(materialized='table', schema='std', tags=['standardized']) }}

-- Type/key conformance only (FR-PL-02) -- thin pass-through, no transforms.
SELECT
    snapshot_week,
    equipment_id,
    spare_part_name,
    units_on_hand,
    lead_time_days
FROM {{ source('raw', 'spare_part_snapshot') }}
