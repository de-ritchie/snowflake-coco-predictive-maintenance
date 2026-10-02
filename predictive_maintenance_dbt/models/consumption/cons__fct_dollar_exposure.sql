{{ config(
    materialized='dynamic_table',
    target_lag=var('target_lag'),
    schema='cons',
    snowflake_warehouse='snowcomotive_wh',
    refresh_mode='auto',
    initialize='on_create',
    tags=['inference']
) }}

-- Dollar-exposure combiner — the new single leaf of the dynamic-table chain
-- (SH-84 §3.3, replaces cons__fct_priority_score as the leaf).
SELECT
    COALESCE(h.equipment_id, f.equipment_id) AS equipment_id,
    f.score_ts,
    f.predicted_rul_hours,
    f.is_at_risk,
    f.assumed_downtime_hours,
    f.forward_demand_units,
    f.throughput_units_per_hour,
    f.unit_margin,
    COALESCE(h.breakdown_count, 0) AS historical_breakdown_count,
    COALESCE(h.total_breakdown_hours, 0) AS historical_total_breakdown_hours,
    COALESCE(h.historical_realized_impact_usd, 0) AS historical_realized_impact_usd,
    COALESCE(f.forward_dollar_at_risk_usd, 0) AS forward_dollar_at_risk_usd
FROM {{ ref('cons__fct_forward_dollar_at_risk') }} f
FULL OUTER JOIN {{ ref('cons__fct_historical_dollar_impact') }} h
    ON h.equipment_id = f.equipment_id
