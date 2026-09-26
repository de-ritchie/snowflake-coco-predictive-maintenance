# Design: SH-45 — RUL inference dynamic table (S-RUL-5)

Status: **Reviewed — PASS (2026-09-26)**. `Reviewer-agent` independently re-verified the implementation with zero code deviations found beyond the one genuine, expected SQL-sketch deviation reconciled in §3 below (`BOOLOR_AGG` over a sliding window frame is unsupported on this account — both agents independently reproduced the error; the shipped `MAX(CASE...)>0` equivalent was independently cross-checked on 2 real rows). §4's explicitly-flagged open question (incremental refresh over an upstream dynamic table's own output) is now resolved — see §4. §5's `volatility=IMMUTABLE` re-verification concern is satisfied by inference-time evidence, not just the model-level manifest check — see §5. Full live-verified numbers in §9 (Results). Ready for `Documenter-agent`.
Branch: `feature/SH-4-45-rul-inference-dynamic-table`
Epic: EPIC-RUL | Story: SH-45 (S-RUL-5: "RUL inference dynamic table")
Traces to: [docs/04-4-LLD.md](../04-4-LLD.md) §5 (inference reads FEAST, bypasses spine/split), [docs/04-5-LLD.md](../04-5-LLD.md) §2 (RUL AFT model spec) + Open Items (`volatility=IMMUTABLE`), `docs/designs/SH-44-train-rul-aft-model.md` §11 (explicit deferral of this table), `docs/designs/SH-46-rul-model-evaluation.md` (one-shot diagnostic vs. this story's live/persisted table — explicit distinction), `docs/designs/SH-50-anomaly-into-rul-features.md` (source of the 5 anomaly-derived training features this table must reproduce at inference time), `docs/05-Epics.md` EPIC-RUL / S-RUL-5

**Follows**: SH-44 (merged, trained `rul_aft_model`), SH-46 (merged, evaluation — confirmed this table is out of that story's scope), SH-23 (merged, `cons.fct_anomaly_result` — the pattern this story mirrors).

---

## 1. Scope

Build `CONS.FCT_RUL_PREDICTION`, a dynamic table that calls `MODEL(rul_aft_model, DEFAULT)!predict()` against live data, producing one persisted, continuously-queryable RUL prediction per new sensor tick per machine — the live-inference counterpart to `cons.fct_anomaly_result` (S-MODEL-3, already built) and the RUL-side counterpart to SH-46's one-shot evaluation diagnostic (explicitly *not* a persisted table, per SH-46 §7/§15).

**Not in scope**: retraining or tuning `rul_aft_model` (SH-44 territory). Any change to `FEAST.TRAINING_DATASET_RUL`, `feast__spine_maintenance_cycle`, or `cons.fct_anomaly_result` (all upstream, already built, read-only from this story's perspective). A "fact of risk"/downstream risk-scoring table that consumes this table's output — flagged by the user during design as a distinct future story; this story only builds the foundation (the prediction feed itself), not anything built on top of it.

---

## 2. Core architectural decision: reuse `cons.fct_anomaly_result`, don't re-derive it

`rul_aft_model` was trained on 22 columns (exact order, from `scripts/06_train_rul_model.sql`):

```
vibration_z, vibration_rolling_1h_z, vibration_rolling_8h_z, vibration_rolling_24h_z, vibration_rolling_7d_z,
temperature_z, temperature_rolling_1h_z, temperature_rolling_8h_z, temperature_rolling_24h_z, temperature_rolling_7d_z,
rpm_z, rpm_rolling_1h_z, rpm_rolling_8h_z, rpm_rolling_24h_z, rpm_rolling_7d_z,
hours_since_last_service, hours_since_install,
is_anomaly, anomaly_score,
any_anomaly_flagged_72h, min_anomaly_score_72h, pct_anomalous_ticks_72h
```

The first 17 columns come straight from `FEAST.FCT_SENSOR_FEATURES_INFERENCE` (already live, already carries `hours_since_last_service`/`hours_since_install` per tick — Module 4 §1). The last 5 are the real design question, since 3 of them (`any_anomaly_flagged_72h`, `min_anomaly_score_72h`, `pct_anomalous_ticks_72h`) are 72h-windowed aggregates, not single-tick values.

**Decision (confirmed with user)**: `cons.fct_rul_prediction` joins to the already-live `cons.fct_anomaly_result` for the single-tick `is_anomaly`/`anomaly_score`, and computes the 3 windowed aggregates via a window function *over that same table's history* — it does **not** independently re-call `isolation_forest_model`.

**Why this is correct, not just convenient** (this is a resolution to a real point of confusion during design, worth recording): a dynamic table's incremental refresh model is "compute exactly one new output row per new input row, but that row's computation may *read* historical rows for windowing context without ever *rewriting* them" — already proven in this exact codebase (Module 4 §3, `copiedRows:0` empirically confirmed for the sensor-rolling-features chain). `cons.fct_anomaly_result` already does this for `is_anomaly`/`anomaly_score` — one persisted row per tick, computed once, never re-scored. So the RUL table's 72h window should **read that already-persisted history**, not re-run `isolation_forest_model` up to 288 times per new tick to recompute values that were already computed and stored the first time. This mirrors the same "persist once, window over persisted history" idea Module 4 §1's rolling-features macro already uses for raw sensor readings — just one abstraction layer higher (windowing over model *output* instead of raw sensor *input*).

**This is a deliberate, explained deviation from LLD Module 4 §5's literal wording** ("`FCT_SENSOR_FEATURES_INFERENCE` is what `cons.fct_anomaly_result` and `cons.fct_rul_prediction` read FROM"), which predates SH-50's addition of anomaly-derived features to RUL's training set and didn't anticipate this dependency. It does **not** contradict Module 4 §5's actual point (inference skips spine/split machinery) — RUL inference still reads no spine, no split flag, nothing training-only. It also does not contradict `feast__training_dataset_rul.sql`'s own comment about deliberately *not* reading inference artifacts during training (that rule is about training never depending on inference, to avoid leakage/non-reproducibility — it says nothing about whether inference may depend on other inference-side live tables, which is the normal, unproblematic case here).

**Why training and inference compute the 72h window differently, and why that's fine, not a shortcut**: training's version (`feast__training_dataset_rul.sql`) is anchored to the maintenance-cycle spine (one row per cycle) via a calendar-time `DATEADD('hour', -72, ...)` join against many ticks — correct and simple for a one-shot batch rebuild. Inference has no spine; it wants "as of the latest tick itself," which is naturally a `ROWS BETWEEN 287 PRECEDING AND CURRENT ROW` frame (72h ÷ 15-min cadence = 288 ticks) — matching every other rolling feature already in Module 4 §1, and the only windowing technique in this codebase actually proven safe for incremental refresh. This was considered as a candidate for a shared macro (mirroring `sensor_rolling_features`'s one-macro-two-materializations pattern) and rejected: the anchor granularity genuinely differs (per-cycle vs. per-tick), so it isn't "identical SQL, different materialization" the way that macro's actual reuse case is — forcing one macro here would be a premature abstraction over two structurally different queries. Train/inference computing a shared concept differently for a good, precedented reason is exactly what Module 4 §5 already argues is intentional, not an inconsistency.

---

## 3. Table shape

**Reconciliation (2026-09-26): the literal `BOOLOR_AGG` sketch below was never viable — kept for context, not as a description of what shipped.** `BOOLOR_AGG(...) OVER (... ROWS BETWEEN 287 PRECEDING AND CURRENT ROW)` fails on this account with `"Sliding window frame unsupported for function BOOLOR_AGG"` — independently reproduced from scratch by both `Developer-agent` and `Reviewer-agent`. The shipped `cons__fct_rul_prediction.sql` computes `any_anomaly_flagged_72h` as `MAX(CASE WHEN is_anomaly THEN 1 ELSE 0 END) OVER (PARTITION BY equipment_id ORDER BY reading_ts ROWS BETWEEN 287 PRECEDING AND CURRENT ROW) > 0` instead — `MAX` over 0/1 supports the sliding frame where `BOOLOR_AGG` doesn't, and `Reviewer-agent` independently cross-checked this produces identical boolean-OR-over-window semantics to what `BOOLOR_AGG` would have computed, on 2 real rows (one TRUE-window case, one FALSE-window case, both matching exactly). `MIN(anomaly_score)` and the `AVG(CASE...)` for `pct_anomalous_ticks_72h` in the sketch below were unaffected — only the boolean aggregate needed the rewrite.

```sql
{{ config(
    materialized='dynamic_table',
    target_lag='1 hour',
    schema='cons',
    snowflake_warehouse='snowcomotive_wh',
    refresh_mode='incremental',
    initialize='on_create',
    tags=['inference']
) }}

-- Compile-time SHOW MODELS resolution of rul_aft_model's default version,
-- identical pattern to cons__fct_anomaly_result.sql.

WITH anomaly_history AS (
    SELECT
        equipment_id,
        reading_ts,
        is_anomaly,
        anomaly_score,
        MAX(CASE WHEN is_anomaly THEN 1 ELSE 0 END) OVER (
            PARTITION BY equipment_id ORDER BY reading_ts
            ROWS BETWEEN 287 PRECEDING AND CURRENT ROW        -- 72h = 288 ticks @ 15-min cadence
        ) > 0 AS any_anomaly_flagged_72h,        -- BOOLOR_AGG rejected here: "Sliding window frame
                                                  -- unsupported for function BOOLOR_AGG" (shipped fix)
        MIN(anomaly_score) OVER (
            PARTITION BY equipment_id ORDER BY reading_ts
            ROWS BETWEEN 287 PRECEDING AND CURRENT ROW
        ) AS min_anomaly_score_72h,
        AVG(CASE WHEN is_anomaly THEN 1.0 ELSE 0.0 END) OVER (
            PARTITION BY equipment_id ORDER BY reading_ts
            ROWS BETWEEN 287 PRECEDING AND CURRENT ROW
        ) AS pct_anomalous_ticks_72h
    FROM {{ ref('cons__fct_anomaly_result') }}
)

SELECT
    feat.equipment_id,
    feat.reading_ts,
    MODEL({{ target.database }}.cons.rul_aft_model, DEFAULT)!predict(
        feat.vibration_z, feat.vibration_rolling_1h_z, feat.vibration_rolling_8h_z, feat.vibration_rolling_24h_z, feat.vibration_rolling_7d_z,
        feat.temperature_z, feat.temperature_rolling_1h_z, feat.temperature_rolling_8h_z, feat.temperature_rolling_24h_z, feat.temperature_rolling_7d_z,
        feat.rpm_z, feat.rpm_rolling_1h_z, feat.rpm_rolling_8h_z, feat.rpm_rolling_24h_z, feat.rpm_rolling_7d_z,
        feat.hours_since_last_service, feat.hours_since_install,
        ah.is_anomaly, ah.anomaly_score,
        ah.any_anomaly_flagged_72h, ah.min_anomaly_score_72h, ah.pct_anomalous_ticks_72h
    ):"output_feature_0"::float AS predicted_rul_hours,
    ah.is_anomaly,
    ah.anomaly_score,
    ah.any_anomaly_flagged_72h,
    ah.min_anomaly_score_72h,
    ah.pct_anomalous_ticks_72h,
    '{{ default_version }}' AS model_version
FROM {{ ref('feast__fct_sensor_features_inference') }} feat
JOIN anomaly_history ah
    ON ah.equipment_id = feat.equipment_id AND ah.reading_ts = feat.reading_ts
```

Column order in the `!predict()` call is a hard correctness requirement, not stylistic — it must match `scripts/06_train_rul_model.sql`'s `feature_cols` list exactly, position-for-position (§2's 22-column list above). A silent reorder produces wrong predictions with no error, same risk explicitly flagged for the anomaly model's own training/inference column-order parity during SH-50's review.

**Boolean-to-numeric cast — confirmed mandatory, not optional (2026-09-26)**: `06_train_rul_model.sql` explicitly casts `is_anomaly`/`any_anomaly_flagged_72h` to `int` before building the `DMatrix` (`train_pdf["is_anomaly"].astype(int)`). `sample_input_data` passed to `log_model` was therefore numeric-typed for these two columns, not boolean. Empirical result: passing native SQL `BOOLEAN` values into `!predict()` for these two positions does **not** work — it fails outright with `"Invalid argument types for function 'PREDICT'"`. This was independently reproduced twice (`Developer-agent` and `Reviewer-agent`, each triggering the same error from scratch). The shipped SQL therefore applies explicit `::int` casts to `is_anomaly`/`any_anomaly_flagged_72h` in the `!predict()` call to match the training-time dtype exactly.

**Output columns** (per user decision): `equipment_id`, `reading_ts`, `predicted_rul_hours`, all 5 anomaly-derived features that fed the prediction (`is_anomaly`, `anomaly_score`, `any_anomaly_flagged_72h`, `min_anomaly_score_72h`, `pct_anomalous_ticks_72h`), `model_version` — mirrors `cons.fct_anomaly_result` carrying `is_anomaly`+`anomaly_score` together rather than exposing only the final number, for downstream transparency/debugging.

---

## 4. Incremental refresh — dependency chain and risk

Dependency chain: `feast__fct_sensor_features_inference` (dynamic, incremental) + `cons__fct_anomaly_result` (dynamic, incremental) → `cons__fct_rul_prediction` (this table). Both upstream tables are confirmed already `refresh_mode=INCREMENTAL` with no fallback (Module 4 §3, SH-23). The new table's own incrementalizability rests on:

- The join between `feat` and `ah` being an equality join on `(equipment_id, reading_ts)`, not an `ASOF`/inequality join — should incrementalize the same way `cons__fct_anomaly_result.sql`'s plain `FROM` already does.
- The `ROWS BETWEEN ... PRECEDING` window frame in `anomaly_history` reading history but only ever producing one new row per new input row from `cons.fct_anomaly_result` — same backward-only invariant Module 4 §3 already established and empirically confirmed for the sensor-features chain (`copiedRows:0` on a single-tick insert test).

**Resolved (2026-09-26) — INCREMENTAL confirmed, no fallback needed.** `SHOW DYNAMIC TABLES` on the real `cons.fct_rul_prediction` confirms `refresh_mode=INCREMENTAL`, `refresh_mode_reason=NULL`. This was not just Developer-agent's own claim — `Reviewer-agent` independently re-ran the same `SHOW DYNAMIC TABLES` check and got the identical result. A full incremental-refresh cascade test was also run and passed: insert one current-timestamp row, refresh all 5 layers in dependency order, confirm `insertedRows:1, copiedRows:0` at `cons.fct_rul_prediction` itself, then clean up the test row. This closes the "genuinely unproven combination" concern this section originally flagged — a window function over an upstream dynamic table's own live output does incrementalize the same way Module 4 §1's window frames over a plain table do; `refresh_mode='full'` was never needed.

---

## 5. `volatility=IMMUTABLE` re-check

SH-44 §10 already set `Volatility.IMMUTABLE` at `log_model()` time for `rul_aft_model`, and SH-44's own review (§8, cited in its Status line) independently re-confirmed via `MANIFEST.yml` export that this took effect for the actual production model version (`SET_MODULE_FUNCTIONS_VOLATILITY_FROM_MANIFEST` capability confirmed `True` on this account). **This is model-level, not table-level** — `cons__fct_anomaly_result.sql` needs no per-table volatility declaration of its own for `isolation_forest_model` (whose volatility defaults to `IMMUTABLE` automatically per Module 5 §1), and the same is true here: `cons__fct_rul_prediction.sql` needs no special declaration beyond the plain `MODEL(...)!predict()` call itself, now that the model-level override is confirmed. Module 5's Open Items line calling this "remains open... do not assume it generalizes automatically" is **stale** — it was written before SH-44's build/review closed exactly this gap for `rul_aft_model` specifically. `refresh_mode='incremental'` was confirmed to hold on the real `cons.fct_rul_prediction` table once built (§4) — the observable proof the volatility override works end-to-end for this new table, not just for the model in isolation.

**Inference-time confirmation beyond the model-level manifest check (2026-09-26)**: the design doc's own re-verification concern for this section is now satisfied by evidence beyond SH-44/51's manifest export. 172,675 live `!predict()` calls against `rul_aft_model` completed with all finite/positive results and 0 errors (§9) — confirming the `IMMUTABLE` guarantee holds at actual inference time through this story's serving path, not merely in the isolated model-registry check SH-44/51 already ran.

---

## 6. Pipeline / dbt wiring

New file: `predictive_maintenance_dbt/models/consumption/cons__fct_rul_prediction.sql`, `tags=['inference']` — same tag as `cons__fct_anomaly_result.sql`. No `manage.py`/script changes needed: `ref('cons__fct_anomaly_result')` and `ref('feast__fct_sensor_features_inference')` give dbt's dependency graph enough information to build this table after `cons.fct_anomaly_result` automatically within the same `dbt run --select tag:inference+` phase-2 call (`scripts/06_pipeline_run_phase2.sql`) — it does not need its own pipeline step or phase.

Prerequisite already satisfied by existing ordering: `rul_aft_model` is trained in phase 1 (`scripts/06_train_rul_model.sql`, step 4 of SH-44 §2's sequence), strictly before phase 2's `tag:inference+` run — so the model exists by the time this table's `SHOW MODELS`/`MODEL(...)` resolution runs at compile/execute time, same guarantee `cons__fct_anomaly_result.sql` already relies on for `isolation_forest_model`.

---

## 7. What "current feature row" means for RUL (resolves design question 1)

RUL predicts "hours until failure from now," but at inference there's no maintenance-cycle spine to anchor to — just "whichever tick is newest for this machine." This is fine, not a gap: `hours_since_last_service`/`hours_since_install` are already carried per-tick in `FEAST.FCT_SENSOR_FEATURES_INFERENCE` (Module 4 §1's `normalized` CTE), so every inference-time feature row already encodes "how far into its current maintenance cycle this machine is" without needing any spine construction. Inference is simply: call `predict()` on the latest tick's feature row, using the identical 22-column feature set training used, whatever cycle context that row already carries — no new per-machine "current cycle" derivation needed beyond what Module 4 §1 already provides.

---

## 8. Explicitly deferred

- A downstream "fact of risk" / risk-scoring table built on top of `cons.fct_rul_prediction` — raised by the user during design as a distinct future story; this story only builds the foundation feed.
- Streamlit/agent-tool surfacing of `predicted_rul_hours` (FR-CC-07 territory) — separate future story, same as SH-46 §5's note about registry metrics.
- Re-tuning `rul_aft_model` itself if this table's live predictions reveal accuracy issues beyond what SH-44 §12's known `CNC_MILLING` outlier already flagged — that's retraining, not this story.

---

## 9. Results (filled in — Reviewer-agent PASS, 2026-09-26)

- **Row count / value sanity**: 172,675 rows in `cons.fct_rul_prediction`, 0 nulls, 0 negatives. `predicted_rul_hours` range: **[17.41h, 18,187.7h]**, average **1,892.6h**.
- **Boolean-cast requirement — mandatory, not optional**: native SQL `BOOLEAN` values passed into `!predict()` for `is_anomaly`/`any_anomaly_flagged_72h` fail outright with `"Invalid argument types for function 'PREDICT'"` — independently reproduced twice (Developer-agent, then Reviewer-agent from scratch). The shipped SQL's explicit `::int` casts on both columns are required, matching training-time dtype exactly (§3).
- **`BOOLOR_AGG` sliding-window deviation**: confirmed to fail on this account (`"Sliding window frame unsupported for function BOOLOR_AGG"`), independently reproduced by both agents. Shipped `MAX(CASE WHEN is_anomaly THEN 1 ELSE 0 END) OVER (...) > 0` equivalent, cross-checked against 2 real rows (one TRUE-window, one FALSE-window case) — both matched exactly (§3).
- **Incremental refresh — resolved, no fallback**: `SHOW DYNAMIC TABLES` confirms `refresh_mode=INCREMENTAL`, `refresh_mode_reason=NULL`, independently re-verified by Reviewer-agent. Full 5-layer incremental-refresh cascade test (insert one current-timestamp row, refresh all layers, confirm `insertedRows:1, copiedRows:0` at `cons.fct_rul_prediction`, clean up) passed (§4).
- **`volatility=IMMUTABLE` at inference time**: 172,675 `!predict()` calls, all finite/positive, 0 errors — confirms the model-level `IMMUTABLE` guarantee (already confirmed via manifest export in SH-44/51) also holds through this story's actual serving path, not just in isolation (§5).
- **Pipeline wiring — zero changes needed**: `manage.py`, `04_pipeline_run_phase1.sql`, and `06_pipeline_run_phase2.sql` all needed no modification — confirmed by both Developer-agent and Reviewer-agent (`git diff` empty on all three). `tags=['inference']` alone was sufficient for phase-2 wiring, validating §6's design as-is.
- **`dbt test`**: 4/4 pass.
- **Verdict**: clean PASS. Implementation is correct; all claims independently re-verified live, including reproducing 2 real error conditions from scratch and cross-checking 2 different rows than Developer-agent used.
