{{ config(materialized='dynamic_table', target_lag='DOWNSTREAM', schema='std', snowflake_warehouse='snowcomotive_wh', tags=['standardized'], immutable_where='insert_time < DATEADD(hour, -1, CURRENT_TIMESTAMP())') }}

-- Leakage-safe joins (FR-PL-02, FR-PL-03). target_lag is DOWNSTREAM (SH-72)
-- -- this is a purely intermediate table in the 6-table dynamic-table chain
-- (std -> cons.sensor_reading -> feast.inference -> cons.anomaly_result ->
-- cons.rul_prediction -> cons.priority_score); Snowflake derives its refresh
-- cadence from the chain's one leaf (cons__fct_priority_score's parameterized
-- target_lag) automatically. No longer independently pinned to 1 hour.
-- NOTE: this Snowflake account's ASOF JOIN grammar does not accept an
-- explicit LEFT/INNER prefix (verified empirically) -- bare `ASOF JOIN`
-- already null-pads unmatched left rows (outer semantics by default), so no
-- prefix is used here; the strict `>` MATCH_CONDITION below is what
-- preserves FR-PL-03's leakage-safety invariant.
-- NOTE (empirically confirmed, Module 3 §3 / Module 4 §3's open verification
-- item): ASOF JOIN blocks native change tracking on this table, forcing
-- REFRESH_MODE=FULL here. immutable_where declares a frozen region, which is
-- what lets FEAST.FCT_SENSOR_FEATURES_INFERENCE downstream still refresh
-- INCREMENTAL despite this table itself being FULL (confirmed empirically,
-- 2026-09-29: removing the frozen region entirely breaks that table with
-- "no longer incrementalizable ... Please recreate the dynamic table").
-- Keyed off insert_time (real wall-clock load time), NOT reading_ts (revised
-- 2026-09-29): reading_ts is simulated event time that the live-tick demo
-- generator replays from arbitrary past dates, so a reading_ts-relative
-- cutoff permanently freezes newly injected ticks out of incremental refresh
-- once enough real time passes after `manage.py up` -- confirmed live the
-- same day. insert_time tracks genuine load recency instead, so newly
-- loaded rows are always active immediately after loading regardless of
-- what date they simulate.
SELECT
    r.reading_id,
    r.equipment_id,
    r.reading_ts,
    r.sensor_type,
    r.reading_value,
    r.insert_time,
    DATEDIFF('hour', m.event_end_ts, r.reading_ts)    AS hours_since_last_service,
    DATEDIFF('hour', e.commissioned_ts, r.reading_ts) AS hours_since_install
FROM {{ source('raw', 'sensor_reading') }} r
JOIN {{ ref('std__equipment') }} e
    ON e.equipment_id = r.equipment_id
ASOF JOIN {{ source('raw', 'cmms_log') }} m
    MATCH_CONDITION (r.reading_ts > m.event_end_ts)
    ON r.equipment_id = m.equipment_id
