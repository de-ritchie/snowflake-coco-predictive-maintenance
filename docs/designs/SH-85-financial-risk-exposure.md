# Design: SH-85 — Financial Risk Exposure: surface $-at-risk in Plant Manager view and semantic layer

Status: **Design frozen** (brainstorm confirmed by user)
Branch: *(not yet created — `Jira-Triage-agent`'s job)*
Epic: EPIC-PERSONAS | Story: SH-85
Traces to: [docs/01-BRD.md](../01-BRD.md) §8 (Impact Statement metrics), [docs/02-FRD.md](../02-FRD.md) FR-CC-07, [docs/04-8-LLD.md](../04-8-LLD.md) §1 (Impact Statement metric cards), [docs/designs/SH-84-dollar-exposure-derivation.md](SH-84-dollar-exposure-derivation.md) (upstream — built the dollar-exposure models this story wires in), [docs/designs/SH-54-55-56-59-60-52-persona-suite.md](SH-54-55-56-59-60-52-persona-suite.md) (persona agents)

**Follows**: SH-84 (merged — `cons__fct_dollar_exposure`, `cons__fct_historical_dollar_impact`, `cons__fct_forward_dollar_at_risk` — zero consumers today, confirmed via repo-wide grep). SH-74 (merged — Plant Overview page, `oee_command_center_app/pages/1_Plant_Overview.py`). SH-48 (merged — Forecast OEE / priority score). SH-60 (merged — persona agent suite in `07_post_setup.sql`).

---

## 1. Scope

Wire `cons__fct_dollar_exposure` (and its upstream `cons__fct_historical_dollar_impact`) into three surfaces that currently have zero financial-exposure visibility:

1. **Streamlit Plant Overview page** — two additions:
   - A new "Historical Revenue Loss" card row (Section A2, after KPI cards, before Asset Risk Summary) showing per-line and plant-wide historical realized $ impact.
   - A new "$ at Risk" column in each per-machine Asset Risk Summary card (forward-looking `forward_dollar_at_risk_usd`), inserted between the machine-name column and the RUL column.

2. **Semantic view** — add `cons__fct_dollar_exposure` and `cons__fct_historical_dollar_impact` as new tables with relationships, facts, dimensions, and metrics, so all 3 persona agents can answer financial-exposure questions via Cortex Analyst.

3. **Agent instructions** — update `plant_manager_agent`'s instruction text to mention financial/dollar exposure as a capability.

**Also in scope**: rework `cons__fct_historical_dollar_impact` from equipment-level grain to **event grain** (1 row per breakdown event, preserving `event_start_ts`), so the semantic layer can support month-on-month and week-on-week trending queries. The combiner (`cons__fct_dollar_exposure`) pre-aggregates this back to equipment-level, so its output schema is unchanged.

**Not in scope**: weekly/monthly granularity toggle for the Streamlit historical cards (current story surfaces lifetime totals only, matching the KPI cards' static-period pattern). Survival-function-based forward projection (deferred per SH-84 §2.3). Inventory-offset demand cap (deferred per SH-84 §2.2).

---

## 2. Key design decisions (confirmed during brainstorm)

### 2.1 Forward $ at Risk card — forward-only, no historical on the per-machine row

The per-machine Asset Risk Summary card shows only `forward_dollar_at_risk_usd` (the forward-looking number). The historical realized impact is shown separately in the new Section A2 card row (plant-wide and per-line totals). Rationale: the forward number is the actionable signal for a Plant Manager ("this is what we stand to lose if we don't act"); the historical number is contextual and better served as a plant-level summary.

### 2.2 Dollar formatting — compact notation ($12.5k)

All dollar values use compact notation: `$12.5k`, `$1.2M`, etc. For the dataset's typical range (low thousands to tens of thousands per machine), this keeps the cards visually clean. Helper function formats to 1 decimal place with k/M suffix.

### 2.3 Historical model grain — event level (not weekly/monthly)

`cons__fct_historical_dollar_impact` is reworked from equipment-level (1 row per machine, lifetime SUM) to **event grain** (1 row per breakdown event per machine). This preserves `event_start_ts` so the semantic layer can aggregate by any time period (week, month, quarter) at query time, rather than pre-deciding a granularity.

The combiner (`cons__fct_dollar_exposure`) pre-aggregates the historical side back to equipment-level before joining with the forward model, keeping its own output schema unchanged — no downstream breakage.

### 2.4 Historical Revenue Loss section — respects line filter

When "All Lines" is selected: 3 cards — Overall (plant-wide), Caliper line, Engine Head line.
When a specific line is selected: 2 cards — Overall (still plant-wide, for context) + the selected line.

### 2.5 Semantic view — two separate tables, never summed

Historical and forward dollar values are kept as distinct concepts in the semantic view. They are never summed into a single "total exposure" metric. Each table gets its own SUM metric: `total_forward_dollar_at_risk` on `dollar_exposure`, `total_historical_impact` on `historical_dollar_impact`.

### 2.6 Agent instruction updates — plant_manager_agent only

Only `plant_manager_agent`'s instructions are updated to mention financial/dollar exposure. The other two agents (maintenance_supervisor, production_planner) discover it implicitly through the semantic view's facts if asked.

---

## 3. dbt model changes

### 3.1 `cons__fct_historical_dollar_impact` — grain change (view, event level)

**File**: `predictive_maintenance_dbt/models/consumption/cons__fct_historical_dollar_impact.sql`

**Before** (SH-84): grain = 1 row per `equipment_id`. Final SELECT is `GROUP BY equipment_id` with `COUNT(*)`, `SUM(duration_hours)`, `SUM(event_realized_impact_usd)`.

**After**: grain = 1 row per `(equipment_id, event_start_ts)`. Remove the final GROUP BY — surface the per-event rows directly from the `breakdown_impact` CTE.

**Output columns** (event grain):
| Column | Type | Description |
|---|---|---|
| `equipment_id` | VARCHAR | FK to `cons__dim_equipment` |
| `event_start_ts` | TIMESTAMP_NTZ | Breakdown event start timestamp |
| `duration_hours` | FLOAT | Duration of this breakdown event |
| `throughput_units_per_hour` | FLOAT | Machine's throughput rate (from equipment dim) |
| `order_units_that_week` | INT | Demand in the event's calendar week (demand cap input) |
| `unit_margin` | FLOAT | Product unit margin (from product seed) |
| `event_realized_impact_usd` | FLOAT | `LEAST(duration_hours × throughput, order_units) × unit_margin` |

**SQL sketch** (delta from SH-84 §3.1):

```sql
{{ config(materialized='view', schema='cons', tags=['consumption']) }}

WITH breakdown_impact AS (
    SELECT
        me.equipment_id,
        me.event_start_ts,
        me.duration_hours,
        eq.throughput_units_per_hour,
        p.unit_margin,
        COALESCE(o.order_units, 0) AS order_units_that_week,
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
    event_start_ts,
    duration_hours,
    throughput_units_per_hour,
    order_units_that_week,
    unit_margin,
    event_realized_impact_usd
FROM breakdown_impact
```

### 3.2 `cons__fct_dollar_exposure` — updated combiner (pre-aggregate historical)

**File**: `predictive_maintenance_dbt/models/consumption/cons__fct_dollar_exposure.sql`

The combiner's output schema is **unchanged** — same columns, same grain (1 row per `equipment_id`). The only change is how it reads the historical side: a pre-aggregation CTE replaces the direct table reference, since the upstream view is now event-grained.

**SQL sketch** (delta from SH-84 §3.3):

```sql
{{ config(
    materialized='dynamic_table',
    target_lag=var('target_lag'),
    schema='cons',
    snowflake_warehouse='snowcomotive_wh',
    refresh_mode='auto',
    initialize='on_create',
    tags=['inference']
) }}

WITH hist_totals AS (
    SELECT
        equipment_id,
        COUNT(*) AS breakdown_count,
        SUM(duration_hours) AS total_breakdown_hours,
        SUM(event_realized_impact_usd) AS historical_realized_impact_usd
    FROM {{ ref('cons__fct_historical_dollar_impact') }}
    GROUP BY equipment_id
)

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
FULL OUTER JOIN hist_totals h
    ON h.equipment_id = f.equipment_id
```

### 3.3 Schema.yml updates

Update `predictive_maintenance_dbt/models/consumption/schema.yml`:
- `cons__fct_historical_dollar_impact` — update column descriptions to reflect event grain; add `event_start_ts`, `event_realized_impact_usd` columns; remove the old aggregate columns (`breakdown_count`, `total_breakdown_hours`, `historical_realized_impact_usd`).

---

## 4. Streamlit changes

**File**: `oee_command_center_app/pages/1_Plant_Overview.py`

### 4.1 New data loader: `load_historical_revenue_loss(line_filter)`

Queries `cons__fct_dollar_exposure` joined to `cons__dim_equipment` for `line_name`, returns per-line totals plus overall:

```python
def load_historical_revenue_loss(line_filter: str) -> pd.DataFrame:
    conn = get_connection()
    line_clause = "" if line_filter == "All Lines" else f"AND eq.line_name = '{line_filter}'"
    return conn.query(
        f"""
        SELECT
            eq.line_name,
            SUM(de.historical_realized_impact_usd) AS lost_revenue
        FROM cons.cons__fct_dollar_exposure de
        JOIN cons.cons__dim_equipment eq
            ON eq.equipment_id = de.equipment_id
        WHERE 1=1
          {line_clause}
        GROUP BY eq.line_name
        """,
        ttl=0,
    )
```

### 4.2 Modified data loader: `load_asset_risk(line_filter)`

Extend the existing query to JOIN `cons__fct_dollar_exposure` on `equipment_id`:

```sql
-- Add to the SELECT list:
de.forward_dollar_at_risk_usd

-- Add to the FROM clause:
LEFT JOIN cons.cons__fct_dollar_exposure de
    ON de.equipment_id = ps.equipment_id
```

### 4.3 Dollar formatting helper

```python
def fmt_dollar(val: float) -> str:
    if abs(val) >= 1_000_000:
        return f"${val / 1_000_000:.1f}M"
    if abs(val) >= 1_000:
        return f"${val / 1_000:.1f}k"
    return f"${val:,.0f}"
```

### 4.4 Section A2: Historical Revenue Loss (new, after KPI cards)

**Placement**: immediately after Section A (KPI cards), before Section B (Asset Risk Summary).

**Rendering**:

```python
st.subheader("Historical Revenue Loss")

loss_df = load_historical_revenue_loss(line_filter)
if not loss_df.empty:
    overall = loss_df["LOST_REVENUE"].sum()
    lines = loss_df.set_index("LINE_NAME")["LOST_REVENUE"].to_dict()

    if line_filter == "All Lines":
        cols = st.columns(3)
        with cols[0]:
            st.metric("Overall", fmt_dollar(overall))
        for i, (name, val) in enumerate(lines.items()):
            with cols[i + 1]:
                st.metric(f"{name} Line", fmt_dollar(val))
    else:
        cols = st.columns(2)
        with cols[0]:
            st.metric("Overall (plant-wide)", fmt_dollar(overall))
        with cols[1]:
            st.metric(f"{line_filter} Line", fmt_dollar(lines.get(line_filter, 0)))
```

Note: when a specific line is filtered, "Overall" still shows the **plant-wide** total (sum across ALL lines, not just the filtered one). This gives context — the user can see what fraction of total historical loss belongs to the line they're looking at.

**Wait — correction**: the `load_historical_revenue_loss` query above applies the `line_clause` filter, so when a specific line is selected, it only returns that line's data. To show plant-wide total even when filtered, the query needs to either: (a) always return all lines and filter in Python, or (b) use a GROUPING SETS / ROLLUP. **Developer-agent should use approach (a)**: always query all lines (no `line_clause` in this particular loader), filter in Python for display. This keeps the query simple.

Revised loader:
```python
def load_historical_revenue_loss() -> pd.DataFrame:
    """Always returns all lines — Streamlit filters in Python for display."""
    conn = get_connection()
    return conn.query(
        """
        SELECT
            eq.line_name,
            SUM(de.historical_realized_impact_usd) AS lost_revenue
        FROM cons.cons__fct_dollar_exposure de
        JOIN cons.cons__dim_equipment eq
            ON eq.equipment_id = de.equipment_id
        GROUP BY eq.line_name
        """,
        ttl=0,
    )
```

### 4.5 Section B modification: Asset Risk Summary — new "$ at Risk" column

**Current column layout**: `[3, 2, 2, 2, 2]` — machine+line, RUL, service, priority, badge.

**New column layout**: `[3, 2, 2, 2, 2, 2]` — machine+line, **$ at risk**, RUL, service, priority, badge.

New column rendering (inserted between c1 and the existing c2):

```python
with c_dollar:
    st.metric("$ at Risk", fmt_dollar(row["FORWARD_DOLLAR_AT_RISK_USD"]))
```

---

## 5. Semantic view changes

**File**: `scripts/07_post_setup.sql`

### 5.1 New TABLES entries

```sql
dollar_exposure AS snowcomotive.cons.cons__fct_dollar_exposure
  PRIMARY KEY (equipment_id)
  WITH SYNONYMS ('financial exposure', 'dollar at risk', 'revenue at risk')
  COMMENT = 'Per-equipment combined dollar exposure: forward $ at risk + historical realized impact (collapsed totals)',

historical_dollar_impact AS snowcomotive.cons.cons__fct_historical_dollar_impact
  PRIMARY KEY (equipment_id, event_start_ts)
  WITH SYNONYMS ('historical loss', 'revenue loss', 'breakdown cost')
  COMMENT = 'Per-breakdown-event realized dollar impact (demand-capped lost units × unit margin)'
```

### 5.2 New RELATIONSHIPS entries

```sql
dollar_exposure_to_machine AS dollar_exposure (equipment_id) REFERENCES machine (equipment_id),
historical_dollar_impact_to_machine AS historical_dollar_impact (equipment_id) REFERENCES machine (equipment_id)
```

### 5.3 New FACTS entries

```sql
dollar_exposure.forward_dollar_at_risk_usd AS forward_dollar_at_risk_usd,
dollar_exposure.historical_realized_impact_usd AS historical_realized_impact_usd,
historical_dollar_impact.event_realized_impact_usd AS event_realized_impact_usd,
historical_dollar_impact.duration_hours AS breakdown_duration_hours
```

### 5.4 New DIMENSIONS entries

```sql
historical_dollar_impact.event_start_ts AS event_start_ts
```

### 5.5 New METRICS entries

```sql
dollar_exposure.total_forward_dollar_at_risk AS SUM(dollar_exposure.forward_dollar_at_risk_usd),
historical_dollar_impact.total_historical_impact AS SUM(historical_dollar_impact.event_realized_impact_usd)
```

### 5.6 Verified query (optional — recommended)

Add a verified query to help Cortex Analyst handle the most common financial question:

```sql
financial_exposure_by_machine AS (
  QUESTION 'What is the forward dollar at risk and historical realized impact for each machine?'
  SQL 'SELECT
    eq.equipment_id,
    eq.equipment_name,
    eq.line_name,
    de.forward_dollar_at_risk_usd,
    de.historical_realized_impact_usd
FROM snowcomotive.cons.cons__fct_dollar_exposure de
JOIN snowcomotive.cons.cons__dim_equipment eq
    ON eq.equipment_id = de.equipment_id
ORDER BY de.forward_dollar_at_risk_usd DESC'
)
```

---

## 6. Agent instruction changes

**File**: `scripts/07_post_setup.sql` — `plant_manager_agent` only.

### 6.1 Response instruction update

Add "financial exposure" and "dollar at risk" to the list of topics the agent covers:

**Before** (excerpt):
> ...machine health, anomalies, maintenance events, OEE, orders, inventory, priority score, and predicted remaining-useful-life (RUL)...

**After**:
> ...machine health, anomalies, maintenance events, OEE, orders, inventory, priority score, predicted remaining-useful-life (RUL), and financial exposure (forward dollar-at-risk and historical realized revenue loss)...

### 6.2 Orchestration instruction update

Add guidance for financial questions:

> For questions about financial exposure, dollar risk, or revenue impact, use the Analyst tool — it can answer both per-machine and plant-wide queries, including historical loss trending by month or week.

---

## 7. Invariants for Reviewer-agent

1. **`cons__fct_historical_dollar_impact` must be event-grained** — 1 row per (equipment_id, event_start_ts), NOT aggregated to equipment_id. Verify the view has no GROUP BY equipment_id in its final SELECT.
2. **`cons__fct_dollar_exposure` output schema must be unchanged from SH-84** — same column names, same types, same grain (1 row per equipment_id). The hist_totals CTE is the only structural change; verify column names in the final SELECT match the SH-84 original exactly.
3. **Demand cap preserved** — the `LEAST(duration_hours × throughput, order_units) × unit_margin` formula must still be present in the historical view's CTE. Do not remove or modify the demand cap (BRD §8 line 149 invariant, same as SH-84 §7 invariant 2).
4. **`is_sensor_enabled` filter preserved** — the historical view must still filter to sensor-enabled equipment only (SH-84 §7 invariant 4).
5. **`event_type = 'BREAKDOWN'` filter preserved** — PM events do not contribute to dollar impact (SH-84 §7 invariant 3).
6. **Streamlit forward $ at risk column placement** — must be between machine-name (c1) and RUL (c2) in the Asset Risk Summary row. Verify column order in the `st.columns()` call.
7. **Historical Revenue Loss section placement** — must be after Section A (KPI cards) and before Section B (Asset Risk Summary).
8. **Historical Revenue Loss "Overall" card** — when a specific line is selected, the Overall card must still show plant-wide total (all lines), NOT the filtered line's total. Verify the loader does not apply the line filter.
9. **Semantic view syntax** — new TABLES/RELATIONSHIPS/FACTS/DIMENSIONS/METRICS entries must follow the exact clause syntax established by the existing entries (see `scripts/07_post_setup.sql` deviation notes at top of file). In particular: `<table>.<logical_name> AS <sql_expr>` for FACTS/DIMENSIONS, not the reverse.
10. **Agent instruction changes limited to `plant_manager_agent` only** — verify `maintenance_supervisor_agent` and `production_planner_agent` instructions are untouched.
11. **No new `target_lag` values introduced** — `cons__fct_dollar_exposure` keeps `var('target_lag')` (it's the leaf). `cons__fct_historical_dollar_impact` is a view (no target_lag). `cons__fct_forward_dollar_at_risk` keeps `'DOWNSTREAM'`. No changes to any of these.

---

## 8. File checklist

**Modified**:
- `predictive_maintenance_dbt/models/consumption/cons__fct_historical_dollar_impact.sql` — grain change: equipment → event (§3.1)
- `predictive_maintenance_dbt/models/consumption/cons__fct_dollar_exposure.sql` — pre-aggregate CTE for historical side (§3.2)
- `predictive_maintenance_dbt/models/consumption/schema.yml` — update column descriptions for historical model (§3.3)
- `oee_command_center_app/pages/1_Plant_Overview.py` — new section A2, modified section B, new helpers (§4)
- `scripts/07_post_setup.sql` — semantic view additions + plant_manager_agent instruction update (§5, §6)

**Reads only, no changes**: `cons__fct_forward_dollar_at_risk.sql`, `cons__fct_priority_score.sql`, `cons__dim_equipment`, `cons__dim_product`, `cons__fct_maintenance_event`, `cons__fct_order`.

---

## 9. Explicitly deferred

- **Monthly/weekly toggle for historical revenue loss Streamlit cards** — current story surfaces lifetime totals. A time-dimension toggle would require a new Streamlit section with a time-series chart reading from `cons__fct_historical_dollar_impact` at event grain, aggregated to the selected period. The data model built here supports this; the UI wiring is a separate follow-up.
- **Survival-function-based forward projection** — deferred per SH-84 §2.3.
- **Inventory-offset demand cap** — deferred per SH-84 §2.2.
- **`cons__fct_forward_dollar_at_risk` in the semantic view** — the combiner (`dollar_exposure`) already surfaces `forward_dollar_at_risk_usd`. Exposing the upstream forward model separately would add its intermediate columns (`assumed_downtime_hours`, `forward_demand_units`, etc.) for agent introspection. Not needed for V1; consider if agents need to explain *why* a machine is at risk.

---

## 10. Open items

None blocking. Every design question raised during the brainstorm (card content, formatting, section placement, line filter behavior, historical model grain, semantic view structure, agent instruction scope) was resolved above. `Developer-agent` should:

1. Verify that `cons__fct_dollar_exposure`'s dynamic-table refresh still works correctly after the combiner's SQL changes (the pre-aggregate CTE over a view should be transparent to Snowflake's dynamic-table engine — same as the current direct view reference — but verify at build time).
2. Run `dbt test` and confirm no regressions on existing models, especially `cons__fct_dollar_exposure`'s own tests (if any — check schema.yml).
3. After applying `07_post_setup.sql`, test the semantic view with a manual Cortex Analyst query to verify the new tables/facts/metrics resolve correctly.
4. Verify the Streamlit page renders correctly with the new sections — check both "All Lines" and a specific-line filter state.
