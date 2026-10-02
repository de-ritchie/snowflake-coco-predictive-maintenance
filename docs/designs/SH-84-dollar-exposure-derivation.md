# Design: SH-84 — Derive $-at-risk / dollar-exposure computation (historical realized + forward-looking)

Status: **Design frozen** (brainstorm confirmed by user)
Branch: *(not yet created — `Jira-Triage-agent`'s job)*
Epic: EPIC-IMPACT | Story: SH-84 (S-IMPACT-2: "$-at-risk derivation")
Traces to: [docs/01-BRD.md](../01-BRD.md) §8 (Impact Statement metrics), [docs/02-FRD.md](../02-FRD.md) FR-CC-07, [docs/04-1-LLD.md](../04-1-LLD.md) (table DDL), [docs/04-8-LLD.md](../04-8-LLD.md) §1 (Impact Statement metric cards), [docs/designs/SH-70-data-mapping.md](SH-70-data-mapping.md) §4 (synthetic gaps: `revenue_per_unit`), [docs/designs/SH-48-forecast-oee-priority-queue-pages.md](SH-48-forecast-oee-priority-queue-pages.md) (demand-forecast/survivability mechanics reused), [docs/designs/SH-47-priority-score.md](SH-47-priority-score.md) (priority score DAG/leaf-table architecture), [docs/designs/SH-72-parameterize-target-lag.md](SH-72-parameterize-target-lag.md) (single-leaf chain convention)

**Follows**: SH-47 (merged, `cons.fct_priority_score` — leaf of the 6-table dynamic chain), SH-48 (merged, Forecast OEE survivability badge, `required_run_hours_next_4wk`), SH-46 (merged, RUL model evaluation — concordance index 0.94, median AE ~182h), SH-69 (merged, `cons.fct_order`/`fct_oee`/`fct_maintenance_event`), SH-72 (merged, single-leaf `target_lag` parameterization). Replaces the mockup's hardcoded `at_risk_revenue_protected_usd: 184000` (`mockup/data.js:116`).

---

## 1. Scope

Replace the mockup's hardcoded $184k `at_risk_revenue_protected_usd` with a pipeline-computed dollar-exposure derivation, producing two distinct numbers:

1. **Historical realized $ impact** — actual past $ lost to unplanned downtime, computed from real CMMS breakdown events × demand-capped throughput × per-product unit margin.
2. **Forward-looking $ at risk** — projected $ exposure if currently at-risk machines (predicted RUL < 4 weeks) fail before meeting demand, using the same demand-capped formula against future orders.

Both numbers are demand-capped per BRD §8 line 149's Theory-of-Constraints refinement: `Lost_Units = MIN(downtime_hours × throughput_rate, order_units_in_window)`, where the demand cap uses raw order volume (no inventory offset — design decision, §2.2).

**End consumers**: `presentation/03-impact-statement.md` (hackathon submission's required Impact Statement section) and a future Streamlit page (user-confirmed intent to bring this data point to Streamlit; the Streamlit page itself is out of scope for this story, but the data model is designed to support it).

**Not in scope**: The Streamlit page wiring itself (separate future story). Survival-function-based failure probability (§2.3 decision). Inventory-offset demand cap (§2.2 decision). Any change to `cons__fct_priority_score`'s SQL (only its config changes, §4.2).

---

## 2. Key design decisions (confirmed during brainstorm)

### 2.1 Unit margin placement — product-level seed CSV (tier 2)

`unit_margin` is added as a new column on `predictive_maintenance_dbt/seeds/cons__dim_product.csv`. This is the standard tier-2 approach used in MES/ERP-integrated predictive-maintenance systems (product-level contribution margin from the product costing module, joined at analysis time). In a production system, this column would come from an ERP feed; for this synthetic dataset, seeding it is the correct analog.

Values (order-of-magnitude realistic for machined automotive components, per SH-70 §5.1's suggested ranges):

| product_id | product_name | variant | unit_margin |
|---|---|---|---|
| BRAKE_CALIPER | Brake Caliper | EV | 55.00 |
| BRAKE_CALIPER | Brake Caliper | ICE | 50.00 |
| ENGINE_HEAD | Engine Head | EV | 150.00 |
| ENGINE_HEAD | Engine Head | ICE | 140.00 |

EV variants carry a small premium over ICE (reflects real-world EV-component pricing). These are contribution margins (revenue minus variable cost), not selling prices — the distinction matters for the Impact Statement's "$-at-risk" framing (it's margin at risk, not revenue at risk, since variable costs aren't incurred on units never produced).

### 2.2 Demand cap — raw demand only (no inventory offset)

`unfulfilled_demand_in_window = order_units` for the weeks overlapping the breakdown/risk window. The full Theory-of-Constraints formula would subtract `fg_units_on_hand` (inventory that could have absorbed orders during downtime), but the user confirmed the simpler version: no inventory offset. This slightly overstates impact (doesn't credit inventory buffers) but avoids the inventory-join complexity and the question of which inventory snapshot timestamp to use for a given breakdown window.

The formula for both historical and forward-looking:

```
lost_units = MIN(downtime_hours × throughput_units_per_hour, order_units_in_window)
dollar_impact = lost_units × unit_margin
```

### 2.3 Forward-looking — binary threshold, not survival curve

The forward-looking $ at risk uses `predicted_rul_hours` as a **binary** risk indicator against a 4-week (672-hour) horizon: if `predicted_rul_hours < 672`, the machine is "at risk" (probability = 1); otherwise probability = 0. This is consistent with SH-48's existing "survives its own demand?" survivability badge (§3.4) and `required_run_hours_next_4wk`.

The alternative — using `rul_aft_model!predict_survival_function()` to get an actual S(t) failure probability and scaling the $ by it — was considered and rejected: (a) `predict_survival_function()` hasn't been verified as working on this account (same discipline as SH-46 §4's scikit-survival check), (b) calling it from a dynamic-table SQL model would require `MODEL()!predict_survival_function()` syntax whose support is unproven, (c) the binary approach is simpler, consistent with what's already built, and still demand-capped. A survival-function refinement is a stated V2 enhancement if the binary approach proves too coarse.

### 2.4 Risk horizon — 4 weeks (672 hours)

Matches `cons__fct_priority_score.required_run_hours_next_4wk` and the Forecast OEE page's 8-week lookahead convention. A machine whose `predicted_rul_hours < 672` is "at risk" for the forward-looking computation.

---

## 3. Architecture — 3-table decomposition with new leaf

### 3.0 Design rationale

The computation decomposes into three models for modularity and refresh efficiency:

1. **`cons__fct_historical_dollar_impact`** (view) — historical realized $ per equipment. Depends only on static tables; never changes between dynamic-table refresh cycles.
2. **`cons__fct_forward_dollar_at_risk`** (dynamic table, DOWNSTREAM) — forward-looking $ per equipment. Depends on the live dynamic chain via `cons__fct_priority_score`.
3. **`cons__fct_dollar_exposure`** (dynamic table, **leaf**) — thin combiner of the above two. Becomes the new single leaf of the entire dynamic-table chain, replacing `cons__fct_priority_score` as the leaf.

**DAG:**

```
static tables → fct_historical_dollar_impact (view)  ──────────────────────┐
                                                                            → fct_dollar_exposure (leaf, target_lag=var)
... → rul_prediction (DOWNSTREAM) → priority_score (DOWNSTREAM) → fct_forward_dollar_at_risk (DOWNSTREAM) ─┘
```

**Why `cons__fct_priority_score` becomes DOWNSTREAM instead of staying the leaf**: `cons__fct_dollar_exposure` needs to read `cons__fct_priority_score` (for `predicted_rul_hours`, `required_run_hours_next_4wk`). If dollar_exposure were placed *between* rul_prediction and priority_score, it would need to duplicate the "latest per equipment" QUALIFY logic. By placing it *downstream* of priority_score, it reads the already-computed columns without duplication. The refresh trigger now propagates from dollar_exposure (the new leaf) backward through priority_score → rul_prediction → ... — the effective refresh cadence is identical to today; only the trigger point shifts.

**What external consumers see: nothing changes.** The Streamlit pages, semantic view, and agents all query `cons__fct_priority_score` by name — it still exists, still has the same columns, still refreshes on the same cadence. The data content is unchanged.

### 3.1 `cons__fct_historical_dollar_impact` (view)

**Materialization**: `view` (not a table or dynamic table). Depends only on static tables (`cons__fct_maintenance_event`, `cons__dim_equipment`, `cons__dim_product`, `cons__fct_order`), all of which are plain tables rebuilt on `dbt run`. A view avoids an unnecessary materialization step and is always current relative to its inputs. The dynamic table leaf re-executes this view on each refresh, but the underlying data volume is trivially small (a few dozen CMMS events).

**Schema**: `cons`

**Tags**: `['consumption']`

**Grain**: 1 row per `equipment_id` (fleet of 3 sensor-enabled machines = 3 output rows).

**SQL sketch**:

```sql
{{ config(materialized='view', schema='cons', tags=['consumption']) }}

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
```

**Join logic for demand cap**: `cons__fct_order` is joined on `(product_id, variant, order_week = DATE_TRUNC('week', event_start_ts))` — matching the breakdown event to the order volume in the same calendar week. If no orders exist for that week/product/variant (LEFT JOIN produces NULL), `COALESCE(o.order_units, 0)` treats it as zero demand → zero realized impact for that event (a breakdown during a week with no demand costs nothing in lost output). `LEAST(...)` is the demand cap: the smaller of "units the machine could have produced during downtime" and "units actually ordered that week."

**`is_sensor_enabled` filter**: only the 3 sensor-enabled machines participate in the dollar computation — non-sensor equipment never has CMMS events in this dataset (confirmed from the generator: only sensor-enabled machines accrue wear/breakdowns), but the filter makes this explicit.

### 3.2 `cons__fct_forward_dollar_at_risk` (dynamic table, DOWNSTREAM)

**Materialization**: `dynamic_table`, `target_lag='DOWNSTREAM'`, `refresh_mode='auto'`, `initialize='on_create'`

**Schema**: `cons`

**Tags**: `['inference']`

**Grain**: 1 row per `equipment_id` (3 output rows).

**Refs**: `cons__fct_priority_score` (dynamic, for `predicted_rul_hours`, `required_run_hours_next_4wk`, `equipment_id`, `score_ts`), `cons__dim_equipment` (static, for `throughput_units_per_hour`, `product_id`, `variant`), `cons__dim_product` (seed, for `unit_margin`), `cons__fct_order` (static, for forward demand), `cons__fct_maintenance_event` (static, for assumed downtime scalar).

**SQL sketch**:

```sql
{{ config(
    materialized='dynamic_table',
    target_lag='DOWNSTREAM',
    schema='cons',
    snowflake_warehouse='snowcomotive_wh',
    refresh_mode='auto',
    initialize='on_create',
    tags=['inference']
) }}

-- Forward-looking dollar-at-risk per equipment. Uses the binary threshold
-- approach (§2.3): if predicted_rul_hours < 672 (4 weeks), the machine is
-- "at risk" with probability 1 and the forward $ = demand-capped lost units
-- × unit_margin. Otherwise 0.
--
-- assumed_downtime_hours is the fleet-wide median of historical BREAKDOWN
-- duration — the same data-driven scalar SH-48's Forecast OEE page uses
-- (§4.1 of that design doc), computed here as a scalar CTE rather than a
-- separate query function.
--
-- forward_demand_units is the sum of cons__fct_order.order_units for weeks
-- falling within the next 4 weeks from the machine's latest scoring tick
-- (score_ts), for that machine's product/variant. This is the demand the
-- machine would need to satisfy if it survives.

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
```

**`assumed_downtime_hours`**: fleet-wide `MEDIAN(duration_hours)` over all BREAKDOWN events — same robust-to-outlier rationale as SH-48 §4.1's identical scalar. Used as the assumed downtime-per-failure for the forward projection (we don't know how long a future breakdown will last; the fleet median is the best available estimate). Surfaced as an output column for transparency.

**`forward_demand_units`**: sum of `cons__fct_order.order_units` for the 4 weeks starting from the machine's latest `score_ts`. This is the total demand volume the machine would need to satisfy over the risk horizon. The demand cap applies per §2.2: `LEAST(assumed_downtime × throughput, forward_demand)` ensures the $ at risk never exceeds what customers actually need.

**`is_at_risk`**: boolean column — `TRUE` if `predicted_rul_hours < 672` (§2.4). Surfaced for downstream consumers (Streamlit badge, presentation doc).

**`refresh_mode='auto'`**: the MEDIAN aggregate over `fct_maintenance_event` doesn't support change tracking; Snowflake falls back to FULL refresh. At 3 output rows, this is trivially cheap — same rationale as `cons__fct_priority_score`'s own `refresh_mode='auto'` (§ header comment in that model's SQL).

### 3.3 `cons__fct_dollar_exposure` (dynamic table, leaf)

**Materialization**: `dynamic_table`, `target_lag=var('target_lag')`, `refresh_mode='auto'`, `initialize='on_create'`

**Schema**: `cons`

**Tags**: `['inference']`

**Grain**: 1 row per `equipment_id` (3 output rows).

**Refs**: `cons__fct_historical_dollar_impact` (view), `cons__fct_forward_dollar_at_risk` (dynamic, DOWNSTREAM).

**SQL sketch**:

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

-- Dollar-exposure combiner — the new single leaf of the dynamic-table chain
-- (replaces cons__fct_priority_score as the leaf, per SH-84 §3.0).
-- Combines historical realized impact (from the view, static) with
-- forward-looking $ at risk (from the dynamic DOWNSTREAM table, live).
--
-- This is intentionally a thin combiner — all computation lives in the two
-- upstream models. target_lag is parameterized via the same dbt var as the
-- former leaf (SH-72 convention), so the effective refresh cadence for the
-- entire chain is unchanged.

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
```

**FULL OUTER JOIN**: handles edge cases where a machine has historical breakdowns but no current RUL prediction (shouldn't happen in this dataset but is structurally correct), or vice versa.

---

## 4. Config changes to existing models

### 4.1 `cons__dim_product` seed — add `unit_margin` column

**File**: `predictive_maintenance_dbt/seeds/cons__dim_product.csv`

**Before**:
```csv
product_id,product_name,variant
BRAKE_CALIPER,Brake Caliper,EV
BRAKE_CALIPER,Brake Caliper,ICE
ENGINE_HEAD,Engine Head,EV
ENGINE_HEAD,Engine Head,ICE
```

**After**:
```csv
product_id,product_name,variant,unit_margin
BRAKE_CALIPER,Brake Caliper,EV,55.00
BRAKE_CALIPER,Brake Caliper,ICE,50.00
ENGINE_HEAD,Engine Head,EV,150.00
ENGINE_HEAD,Engine Head,ICE,140.00
```

**Schema.yml update**: add `unit_margin` column description and a `not_null` test to `predictive_maintenance_dbt/seeds/schema.yml`.

### 4.2 `cons__fct_priority_score` — config-only change (no SQL change)

**File**: `predictive_maintenance_dbt/models/consumption/cons__fct_priority_score.sql`

**Before** (line 3):
```jinja
    target_lag=var('target_lag'),
```

**After**:
```jinja
    target_lag='DOWNSTREAM',
```

This is the **only** change to this file. No SQL change, no new columns, no new JOINs. The model's header comment should be updated to note that it is no longer the leaf of the dynamic-table chain (SH-84 moved the leaf to `cons__fct_dollar_exposure`).

**Implication**: `cons__fct_priority_score` now refreshes when `cons__fct_dollar_exposure` (the new leaf) triggers a refresh, which happens on the same `var('target_lag')` cadence. The effective refresh behavior for all existing consumers (Streamlit pages, semantic view, agents) is unchanged — same data, same cadence, different trigger point.

---

## 5. Fleet-level aggregates for the Impact Statement

The presentation doc (`presentation/03-impact-statement.md`) needs fleet-level totals, not per-equipment rows. These are simple aggregations over `cons__fct_dollar_exposure`:

```sql
-- Fleet-level numbers for the Impact Statement
SELECT
    SUM(historical_realized_impact_usd) AS total_historical_impact_usd,
    SUM(forward_dollar_at_risk_usd) AS total_forward_at_risk_usd,
    SUM(historical_realized_impact_usd) + SUM(forward_dollar_at_risk_usd) AS total_dollar_exposure_usd,
    COUNT_IF(is_at_risk) AS machines_at_risk,
    COUNT(*) AS total_machines
FROM cons.cons__fct_dollar_exposure
```

This query is run once when authoring `presentation/03-impact-statement.md` and the result is quoted in the document. The document itself is a static markdown file — it records a specific snapshot of these numbers, not a live reference. FR-CC-07's "computed from actual pipeline/model output, never hardcoded" requirement is satisfied because the numbers are derived from the pipeline (queryable, reproducible, change when the data changes), even though the presentation doc captures a point-in-time value.

---

## 6. Honesty caveats (required in the presentation doc)

The same caveats that apply to the Forecast OEE page (SH-48 §4.6) apply here, plus one additional:

1. **RUL uncertainty**: the concordance index is 0.94 (strong ranking) but median AE is ~182 hours on only 5 uncensored test rows (SH-46 §14) — the binary 672-hour threshold is a blunt instrument applied to an inherently uncertain prediction. A machine classified as "not at risk" (predicted RUL > 672h) could still fail within 4 weeks if the point estimate is off by the median error.

2. **Assumed downtime**: the forward projection uses a fleet-wide median breakdown duration as the assumed downtime-per-failure. An actual future breakdown's duration may vary substantially from this figure.

3. **Demand cap is raw demand, not net-of-inventory**: the $ impact assumes every unit of demand during a downtime window was unfulfillable. In reality, finished-goods inventory may have absorbed some or all of the shortfall. This means the reported $ figures are an upper bound on actual financial exposure, not a precise loss figure.

4. **Small-sample caveat on historical realized impact**: the synthetic dataset has a finite number of BREAKDOWN events across 3 machines over ~3 years. The historical total is a real sum over real (synthetic) events, but it's not a statistically robust long-run average — it's the specific realization of this particular synthetic dataset.

---

## 7. Invariants for Reviewer-agent

1. **No SQL change to `cons__fct_priority_score.sql`** — only the `target_lag` config line changes from `var('target_lag')` to `'DOWNSTREAM'`. Verify no other line in the file was modified.
2. **Demand cap must use `LEAST(...)`, not uncapped multiplication** — every dollar-impact formula (historical and forward) must compute `LEAST(downtime_hours × throughput, order_units) × unit_margin`, never `downtime_hours × throughput × unit_margin` without the demand cap. This is the core BRD §8 line 149 refinement this story exists to implement.
3. **Historical view must filter to `event_type = 'BREAKDOWN'` only** — `'PM'` events do not contribute to dollar impact (same convention as `cons__fct_oee`'s own breakdown filter).
4. **Historical view must filter to `is_sensor_enabled` equipment only** — non-sensor equipment never generates CMMS events in this dataset, but the filter must be explicit.
5. **Forward-looking threshold must be 672 hours (4 weeks)** — not a different horizon, not a soft/graded threshold, per §2.4's explicit decision.
6. **Forward demand window must be 4 weeks from `score_ts`** — `o.order_week >= DATE_TRUNC('week', ps.score_ts) AND o.order_week < DATEADD('week', 4, DATE_TRUNC('week', ps.score_ts))`. Not from `CURRENT_DATE()` (would silently drift from the RUL model's own scoring timestamp), not from some other anchor.
7. **Assumed downtime must be `MEDIAN(duration_hours)` over BREAKDOWN events** — not `AVG`, not hardcoded, per §3.2's robustness-to-outlier rationale (same as SH-48 §4.1).
8. **`cons__fct_dollar_exposure` must be the new leaf** with `target_lag=var('target_lag')`. `cons__fct_forward_dollar_at_risk` must be `target_lag='DOWNSTREAM'`. Verify the refresh chain propagates correctly (leaf → forward → priority_score → rul_prediction → ...) by checking `SHOW DYNAMIC TABLES` output after a successful `dbt run`.
9. **`unit_margin` values in the seed must match §4.1's table exactly** — Brake Caliper EV $55.00, ICE $50.00; Engine Head EV $150.00, ICE $140.00.
10. **COALESCE(0) on the FULL OUTER JOIN in the leaf combiner** — verify no NULL leaks through to the output `*_usd` columns; both `historical_realized_impact_usd` and `forward_dollar_at_risk_usd` must be 0 (not NULL) for any equipment missing from one side of the join.

---

## 8. File checklist

**New**:
- `predictive_maintenance_dbt/models/consumption/cons__fct_historical_dollar_impact.sql` (§3.1 — view)
- `predictive_maintenance_dbt/models/consumption/cons__fct_forward_dollar_at_risk.sql` (§3.2 — dynamic table, DOWNSTREAM)
- `predictive_maintenance_dbt/models/consumption/cons__fct_dollar_exposure.sql` (§3.3 — dynamic table, leaf)

**Modified**:
- `predictive_maintenance_dbt/seeds/cons__dim_product.csv` — add `unit_margin` column (§4.1)
- `predictive_maintenance_dbt/seeds/schema.yml` — add `unit_margin` column description + `not_null` test (§4.1)
- `predictive_maintenance_dbt/models/consumption/cons__fct_priority_score.sql` — config-only: `target_lag=var('target_lag')` → `target_lag='DOWNSTREAM'` (§4.2)
- `predictive_maintenance_dbt/models/consumption/schema.yml` — add model descriptions and column tests for the 3 new models

**Reads only, no changes**: `cons__fct_maintenance_event`, `cons__fct_order`, `cons__dim_equipment`, `cons__fct_priority_score` (SQL — only config changes), `cons__fct_rul_prediction` (read by priority_score as today, not directly by any new model).

---

## 9. Explicitly deferred

- **Survival-function-based failure probability** (§2.3) — a V2 refinement that would scale the forward $ by `1 - S(672)` (probability of failing within 4 weeks from the AFT survival curve) instead of the binary threshold. Requires verifying `predict_survival_function()` API availability on this account first.
- **Inventory-offset demand cap** (§2.2) — a V2 refinement that would subtract `fg_units_on_hand` from the demand cap, reducing the $ impact when inventory buffers exist. Requires deciding which inventory snapshot timestamp to use for a given breakdown window.
- **Streamlit page wiring** — the user confirmed intent to surface these numbers in Streamlit. The data model (3 new dbt models, queryable from any SQL consumer) supports this without further schema changes; the Streamlit page itself is a separate future story.
- **Per-equipment-type assumed downtime** — currently fleet-wide median; per-machine-type median would be more precise but requires enough breakdown events per machine type to support a per-type median without a small-sample caveat worse than the fleet-wide one.
- **"$ protected" comparison baseline** — BRD §8 metric 3 mentions comparing "$-at-risk revenue protected" against a "naive dollar-value-only or failure-probability-only prioritization baseline." This story computes the raw $ exposure; the counterfactual comparison ("how much $ would have been lost under a different prioritization policy") is a separate analysis that requires defining the baseline policy and replaying history against it. Not in scope here.
- **Semantic view entity for dollar exposure** — analogous to SH-48's `PriorityScore` entity addition. Deferred until the Streamlit wiring story (the semantic view is consumed by agents via the Analyst tool; dollar exposure will likely need its own entity + verified queries at that point).

---

## 10. Open items

None blocking. Every design question raised during the brainstorm (unit-margin placement, demand-cap definition, survival-function vs. binary, DAG architecture, materialization strategy, risk horizon) was resolved to a concrete decision above. `Developer-agent` should:

1. Verify empirically that a dynamic table can ref a dbt view in the same schema (expected to work — Snowflake dynamic tables can ref views — but verify at build time per this project's standing discipline).
2. After the `dbt run`, verify the refresh chain via `SHOW DYNAMIC TABLES IN SCHEMA cons` and confirm `cons__fct_dollar_exposure` is the only table with an explicit `target_lag` (all others should show `DOWNSTREAM`).
3. Run `dbt test` and confirm no regressions on existing models (especially `cons__fct_priority_score`'s existing uniqueness/not-null tests, which should be unaffected by the config-only change).
