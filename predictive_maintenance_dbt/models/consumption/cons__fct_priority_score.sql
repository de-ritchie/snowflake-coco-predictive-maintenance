{{ config(
    materialized='dynamic_table',
    target_lag=var('target_lag'),
    schema='cons',
    snowflake_warehouse='snowcomotive_wh',
    refresh_mode='auto',
    initialize='on_create',
    tags=['inference']
) }}

-- Priority-score inference Consumption fact (S-RUL-6, FR-FS-08,
-- docs/designs/SH-47-priority-score.md) -- the prescriptive headline number,
-- "which machines need attention right now", chaining cons__fct_rul_prediction
-- (dynamic, per-tick) against cons__fct_order/fct_inventory_fg/fct_inventory_spare
-- (static, weekly-grain).
--
-- Grain is 1 row / equipment, always latest -- a deliberate deviation from
-- docs/04-1-LLD.md's originally-specified per-tick grain, see design doc §4
-- for the full rationale (join shape against QUALIFY-filtered latest-per-key
-- CTEs is unproven for incremental refresh; shrinking the output grain to a
-- handful of rows makes a FULL-refresh fallback trivially cheap either way).
--
-- Every input CTE below is independently filtered to latest-per-key BEFORE
-- any join happens (design doc §3) -- the join itself always operates on a
-- handful of rows. The one exception is rul_cap below, a single scalar
-- aggregated over fct_rul_prediction's FULL per-tick history by design (see
-- that CTE's own comment) -- it joins into `scored` as a 1-row CROSS JOIN,
-- so it doesn't change the row-count shape of the join itself.
--
-- target_lag is parameterized (dbt var `target_lag`, dbt_project.yml,
-- default '1 hour') -- this is the pipeline's one leaf table (the only one
-- of the 6-table chain queried by anything outside this dbt-ref DAG: the
-- semantic view + Overview page), so it's the only one that needs an
-- explicit, operator-tunable lag. The 5 upstream tables are all
-- target_lag=DOWNSTREAM and derive their own refresh cadence from this
-- value automatically (Snowflake's own documented best practice for a
-- single linear dynamic-table chain; SH-72). Set via
-- `manage.py up --target-lag <value>` (e.g. '1 minute' for a faster demo
-- cadence) -- Snowflake's real minimum is 60 seconds.
--
-- refresh_mode is 'auto', not 'incremental' -- PERCENTILE_CONT (used in
-- rul_cap below) doesn't support Snowflake change tracking, so incremental
-- refresh isn't possible once that aggregate is in the query; 'auto' lets
-- Snowflake fall back to FULL for this table. Trivially cheap either way,
-- same rationale as the grain-shrinking decision above (§4/handful of rows).
WITH latest_rul AS (
    SELECT
        equipment_id,
        reading_ts,
        predicted_rul_hours,
        is_anomaly,
        anomaly_score
    FROM {{ ref('cons__fct_rul_prediction') }}
    QUALIFY ROW_NUMBER() OVER (PARTITION BY equipment_id ORDER BY reading_ts DESC) = 1
),

-- rul_urgency's normalization cap: a single fleet-wide p95 of predicted_rul_hours
-- over cons__fct_rul_prediction's FULL per-tick history (~170k+ rows, not just
-- the 3-row latest-only snapshot above), so all machines are normalized against
-- the same denominator (cross-machine comparability for ranking) and the cap
-- self-adjusts on future data regeneration instead of a stale hardcoded literal.
-- p95 (not MAX) for robustness against a single outlier prediction stretching
-- everyone else's scale. Exact PERCENTILE_CONT, not APPROX_PERCENTILE -- at this
-- row count exact computation is trivially cheap, so there's no reason to trade
-- accuracy for the t-digest approximation APPROX_PERCENTILE is meant for at
-- billion-row scale.
rul_cap AS (
    SELECT
        PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY predicted_rul_hours) AS rul_urgency_cap_hours
    FROM {{ ref('cons__fct_rul_prediction') }}
),

