-- ============================================================================
-- 06b_evaluate_rul_model.sql — Model evaluation (RUL AFT, rul_aft_model)
-- Traces to: FR-FS-05, LLD Module 5 §3, docs/05-Epics.md EPIC-RUL §4.4,
-- docs/designs/SH-46-rul-model-evaluation.md, docs/designs/SH-44-train-rul-aft-model.md
-- Jira: SH-46 (S-RUL-4)
-- Status: Built (v1)
--
-- Must run in its own connector session strictly after 06_train_rul_model.sql
-- (needs the trained rul_aft_model version to exist as its Registry default)
-- and strictly before the phase-2 dbt run (per manage.py's updated sequence,
-- docs/designs/SH-46-rul-model-evaluation.md §2). Read-only against
-- feast.training_dataset_rul WHERE dataset_split='test' (19 rows: 14
-- censored / 5 uncensored, confirmed live composition per SH-44 §12) and the
-- Registry's rul_aft_model.default version -- does NOT retrain, re-log, or
-- otherwise mutate the model itself.
--
-- Metrics:
--   - Concordance index (manual pairwise implementation, design doc §4b --
--     see the scikit-survival resolution-failure note below) on the FULL
--     19-row test set (both censored and uncensored) -- the whole point of
--     this metric is correctly ranking censored vs. uncensored pairs, so it
--     must never be computed on an uncensored-only subset.
--   - MAE / RMSE / median absolute error on the 5 uncensored rows only --
--     there is no true failure time to compare a censored row's y_lower
--     against, so these three metrics explicitly exclude censored rows.
--   - Per-row breakdown (equipment_id, y_lower/y_upper, censored flag,
--     predicted RUL, abs_error where uncensored) returned as a formatted
--     string for diagnosability -- NOT persisted to any table (design doc
--     §7's explicit decision; that's reserved for a future
--     cons.fct_rul_prediction story, out of scope here).
--   - Outlier root-cause check: dynamically identifies whichever uncensored
--     test-split row has the LARGEST abs_error (|y_lower - predicted_hours|)
--     on THIS run's actual data (originally motivated by SH-44 §12's finding
--     of a specific CNC_MILLING row with y_lower=y_upper=566.00, predicted
--     ~9,396h -- but the check no longer assumes that or any other specific
--     row will exist across future regenerations with a different --now
--     anchor/train-test split). Compares the dynamically-selected row's
--     feature values against the train split's per-feature [min, p25,
--     median, p75, max], flagging any feature outside [min, max] of the
--     train distribution as genuinely out-of-distribution.
--   - Attempts mv.set_metric(...) to log concordance_index/mae/rmse/
--     median_ae against the Registry's rul_aft_model default version.
--
-- scikit-survival package-resolution outcome (design doc §4, empirically
-- verified): information_schema.packages LISTS scikit-survival 0.21.0/0.23.1
-- as available, but every PACKAGES combination tried (unpinned, pinned to
-- 0.23.1 alone, pinned alongside scikit-learn==1.5.2, pinned alongside
-- scikit-learn==1.2.2 per scikit-survival 0.21.0's own <1.3 constraint) fails
-- at CREATE PROCEDURE time with "The source distribution for package
-- ecos-<version> requires native code to be compiled" -- ecos (a transitive
-- dependency scikit-survival pulls in for its SVM-based estimators, unused
-- by concordance_index_censored but still resolved) has no prebuilt wheel in
-- this account's artifact repository for ANY of its available versions
-- (2.0.7.post1, 2.0.12, 2.0.14 all tried and failed identically). This is a
-- genuine, un-work-aroundable resolution failure -- not a version-pinning
-- mistake -- so per §4b this script uses the manual pairwise concordance
-- implementation below instead. scikit-survival was NOT added to
-- pyproject.toml/uv.lock (design doc §11) for the same reason, and also
-- because scikit-survival>=0.21 requires scikit-learn<1.6, which conflicts
-- with this project's existing scikit-learn>=1.9.0 pin (used by
-- isolation_forest_model, 05_train_models.sql) -- downgrading that pin
-- project-wide to accommodate a package that turned out unusable anyway
-- was rejected.
-- ============================================================================

