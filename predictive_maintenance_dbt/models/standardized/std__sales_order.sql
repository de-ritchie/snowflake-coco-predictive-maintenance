{{ config(materialized='table', schema='std', tags=['standardized']) }}

-- Type/key conformance only (FR-PL-02) -- thin pass-through, no transforms.
SELECT
    order_week,
    product_id,
    variant,
    order_units
FROM {{ source('raw', 'sales_order') }}
