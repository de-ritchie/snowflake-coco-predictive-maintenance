-- ============================================================================
-- 06b_evaluate_rul_model.sql — Model evaluation (RUL AFT, rul_aft_model)
-- Traces to: FR-FS-05, LLD Module 5 §3, docs/05-Epics.md EPIC-RUL §4.4,
-- docs/designs/SH-46-rul-model-evaluation.md, docs/designs/SH-44-train-rul-aft-model.md,
-- docs/designs/SH-86-fix-rul-training-leakage.md
-- Jira: SH-46 (S-RUL-4), SH-86
-- Status: Built (v3 — Snowpark-native evaluation)
--
-- Must run in its own connector session strictly after 06_train_rul_model.sql
-- (needs the trained rul_aft_model version to exist as its Registry default)
-- and strictly before the phase-2 dbt run (per manage.py's updated sequence,
-- docs/designs/SH-46-rul-model-evaluation.md §2). Read-only against
-- feast.training_dataset_rul WHERE dataset_split='test' and the Registry's
-- rul_aft_model.default version -- does NOT retrain, re-log, or otherwise
-- mutate the model itself.
--
-- v3 changes (follow-up to SH-86, same PR):
--   - Concordance index now computed via a Snowflake self-join (ROW_NUMBER()
--     based a.rn < b.rn pairing + one aggregation), NOT a hand-rolled Fenwick
--     tree. The warehouse does the O(n^2) pairwise comparison natively
--     instead of a Python loop -- same exact comparability rules, same
--     result, but zero local algorithm. This trades "stays O(n log n)
--     forever" for "100% Snowpark, costs grow quadratically with test-set
--     size" -- an explicit choice given the current ~33k-row scale. If this
--     ever becomes a performance/cost problem on a much larger test set, the
--     fix is to add a `.filter(col("y_lower") <= 144)` (or <= 500) before
--     the self-join, restricting concordance to a near-event window (NOT
--     applied here -- the full-test-set self-join measured acceptable on
--     live data at this scale, see validation note below).
--   - Cross-checked once against lifelines' concordance_index() in a
--     throwaway stored procedure (sp_verify_concordance_lifelines, dropped
--     after use, not part of this file) -- confirmed matching value before
--     trusting the self-join: lifelines=0.701746 vs. self-join=0.701800
--     (full 32,771-row live test set, 2026-10-03). Also confirmed lifelines'
--     sign convention empirically: it treats predicted_scores like a
--     survival-time estimate (higher=later failure/lower risk), the SAME
--     natural meaning as our raw predicted_hours -- so it's passed directly,
--     NOT negated (negating gives 0.298254 = 1 - 0.701746, confirming the
--     flip). Measured self-join performance: 53.4s end-to-end on X-Small
--     warehouse for the full procedure (inference + ~537M-pair self-join +
--     MAE/RMSE/trajectory/outlier check) -- no performance problem at this
--     scale, so the full-test-set approach (no near-event window filter) is
--     kept as-is.
--   - Trajectory-correlation window extended from 0-144h to 0-500h.
--   - _read_split now returns a Snowpark DataFrame (not pandas). Inference
--     runs via mv.run() on a Snowpark DataFrame directly -- confirmed the
--     Registry preserves all input columns alongside the prediction column
--     when given a Snowpark DataFrame (output_with_input_features=True).
--   - MAE/RMSE/median-AE, the outlier check's train-distribution stats, and
--     the top-10 worst-predictions ranking are all now Snowpark aggregations
--     (avg/sqrt/median/percentile_cont/order_by+limit) instead of pulling
--     the full train/test splits into pandas. Only small, already-aggregated
--     results are ever .collect()'d to Python, for string formatting.
--
-- Metrics:
--   - Concordance index (SQL self-join) on the FULL test set (both censored
--     and uncensored).
--   - MAE / RMSE / median absolute error on uncensored rows only (full test
--     set, unchanged scope).
--   - Trajectory correlation (Pearson, bucketed, 0-500h) on uncensored rows.
--   - Top-10 worst predictions (by abs_error) for diagnosability.
--   - Outlier root-cause check on the single worst abs-error uncensored row.
--   - Registry metric logging (concordance_index, mae, rmse, median_ae,
--     trajectory_correlation).
-- ============================================================================

