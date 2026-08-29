"""Thin sensor-data generator (S-DATA-1, SH-2-13).

Generates just enough RAW.EQUIPMENT / RAW.SENSOR_READING rows, with the exact
right schema (LLD Module 1), to let the dbt project be scaffolded and
exercised end-to-end -- before the full multi-machine, crew-capacity,
5-failure-mode generator (Module 2, EPIC-FULLDATA) is built.

Scope (frozen design doc: docs/designs/0 - SH-2-13-thin-snowpark-generator.md):
  - 1 machine (CNC Boring), working-days-only calendar, 15-min cadence.
  - 1 bearing-wear maintenance cycle for the whole run (Module 2 SS3/SS4).
  - No RAW.CMMS_LOG, no right-censoring labels, no Stage upload (S-DATA-2).

Output: equipment.parquet (1 row) and sensor_reading.parquet (long format),
written to --output-dir, schema matching LLD Module 1's RAW DDL exactly.
"""

import argparse
import uuid
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

# --- CNC Boring baselines (Module 2 SS2) ---
EQUIPMENT_ID = "CNC_BORING"
EQUIPMENT_NAME = "CNC Boring"
LINE_NAME = "Caliper"  # BRD SS5.1 Line A - Brake Caliper
PRODUCT_ID = "BRAKE_CALIPER"
VARIANT = "EV"
THROUGHPUT_UNITS_PER_HOUR = 15

SENSOR_BASELINES = {
    "VIBRATION": {"mean": 2.5, "std": 0.3},
    "TEMPERATURE": {"mean": 45.0, "std": 3.0},
    "RPM": {"mean": 2400.0, "std": 150.0},
}
NOISE_STD_FACTOR = 0.3  # Module 2 SS2: noise_std = 0.3 * baseline std

# --- Bearing-wear failure mode (Module 2 SS3) ---
BEARING_WEAR_SENSITIVITY = {
    "VIBRATION": 0.8,
    "TEMPERATURE": 0.6,
    "RPM": 0.1,
}

# Weibull scale (operating hours) for T_fail -- Module 2 SS4 says pick a value
# in the 500-800h range so cycles average a few weeks at 24 op-hr/day. 650h
# lands roughly mid-range: at 24 op-hr/day that's ~27 operating days, close to
# a full working-days month, giving a 3-week window a decent chance of
# showing partial drift without the whole cycle necessarily completing.
LAMBDA_BORING_HOURS = 650.0
WEIBULL_SHAPE = 2.0

CADENCE_MINUTES = 15
SHIFT_HOURS = 24  # 3x8hr shifts, working days only (Module 2 SS1)


def build_working_day_ticks(start_date: date, end_date: date) -> list[datetime]:
    """15-min ticks across the 24hr operating day, Mon-Fri only, inclusive range."""
    ticks: list[datetime] = []
    current_day = start_date
    while current_day <= end_date:
        if current_day.weekday() < 5:  # Mon=0 ... Fri=4
            day_start = datetime.combine(current_day, datetime.min.time())
            ticks_today = SHIFT_HOURS * 60 // CADENCE_MINUTES
            ticks.extend(
                day_start + timedelta(minutes=CADENCE_MINUTES * i)
                for i in range(ticks_today)
            )
        current_day += timedelta(days=1)
    return ticks


def health(sensor: str, t_hours: float, t_fail_hours: float) -> float:
    """H_s(t) = 1 - sensitivity_s * (t / T_fail)^2 -- Module 2 SS4."""
    sensitivity = BEARING_WEAR_SENSITIVITY[sensor]
    return 1.0 - sensitivity * (t_hours / t_fail_hours) ** 2


def sensor_reading(sensor: str, t_hours: float, t_fail_hours: float, rng: np.random.Generator) -> float:
    """reading_s(t) = baseline_mean_s + (1 - H_s(t)) * amplitude_s + noise_s -- Module 2 SS4."""
    baseline = SENSOR_BASELINES[sensor]
    amplitude = 4 * baseline["std"]
    h = health(sensor, t_hours, t_fail_hours)
    noise = rng.normal(0.0, NOISE_STD_FACTOR * baseline["std"])
    return baseline["mean"] + (1 - h) * amplitude + noise


def generate_equipment_frame(start_date: date) -> pd.DataFrame:
    commissioned_ts = datetime.combine(start_date, datetime.min.time()) - timedelta(days=365)
    return pd.DataFrame(
        [
            {
                "equipment_id": EQUIPMENT_ID,
                "equipment_name": EQUIPMENT_NAME,
                "line_name": LINE_NAME,
                "product_id": PRODUCT_ID,
                "variant": VARIANT,
                "is_sensor_enabled": True,
                "throughput_units_per_hour": THROUGHPUT_UNITS_PER_HOUR,
                "commissioned_ts": commissioned_ts,
            }
        ]
    )


def generate_sensor_reading_frame(start_date: date, end_date: date, rng: np.random.Generator) -> pd.DataFrame:
    ticks = build_working_day_ticks(start_date, end_date)
    t_fail_hours = rng.weibull(WEIBULL_SHAPE) * LAMBDA_BORING_HOURS

    rows = []
    operating_hours = 0.0
    tick_hours = CADENCE_MINUTES / 60.0
    for reading_ts in ticks:
        for sensor in SENSOR_BASELINES:
            rows.append(
                {
                    "reading_id": str(uuid.uuid4()),
                    "equipment_id": EQUIPMENT_ID,
                    "reading_ts": reading_ts,
                    "sensor_type": sensor,
                    "reading_value": sensor_reading(sensor, operating_hours, t_fail_hours, rng),
                }
            )
        operating_hours += tick_hours

    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Thin sensor-data generator (S-DATA-1)")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--start-date", type=str, required=True, help="YYYY-MM-DD")
    parser.add_argument("--end-date", type=str, required=True, help="YYYY-MM-DD")
    parser.add_argument("--machine", type=str, default="CNC_BORING")
    parser.add_argument("--output-dir", type=str, default="./output")
    args = parser.parse_args()

    start_date = datetime.strptime(args.start_date, "%Y-%m-%d").date()
    end_date = datetime.strptime(args.end_date, "%Y-%m-%d").date()
    rng = np.random.default_rng(args.seed)

    import os

    os.makedirs(args.output_dir, exist_ok=True)

    equipment_df = generate_equipment_frame(start_date)
    sensor_df = generate_sensor_reading_frame(start_date, end_date, rng)

    equipment_path = os.path.join(args.output_dir, "equipment.parquet")
    sensor_path = os.path.join(args.output_dir, "sensor_reading.parquet")
    equipment_df.to_parquet(equipment_path, index=False)
    sensor_df.to_parquet(sensor_path, index=False)

    print(f"Wrote {len(equipment_df)} equipment row(s) to {equipment_path}")
    print(f"Wrote {len(sensor_df)} sensor_reading row(s) to {sensor_path}")


if __name__ == "__main__":
    main()
