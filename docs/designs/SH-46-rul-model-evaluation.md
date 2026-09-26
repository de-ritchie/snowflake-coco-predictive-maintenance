# Design: SH-46 — Evaluation (S-RUL-4, extended to include IsolationForest evaluation)

Status: **Both halves fully reconciled.** **RUL section (§1–§15): Reviewer-agent PASS (2026-09-25) — zero code bugs. Documentation reconciled (2026-09-26); see §15 for the LLD deviation this required.** **IsolationForest section (§16–§23): Reviewer-agent PASS (2026-09-26) — zero code bugs, every claim independently re-verified live. Documentation reconciled (2026-09-26); see §23 for results and the small-sample caveat that applies to them.**
Branch: `feature/SH-4-46-evaluation`
Epic: EPIC-RUL | Story: SH-46 (S-RUL-4: "Evaluation")
Traces to: [docs/04-5-LLD.md](../04-5-LLD.md) §1 (IsolationForest spec), §3 (Evaluation, FR-FS-05), §4 (explainability validation plan — precedent for this project's "qualitative validation is acceptable, document actual results" convention), `docs/designs/SH-44-train-rul-aft-model.md` (trained `rul_aft_model`, live test-split composition, flagged outlier), `docs/05-Epics.md` EPIC-RUL / S-RUL-4

**Follows**: SH-44 (merged, PASS — `rul_aft_model` trained on `FEAST.TRAINING_DATASET_RUL WHERE dataset_split='train'`, 94 rows/52 censored/42 uncensored, `volatility=IMMUTABLE` confirmed). §16 onward also follows SH-22 (merged — `isolation_forest_model` trained on `FEAST.TRAINING_DATASET_ISO WHERE dataset_split='train'`, S-MODEL-2).

---

## 1. Scope

Evaluate the already-trained `rul_aft_model` against `FEAST.TRAINING_DATASET_RUL WHERE dataset_split = 'test'` (confirmed live composition: 19 rows, 14 censored / 5 uncensored — SH-44 design doc). Compute the concordance index (primary metric, full test set including censored rows) and MAE/RMSE (uncensored subset only), per FR-FS-05. Produce a per-row prediction/residual breakdown for diagnosability. Attempt Model Registry metric logging via `mv.set_metric(...)`, verifying the API empirically. Perform a lightweight root-cause check on the outlier row flagged in SH-44 §12 (`CNC_MILLING`, predicted ~16.6x actual). End with a clear recommendation on whether hyperparameter tuning is warranted.

**Not in scope**: retraining or hyperparameter tuning itself (deferred — this story's conclusion informs that decision, doesn't execute it). `cons.fct_rul_prediction` (live inference dynamic table) — separate future story, not touched here; this story reads/writes nothing that persists beyond a stored procedure's return value. Persisting the per-row evaluation breakdown to a table — explicitly decided against (§7).

**Scope extension (2026-09-26)**: §16 onward adds an analogous evaluation step for `isolation_forest_model` (the anomaly detection model), logged as Model Registry metrics the same way. This is a deliberate, user-confirmed deviation from this story's original single-model scope — see §16 for the rationale. The RUL evaluation above is unaffected by this extension; §1–§15 describe exactly what was built and reviewed for `rul_aft_model` and remain the source of truth for that model.

---

## 2. Where the evaluation code lives

**New file: `scripts/06b_evaluate_rul_model.sql`**. Naming rationale: every numeric slot 01–09 is already claimed by an existing script (including the historical `08_demo.sql` stub and `06_pipeline_run_phase2.sql`, itself already mislabeled "07" in `scripts/README.md`'s run-order table — a pre-existing inconsistency, not a new one this story introduces). `06b` mirrors `manage.py`'s own in-comment "7a"/"7b" step-labeling already used for the RUL-training insertion point, and correctly signals this step's actual pipeline position: it must run in its own connector session strictly after `06_train_rul_model.sql` (needs the trained model to exist) and strictly before the phase-2 dbt run (evaluation has no dependency on inference-tagged models, so it doesn't need to happen after them, and the LLD's "logged as Model Registry metrics" framing suggests this belongs in the training/registry-adjacent part of the pipeline, not bundled into `07_post_setup.sql`'s semantic-view/agent/Streamlit concerns).

**`manage.py` change**: insert a new connector-session block calling `06b_evaluate_rul_model.sql` immediately after the existing `06_train_rul_model.sql` block (same `snowcomotive_role` session pattern) and before `run_dbt_phase2_and_test()`. Update the module docstring's numbered `up:` sequence (currently ends step 7b at `06_train_rul_model.sql`) to add step **7c**. Update `scripts/README.md`'s run-order table and prose to include the new file and its position.

Follows `06_train_rul_model.sql`'s existing pattern: `CREATE OR REPLACE PROCEDURE` + `CALL`, one Python stored procedure, own connector session in `manage.py`.

---

## 3. Test-set read & feature columns

Reads `FEAST.TRAINING_DATASET_RUL WHERE dataset_split = 'test'` — same table, same ~22 feature columns, same `y_lower`/`y_upper` labels as `06_train_rul_model.sql`'s training read (`docs/04-5-LLD.md` §2, `06_train_rul_model.sql`'s `feature_cols` list). The evaluation script's `feature_cols` list must be copied verbatim from `06_train_rul_model.sql` — any drift between the two lists (e.g. one script picking up a column the other doesn't) would silently break `DMatrix` column alignment. Same boolean-to-numeric cast (`is_anomaly`, `any_anomaly_flagged_72h`) applies on the test read, for the same reason.

No `dataset_split = 'train'` row is ever read by this script.

The model is loaded via the Registry's default version, not re-trained or re-logged:

```python
from snowflake.ml.registry import Registry

registry = Registry(session=session, database_name="SNOWCOMOTIVE", schema_name="CONS")
mv = registry.get_model("rul_aft_model").default
```

---

## 4. Concordance index — primary metric, full test set (14 censored + 5 uncensored)

