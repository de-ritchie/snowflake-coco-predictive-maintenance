# Design: SH-22 — Train IsolationForest (S-MODEL-2)

Status: **Frozen** — built and verified directly by the user in this session, not via the Design→Developer→Reviewer chain; this doc captures the decisions and findings retroactively, per this project's established convention for that scenario (see `docs/designs/2 - SH-2-15-20-24-26-dbt-scaffold-consumption.md` §2a/§10 and `docs/designs/3 - SH-2-29-feast-macro-inference-feature-table.md` for the precedent).
Branch: not yet created — changes are currently uncommitted on `main` (`Jira-Triage-agent` handles branch creation/commit on request)
Epic: SH-2 | Story: SH-22
Traces to: `docs/05-Epics.md` §4.4 (S-MODEL-2), FR-FS-00/00a/00f, `docs/04-5-LLD.md` §1

---

## 1. Problem & scope

S-MODEL-2's three tasks (`docs/05-Epics.md` §4.4):

| Task | Scope |
|---|---|
| T1 | Python stored procedure or notebook cell training IsolationForest on the thin dataset. |
| T2 | Log to Model Registry with `sample_input_data`, confirm default `volatility=IMMUTABLE`. |
| T3 | Fold the training call into a first-cut pipeline-run script (pipeline-run script v1, single-model). |

**T0 (a real prerequisite, not listed in the Epics story but required before T1 could run at all)**: `feast.training_dataset_iso` (Module 4 §4b) didn't exist yet — S-MODEL-1 explicitly deferred it. Built as a small dbt model wrapping `feast__fct_sensor_features_train` with a `dataset_split` column, no spine/labels needed since IsolationForest is unsupervised.

**Definition of Done** (this doc's addition): `manage.py up`'s training step produces a new `isolation_forest_model` version in `SNOWCOMOTIVE.CONS`, callable via `PREDICT`/`DECISION_FUNCTION` — met, see §5.

---

## 2. Agreed decisions

| Decision | Resolution |
|---|---|
| **Training API** | **Plain `sklearn.ensemble.IsolationForest`** on a `.to_pandas()` pull — not `snowflake.ml.modeling.ensemble.IsolationForest`'s distributed `.fit()` path as Module 5 originally drafted. See §4 for why; this was FR-FS-00's own explicitly-flagged "verify at build time" item, not a casual substitution. |
| **Stored procedure, not notebook** | Matches Module 5's own reasoning (consistency with FR-OPS-02a's scripted pipeline run) and the existing `scripts/05_train_models.sql` placeholder's `CALL snowcomotive.cons.sp_train_isolation_forest();` shape from Module 10. |
| **Owning role** | `snowcomotive_role`, not `ACCOUNTADMIN` — a stored procedure runs with owner's rights by default; created under the wrong role, it can't read tables it doesn't own. Found for real (see §4), fixed in both the procedure's creation and `manage.py`. |
| **Registry logging options** | `enable_explainability: True` (per Module 5/FR-FS-00c) **plus** `embed_local_ml_library: True` — the latter not in the original draft, required to avoid a package-resolution failure at `log_model` time (see §4). |
| **`volatility`** | No explicit override — `sklearn` is a directly-supported Model Registry built-in type (confirmed via `model_attributes.framework: "sklearn"`), which defaults to `IMMUTABLE` per FR-FS-00f's own stated rule ("custom models default to VOLATILE and all other models default to IMMUTABLE"). Not empirically re-verified via a runtime volatility query — flagged as a minor open item if stricter confirmation is wanted later. |
| **`$$...$$` stored-procedure bodies in `manage.py`** | `statements_from_sql_file()`'s naive `raw.split(";")` would fragment a Python procedure body on any internal semicolon. Fixed to track `$$...$$` blocks and only split outside them — a general fix, not specific to this one procedure (the future RUL training procedure needs the same). |

---

## 3. What was built

- `predictive_maintenance_dbt/models/feast/feast__training_dataset_iso.sql` — `table`, wraps `feast__fct_sensor_features_train` with a `dataset_split` column (Module 4 §4b, FR-FS-03).
- `scripts/05_train_models.sql` — `CREATE OR REPLACE PROCEDURE snowcomotive.cons.sp_train_isolation_forest()` (Python 3.11, `PACKAGES = ('snowflake-snowpark-python', 'snowflake-ml-python', 'pandas', 'scikit-learn')`) + `CALL`.
- `manage.py`:
  - `statements_from_sql_file()` made `$$`-block-aware.
  - `run_dbt()` split into `run_dbt_phase1()` / `run_dbt_phase2_and_test()`, with `05_train_models.sql` run between them (own `snowcomotive_role` connector session), matching FR-OPS-02a/02b's ordering.
  - `run_up()`: switches to `USE ROLE snowcomotive_role` immediately after `01_setup.sql`'s bootstrap, instead of staying on `ACCOUNTADMIN` for the whole session (see §4.3).

