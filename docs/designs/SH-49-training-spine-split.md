# Design: RUL training spine + split (FEAST.SPINE_MAINTENANCE_CYCLE, FEAST.TRAINING_DATASET_RUL)

Status: **Frozen** — brainstormed against `docs/04-4-LLD.md` §4, `docs/04-2-LLD.md` §5/§6 (generator mechanics), `docs/04-5-LLD.md` §2/§3 (what the spine's output feeds), and live repo source (`generator/fulldata/simulate.py`, `cons__fct_maintenance_event.sql`, `cons__dim_equipment.sql`, existing FEAST dbt models). No live Snowflake SQL access in this session — schema/mechanics verified from generator source and existing dbt models, same approach as `docs/designs/SH-69-std-cons-order-inventory-oee-models.md`.
**Reviewer-agent: clean PASS, zero deviations (2026-09-24)** — implementation (`feast__spine_maintenance_cycle.sql`, `feast__training_dataset_rul.sql`, `schema.yml`) matches this doc's SQL sketches verbatim, including comments. All 7 independent verification checks passed. Live-verified numbers worth preserving: **113 spine rows** (110 closed cycles from real `cons.fct_maintenance_event` rows + 3 still-open cycles, one per sensor-enabled machine); **zero censoring mismatches** (63 PM-ended → censored, 47 BREAKDOWN-ended → uncensored); **CNC_BORING spot-check** — `y_lower = 373.00` hours, exact match against an independent tick-count reconstruction; **94/19 train/test split**; and the bare-`ASOF JOIN` grammar (§3) re-confirmed live against this account — it still rejects `LEFT`/`INNER` prefixes, matching `std__sensor_reading.sql`'s existing precedent.
**Story**: SH-49 (S-RUL-1: "Training spine + split")
**Branch**: `feature/SH-4-49-training-spine-split`
**Follows**: SH-51 spike (merged, PASS) — raw `xgboost.Booster` via Model Registry, `MODEL(...)!predict()`/`!explain()` confirmed working in SQL, no stored-procedure fallback needed.
**Traces to**: [docs/04-4-LLD.md](../04-4-LLD.md) §4 (primary spec), [docs/04-2-LLD.md](../04-2-LLD.md) §5/§6, [docs/04-5-LLD.md](../04-5-LLD.md) §2/§3, [docs/02-FRD.md](../02-FRD.md) FR-DG-11, FR-FS-01/03/04, [docs/05-Epics.md](../05-Epics.md) EPIC-RUL / S-RUL-1

---

## 1. Scope

Build the two training-only FEAST models Module 4 §4a specifies, resolving the module's own flagged open item ("exact cycle-pairing SQL... not yet written statement-by-statement"):

- `FEAST.SPINE_MAINTENANCE_CYCLE` (`table`) — one row per maintenance cycle per machine, with right-censored survival labels (`y_lower`/`y_upper`).
- `FEAST.TRAINING_DATASET_RUL` (`table`) — the spine ASOF-joined against `FEAST.FCT_SENSOR_FEATURES_TRAIN`, plus a time-based `dataset_split` flag.

**Not in scope** (explicitly deferred, §7): wiring `cons.fct_anomaly_result`'s `is_anomaly`/`anomaly_score` into the training set (Module 4's own open item, restated in Module 5 §2 — belongs to a later S-RUL story once the anomaly model's inference table is confirmed stable against this feature set); the actual XGBoost AFT training stored procedure and Model Registry logging (Module 5 §2, S-RUL-3); a `hours_since_last_pm` feature distinct from the existing `hours_since_last_service`.

---

## 2. Cycle-pairing logic

### 2a. Cycle boundaries: every CMMS event, not BREAKDOWN-only

Confirmed against `generator/fulldata/simulate.py`: **both** `PM` and `BREAKDOWN` events reset the machine's wear clock (`state.t_hours = 0.0`, new failure mode + `T_fail` drawn — lines 155/199-201/242-244/265-267). A "cycle" is a physical wear-accumulation window bounded by whatever reset it, not specifically by failure. Module 2 LLD §5/§6 (lines 105-118) already documents this: "Either event: log to CMMS, draw a new mode and T_fail for the next cycle, reset t=0" and "Whatever cycle is in progress — for either reason — is right-censored."

So cycle boundaries = every row in `cons.fct_maintenance_event` per `equipment_id`, ordered by `event_start_ts`, plus one still-open final cycle per machine (no closing event yet).

### 2b. Censoring: determined by how the cycle ended, not by boundary choice

| Cycle end reason | Censored? | `y_upper` |
|---|---|---|
| `BREAKDOWN` | No — observed failure | `= y_lower` |
| `PM` | Yes — PM preempted before `T_fail` reached | `NULL` |
| Still open (no closing event) | Yes — window ended before failure | `NULL` |

This is the standard reliability-engineering/survival-analysis treatment for maintenance-interrupted run-to-failure data — it's the reason an AFT objective with censored intervals is used at all (FR-DG-11, Module 5 §2), not a bespoke choice for this project. Pairing cycles BREAKDOWN-to-BREAKDOWN only (ignoring PM as a boundary) was considered and rejected: it would span a wear-clock reset the label doesn't account for, and would discard legitimate censored observations that the AFT objective exists specifically to exploit.

### 2c. `y_lower`/`y_upper` units: operating hours via tick-count, not calendar `DATEDIFF`

Confirmed against `generator/fulldata/simulate.py`: the simulator's own survival clock (`state.t_hours`) increments by `TICK_HOURS` (0.25h) only on ticks where the machine is actually simulated as operating — it is **not** wall-clock time between event timestamps (idle nights/weekends/holidays never advance it). `cons.fct_maintenance_event` stores only `event_start_ts`/`event_end_ts` (calendar timestamps) — no operating-hours-accrued column. A naive `DATEDIFF('hour', prev.event_end_ts, this.event_start_ts)` would include idle time and systematically mismatch the units `T_fail` was actually drawn against.

**Resolution**: reconstruct operating hours by counting `feast.fct_sensor_features_train` rows (one per operating tick, 15-minute cadence) that fall strictly inside each cycle's `(cycle_start_ts, cycle_end_ts]` window, then multiply by 0.25h. Idle/weekend/holiday hours never have a tick row, so they're excluded automatically — no gap-tracking column needed on `cons.fct_maintenance_event`, no Module-1 change required. `cycle_start_ts` for each machine's first-ever cycle (no prior CMMS event) is `cons.dim_equipment.commissioned_ts`.

### 2d. SQL sketch — `FEAST.SPINE_MAINTENANCE_CYCLE`

```sql
{{ config(
    materialized='table',
    schema='feast',
    snowflake_warehouse='snowcomotive_wh',
    tags=['feast']
) }}

-- Per-cycle RUL survival spine (S-RUL-1, LLD Module 4 §4a). Cycle boundaries =
-- every CMMS event (PM or BREAKDOWN) per machine -- both reset the wear clock
-- in the generator (Module 2 LLD §5), so both are natural cycle boundaries.
-- Censoring depends on how the cycle ended (Module 2 LLD §6, FR-DG-11):
--   BREAKDOWN-ended -> uncensored, y_upper = y_lower
--   PM-ended / still-open -> censored, y_upper = NULL
-- y_lower is TRUE OPERATING HOURS, reconstructed by counting feature-table
-- ticks inside the cycle window (0.25h/tick) -- NOT DATEDIFF on event
-- timestamps, which would wrongly include idle/weekend/holiday time that
-- never advances the simulator's own t_hours clock.

WITH events AS (
    SELECT
        equipment_id,
        event_type,
        event_end_ts,
        LAG(event_end_ts) OVER (
            PARTITION BY equipment_id ORDER BY event_start_ts
        ) AS cycle_start_ts
    FROM {{ ref('cons__fct_maintenance_event') }}
),

closed_cycles AS (
    SELECT equipment_id, cycle_start_ts, event_end_ts AS cycle_end_ts, event_type
    FROM events
),

open_cycles AS (
    -- still-in-progress final cycle per machine -- right-censored at the end
    -- of the historical window (FR-DG-11), no closing CMMS event yet
    SELECT
        e.equipment_id,
        MAX(e.event_end_ts) AS cycle_start_ts,
        (SELECT MAX(reading_ts) FROM {{ ref('feast__fct_sensor_features_train') }}) AS cycle_end_ts,
        NULL AS event_type
    FROM {{ ref('cons__fct_maintenance_event') }} e
    GROUP BY e.equipment_id
),

all_cycles AS (
    SELECT * FROM closed_cycles
    UNION ALL
    SELECT * FROM open_cycles
)

SELECT
    c.equipment_id,
    c.cycle_end_ts,
    COUNT(f.reading_ts) * 0.25                                            AS y_lower,
    CASE WHEN c.event_type = 'BREAKDOWN' THEN COUNT(f.reading_ts) * 0.25
         ELSE NULL END                                                     AS y_upper
FROM all_cycles c
JOIN {{ ref('cons__dim_equipment') }} eq ON eq.equipment_id = c.equipment_id
LEFT JOIN {{ ref('feast__fct_sensor_features_train') }} f
    ON f.equipment_id = c.equipment_id
    AND f.reading_ts > COALESCE(c.cycle_start_ts, eq.commissioned_ts)
    AND f.reading_ts <= c.cycle_end_ts
GROUP BY c.equipment_id, c.cycle_end_ts, c.event_type
```

**Invariant for Reviewer-agent**: every machine has exactly one "open" (still-in-progress) cycle with `event_type IS NULL` and `y_upper IS NULL`; every `BREAKDOWN`-ended cycle has `y_upper = y_lower` (non-NULL); every `PM`-ended cycle has `y_upper IS NULL`. A `y_upper` value that's non-NULL for a PM-ended row is the bug this design is most likely to regress into if the `CASE` condition gets inverted or dropped.

---

## 3. `FEAST.TRAINING_DATASET_RUL`

ASOF-joins the spine against the frozen feature snapshot to attach point-in-time-correct sensor features to each cycle's end, then applies the time-based train/test split.

**ASOF JOIN grammar**: uses a **bare `ASOF JOIN`** (no `LEFT`/`INNER` prefix), matching this project's already-established, empirically-verified convention (`std__sensor_reading.sql`'s comment: "this Snowflake account's ASOF JOIN grammar does not accept an explicit LEFT/INNER prefix... bare `ASOF JOIN` already null-pads unmatched left rows"). **Resolved**: Reviewer-agent re-verified this live against the currently active account (2026-09-24) — it still rejects `LEFT`/`INNER` prefixes; the bare-`ASOF JOIN` convention holds, matching `std__sensor_reading.sql`'s precedent.