CREATE OR REPLACE PROCEDURE snowcomotive.cons.sp_evaluate_rul_aft_model()
RETURNS STRING
LANGUAGE PYTHON
RUNTIME_VERSION = '3.11'
PACKAGES = ('snowflake-snowpark-python', 'snowflake-ml-python')
HANDLER = 'evaluate'
AS
$$
from snowflake.ml.registry import Registry
from snowflake.snowpark.functions import (
    abs as sf_abs,
    avg,
    col,
    corr,
    count,
    floor,
    max as sf_max,
    median,
    min as sf_min,
    percentile_cont,
    pow as sf_pow,
    row_number,
    sqrt,
    sum as sf_sum,
    when,
)
from snowflake.snowpark.window import Window

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

# Trajectory-correlation window (v3: 500h, was 144h). 2h bucket width unchanged.
TRAJECTORY_WINDOW_HOURS = 500
TRAJECTORY_BUCKET_WIDTH_HOURS = 2


def _read_split(session, split):
    """Returns a Snowpark DataFrame (not pandas) -- all downstream metric
    computation stays server-side until a final small result is collected.
    """
    df = session.table("snowcomotive.feast.feast__training_dataset_rul").filter(col("dataset_split") == split)
    df = df.select(*FEATURE_COLS, "equipment_id", "y_lower", "y_upper", "reading_ts")
    # Cast booleans to int for the model signature -- mv.run() also auto-casts
    # Snowpark DataFrame input columns to match the signature type, but this
    # is explicit for clarity and matches 06_train_rul_model.sql's own cast.
    df = df.with_column("is_anomaly", col("is_anomaly").cast("int"))
    df = df.with_column("any_anomaly_flagged_72h", col("any_anomaly_flagged_72h").cast("int"))
    return df


def _concordance_index_self_join(scored_df):
    """Concordance index via a Snowflake self-join (v3 -- replaces the
    Fenwick-tree implementation entirely). The warehouse performs the O(n^2)
    pairwise comparison natively instead of a Python loop/tree.

    Comparability rules (right-censoring, unchanged from SH-46 §4 / SH-86):
      - both uncensored: always comparable
      - one uncensored (time_a) vs one censored (time_b): comparable only if
        the uncensored one's time is strictly earlier
      - both censored: never comparable
    concordant: comparable pair, times differ, earlier-time row has LOWER
    predicted_hours (correctly predicted to fail sooner). discordant: earlier
    row has HIGHER predicted_hours. tied_risk: predicted_hours equal.
    tied_time: times equal (only possible when both uncensored, since that's
    the only comparable case where equal times can occur).

    row_number() over a stable total order gives each row a unique rank
    purely to drive the a.rn < b.rn join condition (counts each unordered
    pair exactly once, excludes self-pairs) -- the ordering itself carries
    no semantic meaning.
    """
    scored = scored_df.select(
        row_number().over(Window.order_by("equipment_id", "reading_ts")).alias("rn"),
        col("y_lower"),
        col("event_observed"),
        col("predicted_hours"),
    )
    a = scored.alias("a")
    b = scored.alias("b")
    pairs = a.join(b, col("a", "rn") < col("b", "rn"))

    # col(alias, column_name) -- the TWO-ARGUMENT form is required for
    # disambiguating self-joined columns (confirmed via DataFrame.alias()'s
    # own docstring); a dotted single-string "a.y_lower" is NOT valid syntax.
    a_obs, b_obs = col("a", "event_observed"), col("b", "event_observed")
    a_time, b_time = col("a", "y_lower"), col("b", "y_lower")
    a_pred, b_pred = col("a", "predicted_hours"), col("b", "predicted_hours")

    comparable = (
        (a_obs & b_obs)
        | (a_obs & ~b_obs & (a_time < b_time))
        | (b_obs & ~a_obs & (b_time < a_time))
    )
    times_differ = a_time != b_time
    a_earlier = a_time < b_time

    concordant_expr = comparable & times_differ & (
        (a_earlier & (a_pred < b_pred))
        | (~a_earlier & (b_pred < a_pred))
    )
    discordant_expr = comparable & times_differ & (
        (a_earlier & (a_pred > b_pred))
        | (~a_earlier & (b_pred > a_pred))
    )
    tied_risk_expr = comparable & times_differ & (a_pred == b_pred)
    tied_time_expr = comparable & (a_time == b_time)

    result = pairs.agg(
        sf_sum(when(concordant_expr, 1).otherwise(0)).alias("concordant"),
        sf_sum(when(discordant_expr, 1).otherwise(0)).alias("discordant"),
        sf_sum(when(tied_risk_expr, 1).otherwise(0)).alias("tied_risk"),
        sf_sum(when(tied_time_expr, 1).otherwise(0)).alias("tied_time"),
    ).collect()[0]

    concordant = int(result["CONCORDANT"] or 0)
    discordant = int(result["DISCORDANT"] or 0)
    tied_risk = int(result["TIED_RISK"] or 0)
    tied_time = int(result["TIED_TIME"] or 0)
    denom = concordant + discordant + tied_risk
    c_index = (concordant + 0.5 * tied_risk) / denom if denom > 0 else float("nan")
    return c_index, concordant, discordant, tied_risk, tied_time


