{{ config(materialized='table', schema='cons', tags=['consumption']) }}

-- Lean pass-through of STD.SPARE_PART_SNAPSHOT (FR-PL-05) -- snapshot_week
-- renamed period_week to align with FCT_INVENTORY_FG/FCT_OEE's own week
-- column naming (§3 of the design doc).
SELECT
    snapshot_week AS period_week,
    equipment_id,
    spare_part_name,
    units_on_hand,
    lead_time_days
FROM {{ ref('std__spare_part_snapshot') }}
