{{ config(materialized='table', schema='cons', tags=['consumption']) }}

-- Lean pass-through of STD.INVENTORY_FG_SNAPSHOT (FR-PL-05) -- snapshot_week
-- renamed period_week to align with FCT_INVENTORY_SPARE/FCT_OEE's own week
-- column naming (§3 of the design doc).
SELECT
    snapshot_week AS period_week,
    product_id,
    variant,
    fg_units_on_hand
FROM {{ ref('std__inventory_fg_snapshot') }}
