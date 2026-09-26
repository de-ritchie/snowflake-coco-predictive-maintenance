# Design: SH-44 — Train RUL AFT model (S-RUL-3)

Status: **Reviewed — PASS (2026-09-25)**. `Reviewer-agent` independently re-verified the implementation against this design doc with zero code deviations found: feature columns cross-checked against the live schema, hyperparameters matched verbatim against `docs/04-5-LLD.md` §2, censoring conversion, registry/versioning convention, and pipeline wiring all independently re-verified/re-run (not just read from the Developer-agent report). Fresh independent training run completed (`V_20260925_225448`, 94 train rows: 52 censored/42 uncensored). `SET_MODULE_FUNCTIONS_VOLATILITY_FROM_MANIFEST` platform capability confirmed `True`; `MANIFEST.yml` shows `volatility: IMMUTABLE` on both `PREDICT`/`EXPLAIN` for the real model (re-derived independently, not re-read from Developer-agent's claim). Prediction sweep across all 113 rows: all positive/finite, min 41.98h, max 19,341h, mean ~9,031h. See §8 (volatility results), §12 (model quality observation), and §4 (benign runtime warning note) below for details. Ready for `Documenter-agent`.
Branch: `feature/SH-4-44-train-rul-aft-model`
Epic: EPIC-RUL | Story: SH-44 (S-RUL-3: "Train RUL AFT model")
Traces to: [docs/04-5-LLD.md](../04-5-LLD.md) §2 (RUL AFT model spec), `docs/designs/SH-51-xgboost-registry-model-spike.md` (proven Registry/volatility/predict conventions on this account), `docs/designs/SH-49-training-spine-split.md` + `docs/designs/SH-50-anomaly-into-rul-features.md` (upstream `FEAST.TRAINING_DATASET_RUL` shape), `docs/05-Epics.md` EPIC-RUL / S-RUL-3

**Follows**: SH-51 (merged, PASS spike), SH-49 (merged, spine+split), SH-50 (merged, anomaly features wired in). This is the first story that actually trains the production RUL model.

---

## 1. Scope

Train a raw `xgboost.Booster` with `objective='survival:aft'` on `FEAST.TRAINING_DATASET_RUL` (`dataset_split = 'train'` only), log it to the Model Registry as `rul_aft_model` with `volatility=IMMUTABLE`, and re-verify that override actually took effect on this production-path model (not just trust SH-51's spike result on a throwaway model).

**Not in scope**: evaluation (concordance index / MAE on `dataset_split = 'test'`) — that's S-RUL-4. `cons.fct_rul_prediction` (inference dynamic table) — separate future story. Any change to `TRAINING_DATASET_RUL` itself (SH-49/SH-50 already built it).

---

## 2. Where the training code lives

**New file: `scripts/06_train_rul_model.sql`** — not a second block in `scripts/05_train_models.sql`. Keeps each model's training in its own file (mirroring the numbered-script convention already used for the SQL lifecycle sequence), and matches how the epic's "2-phase form" framing left room for an additional training step, not necessarily inside the same file.

**Pipeline ordering** (`manage.py`, per SH-50's existing 3-step phase-1 sequence): the new script slots in immediately after `05_train_models.sql`, before the second `dbt run --select feast__training_dataset_rul` step is even relevant — actually, since `06_train_rul_model.sql` *reads* `feast__training_dataset_rul` (which itself depends on `isolation_forest_model` existing, per SH-50 §4), it must run **after** that table is built, not merely after `05_train_models.sql`. Updated full sequence:

1. `dbt run --exclude tag:inference+ --exclude feast__training_dataset_rul` (phase 1 minus the RUL table)
2. `05_train_models.sql` — trains/promotes `isolation_forest_model`
3. `dbt run --select feast__training_dataset_rul` — now safe to build (reads `isolation_forest_model`)
4. **`06_train_rul_model.sql` (new, this story)** — trains/promotes `rul_aft_model`, reading `feast.training_dataset_rul`
5. `dbt run --select tag:inference+` — phase 2 (unchanged)

This is a `manage.py` change (inserting step 4 into the existing `run_dbt()`/phase-1 sequence) — flagged explicitly for `Developer-agent`, same as SH-50's own ordering change.

---

## 3. Feature set

**All available columns** from `FEAST.TRAINING_DATASET_RUL` (per SH-50's final shape) are used as training features — nothing curated out:

- 15 sensor z-score columns: `vibration_z`, `vibration_rolling_{1h,8h,24h,7d}_z`, `temperature_z`, `temperature_rolling_{1h,8h,24h,7d}_z`, `rpm_z`, `rpm_rolling_{1h,8h,24h,7d}_z`
- `hours_since_last_service`, `hours_since_install`
- `is_anomaly` (boolean), `anomaly_score` (float) — SH-50 single-tick anomaly columns
- `any_anomaly_flagged_72h` (boolean), `min_anomaly_score_72h` (float), `pct_anomalous_ticks_72h` (float) — SH-50 windowed anomaly aggregates

~22 feature columns total. Rationale: SH-50 was built specifically to make the anomaly signal available to this model — using it now, not leaving it available-but-unused, is the point of doing SH-50 before SH-44. `y_lower`/`y_upper`/`equipment_id`/`cycle_end_ts`/`dataset_split` are excluded from the feature list (labels/identifiers, not features).

Boolean columns (`is_anomaly`, `any_anomaly_flagged_72h`) are cast to numeric (`0`/`1`) before entering the `DMatrix` — XGBoost's raw Python API expects numeric input, matching the Module 5 §2 sketch's implicit assumption (no boolean handling shown there, so cast explicitly rather than let `to_pandas()`'s dtype pass through unchecked).

---

## 4. DMatrix construction & censoring convention

Matches Module 5 §2 and SH-51's proven convention exactly — no deviation:

```python
train_pdf = session.table("snowcomotive.feast.feast__training_dataset_rul").filter(col("dataset_split") == "train").to_pandas()

dtrain = xgb.DMatrix(train_pdf[feature_cols])
dtrain.set_float_info("label_lower_bound", train_pdf["y_lower"].values)
dtrain.set_float_info("label_upper_bound", train_pdf["y_upper"].fillna(np.inf).values)
```

`y_upper = NULL` (right-censored — PM-ended or still-open cycle, per SH-49 §2b) is represented as `+inf` via `.fillna(np.inf)` before `set_float_info`, not left as a Python `NaN`/SQL `NULL` — this is XGBoost's own convention for "failure time unknown, but known to be beyond `y_lower`" under `survival:aft`. Confirmed as the correct approach by both the LLD and SH-51's spike script (which exercised this exact call on synthetic censored/uncensored rows).

**Benign runtime note (confirmed during review, 2026-09-25)**: a `UserWarning: Null value detected in column hours_since_last_service` appears during `!predict` calls, traced to 3 real NULL rows in that column within the live train split (each machine's very first-ever cycle, no prior service to compute "hours since" against — same root cause SH-49 already documented for its own NULL rows). XGBoost's `DMatrix` natively treats NaN as "missing" and handles it during tree splits — not a defect, and not covered by any of this story's censoring invariants (§10 items 1–2 only address `y_upper`, not feature nulls). Noted so a future reader doesn't mistake the warning for a real problem.

---

## 5. Train/test boundary

Training reads `dataset_split = 'train'` only (per SH-49's ~94/19 split). `dataset_split = 'test'` rows are read by nobody in this story — reserved entirely for S-RUL-4's evaluation (concordance index, MAE). The stored procedure does not touch `'test'` rows at all, not even to check row counts beyond a basic sanity guard.

---

## 6. Hyperparameters

Module 5 §2's defaults, used as-is — no adjustment for the smaller real (~94-row) training set, per explicit user decision. Tuning is deferred to a later pass if evaluation (S-RUL-4) shows it's warranted:

```python
params = {
    "objective": "survival:aft",
    "aft_loss_distribution": "normal",
    "aft_loss_distribution_scale": 1.0,
    "tree_method": "hist",
    "max_depth": 4,
}
booster = xgb.train(params, dtrain, num_boost_round=100)
```

---

## 7. Model Registry logging & naming

Model name: **`rul_aft_model`** (not `rul_model` — Module 5 §2's sketch predates this naming decision; this story's script is the actual naming authority going forward). Version naming and default-alias promotion follow `scripts/05_train_models.sql`'s existing `isolation_forest_model` convention exactly (`V_<YYYYMMDD_HHMMSS>` version names, explicit `registry.get_model(...).default = version_name` promotion — Registry does not auto-promote):

```python
from snowflake.ml.registry import Registry
from snowflake.ml.model import Volatility

registry = Registry(session=session, database_name="SNOWCOMOTIVE", schema_name="CONS")
version_name = "V_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

mv = registry.log_model(
    booster,
    model_name="rul_aft_model",
    version_name=version_name,
    sample_input_data=train_pdf[feature_cols].head(1000),
    options={
        "enable_explainability": True,
        "embed_local_ml_library": True,      # same reason as isolation_forest_model -- avoids
                                              # "Packages not found: snowflake-ml-python[version=...]"
        "volatility": Volatility.IMMUTABLE,   # NOT the default for a raw Booster -- required for
                                              # inference-side dynamic table refresh (FR-FS-09)
    },
)

registry.get_model("rul_aft_model").default = version_name
```

`PACKAGES` clause on the stored procedure: `('snowflake-snowpark-python', 'snowflake-ml-python', 'pandas', 'xgboost', 'shap')` — `xgboost` added (new dependency for this script vs. `05_train_models.sql`), `shap` retained for the explainability option (SH-51 confirmed `!explain` works on the raw-Booster path, with the `::DOUBLE`-cast calling convention that inference-side code will need later, not this training script).

---

## 8. Volatility re-verification (required, not optional)

Per explicit user decision: `Developer-agent` must re-verify `volatility=IMMUTABLE` actually took effect on the real `rul_aft_model` version — not rely on SH-51's spike result on a throwaway model. Same method SH-51 proved is the only reliable one:

```python
mv.export(export_mode=ExportMode.FULL)
# read the exported MANIFEST.yml -- confirm volatility: IMMUTABLE on both the
# PREDICT and EXPLAIN method entries. SHOW VERSIONS IN MODEL / DESCRIBE MODEL
# do NOT expose a volatility field on this account -- do not use them as the check.
```

Also re-confirm the `SET_MODULE_FUNCTIONS_VOLATILITY_FROM_MANIFEST` platform capability is enabled on whichever account this actually runs on (`PlatformCapabilities.get_instance(session).is_set_module_functions_volatility_from_manifest()`) — SH-51 confirmed it `True` on `IQWYCFG-OAC98123`, but per Module 5 §2's own prerequisite note, this must not be assumed carried over silently if the active connection differs. **Note**: the current active connection per this session is `snow-co-cat-alyst` (account `IQWYCFG-OAC98123` per SH-51's own header) — same account SH-51 already verified, but `Developer-agent` should still run the check explicitly on the real model rather than skip it as "already known."

Record the actual `MANIFEST.yml` finding (pass/fail, exact field values) in this design doc's own results section once run — same discipline SH-51 used.

**Confirmed results (Reviewer-agent, 2026-09-25)**: `SET_MODULE_FUNCTIONS_VOLATILITY_FROM_MANIFEST` platform capability re-checked and confirmed `True`. `MANIFEST.yml` exported and inspected independently on the real `rul_aft_model` version (`V_20260925_225448`) — `volatility: IMMUTABLE` present on both the `PREDICT` and `EXPLAIN` method entries, matching Developer-agent's original claim exactly. Reviewer-agent re-derived this finding itself rather than trusting the reported result, per this section's own requirement.

**Operational note**: two model versions now exist in the account for `rul_aft_model` — Developer-agent's original training run and Reviewer-agent's independent re-run (`V_20260925_225448`, now the promoted default). Both satisfy every invariant in §10; this is a live-environment state fact from the verification process, not a design or code issue.

---

## 9. Files to create/modify (Developer-agent's checklist — no code written here)

**New**:
- `scripts/06_train_rul_model.sql` — the training stored procedure + `CALL`, per §2–§8 above.

**Modified**:
- `manage.py` — insert step 4 (§2's new pipeline order) into the phase-1 sequence, after the `dbt run --select feast__training_dataset_rul` step and before phase-2 (`tag:inference+`) dbt run.
- `scripts/README.md` — update the pipeline-order description to include the new step.
- `pyproject.toml` / `uv.lock` — add `xgboost` as a project dependency (already added during SH-51's spike per that design doc's §8 — confirm it's still present, don't re-add if so).

No dbt model changes — `FEAST.TRAINING_DATASET_RUL` is read-only input here (SH-49/SH-50 already built it).

---

## 10. Invariants for Reviewer-agent

1. `06_train_rul_model.sql` must filter `dataset_split == 'train'` before constructing the `DMatrix` — no `'test'` row ever enters training.
2. `label_upper_bound` must be `+inf` (via `.fillna(np.inf)`) for every row where `y_upper` is `NULL` in SQL, not left as `NaN`/dropped/treated as `0`.
3. All ~22 feature columns listed in §3 must be present in `feature_cols` — a silently-dropped column (e.g. forgetting to include the SH-50 windowed aggregates) is the most likely regression here.
4. Boolean columns (`is_anomaly`, `any_anomaly_flagged_72h`) must be cast to numeric before entering the `DMatrix` — verify no implicit `bool` dtype survives into `train_pdf[feature_cols]`.
5. `registry.get_model("rul_aft_model").default` must be explicitly set to the newly logged `version_name` after `log_model` — Registry does not auto-promote (same gotcha as `isolation_forest_model`).
6. `volatility=IMMUTABLE` must be independently confirmed via `MANIFEST.yml` export on the real `rul_aft_model` version, not assumed from SH-51's spike — Reviewer-agent should re-run this same export check itself, not just read Developer-agent's reported result.
7. `manage.py`'s phase-1 sequence must run `06_train_rul_model.sql` strictly after the `feast__training_dataset_rul` dbt build (step 3) and strictly before the phase-2 (`tag:inference+`) dbt run (step 5) — verify by reading the actual call ordering in `manage.py`, not just that each script runs successfully in isolation.
8. No `dataset_split = 'test'` row count or content is read/logged anywhere in this script.

---

## 11. Explicitly deferred

- Evaluation (concordance index, MAE on the test split) — S-RUL-4, not this story.
- `cons.fct_rul_prediction` (inference-side dynamic table calling `rul_aft_model`) — separate future story.
- Hyperparameter tuning beyond Module 5 §2's stated defaults — revisit only if S-RUL-4's evaluation results suggest it's warranted.
- Recording `isolation_forest_model`'s version string as a training-time provenance column — same open item SH-50 already deferred, still not addressed here.

---

## 12. Model quality observation (Reviewer-agent, 2026-09-25 — relevant context for S-RUL-4, not a defect in this story)

During Reviewer-agent's independent prediction sweep across all 113 rows of `FEAST.TRAINING_DATASET_RUL`, a specific accuracy pattern emerged among the 5 uncensored test-split rows (i.e. rows with a known, non-censored actual failure time — `y_lower == y_upper`):

- 4 of the 5 track within ~2x of the actual failure time (e.g. actual 42.00h vs. predicted 41.98h — a near-exact match).
- 1 (`CNC_MILLING`, `y_lower = y_upper = 566.00`) predicts 9,396 hours — **~16.6x** the actual value.

This is expected/acceptable variance for a small (94-row), untuned model per §6's explicit "no tuning yet" decision — it is **not** a code defect and does not violate any invariant in §10. It is flagged here explicitly so that S-RUL-4's evaluation (concordance index, MAE on the full test split) doesn't come as a surprise, and so this outlier isn't later mistaken for a bug introduced somewhere else. If S-RUL-4's aggregate metrics look poor, this row is a likely contributor worth investigating first (e.g. whether `CNC_MILLING` is systematically underrepresented in the 94-row train split, or whether its feature values are near a decision-boundary edge case) rather than assuming a training-pipeline error.