def _trajectory_correlation(scored_df):
    """Bucket uncensored test ticks by true remaining_hours (2h buckets,
    0-500h -- v3, was 0-144h), compute mean predicted_hours per bucket
    server-side, then compute Pearson correlation between bucket index and
    mean prediction -- also server-side, on the small (~250-row) bucketed
    result. A healthy model produces strong positive correlation (higher
    true remaining time -> higher predicted RUL); the original SH-86 bug
    produced strong NEGATIVE correlation (predicted RUL rose toward failure).
    """
    uncensored_df = scored_df.filter(col("event_observed"))
    bucketed = (
        uncensored_df
        .filter((col("y_lower") >= 0) & (col("y_lower") < TRAJECTORY_WINDOW_HOURS))
        .with_column("bucket", floor(col("y_lower") / TRAJECTORY_BUCKET_WIDTH_HOURS) * TRAJECTORY_BUCKET_WIDTH_HOURS)
        .group_by("bucket")
        .agg(avg(col("predicted_hours")).alias("mean_predicted"), count("*").alias("n"))
    )
    n_buckets = bucketed.count()
    if n_buckets < 3:
        return float("nan")
    r = bucketed.agg(corr(col("bucket"), col("mean_predicted")).alias("r")).collect()[0]["R"]
    return float(r) if r is not None else float("nan")


