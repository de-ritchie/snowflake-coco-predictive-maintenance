# DATA_MAP.md — UI Element Data Mapping Reference

**Branch**: `feature/SH-7-70-streamlit-ux-revamp-spike`
**Design doc**: `docs/designs/SH-70-data-mapping.md`
**Scope**: Plant Dashboard (25 elements), Production (15 elements), Diagnostics (12 elements) = **52 total**
**Excluded**: Chat page (100% mock agent interaction, no SQL backing)

All SQL sketches use `snowcomotive.cons.*` fully-qualified table names.
OEE values are stored as 0-1 ratios (invariant #1) -- multiply by 100 at display time only.
Priority score weights: `0.40 * rul_urgency + 0.25 * demand_pressure + 0.20 * inventory_buffer + 0.15 * spare_part_readiness` (invariant #3) -- already computed in the dbt model; display code must NOT recompute.

---

## Application-Level Constants

These thresholds are owned by the Streamlit layer, not the dbt pipeline (synthetic gap #4):

| Constant | Value | Used by |
|----------|-------|---------|
| `HEALTH_THRESHOLD_HEALTHY` | `priority_score < 40` | PD-B5, PR-D4, DX-B4 |
| `HEALTH_THRESHOLD_WATCH` | `40 <= priority_score < 60` | PD-B5, PR-D4, DX-B4 |
| `HEALTH_THRESHOLD_AT_RISK` | `priority_score >= 60` | PD-B5, PR-D4, DX-B4 |

---

## 1. Plant Dashboard (25 elements)

### 1A. KPI Cards (8 elements)

---

#### PD-A1 — OEE % (value)

| Field | Content |
|-------|---------|
| **UI description** | Large percentage in the OEE card, e.g. "80.8%" |
| **Availability** | REAL |
| **Source table(s)** | `cons__fct_oee` |

```sql
-- Element: PD-A1 (OEE % -- Plant Dashboard SA, card 1)
-- Availability: REAL
-- Source: cons__fct_oee

SELECT
    SUM(oee_pct * scheduled_hours) / SUM(scheduled_hours) AS plant_oee_pct
FROM snowcomotive.cons.cons__fct_oee
WHERE period_week = (SELECT MAX(period_week) FROM snowcomotive.cons.cons__fct_oee)
  AND (:line_filter = 'all' OR line_name = :line_filter);
```

**Notes**: Weighted average by `scheduled_hours` when line_filter = 'all'. Display as `(plant_oee_pct * 100).toFixed(1) + '%'`.

---

#### PD-A2 — OEE % delta ("+X.Xpp vs. prior week")

| Field | Content |
|-------|---------|
| **UI description** | Delta text below OEE card, e.g. "+1.2pp vs. prior week" |
| **Availability** | DERIVED |
| **Source table(s)** | `cons__fct_oee` + LAG window |

```sql
-- Element: PD-A2 (OEE % delta -- week-over-week change)
-- Availability: DERIVED
-- Source: cons__fct_oee + LAG window

WITH ranked AS (
    SELECT
        line_name,
        period_week,
        oee_pct,
        scheduled_hours,
        LAG(oee_pct) OVER (PARTITION BY line_name ORDER BY period_week) AS prev_oee_pct
    FROM snowcomotive.cons.cons__fct_oee
)
SELECT
    SUM(oee_pct * scheduled_hours) / SUM(scheduled_hours)
    - SUM(prev_oee_pct * scheduled_hours) / SUM(scheduled_hours) AS oee_delta_pp
FROM ranked
WHERE period_week = (SELECT MAX(period_week) FROM snowcomotive.cons.cons__fct_oee)
  AND (:line_filter = 'all' OR line_name = :line_filter);
```

**Notes**: Result is in 0-1 scale; display as `(oee_delta_pp * 100).toFixed(1) + 'pp'`. Positive = green, negative = red.

---

#### PD-A3 — Availability % (value)

| Field | Content |
|-------|---------|
| **UI description** | Large percentage in the Availability card |
| **Availability** | REAL |
| **Source table(s)** | `cons__fct_oee` |

```sql
-- Element: PD-A3 (Availability % -- Plant Dashboard SA, card 2)
-- Availability: REAL
-- Source: cons__fct_oee

SELECT
    SUM(availability_pct * scheduled_hours) / SUM(scheduled_hours) AS plant_availability_pct
FROM snowcomotive.cons.cons__fct_oee
WHERE period_week = (SELECT MAX(period_week) FROM snowcomotive.cons.cons__fct_oee)
  AND (:line_filter = 'all' OR line_name = :line_filter);
```

**Notes**: Same weighted-average pattern as PD-A1.

---

#### PD-A4 — Availability % delta

| Field | Content |
|-------|---------|
| **UI description** | Delta text below Availability card |
| **Availability** | DERIVED |
| **Source table(s)** | `cons__fct_oee` + LAG window |

```sql
-- Element: PD-A4 (Availability % delta)
-- Availability: DERIVED
-- Source: cons__fct_oee + LAG window

WITH ranked AS (
    SELECT
        line_name,
        period_week,
        availability_pct,
        scheduled_hours,
        LAG(availability_pct) OVER (PARTITION BY line_name ORDER BY period_week) AS prev_availability_pct
    FROM snowcomotive.cons.cons__fct_oee
)
SELECT
    SUM(availability_pct * scheduled_hours) / SUM(scheduled_hours)
    - SUM(prev_availability_pct * scheduled_hours) / SUM(scheduled_hours) AS availability_delta_pp
FROM ranked
WHERE period_week = (SELECT MAX(period_week) FROM snowcomotive.cons.cons__fct_oee)
  AND (:line_filter = 'all' OR line_name = :line_filter);
```

---

#### PD-A5 — Performance % (value)

| Field | Content |
|-------|---------|
| **UI description** | Large percentage in the Performance card (constant, plant-wide) |
| **Availability** | REAL (constant) |
| **Source table(s)** | `cons__fct_oee.performance_pct` (dbt var `oee_performance_pct`, default 0.98) |

```sql
-- Element: PD-A5 (Performance % -- constant dbt var)
-- Availability: REAL (constant)
-- Source: cons__fct_oee.performance_pct

SELECT DISTINCT performance_pct
FROM snowcomotive.cons.cons__fct_oee
LIMIT 1;
```

**Notes**: Same value across all rows -- it's a dbt var materialized as a column. Currently 0.98 (env var `OEE_PERFORMANCE_PCT`).

---

#### PD-A6 — Performance % delta

| Field | Content |
|-------|---------|
| **UI description** | Delta text, always "+0.0pp vs. prior week" |
| **Availability** | REAL (constant) |
| **Source table(s)** | N/A -- always 0, because the value is constant across all weeks |

**Notes**: No query needed. Hardcode delta = 0 in display layer.

---

#### PD-A7 — Quality % (value)

| Field | Content |
|-------|---------|
| **UI description** | Large percentage in the Quality card (constant, plant-wide) |
| **Availability** | REAL (constant) |
| **Source table(s)** | `cons__fct_oee.quality_pct` (dbt var `oee_quality_pct`, default 0.97) |

```sql
-- Element: PD-A7 (Quality % -- constant dbt var)
-- Availability: REAL (constant)
-- Source: cons__fct_oee.quality_pct

SELECT DISTINCT quality_pct
FROM snowcomotive.cons.cons__fct_oee
LIMIT 1;
```

---

#### PD-A8 — Quality % delta

| Field | Content |
|-------|---------|
| **UI description** | Delta text, always "+0.0pp vs. prior week" |
| **Availability** | REAL (constant) |
| **Source table(s)** | N/A -- always 0 |

**Notes**: No query needed. Hardcode delta = 0 in display layer.

---

### 1B. Asset Risk Summary (6 elements per machine, 3 machines)

---

#### PD-B1 — Machine name

| Field | Content |
|-------|---------|
| **UI description** | Bold name in asset risk row, e.g. "CNC Boring" |
| **Availability** | REAL |
| **Source table(s)** | `cons__dim_equipment.equipment_name` |

#### PD-B2 — Line name

| Field | Content |
|-------|---------|
| **UI description** | Subtitle text, e.g. "Caliper line" |
| **Availability** | REAL |
| **Source table(s)** | `cons__dim_equipment.line_name` |

#### PD-B3 — RUL hours

| Field | Content |
|-------|---------|
| **UI description** | Stat value, e.g. "96 hrs" with label "RUL" |
| **Availability** | REAL |
| **Source table(s)** | `cons__fct_priority_score.predicted_rul_hours` |

#### PD-B4 — Hours since service

| Field | Content |
|-------|---------|
| **UI description** | Stat value, e.g. "210 hrs" with label "Since service" |
| **Availability** | REAL |
| **Source table(s)** | `cons__fct_sensor_reading.hours_since_last_service` (latest reading per equipment) |

```sql
-- Element: PD-B4 (Hours since service -- latest per equipment)
-- Availability: REAL
-- Source: cons__fct_sensor_reading

SELECT
    equipment_id,
    hours_since_last_service
FROM snowcomotive.cons.cons__fct_sensor_reading
QUALIFY ROW_NUMBER() OVER (PARTITION BY equipment_id ORDER BY reading_ts DESC) = 1;
```

**Notes**: Uses QUALIFY ROW_NUMBER pattern to get the latest reading per equipment. This is the only element that reads from `cons__fct_sensor_reading` outside Diagnostics.

---

#### PD-B5 — Health badge (Healthy / Watch / At-Risk)

| Field | Content |
|-------|---------|
| **UI description** | Colored badge: green "Healthy", yellow "Watch", red "At-Risk" |
| **Availability** | DERIVED |
| **Source table(s)** | `cons__fct_priority_score.priority_score` -> application-level threshold mapping |

```sql
-- Element: PD-B5 (Health badge -- threshold mapping)
-- Availability: DERIVED
-- Source: cons__fct_priority_score.priority_score + app-level constants

SELECT
    equipment_id,
    priority_score,
    CASE
        WHEN priority_score < 40 THEN 'healthy'
        WHEN priority_score < 60 THEN 'watch'
        ELSE 'at-risk'
    END AS health_status
FROM snowcomotive.cons.cons__fct_priority_score;
```

**Notes**: Thresholds (40, 60) are application-level constants defined at top of this doc. The dbt pipeline does not codify them -- the Streamlit app owns them.

---

#### PD-B6 — Priority score (sort order)

| Field | Content |
|-------|---------|
| **UI description** | Not displayed directly; drives sort order of asset risk list (highest priority first) |
| **Availability** | REAL |
| **Source table(s)** | `cons__fct_priority_score.priority_score` |

**Notes**: Used as `ORDER BY priority_score DESC` on the asset risk query.

---

#### Combined query for SB (all fields)

```sql
-- Elements: PD-B1 through PD-B6 (Asset Risk Summary -- combined)
-- Availability: REAL + DERIVED (badge)
-- Source: cons__fct_priority_score JOIN cons__dim_equipment + cons__fct_sensor_reading

WITH latest_service AS (
    SELECT
        equipment_id,
        hours_since_last_service
    FROM snowcomotive.cons.cons__fct_sensor_reading
    QUALIFY ROW_NUMBER() OVER (PARTITION BY equipment_id ORDER BY reading_ts DESC) = 1
)
SELECT
    eq.equipment_name,                                          -- PD-B1
    eq.line_name,                                               -- PD-B2
    ps.predicted_rul_hours,                                     -- PD-B3
    ls.hours_since_last_service,                                -- PD-B4
    CASE
        WHEN ps.priority_score < 40 THEN 'healthy'
        WHEN ps.priority_score < 60 THEN 'watch'
        ELSE 'at-risk'
    END AS health_status,                                       -- PD-B5
    ps.priority_score                                           -- PD-B6
FROM snowcomotive.cons.cons__fct_priority_score ps
JOIN snowcomotive.cons.cons__dim_equipment eq
    ON eq.equipment_id = ps.equipment_id
LEFT JOIN latest_service ls
    ON ls.equipment_id = ps.equipment_id
WHERE (:line_filter = 'all' OR eq.line_name = :line_filter)
ORDER BY ps.priority_score DESC;
```

---

### 1C. Financial Exposure (5 elements)

---

#### PD-C1 — Total Lost Revenue (plant-level headline)

| Field | Content |
|-------|---------|
| **UI description** | Big red dollar value, e.g. "$42,750" with note "17.1 breakdown hours x $2,500/hr blended rate" |
| **Availability** | **SYNTHETIC** |
| **Source table(s)** | `cons__fct_oee.breakdown_hours` x dbt var `cost_per_hour_of_downtime` |

```sql
-- Element: PD-C1 (Total Lost Revenue -- plant-level headline)
-- Availability: SYNTHETIC
-- PREREQUISITE: Add dbt var `cost_per_hour_of_downtime` to dbt_project.yml
--   (default: 2500). Until then, use a hardcoded constant in the Streamlit
--   app as a stopgap (same approach the mockup uses).

SELECT
    SUM(breakdown_hours) * 2500 AS total_lost_revenue,   -- replace 2500 with var('cost_per_hour_of_downtime')
    SUM(breakdown_hours) AS total_breakdown_hours
FROM snowcomotive.cons.cons__fct_oee
WHERE period_week = (SELECT MAX(period_week) FROM snowcomotive.cons.cons__fct_oee);
```

**Notes**: The mockup uses a fleet-wide blended rate ($2,500/hr). This is a single scalar, not per-equipment. The `cost_per_hour_of_downtime` dbt var does not exist yet in `dbt_project.yml` -- it needs to be added as a new var alongside `oee_performance_pct` / `oee_quality_pct`, or defined as a Streamlit app constant.

---

#### PD-C2 — Total breakdown hours (note text)

| Field | Content |
|-------|---------|
| **UI description** | Note line under the revenue card, e.g. "17.1 breakdown hours" |
| **Availability** | REAL |
| **Source table(s)** | `cons__fct_oee.breakdown_hours` |

**Notes**: Produced by the same query as PD-C1 (`total_breakdown_hours` column).

---

#### PD-C3 — Per-line Lost Revenue (per-line cards)

| Field | Content |
|-------|---------|
| **UI description** | Dollar value per line, e.g. "Caliper Line Lost Revenue: $7,282" with note "7.3h breakdown x throughput x unit revenue" |
| **Availability** | **SYNTHETIC** |
| **Source table(s)** | `cons__fct_oee.breakdown_hours` (per-equipment, via `cons__dim_equipment` join) x `cons__dim_equipment.throughput_units_per_hour` x `cons__dim_product.revenue_per_unit` |

```sql
-- Element: PD-C3 (Per-line Lost Revenue)
-- Availability: SYNTHETIC
-- PREREQUISITE: Add `revenue_per_unit` column to the cons__dim_product seed
--   (predictive_maintenance_dbt/seeds/cons__dim_product.csv). Current seed
--   columns are: product_id, product_name, variant. Suggested values based
--   on mockup: Brake Caliper $55/unit, Engine Head $145/unit. Until added,
--   hardcode in the Streamlit app.

-- NOTE: cons__fct_oee is line-grain, not equipment-grain. Per-equipment
-- breakdown_hours require going back to std__cmms_log. This query
-- approximates by splitting line-level breakdown proportionally.

WITH equipment_breakdown AS (
    SELECT
        eq.equipment_id,
        eq.line_name,
        eq.throughput_units_per_hour,
        eq.product_id,
        SUM(cm.duration_hours) AS breakdown_hours
    FROM snowcomotive.std.std__cmms_log cm
    JOIN snowcomotive.cons.cons__dim_equipment eq
        ON eq.equipment_id = cm.equipment_id
    WHERE cm.event_type = 'BREAKDOWN'
      AND DATE_TRUNC('week', cm.event_start_ts) =
          (SELECT MAX(period_week) FROM snowcomotive.cons.cons__fct_oee)
    GROUP BY eq.equipment_id, eq.line_name, eq.throughput_units_per_hour, eq.product_id
)
SELECT
    eb.line_name,
    SUM(eb.breakdown_hours * eb.throughput_units_per_hour * 55) AS line_lost_revenue,
    -- replace 55 with: p.revenue_per_unit once the seed column exists
    SUM(eb.breakdown_hours) AS line_breakdown_hours
FROM equipment_breakdown eb
-- LEFT JOIN snowcomotive.cons.cons__dim_product p
--     ON p.product_id = eb.product_id   -- uncomment once revenue_per_unit exists
GROUP BY eb.line_name;
```

**Notes**: The per-line calculation needs equipment-level breakdown hours (from `std__cmms_log`) because `cons__fct_oee` is line-grain. The `revenue_per_unit` column does not exist on the `cons__dim_product` seed -- it needs to be added (see seed file `predictive_maintenance_dbt/seeds/cons__dim_product.csv`). The product-to-line mapping goes through `cons__dim_equipment.product_id`.

---

#### PD-C4 — Per-line breakdown hours (note text)

| Field | Content |
|-------|---------|
| **UI description** | Note line under per-line card, e.g. "7.3h breakdown x throughput x unit revenue" |
| **Availability** | REAL |
| **Source table(s)** | `std__cmms_log` (same query as PD-C3, `line_breakdown_hours` column) |

---

#### PD-C5 — Blended rate caption

| Field | Content |
|-------|---------|
| **UI description** | Static text "$2,500/hr blended rate" shown under the plant-level card |
| **Availability** | **SYNTHETIC** |
| **Source table(s)** | dbt var `cost_per_hour_of_downtime` (does not exist yet) |

**Notes**: Until the dbt var is created, hardcode as a Streamlit app constant. Same prerequisite as PD-C1.

---

### 1D-left. Historical OEE Trend Chart (3 elements)

---

#### PD-D1 — OEE weekly series (per line, 12 weeks)

| Field | Content |
|-------|---------|
| **UI description** | Multi-line chart, one series per production line, 12 weekly data points |
| **Availability** | REAL |
| **Source table(s)** | `cons__fct_oee` |

```sql
-- Element: PD-D1 (OEE weekly series -- 12 weeks, per line)
-- Availability: REAL
-- Source: cons__fct_oee

SELECT
    line_name,
    period_week,
    oee_pct
FROM snowcomotive.cons.cons__fct_oee
WHERE period_week >= DATEADD(week, -12,
    (SELECT MAX(period_week) FROM snowcomotive.cons.cons__fct_oee))
ORDER BY line_name, period_week;
```

**Notes**: Display values as `oee_pct * 100` for the Y axis. The mockup uses random-walk data; real data comes from the `cons__fct_oee` table which computes OEE from CMMS breakdown events.

---

#### PD-D2 — Monthly rollup (3-point series)

| Field | Content |
|-------|---------|
| **UI description** | Same chart as PD-D1 but toggled to monthly view -- 3 data points per line (each averaging 4 weekly values) |
| **Availability** | DERIVED |
| **Source table(s)** | `cons__fct_oee` with 4-week bucketing |

```sql
-- Element: PD-D2 (OEE monthly rollup -- 3 months, per line)
-- Availability: DERIVED
-- Source: cons__fct_oee with calendar bucketing

WITH weekly AS (
    SELECT
        line_name,
        period_week,
        oee_pct,
        scheduled_hours,
        NTILE(3) OVER (PARTITION BY line_name ORDER BY period_week) AS month_bucket
    FROM snowcomotive.cons.cons__fct_oee
    WHERE period_week >= DATEADD(week, -12,
        (SELECT MAX(period_week) FROM snowcomotive.cons.cons__fct_oee))
)
SELECT
    line_name,
    month_bucket,
    MIN(period_week) AS bucket_start,
    SUM(oee_pct * scheduled_hours) / SUM(scheduled_hours) AS avg_oee_pct
FROM weekly
GROUP BY line_name, month_bucket
ORDER BY line_name, month_bucket;
```

---

#### PD-D3 — Granularity toggle (Weekly / Monthly)

| Field | Content |
|-------|---------|
| **UI description** | Dropdown: "Weekly" or "Monthly" |
| **Availability** | N/A |
| **Source table(s)** | N/A -- UI state only, changes aggregation grain |

---

### 1D-right. Predicted Availability Forecast (5 elements)

---

#### PD-D4 — Predicted availability series (per line, 8 weeks)

| Field | Content |
|-------|---------|
| **UI description** | Dual-axis chart, left Y: predicted weekly availability per line over 8 future weeks |
| **Availability** | **SYNTHETIC** |
| **Source table(s)** | Would require: `cons__fct_rul_prediction` + `cons__fct_oee` + derivation logic |

```sql
-- Element: PD-D4 (Predicted availability -- 8-week forecast)
-- Availability: SYNTHETIC
-- NOT BUILDABLE YET -- no model currently projects weekly availability into the future.
--
-- PREREQUISITE: A new derivation (arguably its own story) that:
--   1. Takes latest predicted_rul_hours per equipment from cons__fct_rul_prediction
--   2. Estimates when each machine fails (reading_ts + predicted_rul_hours)
--   3. Maps failure windows to weekly availability loss per line
--      (each machine's downtime = PM duration estimate, spread across the
--       week it falls in, divided by that line's scheduled_hours)
--   4. Blends with historical OEE baseline from cons__fct_oee to produce
--      a per-line weekly availability forecast
--
-- Sketch of what the OUTPUT shape would look like (not the derivation):

-- SELECT
--     line_name,
--     forecast_week,          -- DATE, 8 future weeks
--     predicted_availability  -- 0-1 ratio
-- FROM <new_forecast_model>
-- ORDER BY line_name, forecast_week;
```

**Notes**: This is the most complex synthetic gap. The mockup hardcodes 8 values per line. Building the real derivation requires combining RUL predictions with scheduled maintenance windows and historical baseline availability. Invariant #4 applies: any forecast chart must include the caveat that predictions are directional, not calibrated.

---

#### PD-D5 — Order demand overlay (right Y-axis)

| Field | Content |
|-------|---------|
| **UI description** | Dashed line series on right Y-axis showing order demand per line over 8 future weeks |
| **Availability** | DERIVED (partial) |
| **Source table(s)** | `cons__fct_order` + `cons__dim_equipment` (for product-to-line mapping) |

```sql
-- Element: PD-D5 (Order demand overlay -- 8-week future)
-- Availability: DERIVED (partial -- depends on data generator producing future-dated orders)
-- Source: cons__fct_order + cons__dim_equipment

SELECT
    eq.line_name,
    o.order_week,
    SUM(o.order_units) AS weekly_order_units
FROM snowcomotive.cons.cons__fct_order o
JOIN snowcomotive.cons.cons__dim_equipment eq
    ON eq.product_id = o.product_id
    AND eq.variant = o.variant
WHERE o.order_week > (SELECT MAX(period_week) FROM snowcomotive.cons.cons__fct_oee)
GROUP BY eq.line_name, o.order_week
ORDER BY eq.line_name, o.order_week;
```

**Notes**: Depends on the data generator producing future-dated sales orders. If only historical orders exist, a trailing extrapolation could substitute (e.g. repeat trailing 4-week average), but that is not currently built. The join from `cons__fct_order` to line requires going through `cons__dim_equipment` on `(product_id, variant)`.

---

#### PD-D6 — Risk window shading

| Field | Content |
|-------|---------|
| **UI description** | Semi-transparent red/orange overlay on the forecast chart marking weeks where a line's RUL-based failure window overlaps high demand |
| **Availability** | **SYNTHETIC** |
| **Source table(s)** | Would require same derivation as PD-D4 + demand overlap logic |

```sql
-- Element: PD-D6 (Risk window shading)
-- Availability: SYNTHETIC
-- NOT BUILDABLE YET -- same prerequisite as PD-D4.
--
-- PREREQUISITE: Once PD-D4's forecast model exists, risk windows are
--   identified as weeks where predicted_availability < some threshold
--   AND order_demand > trailing average (supply-demand squeeze).
--   The mockup defines two risk windows:
--     - Caliper: weeks 2-3 ("CNC Boring failure window x demand peak")
--     - Engine Head: weeks 5-6 ("CNC Horizontal overdue PM x wk5-8 uptick")
--
-- Output shape:
-- SELECT line_name, start_week INT, end_week INT, label TEXT
-- FROM <risk_window_derivation>;
```

---

#### PD-D7 — Risk callout text

| Field | Content |
|-------|---------|
| **UI description** | Warning banner below chart, e.g. "Caliper: CNC Boring failure window x demand peak (weeks 2-3)" |
| **Availability** | **SYNTHETIC** |
| **Source table(s)** | Same derivation as PD-D6 |

**Notes**: Text is generated from the risk window data. Same prerequisite as PD-D4/PD-D6.

---

#### PD-D8 — Honesty caveat

| Field | Content |
|-------|---------|
| **UI description** | Static disclaimer text near the forecast chart (invariant #4) |
| **Availability** | N/A |
| **Source table(s)** | N/A -- always displayed |

**Notes**: Suggested text: "Predicted failure week is directional, not calibrated (concordance index 0.94, median absolute error ~182h on 5 test rows). Forecasts should be treated as indicative, not precise."

---

## 2. Production (15 elements)

### 2A. Order Trend Chart (2 elements)

---

#### PR-A1 — Historical order volume (per line, 12 weeks)

| Field | Content |
|-------|---------|
| **UI description** | Multi-line chart showing order units per production line over 12 trailing weeks |
| **Availability** | REAL |
| **Source table(s)** | `cons__fct_order` + `cons__dim_equipment` |

```sql
-- Element: PR-A1 (Historical order volume -- 12 weeks, per line)
-- Availability: REAL
-- Source: cons__fct_order + cons__dim_equipment (product-to-line mapping)

SELECT
    eq.line_name,
    o.order_week,
    SUM(o.order_units) AS weekly_order_units
FROM snowcomotive.cons.cons__fct_order o
JOIN snowcomotive.cons.cons__dim_equipment eq
    ON eq.product_id = o.product_id
    AND eq.variant = o.variant
WHERE o.order_week >= DATEADD(week, -12,
    (SELECT MAX(order_week) FROM snowcomotive.cons.cons__fct_order))
  AND o.order_week <= (SELECT MAX(order_week) FROM snowcomotive.cons.cons__fct_order)
GROUP BY eq.line_name, o.order_week
ORDER BY eq.line_name, o.order_week;
```

**Notes**: The join from `cons__fct_order` (keyed on `product_id, variant`) to line goes through `cons__dim_equipment`. Multiple equipment on the same line/product will produce the same line-level total (which is correct -- orders are per product, not per machine).

---

#### PR-A2 — 4-week rolling average overlay

| Field | Content |
|-------|---------|
| **UI description** | Dashed line overlay on the order trend chart, showing 4-week trailing average per line |
| **Availability** | DERIVED |
| **Source table(s)** | `cons__fct_order` + window function |

```sql
-- Element: PR-A2 (4-week rolling average overlay)
-- Availability: DERIVED
-- Source: cons__fct_order + cons__dim_equipment + window function

WITH weekly AS (
    SELECT
        eq.line_name,
        o.order_week,
        SUM(o.order_units) AS weekly_order_units
    FROM snowcomotive.cons.cons__fct_order o
    JOIN snowcomotive.cons.cons__dim_equipment eq
        ON eq.product_id = o.product_id
        AND eq.variant = o.variant
    WHERE o.order_week >= DATEADD(week, -15,
        (SELECT MAX(order_week) FROM snowcomotive.cons.cons__fct_order))
    GROUP BY eq.line_name, o.order_week
)
SELECT
    line_name,
    order_week,
    weekly_order_units,
    AVG(weekly_order_units) OVER (
        PARTITION BY line_name
        ORDER BY order_week
        ROWS BETWEEN 3 PRECEDING AND CURRENT ROW
    ) AS rolling_4wk_avg
FROM weekly
ORDER BY line_name, order_week;
```

**Notes**: Fetches 15 weeks to ensure the rolling average has enough lookback for the first displayed week. The overlay is displayed as a dashed line.

---

### 2B. Future Orders Bar Chart (1 element)

---

#### PR-B1 — Future weekly order volume (per line, 4 weeks)

| Field | Content |
|-------|---------|
| **UI description** | Grouped bar chart showing future order units per line for 4 upcoming weeks |
| **Availability** | DERIVED (data-gen dependent) |
| **Source table(s)** | `cons__fct_order` + `cons__dim_equipment` |

```sql
-- Element: PR-B1 (Future orders -- 4-week look-ahead, per line)
-- Availability: DERIVED (data-gen dependent)
-- Source: cons__fct_order + cons__dim_equipment
-- PREREQUISITE: The data generator must produce future-dated rows in
--   raw.sales_order (order_week > current week). If only historical data
--   exists, this chart will be empty.

SELECT
    eq.line_name,
    o.order_week,
    SUM(o.order_units) AS weekly_order_units
FROM snowcomotive.cons.cons__fct_order o
JOIN snowcomotive.cons.cons__dim_equipment eq
    ON eq.product_id = o.product_id
    AND eq.variant = o.variant
WHERE o.order_week > CURRENT_DATE
GROUP BY eq.line_name, o.order_week
ORDER BY eq.line_name, o.order_week
LIMIT 8;  -- 4 weeks x 2 lines max
```

---

### 2C. Production Batch Gap (6 elements)

---

#### PR-C1 — Predicted RUL hours

| Field | Content |
|-------|---------|
| **UI description** | Value in diverging bar row, e.g. "RUL 96h" |
| **Availability** | REAL |
| **Source table(s)** | `cons__fct_priority_score.predicted_rul_hours` |

#### PR-C2 — Required run hours (next 4wk)

| Field | Content |
|-------|---------|
| **UI description** | Value in diverging bar row, e.g. "Need 134h" |
| **Availability** | REAL |
| **Source table(s)** | `cons__fct_priority_score.required_run_hours_next_4wk` |

#### PR-C3 — Gap value (RUL - required)

| Field | Content |
|-------|---------|
| **UI description** | Diverging bar length + value label, e.g. "-38h" (red) or "+206h" (green) |
| **Availability** | REAL |
| **Source table(s)** | Computed: `cons__fct_priority_score.predicted_rul_hours - required_run_hours_next_4wk` |

#### PR-C4 — Color coding (green / yellow / red)

| Field | Content |
|-------|---------|
| **UI description** | Bar color: red if gap < 0, yellow if gap/required < 0.5, green otherwise |
| **Availability** | DERIVED |
| **Source table(s)** | Threshold logic on PR-C3 |

#### PR-C5 — Machine name + line

| Field | Content |
|-------|---------|
| **UI description** | Label on each bar row, e.g. "CNC Boring / Caliper" |
| **Availability** | REAL |
| **Source table(s)** | `cons__dim_equipment.equipment_name`, `cons__dim_equipment.line_name` |

#### PR-C6 — RUL caveat (invariant #4)

| Field | Content |
|-------|---------|
| **UI description** | Static disclaimer below the chart |
| **Availability** | N/A |
| **Source table(s)** | N/A -- always displayed |

**Notes**: Text: "Predicted failure week is directional, not calibrated (concordance index 0.94, median absolute error ~182h on 5 test rows). Gap values should be treated as indicative, not precise."

---

#### Combined query for SC (all batch gap fields)

```sql
-- Elements: PR-C1 through PR-C5 (Production Batch Gap -- combined)
-- Availability: REAL + DERIVED (color)
-- Source: cons__fct_priority_score + cons__dim_equipment

SELECT
    eq.equipment_name,                                              -- PR-C5
    eq.line_name,                                                   -- PR-C5
    ps.predicted_rul_hours,                                         -- PR-C1
    ps.required_run_hours_next_4wk,                                 -- PR-C2
    ps.predicted_rul_hours - ps.required_run_hours_next_4wk AS gap, -- PR-C3
    CASE                                                            -- PR-C4
        WHEN ps.predicted_rul_hours - ps.required_run_hours_next_4wk < 0 THEN 'red'
        WHEN ps.required_run_hours_next_4wk > 0
            AND (ps.predicted_rul_hours - ps.required_run_hours_next_4wk)
                / ps.required_run_hours_next_4wk < 0.5 THEN 'yellow'
        ELSE 'green'
    END AS gap_color
FROM snowcomotive.cons.cons__fct_priority_score ps
JOIN snowcomotive.cons.cons__dim_equipment eq
    ON eq.equipment_id = ps.equipment_id
ORDER BY gap ASC;
```

---

### 2D. Machine Health Impact (5 elements per machine)

---

#### PR-D1 — Demand pressure score

| Field | Content |
|-------|---------|
| **UI description** | Stat value in health impact row, e.g. "92" with label "Demand press." |
| **Availability** | REAL |
| **Source table(s)** | `cons__fct_priority_score.demand_pressure` |

#### PR-D2 — RUL hours

| Field | Content |
|-------|---------|
| **UI description** | Stat value, e.g. "96 hrs" with label "RUL" |
| **Availability** | REAL |
| **Source table(s)** | `cons__fct_priority_score.predicted_rul_hours` |

#### PR-D3 — Throughput

| Field | Content |
|-------|---------|
| **UI description** | Stat value, e.g. "18/hr" with label "Throughput" |
| **Availability** | REAL |
| **Source table(s)** | `cons__dim_equipment.throughput_units_per_hour` |

#### PR-D4 — Health badge

| Field | Content |
|-------|---------|
| **UI description** | Colored badge (same logic as PD-B5) |
| **Availability** | DERIVED |
| **Source table(s)** | `cons__fct_priority_score.priority_score` -> app-level threshold |

#### PR-D5 — Machine name + line

| Field | Content |
|-------|---------|
| **UI description** | Identity in health impact row |
| **Availability** | REAL |
| **Source table(s)** | `cons__dim_equipment.equipment_name`, `cons__dim_equipment.line_name` |

---

#### Combined query for SD (all health impact fields)

```sql
-- Elements: PR-D1 through PR-D5 (Machine Health Impact -- combined)
-- Availability: REAL + DERIVED (badge)
-- Source: cons__fct_priority_score + cons__dim_equipment

SELECT
    eq.equipment_name,                                              -- PR-D5
    eq.line_name,                                                   -- PR-D5
    ps.demand_pressure,                                             -- PR-D1
    ps.predicted_rul_hours,                                         -- PR-D2
    eq.throughput_units_per_hour,                                   -- PR-D3
    CASE                                                            -- PR-D4
        WHEN ps.priority_score < 40 THEN 'healthy'
        WHEN ps.priority_score < 60 THEN 'watch'
        ELSE 'at-risk'
    END AS health_status,
    ps.priority_score
FROM snowcomotive.cons.cons__fct_priority_score ps
JOIN snowcomotive.cons.cons__dim_equipment eq
    ON eq.equipment_id = ps.equipment_id
ORDER BY ps.demand_pressure DESC;
```

---

## 3. Diagnostics (12 elements)

### 3A. Line Selector (1 element)

---

#### DX-A1 — Line options (All / Caliper / Engine Head)

| Field | Content |
|-------|---------|
| **UI description** | Dropdown with "All Lines", plus one entry per distinct production line |
| **Availability** | REAL |
| **Source table(s)** | `cons__dim_equipment.line_name` |

```sql
-- Element: DX-A1 (Line selector options)
-- Availability: REAL
-- Source: cons__dim_equipment

SELECT DISTINCT line_name
FROM snowcomotive.cons.cons__dim_equipment
WHERE is_sensor_enabled
ORDER BY line_name;
```

**Notes**: Prepend an "All Lines" option in the Streamlit layer. Filter `is_sensor_enabled = true` to exclude non-sensor equipment.

---

### 3B. Sensor Diagnostics (5 element types: 3 series + badge + label)

---

#### DX-B1 — Vibration series (~24 ticks per machine)

| Field | Content |
|-------|---------|
| **UI description** | Mini line chart of raw vibration readings, ~6h window (24 ticks at 15-min cadence) |
| **Availability** | REAL |
| **Source table(s)** | `cons__fct_sensor_reading` |

#### DX-B2 — Temperature series

| Field | Content |
|-------|---------|
| **UI description** | Mini line chart of raw temperature readings |
| **Availability** | REAL |
| **Source table(s)** | `cons__fct_sensor_reading` |

#### DX-B3 — RPM series

| Field | Content |
|-------|---------|
| **UI description** | Mini line chart of raw RPM readings |
| **Availability** | REAL |
| **Source table(s)** | `cons__fct_sensor_reading` |

```sql
-- Elements: DX-B1, DX-B2, DX-B3 (Sensor series -- per machine, per type)
-- Availability: REAL
-- Source: cons__fct_sensor_reading (invariant #2: raw sensor values, not FEAST z-scores)

SELECT
    sr.equipment_id,
    eq.equipment_name,
    eq.line_name,
    sr.sensor_type,
    sr.reading_ts,
    sr.reading_value
FROM snowcomotive.cons.cons__fct_sensor_reading sr
JOIN snowcomotive.cons.cons__dim_equipment eq
    ON eq.equipment_id = sr.equipment_id
WHERE sr.reading_ts >= DATEADD(hour, -6, CURRENT_TIMESTAMP())
  AND (:line_filter = 'all' OR eq.line_name = :line_filter)
ORDER BY sr.equipment_id, sr.sensor_type, sr.reading_ts;
```

**Notes**: Invariant #2 -- sensor charts read from `cons__fct_sensor_reading` (raw values), NOT from FEAST z-scores. The `sensor_type` column contains values: `'vibration'`, `'temperature'`, `'rpm'`. Pivot in the Streamlit layer to produce one chart per sensor type per machine. Invariant #5 (data-gen gap handling): use `split_trend_and_latest_tick()` pattern if applicable.

---

#### DX-B4 — Health badge on summary row

| Field | Content |
|-------|---------|
| **UI description** | Colored badge on each machine's expandable section header |
| **Availability** | DERIVED |
| **Source table(s)** | `cons__fct_priority_score.priority_score` -> app-level threshold |

**Notes**: Same threshold logic as PD-B5. Can be fetched from the same combined query used for the priority table (section 3C).

---

#### DX-B5 — Machine name + line label

| Field | Content |
|-------|---------|
| **UI description** | Summary row text, e.g. "CNC Boring -- Caliper line -- raw sensor detail (vibration / temperature / RPM)" |
| **Availability** | REAL |
| **Source table(s)** | `cons__dim_equipment.equipment_name`, `cons__dim_equipment.line_name` |

---

### 3C. Priority Risk Score Table (8 elements)

---

#### DX-C1 — Priority score (0-100)

| Field | Content |
|-------|---------|
| **UI description** | Numeric value in score column, e.g. "62" |
| **Availability** | REAL |
| **Source table(s)** | `cons__fct_priority_score.priority_score` |

**Notes**: Already computed by the dbt model as `100 * (0.40 * rul_urgency + 0.25 * demand_pressure + 0.20 * inventory_buffer + 0.15 * spare_part_readiness)`. Invariant #3: display code must NOT recompute.

---

#### DX-C2 — RUL urgency

| Field | Content |
|-------|---------|
| **UI description** | Numeric value in rul_urgency column |
| **Availability** | REAL |
| **Source table(s)** | `cons__fct_priority_score.rul_urgency` |

#### DX-C3 — Demand pressure

| Field | Content |
|-------|---------|
| **UI description** | Numeric value in demand_pressure column |
| **Availability** | REAL |
| **Source table(s)** | `cons__fct_priority_score.demand_pressure` |

#### DX-C4 — Inventory buffer

| Field | Content |
|-------|---------|
| **UI description** | Numeric value in inventory_buffer column |
| **Availability** | REAL |
| **Source table(s)** | `cons__fct_priority_score.inventory_buffer` |

#### DX-C5 — Spare part readiness

| Field | Content |
|-------|---------|
| **UI description** | Numeric value in spare_part_readiness column |
| **Availability** | REAL |
| **Source table(s)** | `cons__fct_priority_score.spare_part_readiness` |

---

#### DX-C6 — Stacked factor bar

| Field | Content |
|-------|---------|
| **UI description** | Horizontal bar with 4 color segments proportional to each factor's value |
| **Availability** | REAL |
| **Source table(s)** | Same 4 factor columns; widths = `factor_value / (rul_urgency + demand_pressure + inventory_buffer + spare_part_readiness)` |

**Notes**: Pure display logic. Bar segment widths are proportional to each raw factor value (not the weighted contribution). The tooltip shows factor values with their weights: rul_urgency (w=0.40), demand_pressure (w=0.25), inventory_buffer (w=0.20), spare_part_readiness (w=0.15).

---

#### DX-C7 — "Survives demand?" badge

| Field | Content |
|-------|---------|
| **UI description** | Green "Yes" or red "No" badge |
| **Availability** | REAL |
| **Source table(s)** | `cons__fct_priority_score.predicted_rul_hours`, `cons__fct_priority_score.required_run_hours_next_4wk` |

**Notes**: Logic: `predicted_rul_hours > required_run_hours_next_4wk` -> "Yes" (green), else "No" (red).

---

#### DX-C8 — Machine name + line

| Field | Content |
|-------|---------|
| **UI description** | First column in table row, e.g. "CNC Boring / Caliper line" |
| **Availability** | REAL |
| **Source table(s)** | `cons__dim_equipment.equipment_name`, `cons__dim_equipment.line_name` |

---

#### Combined query for SC (full priority table)

```sql
-- Elements: DX-C1 through DX-C8 (Priority Risk Score Table -- combined)
-- Availability: REAL + DERIVED (badge)
-- Source: cons__fct_priority_score + cons__dim_equipment

SELECT
    eq.equipment_name,                                                          -- DX-C8
    eq.line_name,                                                               -- DX-C8
    ps.priority_score,                                                          -- DX-C1
    ps.rul_urgency,                                                             -- DX-C2
    ps.demand_pressure,                                                         -- DX-C3
    ps.inventory_buffer,                                                        -- DX-C4
    ps.spare_part_readiness,                                                    -- DX-C5
    -- DX-C6: factor bar widths (compute in app layer from the 4 values above)
    ps.predicted_rul_hours,                                                     -- DX-C7 input
    ps.required_run_hours_next_4wk,                                             -- DX-C7 input
    ps.predicted_rul_hours > ps.required_run_hours_next_4wk AS survives_demand, -- DX-C7
    ps.anomaly_score,                                                            -- available but not used for badge
    ps.priority_score                                                            -- for DX-B4 badge
FROM snowcomotive.cons.cons__fct_priority_score ps
JOIN snowcomotive.cons.cons__dim_equipment eq
    ON eq.equipment_id = ps.equipment_id
WHERE (:line_filter = 'all' OR eq.line_name = :line_filter)
ORDER BY ps.priority_score DESC;
```

---

## 4. Availability Summary

| Status | Count | Elements |
|--------|-------|----------|
| **REAL** | 32 | PD-A1, PD-A3, PD-A5, PD-A7, PD-A6, PD-A8, PD-B1-B4, PD-B6, PD-C2, PD-C4, PD-D1, PR-A1, PR-B1 (partial), PR-C1-C3, PR-C5, PR-D1-D3, PR-D5, DX-A1, DX-B1-B3, DX-B5, DX-C1-C8 |
| **DERIVED** | 10 | PD-A2, PD-A4, PD-B5, PD-D2, PD-D5, PR-A2, PR-C4, PR-D4, DX-B4, PR-B1 |
| **SYNTHETIC** | 8 | PD-C1, PD-C3, PD-C5, PD-D4, PD-D5 (partial), PD-D6, PD-D7 |
| **N/A** | 2 | PD-D3, PD-D8, PR-C6 |

### Synthetic Gaps (blocking real data wiring)

1. **`cons__dim_product.revenue_per_unit`** -- column does not exist on the seed (`predictive_maintenance_dbt/seeds/cons__dim_product.csv`, current columns: `product_id, product_name, variant`). Needed for PD-C3 (per-line lost revenue). **Action**: add a `revenue_per_unit` FLOAT column to the CSV seed.

2. **`cost_per_hour_of_downtime` dbt var** -- not in `dbt_project.yml` (current vars: `oee_performance_pct`, `oee_quality_pct`, `target_lag`). Needed for PD-C1 (plant-level lost revenue) and PD-C5 (blended rate caption). **Action**: add to `dbt_project.yml` vars block with env var override pattern matching the existing vars: `cost_per_hour_of_downtime: "{{ env_var('COST_PER_HOUR_OF_DOWNTIME', '2500') }}"`.

3. **8-week predicted availability forecast** (PD-D4, PD-D6, PD-D7) -- no model currently projects weekly availability into the future. Would need: latest `predicted_rul_hours` per equipment -> estimate failure timing -> map to weekly availability loss per line -> blend with historical OEE baseline. This is a non-trivial derivation, arguably its own story.

4. **Health badge thresholds** (PD-B5, PR-D4, DX-B4) -- the mockup uses `priority_score` buckets (`healthy < 40`, `watch 40-60`, `at-risk >= 60`). These are NOT codified in the dbt pipeline. Documented as application-level constants at the top of this file. The Streamlit app owns them.

---

## 5. Table Reference (columns used by this mapping)

| Table | Columns referenced | Layer |
|-------|--------------------|-------|
| `cons__dim_equipment` | `equipment_id`, `equipment_name`, `line_name`, `product_id`, `variant`, `is_sensor_enabled`, `throughput_units_per_hour` | Consumption |
| `cons__dim_product` (seed) | `product_id`, `product_name`, `variant` | Consumption (seed) |
| `cons__fct_oee` | `line_name`, `period_week`, `scheduled_hours`, `breakdown_hours`, `availability_pct`, `performance_pct`, `quality_pct`, `oee_pct` | Consumption |
| `cons__fct_priority_score` | `equipment_id`, `score_ts`, `predicted_rul_hours`, `is_anomaly`, `anomaly_score`, `rul_urgency`, `demand_pressure`, `inventory_buffer`, `spare_part_readiness`, `priority_score`, `required_run_hours_next_4wk` | Consumption (dynamic table) |
| `cons__fct_sensor_reading` | `equipment_id`, `reading_ts`, `sensor_type`, `reading_value`, `hours_since_last_service` | Consumption (dynamic table) |
| `cons__fct_order` | `order_week`, `product_id`, `variant`, `order_units` | Consumption |
| `cons__fct_rul_prediction` | `equipment_id`, `reading_ts`, `predicted_rul_hours`, `is_anomaly`, `anomaly_score` | Consumption (dynamic table) |
| `std__cmms_log` | `equipment_id`, `event_start_ts`, `event_type`, `duration_hours` | Standardized (used for PD-C3 per-equipment breakdown) |
