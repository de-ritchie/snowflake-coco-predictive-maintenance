# Design: SH-23 — Inference dynamic table (S-MODEL-3)

Status: **Frozen** — built and verified directly by the user in this session, not via the Design→Developer→Reviewer chain; this doc captures the decisions and findings retroactively, per this project's established convention (see `docs/designs/3 - SH-2-29-feast-macro-inference-feature-table.md` §8, `docs/designs/4 - SH-2-22-train-isolation-forest.md`).
Branch: not yet created — changes are currently uncommitted on `main` (`Jira-Triage-agent` handles branch creation/commit on request)
Epic: SH-2 | Story: SH-23
Traces to: `docs/05-Epics.md` §4.4 (S-MODEL-3), FR-FS-00b/00e, FR-FS-09 (critical verification), `docs/04-1-LLD.md` (CONS section)

---

## 1. Problem & scope

S-MODEL-3's two tasks (`docs/05-Epics.md` §4.4):

| Task | Scope |
|---|---|
| T1 | `CONS.FCT_ANOMALY_RESULT` dynamic table calling `MODEL(...)!predict/!decision_function(...)`. |
| T2 | **Spike/verify**: does `INITIALIZE=ON_CREATE` correctly backfill history (FR-FS-00e)? Does the dynamic table achieve true incremental refresh, or fall back to full rescore (FR-FS-09)? Document the answer — do not assume. |

**Definition of Done** (per Epics): a manually-triggered new sensor tick produces exactly one new row in `CONS.FCT_ANOMALY_RESULT` without visibly rescoring history — **met, see §5**.

---

## 2. Agreed decisions

| Decision | Resolution |
|---|---|
| **Model version call form** | `MODEL(isolation_forest_model, DEFAULT)!predict(...)` — not FRD's originally-specified `LAST` alias. `SHOW MODELS` confirmed `DEFAULT` is a real, valid alias (`aliases: {"DEFAULT":..., "FIRST":..., "LAST":...}`), and this project now explicitly promotes each newly trained version to default (`scripts/05_train_models.sql`, SH-22 §4.7) — `DEFAULT` expresses "serve whichever version was deliberately promoted" directly, instead of relying on "most recently created" happening to coincide with "the one we want serving" (`LAST` would silently start serving an untested version the moment training logs it, before anyone decides it should go live). |
| **`model_version` column source** | No live SQL-queryable source exists for this — confirmed empirically: no `INFORMATION_SCHEMA.MODELS`/`MODEL_VERSIONS` table/view in this account. Resolved once at `dbt run` compile time via a Jinja macro (`run_query(SHOW MODELS ...)`) instead, baked into the model's SQL as a literal. Safe given this project's own pipeline ordering (`05_train_models.sql` always runs immediately before this table's `dbt run`, FR-OPS-02a/02b) — flagged as a real constraint in §6, not silently assumed safe forever. |
| **Output extraction** | `MODEL(...)!predict(...)`/`!decision_function(...)` return their single output as `output_feature_0`, extracted via `:"output_feature_0"` — confirmed against the model's own logged spec (`SHOW VERSIONS`' `model_spec` field: `predict` returns `INT64`, `decision_function`/`score_samples` return `DOUBLE`). Not guessed — this was FR-FS-00b's own flagged "exact return shape" open item, resolved by direct SQL testing before writing the model (see §4.1). |
| **`is_anomaly` derivation** | `sklearn.IsolationForest.predict()` returns `-1`=anomaly / `1`=normal — `is_anomaly` is `(predict_raw = -1)`, a boolean, not the raw `-1`/`1` int. |
| **`initialize`** | `initialize='on_create'` set explicitly (dbt-snowflake supports this as a real dynamic-table config key, confirmed via adapter source) even though it's Snowflake's own default — same discipline as pinning `refresh_mode` explicitly rather than relying on defaults holding silently. |

---

## 3. What was built

- `predictive_maintenance_dbt/models/consumption/cons__fct_anomaly_result.sql` — `dynamic_table`, `refresh_mode='incremental'`, `target_lag='1 hour'` (dev-mode, matching this project's existing deviation from the 15-min demo spec), `tags=['inference']` (picked up automatically by `manage.py`'s `run_dbt_phase2_and_test()`, no orchestrator change needed). Reads `FROM feast.feast__fct_sensor_features_inference`.
- `predictive_maintenance_dbt/models/consumption/schema.yml` — tests for `cons__fct_anomaly_result` (`not_null` on `equipment_id`/`reading_ts`/`is_anomaly`, `relationships` on `equipment_id` → `cons__dim_equipment`).
- `manage.py`'s comment updated (line ~25) — no code change needed, `tag:inference+` selection was already wired ahead of time in SH-22.
- `scripts/06_pipeline_run_phase2.sql`'s stale "NOT YET BUILT" header updated to reflect this story's completion.