---

## 4. Deviations found during implementation (2026-08-30)

Mirroring the precedent in prior design docs' "Deviations" sections — real gaps between the Module 5 draft and what was actually needed, found while building and testing this story.

### 4.1 Distributed Snowpark ML training fails inside a stored procedure

| | |
|---|---|
| **Design doc said** (Module 5 §1) | `model = IsolationForest(...); model.fit(train_df, input_cols=sensor_feature_cols)` using `snowflake.ml.modeling.ensemble.IsolationForest`'s distributed trainer. |
| **Actually found** | `.fit()` internally spins up its own ephemeral nested stored procedure (`SNOWPARK_TEMP_PROCEDURE_*`) to run the actual training. That procedure does not inherit packages from the outer procedure's `PACKAGES` clause, nor from `session.add_packages(...)` called inside the handler — both tried, both failed identically: `ModuleNotFoundError: No module named 'pandas'` raised from inside the generated procedure. |
| **Fix** | Pull the training data to pandas (`train_df.select(sensor_feature_cols).to_pandas()`) and fit plain `sklearn.ensemble.IsolationForest` in-process — no nested stored procedure involved. Fine at this project's scale (1440 rows); revisit if EPIC-FULLDATA's volume makes an in-memory pull impractical. |
| **Registry impact** | None — scikit-learn is a directly-supported `Registry.log_model` built-in type; `PREDICT`/`DECISION_FUNCTION`/`SCORE_SAMPLES`/`EXPLAIN` are all still exposed (confirmed, see §5). |
| **Files** | `scripts/05_train_models.sql` |

### 4.2 `Registry.log_model` package resolution failure

| | |
|---|---|
| **Actually found** | Even after 4.1's fix, `log_model()` failed: `SQL compilation error: Cannot create a Python function with the specified packages... 'Packages not found: snowflake-ml-python[version='<2,>=1.53']'` — the model artifact's own serving-function packaging couldn't resolve a matching Anaconda-channel version for the exact installed `snowflake-ml-python` build (`1.53.0+f2d170cd352db029ad6936820cb3afd380f8df18`, a dev/patch build). |
| **Fix** | `options={"enable_explainability": True, "embed_local_ml_library": True}` — bundles the currently-running `snowflake-ml-python` into the model artifact instead of requiring a resolvable standalone Anaconda package. |
| **Files** | `scripts/05_train_models.sql` |

### 4.3 Stored procedure owner-rights vs. table ownership mismatch

| | |
|---|---|
| **Actually found** | Creating the procedure as `ACCOUNTADMIN` (the role `manage.py`'s `run_up()` had been using for its entire session since SH-29's bootstrap-deadlock fix) caused: `SQL access control error: Insufficient privileges to operate on table 'FEAST__TRAINING_DATASET_ISO'... owner role ACCOUNTADMIN must have SELECT granted`. A stored procedure runs with owner's rights by default; `feast.*` tables are owned by `snowcomotive_role` (dbt's own connection role), not `ACCOUNTADMIN`. This also meant `01_setup.sql`/`02_setup_raw_ddl.sql`/`03_setup_raw_load.sql` had been creating `RAW.*` tables under `ACCOUNTADMIN` ownership since SH-29's fix — an unintended side effect of that earlier bootstrap-deadlock fix, only surfaced now. |
| **Fix** | `manage.py`'s `run_up()` now switches to `USE ROLE snowcomotive_role` immediately after `01_setup.sql` completes (which needs `ACCOUNTADMIN` only to create the role that doesn't exist yet) — everything downstream, including the training procedure, now runs and owns objects consistently as `snowcomotive_role`. `ACCOUNTADMIN` is reserved for the two bootstrap operations that genuinely require it (creating the role in `01_setup.sql`, dropping it in `09_teardown.sql`). |
| **Files** | `manage.py` |

### 4.4 LLD's abstract table name vs. dbt's actual physical name

