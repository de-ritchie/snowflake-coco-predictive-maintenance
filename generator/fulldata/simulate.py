"""Week-major simulation loop orchestrating calendar/orders/degradation/crew
capacity/CMMS/inventory into the full generator output (LLD SS9, design doc
SS8).
"""

import math
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

import numpy as np
import pandas as pd

from generator.fulldata import cmms, degradation, inventory, orders, sim_calendar
from generator.fulldata.crew_capacity import background_plant_draw, decide_pm_winner, is_pm_due
from generator.fulldata.machine_config import (
    CADENCE_MINUTES,
    MACHINES,
    NON_SENSOR_EQUIPMENT,
    SENSOR_TYPES,
    SPARE_PART_BY_MODE,
)
from generator.fulldata.sim_calendar import (
    is_maintenance_weekend,
    maintenance_weekend_date,
    week_start,
    working_days_in_week,
)

LIVE_WINDOW_DAYS = 30
TICK_HOURS = CADENCE_MINUTES / 60.0


@dataclass
class _MachineRuntimeState:
    t_hours: float
    mode: str
    t_fail_hours: float
    last_service_date: date
    sensor_health_start: dict[str, float] = field(default_factory=lambda: {s: 1.0 for s in SENSOR_TYPES})


def _new_cycle(rng: np.random.Generator, weibull_lambda_hours: float) -> tuple[str, float]:
    mode = degradation.draw_failure_mode(rng)
    t_fail_hours = degradation.draw_t_fail(rng, mode, weibull_lambda_hours)
    return mode, t_fail_hours


def _restore_pm(rng: np.random.Generator, state: _MachineRuntimeState) -> None:
    for sensor in SENSOR_TYPES:
        state.sensor_health_start[sensor] = rng.uniform(0.90, 0.95)


def _restore_breakdown(rng: np.random.Generator, state: _MachineRuntimeState, failed_mode: str) -> None:
    """Restoration table per LLD SS5: primary sensor (highest sensitivity for
    the mode that just failed) -> Uniform(0.90,0.95); other sensors with
    sensitivity > 0.3 -> Uniform(0.70,0.80); sensitivity <= 0.3 -> unchanged.

    Unexplainable mode has no sensitivity table (LLD SS3 shows no values for
    it) -- no hardware fault was identified, so no sensor is reset here
    (documented interpretation, not explicitly specified by the LLD).
    """
    from generator.fulldata.machine_config import FAILURE_MODES

    sensitivities = FAILURE_MODES[failed_mode]["sensitivity"]
    if sensitivities is None:
        return
    primary_sensor = max(sensitivities, key=sensitivities.get)
    for sensor in SENSOR_TYPES:
        if sensor == primary_sensor:
            state.sensor_health_start[sensor] = rng.uniform(0.90, 0.95)
        elif sensitivities[sensor] > 0.3:
            state.sensor_health_start[sensor] = rng.uniform(0.70, 0.80)
        # else: sensitivity <= 0.3 and not primary -- unchanged.


