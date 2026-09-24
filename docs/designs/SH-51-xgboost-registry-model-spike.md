# Design: SH-51 — Spike: raw xgboost.Booster via Model Registry, called via MODEL(...)!predict() in SQL (S-RUL-0)

Status: **Frozen** — brainstormed interactively with the user, ready for `Developer-agent`.
Branch: `feature/SH-4-51-xgboost-registry-model-sql-spike`
Epic: EPIC-RUL (SH-4?) | Story: SH-51 (S-RUL-0)
Traces to: `docs/05-Epics.md` §6 (EPIC-RUL, S-RUL-0), `docs/03-HLD.md`, `docs/04-1-LLD.md` (CONS section, `cons.fct_rul_prediction`), `docs/04-4-LLD.md` §4a/§5, `docs/04-5-LLD.md` §2/§4 (RUL AFT model spec + explainability validation plan), `docs/designs/5 - SH-23-inference-dynamic-table.md` (precedent for verifying `MODEL(...)!predict()`/incremental-refresh empirically rather than assuming)

---

## 1. Problem & scope

Per the epic's own framing (`docs/05-Epics.md` §6):

> Toy model, toy data — confirm the whole "no stored procedure for RUL inference" design actually works before building the real pipeline around it. If it fails, fall back to procedure-based RUL inference and update HLD/Module 1/4/5 accordingly.