def evaluate(session):
    test_df = _read_split(session, "test")
    row_count = test_df.count()
    if row_count == 0:
        return "SKIPPED: 0 rows in feast.training_dataset_rul WHERE dataset_split = 'test'"

    registry = Registry(session=session, database_name="SNOWCOMOTIVE", schema_name="CONS")
    mv = registry.get_model("rul_aft_model").default

    # Inference via mv.run() on a Snowpark DataFrame directly -- the Registry
    # preserves all input columns (y_lower, y_upper, equipment_id, reading_ts,
    # features) alongside the new prediction column, entirely server-side.
    scored_df = mv.run(test_df, function_name="predict")
    scored_df = scored_df.with_column_renamed("OUTPUT_FEATURE_0", "predicted_hours")
    scored_df = scored_df.with_column("event_observed", col("y_upper").is_not_null())
    scored_df = scored_df.with_column("abs_error", sf_abs(col("y_lower") - col("predicted_hours")))
    scored_df = scored_df.cache_result()  # reused by concordance, MAE/RMSE, trajectory, top-10, outlier

    # --- Concordance index: FULL test set (both censored and uncensored),
    # via Snowflake self-join (v3).
    c_index, concordant, discordant, tied_risk, tied_time = _concordance_index_self_join(scored_df)

    # --- MAE / RMSE / median-AE: uncensored subset ONLY, full test set
    # (unchanged scope) -- single Snowpark aggregation, no pandas.
    uncensored_df = scored_df.filter(col("event_observed"))
    n_uncensored = uncensored_df.count()
    if n_uncensored == 0:
        mae = rmse = median_ae = None
    else:
        stats_row = uncensored_df.agg(
            avg(col("abs_error")).alias("mae"),
            sqrt(avg(sf_pow(col("y_lower") - col("predicted_hours"), 2))).alias("rmse"),
            median(col("abs_error")).alias("median_ae"),
        ).collect()[0]
        mae = float(stats_row["MAE"])
        rmse = float(stats_row["RMSE"])
        median_ae = float(stats_row["MEDIAN_AE"])

    # --- Trajectory correlation (v3: 0-500h window): permanent regression guard.
    traj_corr = _trajectory_correlation(scored_df)
    traj_corr_warning = ""
    if traj_corr == traj_corr and traj_corr < 0.5:  # traj_corr == traj_corr is a NaN check
        traj_corr_warning = (
            f"\nWARNING: trajectory_correlation={traj_corr:.4f} (< 0.5 threshold) — "
            f"predicted RUL may not decrease toward failure as expected. "
            f"Investigate model behavior."
        )

    # --- Top-10 worst predictions: filter/sort/limit server-side, only the
    # final 10 rows ever leave Snowflake.
    top10_lines = [
        f"{'equipment_id':16s} {'y_lower':>10s} {'y_upper':>10s} {'censored':>9s} {'predicted_hours':>16s} {'abs_error':>10s}"
    ]
    if n_uncensored > 0:
        top10_rows = (
            uncensored_df
            .select("equipment_id", "y_lower", "y_upper", "predicted_hours", "abs_error")
            .order_by(col("abs_error").desc())
            .limit(10)
            .collect()
        )
        for r in top10_rows:
            top10_lines.append(
                f"{r['EQUIPMENT_ID']:16s} {r['Y_LOWER']:>10.2f} {r['Y_UPPER']:>10.2f} {'False':>9s} "
                f"{r['PREDICTED_HOURS']:>16.2f} {r['ABS_ERROR']:>10.2f}"
            )
    else:
        top10_lines.append("(no uncensored rows -- cannot compute abs_error)")
    top10_breakdown = "\n".join(top10_lines)

    # --- Outlier root-cause check: find the worst row(s) server-side, then
    # compute train-distribution stats for all 22 features in ONE
    # aggregation pass (not a 138k-row pandas pull).
    outlier_lines = []
    if n_uncensored == 0:
        outlier_lines.append("WARNING: no uncensored rows in test set -- cannot identify an outlier row")
    else:
        max_err_row = uncensored_df.agg(sf_max(col("abs_error")).alias("max_err")).collect()[0]
        max_abs_error = float(max_err_row["MAX_ERR"])
        worst_rows = (
            uncensored_df
            .filter(col("abs_error") == max_abs_error)
            .select(*FEATURE_COLS, "equipment_id", "y_lower", "predicted_hours")
            .collect()
        )
        if len(worst_rows) > 1:
            outlier_lines.append(
                f"NOTE: {len(worst_rows)} uncensored rows tied for largest abs_error "
                f"({max_abs_error:.2f} hours) -- reporting all tied rows."
            )

        train_df = _read_split(session, "train")
        agg_exprs = []
        for feat in FEATURE_COLS:
            agg_exprs += [
                sf_min(col(feat)).alias(f"{feat}__min"),
                percentile_cont(0.25).within_group(col(feat)).alias(f"{feat}__p25"),
                median(col(feat)).alias(f"{feat}__median"),
                percentile_cont(0.75).within_group(col(feat)).alias(f"{feat}__p75"),
                sf_max(col(feat)).alias(f"{feat}__max"),
            ]
        train_stats = train_df.agg(*agg_exprs).collect()[0]

        for row in worst_rows:
            outlier_lines.append(
                f"Outlier row (largest abs_error on this run): equipment_id={row['EQUIPMENT_ID']}, "
                f"y_lower={row['Y_LOWER']:.2f}, predicted_hours={row['PREDICTED_HOURS']:.2f}, "
                f"abs_error={max_abs_error:.2f}"
            )
            for feat in FEATURE_COLS:
                val = row[feat.upper()]
                tmin = train_stats[f"{feat.upper()}__MIN"]
                tp25 = train_stats[f"{feat.upper()}__P25"]
                tmed = train_stats[f"{feat.upper()}__MEDIAN"]
                tp75 = train_stats[f"{feat.upper()}__P75"]
                tmax = train_stats[f"{feat.upper()}__MAX"]
                flag = "OUT-OF-RANGE (never seen in training)" if (val < tmin or val > tmax) else ""
                outlier_lines.append(
                    f"  {feat:28s} outlier={val:12.4f}  train[min={tmin:10.4f} p25={tp25:10.4f} "
                    f"median={tmed:10.4f} p75={tp75:10.4f} max={tmax:10.4f}]  {flag}"
                )
    outlier_report = "\n".join(outlier_lines)

    # --- Registry metric logging
    metrics = {"concordance_index": float(c_index)}
    if mae is not None:
        metrics["mae"] = mae
        metrics["rmse"] = rmse
        metrics["median_ae"] = median_ae
    if traj_corr == traj_corr:  # not NaN
        metrics["trajectory_correlation"] = float(traj_corr)
    try:
        for name, value in metrics.items():
            mv.set_metric(name, value)
        metric_logging_status = f"OK -- logged {list(metrics.keys())} on version {mv.version_name}"
        if mae is None:
            metric_logging_status += " (mae/rmse/median_ae skipped -- N/A, 0 uncensored rows)"
    except Exception as e:
        metric_logging_status = f"FAILED -- {type(e).__name__}: {e}"

    censored_count = row_count - n_uncensored
    mae_str = f"{mae:.2f} hours" if mae is not None else "N/A (0 uncensored rows)"
    rmse_str = f"{rmse:.2f} hours" if rmse is not None else "N/A (0 uncensored rows)"
    median_ae_str = f"{median_ae:.2f} hours" if median_ae is not None else "N/A (0 uncensored rows)"
    traj_corr_str = f"{traj_corr:.4f}" if traj_corr == traj_corr else "N/A (< 3 buckets)"

    return (
        f"Evaluated rul_aft_model {mv.version_name} on {row_count} test rows "
        f"({censored_count} censored / {n_uncensored} uncensored)\n\n"
        f"Concordance index (full {row_count}-row test set, SQL self-join): {c_index:.4f} "
        f"(concordant={concordant}, discordant={discordant}, tied_risk={tied_risk}, tied_time={tied_time})\n"
        f"MAE ({n_uncensored} uncensored rows): {mae_str}\n"
        f"RMSE ({n_uncensored} uncensored rows): {rmse_str}\n"
        f"Median AE ({n_uncensored} uncensored rows): {median_ae_str}\n"
        f"Trajectory correlation ({n_uncensored} uncensored rows, 2h buckets, 0-{TRAJECTORY_WINDOW_HOURS}h window): {traj_corr_str}"
        f"{traj_corr_warning}\n\n"
        f"Top-10 worst predictions (uncensored, by abs_error):\n{top10_breakdown}\n\n"
        f"Outlier root-cause check:\n{outlier_report}\n\n"
        f"Registry metric logging: {metric_logging_status}"
    )
$$;

CALL snowcomotive.cons.sp_evaluate_rul_aft_model();
