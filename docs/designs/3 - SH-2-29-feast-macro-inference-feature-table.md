# Design: SH-29 — FEAST macro + inference feature table (S-MODEL-1)

Status: **Frozen** — built and verified directly by the user in this session, not via the Design→Developer→Reviewer chain; this doc captures the decisions and findings retroactively, per this project's established convention for that scenario (see `docs/designs/2 - SH-2-15-20-24-26-dbt-scaffold-consumption.md` §2a/§10 for the precedent).
Branch: not yet created — changes are currently uncommitted on `main` (`Jira-Triage-agent` handles branch creation/commit on request)
Epic: SH-2 | Story: SH-29
Traces to: `docs/05-Epics.md` §4.4 (S-MODEL-1), FR-FS-01/FR-FS-09, `docs/04-4-LLD.md`, `docs/04-3-LLD.md` §3

---

## 1. Problem & scope

S-MODEL-1's two tasks (`docs/05-Epics.md` §4.4):

| Task | Scope |
|---|---|
| T1 | Write `sensor_rolling_features` macro — rolling 1h/8h/24h/7d windows + baseline normalization, wide-format pivot (one row per tick, one column per sensor type) so IsolationForest sees vibration/temperature/RPM jointly, not as three independent rows. |
| T2 | `FEAST.FCT_SENSOR_FEATURES_INFERENCE` dynamic table — always-fresh, feeds the not-yet-built `cons.fct_anomaly_result`/`cons.fct_rul_prediction` (S-MODEL-3). |

Epics' escape hatch ("skip `FCT_SENSOR_FEATURES_TRAIN`'s frozen-snapshot distinction if time-pressed") was **not taken** — `FEAST.FCT_SENSOR_FEATURES_TRAIN` was built too, since a real train/infer split was needed to actually test the "predict only new events" invariant this story exists to deliver (FR-FS-09).

