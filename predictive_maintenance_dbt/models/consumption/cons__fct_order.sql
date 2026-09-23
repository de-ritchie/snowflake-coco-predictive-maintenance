{{ config(materialized='table', schema='cons', tags=['consumption']) }}

-- Lean pass-through of STD.SALES_ORDER (FR-PL-05) -- no aggregation.
SELECT
    order_week,
    product_id,
    variant,
    order_units
FROM {{ ref('std__sales_order') }}
