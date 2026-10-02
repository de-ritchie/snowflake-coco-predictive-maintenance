-- ============================================================================
-- 06b_evaluate_rul_model.sql — Model evaluation (RUL AFT, rul_aft_model)
-- Traces to: FR-FS-05, LLD Module 5 §3, docs/05-Epics.md EPIC-RUL §4.4,
-- docs/designs/SH-46-rul-model-evaluation.md, docs/designs/SH-44-train-rul-aft-model.md,
-- docs/designs/SH-86-fix-rul-training-leakage.md
-- Jira: SH-46 (S-RUL-4), SH-86
-- Status: Built (v2 — SH-86 per-tick evaluation)
--
-- Must run in its own connector session strictly after 06_train_rul_model.sql
-- (needs the trained rul_aft_model version to exist as its Registry default)
-- and strictly before the phase-2 dbt run (per manage.py's updated sequence,
-- docs/designs/SH-46-rul-model-evaluation.md §2). Read-only against
-- feast.training_dataset_rul WHERE dataset_split='test' and the Registry's
-- rul_aft_model.default version -- does NOT retrain, re-log, or otherwise
-- mutate the model itself.
--
-- SH-86 changes:
--   - O(n log n) concordance index via Fenwick tree (replaces O(n^2) pairwise
--     loop — test set grows from ~19 to ~30k+ rows under per-tick grain).
--   - Trajectory-correlation metric (permanent regression guard against
--     inverted-trajectory models — the original bug's symptom).
--   - Per-row breakdown replaced with top-10 worst predictions (full listing
--     impractical at ~30k rows).
--   - _read_split includes reading_ts for consistency with training script.
--
-- Metrics:
--   - Concordance index (O(n log n) Fenwick tree implementation) on the FULL
--     test set (both censored and uncensored).
--   - MAE / RMSE / median absolute error on uncensored rows only.
--   - Trajectory correlation (Pearson, bucketed) on uncensored rows only.
--   - Top-10 worst predictions (by abs_error) for diagnosability.
--   - Outlier root-cause check on the single worst abs-error uncensored row.
--   - Registry metric logging (concordance_index, mae, rmse, median_ae,
--     trajectory_correlation).
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


class FenwickTree:
    """Point-update, prefix-sum BIT over [1..n]."""
    def __init__(self, n):
        self.n = n
        self.tree = [0] * (n + 1)

    def update(self, i, delta=1):
        while i <= self.n:
            self.tree[i] += delta
            i += i & (-i)

    def prefix_sum(self, i):
        s = 0
        while i > 0:
            s += self.tree[i]
            i -= i & (-i)
        return s


def _concordance_index_censored(event_observed, event_time, estimate):
    """O(n log n) concordance index with exact same comparability rules as the
    O(n^2) brute-force version it replaces (SH-86).

    Comparability rules (right-censoring, unchanged from SH-46 §4):
      - both uncensored: always comparable
      - one uncensored (time_i) vs one censored (time_j): comparable only if
        the uncensored one's time is strictly earlier
      - both censored: never comparable

    Sweep direction: process rows in DESCENDING event_time order. For each
    uncensored row, all previously inserted rows (with strictly larger
    event_time) are comparable — regardless of whether the previously
    inserted row is censored or uncensored. Censored rows are never
    comparable to any previously inserted row (those have strictly larger
    time, and the rule requires the censored row's time to be strictly
    LARGER than the uncensored counterpart — which is the opposite of what
    the sweep order guarantees).

    Tied-time handling: rows sharing an identical event_time are processed
    as one atomic batch. Within a batch, only uncensored-uncensored pairs
    are comparable (both-uncensored rule), and since their times are equal,
    they are counted as tied_time (not concordant/discordant/tied_risk).
    All other within-batch pair types (uncensored-censored, both-censored)
    are not comparable.
    """
    import numpy as np

    n = len(event_time)
    if n == 0:
        return float("nan"), 0, 0, 0, 0

    # 1. Rank-compress estimate values to [1..m]
    sorted_unique = sorted(set(estimate))
    rank_map = {v: r + 1 for r, v in enumerate(sorted_unique)}
    m = len(sorted_unique)
    ranks = [rank_map[e] for e in estimate]

    # 2. Group rows into batches by event_time (descending)
    indices_by_time = {}
    for i in range(n):
        t = event_time[i]
        if t not in indices_by_time:
            indices_by_time[t] = []
        indices_by_time[t].append(i)

    sorted_times = sorted(indices_by_time.keys(), reverse=True)

    # 3. Sweep: descending event_time, Fenwick tree over estimate ranks
    bit = FenwickTree(m)
    total_inserted = 0
    concordant = 0
    discordant = 0
    tied_risk = 0
    tied_time = 0

    for t in sorted_times:
        batch = indices_by_time[t]

        # 3a. Query phase: only UNCENSORED rows in this batch query the BIT.
        #     All previously inserted rows have strictly larger event_time.
        #     An uncensored current row (smaller time) is comparable to ALL
        #     of them (both uncensored and censored previously inserted).
        for i in batch:
            if not event_observed[i]:
                continue  # censored row — not comparable to anything already inserted
            r = ranks[i]
            # Concordant: previously inserted rows with rank < r
            #   (lower risk estimate than current → correct, since current
            #   fails sooner and should have higher risk)
            c = bit.prefix_sum(r - 1)
            # Rows at exactly rank r: tied risk
            at_r = bit.prefix_sum(r) - c
            # Discordant: previously inserted rows with rank > r
            d = total_inserted - bit.prefix_sum(r)

            concordant += c
            tied_risk += at_r
            discordant += d

        # 3b. Within-batch tied_time: count of uncensored-uncensored pairs
        k_uncensored = sum(1 for i in batch if event_observed[i])
        tied_time += k_uncensored * (k_uncensored - 1) // 2

        # 3c. Insert ALL rows in this batch (uncensored and censored) into BIT
        for i in batch:
            bit.update(ranks[i])
        total_inserted += len(batch)

    denom = concordant + discordant + tied_risk
    c_index = (concordant + 0.5 * tied_risk) / denom if denom > 0 else float("nan")
    return c_index, concordant, discordant, tied_risk, tied_time


def _concordance_index_censored_bruteforce(event_observed, event_time, estimate):
    """O(n^2) brute-force concordance index — retained for manual re-verification
    if the Fenwick implementation is ever modified. Not called in the normal
    evaluate() path (O(n^2) is infeasible on the ~30k+ per-tick test set).
    Cross-checked and confirmed bit-identical to the Fenwick implementation on
    the pre-SH-86 24-row test set (2026-10-02): c_index=0.920354, concordant=208,
    discordant=18, tied_risk=0, tied_time=0. See design doc §7b."""
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


def _trajectory_correlation(y_lower, predicted_hours, event_observed):
    """Bucket uncensored test ticks by true remaining_hours (2h buckets,
    0–144h), compute mean predicted_hours per bucket, return Pearson
    correlation between bucket center and mean prediction.

    A healthy model produces strong positive correlation (higher true
    remaining time → higher predicted RUL). The original bug produced
    strong NEGATIVE correlation (predicted RUL rose toward failure).
    """
    import numpy as np

    mask = event_observed  # uncensored ticks only
    y = y_lower[mask]
    pred = predicted_hours[mask]

    if len(y) == 0:
        return float("nan")

    # 2h buckets from 0 to 144h (72 buckets)
    bucket_edges = np.arange(0, 146, 2)  # [0, 2, 4, ..., 144]
    bucket_centers = []
    bucket_means = []

    for lo, hi in zip(bucket_edges[:-1], bucket_edges[1:]):
        in_bucket = (y >= lo) & (y < hi)
        if in_bucket.sum() == 0:
            continue
        bucket_centers.append((lo + hi) / 2.0)
        bucket_means.append(pred[in_bucket].mean())

    if len(bucket_centers) < 3:
        return float("nan")  # not enough buckets for meaningful correlation

    return float(np.corrcoef(bucket_centers, bucket_means)[0, 1])


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
    pdf = df.select(FEATURE_COLS + ["equipment_id", "y_lower", "y_upper", "reading_ts"]).to_pandas()
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

    # --- Concordance index: FULL test set (both censored and uncensored) --
    # never filtered to uncensored-only (design doc §4/§12 invariant 3).
    event_observed = test_pdf["y_upper"].notna().values
    event_time = test_pdf["y_lower"].values.astype(float)
    c_index, concordant, discordant, tied_risk, tied_time = _concordance_index_censored(
        event_observed, event_time, -pred_hours,
    )

    # --- MAE / RMSE / median-AE: uncensored subset ONLY -- a censored row's
    # y_lower is a lower bound, not a true failure time, so it must never
    # enter these three metrics (design doc §5/§12 invariant 4).
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

    # --- Trajectory correlation (SH-86 §7c): permanent regression guard.
    import numpy as np
    traj_corr = _trajectory_correlation(
        test_pdf["y_lower"].values.astype(float),
        pred_hours,
        event_observed,
    )
    traj_corr_warning = ""
    if not np.isnan(traj_corr) and traj_corr < 0.5:
        traj_corr_warning = (
            f"\nWARNING: trajectory_correlation={traj_corr:.4f} (< 0.5 threshold) — "
            f"predicted RUL may not decrease toward failure as expected. "
            f"Investigate model behavior."
        )

    # --- Top-10 worst predictions (SH-86 §7d): replaces the full per-row
    # breakdown which is impractical at ~30k rows.
    top10_lines = [
        f"{'equipment_id':16s} {'y_lower':>10s} {'y_upper':>10s} {'censored':>9s} {'predicted_hours':>16s} {'abs_error':>10s}"
    ]
    if n_uncensored > 0:
        uncensored_pdf = test_pdf[uncensored_mask].copy()
        uncensored_pdf["abs_error"] = (uncensored_pdf["y_lower"] - uncensored_pdf["predicted_hours"]).abs()
        top10 = uncensored_pdf.nlargest(10, "abs_error")
        for _, r in top10.iterrows():
            top10_lines.append(
                f"{r['equipment_id']:16s} {r['y_lower']:>10.2f} {r['y_upper']:>10.2f} {'False':>9s} "
                f"{r['predicted_hours']:>16.2f} {r['abs_error']:>10.2f}"
            )
    else:
        top10_lines.append("(no uncensored rows — cannot compute abs_error)")
    top10_breakdown = "\n".join(top10_lines)

    # --- Outlier root-cause check (§8): dynamically identify whichever
    # uncensored test-split row has the LARGEST abs_error on THIS run's real
    # data. Compare that row's feature values against the TRAIN split's
    # per-feature distribution.
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

    # --- Registry metric logging (§9)
    metrics = {"concordance_index": float(c_index)}
    if mae is not None:
        metrics["mae"] = float(mae)
        metrics["rmse"] = float(rmse)
        metrics["median_ae"] = float(median_ae)
    if not np.isnan(traj_corr):
        metrics["trajectory_correlation"] = float(traj_corr)
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
    traj_corr_str = f"{traj_corr:.4f}" if not np.isnan(traj_corr) else "N/A (< 3 buckets)"

    return (
        f"Evaluated rul_aft_model {mv.version_name} on {row_count} test rows "
        f"({censored_count} censored / {uncensored_count} uncensored)\n\n"
        f"Concordance index (full {row_count}-row test set): {c_index:.4f} "
        f"(concordant={int(concordant)}, discordant={int(discordant)}, tied_risk={int(tied_risk)}, tied_time={int(tied_time)})\n"
        f"MAE ({uncensored_count} uncensored rows): {mae_str}\n"
        f"RMSE ({uncensored_count} uncensored rows): {rmse_str}\n"
        f"Median AE ({uncensored_count} uncensored rows): {median_ae_str}\n"
        f"Trajectory correlation ({uncensored_count} uncensored rows, 2h buckets): {traj_corr_str}"
        f"{traj_corr_warning}\n\n"
        f"Top-10 worst predictions (uncensored, by abs_error):\n{top10_breakdown}\n\n"
        f"Outlier root-cause check:\n{outlier_report}\n\n"
        f"Registry metric logging: {metric_logging_status}"
    )
$$;

CALL snowcomotive.cons.sp_evaluate_rul_aft_model();