| | |
|---|---|
| **Actually found** | `session.table("feast.training_dataset_iso")` (matching Module 4 §4b's abstract naming) failed: `Object 'SNOWCOMOTIVE.FEAST.TRAINING_DATASET_ISO' does not exist`. dbt's actual table keeps the model file's name in full, including the schema prefix: `feast__training_dataset_iso`. Same class of bug as the `sensor_type` casing issue found in SH-29. |
| **Fix** | `session.table("snowcomotive.feast.feast__training_dataset_iso")` — fully qualified and using the real table name. |
| **Files** | `scripts/05_train_models.sql` |

### 4.5 `manage.py`'s naive `;`-splitter can't handle a stored-procedure body

| | |
|---|---|
| **Actually found** | `statements_from_sql_file()` blindly split on every `;` in the file — a Python stored procedure body wrapped in `$$...$$` would be fragmented at the first internal semicolon, corrupting the `CREATE PROCEDURE` statement. Not hit in practice (the handler code happened not to need one), but a real latent bug for the very first `$$...$$` block ever introduced into this codebase's script execution flow, and specifically for the next one (RUL training). |
| **Fix** | `statements_from_sql_file()` now tracks `$$...$$` block state and only splits on `;` outside of one. Verified directly: `manage.statements_from_sql_file(... / "05_train_models.sql")` returns exactly 2 statements (`CREATE PROCEDURE...`, `CALL...`), not fragments. |
| **Files** | `manage.py` |

### 4.6 Follow-up investigation (2026-08-31): exhaustive retry of the distributed training path

After 4.1's sklearn fallback was already working, a separate investigation retried `snowflake.ml.modeling.ensemble.IsolationForest`'s distributed `.fit()` path directly (outside `scripts/05_train_models.sql`, via an ad-hoc script) to see if 4.1's failure was configuration-specific rather than intrinsic. **Conclusion: it is intrinsic — the sklearn fallback in 4.1 remains the correct, final approach.**

| Variable isolated | Configurations tried |
|---|---|
| Package declaration mechanism | outer `PACKAGES` clause; `session.add_packages(...)` inside the handler; `custom_package_usage_config`; `enable_anonymous_sproc` |
| Runtime version | `3.11` (project default); `3.10` (both local `.python-version`/`requires-python` and stored-procedure `RUNTIME_VERSION` tried, reverted back to `3.11` — no effect either way) |
| Execution context | nested inside another stored procedure vs. top-level; inline `-c` string vs. `.py` script file |
| `snowflake-ml-python` version | `1.53.0` (project default) and an exact pin to `1.30.0` |

Every single configuration (10 total across both this and the original 4.1 investigation) failed identically:

```
File ".../snowflake/ml/modeling/_internal/snowpark_implementations/snowpark_trainer.py", line 14X, in fit_wrapper_function
ModuleNotFoundError: No module named 'pandas'
 in function SNOWPARK_TEMP_PROCEDURE_*
```

**Root cause**: `.fit()` on the distributed trainer internally calls `fit_wrapper_sproc(...)`, which dynamically creates its own ephemeral `SNOWPARK_TEMP_PROCEDURE_*` at runtime with an independently-resolved package list. That inner procedure's packages are not influenced by any outer-scope package declaration, runtime version, or execution context available to callers — a library-internal bug, not a configuration mistake on our end. Not worth further investigation; no code in this repo depends on the distributed path.

### 4.7 Registry does not auto-promote new versions to default

| | |
|---|---|
| **Actually found** (2026-08-31) | `SHOW VERSIONS IN MODEL isolation_forest_model` after 2 training runs showed `is_default_version = true` only on `V_20260830_114256` — the **first** version ever logged, not the latest (`V_20260830_114543` was `false`). Registry does not automatically re-point the default at whatever was logged most recently. This matters because S-MODEL-3's inference dynamic table calls the model with no explicit version (`MODEL(isolation_forest_model)!predict(...)`), which resolves to the default — so without a fix, inference would silently keep scoring off the very first trained version forever, regardless of how many times the model is retrained. |
| **Fix** | `registry.get_model("isolation_forest_model").default = version_name` added to `train()` immediately after `log_model()` — confirmed via the installed library source (`snowflake/ml/model/_client/model/model_impl.py`, `Model.default` is a settable property backed by `set_default_version`). Manually applied once to promote `V_20260830_114543` to default so the predict-SQL spike (§4.8) could be tested without a full retrain; the code fix makes this automatic for every future training run. |
| **Files** | `scripts/05_train_models.sql` |

### 4.8 Predict/decision_function SQL spike (ahead of S-MODEL-3)

Ran directly against `feast.feast__fct_sensor_features_inference` (already built by S-MODEL-1) before writing S-MODEL-3's actual dynamic table, to de-risk the SQL syntax:

```sql
SELECT
    f.equipment_id, f.reading_ts,
    isolation_forest_model!predict(<15 sensor_z cols>):"output_feature_0"::int AS predict_raw,
    isolation_forest_model!decision_function(<15 sensor_z cols>):"output_feature_0"::float AS decision_score
FROM feast.feast__fct_sensor_features_inference f
```

**Confirmed**: (a) omitting the version entirely (`MODEL_NAME!predict(...)`) resolves to the default version — identical output to the explicit `MODEL(isolation_forest_model, LAST)!predict(...)` form FRD's FR-FS-00b specified; (b) both `predict` and `decision_function` expose their single output as `output_feature_0`, extracted via `:"output_feature_0"`; (c) `predict` returns `INT64` (`-1`/`1`), `decision_function`/`score_samples` return `DOUBLE` — matches the model's own logged spec (`SHOW VERSIONS`' `model_spec` field). This resolves S-MODEL-3's §4 "exact return shape" open item — no longer a guess.

**Files**: none changed — pure ad-hoc SQL run and discarded, not committed anywhere.

---

## 5. Verification evidence (2026-08-30)

- Manual `CREATE`/`CALL` test (iterating through 4.1–4.4's fixes) → `"Trained isolation_forest_model V_20260830_114256 on 1440 rows"`.
- Re-ran through `manage.py`'s actual `run_sql_file()` function (not ad-hoc reimplementation) → produced a second version, `V_20260830_114543` — confirms the real orchestrator code path works, not just manual testing.
- `SHOW MODELS`/`SHOW VERSIONS`: `ISOLATION_FOREST_MODEL`, owner `SNOWCOMOTIVE_ROLE`, `model_attributes.framework: "sklearn"`, functions `["EXPLAIN","PREDICT","DECISION_FUNCTION","SCORE_SAMPLES"]` — confirms FR-FS-00b's previously-unverified `score_samples` exposure, and FR-FS-00c's explainability (`explainability.algorithm: shap` in the model spec).
- `manage.statements_from_sql_file()` unit-style check: 2 statements from `05_train_models.sql`, correctly un-fragmented.

### Verification evidence (2026-08-31 addendum)

- `is_default_version` bug (§4.7) confirmed and fixed; default version manually re-pointed to `V_20260830_114543` for testing.
- Predict/decision_function SQL spike (§4.8) succeeded against real data — both the no-version (default) and explicit `LAST` call forms return identical results.
- **Smoke test — not a true held-out evaluation** (see open items: `dataset_split='test'` has 0 rows right now, so there is no genuine test-set evaluation possible yet): scored all 1,440 rows of `feast__fct_sensor_features_inference` (single machine, `CNC_BORING` — the only equipment present in this thin dataset). Result: `n_anomalous=72`, `anomaly_rate=0.0500`, `decision_score` range `[-0.1596, 0.1420]`, `avg_score=0.0753`. The `0.0500` anomaly rate lands exactly on the `contamination=0.05` value the model was trained with — expected/consistent behavior (contamination sets the decision threshold), not independent confirmation of real-world accuracy.

---

## 6. Open items

- `volatility=IMMUTABLE` inferred from `model_attributes.framework: "sklearn"` (a non-custom, directly-supported type) per FR-FS-00f's documented default rule — not independently re-confirmed via a runtime volatility query. Worth a quick check before S-MODEL-3 relies on it for incremental refresh.
- RUL's training procedure (EPIC-RUL) will need the same `embed_local_ml_library`/ownership-role treatment, plus its own `volatility=IMMUTABLE` override since it's logged via the raw `xgboost.Booster` (custom) path — FR-FS-00f already anticipates this explicitly.
- S-MODEL-3 (`cons.fct_anomaly_result`) is the next story — needs its own incremental-refresh verification (same `insertedRows`/`copiedRows` check used for `feast` in SH-29), not assumed to inherit from this story.
- **No genuine test-set evaluation exists yet** — `feast__training_dataset_iso.dataset_split` currently has 0 `'test'` rows (all 1,440 rows are `'train'`); the time-based cutoff (FR-FS-03) hasn't been crossed by wall-clock time yet in this dev environment. Until it has, "testing" this model can only mean smoke-testing predict/decision_function on data it was trained on (see §5 addendum) — not a real held-out accuracy check. Revisit once real time (or a backdated test fixture) produces test rows.
