{{ config(materialized='table', schema='cons', tags=['consumption']) }}

-- Lean pass-through of STD.CMMS_LOG (FR-PL-05) -- 1 row / event, both
-- event_type values ('PM' and 'BREAKDOWN') included, no filtering. FCT_OEE
-- is the model responsible for filtering to BREAKDOWN-only downtime; this
-- fact preserves the full event grain for the Semantic View MaintenanceEvent
-- entity.
SELECT
    event_id,
    equipment_id,
    event_type,
    event_start_ts,
    event_end_ts,
    technician_notes,
    duration_hours
FROM {{ ref('std__cmms_log') }}
