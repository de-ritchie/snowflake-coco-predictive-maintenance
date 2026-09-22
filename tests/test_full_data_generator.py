"""Structural invariant tests for the full data generator (design doc SS12/13).

Runs a small/fast generation (a few months, not the full 3 years) and asserts
the hard structural invariants that must never break -- not full statistical
distribution checks (those stay a manual/notebook sanity pass per the design
doc).
"""

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from generator.fulldata import degradation
from generator.fulldata.machine_config import FAILURE_MODES, MACHINES
from generator.fulldata.orders import PRODUCTS
from generator.fulldata.simulate import (
    LIVE_WINDOW_DAYS,
    _MachineRuntimeState,
    _restore_breakdown,
    _restore_pm,
    run_simulation,
)
from generator.fulldata.sim_calendar import is_holiday, is_working_day, run_start_monday


HISTORICAL_WEEKS = 12
LOOKAHEAD_WEEKS = 4
SEED = 42
NOW = date(2026, 9, 21)


@pytest.fixture(scope="module")
def tables_and_dir(tmp_path_factory):
    output_dir = tmp_path_factory.mktemp("fulldata_test_output")
    rng = np.random.default_rng(SEED)
    tables = run_simulation(
        rng, NOW, str(output_dir), historical_weeks=HISTORICAL_WEEKS, lookahead_weeks=LOOKAHEAD_WEEKS
    )
    return tables, output_dir


def test_invariant_1_at_most_one_pm_slot_per_week(tables_and_dir):
    """At most one weekday PM job across all 3 machines per week; at most one
    additional PM per designated maintenance weekend."""
    cmms = tables_and_dir[0]["cmms_log"]
    pm = cmms[cmms["event_type"] == "PM"].copy()
    if pm.empty:
        return
    pm["is_weekend"] = pm["event_start_ts"].dt.weekday >= 5
    pm["week"] = pm["event_start_ts"].dt.to_period("W-SUN")

    weekday_counts = pm[~pm["is_weekend"]].groupby("week").size()
    weekend_counts = pm[pm["is_weekend"]].groupby("week").size()
    assert weekday_counts.le(1).all()
    assert weekend_counts.le(1).all()


def test_invariant_2_breakdowns_are_logged_with_valid_duration(tables_and_dir):
    """Breakdowns always get immediate repair -- every BREAKDOWN event has a
    duration within the Uniform(2,8)h restoration window."""
    cmms = tables_and_dir[0]["cmms_log"]
    breakdowns = cmms[cmms["event_type"] == "BREAKDOWN"]
    if breakdowns.empty:
        return
    duration_hours = (breakdowns["event_end_ts"] - breakdowns["event_start_ts"]).dt.total_seconds() / 3600.0
    assert (duration_hours >= 2.0 - 1e-6).all()
    assert (duration_hours <= 8.0 + 1e-6).all()


def test_invariant_3_degradation_only_accrues_on_working_days(tables_and_dir):
    """No sensor reading ticks land on a non-working day (holiday or weekend)."""
    sensor_reading = tables_and_dir[0]["sensor_reading"]
    reading_dates = sensor_reading["reading_ts"].dt.date.unique()
    for d in reading_dates:
        assert is_working_day(d), f"Sensor reading found on non-working day {d}"
        assert not is_holiday(d)


def test_invariant_4_restoration_ranges(tables_and_dir):
    """PM -> all 3 sensors Uniform(0.90,0.95); breakdown -> primary sensor
    Uniform(0.90,0.95), other sensitivity>0.3 sensors Uniform(0.70,0.80),
    sensitivity<=0.3 sensors unchanged."""
    rng = np.random.default_rng(7)

    for _ in range(200):
        state = _MachineRuntimeState(t_hours=0.0, mode="BEARING_WEAR", t_fail_hours=100.0, last_service_date=NOW)
        _restore_pm(rng, state)
        for value in state.sensor_health_start.values():
            assert 0.90 <= value <= 0.95

    for mode, spec in FAILURE_MODES.items():
        if spec["sensitivity"] is None:
            continue
        sensitivities = spec["sensitivity"]
        primary_sensor = max(sensitivities, key=sensitivities.get)
        for _ in range(200):
            state = _MachineRuntimeState(
                t_hours=0.0, mode=mode, t_fail_hours=100.0, last_service_date=NOW,
                sensor_health_start={s: 0.42 for s in sensitivities},
            )
            _restore_breakdown(rng, state, mode)
            for sensor, sensitivity in sensitivities.items():
                value = state.sensor_health_start[sensor]
                if sensor == primary_sensor:
                    assert 0.90 <= value <= 0.95
                elif sensitivity > 0.3:
                    assert 0.70 <= value <= 0.80
                else:
                    assert value == 0.42  # unchanged