This is the riskiest unknown blocking S-RUL-1 through S-RUL-7: the entire architecture assumes a raw `xgboost.Booster` (trained standalone via plain `xgb.train()`, **not** wrapped in a Snowpark stored procedure and **not** Snowpark ML's high-level `XGBRegressor`) can be logged to the Model Registry via its generic/custom-model path and then called directly with `MODEL(<ref>)!predict(...)` inside a SQL `SELECT` — matching how `isolation_forest_model` is already called in `cons.fct_anomaly_result` (SH-23), but for a model type that was never itself trained through `Registry.log_model`'s high-level sklearn-native path.

**Out of scope** (explicitly, per the epic and this brainstorm):
- Building any part of the real RUL pipeline (`FEAST.SPINE_MAINTENANCE_CYCLE`, `TRAINING_DATASET_RUL`, `cons.fct_rul_prediction`) — that's S-RUL-1 through S-RUL-5.
- Real sensor features, real censoring labels from `cons.fct_maintenance_event` — toy/synthetic data only.
- Testing this inside a dynamic table or any dbt model — pure ad hoc SQL against a scratch Model Registry entry.
- Deciding *where* to document a failure (HLD/Module 1/4/5 edits) — deferred to a follow-up action once the spike's actual pass/fail result is known (see §6).

---

## 2. Success criteria (what PASS means)

Both of the following must hold, run directly against the live Snowflake account (not assumed from docs/precedent):

1. **`predict()` plumbing**: a raw `xgboost.Booster` (trained via plain `xgb.train()`, `objective='survival:aft'`) logs successfully to the Model Registry via the generic/custom-model path, and `MODEL(<registry_ref>)!predict(<col>, <col>, ...)` — called directly inside a SQL `SELECT`, no stored procedure — returns numerically correct output (verified by comparing SQL output against the same Booster's in-Python `.predict()` call on identical rows).
2. **`volatility=IMMUTABLE` override actually holds**: per Module 5 §2's explicit note ("NOT the default for custom models — required for incremental dynamic-table refresh, FR-FS-09, to work at inference time"), confirm the `Volatility.IMMUTABLE` option passed to `registry.log_model(...)` is actually reflected on the logged version (e.g. via `SHOW VERSIONS` or equivalent metadata) — not just assumed to have been accepted silently.

**Stretch (in scope per this brainstorm, not required for PASS)**: `MODEL(<registry_ref>)!explain(...)` is also called against the toy model, answering Module 5 §4 validation-plan item 2's own flagged open question ("first confirm explain is even callable for a model logged via the raw/generic Python path") — a genuine unknown this spike can resolve now instead of leaving open until S-RUL-3/S-STRETCH-1. A failure here does **not** fail the spike overall (Module 5 §4 already has its own fallback: expose raw z-scores instead, drop `explain_prediction` from scope) — it's recorded as its own finding.

If either of the two required checks fails, the spike is a **FAIL** and the epic's stated fallback applies: procedure-based RUL inference, with HLD/Module 1/4/5 updated accordingly (see §6 for where that update happens).

---

## 3. Toy model & data

**Objective**: `survival:aft`, matching Module 5 §2's real params as closely as a toy dataset allows — this is a deliberate fidelity choice (not the simpler "plain regression first" option) since the whole point is de-risking the actual shape S-RUL-3 will use, not just generic Booster-logging plumbing.

**Synthetic data** (a handful of rows — no real sensor/generator data, no dependency on any existing table):
- 2–3 numeric feature columns (e.g. `feature_1`, `feature_2`) — arbitrary synthetic values, not tied to real sensor z-scores.
- `y_lower` / `y_upper` columns matching Module 5 §2's censoring convention: **some rows uncensored** (`y_upper = y_lower`, a known failure time) and **some rows right-censored** (`y_upper = NULL` → `+inf` via `.fillna(np.inf)`, matching FR-DG-11's convention) — exercising the real `dtrain.set_float_info("label_lower_bound", ...)` / `set_float_info("label_upper_bound", ...)` calls, not just the objective string.
- Same params as Module 5 §2: `aft_loss_distribution='normal'`, `aft_loss_distribution_scale=1.0`, `tree_method='hist'`, shallow `max_depth` appropriate for a toy dataset (e.g. 2–3 given only a handful of rows), small `num_boost_round`.

```python
import numpy as np
import pandas as pd
import xgboost as xgb

toy_df = pd.DataFrame({
    "feature_1": [0.1, 0.5, -0.3, 1.2, -0.8, 0.4, 0.9, -0.1],
    "feature_2": [1.0, 0.2, 0.7, -0.4, 0.3, -0.9, 0.1, 0.6],
    "y_lower":   [100,  50, 200,  30, 150,  80, 120,  60],
    "y_upper":   [100, np.nan, 200, np.nan, 150, np.nan, 120, np.nan],  # alternating censored/uncensored
})
feature_cols = ["feature_1", "feature_2"]

dtrain = xgb.DMatrix(toy_df[feature_cols])
dtrain.set_float_info("label_lower_bound", toy_df["y_lower"].values)
dtrain.set_float_info("label_upper_bound", toy_df["y_upper"].fillna(np.inf).values)

params = {
    "objective": "survival:aft",
    "aft_loss_distribution": "normal",
    "aft_loss_distribution_scale": 1.0,
    "tree_method": "hist",
    "max_depth": 2,
}
booster = xgb.train(params, dtrain, num_boost_round=20)
```

**Trained standalone** — plain Python, no Snowpark stored procedure wrapper (this is the exact condition the epic's own framing calls out: "confirm the whole 'no stored procedure for RUL inference' design actually works"). Can run in a notebook cell or ad hoc Python session against the `snow-co-cat-alyst` connection.

---

## 4. Where it runs — pure scratch, nothing tracked

Per the user's explicit call: **entirely in a Python cell/notebook against the existing `SNOWCOMOTIVE` database**, with a throwaway model name (e.g. `rul_spike_toy_model`), queried via a plain ad hoc `SELECT` (not a dynamic table, not any dbt model). No new schema/stage needed — reuse whatever schema `isolation_forest_model` is already logged into (confirm live via `SHOW MODELS` before choosing, don't assume).

Nothing in `predictive_maintenance_dbt/` is touched by this spike. No production schema/table is read or written. Cleanup: `DROP MODEL rul_spike_toy_model` (or leave it, low cost) once the finding is recorded — teardown is not a blocking concern for a scratch spike.

```python
from snowflake.ml.registry import Registry
from snowflake.ml.model import Volatility

registry = Registry(session=session)  # same schema isolation_forest_model already lives in — confirm via SHOW MODELS first

mv = registry.log_model(
    booster,
    model_name="rul_spike_toy_model",
    version_name="v1",
    sample_input_data=toy_df[feature_cols],
    options={
        "enable_explainability": True,   # for the stretch !explain check, §2
        "volatility": Volatility.IMMUTABLE,
    },
)
```

```sql
-- Predict check (§2.1) — compare against booster.predict(dtrain) in Python for the same rows
SELECT
    feature_1, feature_2,
    rul_spike_toy_model!predict(feature_1, feature_2):"output_feature_0"::float AS predicted_time
FROM <toy_rows_as_a_temp_table_or_VALUES_clause>;

-- Volatility check (§2.2)
SHOW VERSIONS IN MODEL rul_spike_toy_model;
-- inspect returned metadata for the IMMUTABLE volatility setting

-- Stretch: explain check
SELECT feature_1, feature_2,
    e.*
FROM <toy_rows> t,
     TABLE(rul_spike_toy_model!explain(t.feature_1, t.feature_2)) e;
```

Exact `SHOW VERSIONS`/metadata field name for volatility, and exact `!predict`/`!explain` return-column shape (`output_feature_0` vs. something else for a custom Booster path, as opposed to the sklearn-native path SH-23 already confirmed) are **not** assumed — `Developer-agent` confirms both live, same discipline SH-23 used for `isolation_forest_model` (§4.1 of that design doc).

---

## 5. What "correct numeric output" means, precisely

Not just "SQL returns a number without erroring" — compare the SQL-returned `predicted_time` value for each toy row against `booster.predict(dtrain)` computed in the same Python session that trained the model, for the identical feature values. A match (within floating-point tolerance) is required for PASS §2.1; a SQL call that returns *some* number but doesn't match the Python-side prediction is a partial failure worth recording distinctly (e.g. wrong column extracted, wrong version served) rather than silently counted as PASS.

---

## 6. Fallback / documentation — explicitly deferred

Per the user's explicit direction in this brainstorm: **do not decide now** where a FAIL result gets written up (whether that means editing `03-HLD.md`/`04-1-LLD.md`/`04-4-LLD.md`/`04-5-LLD.md` directly, in this same pass or a separate follow-up story). Rationale offered: the precedent from `isolation_forest_model` (a custom-registered model that already works end-to-end, including `!explain` via SHAP per SH-27-28-30-31-32-33's §12.3) makes FAIL less likely than the epic text alone might suggest — so this is deferred until the spike's actual result is in hand, rather than pre-committing to a documentation plan for a scenario that may not occur.