**Dependency decision**: try `scikit-survival` (`from sksurv.metrics import concordance_index_censored`, per LLD §3's exact code sketch) first. Add `'scikit-survival'` to the stored procedure's `PACKAGES` clause. `Developer-agent` must verify empirically at build time whether Snowflake's Anaconda channel actually resolves this package for a Python stored procedure — same discipline as SH-51's `volatility` verification and SH-44's `MANIFEST.yml` re-check: don't assume it's available just because the LLD sketch imports it. **If it fails to resolve** (package not found, version mismatch, or any resolution error), fall back to a manual pairwise concordance-index implementation (§4b) rather than substituting a different library ad hoc — record whichever path was actually taken in this doc's results section once run.

**Mechanics — how the censored pairs are actually handled** (walked through explicitly per the story's own request, not handwaved):

Concordance index compares every *comparable* pair of subjects and checks whether the model ranked them in the correct relative order. A pair `(i, j)` is comparable under right-censoring rules if:
- Both are uncensored (both actual failure times known) — always comparable.
- One is uncensored with `time_i < time_j`, and `j` is censored — comparable (we know `i` failed before `j`'s last known survival point, regardless of when `j` eventually fails). The reverse (comparing an uncensored point against a *later*-censored point where the censored one's `time_j < time_i`) is **not** comparable — a censored observation's recorded time is a lower bound on its true failure time, not the true failure time itself, so it cannot be safely ordered against an earlier uncensored failure.
- Both censored — never comparable (neither true failure time is known, so relative order is undecidable).

`sksurv.metrics.concordance_index_censored` implements exactly this comparability rule internally — it is not a naive "treat censored time as if it were the true failure time" computation. The call signature per LLD §3:

```python
c_index = concordance_index_censored(
    event_observed=test_pdf["y_upper"].notna().values,   # True = uncensored (failure observed)
    event_time=test_pdf["y_lower"].values,                # for censored rows, y_lower is the last-known-survival lower bound; for uncensored rows, y_lower == y_upper == the actual failure time
    estimate=-pred_hours,   # higher predicted RUL = lower risk -> negate for c-index's risk-score convention
)[0]
```

This is the correct application: `event_time` uses `y_lower` uniformly (it equals the true failure time when uncensored, and the last-known-survival bound when censored — exactly what the comparability rule above needs), and `event_observed` flags which rows get the "known true failure time" treatment vs. the "lower-bound-only" treatment. **This is the one part of this story most likely to be subtly wrong if re-implemented casually** — Reviewer-agent should specifically check that no code path treats a censored row's `y_lower` as if it were an exact failure time in any metric *other* than the concordance index (MAE/RMSE explicitly exclude censored rows entirely, per §5).

### 4b. Manual fallback (only if scikit-survival is unavailable)

Pairwise loop over all `(i, j)` test-set index pairs, applying the exact comparability rule above, incrementing concordant/discordant/tied counters based on whether `estimate` and `event_time` agree in ordering for each comparable pair, then `c_index = concordant / (concordant + discordant + 0.5*tied)`. Given the test set is only 19 rows (171 pairs), an O(n²) Python loop is trivially fast — no vectorization needed. `Developer-agent` should write this as a small local helper function inside the stored procedure body if triggered, not a separate importable module (matches this project's existing convention of self-contained stored-procedure bodies).

---

## 5. MAE / RMSE — uncensored subset only (5 rows)

Per FR-FS-05 exactly: computed only on the 5 uncensored test rows (`y_upper.notna()`), since there's no true failure time to compare against for a censored row.

```python
uncensored_mask = test_pdf["y_upper"].notna()
mae = mean_absolute_error(test_pdf.loc[uncensored_mask, "y_lower"], pred_hours[uncensored_mask])
rmse = mean_squared_error(test_pdf.loc[uncensored_mask, "y_lower"], pred_hours[uncensored_mask]) ** 0.5
median_ae = median_absolute_error(test_pdf.loc[uncensored_mask, "y_lower"], pred_hours[uncensored_mask])
```

**`median_ae` added beyond the LLD's literal MAE/RMSE ask**: with only 5 uncensored rows, one outlier (the already-flagged `CNC_MILLING` row) can dominate both MAE and RMSE. Median absolute error is robust to a single outlier and gives a fuller picture of "how good is the model on the rows it's not badly wrong on" — report both, don't let MAE alone stand in for "typical" performance on a 5-row sample.

`sklearn.metrics.mean_absolute_error`, `mean_squared_error`, `median_absolute_error` — already available (`scikit-learn` is an existing `pyproject.toml` dependency and an existing `PACKAGES` entry pattern in `05_train_models.sql`).

---

## 6. Per-row breakdown (diagnosability)

For all 19 test rows, compute and include in the stored procedure's return value:

| equipment_id | cycle_end_ts | y_lower | y_upper | censored | predicted_hours | abs_error (uncensored only) |
|---|---|---|---|---|---|---|

This makes the outlier's contribution to MAE/RMSE directly visible without needing a separate query — the return string should be a formatted multi-line table (matching `06_train_rul_model.sql`'s convention of a single descriptive return string, just longer here). Not persisted to a table (§7).

---

## 7. Persistence decision (explicit, per user confirmation)

The per-row breakdown and aggregate metrics are **return-value/log output only** — not written to any table. Rationale confirmed with the user: this is a one-shot diagnostic snapshot tied to a single evaluation run against a static, already-fixed 19-row test split — nothing downstream needs to query it repeatedly or continuously. This is a different concern from **live inference** (`cons.fct_rul_prediction`, predicting RUL for real, currently-running equipment), which genuinely does need to be a persisted, queryable table for Streamlit/agent consumption — but that table is already explicitly scoped to a separate future story (SH-44 §11, LLD §2) and is *not* pulled into this story's scope.

---

## 8. Outlier root-cause check (lightweight, per user decision)

In addition to metrics, the script pulls the `CNC_MILLING` outlier row's feature values (the row with `y_lower=y_upper=566.00`, predicted ~9,396h per SH-44 §12) and compares them against the **train split's** per-feature summary statistics (min/max/mean/percentile via `train_pdf[feature_cols].describe()` or equivalent) as a quick sanity check for "is this row genuinely out-of-distribution relative to what the model was trained on." This is a diagnostic printed alongside the per-row breakdown, not a separate persisted artifact, and does **not** involve retraining, feature engineering, or any code change to the model itself — purely descriptive comparison.

Expected output shape: for each feature, report the outlier row's value alongside train-split `[min, p25, median, p75, max]`, flagging any feature where the outlier sits outside `[min, max]` of the train distribution (genuinely never-seen territory) vs. merely near an edge (within range but unusual). This distinction matters for the final recommendation (§10): a feature value the model never saw in training is a data/coverage gap; a feature value within range but rare is more likely a "small dataset, needs more examples of this regime" issue — different implications for whether tuning vs. more data collection is the right next step.

---

## 9. Model Registry metric logging

Per LLD §3 ("Both metrics get logged as Model Registry metrics on the version"): attempt `mv.set_metric(name, value)` for each computed metric (`concordance_index`, `mae`, `rmse`, `median_ae`) on the loaded `ModelVersion` object. **Verify empirically** that this API exists and succeeds on this account's installed `snowflake-ml-python` version — same discipline as SH-44 §8's volatility re-verification and this story's own §4 scikit-survival check: don't assume it works because the LLD says so. If `set_metric` doesn't exist or errors, note the actual finding in this doc's results section and fall back to metrics appearing only in the stored procedure's return value (still satisfies this story's own diagnostic purpose; the Streamlit Impact Statement view's future consumption of registry-logged metrics, FR-CC-07, is a downstream concern for whichever story builds that view — not blocked by this fallback, just means that future story needs its own plan if `set_metric` genuinely isn't available on this account).

---

## 10. Recommendation section (required doc content, not optional)

The stored procedure's return value — and this design doc's own eventual "Results" section once `Developer-agent`/`Reviewer-agent` actually run it — must end with an explicit recommendation, not just raw numbers: given the actual computed concordance index, MAE/RMSE/median-AE, and the outlier root-cause finding (§8), state plainly whether hyperparameter tuning (deferred per SH-44 §11) now looks warranted, or whether the numbers are acceptable for MVP scope as-is. This is the natural conclusion the user's own prior "wait for real evaluation numbers first" decision was pointing toward — don't let the story silently stop at "here are the numbers" with no verdict.

---

## 11. Files to create/modify (Developer-agent's checklist — no code written here)

**New**:
- `scripts/06b_evaluate_rul_model.sql` — the evaluation stored procedure + `CALL`, per §2–§10 above.
- `scripts/06c_evaluate_iso_model.sql` — the IsolationForest evaluation stored procedure + `CALL`, per §16–§22 below.

**Modified**:
- `manage.py` — insert step 7c (new connector-session block calling `06b_evaluate_rul_model.sql`) between the existing `06_train_rul_model.sql` block and `run_dbt_phase2_and_test()`; insert a new step (per §17's placement decision) calling `06c_evaluate_iso_model.sql` immediately after the existing `05_train_models.sql` block, i.e. *before* `run_dbt_training_dataset_rul()`, since `isolation_forest_model` already exists at that point and this new step has no dependency on `feast.training_dataset_rul`, `rul_aft_model`, or `cons.fct_anomaly_result`; update the module docstring's `up:` sequence accordingly (both the RUL-eval 7c insertion and the new ISO-eval insertion).
- `scripts/README.md` — add both new files to the run-order table and prose.
- `pyproject.toml` / `uv.lock` — add `scikit-survival` as a project dependency if the empirical PACKAGES check (§4) succeeds; if it doesn't, no dependency added and §4b's manual implementation is used instead (record which path was taken).

No dbt model changes for either evaluation script — `FEAST.TRAINING_DATASET_RUL` and `FEAST.TRAINING_DATASET_ISO` are read-only inputs, same as SH-44's read pattern. `RAW.CMMS_LOG` is also read-only for the IsolationForest evaluation (§18).

---

## 12. Invariants for Reviewer-agent

1. Evaluation reads `dataset_split == 'test'` only — no `'train'` row ever enters the concordance index, MAE, RMSE, or median-AE computation.
2. `feature_cols` in the evaluation script must match `06_train_rul_model.sql`'s list exactly (same ~22 columns, same boolean-to-numeric casts) — verify no drift between the two scripts.
3. Concordance index must be computed on the **full** test set (all 19 rows, both censored and uncensored) — verify no code path filters to uncensored-only before this computation (that would defeat the metric's whole purpose per LLD §3).
4. MAE/RMSE/median-AE must be computed on the **uncensored subset only** (5 rows) — verify no code path includes a censored row's `y_lower` as if it were a true failure time in these three metrics specifically (§4's comparability-rule discussion is only valid for concordance index, not these).
5. The `event_observed`/`event_time`/`estimate` arguments to `concordance_index_censored` (or the manual fallback's equivalent logic) must follow §4's exact convention — `event_time` always uses `y_lower`, `event_observed` is `y_upper.notna()`, `estimate` is negated predicted hours. Reviewer-agent should re-derive the comparability-rule reasoning independently, not just check the code matches this doc syntactically.
6. If the manual fallback (§4b) was used instead of `scikit-survival`, the actual reason (package resolution failure) must be recorded in this doc's results section, and the fallback's pairwise logic must correctly implement the three comparability cases in §4 (both-uncensored always comparable; uncensored-earlier vs. later-censored comparable; both-censored never comparable) — spot-check a handful of pairs by hand against the code's output.
7. No table is created/written by this script — verify the per-row breakdown and metrics appear only in the stored procedure's return value, per §7's explicit decision.
8. `mv.set_metric(...)` calls (if the API was confirmed to exist) must target the correct model version (the one currently promoted as `rul_aft_model`'s default, per §3) — not a stale or newly-created version.
9. The final return value (or this doc's Results section) must contain an explicit tune-vs-don't-tune recommendation per §10 — not just raw metric numbers with no verdict.
10. **(IsolationForest, §16–§22)** No row from `FEAST.TRAINING_DATASET_ISO WHERE dataset_split='train'` ever enters any of the anomaly-evaluation metrics — verify the script filters to `'test'` before scoring, exactly mirroring invariant 1 for the RUL side.
11. **(IsolationForest)** Catch-rate/lead-time is computed only over breakdowns whose full lookback cycle (previous CMMS event → the breakdown) falls entirely after the test-split cutoff timestamp — verify any breakdown whose cycle start predates the cutoff is excluded from the catch-rate denominator (not silently scored as "missed" for lack of pre-cutoff tick data), and that the exclusion count is reported.
12. **(IsolationForest)** The 72h precision/recall/FPR window must label ticks using `is_anomaly` (the `!predict` output, `output_feature_0::int == -1`), not `anomaly_score`/`decision_function` compared against an ad hoc threshold — the model's own `predict` already encodes the -1/+1 decision at the platform's chosen threshold (empirically confirmed at 0.0 on `decision_function`, per the ad hoc exploration this section is based on); recomputing a separate threshold in this script would risk silently diverging from what `cons.fct_anomaly_result` actually serves in production.
13. **(IsolationForest)** No read of `cons.fct_anomaly_result` anywhere in `06c_evaluate_iso_model.sql` — the model must be re-scored directly via `mv.run(..., function_name="predict"/"decision_function")` against `FEAST.TRAINING_DATASET_ISO` test rows, per §18's explicit decision (avoids the step-8 ordering dependency and the train/test-mixing that reading the live inference table would introduce).
14. **(IsolationForest)** No table is created/written by `06c_evaluate_iso_model.sql` — per-machine breakdown and the missed-breakdown technician-notes cross-check are diagnostic return-value output only, per §21's explicit decision (mirrors invariant 7 for the RUL side).

---

## 13. Explicitly deferred

- Hyperparameter tuning itself — this story's recommendation (§10) informs whether it's warranted; execution is a separate future decision/story.
- `cons.fct_rul_prediction` (live inference dynamic table) — separate future story, per SH-44 §11 and LLD §2; not touched by this story's persistence decision (§7).
- Persisting per-row or per-machine evaluation breakdowns (either model) to a queryable table — explicitly decided against for this story (§7, §21); revisit only if a future need for historical eval-run comparison emerges.
- Streamlit Impact Statement view's actual consumption of Registry-logged metrics (FR-CC-07, both models) — this story only attempts to log them (§9, §22); wiring them into the UI is a separate future story.
- Retuning `isolation_forest_model`'s `contamination`/`n_estimators` hyperparameters based on the catch-rate/precision-recall findings — this story's IsolationForest section produces the numbers that would inform that decision, same relationship as §10 has to the RUL model; execution is out of scope here.
- Investigating the `CNC_HORIZONTAL` 2026-07-20 miss (flat anomaly score for 2 weeks before a bearing-wear breakdown the technician notes described as having an early precursor signal) as a genuine model gap warranting a feature-engineering fix — flagged in §21's results as a finding, not something this story resolves.

---

## 14. Results (filled in — run against live `rul_aft_model` version `V_20260925_225448`)

- **Concordance index (full 19-row test set, 14 censored / 5 uncensored): 0.9400** (47 concordant, 3 discordant, 0 tied-risk, 0 tied-time, 50 comparable pairs out of 171 total pairs). Strong ranking performance — the model correctly orders which equipment will fail sooner in the large majority of comparable cases.
- **MAE (5 uncensored rows): 1861.89 hours** — dominated by the single flagged outlier (see below); without it the remaining 4 errors are 80.46h, 181.87h, 26.50h, 190.33h.
- **RMSE (5 uncensored rows): 3950.95 hours** — squared-error metric further inflated by the same outlier.
- **Median AE (5 uncensored rows): 181.87 hours** — robust to the single outlier, and a much more representative "typical" absolute-error figure than MAE/RMSE for this 5-row sample.
- **Per-row breakdown**: all 19 rows returned by the stored procedure (equipment_id, y_lower/y_upper, censored flag, predicted_hours, abs_error where uncensored) — see procedure's return value; not persisted to a table per §7.
- **Outlier root-cause finding (§8)**: the flagged `CNC_MILLING` row (`y_lower=y_upper=566.00`, predicted 9396.26h) has **two features genuinely out-of-distribution relative to the train split**:
  - `hours_since_install = 25948` vs. train-split max of `21702` — this equipment is ~20% older (by install-hours) than anything the model saw during training. Tree-based models (XGBoost) cannot meaningfully extrapolate beyond the value ranges established by training-set leaf splits, so a prediction for equipment this old is effectively undefined behavior, not a calibration error.
  - `vibration_rolling_7d_z = 2.8208` vs. train-split max of `2.7045` — mildly out-of-range (marginal, ~4% beyond max), a secondary contributing factor.
  - All other 19 features fall within the train-split's observed range (some near the p75/max edge, e.g. `vibration_rolling_24h_z=3.159` vs. train max `3.619`, but not exceeding it).
  - **Verdict**: this is a genuine data/coverage gap (never-seen equipment-age regime), not an unexplained model failure or a symptom of poor hyperparameters.
- **scikit-survival availability (§4)**: package is *listed* as available in `information_schema.packages` (versions 0.21.0/0.23.1), but **every** `PACKAGES` combination tried (unpinned; `scikit-survival==0.23.1` alone; paired with `scikit-learn==1.5.2`; paired with `scikit-learn==1.2.2` matching `scikit-survival==0.21.0`'s own `<1.3` constraint) failed at `CREATE PROCEDURE` time with `"The source distribution for package ecos-<version> requires native code to be compiled"` — `ecos` (a transitive dependency scikit-survival pulls in for its SVM-based estimators, unused by `concordance_index_censored` but still resolved) has no prebuilt wheel in this account's artifact repository for any of its 3 available versions (2.0.7.post1, 2.0.12, 2.0.14, all tried). **Genuine resolution failure — the manual pairwise fallback (§4b) was used.** The fallback's output was independently cross-validated against a real `sksurv.metrics.concordance_index_censored` call run locally (outside the stored procedure, via a temporary local `scikit-survival` install) on the identical 19-row test set and predictions — both produced identical results (c_index=0.9400, concordant=47, discordant=3, tied=0), confirming the manual implementation's correctness. `scikit-survival` was **not** added to `pyproject.toml`/`uv.lock` — beyond the resolution failure itself, `scikit-survival>=0.21` requires `scikit-learn<1.6`, which conflicts with this project's existing `scikit-learn>=1.9.0` pin (used by `isolation_forest_model`, `05_train_models.sql`); downgrading that pin project-wide to accommodate a package that turned out unusable in Snowflake anyway was rejected.
- **`mv.set_metric` availability (§9)**: confirmed **working** on this account (`snowflake-ml-python` registry API) — `concordance_index`, `mae`, `rmse`, `median_ae` were all successfully logged against `rul_aft_model` version `V_20260925_225448` (verified via a follow-up `mv.show_metrics()` call returning all four values).
- **Recommendation (§10): Do not tune hyperparameters yet.** The concordance index (0.94) shows the model already ranks equipment failure order well — the metric that matters most for maintenance-scheduling decisions. The median AE (~182h on the uncensored subset) is a reasonable absolute-error figure for MVP scope. The inflated MAE/RMSE are fully explained by one row whose `hours_since_install` value is genuinely outside the training distribution — a **data coverage gap**, not a symptom hyperparameter tuning would fix (a tree-based model cannot be tuned into extrapolating correctly beyond value ranges it never saw). The more effective next step, if pursued, is collecting more training examples of aging equipment (install-hours approaching/exceeding ~22,000h) to close that specific coverage gap — not a tuning pass on the current 94-row training set. This conclusion should also be read with the caveat that the uncensored test subset is only 5 rows, too small to treat MAE/RMSE/median-AE as statistically robust regardless of tuning; a larger test set would be needed before drawing a stronger conclusion either way.

---

## 15. Deviations from original design (Reviewer-agent PASS, 2026-09-25)

Unlike SH-49/50/44's clean implementations (no deviation from their frozen designs), this story's shipped code diverges from its own §4 **primary path** — flagged here explicitly rather than left implicit in §14's results prose, since §14 was written by `Developer-agent` before independent re-verification and this section is what an LLD-reconciliation pass (`Documenter-agent`, 2026-09-26) actually consulted.

- **§4's primary path (`scikit-survival`) was never viable, not merely deprioritized.** `Reviewer-agent` independently reproduced the `ecos`-transitive-dependency wheel failure across the same 3 version/pairing combinations `Developer-agent` tried, confirming this is a genuine account-level platform limitation (no prebuilt wheel for `ecos` in this account's artifact repository, any version) — not a shortcut taken to save time. §4b's manual fallback was therefore not optional per the letter of the design doc; it was the only implementable path.
- **The fallback was independently verified as correct, not just self-consistent.** `Reviewer-agent` hand-verified the pairwise concordance logic against 13 real test-row pairs spanning all three comparability cases (both-uncensored, uncensored-vs-censored, both-censored) called out in §4/§4b, and separately cross-validated the full 19-row result against a real local `scikit-survival` install run outside the stored-procedure sandbox on identical data — both produced identical results (c_index=0.9400, 47 concordant/3 discordant/0 tied). This closes the loop `Developer-agent`'s own §14 note started (a single local cross-check) with a fully independent second run.
- **Outlier root-cause finding independently confirmed.** `Reviewer-agent` re-derived the `CNC_MILLING` row's `hours_since_install=25948` vs. train-split max `21702` finding directly via raw SQL against the live tables, not by trusting §14's prose — confirming the ~20%-beyond-training-range extrapolation gap is real and not an artifact of how `Developer-agent` computed the train-split summary statistics.
- **`set_metric`/`show_metrics` persistence independently confirmed.** `Reviewer-agent` used `SHOW VERSIONS IN MODEL` to verify all four metrics (`concordance_index`, `mae`, `rmse`, `median_ae`) are actually attached to the `DEFAULT` model version, not just returned successfully by the stored procedure's own return value.
- **Recommendation (§10/§14) independently assessed, not just accepted.** `Reviewer-agent` judged the "don't tune, it's a data-coverage gap" verdict as justified by the numbers themselves (tree-based-model extrapolation limits, the 20%-beyond-range outlier, the small 5-row uncensored subset caveat already present in §14) rather than treating the recommendation as an assertion to take on faith.
- **Net effect on this doc**: `docs/04-5-LLD.md` §3's evaluation code sketch — which showed the §4 primary path (`sksurv`) as if it were what actually runs — has been reconciled (2026-09-26) to mark that sketch as original design intent only, point to this doc's §4b/§14 for the actual manual-pairwise implementation and its independent verification, and record the confirmed `mv.set_metric` outcome. No other doc (`docs/05-Epics.md`, Jira) required changes per `Reviewer-agent`'s own review.

**Zero code bugs found** — this PASS's only follow-up action was the documentation reconciliation above; no code changes were requested or made.

---

## 16. IsolationForest evaluation — scope extension (2026-09-26)

**Why this is bundled into SH-46 rather than a new story**: explicit user decision, acknowledged as "a slight deviation" from the original single-model ticket scope. Rationale: both models now log evaluation metrics to the Model Registry via the identical mechanism (`ModelVersion.set_metric(...)`, confirmed working in §14 above), and evaluating both together keeps the "did the models we trained actually work" story unified rather than splitting it across two tickets for what is conceptually one evaluation phase of the pipeline.

**Where this methodology actually comes from**: substantial ad hoc exploratory analysis was already done in the session that produced this extension (not committed to any story) — real `RAW.CMMS_LOG.BREAKDOWN` events were used as proxy ground truth (not PM events, and not the generator's internal simulated wear state, since real observed outcomes generalize to production), catch rate/lead time were computed per-machine per-maintenance-cycle, missed breakdowns were cross-checked against `technician_notes`, and precision/recall/FPR were computed with a 72h-before-breakdown labeling window. This section formalizes that methodology into a reproducible, versioned evaluation step — see §18 for what changed in translating it from a live/ad-hoc query into this stored procedure.

**Scope**: evaluate the already-trained `isolation_forest_model` against `FEAST.TRAINING_DATASET_ISO WHERE dataset_split='test'`, re-scored directly via the model (no read of any pre-computed inference table), joined against real `RAW.CMMS_LOG` breakdown history. Compute catch rate, median lead time, precision/recall/false-positive-rate at a 72h-before-breakdown window, and a per-machine catch-rate breakdown plus a missed-breakdown `technician_notes` cross-check, both as diagnostic (non-persisted) output. Log the five aggregate metrics to the Model Registry via `mv.set_metric(...)`.

**Not in scope**: retuning `isolation_forest_model`'s hyperparameters (`contamination`, `n_estimators`) — this section's findings inform that decision, same relationship §10 has to the RUL model, but execution is deferred (§13). Persisting any per-machine or per-breakdown diagnostic to a table (§21). Reading or modifying `cons.fct_anomaly_result` in any way (§18/§12 invariant 13).

---

## 17. Where the IsolationForest evaluation code lives & pipeline placement

**New file: `scripts/06c_evaluate_iso_model.sql`** — a separate file from `06b_evaluate_rul_model.sql`, not appended to it. Rationale: the two models use genuinely different evaluation methodologies (concordance-index/survival-metrics vs. breakdown-proximity catch-rate/precision-recall) reading different source tables (`TRAINING_DATASET_RUL` vs. `TRAINING_DATASET_ISO` + `RAW.CMMS_LOG`) — a single combined file would mix two unrelated procedures with no shared logic, working against this project's existing one-stored-procedure-per-file convention (`05_train_models.sql`, `06_train_rul_model.sql`, `06b_evaluate_rul_model.sql` each hold exactly one).

**Pipeline placement**: runs immediately after `05_train_models.sql` (step 7), *before* `run_dbt_training_dataset_rul()` (step 7a) and well before `06_train_rul_model.sql`/`06b_evaluate_rul_model.sql` (7b/7c). This is earlier in the pipeline than the RUL evaluation, not grouped alongside it — confirmed deliberately: `isolation_forest_model` exists as of step 7, and because this evaluation re-scores `FEAST.TRAINING_DATASET_ISO` directly via `mv.run(...)` rather than reading `cons.fct_anomaly_result` (§18), it has no dependency on `feast.training_dataset_rul`, `rul_aft_model`, or the phase-2 dbt run (step 8) that builds `cons.fct_anomaly_result`. Waiting until after 7b/7c purely for visual grouping would introduce an artificial ordering constraint this step doesn't actually need.

**`manage.py` change**: insert a new connector-session block calling `06c_evaluate_iso_model.sql` immediately after the existing `05_train_models.sql` block (same `snowcomotive_role` session pattern), before `run_dbt_training_dataset_rul()`. **As built**: the module docstring's `up:` sequence labels this sub-step **7-iso**, between step 7 and step 7a, with an ordering comment explaining the "why here, not later" reasoning from this section.

---

## 18. Data source & re-scoring methodology

**Confirmed decision**: no read of `cons.fct_anomaly_result` anywhere in this script. That table is a materialized dynamic table spanning the *entire* sensor-reading history (train+test mixed, continuously refreshed) and — more importantly for placement — doesn't exist until step 8 (phase-2 dbt run), which would force this evaluation to run much later in the pipeline than §17 places it.

Instead, `FEAST.TRAINING_DATASET_ISO WHERE dataset_split='test'` is read directly (raw 15-column sensor z-score feature set, `equipment_id`, `reading_ts` — no anomaly flags/scores present in this table, since those are the model's *output*, not a training feature). The loaded model is called directly against these rows, using the exact same two method calls and column list `cons__fct_anomaly_result.sql` already uses in production SQL (matching the served serving surface, not inventing a new one):

```python
from snowflake.ml.registry import Registry

registry = Registry(session=session, database_name="SNOWCOMOTIVE", schema_name="CONS")
mv = registry.get_model("isolation_forest_model").default

SENSOR_COLS = [
    "vibration_z", "vibration_rolling_1h_z", "vibration_rolling_8h_z", "vibration_rolling_24h_z", "vibration_rolling_7d_z",
    "temperature_z", "temperature_rolling_1h_z", "temperature_rolling_8h_z", "temperature_rolling_24h_z", "temperature_rolling_7d_z",
    "rpm_z", "rpm_rolling_1h_z", "rpm_rolling_8h_z", "rpm_rolling_24h_z", "rpm_rolling_7d_z",
]

test_pdf = session.table("snowcomotive.feast.feast__training_dataset_iso") \
    .filter(col("dataset_split") == "test") \
    .select(SENSOR_COLS + ["equipment_id", "reading_ts"]).to_pandas()

predict_out = mv.run(test_pdf[SENSOR_COLS], function_name="predict")
test_pdf["is_anomaly"] = (predict_out["output_feature_0"].astype(int) == -1)

score_out = mv.run(test_pdf[SENSOR_COLS], function_name="decision_function")
test_pdf["anomaly_score"] = score_out["output_feature_0"].astype(float)
```

This deliberately mirrors `cons__fct_anomaly_result.sql`'s own `!predict`/`!decision_function` calls and `output_feature_0` column-naming convention (`predictive_maintenance_dbt/models/consumption/cons__fct_anomaly_result.sql`) — the -1/+1 decision threshold is whatever the model's own `predict` method encodes (empirically confirmed at `decision_function == 0.0` by the ad hoc exploration this section formalizes; this script does not recompute or hardcode that threshold itself, it just trusts `is_anomaly` the same way production inference does).

**Test-window cutoff**: derived directly from the loaded test rows (`cutoff_ts = test_pdf["reading_ts"].min()`) rather than re-deriving `feast__training_dataset_iso.sql`'s `DATEADD('month', -6, MAX(reading_ts))` expression independently — this avoids a second copy of that boundary logic drifting from the dbt model's actual SQL. `cutoff_ts` is used only to decide which breakdowns' lookback cycles fall entirely inside the test window (§19); it is not a metric on its own.

---

## 19. Catch rate / lead time methodology (per-cycle, test-window-scoped)

Reads `RAW.CMMS_LOG` for `event_type = 'BREAKDOWN'` rows with `event_start_ts > cutoff_ts` (the breakdown itself must fall inside the test window). For each such breakdown, on the same `equipment_id`, finds the most recent prior `RAW.CMMS_LOG` event (any `event_type` — a repair, PM, or an earlier breakdown) as `cycle_start_ts`.

**Full-cycle-inside-test-window filter (§12 invariant 11)**: if `cycle_start_ts < cutoff_ts`, the maintenance cycle spans the train/test boundary — the model was never scored against the pre-cutoff portion of that cycle's ticks (they belong to the train split), so scoring this breakdown as "caught" or "missed" using only test-window ticks would be silently wrong (a cycle that started shortly before the cutoff could have its true precursor signal sitting entirely in the excluded train period). These breakdowns are **excluded** from the catch-rate denominator entirely, and the exclusion count is reported in the return value for transparency — this is the same reasoning the earlier naive 30-day-calendar-window version got wrong before being corrected to per-cycle scoping in the ad hoc exploration, just applied to a second boundary (the train/test cutoff) that didn't exist in that exploration's original live-table version.

For each **included** breakdown: scope test-window ticks to `[cycle_start_ts, breakdown_ts)` for that `equipment_id`. `caught = True` if any tick in that range has `is_anomaly = True`; `lead_time_hours = breakdown_ts - (timestamp of the first such anomalous tick)` if caught.

```
catch_rate_pct = 100 * caught_count / included_breakdown_count
median_lead_time_hours = median(lead_time_hours over caught breakdowns only)
```

---

## 20. Precision / recall / false-positive-rate (72h window)

Tick-level labeling over all `FEAST.TRAINING_DATASET_ISO` test rows: a tick is labeled `actual_positive = True` if its `reading_ts` falls within `[breakdown_ts - 72h, breakdown_ts)` for any breakdown with `event_start_ts > cutoff_ts` (breakdown itself inside the test window — the same breakdown set as §19 *before* the full-cycle exclusion filter, since a 72h window is short enough that boundary bleed from a breakdown very close to the cutoff is a minor, accepted approximation, unlike the whole-cycle contamination §19 must guard against).

```
TP = count(is_anomaly=True AND actual_positive=True)
FP = count(is_anomaly=True AND actual_positive=False)
FN = count(is_anomaly=False AND actual_positive=True)
TN = count(is_anomaly=False AND actual_positive=False)

precision_72h = TP / (TP + FP)
recall_72h = TP / (TP + FN)
false_positive_rate_72h = FP / (FP + TN)
```

This is the tight/"is it working" window from the ad hoc exploration (precision ~49.9%/recall ~61.6% observed live). The loose full-prior-cycle window explored ad hoc (precision ~71.8%/recall ~12.3%) is **not** carried into this story's logged metrics — per the metrics-selection decision below, its low recall reflects long stretches of genuinely healthy post-repair time being counted as "missed," which is expected/correct model behavior, not something worth logging as if it were a flaw.

**Note on the formal test-window numbers vs. the ad hoc figures above (see §23 for the actual logged results)**: the formally logged `precision_72h=0.3516`/`recall_72h=0.8674` differ substantially from the ad hoc ~49.9%/~61.6% figures quoted above. This is expected, not a discrepancy to resolve — the ad hoc exploration scored against the entire live sensor history (47 breakdowns), while this story's formal run is deliberately scoped to only the 5 breakdowns falling inside the much smaller test-split window (§18/§23). With a sample this small, the tick-level precision/recall is highly sensitive to exactly which cycles happen to land in the test window and how many ticks each one contributes — see §23's small-sample caveat for the full discussion. The methodology (labeling rule, comparability logic) is unchanged between the two; only the underlying sample differs.

**Metrics actually logged** (per user confirmation): `catch_rate_pct`, `median_lead_time_hours`, `precision_72h`, `recall_72h`, `false_positive_rate_72h`. Nothing beyond this set — the loose-window precision/recall and the raw threshold-separation diagnostic (avg decision_function score for normal vs. anomalous) are informative context for this doc's Results section (§23) but are not themselves logged as Registry metrics.

---

## 21. Per-machine breakdown & missed-breakdown root-cause check (diagnostic only)

**Per-machine catch rate**: the same §19 catch-rate computation, grouped by `equipment_id`, included in the stored procedure's return value as a formatted table — not written to any table, not logged as separate per-machine Registry metrics (only the fleet-wide aggregate from §20 is logged). Mirrors §6/§7's RUL per-row-breakdown pattern: diagnostic return-value output only.

**Missed-breakdown technician-notes cross-check**: for each included breakdown from §19 that was *not* caught, pull that breakdown's `RAW.CMMS_LOG.technician_notes` and include it verbatim in the return value alongside the breakdown's `equipment_id`/timestamp. This mirrors §8's RUL outlier root-cause check and LLD §4's "qualitative validation, document actual results" convention for this kind of model — the point is distinguishing a genuinely unexplainable breakdown (no precursor signal by design) from a real model gap (a precursor existed but the model didn't flag it), the same distinction the ad hoc exploration already found meaningful (4/5 misses unexplainable-by-notes, 1/5 a genuine gap). Purely descriptive — no code change to the model, no retraining.

---

## 22. Model Registry metric logging (IsolationForest)

Same confirmed-working API as §9/§14 (`mv.set_metric(...)`, this account's `snowflake-ml-python` registry API), targeting `isolation_forest_model`'s currently-promoted default version:

```python
metrics = {
    "catch_rate_pct": catch_rate_pct,
    "median_lead_time_hours": median_lead_time_hours,
    "precision_72h": precision_72h,
    "recall_72h": recall_72h,
    "false_positive_rate_72h": false_positive_rate_72h,
}
for name, value in metrics.items():
    mv.set_metric(name, float(value))
```

Wrapped in the same try/except-and-report pattern `06b_evaluate_rul_model.sql` uses (§9) — logging failure degrades to a documented finding in the return value, not an unhandled exception, even though §14 already confirmed the API works for `rul_aft_model`; re-verify independently for this model version rather than assuming the confirmation transfers. **As built**: independently re-verified rather than assumed — see §23 for the confirmed (working, twice-checked) outcome.

---

## 23. Results (filled in — run against live `isolation_forest_model` default version `V_20260924_121719`)

- **5 breakdowns fall inside the test window (`event_start_ts > cutoff_ts = 2026-03-12 00:00`); of those, 1 is excluded from the §19 catch-rate denominator** under the §12-invariant-11 full-cycle filter: `CNC_BORING`, breakdown at 2026-04-09 17:30, whose prior CMMS event (`cycle_start_ts`) predates the cutoff, meaning its maintenance cycle spans the train/test boundary and cannot be fairly scored as "caught"/"missed" using test-window ticks alone. This leaves **4 scoreable cycles** for §19's catch-rate/lead-time computation, while §20's 72h-window tick labeling (which deliberately does not apply this exclusion, per §20's own accepted-approximation rationale) still uses the full **5 breakdowns**. This exclusion count was independently confirmed via raw SQL by both `Developer-agent` and `Reviewer-agent` (same 1-row result both times). Either way, this is a genuinely small evaluation sample — far smaller than the ad hoc exploration's full-history sample of 47 breakdowns — since only breakdowns actually falling inside the test-split window qualify at all.
- **Catch rate: 75.00%** (3 of 4 scoreable cycles caught) — **median lead time: 568.25 hours** over the caught cycles.
- **72h-window tick-level metrics**: `precision_72h = 0.3516`, `recall_72h = 0.8674`, `false_positive_rate_72h = 0.0471`. Internal-consistency check (independently confirmed by `Reviewer-agent`): the 875 ticks falling inside a 72h-before-breakdown window across the 5 breakdowns sum to exactly TP+FN, and the full 30,614-tick test set sums to exactly TP+FP+FN+TN — both confusion-matrix identities check out with no unaccounted ticks.
- **Per-machine catch-rate breakdown**: returned by the stored procedure as a formatted table (per-`equipment_id` catch rate over that machine's scoreable cycles) — not persisted to a table, per §21's explicit decision.
- **Missed-breakdown `technician_notes` cross-check**: of the 4 scoreable cycles, 1 was missed — `CNC_HORIZONTAL`, breakdown at 2026-07-20 05:45, technician notes: *"Routine vibration check flagged early bearing wear; preemptively serviced."* This is a **genuine model gap, not an unexplainable-by-design miss** — the notes describe a real precursor signal (early bearing wear caught by a routine check) that the anomaly model itself did not flag in the preceding ticks. This matches the exact same breakdown and finding already surfaced in this session's earlier ad hoc exploration, now confirmed against the formal test-window evaluation rather than the ad hoc full-history one.
- **`mv.set_metric` availability**: confirmed **working** for `isolation_forest_model` — independently verified **twice**: once by `Developer-agent` (stored procedure's own `mv.show_metrics()` follow-up call) and once separately by `Reviewer-agent` (`SHOW VERSIONS IN MODEL`, confirming all five metrics — `catch_rate_pct`, `median_lead_time_hours`, `precision_72h`, `recall_72h`, `false_positive_rate_72h` — are actually attached to the `V_20260924_121719` version, not just returned by the procedure's return value). The RUL model's §14 confirmation transfers cleanly to this model.
- **Small-sample caveat (parallels §14's "5-row uncensored subset" caveat for the RUL side)**: the entire evaluation above rests on **4 scoreable cycles / 5 breakdowns** — a sample this small is nowhere near enough to treat `catch_rate_pct=75.00%`, `precision_72h=0.3516`, `recall_72h=0.8674`, or `false_positive_rate_72h=0.0471` as statistically robust long-run estimates. A single sparse window can swing the aggregate tick-level precision/recall materially on its own — e.g. one machine's cycle contributing a 63-tick window vs. another contributing a 288-tick window means the 72h-window metrics are not evenly weighted across the 5 breakdowns, so one unusually short or long cycle can move `precision_72h`/`recall_72h` by a wide margin. These numbers describe **this specific test window's behavior**, not a stable estimate of the model's true precision/recall in general.
- **Recommendation (§22-referenced retuning question): do not retune `contamination`/`n_estimators` yet, but treat the numbers as directional, not precise.** A 75% catch rate with a 568-hour median lead time is a meaningfully useful early-warning signal even on this small sample — three of four scoreable maintenance cycles had the model flag an anomaly nearly 24 days (on average, among caught cycles) before the actual breakdown, which is exactly the kind of lead time a maintenance-scheduling workflow can act on. The tick-level `precision_72h=0.35`/`false_positive_rate_72h=0.047` numbers should be read with real caution, though — they're built from only 5 breakdowns' worth of 72h windows, and the small-sample caveat above means a handful of additional real breakdowns falling into future test windows could shift precision meaningfully in either direction. This does **not** undermine SH-50's leakage-safety argument for using `isolation_forest_model`'s output as a RUL feature — that argument rests on split-cutoff alignment (the anomaly feature is computed consistently across train/test with no future-leakage), a structural property independent of how precise this evaluation's specific precision/recall numbers are. But nobody consuming these metrics (e.g. a future Streamlit Impact Statement view, FR-CC-07) should treat `precision_72h=0.35` as a fixed, reliable number — it is a first read from a 4-5-event sample, not a converged estimate, and will firm up materially as more elapsed time produces more breakdowns in future test windows. The one genuine model gap found (`CNC_HORIZONTAL`, §21) is worth tracking as a candidate feature-engineering follow-up (per §13's deferred item) independent of any hyperparameter retuning decision — it's a coverage gap in what the model was given to learn from, not something `contamination`/`n_estimators` tuning would fix.
