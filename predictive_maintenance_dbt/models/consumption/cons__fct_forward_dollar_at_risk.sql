{{ config(
    materialized='dynamic_table',
    target_lag='DOWNSTREAM',
    schema='cons',
    snowflake_warehouse='snowcomotive_wh',
    refresh_mode='auto',
    initialize='on_create',
    tags=['inference']
) }}

-- Forward-looking dollar-at-risk per equipment (SH-84 §3.2).
-- Binary threshold: predicted_rul_hours < 672 (4 weeks) → at risk.
-- assumed_downtime_hours is fleet-wide median BREAKDOWN duration.
WITH assumed_downtime AS (
    SELECT MEDIAN(duration_hours) AS assumed_downtime_hours
    FROM {{ ref('cons__fct_maintenance_event') }}
    WHERE event_type = 'BREAKDOWN'
),

forward_demand AS (
    SELECT
        ps.equipment_id,
        COALESCE(SUM(o.order_units), 0) AS forward_demand_units
    FROM {{ ref('cons__fct_priority_score') }} ps
    JOIN {{ ref('cons__dim_equipment') }} eq
        ON eq.equipment_id = ps.equipment_id
    LEFT JOIN {{ ref('cons__fct_order') }} o
        ON o.product_id = eq.product_id
        AND o.variant = eq.variant
        AND o.order_week >= DATE_TRUNC('week', ps.score_ts)
        AND o.order_week < DATEADD('week', 4, DATE_TRUNC('week', ps.score_ts))
    GROUP BY ps.equipment_id
)

SELECT
    ps.equipment_id,
    ps.score_ts,
    ps.predicted_rul_hours,
    ps.required_run_hours_next_4wk,
    ad.assumed_downtime_hours,
    fd.forward_demand_units,
    eq.throughput_units_per_hour,
    p.unit_margin,
    ps.predicted_rul_hours < 672 AS is_at_risk,
    CASE
        WHEN ps.predicted_rul_hours < 672 THEN
            LEAST(
                ad.assumed_downtime_hours * eq.throughput_units_per_hour,
                fd.forward_demand_units
            ) * p.unit_margin
        ELSE 0
    END AS forward_dollar_at_risk_usd
FROM {{ ref('cons__fct_priority_score') }} ps
JOIN {{ ref('cons__dim_equipment') }} eq
    ON eq.equipment_id = ps.equipment_id
JOIN {{ ref('cons__dim_product') }} p
    ON p.product_id = eq.product_id AND p.variant = eq.variant
CROSS JOIN assumed_downtime ad
JOIN forward_demand fd
    ON fd.equipment_id = ps.equipment_id