**Action item for `Developer-agent`/whoever runs this spike**: record the PASS/FAIL result (and the `!explain` stretch finding) directly in this design doc's §7 below once run. If FAIL, flag it back to the user before touching any HLD/LLD file — this doc's job stops at "here's what was found," not "here's what I changed in the architecture docs."

---

## 7. Results (run 2026-09-24, `Developer-agent`, account `IQWYCFG-OAC98123` / connection `snow-co-cat-alyst`)

Executed via `scripts/spikes/sh51_xgboost_registry_spike.py`. Full captured output: see the log this script produces when re-run (not itself committed — the script is the tracked artifact; re-running reproduces the evidence below).

- [x] **§2.1 `predict()` plumbing: PASS.** Raw `xgboost.Booster` (plain `xgb.train()`, `objective='survival:aft'`) logged via `registry.log_model(booster, ...)` — routed through `snowflake-ml-python`'s dedicated `XGBModelHandler` (confirmed by inspecting the installed package's `model_handlers/xgboost.py`: first-class support for raw `xgboost.Booster`/`xgboost.XGBModel` types), **not** the fully generic `custom_model.CustomModel` path as originally worded here. The exported `model.yaml` shows `model_type: xgboost` / `xgb_estimator_type: Booster`, confirming no sklearn-native wrapper was invoked. `MODEL(...)!predict(feature_1, feature_2)` called directly inside a SQL `SELECT` (no stored procedure) returned, for all 8 toy rows, values matching `booster.predict(dtrain)` computed in the same Python session within float32 tolerance:
  - `(-0.8, 0.3)` → SQL `161.5246276855469` vs Python `161.52463` ✓
  - `(-0.3, 0.7)` → SQL `194.2801971435547` vs Python `194.2802` ✓
  - `(-0.1, 0.6)` → SQL `272.0911865234375` vs Python `272.0912` ✓
  - `(0.1, 1.0)` → SQL `110.7873382568359` vs Python `110.78734` ✓
  - `(0.4, -0.9)` → SQL `272.0911865234375` vs Python `272.0912` ✓
  - `(0.5, 0.2)` → SQL `272.0911865234375` vs Python `272.0912` ✓
  - `(0.9, 0.1)` → SQL `145.6965942382812` vs Python `145.6966` ✓
  - `(1.2, -0.4)` → SQL `145.6965942382812` vs Python `145.6966` ✓
  - Return-column shape for the raw-Booster path: `predict()` returns an `OBJECT` with a single key `output_feature_0` (accessed as `RUL_SPIKE_TOY_MODEL!predict(feature_1, feature_2):"output_feature_0"::float`) — same shape SH-23 confirmed for the sklearn-native `isolation_forest_model` path.

- [x] **§2.2 `volatility=IMMUTABLE` holds: PASS, but not via the docs-assumed mechanism.** `SHOW VERSIONS IN MODEL` and `DESC MODEL` do **not** expose a volatility column at all (confirmed empirically — full column list checked, neither surfaces it). The only place it's actually recorded is the deployed `MANIFEST.yml`, retrievable via `ModelVersion.export(export_mode=ExportMode.FULL)` (the default `ExportMode.MODEL` only exports the packager-level `model.yaml`, which also has no volatility field — `method_options: {}` — and is the wrong artifact to check). The exported `MANIFEST.yml` explicitly shows `volatility: IMMUTABLE` on **both** the `PREDICT` and `EXPLAIN` method entries, confirming the override passed at `log_model(..., options={"volatility": Volatility.IMMUTABLE})` time was honored server-side. (This also depends on the account having the `SET_MODULE_FUNCTIONS_VOLATILITY_FROM_MANIFEST` platform capability enabled — confirmed `True` on this account via `PlatformCapabilities.get_instance(session).is_set_module_functions_volatility_from_manifest()`; on an account where that capability is off, the client silently drops the volatility override without erroring, which would be a real gotcha worth flagging in Module 5 §2 if the real pipeline's target account doesn't have it enabled.)