CREATE OR REPLACE PROCEDURE snowcomotive.cons.sp_evaluate_rul_aft_model()
RETURNS STRING
LANGUAGE PYTHON
RUNTIME_VERSION = '3.11'
PACKAGES = ('snowflake-snowpark-python', 'snowflake-ml-python', 'pandas', 'scikit-learn')
HANDLER = 'evaluate'
AS
$$
from snowflake.ml.registry import Registry
from snowflake.snowpark.functions import col
from sklearn.metrics import mean_absolute_error, mean_squared_error, median_absolute_error


def _concordance_index_censored(event_observed, event_time, estimate):
    # Manual pairwise fallback (design doc §4b) -- scikit-survival's
    # ecos transitive dependency has no prebuilt wheel on this account for
    # any tried version (see file header). 19 rows -> 171 pairs, an O(n^2)
    # Python loop is trivially fast, no vectorization needed.
    #
    # Comparability rule (design doc §4, right-censoring semantics):
    #   - both uncensored: always comparable.
    #   - one uncensored with time_i < time_j, other censored with
    #     time_j >= time_i: comparable (i's failure is known to precede j's
    #     last known survival point). The reverse (comparing against a
    #     later-censored point whose time is earlier) is NOT comparable --
    #     a censored time is a lower bound on true failure time, not the
    #     true failure time.
    #   - both censored: never comparable (neither true failure time known).
    n = len(event_time)
    concordant = 0
    discordant = 0
    tied_risk = 0
    tied_time = 0
    comparable_pairs = 0
    for i in range(n):
        for j in range(i + 1, n):
            ti, tj = event_time[i], event_time[j]
            ei, ej = event_observed[i], event_observed[j]
            if ei and ej:
                comparable = True
            elif ei and not ej:
                comparable = ti < tj
            elif ej and not ei:
                comparable = tj < ti
            else:
                comparable = False
            if not comparable:
                continue
            comparable_pairs += 1
            # Order i,j so that the one with the smaller (comparable) event
            # time is treated as "should fail first" per estimate's risk
            # convention (higher estimate = should fail sooner).
            if ti == tj:
                tied_time += 1
                continue
            if ti < tj:
                lo_est, hi_est = estimate[i], estimate[j]
            else:
                lo_est, hi_est = estimate[j], estimate[i]
            if lo_est == hi_est:
                tied_risk += 1
            elif lo_est > hi_est:
                concordant += 1
            else:
                discordant += 1
    denom = concordant + discordant + tied_risk
    c_index = (concordant + 0.5 * tied_risk) / denom if denom > 0 else float("nan")
    return c_index, concordant, discordant, tied_risk, tied_time


# feature_cols copied verbatim from 06_train_rul_model.sql's list -- must not
# drift, or predict()'s column alignment against the logged model signature
# silently breaks (design doc §3).
FEATURE_COLS = [
    "vibration_z", "vibration_rolling_1h_z", "vibration_rolling_8h_z", "vibration_rolling_24h_z", "vibration_rolling_7d_z",
    "temperature_z", "temperature_rolling_1h_z", "temperature_rolling_8h_z", "temperature_rolling_24h_z", "temperature_rolling_7d_z",
    "rpm_z", "rpm_rolling_1h_z", "rpm_rolling_8h_z", "rpm_rolling_24h_z", "rpm_rolling_7d_z",
    "hours_since_last_service", "hours_since_install",
    "is_anomaly", "anomaly_score",
    "any_anomaly_flagged_72h", "min_anomaly_score_72h", "pct_anomalous_ticks_72h",
]


def _read_split(session, split):
    df = session.table("snowcomotive.feast.feast__training_dataset_rul").filter(col("dataset_split") == split)
    pdf = df.select(FEATURE_COLS + ["equipment_id", "y_lower", "y_upper"]).to_pandas()
    pdf.columns = [c.lower() for c in pdf.columns]
    # Same boolean-to-numeric cast as 06_train_rul_model.sql's training read
    # (design doc §3) -- required for identical reasons on the test read.
    pdf["is_anomaly"] = pdf["is_anomaly"].astype(int)
    pdf["any_anomaly_flagged_72h"] = pdf["any_anomaly_flagged_72h"].astype(int)
    return pdf