def test_invariant_5_unexplainable_mode_is_flat_baseline_no_precursor():
    """Unexplainable-mode readings show flat baseline+noise regardless of t --
    no ramp toward failure."""
    rng = np.random.default_rng(11)
    baseline_mean, baseline_std = 45.0, 3.0
    early = [
        degradation.generate_reading(rng, "TEMPERATURE", baseline_mean, baseline_std, "UNEXPLAINABLE", 1.0, 1.0, 400.0)
        for _ in range(2000)
    ]
    late = [
        degradation.generate_reading(
            rng, "TEMPERATURE", baseline_mean, baseline_std, "UNEXPLAINABLE", 1.0, 399.0, 400.0
        )
        for _ in range(2000)
    ]
    assert abs(np.mean(early) - baseline_mean) < 1.0
    assert abs(np.mean(late) - baseline_mean) < 1.0
    assert abs(np.mean(early) - np.mean(late)) < 1.0


def test_invariant_6_t_fail_draw_distribution_matches_mode():
    """Weibull(shape=2.0) for explainable modes; Exponential(mean=400h) for
    unexplainable -- never mixed up."""
    rng = np.random.default_rng(5)
    explainable_draws = [degradation.draw_t_fail(rng, "BEARING_WEAR", 650.0) for _ in range(5000)]
    unexplainable_draws = [degradation.draw_t_fail(rng, "UNEXPLAINABLE", 650.0) for _ in range(5000)]

    # Weibull(2.0, 650) mean ~= 650 * Gamma(1.5) ~= 576; Exponential(400) mean = 400.
    assert 500 < np.mean(explainable_draws) < 650
    assert 350 < np.mean(unexplainable_draws) < 450


def test_invariant_7_no_sensor_ticks_in_lookahead_weeks(tables_and_dir):
    """Sensor tick generation stops at 'now' -- no sensor rows (bulk or live)
    fall in the look-ahead window."""
    tables, output_dir = tables_and_dir
    base_monday = run_start_monday(NOW)
    from generator.fulldata.sim_calendar import week_start

    historical_end_date = week_start(base_monday, HISTORICAL_WEEKS + 1) - timedelta(days=1)

    sensor_reading = tables["sensor_reading"]
    assert (sensor_reading["reading_ts"].dt.date <= historical_end_date).all()

    live_files = list(output_dir.glob("live_ticks/*.parquet"))
    assert len(live_files) > 0
    for f in live_files:
        ts = pd.read_parquet(f)["reading_ts"].iloc[0]
        assert ts.date() <= historical_end_date


def test_invariant_8_no_censoring_flag_columns(tables_and_dir):
    """Right-censoring must be derivable purely from RAW.CMMS_LOG's
    event_type/timestamps + the window boundary -- no separate censored-flag
    column anywhere in the generated output."""
    tables = tables_and_dir[0]
    expected_columns = {
        "equipment": {
            "equipment_id", "equipment_name", "line_name", "product_id",
            "variant", "is_sensor_enabled", "throughput_units_per_hour", "commissioned_ts",
        },
        "sensor_reading": {"reading_id", "equipment_id", "reading_ts", "sensor_type", "reading_value"},
        "cmms_log": {"event_id", "equipment_id", "event_type", "event_start_ts", "event_end_ts", "technician_notes"},
        "sales_order": {"order_week", "product_id", "variant", "order_units"},
        "inventory_fg_snapshot": {"snapshot_week", "product_id", "variant", "fg_units_on_hand"},
        "spare_part_snapshot": {"snapshot_week", "equipment_id", "spare_part_name", "units_on_hand", "lead_time_days"},
    }
    for name, expected in expected_columns.items():
        assert set(tables[name].columns) == expected, f"{name} columns mismatch"