**Split cutoff**: anchored to the data's own max `cycle_end_ts`, not wall-clock `CURRENT_DATE()`. The generator's `historical_end_date` is anchored to whatever `--now` was passed to `manage.py up` at generation time (defaults to real `date.today()` then) — if dbt runs materially later than the data was generated, a literal `CURRENT_DATE()` cutoff (as in the LLD's original draft sketch) could drift past the data's actual max timestamp and produce a degenerate split (empty or near-empty `test`). Anchoring to `MAX(cycle_end_ts)` in the spine itself makes the ~2.5yr-train/~6mo-test split (FR-FS-03) always relative to the data's own range, regardless of when dbt actually runs.

```sql
{{ config(
    materialized='table',
    schema='feast',
    snowflake_warehouse='snowcomotive_wh',
    tags=['feast']
) }}

-- RUL training set (S-RUL-1, LLD Module 4 §4a) -- spine ASOF-joined against
-- the frozen feature snapshot, plus a time-based dataset_split (FR-FS-03).
-- Split cutoff is anchored to the data's own MAX(cycle_end_ts), not wall-clock
-- CURRENT_DATE(), so it stays a clean ~85/15 split regardless of how much
-- time has passed between data generation and this dbt run.
-- feat is already one row per (equipment_id, reading_ts) post-pivot (Module 4
-- §1) -- no per-sensor-type fanout to worry about.

WITH split_anchor AS (
    SELECT DATEADD('month', -6, MAX(cycle_end_ts)) AS cutoff_ts
    FROM {{ ref('feast__spine_maintenance_cycle') }}
)

SELECT
    spine.equipment_id,
    spine.cycle_end_ts,
    spine.y_lower,
    spine.y_upper,
    feat.* EXCLUDE (equipment_id, reading_ts),
    CASE WHEN spine.cycle_end_ts <= split_anchor.cutoff_ts
         THEN 'train' ELSE 'test' END AS dataset_split
FROM {{ ref('feast__spine_maintenance_cycle') }} spine
CROSS JOIN split_anchor
ASOF JOIN {{ ref('feast__fct_sensor_features_train') }} feat
    MATCH_CONDITION (spine.cycle_end_ts >= feat.reading_ts)
    ON spine.equipment_id = feat.equipment_id
```

**Invariant for Reviewer-agent**: `dataset_split` must contain both `'train'` and `'test'` values with a non-trivial `test` count (not just a single row) — a degenerate split (all rows landing in one bucket) signals either the anchor logic broke or the spine has too few cycles, and must be investigated, not silently accepted. Also verify the ASOF join produces no fully-NULL `feat.*` rows for any cycle (would indicate a cycle_end_ts earlier than any available feature row — shouldn't occur given `cycle_end_ts` always comes from real CMMS/tick timestamps, but worth checking on first run).

---

## 4. Materialization

Both models are plain dbt `table` materializations — matching `FEAST.FCT_SENSOR_FEATURES_TRAIN`'s existing pattern and this project's established convention (`config(materialized='table', schema='feast', snowflake_warehouse='snowcomotive_wh', tags=['feast'])`, identical block used by `feast__fct_sensor_features_train.sql`/`feast__training_dataset_iso.sql`). Not dynamic tables — Module 4 §2 already established that training-time snapshots must be frozen and rebuilt once per explicit pipeline run (FR-OPS-02a), not live-refreshing; a training-only spine/dataset table is no different.

---

## 5. Files to create/modify (Developer-agent's checklist — no code written here)

**New FEAST models** (`predictive_maintenance_dbt/models/feast/`):
- `feast__spine_maintenance_cycle.sql` — §2d SQL.
- `feast__training_dataset_rul.sql` — §3 SQL.

**Modified `.yml`**:
- `predictive_maintenance_dbt/models/feast/schema.yml` — add both models, matching the existing `feast__training_dataset_iso` entry's style:
  - `feast__spine_maintenance_cycle`: `not_null` on `equipment_id`, `cycle_end_ts`, `y_lower`.
  - `feast__training_dataset_rul`: `not_null` on `equipment_id`, `cycle_end_ts`, `y_lower`, `dataset_split`; `accepted_values: ["train", "test"]` on `dataset_split` (matches `feast__training_dataset_iso`'s existing test on the same column).

No changes to any existing model file (`cons__fct_maintenance_event`, `cons__dim_equipment`, `feast__fct_sensor_features_train` all read-only inputs here).

---

## 6. Invariants for Reviewer-agent (consolidated)

1. Every machine has exactly one open cycle (`event_type IS NULL`, `y_upper IS NULL`) in the spine.
2. `y_upper` is non-NULL if and only if the cycle's closing event was `BREAKDOWN`.
3. `y_lower`/`y_upper` are tick-count-derived (operating hours), not `DATEDIFF` on calendar timestamps — spot-check one cycle's `y_lower` against a manual `COUNT(*) * 0.25` over `feast__fct_sensor_features_train` for that window.
4. `dataset_split` in `feast__training_dataset_rul` has both values represented, with the cutoff anchored to `MAX(cycle_end_ts)` in the data, not `CURRENT_DATE()`.
5. `feast__training_dataset_rul`'s ASOF join must not silently null-pad every row's feature columns — verify at least one row has non-NULL `feat.*` values before trusting the join.
6. No existing model is modified by this story.

---

## 7. Explicitly deferred

- Wiring `cons.fct_anomaly_result.is_anomaly`/`anomaly_score` into `FEAST.TRAINING_DATASET_RUL` — Module 4's own open item, restated in Module 5 §2; deferred to a later S-RUL story once needed.
- A `hours_since_last_pm` feature (resets on PM only, distinct from the existing `hours_since_last_service` which resets on any event) — would require a Module 3/4 (FEAST macro / `std__sensor_reading`) change, out of this story's spine+split scope. Existing `hours_since_last_service`/`hours_since_install` are used as-is.
- The actual XGBoost AFT training stored procedure, `DMatrix` construction, Model Registry logging, and evaluation (Module 5 §2/§3) — S-RUL-3's job, this story only builds the two upstream tables it will read from.
