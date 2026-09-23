# Design: STD/CONS models for order, inventory, calendar, OEE, maintenance events

Status: **Frozen** — brainstormed against real generator source (no live Snowflake SQL access in this session; RAW schema/values verified from `generator/fulldata/*.py`, `scripts/02_setup_raw_ddl.sql`, `scripts/03_setup_raw_load.sql` instead — same ground truth, since that code is what produced/loads the actual rows). Frozen non-interactively per explicit instruction. Reconciled 2026-09-23 post-implementation/review — Reviewer-agent returned a clean PASS verdict, zero bugs/deviations found, implementation matched the frozen design exactly across all 8 checks performed; see §6 (live-verification results) for what was confirmed, and §3 Q1/Q3 for the two previously-flagged assumptions now settled.
**Story**: SH-69 (S-DATA-11)
**Branch**: `feature/SH-6-69-std-cons-order-inventory-oee-models`
**Traces to**: [docs/04-1-LLD.md](../04-1-LLD.md) (Module 1, primary spec), [docs/02-FRD.md](../02-FRD.md) FR-PL-02/FR-PL-07/FR-PL-08, [docs/01-BRD.md](../01-BRD.md) assumption 3, [docs/05-Epics.md](../05-Epics.md) §5 S-DATA-11

---

## 1. Scope

Build the STD/CONS layer for the 4 RAW domains that were loaded (EPIC-FULLDATA) but never got a dbt model:

- `STD.SALES_ORDER`, `STD.INVENTORY_FG_SNAPSHOT`, `STD.SPARE_PART_SNAPSHOT` (tables), `STD.CALENDAR` (view) — type/key conformance only.
- `CONS.DIM_PRODUCT`, `CONS.FCT_ORDER`, `CONS.FCT_INVENTORY_FG`, `CONS.FCT_INVENTORY_SPARE`, `CONS.FCT_OEE`, `CONS.FCT_MAINTENANCE_EVENT`.

**Gap found during this design, added to scope**: `STD.CMMS_LOG` does not exist yet either (only `std__equipment.sql`/`std__sensor_reading.sql` exist today). Module 1 specifies it (`+ duration_hours`) and both `CONS.FCT_OEE` (breakdown-hours numerator) and `CONS.FCT_MAINTENANCE_EVENT` (direct pass-through) depend on it. Building it is a prerequisite this story must cover, even though the Epics doc's T1 line didn't name it explicitly — it was scoped under Module 1's original RAW/STD table list, just missed like the others.

**Not in scope** (per Epics note): `CONS.FCT_PRIORITY_SCORE` / `PriorityScore` semantic entity — needs `CONS.FCT_RUL_PREDICTION` from EPIC-RUL, not built here.

---

## 2. RAW ground truth (verified from generator source + load scripts, not live SQL)

No live Snowflake SQL access in this session (no generic SQL-execution tool available; only Atlassian/Confluence tools and local-file tools were available). Verified instead directly against `generator/fulldata/*.py` (the code that actually produced the rows loaded by `manage.py up`) and `scripts/02_setup_raw_ddl.sql`/`03_setup_raw_load.sql` (the DDL/load statements) — equally authoritative for schema/value-shape purposes, and arguably more reliable than an ad hoc SELECT since it's the generating logic itself, not a sample.

