"""RUL model Impact Statement analysis -- precision/recall + revenue impact.

Consolidated, cleaned-up version of the exploratory work in
scratchpad/impact_analysis/ (scripts 01-08). This is the ONE script to run
and trust; the scratchpad folder is kept for history/exploration only.

====================================================================
METHODOLOGY (validated over several iterations -- see scratchpad/impact_
analysis/ for the dead ends that led here, if you want the full history)
====================================================================

RUL is the single standard signal (anomaly_score/is_anomaly are already
input features to the RUL model, so they're not OR-combined separately --
that would double-count the same information).

Ground truth / scope:
  - Only BREAKDOWN-ended cycles are scored against (PM-ended cycles are a
    successful scheduled intervention, not a failure -- nothing to "catch").
  - PM-ended cycles DO still contribute false-positive risk (if the model
    flags during a routine, healthy PM cycle, that's a real false alarm).
  - Computed at both ALL_HISTORY (full 3yr dataset) and TEST_ONLY (trailing
    6-month window, same cutoff convention as the training pipeline split).

Noise handling (REQUIRED -- raw tick-level thresholding chatters badly):
  - Median-smooth predicted_rul_hours with a trailing SMOOTHING_HOURS window.
  - Debounce the resulting flag: episodes within COOLDOWN_HOURS of each other
    merge into one; episodes shorter than MIN_ALARM_DURATION_HOURS are
    discarded as noise (nobody dispatches a technician for a 15-minute blip).
  - Without this, ~40-50% of raw "alerts" were single-tick noise blips that
    immediately self-corrected.

PART A -- Precision/Recall/FPR (tick-level, mirrors the IsolationForest
model's existing 06c_evaluate_iso_model.sql methodology for apples-to-apples
comparison): a fixed WINDOW_HOURS before a real breakdown defines ground-
truth positive ticks; predicted_rul_hours below a THRESHOLD defines flagged
ticks. Swept across window x threshold to find non-saturated operating
points. This answers "how good is the raw signal," independent of any
dispatch/debounce logic.

PART B -- Revenue impact (per-cycle, debounced): for each breakdown cycle,
look for the first debounced alert episode OVERLAPPING [breakdown_ts -
WINDOW, breakdown_ts). Caught -> TP (credit = full event $ impact). Never
alerted -> FN (that breakdown's $ impact is NOT a new cost caused by the
model -- it would happen either way -- reported separately, not subtracted
from "net savings"). Any OTHER debounced episode in the same equipment's
history that ends strictly BEFORE the window starts (zero overlap with the
real warning -- avoids double-counting an early-starting true alert as also
a separate false alarm) -> FP, priced as a PM-length (2.33h avg, from real
PM event durations) stoppage using that equipment's own throughput/margin/
order-demand (the only grounded cost figure available -- there is no
labor_rate/dispatch_cost column anywhere in this dataset).

Two "net value" framings (deliberately not collapsed into one number):
  - net_savings_vs_baseline = TP - FP          (financially correct: FN is
    a wash vs. no-model baseline, not a new cost)
  - net_score_all_subtracted = TP - FP - FN    (stricter, reward-style score
    across all outcomes; use for a more conservative narrative)
Both are also expressed as a % of the ACTUAL total lost revenue (from
cons__fct_historical_dollar_impact) -- "how much of what we actually lost
could this model have recovered."

KNOWN LIMITATION: all dollar figures are LOST PRODUCTION MARGIN only. There
is no repair/parts/labor cost data anywhere in this dataset, so the real-
world premium of unplanned (corrective) vs. planned (preventive) repair
cost is NOT included. If you want that in the Impact Statement, it would
have to be an explicitly-labeled external assumption (e.g. a commonly-cited
corrective-vs-preventive cost multiplier), not something derived from this
data.

CAVEAT ON 72h WINDOW: at window=threshold=72h, catch rate saturates to
100% for both scopes -- the model's recall at that operating point (~91%
tick-level) is high enough that "at least one qualifying episode somewhere
in a 72h window" is nearly guaranteed regardless of real skill. Treat 72h
results as directional context only; 24h and 48h are the trustworthy,
non-saturated operating points. 48h is the recommended headline (95% catch
rate, ~36h median lead time, not saturated).

Run: uv run python impact_analysis/rul_impact_analysis.py
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from common import get_logger, query_df  # noqa: E402

log = get_logger("rul_impact_analysis")
DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)

# -- shared noise-handling constants (validated in scratchpad/impact_analysis/04,05) --
SMOOTHING_HOURS = 1.0
COOLDOWN_HOURS = 4.0
MIN_ALARM_DURATION_HOURS = 1.0
PM_AVG_DURATION_HOURS = 2.330648  # AVG(duration_hours) from cons__fct_maintenance_event WHERE event_type='PM'

# -- Part A: precision/recall/FPR sweep --
PR_WINDOW_HOURS = [24, 48, 72]
PR_RUL_THRESHOLDS = [12, 24, 48, 72, 96, 168, 336, 504, 672]

# -- Part B: revenue impact sweep (threshold == window at each point) --
REVENUE_WINDOWS_HOURS = [24, 48, 72]


# ============================================================
# Shared helpers
# ============================================================
def build_cycles(events: pd.DataFrame, predictions: pd.DataFrame) -> pd.DataFrame:
    """Cycle boundaries = consecutive maintenance events per equipment
    (mirrors feast__spine_maintenance_cycle.sql's logic). Keeps the raw
    prediction ticks for each cycle so downstream code can run the debounce
    engine independently per cycle."""
    all_cycles = []
    for eq, eq_events in events.sort_values("event_start_ts").groupby("equipment_id"):
        eq_events = eq_events.reset_index(drop=True)
        eq_preds = predictions[predictions["equipment_id"] == eq].sort_values("reading_ts").reset_index(drop=True)
        if eq_preds.empty:
            continue
        cycle_start = eq_preds["reading_ts"].min() - pd.Timedelta(minutes=1)
        for _, ev in eq_events.iterrows():
            cycle_end = ev["event_end_ts"]
            ticks = eq_preds[(eq_preds["reading_ts"] > cycle_start) & (eq_preds["reading_ts"] <= cycle_end)]
            all_cycles.append({
                "equipment_id": eq, "cycle_start_ts": cycle_start, "cycle_end_ts": cycle_end,
                "event_start_ts": ev["event_start_ts"], "event_type": ev["event_type"], "ticks": ticks,
            })
            cycle_start = cycle_end
    return pd.DataFrame(all_cycles)


def label_actual_positive(predictions: pd.DataFrame, events: pd.DataFrame, window_hours: float) -> pd.Series:
    """Ground truth: is this tick within `window_hours` of SOME real breakdown
    on the same equipment. Derived from real timestamps, not model output."""
    actual_positive = pd.Series(False, index=predictions.index)
    for _, bd in events.iterrows():
        mask = (
            (predictions["equipment_id"] == bd["equipment_id"])
            & (predictions["reading_ts"] >= bd["event_start_ts"] - pd.Timedelta(hours=window_hours))
            & (predictions["reading_ts"] < bd["event_start_ts"])
        )
        actual_positive.loc[mask] = True
    return actual_positive


def get_all_debounced_episodes(ticks: pd.DataFrame, threshold_hours: float) -> list[tuple]:
    """Median-smooth -> threshold -> cooldown-merge -> min-duration filter.
    Returns [(episode_start_ts, episode_end_ts, predicted_rul_at_start), ...]
    for every surviving episode in this tick set."""
    if ticks.empty:
        return []
    ticks = ticks.sort_values("reading_ts")
    smoothed = (
        ticks.set_index("reading_ts")["predicted_rul_hours"]
        .rolling(f"{SMOOTHING_HOURS}h", min_periods=1)
        .median()
    )
    is_flagged = smoothed < threshold_hours
    flagged_ts = smoothed.index[is_flagged.to_numpy()]
    if len(flagged_ts) == 0:
        return []
    gap_hours = pd.Series(flagged_ts).diff().dt.total_seconds() / 3600.0
    new_episode = gap_hours.isna() | (gap_hours > COOLDOWN_HOURS)
    episode_id = new_episode.cumsum()
    ep_df = pd.DataFrame({"ts": flagged_ts, "episode_id": episode_id.to_numpy()})
    raw_rul_by_ts = ticks.set_index("reading_ts")["predicted_rul_hours"]
    episodes = []
    for ep_id, ep_grp in ep_df.groupby("episode_id"):
        start_ts, end_ts = ep_grp["ts"].min(), ep_grp["ts"].max()
        if (end_ts - start_ts).total_seconds() / 3600.0 >= MIN_ALARM_DURATION_HOURS:
            episodes.append((start_ts, end_ts, float(raw_rul_by_ts.loc[start_ts])))
    return episodes


# ============================================================
# Part A: precision / recall / FPR sweep (tick-level)
# ============================================================
def run_precision_recall(predictions: pd.DataFrame, events: pd.DataFrame, cutoff_ts: pd.Timestamp) -> pd.DataFrame:
    log.info("")
    log.info("=== PART A: precision/recall/FPR sweep (tick-level, raw signal) ===")
    results = []
    for window in PR_WINDOW_HOURS:
        actual_pos = label_actual_positive(predictions, events, window)
        for threshold in PR_RUL_THRESHOLDS:
            is_flagged = predictions["predicted_rul_hours"] < threshold
            for scope_label, scope_mask in [
                ("ALL_HISTORY", pd.Series(True, index=predictions.index)),
                ("TEST_ONLY", predictions["reading_ts"] > cutoff_ts),
            ]:
                flagged = is_flagged[scope_mask]
                pos = actual_pos[scope_mask]
                tp = int((flagged & pos).sum())
                fp = int((flagged & ~pos).sum())
                fn = int((~flagged & pos).sum())
                tn = int((~flagged & ~pos).sum())
                precision = tp / (tp + fp) if (tp + fp) > 0 else float("nan")
                recall = tp / (tp + fn) if (tp + fn) > 0 else float("nan")
                fpr = fp / (fp + tn) if (fp + tn) > 0 else float("nan")
                f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else float("nan")
                results.append({
                    "window_hours": window, "rul_threshold_hours": threshold, "scope": scope_label,
                    "tp": tp, "fp": fp, "fn": fn, "tn": tn,
                    "precision": round(precision, 4), "recall": round(recall, 4),
                    "fpr": round(fpr, 4), "f1": round(f1, 4) if f1 == f1 else None,
                })
    results_df = pd.DataFrame(results)
    results_df.to_csv(DATA_DIR / "precision_recall_sweep.csv", index=False)

    for window in PR_WINDOW_HOURS:
        for scope_label in ["ALL_HISTORY", "TEST_ONLY"]:
            sub = results_df[(results_df["window_hours"] == window) & (results_df["scope"] == scope_label)].dropna(subset=["f1"])
            if sub.empty:
                continue
            best = sub.loc[sub["f1"].idxmax()]
            log.info(
                f"  window={window:3d}h scope={scope_label:11s} best_threshold={best['rul_threshold_hours']:4.0f}h "
                f"precision={best['precision']:.3f} recall={best['recall']:.3f} fpr={best['fpr']:.4f} f1={best['f1']:.3f}"
            )
    log.info(f"Full sweep saved to {DATA_DIR}/precision_recall_sweep.csv")
    return results_df


# ============================================================
# Part B: revenue impact (per-cycle, debounced TP/FP/FN)
# ============================================================
def run_revenue_impact(
    cycles: pd.DataFrame, dollar_impact: pd.DataFrame, equipment: pd.DataFrame,
    product: pd.DataFrame, orders: pd.DataFrame, cutoff_ts: pd.Timestamp,
) -> pd.DataFrame:
    log.info("")
    log.info("=== PART B: revenue impact (per-cycle, debounced TP/FP/FN) ===")

    cycles = cycles.merge(dollar_impact, on=["equipment_id", "event_start_ts"], how="left")
    cycles["is_test"] = cycles["cycle_end_ts"] > cutoff_ts

    detail_rows = []
    for window in REVENUE_WINDOWS_HOURS:
        for _, cyc in cycles.iterrows():
            all_episodes = get_all_debounced_episodes(cyc["ticks"], threshold_hours=window)

            if cyc["event_type"] == "BREAKDOWN":
                window_start = cyc["event_start_ts"] - pd.Timedelta(hours=window)
                bd_ts = cyc["event_start_ts"]
                tp_episodes = [e for e in all_episodes if e[0] < bd_ts and e[1] >= window_start]
                is_tp = len(tp_episodes) > 0

                outcome = "TP" if is_tp else "FN"
                lead_time_hours = (bd_ts - tp_episodes[0][0]).total_seconds() / 3600.0 if is_tp else None
                detail_rows.append({
                    "window_hours": window, "equipment_id": cyc["equipment_id"], "event_start_ts": bd_ts,
                    "is_test": cyc["is_test"], "outcome": outcome, "lead_time_hours": lead_time_hours,
                    "event_realized_impact_usd": cyc["event_realized_impact_usd"],
                })
                # FP: episodes entirely finished before the window starts --
                # zero overlap with the real warning, so never double-counted
                # as part of the TP credit above.
                for ep_start, ep_end, rul_at_start in all_episodes:
                    if ep_end < window_start:
                        detail_rows.append({
                            "window_hours": window, "equipment_id": cyc["equipment_id"], "event_start_ts": ep_start,
                            "is_test": cyc["is_test"], "outcome": "FP", "lead_time_hours": None,
                            "event_realized_impact_usd": None,
                        })
            else:
                # PM-ended cycle: no breakdown ever happens, every episode is FP.
                for ep_start, ep_end, rul_at_start in all_episodes:
                    detail_rows.append({
                        "window_hours": window, "equipment_id": cyc["equipment_id"], "event_start_ts": ep_start,
                        "is_test": cyc["is_test"], "outcome": "FP", "lead_time_hours": None,
                        "event_realized_impact_usd": None,
                    })

    detail_df = pd.DataFrame(detail_rows)

    # Price FP episodes: PM-length stoppage x that equipment's own
    # throughput/margin/that week's order demand (same formula as real
    # breakdowns -- the only grounded cost figure this dataset supports).
    fp_df = detail_df[detail_df["outcome"] == "FP"].copy()
    fp_df = fp_df.merge(equipment, on="equipment_id", how="left")
    fp_df = fp_df.merge(product, on=["product_id", "variant"], how="left")
    fp_df["order_week"] = fp_df["event_start_ts"].dt.to_period("W").dt.start_time
    fp_df = fp_df.merge(orders, on=["product_id", "variant", "order_week"], how="left")
    fp_df["order_units"] = fp_df["order_units"].fillna(0.0)
    fp_df["fp_cost_usd"] = (
        fp_df[["throughput_units_per_hour", "order_units"]]
        .apply(lambda r: min(PM_AVG_DURATION_HOURS * r["throughput_units_per_hour"], r["order_units"]), axis=1)
        * fp_df["unit_margin"]
    )

    detail_df.to_csv(DATA_DIR / "revenue_impact_detail.csv", index=False)
    fp_df.to_csv(DATA_DIR / "revenue_impact_fp_priced.csv", index=False)

    summary_rows = []
    for window in REVENUE_WINDOWS_HOURS:
        for scope_label, is_test in [("ALL_HISTORY", None), ("TEST_ONLY", True)]:
            w_detail = detail_df[detail_df["window_hours"] == window]
            w_fp = fp_df[fp_df["window_hours"] == window]
            if is_test is not None:
                w_detail = w_detail[w_detail["is_test"]]
                w_fp = w_fp[w_fp["is_test"]]

            tp_rows = w_detail[w_detail["outcome"] == "TP"]
            fn_rows = w_detail[w_detail["outcome"] == "FN"]
            n_breakdowns = len(tp_rows) + len(fn_rows)
            catch_rate_pct = 100.0 * len(tp_rows) / n_breakdowns if n_breakdowns > 0 else float("nan")
            median_lead_time = float(tp_rows["lead_time_hours"].median()) if len(tp_rows) > 0 else None

            total_tp = float(tp_rows["event_realized_impact_usd"].sum())
            total_fn = float(fn_rows["event_realized_impact_usd"].sum())
            total_at_risk = total_tp + total_fn
            total_fp = float(w_fp["fp_cost_usd"].sum())

            net_savings = total_tp - total_fp
            net_all_subtracted = total_tp - total_fp - total_fn
            pct_net_savings = 100.0 * net_savings / total_at_risk if total_at_risk > 0 else float("nan")
            pct_all_subtracted = 100.0 * net_all_subtracted / total_at_risk if total_at_risk > 0 else float("nan")

            summary_rows.append({
                "window_hours": window, "scope": scope_label,
                "n_breakdowns": n_breakdowns, "n_caught": len(tp_rows),
                "catch_rate_pct": round(catch_rate_pct, 2),
                "median_lead_time_hours": round(median_lead_time, 2) if median_lead_time is not None else None,
                "total_dollar_at_risk": round(total_at_risk, 2),
                "tp_dollar_benefit": round(total_tp, 2),
                "fn_dollar_still_at_risk": round(total_fn, 2),
                "n_fp_episodes": len(w_fp), "fp_dollar_cost": round(total_fp, 2),
                "net_savings_vs_baseline": round(net_savings, 2),
                "net_score_all_subtracted": round(net_all_subtracted, 2),
                "pct_lost_revenue_recovered_net_savings": round(pct_net_savings, 2),
                "pct_lost_revenue_recovered_all_subtracted": round(pct_all_subtracted, 2),
            })
            r = summary_rows[-1]
            saturated_note = "  [SATURATED -- directional only]" if r["catch_rate_pct"] == 100.0 else ""
            log.info(f"  window={window}h{saturated_note}")
            log.info(
                f"    scope={scope_label:11s} catch_rate={r['catch_rate_pct']}% ({r['n_caught']}/{r['n_breakdowns']}) "
                f"median_lead_time={r['median_lead_time_hours']}h"
            )
            log.info(
                f"    TP=${r['tp_dollar_benefit']:,.0f}  FN=${r['fn_dollar_still_at_risk']:,.0f}  "
                f"FP=${r['fp_dollar_cost']:,.0f} ({r['n_fp_episodes']} episodes)"
            )
            log.info(
                f"    net_savings(TP-FP)=${r['net_savings_vs_baseline']:,.0f} ({r['pct_lost_revenue_recovered_net_savings']}% of "
                f"${r['total_dollar_at_risk']:,.0f} actual lost revenue)  |  "
                f"net_all_subtracted(TP-FP-FN)=${r['net_score_all_subtracted']:,.0f} ({r['pct_lost_revenue_recovered_all_subtracted']}%)"
            )

    summary_df = pd.DataFrame(summary_rows)
    summary_df.to_csv(DATA_DIR / "revenue_impact_summary.csv", index=False)
    log.info(f"Summary saved to {DATA_DIR}/revenue_impact_summary.csv")
    return summary_df


# ============================================================
def main() -> None:
    log.info("=== RUL Impact Statement analysis (precision/recall + revenue impact) ===")

    predictions = query_df("""
        SELECT equipment_id, reading_ts, predicted_rul_hours
        FROM snowcomotive.cons.cons__fct_rul_prediction
    """)
    predictions["reading_ts"] = pd.to_datetime(predictions["reading_ts"])

    events_all = query_df("""
        SELECT equipment_id, event_type, event_start_ts, event_end_ts, duration_hours
        FROM snowcomotive.cons.cons__fct_maintenance_event
    """)
    events_all["event_start_ts"] = pd.to_datetime(events_all["event_start_ts"])
    events_all["event_end_ts"] = pd.to_datetime(events_all["event_end_ts"])
    events_all["duration_hours"] = events_all["duration_hours"].astype(float)
    events_breakdown = events_all[events_all["event_type"] == "BREAKDOWN"]

    dollar_impact = query_df("""
        SELECT equipment_id, event_start_ts, event_realized_impact_usd
        FROM snowcomotive.cons.cons__fct_historical_dollar_impact
    """)
    dollar_impact["event_start_ts"] = pd.to_datetime(dollar_impact["event_start_ts"])
    dollar_impact["event_realized_impact_usd"] = dollar_impact["event_realized_impact_usd"].astype(float)

    equipment = query_df("SELECT equipment_id, product_id, variant, throughput_units_per_hour FROM snowcomotive.cons.cons__dim_equipment")
    equipment["throughput_units_per_hour"] = equipment["throughput_units_per_hour"].astype(float)
    product = query_df("SELECT product_id, variant, unit_margin FROM snowcomotive.cons.cons__dim_product")
    product["unit_margin"] = product["unit_margin"].astype(float)
    orders = query_df("SELECT product_id, variant, order_week, order_units FROM snowcomotive.cons.cons__fct_order")
    orders["order_week"] = pd.to_datetime(orders["order_week"])
    orders["order_units"] = orders["order_units"].astype(float)

    max_ts = predictions["reading_ts"].max()
    cutoff_ts = max_ts - pd.DateOffset(months=6)
    log.info(
        f"Loaded {len(predictions):,} predictions, {len(events_all):,} events "
        f"({len(events_breakdown)} breakdowns), TEST_ONLY cutoff={cutoff_ts}"
    )

    run_precision_recall(predictions, events_breakdown, cutoff_ts)

    cycles = build_cycles(events_all, predictions)
    run_revenue_impact(cycles, dollar_impact, equipment, product, orders, cutoff_ts)

    log.info("")
    log.info("=== Done. All outputs in impact_analysis/data/*.csv ===")


if __name__ == "__main__":
    main()
