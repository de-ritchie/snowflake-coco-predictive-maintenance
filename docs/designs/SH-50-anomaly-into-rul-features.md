# Design: Wire anomaly output into RUL features (feast.training_dataset_rul)

Status: **Frozen** — brainstormed against `docs/04-4-LLD.md` §4/§5, `docs/04-5-LLD.md` §1/§2, and live repo source (`predictive_maintenance_dbt/models/feast/feast__training_dataset_rul.sql`, `feast__training_dataset_iso.sql`, `predictive_maintenance_dbt/models/consumption/cons__fct_anomaly_result.sql`, `scripts/05_train_models.sql`, `scripts/README.md`, `manage.py`). No live Snowflake SQL access in this session — grounded in the actual committed SQL/scripts, not just the LLD's abstract description.

**Reviewer-agent: clean PASS, zero deviations (2026-09-24)** — implementation matches this doc's SQL sketch (§5) and pipeline-reordering plan (§4) exactly; pipeline reordering independently confirmed by reading actual code, not comments. Live-verified numbers worth preserving: **`iso_cutoff = rul_cutoff = 2026-03-11 22:15:00` exactly**, traced to the same underlying `MAX(reading_ts)` source (§2.4's leakage-closing fix confirmed by construction, not approximation); **zero sign/range-consistency violations across all 113 rows** (invariants 3 and 5, §7); **byte-for-byte column-order parity** with `cons__fct_anomaly_result.sql`'s existing anomaly-inference call; backward-only window aggregation confirmed (invariant 4); **53/53 dbt tests passing**; and a spot-check on a row different from Developer-agent's own reported row matched exactly.
**Story**: SH-50 (S-RUL-2: "Wire anomaly output into RUL features")
**Branch**: `feature/SH-4-50-anomaly-into-rul-features`
**Follows**: SH-49 (merged, PASS) — `FEAST.SPINE_MAINTENANCE_CYCLE` + `FEAST.TRAINING_DATASET_RUL` exist, `y_lower`/`y_upper` censored labels, ASOF-joined sensor features, time-based `dataset_split` anchored to the data's own `MAX(cycle_end_ts)`.
**Traces to**: [docs/04-4-LLD.md](../04-4-LLD.md) §4a/§5, [docs/04-5-LLD.md](../04-5-LLD.md) §1/§2 ("Open Items"), [docs/02-FRD.md](../02-FRD.md) FR-FS-02/03/04, [docs/05-Epics.md](../05-Epics.md) EPIC-RUL / S-RUL-2

---

## 1. Scope

Add the `isolation_forest_model`'s per-tick anomaly signal as new feature columns on `FEAST.TRAINING_DATASET_RUL`, resolving Module 4/5's own flagged open item, and fix one existing model (`feast__training_dataset_iso.sql`) so the leakage argument in §2 actually holds by construction rather than by coincidence.

**Not in scope**: the RUL AFT training procedure itself (S-RUL-3); `cons.fct_rul_prediction` (inference side, separate future story); walk-forward/expanding-window retraining of the anomaly model (considered and explicitly rejected, §2.3).

---

## 2. Leakage analysis (the central design question)

### 2.1 Correcting an initial premise

The anomaly model is **not** trained on the full undivided history. `scripts/05_train_models.sql` trains `IsolationForest` on `feast.training_dataset_iso WHERE dataset_split = 'train'` only — it already respects a train/test boundary of its own. The real gap is narrower than "no split at all": it's whether that boundary is *guaranteed* to sit at or before `TRAINING_DATASET_RUL`'s own boundary.

### 2.2 Why a single static split is sufficient (no walk-forward retraining needed)

`isolation_forest_model` uses no RUL labels — it's a fully independent unsupervised model over sensor features only. The only way it could leak into RUL's own train/test evaluation is if its training data physically included sensor readings from RUL's *test* period, i.e. if `iso_train_cutoff > rul_train_cutoff`. If `iso_train_cutoff <= rul_train_cutoff`, the anomaly model has never seen a single tick from RUL's test window during its own fit — scoring those ticks when building `TRAINING_DATASET_RUL` is genuine out-of-sample application, no different in kind from real production inference. That is sufficient; it does not require the two models to have been trained on data ending at the exact same historical instant, only that ISO's cutoff not sit later than RUL's.

### 2.3 Walk-forward retraining — considered, rejected