def test_invariant_11_only_primary_variants_generated(tables_and_dir):
    """RAW.SALES_ORDER/RAW.INVENTORY_FG_SNAPSHOT only ever contain
    (BRAKE_CALIPER, EV) and (ENGINE_HEAD, ICE) rows."""
    allowed = set(PRODUCTS.values())
    for table_name in ["sales_order", "inventory_fg_snapshot"]:
        df = tables_and_dir[0][table_name]
        actual = set(zip(df["product_id"], df["variant"]))
        assert actual <= allowed


def test_invariant_12_bulk_and_live_ticks_never_overlap(tables_and_dir):
    """The bulk sensor_reading.parquet and the live_ticks/ per-tick files
    never overlap in timestamp range."""
    tables, output_dir = tables_and_dir
    bulk_max_ts = tables["sensor_reading"]["reading_ts"].max()

    live_files = list(output_dir.glob("live_ticks/*.parquet"))
    assert len(live_files) > 0
    live_min_ts = min(pd.read_parquet(f)["reading_ts"].min() for f in live_files)

    assert bulk_max_ts < live_min_ts


def test_pm_breakdown_tie_break_breakdown_wins_and_slot_forfeited(tmp_path, monkeypatch):
    """Design doc SS8: if a breakdown would land the same day as the tentative
    weekday PM for the winning machine, breakdown wins and the PM slot is
    forfeited for the whole week (not reused by another machine)."""
    import generator.fulldata.simulate as simulate_mod

    monkeypatch.setattr(simulate_mod, "background_plant_draw", lambda rng: False)
    monkeypatch.setattr(simulate_mod, "is_pm_due", lambda week_monday, last_service_date: True)

    winner_machine = next(iter(MACHINES))
    monkeypatch.setattr(
        simulate_mod,
        "decide_pm_winner",
        lambda rng, due, machine_line, order_units_by_line, days_since_service: winner_machine,
    )

    # Force every machine's failure clock to fire on the very first tick, so
    # the tentative-PM winner's breakdown is imminent (t_fail_hours - t_hours
    # <= TICK_HOURS) on the tentative PM day (the first working day of week 1,
    # where t_hours is still 0 from cycle init).
    tiny_t_fail = simulate_mod.TICK_HOURS * 0.5
    monkeypatch.setattr(degradation, "draw_t_fail", lambda rng, mode, weibull_lambda_hours: tiny_t_fail)

    rng = np.random.default_rng(SEED)
    tables = run_simulation(
        rng, NOW, str(tmp_path), historical_weeks=1, lookahead_weeks=0
    )
    cmms = tables["cmms_log"]

    base_monday = run_start_monday(NOW)
    from generator.fulldata.sim_calendar import week_start, working_days_in_week

    week1_monday = week_start(base_monday, 1)
    tentative_pm_day = working_days_in_week(week1_monday)[0]

    winner_events = cmms[
        (cmms["equipment_id"] == winner_machine)
        & (cmms["event_start_ts"].dt.date == tentative_pm_day)
    ]
    assert not winner_events.empty
    assert set(winner_events["event_type"]) == {"BREAKDOWN"}

    # No PM anywhere in week 1 -- the slot was forfeited, not reused by any
    # other machine.
    week1_end = tentative_pm_day + timedelta(days=7)
    week1_pm = cmms[
        (cmms["event_type"] == "PM")
        & (cmms["event_start_ts"].dt.date >= week1_monday)
        & (cmms["event_start_ts"].dt.date < week1_end)
    ]
    assert week1_pm.empty


def test_reproducibility_same_seed_same_row_counts():
    """Running with the same seed twice produces identical row counts (no
    hidden nondeterminism)."""
    tables_a = run_simulation(
        np.random.default_rng(99), NOW, "/tmp/fulldata_repro_a", historical_weeks=6, lookahead_weeks=2
    )
    tables_b = run_simulation(
        np.random.default_rng(99), NOW, "/tmp/fulldata_repro_b", historical_weeks=6, lookahead_weeks=2
    )
    for name in tables_a:
        assert len(tables_a[name]) == len(tables_b[name])
