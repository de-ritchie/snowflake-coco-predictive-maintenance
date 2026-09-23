{{ config(materialized='table', schema='std', tags=['standardized']) }}

-- Type/key conformance only (FR-PL-02) -- thin pass-through of RAW.CMMS_LOG,
-- plus duration_hours (derived: event_end_ts - event_start_ts). Seconds-based
-- DATEDIFF, not hour-based -- durations are fractional (Uniform(1,3)h,
-- Uniform(2,8)h draws), so an hour-truncated DATEDIFF would silently round
-- every event down to a whole number, corrupting downstream OEE downtime sums.
SELECT
    event_id,
    equipment_id,
    event_type,
    event_start_ts,
    event_end_ts,
    technician_notes,
    DATEDIFF('second', event_start_ts, event_end_ts) / 3600.0 AS duration_hours
FROM {{ source('raw', 'cmms_log') }}