The rigorous textbook answer (many anomaly-model versions, each trained on an expanding window ending strictly before whatever RUL row it's asked to featurize) was considered and explicitly rejected for this project:
- It's disproportionate: this project has exactly **one** time-based split everywhere else (FR-FS-03), not a walk-forward CV scheme — introducing one here just for this feature would be architecturally inconsistent with the rest of the pipeline.
- It doesn't fit the existing "frozen snapshot per training run" philosophy (`docs/04-4-LLD.md` §2) — walk-forward implies many snapshots, not one.
- §2.2 shows it isn't necessary: a single split is leak-free as long as the two cutoffs are ordered correctly, which is a much smaller, surgical fix (§2.4).

### 2.4 The actual gap, and its fix

Today, `feast__training_dataset_iso.sql`'s cutoff is anchored to `CURRENT_DATE()` (wall-clock), while `feast__training_dataset_rul.sql`'s cutoff (SH-49) is anchored to `MAX(cycle_end_ts)` in the data. These usually land close together in practice, but nothing guarantees `iso_cutoff <= rul_cutoff` — they're two different anchors that could drift apart depending on when dbt actually runs relative to when data was generated (the exact CURRENT_DATE()-drift risk SH-49 already flagged and fixed for RUL's own cutoff, §3 of that design doc).