| Table | Confirmed shape |
|---|---|
| `RAW.EQUIPMENT` | Has `line_name`. Values: `CNC_BORING`→`Caliper`, `CNC_MILLING`→`Caliper`, `CNC_HORIZONTAL`→`Engine Head` (sensor-enabled, `machine_config.py` `MACHINES`), plus 12 non-sensor stages sharing the same two `line_name` values (`NON_SENSOR_EQUIPMENT`). Two sensor-enabled machines share `Caliper`; one has `Engine Head` alone. |
| `RAW.CMMS_LOG` | `event_id, equipment_id, event_type, event_start_ts, event_end_ts, technician_notes` — separate start/end timestamps (not a single ts+duration column). `event_type` has exactly two values: `'PM'` and `'BREAKDOWN'` (`simulate.py`/`cmms.py`, no other values emitted). Only the 3 sensor-enabled machines ever get CMMS rows (non-sensor equipment never breaks down or gets PM'd in this generator). |
| `RAW.SALES_ORDER` | `order_week, product_id, variant, order_units`. Only 2 of the 4 conceptual product/variant combos are ever generated: `(BRAKE_CALIPER, EV)` and `(ENGINE_HEAD, ICE)` (`orders.py` `PRODUCTS` dict — secondary variants ICE Caliper / EV Engine Head are dropped entirely, not generated as zero-filler). `order_week` is a Monday-midnight `TIMESTAMP_NTZ` (`week_start()` combined with `time.min`). No `product_name` column anywhere in RAW — confirmed absent from every generator table. |
| `RAW.INVENTORY_FG_SNAPSHOT` | `snapshot_week, product_id, variant, fg_units_on_hand` — same 2 combos, same Monday-midnight week grain as orders. |
| `RAW.SPARE_PART_SNAPSHOT` | `snapshot_week, equipment_id, spare_part_name, units_on_hand, lead_time_days` — 1 row per `(equipment_id, spare_part_name)` per week, all 3 sensor-enabled machines × 4 part names (`SPARE_PART_LEAD_TIME_DAYS`: `BEARING`, `TOOL_INSERT`, `COOLANT_PUMP`, `SERVO_DRIVE`), same week grain. |
| `RAW.CALENDAR` | `calendar_date, is_working_day, is_holiday` — one row per calendar day, full 3yr history + 8wk look-ahead, `is_working_day` = Mon–Fri minus 10 fixed annual holidays (`sim_calendar.py`). Full-replace load (`TRUNCATE` + `FORCE=TRUE`, per `03_setup_raw_load.sql`), unlike the other append-only RAW tables. |

---

## 3. Resolution of the 6 open questions

### Q1 — `CONS.DIM_PRODUCT.product_name`: no source exists, seed it as static reference data

Confirmed: **no RAW table anywhere carries a `product_name`** — not `SALES_ORDER`, not `INVENTORY_FG_SNAPSHOT`, not `EQUIPMENT` (which has `product_id`/`variant` but no display name either). LLD Module 1 requires 4 rows total (2 products × 2 variants — `BRAKE_CALIPER`/`ENGINE_HEAD` × `EV`/`ICE`), but `RAW.SALES_ORDER` only ever contains 2 of those 4 combos (secondary variants were deliberately never generated, §2 above). So `DIM_PRODUCT` **cannot** be derived from `SALES_ORDER` (would only yield 2 rows, contradicting the LLD's explicit "4 rows total") and there's no other RAW source to derive `product_name` from.

**Decision**: `CONS.DIM_PRODUCT` is a dbt **seed** (`predictive_maintenance_dbt/seeds/cons__dim_product.csv`), matching this project's existing precedent for exactly this situation — `cons__dim_sensor_baseline.csv` is already static reference data seeded the same way, not derived from RAW. Content (all 4 combos, `product_name` a straightforward human-readable label — **confirmed final by the user**, "Brake Caliper"/"Engine Head", no longer a flagged assumption):

```
product_id,product_name,variant
BRAKE_CALIPER,Brake Caliper,EV
BRAKE_CALIPER,Brake Caliper,ICE
ENGINE_HEAD,Engine Head,EV
ENGINE_HEAD,Engine Head,ICE
```

`CONS.FCT_ORDER`/`FCT_INVENTORY_FG` will only ever populate rows against 2 of these 4 keys (matching what `STD.SALES_ORDER`/`STD.INVENTORY_FG_SNAPSHOT` actually contain) — the other 2 rows exist in the dimension for completeness/FK-target purposes only, with no matching facts. This is expected, not a data-quality issue.

### Q2 — `CONS.FCT_OEE` line grain: confirmed, `line_name` exists and is shared

`RAW.EQUIPMENT.line_name` exists and is already surfaced in `CONS.DIM_EQUIPMENT` (built, `cons__dim_equipment.sql`). Two sensor-enabled machines (`CNC_BORING`, `CNC_MILLING`) share `line_name = 'Caliper'`; one (`CNC_HORIZONTAL`) has `line_name = 'Engine Head'` alone. **Decision**: `CONS.FCT_OEE` aggregates across all sensor-enabled equipment sharing a `line_name` for that week — both downtime hours (numerator) and scheduled hours (denominator, `num_sensor_machines_on_line × working_days × 24`) sum across the line's sensor-enabled machines, so a Caliper-line week reflects both Boring's and Milling's downtime together. Non-sensor equipment on the same `line_name` (e.g. `CASTING_CALIPER`) is excluded from this calculation — they never emit CMMS rows in this generator, and including them in the denominator would artificially dilute `availability_pct` toward 1.0 without ever being able to move the numerator.

### Q3 — `availability_pct` formula: BREAKDOWN-only downtime against calendar-scheduled hours

This is the genuinely ambiguous one — flagged clearly, not just a lookup.

**Decision**: `availability_pct = 1 - (breakdown_hours / scheduled_hours)` where:
- `scheduled_hours` (per line, per week) = `(COUNT of RAW.CALENDAR.is_working_day=TRUE dates that week) × 24 × (COUNT of sensor-enabled equipment on that line)` — pure calendar capacity, not reduced by orders/idle (that's `machine_config`'s `operating_hours_week` concept, which belongs to the generator's internal simulation, not this OEE mart).
- `breakdown_hours` (per line, per week) = `SUM(STD.CMMS_LOG.duration_hours)` for that line's sensor-enabled equipment, **`event_type = 'BREAKDOWN'` only** — `'PM'` events are excluded from the loss calculation entirely.

**Why PM is excluded (the actual judgment call)**: classical OEE convention treats planned/scheduled maintenance as excluded from "Planned Production Time" itself (denominator), not as an Availability *loss* (numerator) — Availability is specifically meant to capture *unplanned* stops. This also matches this project's own Impact Statement metric #1 ("unplanned downtime reduction %", BRD §8) and the demo's central point (predictive maintenance → less unplanned downtime → better Availability). The alternative — counting PM duration as downtime too — was considered and rejected: it would perversely penalize the very PM events that predictive maintenance is supposed to encourage, undercutting the demo narrative. **Simplification accepted**: PM hours are excluded from the loss but *not* subtracted from `scheduled_hours` either (a stricter classical-OEE treatment would shrink the denominator by planned-downtime hours) — kept as pure calendar capacity for simplicity; this slightly understates `availability_pct`'s ceiling on a week that had a PM event, which is an acceptable MVP simplification given PM events are short (1–3h) relative to weekly capacity. Document this as-is; revisit only if judges/reviewers push back on OEE-textbook fidelity.

Week bucketing: `DATE_TRUNC('week', event_start_ts)` for CMMS events and `DATE_TRUNC('week', calendar_date)` for calendar rows — both align to Monday-start weeks matching `RAW.SALES_ORDER.order_week`'s own Monday-midnight convention (verified in `orders.py`/`simulate.py`), so `period_week` is consistent across `FCT_ORDER`/`FCT_INVENTORY_*`/`FCT_OEE`. An event spanning a week boundary is attributed entirely to its `event_start_ts` week (documented simplification — no observed multi-day PM/breakdown events in the generator's `Uniform(1,3)h`/`Uniform(2,8)h` duration draws, so this edge case shouldn't actually occur with this data, but the rule is stated for robustness).

`performance_pct`/`quality_pct`: fixed constants (BRD assumption 3, FR-PL-07) — **confirmed final per explicit user instruction**: not a per-line seed after all. Instead, both are single dbt `vars` (same value applied to every line, `Caliper` and `Engine Head` alike), sourced from environment variables with defaults, declared in `dbt_project.yml`:

```yaml
vars:
  oee_performance_pct: "{{ env_var('OEE_PERFORMANCE_PCT', '0.98') }}"
  oee_quality_pct: "{{ env_var('OEE_QUALITY_PCT', '0.97') }}"
```

`cons__fct_oee.sql` references them via `{{ var('oee_performance_pct') }}` / `{{ var('oee_quality_pct') }}`, cast to `FLOAT` in the `SELECT`. This supersedes the original per-line `cons__oee_constants.csv` seed idea (removed from §4's file list below) — no seed needed for this, since a single scalar constant doesn't need a lookup table. Operators can override either value per-run via `OEE_PERFORMANCE_PCT`/`OEE_QUALITY_PCT` env vars (or `--vars` on the dbt CLI) without a code change; defaults (0.98/0.97) apply identically to both lines.

`oee_pct = availability_pct × performance_pct × quality_pct` (standard OEE formula, FR-PL-07 confirms Quality/Performance are joined-in constants, Availability is the only computed driver).

### Q4 — `CONS.FCT_MAINTENANCE_EVENT` pass-through: confirmed separate start/end columns

`RAW.CMMS_LOG` has `event_start_ts`/`event_end_ts` as two separate columns (not a single timestamp + duration) — confirmed directly in `cmms.make_event()`. `duration_hours` is computed once, in `STD.CMMS_LOG` (per Module 1's own STD spec: "+ duration_hours (derived: event_end_ts − event_start_ts)"), as `DATEDIFF('second', event_start_ts, event_end_ts) / 3600.0` — **seconds, not `DATEDIFF('hour', ...)`**, since durations are fractional (`Uniform(1,3)h`, `Uniform(2,8)h`) and an hour-truncated `DATEDIFF` would silently round every event down to a whole number, corrupting both the OEE downtime sum and the displayed duration. `CONS.FCT_MAINTENANCE_EVENT` is then a straight pass-through of `STD.CMMS_LOG` (all columns, no filtering — `event_type IN ('PM','BREAKDOWN')` both included, matching the Semantic View `MaintenanceEvent` entity's "1 row / event" grain regardless of type).

### Q5 — `STD.CALENDAR` as a view: confirmed, matches project convention

`dbt_project.yml` sets no project-level `+materialized` default, so dbt's own default (`view`) already applies when a model omits `config(materialized=...)`. This project's existing models (`std__equipment.sql`, `cons__dim_equipment.sql`, etc.) always set `materialized` explicitly even when it happens to match the implicit default — so `std__calendar.sql` will explicitly set `config(materialized='view', schema='std', tags=['standardized'])` for consistency with that style, not rely on the implicit default silently. No conflict with any project convention.

### Q6 — dbt tests: not_null + relationships, matching existing precedent exactly

This project's `schema.yml` files so far use only `not_null` (on key/grain columns) and `relationships` (FK to a dim), never `unique`/`accepted_values`, and there is no `dbt_utils` package installed (`packages.yml` doesn't exist) for composite-key uniqueness tests. Matching that precedent exactly rather than introducing a new package dependency for this story:

| Model | Tests |
|---|---|
| `std__sales_order` | `not_null` on `order_week`, `product_id`, `variant` |
| `std__inventory_fg_snapshot` | `not_null` on `snapshot_week`, `product_id`, `variant` |
| `std__spare_part_snapshot` | `not_null` on `snapshot_week`, `equipment_id`, `spare_part_name` |
| `std__calendar` | `not_null` on `calendar_date` |
| `std__cmms_log` | `not_null` on `event_id`, `equipment_id`; `accepted_values` skipped (would need `dbt_utils`-free inline `accepted_values` test, which is dbt core built-in actually — include `accepted_values: ['PM','BREAKDOWN']` on `event_type`, no package needed) |
| `cons__dim_product` | `not_null` on `product_id`, `variant` |
| `cons__fct_order` | `not_null` on `order_week`; `relationships` product_id/variant → `cons__dim_product` not expressible as a single-column dbt relationships test (composite FK) — documented gap, single-column `not_null` only, matching this project's existing single-column-only relationships precedent |
| `cons__fct_inventory_fg` | `not_null` on `period_week` |
| `cons__fct_inventory_spare` | `not_null` on `period_week`; `relationships` equipment_id → `cons__dim_equipment` |
| `cons__fct_oee` | `not_null` on `line_name`, `period_week` |
| `cons__fct_maintenance_event` | `not_null` on `event_id`; `relationships` equipment_id → `cons__dim_equipment` |

`accepted_values` is a dbt-core built-in generic test (no package needed) — using it on `std__cmms_log.event_type` is a small, justified addition beyond pure precedent-matching, since the generator only ever emits exactly 2 literal values and this is a cheap, high-value correctness check.

---

## 4. Files to create/modify (Developer-agent's checklist — no code written here)

**New STD models** (`predictive_maintenance_dbt/models/standardized/`):
- `std__sales_order.sql` — `table`, pass-through of `source('raw','sales_order')`.
- `std__inventory_fg_snapshot.sql` — `table`, pass-through of `source('raw','inventory_fg_snapshot')`.
- `std__spare_part_snapshot.sql` — `table`, pass-through of `source('raw','spare_part_snapshot')`.
- `std__calendar.sql` — `view`, pass-through of `source('raw','calendar')`.
- `std__cmms_log.sql` — `table`, pass-through of `source('raw','cmms_log')` + `duration_hours` (seconds-based `DATEDIFF`, §3 Q4).

**New CONS models** (`predictive_maintenance_dbt/models/consumption/`):
- `cons__fct_order.sql` — `table`, pass-through of `ref('std__sales_order')`.
- `cons__fct_inventory_fg.sql` — `table`, pass-through of `ref('std__inventory_fg_snapshot')`, `snapshot_week` renamed `period_week`.
- `cons__fct_inventory_spare.sql` — `table`, pass-through of `ref('std__spare_part_snapshot')`, `snapshot_week` renamed `period_week`.
- `cons__fct_oee.sql` — `table`, the aggregation described in §3 Q3, joining `ref('std__calendar')` + `ref('std__cmms_log')` + `ref('cons__dim_equipment')`, with `performance_pct`/`quality_pct` pulled from `var('oee_performance_pct')`/`var('oee_quality_pct')` (no seed involved — see §3 Q3).
- `cons__fct_maintenance_event.sql` — `table`, pass-through of `ref('std__cmms_log')`.

**New seeds** (`predictive_maintenance_dbt/seeds/`):
- `cons__dim_product.csv` — 4 static rows, §3 Q1.

**Modified `.yml`/config files**:
- `predictive_maintenance_dbt/dbt_project.yml` — add `vars.oee_performance_pct`/`vars.oee_quality_pct` (env-var-backed, defaults `0.98`/`0.97`, §3 Q3).
- `predictive_maintenance_dbt/models/staging/sources.yml` — add `sales_order`, `inventory_fg_snapshot`, `spare_part_snapshot`, `calendar` under the `raw` source (`cmms_log`/`equipment`/`sensor_reading` already declared).
- `predictive_maintenance_dbt/models/standardized/schema.yml` — add entries + tests for the 5 new STD models (§3 Q6 table).
- `predictive_maintenance_dbt/models/consumption/schema.yml` — add entries + tests for the 6 new CONS models.
- `predictive_maintenance_dbt/seeds/schema.yml` — add entry + test for the 1 new seed.

No changes to any existing model file.

---

## 5. Invariants for Reviewer-agent

1. `CONS.DIM_PRODUCT` has exactly 4 rows, all 4 product/variant combos, regardless of how many actually appear in `SALES_ORDER`/`INVENTORY_FG_SNAPSHOT` (only 2 will have matching facts — expected, not a bug).
2. `CONS.FCT_OEE.availability_pct` numerator sums `STD.CMMS_LOG.duration_hours` for `event_type = 'BREAKDOWN'` **only** — a `'PM'` row must never appear in the downtime sum. If a future test shows PM rows leaking into the aggregation, that's the invariant this story is most likely to break silently (a naive `SUM(duration_hours)` without the `WHERE event_type = 'BREAKDOWN'` filter).
3. `CONS.FCT_OEE`'s `scheduled_hours` denominator counts only **sensor-enabled** equipment per line (`CONS.DIM_EQUIPMENT.is_sensor_enabled = TRUE`) — never all equipment on the line.
4. `duration_hours` (both `STD.CMMS_LOG` and anything downstream) must be fractional-hour-precise (seconds-based `DATEDIFF`), not truncated to whole hours.
5. `period_week`/`order_week`/`snapshot_week`/`event_start_ts`-derived week buckets all align to the same Monday-start week boundary across every model in this story — verify with a spot join between `cons__fct_order.order_week` and `cons__fct_oee.period_week` for the same calendar week.
6. No existing model (`std__equipment`, `std__sensor_reading`, `cons__dim_equipment`, `cons__fct_sensor_reading`, `cons__fct_anomaly_result`) is modified by this story.

---

## 6. Verification (live, 2026-09-23)

**Developer-agent (implementation)**: `dbt run` — 10/10 SUCCESS across all new models (5 STD + 5 CONS, excluding the seed). `dbt seed` — loaded 4 rows into `cons__dim_product` as designed (§3 Q1). `dbt test` — 23/23 PASS.

One self-caught build-time bug, fixed before review: an accidental duplicate `cons__dim_product` test-declaration entry existed in both `seeds/schema.yml` and `models/consumption/schema.yml` — dbt correctly rejected the clash at parse time. Fixed by removing the duplicate from `consumption/schema.yml`, keeping it solely in `seeds/schema.yml`, matching the `cons__dim_sensor_baseline` precedent (§3 Q1).

**Reviewer-agent (independent review)**: returned a clean **PASS** verdict — zero bugs/deviations found, implementation matched the frozen design exactly across all 8 checks performed. Independently re-verified 4/4 live tests against `cons__fct_oee`/`cons__dim_product`. Specifically confirmed:
- The BREAKDOWN-only PM-exclusion logic (§3 Q3, invariant 2) against a real co-occurring PM+BREAKDOWN week on the Caliper line — PM duration does not leak into the `availability_pct` numerator.
- Week-bucketing consistency (§3 Q3, invariant 5) via a live join between `cons__fct_order.order_week` and `cons__fct_oee.period_week` for the same calendar week.
- No existing model file was modified (invariant 6).

---

## 7. Explicitly deferred

- `CONS.FCT_PRIORITY_SCORE` / `PriorityScore` semantic entity — EPIC-RUL's job (needs `CONS.FCT_RUL_PREDICTION`).
- Semantic View wiring for the new `Order`/`Inventory`/`OEEMetric`/`MaintenanceEvent` entities — `S-SEM-1`/`S-OPS-POST-2`'s job, this story only makes the tables exist.
- Any stricter classical-OEE treatment that would subtract PM hours from `scheduled_hours` too (§3 Q3's accepted simplification) — revisit only if flagged in review.
- Exact `product_name` display strings — **confirmed by user** ("Brake Caliper"/"Engine Head", §3 Q1), no longer a flagged assumption.
- `performance_pct`/`quality_pct` — **updated by user**: no longer per-line placeholders; now single dbt vars (`oee_performance_pct`=0.98, `oee_quality_pct`=0.97 by default, env-var overridable), same value applied to both `Caliper` and `Engine Head` (§3 Q3).