def run_simulation(
    rng: np.random.Generator,
    now: date,
    output_dir: str,
    historical_weeks: int = 156,
    lookahead_weeks: int = 8,
) -> dict[str, pd.DataFrame]:
    """Runs the full week-major simulation. Writes the trailing-30-day
    per-tick sensor files to `output_dir/live_ticks/` directly; returns the
    remaining bulk tables as DataFrames for the caller to write out.
    """
    import os

    base_monday = sim_calendar.run_start_monday(now)
    total_weeks = historical_weeks + lookahead_weeks

    historical_end_date = week_start(base_monday, historical_weeks + 1) - timedelta(days=1)
    live_start_date = historical_end_date - timedelta(days=LIVE_WINDOW_DAYS - 1)

    order_series = orders.generate_order_series(rng, total_weeks, historical_weeks)

    states: dict[str, _MachineRuntimeState] = {}
    for machine_id, cfg in MACHINES.items():
        mode, t_fail_hours = _new_cycle(rng, cfg["weibull_lambda_hours"])
        states[machine_id] = _MachineRuntimeState(
            t_hours=0.0, mode=mode, t_fail_hours=t_fail_hours, last_service_date=base_monday
        )

    machine_line = {m: cfg["line_name"] for m, cfg in MACHINES.items()}
    spare_parts_sim = inventory.SparePartsSimulator(list(MACHINES.keys()))

    cmms_rows: list[dict] = []
    sensor_rows_historical: list[dict] = []
    live_tick_buffer: dict[datetime, list[dict]] = {}
    spare_part_snapshot_rows: list[dict] = []

    for week_num in range(1, total_weeks + 1):
        week_monday = week_start(base_monday, week_num)
        is_lookahead = week_num > historical_weeks
        wdays = working_days_in_week(week_monday)
        num_working_days = len(wdays)

        order_units_by_line = {
            line_name: order_series[line_name][week_num - 1] for line_name in order_series
        }

        # --- Pass 1: Monday-morning PM decision ---
        weekday_pm_machine = None
        if not background_plant_draw(rng):
            due = [m for m in MACHINES if is_pm_due(week_monday, states[m].last_service_date)]
            if due:
                days_since_service = {m: (week_monday - states[m].last_service_date).days for m in MACHINES}
                weekday_pm_machine = decide_pm_winner(rng, due, machine_line, order_units_by_line, days_since_service)
        tentative_pm_day = wdays[0] if wdays else None

        if is_lookahead:
            # Crew-capacity bookkeeping only -- no sensor ticks (LLD SS9 step 3).
            if weekday_pm_machine and tentative_pm_day:
                state = states[weekday_pm_machine]
                duration_hours = float(rng.uniform(1, 3))
                start_ts = datetime.combine(tentative_pm_day, time.min)
                end_ts = start_ts + timedelta(hours=duration_hours)
                _restore_pm(rng, state)
                cmms_rows.append(
                    cmms.make_event(weekday_pm_machine, "PM", start_ts, end_ts, cmms.pick_pm_note(rng))
                )
                state.mode, state.t_fail_hours = _new_cycle(rng, MACHINES[weekday_pm_machine]["weibull_lambda_hours"])
                state.t_hours = 0.0
                state.last_service_date = tentative_pm_day
            spare_part_snapshot_rows.extend(spare_parts_sim.snapshot_week(week_num))
            continue

        # --- Required/operating hours for the week, evenly split across working days ---
        hours_per_day: dict[str, float] = {}
        for machine_id, cfg in MACHINES.items():
            smoothed = orders.smoothed_orders(order_series[cfg["line_name"]], week_num)
            required_hours = smoothed / cfg["throughput_units_per_hour"]
            available_hours = num_working_days * 24
            operating_hours_week = min(required_hours, available_hours)
            hours_per_day[machine_id] = operating_hours_week / num_working_days if num_working_days else 0.0

        weekday_pm_done = False

        # --- Pass 2: day-by-day tick simulation ---
        for d in wdays:
            for machine_id, cfg in MACHINES.items():
                state = states[machine_id]
                day_hours = hours_per_day[machine_id]

                if rng.random() < sim_calendar.RESIDUAL_IDLE_PROBABILITY:
                    day_hours = 0.0
                elif (
                    weekday_pm_machine == machine_id
                    and d == tentative_pm_day
                    and not weekday_pm_done
                ):
                    if state.t_fail_hours - state.t_hours <= TICK_HOURS:
                        # Breakdown is imminent within the next tick -- breakdown
                        # always wins per design doc SS8/LLD SS5 step 1. The
                        # tentative PM is forfeited for the whole week (slot not
                        # reused by anyone else); fall through to normal ticking
                        # so the breakdown-detection logic below fires it.
                        weekday_pm_done = True
                    else:
                        pm_duration = float(rng.uniform(1, 3))
                        day_hours = max(0.0, day_hours - pm_duration)
                        start_ts = datetime.combine(d, time.min)
                        end_ts = start_ts + timedelta(hours=pm_duration)
                        _restore_pm(rng, state)
                        cmms_rows.append(cmms.make_event(machine_id, "PM", start_ts, end_ts, cmms.pick_pm_note(rng)))
                        state.mode, state.t_fail_hours = _new_cycle(rng, cfg["weibull_lambda_hours"])
                        state.t_hours = 0.0
                        state.last_service_date = d
                        weekday_pm_done = True

                n_ticks = math.floor(day_hours / TICK_HOURS + 1e-9)
                day_start_ts = datetime.combine(d, time.min)
                is_live_day = d >= live_start_date

                for i in range(n_ticks):
                    reading_ts = day_start_ts + timedelta(minutes=CADENCE_MINUTES * i)
                    state.t_hours += TICK_HOURS
                    for sensor in SENSOR_TYPES:
                        mean, std = cfg["sensor_baselines"][sensor]
                        value = degradation.generate_reading(
                            rng, sensor, mean, std, state.mode,
                            state.sensor_health_start[sensor], state.t_hours, state.t_fail_hours,
                        )
                        row = {
                            "reading_id": str(uuid.uuid4()),
                            "equipment_id": machine_id,
                            "reading_ts": reading_ts,
                            "sensor_type": sensor,
                            "reading_value": value,
                        }
                        if is_live_day:
                            live_tick_buffer.setdefault(reading_ts, []).append(row)
                        else:
                            sensor_rows_historical.append(row)

                    if state.t_hours >= state.t_fail_hours:
                        breakdown_duration = float(rng.uniform(2, 8))
                        end_ts = reading_ts + timedelta(hours=breakdown_duration)
                        failed_mode = state.mode
                        _restore_breakdown(rng, state, failed_mode)
                        cmms_rows.append(
                            cmms.make_event(
                                machine_id, "BREAKDOWN", reading_ts, end_ts, cmms.pick_technician_note(rng, failed_mode)
                            )
                        )
                        part_name = SPARE_PART_BY_MODE.get(failed_mode)
                        if part_name:
                            spare_parts_sim.consume(machine_id, part_name, week_num)
                        state.mode, state.t_fail_hours = _new_cycle(rng, cfg["weibull_lambda_hours"])
                        state.t_hours = 0.0
                        state.last_service_date = d
                        break  # remainder of this day's budget is lost to the breakdown

        # --- Weekend pass ---
        if is_maintenance_weekend(week_num) and not background_plant_draw(rng):
            due_weekend = [m for m in MACHINES if is_pm_due(week_monday, states[m].last_service_date)]
            if due_weekend:
                days_since_service = {m: (week_monday - states[m].last_service_date).days for m in MACHINES}
                weekend_pm_machine = decide_pm_winner(
                    rng, due_weekend, machine_line, order_units_by_line, days_since_service
                )
                if weekend_pm_machine:
                    state = states[weekend_pm_machine]
                    weekend_date = maintenance_weekend_date(week_monday)
                    duration_hours = float(rng.uniform(1, 3))
                    start_ts = datetime.combine(weekend_date, time.min)
                    end_ts = start_ts + timedelta(hours=duration_hours)
                    _restore_pm(rng, state)
                    cmms_rows.append(
                        cmms.make_event(weekend_pm_machine, "PM", start_ts, end_ts, cmms.pick_pm_note(rng))
                    )
                    state.mode, state.t_fail_hours = _new_cycle(rng, MACHINES[weekend_pm_machine]["weibull_lambda_hours"])
                    state.t_hours = 0.0
                    state.last_service_date = weekend_date

        spare_part_snapshot_rows.extend(spare_parts_sim.snapshot_week(week_num))

    # --- Write trailing-30-day per-tick live files ---
    live_ticks_dir = os.path.join(output_dir, "live_ticks")
    os.makedirs(live_ticks_dir, exist_ok=True)
    for reading_ts, rows in live_tick_buffer.items():
        file_name = f"reading_{reading_ts.strftime('%Y-%m-%dT%H-%M-%S')}.parquet"
        pd.DataFrame(rows).to_parquet(
            os.path.join(live_ticks_dir, file_name), index=False, use_deprecated_int96_timestamps=True
        )

    # --- Equipment master ---
    equipment_rows = []
    commissioned_ts = datetime.combine(base_monday, time.min)
    for machine_id, cfg in MACHINES.items():
        equipment_rows.append(
            {
                "equipment_id": machine_id,
                "equipment_name": cfg["equipment_name"],
                "line_name": cfg["line_name"],
                "product_id": cfg["product_id"],
                "variant": cfg["variant"],
                "is_sensor_enabled": True,
                "throughput_units_per_hour": cfg["throughput_units_per_hour"],
                "commissioned_ts": commissioned_ts,
            }
        )
    for stage in NON_SENSOR_EQUIPMENT:
        equipment_rows.append({**stage, "is_sensor_enabled": False, "commissioned_ts": commissioned_ts})
    equipment_df = pd.DataFrame(equipment_rows)

    # --- Sales orders ---
    sales_order_rows = []
    for line_name, (product_id, variant) in orders.PRODUCTS.items():
        for week_num in range(1, total_weeks + 1):
            sales_order_rows.append(
                {
                    "order_week": datetime.combine(week_start(base_monday, week_num), time.min),
                    "product_id": product_id,
                    "variant": variant,
                    "order_units": round(order_series[line_name][week_num - 1]),
                }
            )
    sales_order_df = pd.DataFrame(sales_order_rows)

    fg_snapshot_df = inventory.generate_fg_snapshot(order_series, historical_weeks, lookahead_weeks, base_monday)
    fg_snapshot_df["snapshot_week"] = fg_snapshot_df["snapshot_week"].apply(lambda d: datetime.combine(d, time.min))

    spare_part_df = pd.DataFrame(spare_part_snapshot_rows)
    parts_per_week = spare_parts_sim.part_count
    spare_part_df.insert(
        0,
        "snapshot_week",
        [
            datetime.combine(week_start(base_monday, w), time.min)
            for w in range(1, total_weeks + 1)
            for _ in range(parts_per_week)
        ],
    )

    cmms_df = pd.DataFrame(cmms_rows)
    sensor_reading_df = pd.DataFrame(sensor_rows_historical)

    return {
        "equipment": equipment_df,
        "sensor_reading": sensor_reading_df,
        "cmms_log": cmms_df,
        "sales_order": sales_order_df,
        "inventory_fg_snapshot": fg_snapshot_df,
        "spare_part_snapshot": spare_part_df,
    }