- [x] **Stretch: `!explain` callable on raw-Booster path: PASS, with a calling-convention caveat.** `enable_explainability: True` at `log_model` time works for a raw Booster (SHAP background-data artifact generated: `RUL_SPIKE_TOY_MODEL_background_data.pqt`). `MODEL(...)!explain(...)` as a lateral `TABLE(...)` function call **fails** when passed bare numeric literals (`t.feature_1, t.feature_2` from a `VALUES` clause resolve to `NUMBER(2,1)`, and `explain`'s compiled signature requires `DOUBLE` inputs exactly — no implicit cast, unlike `predict()` which does accept `NUMBER` args). Adding an explicit `::DOUBLE` cast (`t.feature_1::DOUBLE, t.feature_2::DOUBLE`) resolves it cleanly; output columns are `feature_1_explanation` / `feature_2_explanation` (per-feature SHAP values), returned successfully for all 8 rows. This resolves Module 5 §4's open question: raw-Booster `explain()` **is** callable via the same `TABLE(...)` lateral convention SH-23 already used — no fallback to raw z-scores needed on this finding alone (explicit `::DOUBLE` casts should just be baked into the real S-RUL-3 inference query/dynamic table from the start).

- [x] **Overall spike verdict: PASS.** Both required checks (§2.1, §2.2) pass; the stretch check also passes. The "no stored procedure for RUL inference" architecture is confirmed viable for a raw AFT `xgboost.Booster`. No fallback to procedure-based RUL inference is needed; no HLD/LLD edit is triggered by this result (§6's escalation path is N/A since this is not a FAIL).

- [x] **Reviewer-agent verification: PASS, independently reproduced.** Reviewer-agent independently retrained the Booster from scratch, hit the live `RUL_SPIKE_TOY_MODEL` directly (not relying on the script's captured output), and reproduced the `!predict` values, the `MANIFEST.yml` volatility finding, and the `!explain` cast-requirement error byte-for-byte / field-for-field / error-for-error against this section's claims. No bugs found.

**Deviations from the design doc's plan** (both are additive — confirming things empirically rather than assuming, consistent with the doc's own stated discipline in §4/§5):
1. Volatility confirmation required `ModelVersion.export(export_mode=ExportMode.FULL)` + reading `MANIFEST.yml`, not `SHOW VERSIONS`/`DESC MODEL` as the design doc's SQL sketch implied — those commands don't carry a volatility field on this server version.
2. The `!explain()` SQL sketch in §4 needed explicit `::DOUBLE` casts on the input columns to compile — omitted from the design doc's sketch, added here as a documented calling-convention note for S-RUL-3.

---

## 8. Files touched by this spike

Per §4, the plan was a pure ad hoc Python cell/notebook with nothing tracked. In practice, the following **are** left in the working tree (uncommitted, for `Reviewer-agent`/`Documenter-agent` to see):

- `scripts/spikes/sh51_xgboost_registry_spike.py` (new) — the actual spike script run to produce §7's results; kept as a reproducible artifact rather than a throwaway ad hoc cell, since it doubles as the evidence trail for the PASS verdict. Not part of the numbered `scripts/0N_*.sql` lifecycle sequence (per `scripts/README.md` reserving that numbering for SQL) — lives in its own `scripts/spikes/` subdirectory instead.
- `pyproject.toml` / `uv.lock` (modified) — added `xgboost` and `snowflake-ml-python` as project dependencies via `uv add` (neither was previously installed; required to train the toy Booster and call `Registry.log_model`).
- `docs/designs/SH-51-xgboost-registry-model-spike.md` (this file, modified) — §7 results filled in.
- No `predictive_maintenance_dbt/` changes. No `scripts/0N_*.sql` changes. No production `RAW`/`STD`/`CONS`/`FEAST` table touched.
- One throwaway Model Registry object, `SNOWCOMOTIVE.CONS.RUL_SPIKE_TOY_MODEL` (version `V1`), left live in the account per §4's "leave it, low cost" allowance — clearly named as a spike artifact.

**Action item**: drop `SNOWCOMOTIVE.CONS.RUL_SPIKE_TOY_MODEL` before any demo pass that surfaces `CONS` schema contents through the semantic view/agent chat — not urgent, just a flagged cleanup item.
