# SH-70 — UI Element Data Mapping Design (Continuation)
## SnowComotive — Predictive Maintenance & OEE Command Center

**Story**: SH-70 (continuation — same branch `feature/SH-7-70-streamlit-ux-revamp-spike`)
**Parent design**: `docs/designs/SH-70-streamlit-ux-revamp.md`
**Status**: Frozen (user-confirmed)

---

## 1. Deliverable

A single markdown file `mockup_v2/DATA_MAP.md` structured page-by-page (Plant Dashboard, Production, Diagnostics — Chat excluded by decision). For every displayed UI element (KPI card, chart, list row, delta value, badge, bar segment), the doc provides:

| Field | Content |
|-------|---------|
| **Element ID** | Short unique key, e.g. `PD-A1` (Plant Dashboard, Section A, element 1) |
| **UI description** | What the user sees (label, value shape, delta text) |
| **Source table(s)** | Fully-qualified Snowflake table path(s) through the dbt lineage: RAW → STD → CONS (and FEAST where applicable) |
| **SQL sketch** | Runnable-ish Snowflake SQL that computes the value — copy-pasteable as a starting point for the real Streamlit query function. Uses `snowcomotive.cons.*` table names directly. |
| **Availability** | One of: **REAL** (backed by existing pipeline data), **SYNTHETIC** (needs new column/table/var not yet built), **DERIVED** (computable from existing data but the specific query isn't built yet — e.g. week-over-week delta needs a LAG window) |
| **Notes** | Invariants to preserve, caveats, or dependencies on other elements |

---

## 2. Scope — Pages & Sections Covered

### Chat page — excluded
100% mock (hardcoded JS scripts, history, tool chips). No data-source mapping needed — the real backing is Cortex Agent API calls + `st.session_state`, not SQL queries.

### Pages in scope

| Page | Sections | Approx. element count |
|------|----------|-----------------------|
| Plant Dashboard | §A: 4 KPI cards (OEE, Availability, Performance, Quality) + deltas; §B: Asset risk list (3 machines × 4 fields each); §C: Financial exposure (plant total + per-line cards); §D-left: OEE trend chart; §D-right: Forecast availability dual-axis chart + risk callouts | ~25 |
| Production | §A: Order trend chart + rolling avg overlay; §B: Future orders bar chart; §C: Batch gap diverging bar (3 machines × gap/RUL/required); §D: Machine health impact list (3 machines × 4 fields each) | ~15 |
| Diagnostics | §A: Line selector; §B: Sensor charts (3 machines × 3 sensor types); §C: Priority table (3 machines × score + 4 factors + factor bar + survives badge) | ~12 |
| **Total** | | **~52 elements** |

---

## 3. Full Element Inventory

Below is the exhaustive inventory grouped by page/section. Each entry specifies the mock data field it reads from `data.js`, enabling the Developer-agent to wire the mapping doc entries directly to the mockup's rendering functions.

### 3.1 Plant Dashboard

#### §A — KPI Cards (4 cards, each with value + delta)

| ID | Element | Mock data field | Real source | Availability |
|----|---------|----------------|-------------|--------------|
| PD-A1 | **OEE %** (value) | `oeeLatest[line].oee_pct` weighted by `scheduled_hours` | `cons__fct_oee.oee_pct` | REAL |
| PD-A2 | **OEE % delta** ("+X.Xpp vs. prior week") | `oee_pct - prev_oee_pct` | LAG over `cons__fct_oee` | DERIVED |
| PD-A3 | **Availability %** (value) | `oeeLatest[line].availability_pct` weighted | `cons__fct_oee.availability_pct` | REAL |
| PD-A4 | **Availability % delta** | `availability_pct - prev_availability_pct` | LAG over `cons__fct_oee` | DERIVED |
| PD-A5 | **Performance %** (value) | `MOCK.performancePct` (constant 0.95) | dbt var `oee_performance_pct` | REAL (constant) |
| PD-A6 | **Performance % delta** | Always 0 (same value both weeks) | N/A — constant | REAL (constant) |
| PD-A7 | **Quality %** (value) | `MOCK.qualityPct` (constant 0.98) | dbt var `oee_quality_pct` | REAL (constant) |
| PD-A8 | **Quality % delta** | Always 0 | N/A — constant | REAL (constant) |

#### §B — Asset Risk Summary (per machine)

| ID | Element | Mock data field | Real source | Availability |
|----|---------|----------------|-------------|--------------|
| PD-B1 | **Machine name** | `machines[].name` | `cons__dim_equipment.equipment_name` | REAL |
| PD-B2 | **Line name** | `machines[].line` | `cons__dim_equipment.line_name` | REAL |
| PD-B3 | **RUL hours** | `machines[].rul_hours` | `cons__fct_priority_score.predicted_rul_hours` (latest per equipment) | REAL |
| PD-B4 | **Hours since service** | `machines[].hours_since_last_service` | `cons__fct_sensor_reading.hours_since_last_service` (latest reading per equipment) | REAL |
| PD-B5 | **Health badge** (Healthy/Watch/At-Risk) | `machines[].status` derived from `anomaly_score` | `cons__fct_priority_score.anomaly_score` → threshold mapping | DERIVED |
| PD-B6 | **Anomaly score** (used for sort order) | `machines[].anomaly_score` | `cons__fct_priority_score.anomaly_score` | REAL |

#### §C — Financial Exposure

| ID | Element | Mock data field | Real source | Availability |
|----|---------|----------------|-------------|--------------|
| PD-C1 | **Total Lost Revenue** (plant-level headline) | `totalBreakdownHrs × cost_per_hour_of_downtime` | `SUM(cons__fct_oee.breakdown_hours)` for latest week × dbt var `cost_per_hour_of_downtime` | **SYNTHETIC** — `cost_per_hour_of_downtime` var doesn't exist yet |
| PD-C2 | **Total breakdown hours** (note text) | `totalBreakdownHrs` | `SUM(cons__fct_oee.breakdown_hours)` for latest week | REAL |
| PD-C3 | **Per-line Lost Revenue** (per-line cards) | `breakdownHours[equip] × throughput × revenue_per_unit` | `cons__fct_oee.breakdown_hours` per line × `cons__dim_equipment.throughput_units_per_hour` × `cons__dim_product.revenue_per_unit` | **SYNTHETIC** — `revenue_per_unit` column doesn't exist on `cons__dim_product` yet |
| PD-C4 | **Per-line breakdown hours** (note text) | `lineData[line].breakdownHrs` | `cons__fct_oee.breakdown_hours` filtered to line, latest week | REAL |
| PD-C5 | **Blended rate caption** | Static text `$2,500/hr` | dbt var | SYNTHETIC |

#### §D-left — Historical OEE Trend Chart

| ID | Element | Mock data field | Real source | Availability |
|----|---------|----------------|-------------|--------------|
| PD-D1 | **OEE weekly series** (per line, 12 weeks) | `oeeWeekly[line]` (random walk) | `cons__fct_oee.oee_pct` grouped by `line_name, period_week` | REAL |
| PD-D2 | **Monthly rollup** (3-point series) | `chunkAvg(oeeWeekly, 4)` | AVG of weekly OEE grouped into 4-week buckets | DERIVED |
| PD-D3 | **Granularity toggle** (Weekly/Monthly) | UI state only | N/A — just changes aggregation grain | N/A |

#### §D-right — Predicted Availability (8-week Forecast)

| ID | Element | Mock data field | Real source | Availability |
|----|---------|----------------|-------------|--------------|
| PD-D4 | **Predicted availability series** (per line, 8 weeks) | `forecast[line]` (hardcoded array) | **No direct backing** — would need RUL-to-weekly-availability derivation from `cons__fct_rul_prediction` + `cons__fct_oee` historical pattern | **SYNTHETIC** |
| PD-D5 | **Order demand overlay** (right Y-axis) | `forecast.demandOrders[line]` | `cons__fct_order` future weeks (if data gen produces them), else trailing extrapolation | DERIVED (partial) |
| PD-D6 | **Risk window shading** | `forecast.riskWindow` / `riskWindow2` | Derived from RUL + demand overlap logic — not a stored value | **SYNTHETIC** |
| PD-D7 | **Risk callout text** | `riskWindow.label` | Same derivation as PD-D6 | SYNTHETIC |
| PD-D8 | **Honesty caveat** | Static text (invariant #4) | N/A — always displayed | N/A |

### 3.2 Production

#### §A — Order Trend Chart

| ID | Element | Mock data field | Real source | Availability |
|----|---------|----------------|-------------|--------------|
| PR-A1 | **Historical order volume** (per line, 12 weeks) | `orderHistory[line]` | `cons__fct_order` joined to `cons__dim_equipment` (product→line mapping) aggregated by week | REAL |
| PR-A2 | **4-week rolling average overlay** | Computed in JS from `orderHistory` | Window function: `AVG(order_units) OVER (PARTITION BY line ROWS BETWEEN 3 PRECEDING AND CURRENT ROW)` | DERIVED |

#### §B — Future Orders Bar Chart

| ID | Element | Mock data field | Real source | Availability |
|----|---------|----------------|-------------|--------------|
| PR-B1 | **Future weekly order volume** (per line, 4 weeks) | `orderFuture[line]` | `cons__fct_order WHERE order_week > CURRENT_DATE` — depends on data gen producing future-dated orders | DERIVED (data-gen dependent) |

#### §C — Production Batch Gap (per machine)

| ID | Element | Mock data field | Real source | Availability |
|----|---------|----------------|-------------|--------------|
| PR-C1 | **Predicted RUL hours** | `priorityScores[].predicted_rul_hours` | `cons__fct_priority_score.predicted_rul_hours` | REAL |
| PR-C2 | **Required run hours (next 4wk)** | `priorityScores[].required_run_hours_next_4wk` | `cons__fct_priority_score.required_run_hours_next_4wk` | REAL |
| PR-C3 | **Gap value** (RUL − required) | `predicted_rul_hours - required_run_hours_next_4wk` | Subtraction of PR-C1 and PR-C2 | REAL |
| PR-C4 | **Color coding** (green/yellow/red) | Based on gap / required ratio | Threshold logic on PR-C3 | DERIVED |
| PR-C5 | **Machine name + line** | From `machineById()` | `cons__dim_equipment` | REAL |
| PR-C6 | **RUL caveat** (invariant #4) | Static text | N/A | N/A |

#### §D — Machine Health Impact (per machine, sorted by demand pressure)

| ID | Element | Mock data field | Real source | Availability |
|----|---------|----------------|-------------|--------------|
| PR-D1 | **Demand pressure score** | `priorityScores[].demand_pressure` | `cons__fct_priority_score.demand_pressure` | REAL |
| PR-D2 | **RUL hours** | `machines[].rul_hours` | `cons__fct_priority_score.predicted_rul_hours` | REAL |
| PR-D3 | **Throughput** | `machines[].throughput_units_per_hour` | `cons__dim_equipment.throughput_units_per_hour` | REAL |
| PR-D4 | **Health badge** | `machines[].status` | Same as PD-B5 | DERIVED |
| PR-D5 | **Machine name + line** | From `machines[]` | `cons__dim_equipment` | REAL |

### 3.3 Diagnostics

#### §A — Line Selector

| ID | Element | Mock data field | Real source | Availability |
|----|---------|----------------|-------------|--------------|
| DX-A1 | **Line options** (All/Caliper/Engine Head) | Hardcoded in JS | `SELECT DISTINCT line_name FROM cons__dim_equipment` | REAL |

#### §B — Sensor Diagnostics (per machine × 3 sensor types)

| ID | Element | Mock data field | Real source | Availability |
|----|---------|----------------|-------------|--------------|
| DX-B1 | **Vibration series** (~24 ticks) | `sensorSeries[equip].vibration` | `cons__fct_sensor_reading WHERE sensor_type = 'vibration'` latest N rows per equipment | REAL |
| DX-B2 | **Temperature series** | `sensorSeries[equip].temperature` | `cons__fct_sensor_reading WHERE sensor_type = 'temperature'` | REAL |
| DX-B3 | **RPM series** | `sensorSeries[equip].rpm` | `cons__fct_sensor_reading WHERE sensor_type = 'rpm'` | REAL |
| DX-B4 | **Health badge on summary row** | `machines[].status` | Same as PD-B5 | DERIVED |
| DX-B5 | **Machine name + line label** | `machines[]` | `cons__dim_equipment` | REAL |

#### §C — Priority Risk Score Table (per machine)

| ID | Element | Mock data field | Real source | Availability |
|----|---------|----------------|-------------|--------------|
| DX-C1 | **Priority score** (0–100) | `priorityScores[].priority_score` (computed via invariant #3) | `cons__fct_priority_score.priority_score` | REAL |
| DX-C2 | **RUL urgency** | `priorityScores[].rul_urgency` | `cons__fct_priority_score.rul_urgency` | REAL |
| DX-C3 | **Demand pressure** | `priorityScores[].demand_pressure` | `cons__fct_priority_score.demand_pressure` | REAL |
| DX-C4 | **Inventory buffer** | `priorityScores[].inventory_buffer` | `cons__fct_priority_score.inventory_buffer` | REAL |
| DX-C5 | **Spare part readiness** | `priorityScores[].spare_part_readiness` | `cons__fct_priority_score.spare_part_readiness` | REAL |
| DX-C6 | **Stacked factor bar** | Proportional widths from 4 factor values | Same 4 columns, widths = `factor_value / sum(all_factors)` | REAL |
| DX-C7 | **"Survives demand?" badge** | `predicted_rul_hours > required_run_hours_next_4wk` | `cons__fct_priority_score` columns | REAL |
| DX-C8 | **Machine name + line** | Via `machineById()` | `cons__dim_equipment` | REAL |

---

## 4. Availability Summary

| Status | Count | Notes |
|--------|-------|-------|
| **REAL** | ~32 | Directly queryable from existing consumption tables |
| **DERIVED** | ~10 | Computable from existing data via window function / aggregation / threshold logic — no new tables needed, just SQL |
| **SYNTHETIC** | ~8 | Requires new columns (`revenue_per_unit`), new dbt vars (`cost_per_hour_of_downtime`), or non-trivial derivation not yet built (8-week forecast, risk windows) |
| **N/A** | ~2 | Static text, UI controls |

### Key synthetic gaps (blocking real data wiring)

1. **`cons__dim_product.revenue_per_unit`** — column doesn't exist on the seed. Needed for PD-C3 (per-line lost revenue).
2. **`cost_per_hour_of_downtime` dbt var** — not in `dbt_project.yml`. Needed for PD-C1 (plant-level lost revenue).
3. **8-week predicted availability forecast** (PD-D4, PD-D6, PD-D7) — no model currently projects weekly availability into the future. Would need: take latest `predicted_rul_hours` per equipment → estimate when each machine fails → map failure windows to weekly availability loss per line → blend with historical OEE baseline. This is a non-trivial derivation, arguably its own story.
4. **Health badge thresholds** (PD-B5, PR-D4, DX-B4) — the mockup uses `anomaly_score` buckets (healthy < 0.3, watch 0.3–0.5, at-risk > 0.5). These thresholds are not codified in the dbt pipeline — the Streamlit app defines them. Document them in the mapping as application-level constants.

---

## 5. SQL Sketch Format

Each element's SQL sketch in the final `DATA_MAP.md` will follow this template:

```sql
-- Element: PD-A1 (OEE % — Plant Dashboard §A, card 1)
-- Availability: REAL
-- Source: cons__fct_oee

SELECT
    SUM(oee_pct * scheduled_hours) / SUM(scheduled_hours) AS plant_oee_pct
FROM snowcomotive.cons.cons__fct_oee
WHERE period_week = (SELECT MAX(period_week) FROM snowcomotive.cons.cons__fct_oee)
  AND (:line_filter = 'all' OR line_name = :line_filter);
```

For DERIVED elements, the sketch includes the window function / join that computes it:

```sql
-- Element: PD-A2 (OEE % delta — week-over-week change)
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
    - SUM(prev_oee_pct * scheduled_hours) / SUM(scheduled_hours) AS oee_delta
FROM ranked
WHERE period_week = (SELECT MAX(period_week) FROM snowcomotive.cons.cons__fct_oee)
  AND (:line_filter = 'all' OR line_name = :line_filter);
```

For SYNTHETIC elements, the sketch shows what the query would look like IF the prerequisite data existed, with a clear `-- PREREQUISITE:` header:

```sql
-- Element: PD-C1 (Total Lost Revenue — plant-level headline)
-- Availability: SYNTHETIC
-- PREREQUISITE: Add dbt var `cost_per_hour_of_downtime` to dbt_project.yml (default: 2500)

SELECT
    SUM(breakdown_hours) * 2500 AS total_lost_revenue  -- replace 2500 with var('cost_per_hour_of_downtime')
FROM snowcomotive.cons.cons__fct_oee
WHERE period_week = (SELECT MAX(period_week) FROM snowcomotive.cons.cons__fct_oee);
```

---

## 6. Named Invariants Referenced

These invariants (from the parent design doc §8) affect specific SQL sketches:

1. **OEE stored as 0–1 ratio** (invariant #1) — multiply by 100 at display time, never in SQL. Affects PD-A1/A3, PD-D1/D2.
2. **Sensor reads from `cons__fct_sensor_reading`** (invariant #2) — not from FEAST z-scores. Affects DX-B1/B2/B3.
3. **Priority score weights** (invariant #3) — `0.40 × rul_urgency + 0.25 × demand_pressure + 0.20 × inventory_buffer + 0.15 × spare_part_readiness`. Already computed in the dbt model; display code should NOT recompute. Affects DX-C1/C6.
4. **Predicted failure = directional, not calibrated** (invariant #4) — caveat text must appear near any RUL-derived calendar projection. Affects PR-C6, PD-D8.
5. **Data-gen gap handling** (invariant #6) — `split_trend_and_latest_tick()` pattern for sensor charts. Affects DX-B1/B2/B3.

---

## 7. Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Delivery format | Markdown doc only (`mockup_v2/DATA_MAP.md`) | Lower effort, easy to maintain, good developer reference |
| Detail level | Runnable SQL sketches | User wants copy-pasteable starting points for real Streamlit query functions |
| Chat page | Excluded | 100% mock agent interaction, no SQL data sources to map |
| Jira tracking | Stays in SH-70 | Same spike, deeper layer of the same work |

---

## 8. Open Items for Developer-agent

1. The final `DATA_MAP.md` will be ~300-400 lines. Structure it with collapsible sections per page for readability.
2. For the 8-week forecast (PD-D4/D6/D7), write the SQL sketch as a "what it would look like" placeholder with explicit `-- NOT BUILDABLE YET` markers — don't pretend it's a simple query.
3. Health badge threshold constants (healthy < 0.3 anomaly_score, watch 0.3–0.5, at-risk > 0.5) should be documented as application-level constants in the mapping, not SQL logic — the Streamlit layer owns them.
4. The `hours_since_last_service` value (PD-B4) requires a latest-reading-per-equipment subquery against `cons__fct_sensor_reading` — write the QUALIFY ROW_NUMBER pattern explicitly in the sketch.