**Definition of Done** (this doc's addition, since the story predates a written DoD): a single new tick appended to `RAW.SENSOR_READING` produces exactly one new row in `FEAST.FCT_SENSOR_FEATURES_INFERENCE`, confirmed via Snowflake's own refresh telemetry (not just "the row count went up") — met, see §7.

---

## 2. Agreed decisions

| Decision | Resolution |
|---|---|
| **Macro signature** | `sensor_rolling_features(source_relation, baseline_relation)` — **not** `(source_relation)` alone as Module 4's original draft specified. The baseline table must be passed in by the caller as a `ref()`, not hardcoded inside the macro as a literal `cons.dim_sensor_baseline` string — a hardcoded reference breaks dbt's dependency graph (no lineage edge, no guaranteed build-order relative to the seed). Caught by the user during the design conversation, before any code was written. |
| **`sensor_type` literal casing in the pivot** | `'VIBRATION'`/`'TEMPERATURE'`/`'RPM'` (uppercase) — matches the generator's actual `SENSOR_BASELINES` dict keys. Module 4's original draft used lowercase literals, which is a real bug (see §7) rather than a style choice. |
| **`refresh_mode`** | `'incremental'` set explicitly on `feast__fct_sensor_features_inference`, not left on the `AUTO` default — `AUTO` resolves once at `CREATE` time and silently falls back to `FULL` on any unsupported construct, with no error. Pinning `INCREMENTAL` makes `CREATE` fail loudly instead if this ever regresses. |
| **`immutable_where` (frozen region)** | Added to **both** `std__sensor_reading` and `cons__fct_sensor_reading` (not just one) — `reading_ts < DATEADD(hour, -1, CURRENT_TIMESTAMP())` on each. Required because `std`'s leakage-safe `ASOF JOIN` (Module 3 §1) permanently forces `REFRESH_MODE=FULL` on it, cascading to `cons`; without a frozen region on each `FULL` table in the chain, `feast` (declared `INCREMENTAL`) cannot legally consume them. A frozen region isn't inherited from upstream — each table needs its own. See §7 for how this was discovered (it wasn't anticipated by the original design). |
| **`target_lag`** | `1 hour`, same deliberate thin/dev deviation as `std`/`cons` (from the SH-2-15..26 design doc) — not yet reverted to the 15-minute demo spec (FR-PL-04a). Carried forward, not re-decided here. |
| **Schema/tags** | `+schema: feast` added to `dbt_project.yml`; both models tagged `['feast']`, matching the existing `['standardized']`/`['consumption']` convention. |
| **dbt tests** | `not_null` on `equipment_id`/`reading_ts` for both `feast` models, plus one `relationships` test (`feast__fct_sensor_features_inference.equipment_id` → `cons__dim_equipment.equipment_id`) — same minimal-but-present pattern as SH-26, no `dbt_utils` dependency added (none exists in this project). |

---

## 3. The macro

`predictive_maintenance_dbt/macros/sensor_rolling_features.sql` — full body matches `docs/04-4-LLD.md` §1 as now corrected (parameterized `baseline_relation`, uppercase pivot literals). Not reproduced here in full; see that file and LLD Module 4 §1 for the authoritative SQL.

Tick-to-hour window sizes (1h=3, 8h=31, 24h=95, 7d=671 preceding rows, 15-min cadence) are unchanged from the original design — no deviation there.

---

## 4. Two invocations

```sql
-- feast/feast__fct_sensor_features_inference.sql
{{ config(
    materialized='dynamic_table',
    target_lag='1 hour',
    schema='feast',
    snowflake_warehouse='snowcomotive_wh',
    refresh_mode='incremental',
    tags=['feast']
) }}
{{ sensor_rolling_features(
    source_relation=ref('cons__fct_sensor_reading'),
    baseline_relation=ref('cons__dim_sensor_baseline')
) }}
```

```sql
-- feast/feast__fct_sensor_features_train.sql
{{ config(
    materialized='table',
    schema='feast',
    snowflake_warehouse='snowcomotive_wh',
    tags=['feast']
) }}
{{ sensor_rolling_features(
    source_relation=ref('cons__fct_sensor_reading'),
    baseline_relation=ref('cons__dim_sensor_baseline')
) }}
```

Identical macro call, differing only in `materialized` — the reuse benefit Module 4 §2 designed for, confirmed to actually work as intended.

---

## 5. Out of scope for this story

- Module 4 §4's training spine/split machinery (`FEAST.SPINE_MAINTENANCE_CYCLE`, `TRAINING_DATASET_RUL`/`TRAINING_DATASET_ISO`) — not part of S-MODEL-1, deferred to whichever RUL/IsolationForest training story needs it.
- `cons.fct_anomaly_result`/`cons.fct_rul_prediction` (S-MODEL-3) — reads `FROM` this story's output but is a separate not-yet-built story, with its own incremental-refresh verification still required (see §8).
- Reverting `target_lag` to 15 minutes — carried-forward open item from SH-2-15..26, not re-scoped here.

---

## 6. Deviations found during implementation (2026-08-30)

Mirroring the precedent set in `docs/designs/2 - SH-2-15-20-24-26-dbt-scaffold-consumption.md` §10 — real gaps between the original Module 4 draft and what was actually needed, found while building and testing this story, not silently absorbed.

### 6.1 Macro's baseline join was a hardcoded literal, not a `ref()`

| | |
|---|---|
| **Design doc said** (Module 4 §1, original draft) | `JOIN cons.dim_sensor_baseline b ...` inside the macro body |
| **Actually built** | `baseline_relation` promoted to a second macro parameter; both invocations pass `ref('cons__dim_sensor_baseline')` |
| **Why it matters** | A bare schema-qualified string is invisible to dbt's dependency graph — no lineage edge, no guaranteed seed-before-model build order on a fresh environment. |
| **Files** | `predictive_maintenance_dbt/macros/sensor_rolling_features.sql` |

### 6.2 `sensor_type` pivot literals were the wrong case

| | |
|---|---|
| **Design doc said** | `CASE WHEN sensor_type = 'vibration' ...` (and `'temperature'`/`'rpm'`), lowercase |
| **Actual data** | Generator emits uppercase (`VIBRATION`/`TEMPERATURE`/`RPM`) |
| **Effect if unfixed** | Every `CASE WHEN` branch silently evaluated false (Snowflake string comparison is case-sensitive) — every feature column NULL, no error raised. |
| **Verified how** | `COUNT(*) = COUNT(vibration_z)` on the live table (1632 = 1632) after the fix — confirms zero NULLs, not just "looks right in a sample". |
| **Files** | `predictive_maintenance_dbt/macros/sensor_rolling_features.sql` |

### 6.3 `ASOF JOIN` forces `std`/`cons` to `FULL`, blocking `feast`'s `INCREMENTAL` build entirely — not anticipated by the original design

| | |
|---|---|
| **Design doc assumed** | The macro's backward-only window shape alone would be sufficient for `feast` to achieve incremental refresh (Module 3 §3 flagged this as a risk to verify, but didn't anticipate the specific blocking mechanism). |
| **Actually found** | `std__sensor_reading`'s `ASOF JOIN` is confirmed unsupported for incremental refresh (`refresh_mode_reason`: `"Change tracking is not supported on queries with joins of type '[ASOF_JOIN]'"`), forcing `REFRESH_MODE=FULL` on `std`, cascading to `cons`. Attempting to create `feast__fct_sensor_features_inference` with `refresh_mode='incremental'` against this failed outright: `"...is no longer incrementalizable because... Please recreate the dynamic table."` |
| **Fix** | `immutable_where` (frozen region) added to **both** `std__sensor_reading` and `cons__fct_sensor_reading` individually — not inherited, each `FULL` table in the chain needs its own declaration. |
| **Verified how** | Post-fix, `ALTER DYNAMIC TABLE ... REFRESH` on a single new tick returned `std: insertedRows:3, copiedRows:0`, `cons: insertedRows:3, copiedRows:0`, `feast: insertedRows:1, copiedRows:0`. `SHOW DYNAMIC TABLES` confirmed `feast__fct_sensor_features_inference` resolved to `refresh_mode=INCREMENTAL`, `refresh_mode_reason=NULL`. |
| **Files** | `predictive_maintenance_dbt/models/standardized/std__sensor_reading.sql`, `predictive_maintenance_dbt/models/consumption/cons__fct_sensor_reading.sql` |
| **Docs updated** | `docs/04-3-LLD.md` §3, `docs/04-4-LLD.md` §2/§3, `docs/02-FRD.md` FR-FS-09, `docs/03-HLD.md` |

### 6.4 `immutable_where`'s `CURRENT_TIMESTAMP()` anchor breaks on backdated test data — test-methodology finding, not a build defect

Discovered while testing 6.3's fix with the thin generator's fixed-calendar-date output (Jan 2026, while the account's real clock is Aug 2026): every row, including brand-new ticks, already satisfies "older than 1 hour ago" the instant it's inserted, so incremental refresh silently reports `"No new data"` forever for such batches. Confirmed the mechanism itself was sound by inserting a row with a genuinely current `reading_ts` — that one refreshed correctly (`insertedRows:3, copiedRows:0`). Not a defect in this story's deliverable; documented as an operational caveat for future testing/data generation (`docs/04-4-LLD.md` §2, `/memories/repo/dynamic-tables-feast.md`).

---

## 7. Verification evidence (2026-08-30)

- `dbt test` — 10/10 PASS across `std__sensor_reading`, `cons__fct_sensor_reading`, `feast__fct_sensor_features_inference`, `feast__fct_sensor_features_train`.
- `SHOW DYNAMIC TABLES`: `FEAST__FCT_SENSOR_FEATURES_INFERENCE | INCREMENTAL | None` (no fallback reason).
- Live row counts consistent end-to-end: 4896 raw-grain rows → 1632 pivoted ticks in both `feast` tables, `COUNT(vibration_z) = COUNT(*)` (zero NULLs).
- Single-new-tick refresh telemetry: `feast__fct_sensor_features_inference: insertedRows:1, copiedRows:0` — the concrete proof of FR-FS-09's "predict only new events, never re-derive history" invariant holding, not just a functional-correctness argument.

---

## 8. Open items for the next story (S-MODEL-3)

- The same incremental-refresh verification is required one layer down, for `cons.fct_anomaly_result`/`cons.fct_rul_prediction` — this story's confirmation does not automatically extend to that layer. Must independently confirm `refresh_mode='incremental'` holds there too, and that the registered model's `volatility=IMMUTABLE` (FR-FS-00f) is actually set (a `VOLATILE` method forces a full rescore regardless of everything else being correct).
- `MODEL(...)!predict()`/`!decision_function()` as a construct has not been empirically tested against Snowflake's incremental-refresh support matrix — flagged, not assumed, in `docs/05-Epics.md`'s existing S-MODEL-3 "Spike/verify" task.
