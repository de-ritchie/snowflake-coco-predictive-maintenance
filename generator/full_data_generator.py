"""Full data generator CLI entrypoint (EPIC-FULLDATA, SH-37/35/40/38/39).

Supersedes generator/thin_sensor_generator.py (S-DATA-1) as the real
generator; the thin file is left untouched as a reference/fallback.

Scope (frozen design doc: docs/designs/SH-37-35-40-38-39-full-data-generator-core.md):
  - 3 sensor-enabled machines, 5 failure-mode signatures, shared weekly
    crew-capacity contention, order-derived utilization, CMMS logging, and
    finished-goods/spare-part inventory snapshots.
  - Full 3-year history + 8-week look-ahead window, trailing 30 days split
    into per-tick live_ticks/ Parquet files for progressive release.
"""

import argparse
import os
from datetime import datetime

import numpy as np

from generator.fulldata.simulate import run_simulation

BULK_OUTPUT_FILES = [
    "equipment.parquet",
    "sensor_reading.parquet",
    "cmms_log.parquet",
    "sales_order.parquet",
    "inventory_fg_snapshot.parquet",
    "spare_part_snapshot.parquet",
]


def _reuse_dataset_exists(output_dir: str) -> bool:
    return all(os.path.exists(os.path.join(output_dir, f)) for f in BULK_OUTPUT_FILES)


def main() -> None:
    parser = argparse.ArgumentParser(description="Full data generator (EPIC-FULLDATA)")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--now", type=str, default=None, help="YYYY-MM-DD, default: today")
    parser.add_argument("--output-dir", type=str, default="./output")
    parser.add_argument(
        "--reuse-dataset-path",
        type=str,
        default=None,
        help="If provided and this path already has the expected output files, skip generation.",
    )
    args = parser.parse_args()

    if args.reuse_dataset_path and _reuse_dataset_exists(args.reuse_dataset_path):
        print(f"Reusing existing dataset at {args.reuse_dataset_path} -- skipping generation.")
        return

    now = datetime.strptime(args.now, "%Y-%m-%d").date() if args.now else datetime.now().date()
    rng = np.random.default_rng(args.seed)

    os.makedirs(args.output_dir, exist_ok=True)

    tables = run_simulation(rng, now, args.output_dir)

    for name, df in tables.items():
        path = os.path.join(args.output_dir, f"{name}.parquet")
        # use_deprecated_int96_timestamps=True: Snowflake's Parquet COPY INTO reader
        # misinterprets the newer INT64 TIMESTAMP logical-type unit annotation --
        # the older INT96 encoding is unambiguous and what Snowflake expects.
        df.to_parquet(path, index=False, use_deprecated_int96_timestamps=True)
        print(f"Wrote {len(df)} row(s) to {path}")


if __name__ == "__main__":
    main()