**Fix**: anchor `training_dataset_iso`'s cutoff to the same data-derived timestamp source RUL uses. Since `feast.spine_maintenance_cycle`'s still-open cycle sets `cycle_end_ts = MAX(reading_ts)` from `feast.fct_sensor_features_train` (per SH-49's SQL), anchoring ISO's cutoff to `MAX(reading_ts)` from that same table makes `iso_cutoff == rul_cutoff` **exactly**, not approximately — closing the leakage question by construction, with no retraining scheme required.

```sql
-- feast__training_dataset_iso.sql (existing model, small change)
SELECT *,
    CASE WHEN reading_ts <= (
        SELECT DATEADD('month', -6, MAX(reading_ts)) FROM {{ ref('feast__fct_sensor_features_train') }}
    ) THEN 'train' ELSE 'test' END AS dataset_split
FROM {{ ref('feast__fct_sensor_features_train') }}
```

### 2.5 Point-in-time correctness at attachment time

Separately from the cross-model split question: whatever anomaly value gets attached to a given RUL row must only reflect information knowable as of that row's `cycle_end_ts`. This is satisfied trivially for the single-tick columns (§3.1) because they're computed on the exact same `feat` row SH-49's ASOF join already selected — no new lookup, no new risk of peeking forward. It requires more care for the windowed columns (§3.2), addressed there with an explicit backward-only range condition.

### 2.6 Read against the frozen feature table, not the live inference table

`isolation_forest_model` gets called directly via `MODEL(...)!predict()`/`!decision_function()` inside `feast__training_dataset_rul.sql`, against `FEAST.FCT_SENSOR_FEATURES_TRAIN` (frozen) — the same table SH-49 already ASOF-joins against. This was chosen over reading `cons.fct_anomaly_result` for two reasons:
- `cons.fct_anomaly_result` reads from `FCT_SENSOR_FEATURES_INFERENCE`, the live/incrementally-refreshing table — coupling training-set construction to it would violate `docs/04-4-LLD.md` §5's explicit training/inference asymmetry ("training goes through spine+split, inference doesn't... this asymmetry is intentional, not an inconsistency").
- It sidesteps a **pipeline-ordering conflict** described next (§4) — `cons.fct_anomaly_result` is a phase-2 (`tag:inference+`) object; depending on it from a phase-1 FEAST table would require dbt to build phase-2 objects before phase-1 finishes, which doesn't happen today without restructuring `manage.py` in a way that's harder to reason about than calling `MODEL()` directly.

---

## 3. New columns on `FEAST.TRAINING_DATASET_RUL`

### 3.1 Single-tick columns (mirrors `cons__fct_anomaly_result.sql`'s exact call pattern)

`is_anomaly` (boolean) and `anomaly_score` (float, `decision_function`) computed for the exact same `feat` row already ASOF-attached per cycle — zero new join risk, exact parity with §2.5.

### 3.2 Rolling 72h window columns

Per this session's own ad hoc validation (72h pre-failure window showed 50% precision / 62% recall against real breakdowns), a 72-hour backward-looking lookback from `cycle_end_ts`:

- `any_anomaly_flagged_72h` (boolean) — was any tick in `(cycle_end_ts - 72h, cycle_end_ts]` flagged anomalous.
- `min_anomaly_score_72h` (float) — the most anomalous (lowest `decision_function`) tick in that window.
- `pct_anomalous_ticks_72h` (float, 0–1) — fraction of ticks in the window flagged anomalous (persistence/duration signal, not just presence).

Window is strictly backward-looking (`reading_ts > cycle_end_ts - 72h AND reading_ts <= cycle_end_ts`) — same backward-only discipline as the sensor rolling features (`docs/04-4-LLD.md` §1's correctness invariant), so this never peeks past `cycle_end_ts` into that same cycle's own future.

**Cold-start note**: cycles very close to a machine's `commissioned_ts` may have fewer than a full 72h of ticks in the window (or, in the extreme, zero) — aggregates are computed over whatever ticks exist, `NULL` only in the zero-tick edge case, consistent with how the sensor rolling features already handle cold start.

**Untested-path note (2026-09-24, Reviewer-agent)**: this zero-tick `NULL` path is implemented in the shipped SQL exactly as designed here, but the current live dataset has **zero NULLs** across all 113 rows for `any_anomaly_flagged_72h`/`min_anomaly_score_72h`/`pct_anomalous_ticks_72h` — every cycle in the live data has at least one tick in its 72h window. The code path is implemented but has never actually been exercised against real data; it should be re-checked if the dataset is ever regenerated with a shorter history that could plausibly produce a cold-start cycle with zero ticks in its window.

---

## 4. Pipeline reordering (required, not optional)

`feast__training_dataset_rul` is currently a plain `feast`-tagged model built in **phase 1** (`manage.py`'s `dbt run --exclude tag:inference+`), which runs *before* `05_train_models.sql` trains `isolation_forest_model`. Since this story's new columns call `MODEL(cons.isolation_forest_model, DEFAULT)` directly inside `feast__training_dataset_rul.sql`, the model must exist before this table builds — today it doesn't yet at that point in the sequence. `isolation_forest_model` is not a dbt-managed object (no `ref()`), so dbt's own DAG can't enforce this ordering automatically; `manage.py` must be restructured.

**New required order** (replacing `manage.py`'s current single `dbt run --exclude tag:inference+` phase-1 call):

1. `dbt run --exclude tag:inference+ --exclude feast__training_dataset_rul` — everything phase 1 already builds, minus this one table (still includes `feast__fct_sensor_features_train`, `feast__training_dataset_iso`, `feast__spine_maintenance_cycle` — none of which need the anomaly model).
2. `05_train_models.sql` (unchanged) — trains and promotes `isolation_forest_model`.
3. `dbt run --select feast__training_dataset_rul` — now safe to build; `isolation_forest_model` exists.
4. *(future, S-RUL-3)* RUL model training reads `feast.training_dataset_rul`.
5. `dbt run --select tag:inference+` — phase 2, unchanged.

This is a `manage.py` change (the `run_dbt()` / phase-1 invocation), not a dbt-only change — flagged explicitly for `Developer-agent`.

---

## 5. SQL sketch — `feast__training_dataset_rul.sql`

```sql
{{ config(
    materialized='table',
    schema='feast',
    snowflake_warehouse='snowcomotive_wh',
    tags=['feast']
) }}

-- RUL training set (S-RUL-1/S-RUL-2, LLD Module 4 §4a, Module 5 §2) -- spine
-- ASOF-joined against the frozen feature snapshot (now including the
-- anomaly model's own output), plus a time-based dataset_split (FR-FS-03).
--
-- Anomaly columns are computed here directly via MODEL(isolation_forest_model)
-- against FEAST.FCT_SENSOR_FEATURES_TRAIN (frozen) -- deliberately NOT read
-- from cons.fct_anomaly_result / FCT_SENSOR_FEATURES_INFERENCE, which are
-- inference-only, live-refreshing artifacts (Module 4 §5's train/inference
-- asymmetry: training must not depend on inference). See
-- docs/designs/SH-50-anomaly-into-rul-features.md §2 for the full leakage
-- analysis -- a single static split is sufficient here (no walk-forward
-- retraining) because isolation_forest_model is trained only on
-- feast.training_dataset_iso's 'train' split, and that split's cutoff is
-- now anchored to the same MAX(reading_ts) source this model's own cutoff
-- uses (see feast__training_dataset_iso.sql), so iso_cutoff == rul_cutoff
-- by construction -- the anomaly model never sees a tick from this table's
-- own test period during its training.
--
-- NOTE: bare `ASOF JOIN` (no LEFT/INNER prefix) required on this account,
-- matching std__sensor_reading.sql / SH-49's existing convention.

WITH split_anchor AS (
    SELECT DATEADD('month', -6, MAX(cycle_end_ts)) AS cutoff_ts
    FROM {{ ref('feast__spine_maintenance_cycle') }}
),

feat_with_anomaly AS (
    -- Extends the frozen feature snapshot with the anomaly model's per-tick
    -- output before anything else joins to it -- so the existing ASOF join
    -- below picks up is_anomaly/anomaly_score "for free" via feat.* EXCLUDE,
    -- with zero risk of the point-in-time-attached tick disagreeing with
    -- whatever tick the window aggregation (below) independently selects,
    -- since both read from this exact same CTE.
    SELECT
        feat.*,
        (MODEL({{ target.database }}.cons.isolation_forest_model, DEFAULT)!predict(
            vibration_z, vibration_rolling_1h_z, vibration_rolling_8h_z, vibration_rolling_24h_z, vibration_rolling_7d_z,
            temperature_z, temperature_rolling_1h_z, temperature_rolling_8h_z, temperature_rolling_24h_z, temperature_rolling_7d_z,
            rpm_z, rpm_rolling_1h_z, rpm_rolling_8h_z, rpm_rolling_24h_z, rpm_rolling_7d_z
        ):"output_feature_0"::int = -1) AS is_anomaly,
        MODEL({{ target.database }}.cons.isolation_forest_model, DEFAULT)!decision_function(
            vibration_z, vibration_rolling_1h_z, vibration_rolling_8h_z, vibration_rolling_24h_z, vibration_rolling_7d_z,
            temperature_z, temperature_rolling_1h_z, temperature_rolling_8h_z, temperature_rolling_24h_z, temperature_rolling_7d_z,
            rpm_z, rpm_rolling_1h_z, rpm_rolling_8h_z, rpm_rolling_24h_z, rpm_rolling_7d_z
        ):"output_feature_0"::float AS anomaly_score
    FROM {{ ref('feast__fct_sensor_features_train') }} feat
),

window_anomaly_agg AS (
    -- Backward-only 72h lookback per cycle -- never reads a tick with
    -- reading_ts > spine.cycle_end_ts, so this can't peek into that same
    -- cycle's own future.
    SELECT
        spine.equipment_id,
        spine.cycle_end_ts,
        BOOLOR_AGG(fa.is_anomaly)               AS any_anomaly_flagged_72h,
        MIN(fa.anomaly_score)                   AS min_anomaly_score_72h,
        AVG(CASE WHEN fa.is_anomaly THEN 1.0 ELSE 0.0 END) AS pct_anomalous_ticks_72h
    FROM {{ ref('feast__spine_maintenance_cycle') }} spine
    JOIN feat_with_anomaly fa
        ON fa.equipment_id = spine.equipment_id
        AND fa.reading_ts > DATEADD('hour', -72, spine.cycle_end_ts)
        AND fa.reading_ts <= spine.cycle_end_ts
    GROUP BY spine.equipment_id, spine.cycle_end_ts
)

SELECT
    spine.equipment_id,
    spine.cycle_end_ts,
    spine.y_lower,
    spine.y_upper,
    feat.* EXCLUDE (equipment_id, reading_ts),   -- now includes is_anomaly, anomaly_score
    wa.any_anomaly_flagged_72h,
    wa.min_anomaly_score_72h,
    wa.pct_anomalous_ticks_72h,
    CASE WHEN spine.cycle_end_ts <= split_anchor.cutoff_ts
         THEN 'train' ELSE 'test' END AS dataset_split
FROM {{ ref('feast__spine_maintenance_cycle') }} spine
CROSS JOIN split_anchor
ASOF JOIN feat_with_anomaly feat
    MATCH_CONDITION (spine.cycle_end_ts >= feat.reading_ts)
    ON spine.equipment_id = feat.equipment_id
JOIN window_anomaly_agg wa
    ON wa.equipment_id = spine.equipment_id AND wa.cycle_end_ts = spine.cycle_end_ts
```

`BOOLOR_AGG` (Snowflake built-in, logical OR aggregate) is used for `any_anomaly_flagged_72h` instead of a `MAX(CASE...)` idiom — direct fit, no extra casting.

---

## 6. Files to create/modify (Developer-agent's checklist — no code written here)

**Modified dbt models**:
- `predictive_maintenance_dbt/models/feast/feast__training_dataset_rul.sql` — §5 SQL (rewrite).
- `predictive_maintenance_dbt/models/feast/feast__training_dataset_iso.sql` — §2.4 cutoff-anchor fix (small, existing model).
- `predictive_maintenance_dbt/models/feast/schema.yml` — add new columns on `feast__training_dataset_rul`: `is_anomaly` (boolean, `not_null`), `anomaly_score` (float, `not_null`), `any_anomaly_flagged_72h` (boolean), `min_anomaly_score_72h` (float), `pct_anomalous_ticks_72h` (float).

**Modified pipeline script**:
- `manage.py` — split the phase-1 `dbt run --exclude tag:inference+` call per §4's new 3-step order (exclude `feast__training_dataset_rul` from the first pass, run `05_train_models.sql`, then a second `dbt run --select feast__training_dataset_rul`).
- `scripts/README.md` — update the pipeline-order table/description (§4) to reflect the new interleaved step.

No changes to `cons__fct_anomaly_result.sql`, `feast__spine_maintenance_cycle.sql`, or any inference-side (`tag:inference`) model.

---

## 7. Invariants for Reviewer-agent

1. `feast__training_dataset_rul.sql` must not reference `cons.fct_anomaly_result` or `feast__fct_sensor_features_inference` anywhere — the anomaly columns must come only from `MODEL(cons.isolation_forest_model, ...)` calls against `feast__fct_sensor_features_train`.
2. `feast__training_dataset_iso.sql`'s `dataset_split` cutoff must no longer reference `CURRENT_DATE()` — it must anchor to `MAX(reading_ts)` from `feast__fct_sensor_features_train`, and this value must equal `feast__training_dataset_rul`'s own split cutoff exactly (both ultimately derive from the same `MAX(reading_ts)`/`MAX(cycle_end_ts)` source per §2.4 — spot-check the two cutoff timestamps are identical).
3. `min_anomaly_score_72h <= anomaly_score` (point-in-time value) for every row whose ASOF-attached tick falls inside its own 72h window (should be virtually all rows) — the point value is one of the window's own members, so the window minimum can never exceed it.
4. The window join (`window_anomaly_agg`) must use a strict backward-only range (`reading_ts > cycle_end_ts - 72h AND reading_ts <= cycle_end_ts`) — never `reading_ts > cycle_end_ts`, which would leak future ticks into that cycle's own aggregate.
5. `pct_anomalous_ticks_72h` must be within `[0, 1]` for every non-NULL row; `NULL` only permitted for cycles with zero ticks in the window (extreme cold-start case near `commissioned_ts`).
6. `manage.py`'s phase-1 dbt invocation must build `isolation_forest_model` (via `05_train_models.sql`) strictly before `feast__training_dataset_rul` — verify by checking `manage.py`'s call ordering, not just that the SQL compiles (a `MODEL(...)` reference to a not-yet-created model fails at `dbt run` time, not at compile time).
7. No changes to `cons__fct_anomaly_result.sql`, `feast__spine_maintenance_cycle.sql`, or any `tag:inference` model.

---

## 8. Explicitly deferred / rejected

- **Walk-forward/expanding-window retraining of `isolation_forest_model`** — considered in depth (§2.3), rejected as disproportionate given this project's single-split design elsewhere; the cutoff-alignment fix (§2.4) closes the same leakage risk with far less complexity.
- RUL AFT model training itself, `DMatrix` construction, Model Registry logging (Module 5 §2, S-RUL-3) — this story only builds the feature columns S-RUL-3 will read.
- `cons.fct_rul_prediction` (inference side) — separate future story; not addressed here.
- Recording `isolation_forest_model`'s version string as a column on `feast__training_dataset_rul` (for provenance, matching `cons__fct_anomaly_result.sql`'s `model_version` pattern) — reasonable follow-up, not required for this story's scope; left out to avoid over-engineering a training-time snapshot that's already rebuilt wholesale on every run.
