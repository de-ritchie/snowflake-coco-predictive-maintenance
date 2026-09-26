-- ============================================================================
-- 06c_evaluate_iso_model.sql — Model evaluation (IsolationForest, isolation_forest_model)
-- Traces to: FR-FS-05, LLD Module 5 §1/§3/§4, docs/05-Epics.md EPIC-RUL §4.4,
-- docs/designs/SH-46-rul-model-evaluation.md §16-§23, docs/designs/SH-22 (S-MODEL-2)
-- Jira: SH-46 (S-RUL-4, scope extension)
-- Status: Built (v1)
--
-- Must run immediately after 05_train_models.sql (step 7) -- isolation_forest_
-- model must already exist -- and strictly before run_dbt_training_dataset_rul()
-- (design doc §17). Has no dependency on feast.training_dataset_rul,
-- rul_aft_model, or cons.fct_anomaly_result (that table doesn't even exist
-- yet at this point in the pipeline -- it's built in the phase-2 dbt run,
-- step 8), which is exactly why this step can run this early.
--
-- Data source: feast.feast__training_dataset_iso WHERE dataset_split='test'
-- (the trailing 6-month window, re-scored directly via
-- mv.run(..., function_name="predict"/"decision_function") -- NO read of
-- cons.fct_anomaly_result anywhere in this script (design doc §18/§12
-- invariant 13). The 15-column SENSOR_COLS list and output_feature_0
-- convention mirror cons__fct_anomaly_result.sql's own MODEL(...)!predict()/
-- !decision_function() calls exactly (same column order), so this script's
-- re-scoring matches what production inference actually serves.
--
-- IMPORTANT column-casing finding (empirically verified, not in the design
-- doc's literal code sketch): unlike rul_aft_model (a raw xgboost.Booster
-- custom function with no signature validation, hence 06b_evaluate_rul_
-- model.sql's lowercase FEATURE_COLS works), isolation_forest_model's logged
-- signature expects UPPERCASE column names (e.g. VIBRATION_Z, not
-- vibration_z) -- passing lowercase columns to mv.run() raises a Data
-- Validation Error ("feature VIBRATION_Z does not exist in data"). This
-- script therefore selects/passes the sensor columns in their native
-- Snowpark-uppercase form to mv.run() specifically, then lowercases the
-- resulting pandas DataFrame afterward for all downstream processing (join
-- against RAW.CMMS_LOG, per-machine grouping, etc.) -- matching 06b's
-- lowercase convention for everything except the two mv.run() calls
-- themselves.
--
-- Metrics (per design doc §19/§20, logged via mv.set_metric on isolation_
-- forest_model's DEFAULT version):
--   - catch_rate_pct / median_lead_time_hours: per-maintenance-cycle
--     (previous CMMS event -> breakdown), scoped to breakdowns whose FULL
--     lookback cycle falls entirely inside the test-split window (cycles
--     spanning the train/test boundary are excluded from the denominator,
--     not silently scored as missed -- design doc §19/§12 invariant 11).
--   - precision_72h / recall_72h / false_positive_rate_72h: tick-level
--     labeling using is_anomaly (the !predict output already encoding the
--     platform's own -1/+1 decision), NOT a re-derived decision_function
--     threshold (design doc §20/§12 invariant 12).
--   - Per-machine catch-rate breakdown and missed-breakdown technician_notes
--     cross-check are diagnostic return-value output ONLY -- no table
--     created/written by this script (design doc §21/§12 invariant 14).
-- ============================================================================

CREATE OR REPLACE PROCEDURE snowcomotive.cons.sp_evaluate_isolation_forest_model()
RETURNS STRING
LANGUAGE PYTHON
RUNTIME_VERSION = '3.11'
PACKAGES = ('snowflake-snowpark-python', 'snowflake-ml-python', 'pandas', 'numpy')
HANDLER = 'evaluate'
AS
$$
from snowflake.ml.registry import Registry
from snowflake.snowpark.functions import col
import pandas as pd


# Native Snowpark-uppercase column order -- must match cons__fct_anomaly_
# result.sql's MODEL(...)!predict()/!decision_function() argument list
# exactly (design doc §18) and isolation_forest_model's logged signature
# (empirically confirmed uppercase, see file header).
SENSOR_COLS = [
    "vibration_z", "vibration_rolling_1h_z", "vibration_rolling_8h_z", "vibration_rolling_24h_z", "vibration_rolling_7d_z",
    "temperature_z", "temperature_rolling_1h_z", "temperature_rolling_8h_z", "temperature_rolling_24h_z", "temperature_rolling_7d_z",
    "rpm_z", "rpm_rolling_1h_z", "rpm_rolling_8h_z", "rpm_rolling_24h_z", "rpm_rolling_7d_z",
]
SENSOR_COLS_UPPER = [c.upper() for c in SENSOR_COLS]


def evaluate(session):
    registry = Registry(session=session, database_name="SNOWCOMOTIVE", schema_name="CONS")
    mv = registry.get_model("isolation_forest_model").default

    df = session.table("snowcomotive.feast.feast__training_dataset_iso") \
        .filter(col("dataset_split") == "test") \
        .select(SENSOR_COLS + ["equipment_id", "reading_ts"])
    test_pdf = df.to_pandas()
    row_count = len(test_pdf)
    if row_count == 0:
        return "SKIPPED: 0 rows in feast.training_dataset_iso WHERE dataset_split = 'test'"

    # --- Re-score directly via the model (§18) -- native uppercase columns
    # for mv.run() specifically (see file header finding); is_anomaly derived
    # from !predict's own -1/+1 decision, NOT a re-derived threshold on
    # decision_function (§12 invariant 12).
    predict_out = mv.run(test_pdf[SENSOR_COLS_UPPER], function_name="predict")
    test_pdf["is_anomaly"] = (predict_out["output_feature_0"].astype(int) == -1)
    score_out = mv.run(test_pdf[SENSOR_COLS_UPPER], function_name="decision_function")
    test_pdf["anomaly_score"] = score_out["output_feature_0"].astype(float)
    test_pdf.columns = [c.lower() for c in test_pdf.columns]

    # cutoff_ts derived from the test rows themselves, not re-derived from
    # feast__training_dataset_iso.sql's DATEADD expression independently
    # (§18) -- avoids a second copy of that boundary logic drifting from the
    # dbt model's actual SQL.
    cutoff_ts = test_pdf["reading_ts"].min()

    cmms = session.table("snowcomotive.raw.cmms_log").to_pandas()
    cmms.columns = [c.lower() for c in cmms.columns]
    cmms = cmms.sort_values(["equipment_id", "event_start_ts"]).reset_index(drop=True)

    # Breakdown set: event_start_ts > cutoff_ts (breakdown itself inside the
    # test window). Used for BOTH §19 (full-cycle catch-rate, after the
    # boundary-exclusion filter below) and §20 (72h precision/recall/FPR,
    # deliberately using this SAME pre-exclusion set per §20's explicit
    # "minor, accepted approximation" decision).
    breakdowns = cmms[(cmms["event_type"] == "BREAKDOWN") & (cmms["event_start_ts"] > cutoff_ts)].copy()

    # --- §19: per-cycle catch rate / lead time, scoped to cycles falling
    # entirely inside the test window (§12 invariant 11).
    cycle_records = []
    excluded_records = []
    for _, bd in breakdowns.iterrows():
        eq = bd["equipment_id"]
        bd_ts = bd["event_start_ts"]
        prior = cmms[(cmms["equipment_id"] == eq) & (cmms["event_start_ts"] < bd_ts)]
        cycle_start = prior["event_start_ts"].max() if not prior.empty else None
        if cycle_start is None or cycle_start < cutoff_ts:
            # Cycle straddles the train/test boundary (or has no prior CMMS
            # event at all) -- excluded from the catch-rate denominator
            # entirely, not silently scored as "missed" (§19).
            excluded_records.append({
                "equipment_id": eq, "breakdown_ts": bd_ts, "cycle_start_ts": cycle_start,
            })
            continue
        ticks = test_pdf[
            (test_pdf["equipment_id"] == eq)
            & (test_pdf["reading_ts"] >= cycle_start)
            & (test_pdf["reading_ts"] < bd_ts)
        ]
        anomalous = ticks[ticks["is_anomaly"]]
        caught = len(anomalous) > 0
        lead_time_hours = None
        if caught:
            first_anomalous_ts = anomalous["reading_ts"].min()
            lead_time_hours = (bd_ts - first_anomalous_ts).total_seconds() / 3600.0
        cycle_records.append({
            "equipment_id": eq, "cycle_start_ts": cycle_start, "breakdown_ts": bd_ts,
            "caught": caught, "lead_time_hours": lead_time_hours, "n_ticks": len(ticks),
            "technician_notes": bd["technician_notes"],
        })

    cyc_df = pd.DataFrame(cycle_records)
    included_count = len(cyc_df)
    excluded_count = len(excluded_records)
    if included_count == 0:
        return (
            f"SKIPPED: 0 breakdown cycles fell entirely inside the test window "
            f"(cutoff_ts={cutoff_ts}); {excluded_count} excluded as boundary-straddling."
        )

    caught_count = int(cyc_df["caught"].sum())
    catch_rate_pct = 100.0 * caught_count / included_count
    caught_lead_times = cyc_df.loc[cyc_df["caught"], "lead_time_hours"]
    median_lead_time_hours = float(caught_lead_times.median()) if len(caught_lead_times) > 0 else float("nan")

    # --- §21: per-machine breakdown (diagnostic return-value output only).
    per_machine = cyc_df.groupby("equipment_id").agg(
        n_cycles=("caught", "size"), n_caught=("caught", "sum"),
    )
    per_machine["catch_rate_pct"] = 100.0 * per_machine["n_caught"] / per_machine["n_cycles"]
    per_machine_lines = [f"{'equipment_id':16s} {'n_cycles':>9s} {'n_caught':>9s} {'catch_rate_pct':>15s}"]
    for eq, r in per_machine.iterrows():
        per_machine_lines.append(
            f"{eq:16s} {int(r['n_cycles']):>9d} {int(r['n_caught']):>9d} {r['catch_rate_pct']:>15.2f}"
        )
    per_machine_report = "\n".join(per_machine_lines)

    # --- §21: missed-breakdown technician_notes cross-check (diagnostic
    # only, mirrors §8's RUL outlier root-cause check).
    missed = cyc_df[~cyc_df["caught"]]
    if len(missed) > 0:
        missed_lines = []
        for _, r in missed.iterrows():
            missed_lines.append(
                f"  {r['equipment_id']:16s} breakdown_ts={r['breakdown_ts']}  notes: {r['technician_notes']}"
            )
        missed_report = "\n".join(missed_lines)
    else:
        missed_report = "  (none -- every included breakdown was caught)"

    excluded_lines = []
    for r in excluded_records:
        excluded_lines.append(
            f"  {r['equipment_id']:16s} breakdown_ts={r['breakdown_ts']}  cycle_start_ts={r['cycle_start_ts']} (< cutoff_ts={cutoff_ts})"
        )
    excluded_report = "\n".join(excluded_lines) if excluded_lines else "  (none)"

    # --- §20: 72h precision/recall/false-positive-rate. Tick-level labeling
    # over ALL test rows using the SAME pre-exclusion breakdown set as §19
    # (per §20's explicit decision -- a 72h window is short enough that
    # boundary bleed from a breakdown very close to the cutoff is a minor,
    # accepted approximation). Labeled per equipment_id (a breakdown on one
    # machine must not mark another machine's ticks as positive).
    test_pdf["actual_positive"] = False
    for _, bd in breakdowns.iterrows():
        eq = bd["equipment_id"]
        bd_ts = bd["event_start_ts"]
        window_start = bd_ts - pd.Timedelta(hours=72)
        mask = (
            (test_pdf["equipment_id"] == eq)
            & (test_pdf["reading_ts"] >= window_start)
            & (test_pdf["reading_ts"] < bd_ts)
        )
        test_pdf.loc[mask, "actual_positive"] = True

    is_anom = test_pdf["is_anomaly"]
    actual_pos = test_pdf["actual_positive"]
    tp = int((is_anom & actual_pos).sum())
    fp = int((is_anom & ~actual_pos).sum())
    fn = int((~is_anom & actual_pos).sum())
    tn = int((~is_anom & ~actual_pos).sum())
    precision_72h = tp / (tp + fp) if (tp + fp) > 0 else float("nan")
    recall_72h = tp / (tp + fn) if (tp + fn) > 0 else float("nan")
    false_positive_rate_72h = fp / (fp + tn) if (fp + tn) > 0 else float("nan")

    # --- §22: Registry metric logging -- same confirmed-working API as
    # §9/§14, re-verified independently for this model (empirically
    # confirmed OK on this account/model version during development, per
    # this file's design doc). Guarded so a future account/version drift
    # degrades to a documented finding rather than an unhandled failure.
    metrics = {
        "catch_rate_pct": float(catch_rate_pct),
        "median_lead_time_hours": float(median_lead_time_hours),
        "precision_72h": float(precision_72h),
        "recall_72h": float(recall_72h),
        "false_positive_rate_72h": float(false_positive_rate_72h),
    }
    try:
        for name, value in metrics.items():
            mv.set_metric(name, value)
        metric_logging_status = f"OK -- logged {list(metrics.keys())} on version {mv.version_name}"
    except Exception as e:
        metric_logging_status = f"FAILED -- {type(e).__name__}: {e}"

    return (
        f"Evaluated isolation_forest_model {mv.version_name} on {row_count} test rows "
        f"(cutoff_ts={cutoff_ts})\n\n"
        f"Catch rate / lead time (per-cycle, test-window-scoped, §19):\n"
        f"  catch_rate_pct = {catch_rate_pct:.2f}% ({caught_count}/{included_count} cycles caught)\n"
        f"  median_lead_time_hours = {median_lead_time_hours:.2f}\n"
        f"  excluded_count (boundary-straddling cycles) = {excluded_count}\n"
        f"  excluded cycles:\n{excluded_report}\n\n"
        f"Precision / recall / false-positive-rate (72h window, §20):\n"
        f"  TP={tp} FP={fp} FN={fn} TN={tn}\n"
        f"  precision_72h = {precision_72h:.4f}\n"
        f"  recall_72h = {recall_72h:.4f}\n"
        f"  false_positive_rate_72h = {false_positive_rate_72h:.4f}\n\n"
        f"Per-machine catch-rate breakdown (§21):\n{per_machine_report}\n\n"
        f"Missed-breakdown technician_notes cross-check (§21):\n{missed_report}\n\n"
        f"Registry metric logging: {metric_logging_status}"
    )
$$;

CALL snowcomotive.cons.sp_evaluate_isolation_forest_model();
