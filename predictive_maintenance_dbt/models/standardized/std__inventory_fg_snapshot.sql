{{ config(materialized='table', schema='std', tags=['standardized']) }}

-- Type/key conformance only (FR-PL-02) -- thin pass-through, no transforms.
SELECT
    snapshot_week,
    product_id,
    variant,
    fg_units_on_hand
FROM {{ source('raw', 'inventory_fg_snapshot') }}