def evaluate(session):
    test_pdf = _read_split(session, "test")
    row_count = len(test_pdf)
    if row_count == 0:
        return "SKIPPED: 0 rows in feast.training_dataset_rul WHERE dataset_split = 'test'"

    registry = Registry(session=session, database_name="SNOWCOMOTIVE", schema_name="CONS")
    mv = registry.get_model("rul_aft_model").default

    preds = mv.run(test_pdf[FEATURE_COLS], function_name="predict")
    pred_hours = preds["output_feature_0"].values.astype(float)
    test_pdf["predicted_hours"] = pred_hours

    # --- Concordance index: FULL 19-row test set (both censored and
    # uncensored) -- never filtered to uncensored-only (design doc §4/§12
    # invariant 3). event_time always uses y_lower (true failure time when
    # uncensored, last-known-survival lower bound when censored);
    # event_observed is y_upper.notna() (True = uncensored/failure observed);
    # estimate is negated predicted hours (higher predicted RUL = lower risk,
    # c-index expects a risk score where higher = sooner failure).
    event_observed = test_pdf["y_upper"].notna().values
    event_time = test_pdf["y_lower"].values.astype(float)
    c_index, concordant, discordant, tied_risk, tied_time = _concordance_index_censored(
        event_observed, event_time, -pred_hours,
    )

    # --- MAE / RMSE / median-AE: uncensored subset ONLY -- a censored row's
    # y_lower is a lower bound, not a true failure time, so it must never
    # enter these three metrics (design doc §5/§12 invariant 4). Guarded for
    # the zero-uncensored-rows edge case -- sklearn's mean_absolute_error/
    # mean_squared_error/median_absolute_error all raise
    # "ValueError: Found array with 0 sample(s)" on empty arrays, so this
    # must be checked BEFORE calling them, not just in the outlier-check
    # section below (there's genuinely no ground truth to compare against
    # for a censored-only test set, so these become N/A, not 0.0/nan).
    uncensored_mask = test_pdf["y_upper"].notna()
    y_true_uncensored = test_pdf.loc[uncensored_mask, "y_lower"]
    pred_uncensored = pred_hours[uncensored_mask.values]
    n_uncensored = len(pred_uncensored)
    if n_uncensored == 0:
        mae = rmse = median_ae = None
    else:
        mae = mean_absolute_error(y_true_uncensored, pred_uncensored)
        rmse = mean_squared_error(y_true_uncensored, pred_uncensored) ** 0.5
        median_ae = median_absolute_error(y_true_uncensored, pred_uncensored)

    # --- Per-row breakdown (diagnosability only, not persisted -- §7).
    lines = [
        f"{'equipment_id':16s} {'y_lower':>10s} {'y_upper':>10s} {'censored':>9s} {'predicted_hours':>16s} {'abs_error':>10s}"
    ]
    for _, r in test_pdf.iterrows():
        censored = bool(r["y_upper"] != r["y_upper"])  # NaN check without importing numpy
        y_upper_str = f"{r['y_upper']:.2f}" if not censored else "NULL"
        abs_err_str = f"{abs(r['y_lower'] - r['predicted_hours']):.2f}" if not censored else ""
        lines.append(
            f"{r['equipment_id']:16s} {r['y_lower']:>10.2f} {y_upper_str:>10s} {str(censored):>9s} "
            f"{r['predicted_hours']:>16.2f} {abs_err_str:>10s}"
        )
    per_row_breakdown = "\n".join(lines)

    # --- Outlier root-cause check (§8): dynamically identify whichever
    # uncensored test-split row has the LARGEST abs_error on THIS run's real
    # data (reusing the abs-error values already computed for MAE/RMSE above,
    # rather than hardcoding a specific row/equipment_id/y_lower -- a fixed
    # expectation broke across a teardown/rebuild once the train/test split
    # boundary shifted). Compare that row's feature values against the TRAIN
    # split's per-feature distribution. Purely descriptive, no retraining/
    # feature-engineering/model change.
    train_pdf = _read_split(session, "train")
    outlier_lines = []
    if n_uncensored == 0:
        outlier_lines.append("WARNING: no uncensored rows in test set -- cannot identify an outlier row")
    else:
        abs_errors_uncensored = (y_true_uncensored - pred_uncensored).abs()
        max_abs_error = abs_errors_uncensored.max()
        worst_idx = abs_errors_uncensored[abs_errors_uncensored == max_abs_error].index
        if len(worst_idx) > 1:
            outlier_lines.append(
                f"NOTE: {len(worst_idx)} uncensored rows tied for largest abs_error "
                f"({max_abs_error:.2f} hours) -- reporting all tied rows."
            )
        for idx in worst_idx:
            outlier_row = test_pdf.loc[idx]
            outlier_lines.append(
                f"Outlier row (largest abs_error on this run): equipment_id={outlier_row['equipment_id']}, "
                f"y_lower={outlier_row['y_lower']:.2f}, predicted_hours={outlier_row['predicted_hours']:.2f}, "
                f"abs_error={max_abs_error:.2f}"
            )
            for feat in FEATURE_COLS:
                val = outlier_row[feat]
                tmin = train_pdf[feat].min()
                tp25 = train_pdf[feat].quantile(0.25)
                tmed = train_pdf[feat].median()
                tp75 = train_pdf[feat].quantile(0.75)
                tmax = train_pdf[feat].max()
                if val < tmin or val > tmax:
                    flag = "OUT-OF-RANGE (never seen in training)"
                else:
                    flag = ""
                outlier_lines.append(
                    f"  {feat:28s} outlier={val:12.4f}  train[min={tmin:10.4f} p25={tp25:10.4f} "
                    f"median={tmed:10.4f} p75={tp75:10.4f} max={tmax:10.4f}]  {flag}"
                )
    outlier_report = "\n".join(outlier_lines)

    # --- Registry metric logging (§9) -- verified empirically that
    # set_metric() exists on this account's snowflake-ml-python version and
    # succeeds; still guarded so a future account/version drift degrades to a
    # documented finding rather than an unhandled failure.
    metrics = {"concordance_index": float(c_index)}
    if mae is not None:
        metrics["mae"] = float(mae)
        metrics["rmse"] = float(rmse)
        metrics["median_ae"] = float(median_ae)
    try:
        for name, value in metrics.items():
            mv.set_metric(name, value)
        metric_logging_status = f"OK -- logged {list(metrics.keys())} on version {mv.version_name}"
        if mae is None:
            metric_logging_status += " (mae/rmse/median_ae skipped -- N/A, 0 uncensored rows)"
    except Exception as e:
        metric_logging_status = f"FAILED -- {type(e).__name__}: {e}"

    censored_count = int(sum(~event_observed))
    uncensored_count = int(sum(event_observed))
    mae_str = f"{mae:.2f} hours" if mae is not None else "N/A (0 uncensored rows)"
    rmse_str = f"{rmse:.2f} hours" if rmse is not None else "N/A (0 uncensored rows)"
    median_ae_str = f"{median_ae:.2f} hours" if median_ae is not None else "N/A (0 uncensored rows)"

    return (
        f"Evaluated rul_aft_model {mv.version_name} on {row_count} test rows "
        f"({censored_count} censored / {uncensored_count} uncensored)\n\n"
        f"Concordance index (full {row_count}-row test set): {c_index:.4f} "
        f"(concordant={int(concordant)}, discordant={int(discordant)}, tied_risk={int(tied_risk)}, tied_time={int(tied_time)})\n"
        f"MAE ({uncensored_count} uncensored rows): {mae_str}\n"
        f"RMSE ({uncensored_count} uncensored rows): {rmse_str}\n"
        f"Median AE ({uncensored_count} uncensored rows): {median_ae_str}\n\n"
        f"Per-row breakdown:\n{per_row_breakdown}\n\n"
        f"Outlier root-cause check:\n{outlier_report}\n\n"
        f"Registry metric logging: {metric_logging_status}"
    )
$$;

CALL snowcomotive.cons.sp_evaluate_rul_aft_model();