-- trailing_4wk_avg_order_units is computed as a ROWS BETWEEN 3 PRECEDING AND
-- CURRENT ROW average anchored at each product/variant's own latest available
-- week, then QUALIFY-filtered to that single latest row -- still exactly one
-- row per key, with the trailing average already folded in before the join.
latest_order AS (
    SELECT
        product_id,
        variant,
        order_week,
        AVG(order_units) OVER (
            PARTITION BY product_id, variant
            ORDER BY order_week
            ROWS BETWEEN 3 PRECEDING AND CURRENT ROW
        ) AS trailing_4wk_avg_order_units
    FROM {{ ref('cons__fct_order') }}
    QUALIFY ROW_NUMBER() OVER (PARTITION BY product_id, variant ORDER BY order_week DESC) = 1
),

latest_inventory_fg AS (
    SELECT
        product_id,
        variant,
        period_week,
        fg_units_on_hand
    FROM {{ ref('cons__fct_inventory_fg') }}
    QUALIFY ROW_NUMBER() OVER (PARTITION BY product_id, variant ORDER BY period_week DESC) = 1
),

-- fct_inventory_spare carries 1 row per equipment per spare_part_name per
-- week -- aggregated across all tracked parts for that equipment's own
-- latest available week (GROUP BY + QUALIFY on the window-computed latest
-- period_week, not a plain ROW_NUMBER = 1, since collapsing multiple parts
-- into one row per equipment is itself an aggregation, not a row pick).
latest_inventory_spare AS (
    SELECT
        equipment_id,
        SUM(units_on_hand) AS total_units_on_hand,
        MIN(lead_time_days) AS min_lead_time_days
    FROM {{ ref('cons__fct_inventory_spare') }}
    GROUP BY equipment_id, period_week
    QUALIFY period_week = MAX(period_week) OVER (PARTITION BY equipment_id)
),

scored AS (
    SELECT
        eq.equipment_id,
        lr.reading_ts AS score_ts,
        lr.predicted_rul_hours,
        lr.is_anomaly,
        lr.anomaly_score,

        -- rul_urgency: inverse-normalized against the fleet-wide p95 cap (rc.rul_urgency_cap_hours).
        1 - LEAST(lr.predicted_rul_hours, rc.rul_urgency_cap_hours) / rc.rul_urgency_cap_hours AS rul_urgency,

        -- demand_pressure: required weekly run-hours (trailing 4wk avg order
        -- volume / throughput) normalized against a 168h (7-day) full week.
        LEAST(
            (lo.trailing_4wk_avg_order_units / eq.throughput_units_per_hour) / 168,
            1.0
        ) AS demand_pressure,

        -- inventory_buffer: inverse-normalized days-of-supply, capped at 7 days.
        1 - LEAST(
            lifg.fg_units_on_hand / (lo.trailing_4wk_avg_order_units / 7),
            7
        ) / 7 AS inventory_buffer,

        -- spare_part_readiness: half on-hand-quantity signal, half lead-time signal.
        0.5 * LEAST(lis.total_units_on_hand, 5) / 5
        + 0.5 * (1 - LEAST(lis.min_lead_time_days, 30) / 30) AS spare_part_readiness,

        -- Surfaced separately per design doc §8's explicit decision not to
        -- bake an RUL-vs-demand cross-check into the weighted formula itself.
        (lo.trailing_4wk_avg_order_units / eq.throughput_units_per_hour) * 4 AS required_run_hours_next_4wk
    FROM {{ ref('cons__dim_equipment') }} eq
    JOIN latest_rul lr
        ON lr.equipment_id = eq.equipment_id
    CROSS JOIN rul_cap rc
    LEFT JOIN latest_order lo
        ON lo.product_id = eq.product_id AND lo.variant = eq.variant
    LEFT JOIN latest_inventory_fg lifg
        ON lifg.product_id = eq.product_id AND lifg.variant = eq.variant
    LEFT JOIN latest_inventory_spare lis
        ON lis.equipment_id = eq.equipment_id
    WHERE eq.is_sensor_enabled
)

SELECT
    equipment_id,
    score_ts,
    predicted_rul_hours,
    is_anomaly,
    anomaly_score,
    rul_urgency,
    demand_pressure,
    inventory_buffer,
    spare_part_readiness,
    100 * (
        0.40 * rul_urgency
        + 0.25 * demand_pressure
        + 0.20 * inventory_buffer
        + 0.15 * spare_part_readiness
    ) AS priority_score,
    required_run_hours_next_4wk
FROM scored
