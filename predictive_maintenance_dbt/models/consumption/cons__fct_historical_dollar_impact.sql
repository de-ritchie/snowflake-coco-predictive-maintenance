{{ config(materialized='view', schema='cons', tags=['consumption']) }}

-- Historical realized $ impact per equipment (SH-84 §3.1).
WITH breakdown_impact AS (
    SELECT
        me.equipment_id,
        me.event_start_ts,
        me.duration_hours,
        eq.throughput_units_per_hour,
        eq.product_id,
        eq.variant,
        p.unit_margin,
        o.order_units AS order_units_that_week,
        LEAST(
            me.duration_hours * eq.throughput_units_per_hour,
            COALESCE(o.order_units, 0)
        ) * p.unit_margin AS event_realized_impact_usd
    FROM {{ ref('cons__fct_maintenance_event') }} me
    JOIN {{ ref('cons__dim_equipment') }} eq
        ON eq.equipment_id = me.equipment_id
    JOIN {{ ref('cons__dim_product') }} p
        ON p.product_id = eq.product_id AND p.variant = eq.variant
    LEFT JOIN {{ ref('cons__fct_order') }} o
        ON o.product_id = eq.product_id
        AND o.variant = eq.variant
        AND o.order_week = DATE_TRUNC('week', me.event_start_ts)
    WHERE me.event_type = 'BREAKDOWN'
      AND eq.is_sensor_enabled
)

SELECT
    equipment_id,
    COUNT(*) AS breakdown_count,
    SUM(duration_hours) AS total_breakdown_hours,
    SUM(event_realized_impact_usd) AS historical_realized_impact_usd
FROM breakdown_impact
GROUP BY equipment_id