---

## 4. Deviations / findings during implementation (2026-08-31)

### 4.1 Predict/decision_function SQL spike (ahead of writing the actual model)

Ran directly against the already-built `feast.feast__fct_sensor_features_inference` before writing this story's dynamic table, to de-risk syntax:

```sql
SELECT
    f.equipment_id, f.reading_ts,
    isolation_forest_model!predict(<15 sensor_z cols>):"output_feature_0"::int AS predict_raw,
    isolation_forest_model!decision_function(<15 sensor_z cols>):"output_feature_0"::float AS decision_score
FROM feast.feast__fct_sensor_features_inference f
```

Confirmed: (a) omitting the version entirely resolves to the default version, identical output to explicit `MODEL(name, LAST)!predict(...)`; (b) `MODEL(name, DEFAULT)!predict(...)` is also valid syntax; (c) both functions expose `output_feature_0`. This resolved FR-FS-00b's own flagged "exact return shape" unknown — no longer a guess by the time the real model was written.

### 4.2 No SQL-queryable model-version metadata source

| | |
|---|---|
| **Actually found** | `SELECT * FROM TABLE(INFORMATION_SCHEMA.MODEL_VERSIONS(...))` → `Unknown table function`. `SELECT * FROM information_schema.models` → `Object does not exist`. Neither exists in this account as a plain-SQL-queryable source. `SHOW MODELS`/`SHOW VERSIONS` do expose the right data (`default_version_name`, `aliases`), but `SHOW` commands can't be embedded as a subquery inside a dynamic table's `SELECT`. |
| **Fix** | Resolved once at `dbt run` compile time instead, via a Jinja macro (`{% if execute %}{% set results = run_query('SHOW MODELS ...') %}{% endif %}`), baked into the model SQL as a string literal. |
| **Files** | `predictive_maintenance_dbt/models/consumption/cons__fct_anomaly_result.sql` |

---

## 5. Verification evidence (2026-08-31)

- **Backfill (FR-FS-00e)**: `dbt run --select cons__fct_anomaly_result` created the table with all 1,440 historical rows correctly scored on the initial `CREATE` — no separate backfill step needed.
- **`SHOW DYNAMIC TABLES`**: `refresh_mode: 'INCREMENTAL'` confirmed (not `FULL`) — the model's `volatility=IMMUTABLE` (inferred from `sklearn` built-in type, FR-FS-00f) held as expected.
- **Critical incremental-refresh test (FR-FS-09, Epics' Definition of Done)**: inserted one new real-time tick (single shared `CURRENT_TIMESTAMP()` across all 3 sensor rows — an initial attempt using 3 independent `CURRENT_TIMESTAMP()` calls produced 3 slightly different timestamps and was redone) into `RAW.SENSOR_READING`, then cascade-refreshed `std__sensor_reading` → `cons__fct_sensor_reading` → `feast__fct_sensor_features_inference` → `cons__fct_anomaly_result`:

  | Table | Result |
  |---|---|
  | `std__sensor_reading` | `insertedRows:6, copiedRows:0, deletedRows:3` (expected churn — `FULL` refresh mode, only rows older than 1hr are frozen) |
  | `cons__fct_sensor_reading` | `insertedRows:6, copiedRows:0, deletedRows:3` (same reason, cascades from std) |
  | `feast__fct_sensor_features_inference` | **`insertedRows:1, copiedRows:0, deletedRows:0`** |
  | `cons__fct_anomaly_result` | **`insertedRows:1, copiedRows:0, deletedRows:0`** |

  The two `INCREMENTAL`-pinned layers both show the clean single-row, zero-history-touched result — genuine incremental refresh confirmed at this new model-inference layer too, not just inherited by assumption from SH-29's FEAST-layer confirmation.
- `dbt test --select cons__fct_anomaly_result`: 4/4 tests pass.
- Test tick rows are left in `RAW.SENSOR_READING`/downstream tables (not cleaned up) — same precedent as SH-29's equivalent test. A full `manage.py down && manage.py up` clears them if a pristine 1,440-row baseline is needed again.

---

## 6. Open items

- `model_version`'s compile-time resolution (§2, §4.2) goes stale if the model is ever retrained without a following `dbt run` — not a risk in this project's own pipeline ordering (training always precedes the phase-2 `dbt run`, FR-OPS-02a/02b), but a real constraint if that ordering ever changes.
- `cons.fct_rul_prediction` (RUL model inference) is a separate future story (EPIC-RUL) — needs its own incremental-refresh verification, plus its own explicit `volatility=IMMUTABLE` override since the RUL model is logged via the custom `xgboost.Booster` path (FR-FS-00f already anticipates this).
- `target_lag='1 hour'` is still the dev-mode deviation from the 15-minute demo spec (matching `std`/`cons`/`feast`'s existing same deviation) — revert before the real demo.
