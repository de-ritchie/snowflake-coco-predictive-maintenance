# SH-2-13 — Thin Snowpark generator (S-DATA-1)

Status: **Frozen** — confirmed by user 2026-08-29
Epic: EPIC-SKELETON (P0) §4.2 "Data generation — thin"
Traces to: [02-FRD.md](../02-FRD.md) FR-DG-12 (thin invocation only), [04-2-LLD.md](../04-2-LLD.md) Module 2 (full algorithm — this story implements a deliberately small slice, see §5 below), [04-1-LLD.md](../04-1-LLD.md) Module 1 (RAW table DDL, schema must match exactly)

---

## 1. Problem / scope

EPIC-SKELETON needs `RAW.SENSOR_READING` to have real rows in Snowflake *before any dbt model is written* (§4.2 Definition of Done). The full 3-year + 8-week, crew-capacity-constrained, 5-failure-mode, multi-machine generator (Module 2, EPIC-FULLDATA/S-DATA-3 through S-DATA-10) is a separate, later story — not in scope here. This story's only job is to produce **just enough rows, with the exact right schema**, to let the dbt project (S-DBT-1 onward) be scaffolded and exercised end-to-end.

This design covers **S-DATA-1 only** (generate + write Parquet). Uploading to the Stage and `COPY INTO`-ing Raw tables is **S-DATA-2**, a separate story, out of scope here.

---

## 2. Agreed scope

| Dimension | Decision |
|---|---|
| Machines | **1 machine: CNC Boring** only (baseline: vibration 2.5/0.3 mm/s, temp 45/3 °C, RPM 2400/150, throughput 15 u/hr — Module 2 §2) |
| Date range | **3 weeks**, explicit `--start-date`/`--end-date` CLI args (not just `--num-days` from today) |
| Calendar | **Working-days-only** (Module 2 §1: 5-day week, Mon–Fri, 3×8hr shifts = 24 operating hr/day on working days). Weekends produce no ticks. No holiday table, no PM/idle-chance mechanics (those stay deferred — see §5). |
| Failure mode | **Bearing wear only** (Module 2 §3: vibration sensitivity 0.8, temperature sensitivity 0.6, RPM sensitivity 0.1) — one single maintenance cycle for the whole run, no cycle resets, no restoration events |
| Decay formula | Module 2 §4's real formula and real Weibull params as-is: `T_fail ~ Weibull(shape=2.0, scale=λ_boring)` (λ in the 500–800h range), `H_s(t) = 1 - sensitivity_s * (t/T_fail)^2`, `reading_s(t) = baseline_mean_s + (1 - H_s(t)) * amplitude_s + noise_s`, `amplitude_s = 4 * baseline_std_s`. No compression/tuning override — whatever the 3-week working-days window shows (possibly no visible degradation, possibly a full cycle) is accepted as-is for this thin pass. |
| Sensors | All 3 sensors on CNC Boring (vibration, temperature, RPM) — long format, one row per `(equipment_id, reading_ts, sensor_type)`, matching `RAW.SENSOR_READING` grain |
| Cadence | Locked 15-minute cadence (FR-DG-01a), working hours only per calendar above |
| CMMS_LOG | **Not written.** No PM/breakdown events are simulated in this thin pass (no cycle-reset logic at all), so there is nothing to log. `RAW.CMMS_LOG` stays empty until EPIC-FULLDATA. |
| RAW.EQUIPMENT | **1 row only** — the CNC Boring machine. Not the full value-stream master list. |
| Right-censoring labels | **Not produced.** FR-DG-11's `y_lower`/`y_upper` labeling is a training-time concern (EPIC-RUL); this thin generator emits no such columns and does not persist internal `T_fail`/`t` state anywhere. |

---

## 3. Script structure

- **Standalone Python script**, not a stored procedure — run locally via `snow` CLI or plain `python`, opens its own `snowflake.snowpark.Session` from connection config (only needed if/when it touches Snowflake directly; per §4 below this thin version doesn't even need a live session since it writes local Parquet only — retained as a plain Python script using `pandas`/`numpy`, with the Snowpark session wiring stubbed for parity with the eventual full generator's interface).
- Entry point: single `main()` guarded by `if __name__ == "__main__":`, argument parsing via `argparse`.

**CLI arguments:**

| Arg | Type | Default | Meaning |
|---|---|---|---|
| `--seed` | int | `42` | RNG seed, configurable — reproducible by default, overridable |
| `--start-date` | date (`YYYY-MM-DD`) | required | First calendar day of the generation window |
| `--end-date` | date (`YYYY-MM-DD`) | required | Last calendar day (inclusive) |
| `--machine` | str | `CNC_BORING` | Which sensor-enabled machine to simulate — accepted as an arg for interface parity with the eventual full generator (FR-DG-12's "machine list"), even though this thin version is only exercised against CNC Boring |
| `--output-dir` | str | `./output` | Local directory Parquet files are written to |

---

## 4. Output

Two local Parquet files, written to `--output-dir`, schema matching `RAW.EQUIPMENT` / `RAW.SENSOR_READING` (Module 1) **exactly** — column names and types, so S-DATA-2's `COPY INTO` needs no transformation:

**`equipment.parquet`** — 1 row:

| Column | Type | Value for this run |
|---|---|---|
| `equipment_id` | string | e.g. `"CNC_BORING"` |
| `equipment_name` | string | `"CNC Boring"` |
| `line_name` | string | Caliper line name per Module 1/BRD §5.1 |
| `product_id` | string | Caliper product id |
| `variant` | string | e.g. `"EV"` (or whichever variant Module 1's `DIM_PRODUCT` uses as CNC Boring's default) |
| `is_sensor_enabled` | boolean | `true` |
| `throughput_units_per_hour` | number | `15` (Module 2 §2) |
| `commissioned_ts` | timestamp | Arbitrary fixed date before `--start-date` |

**`sensor_reading.parquet`** — one row per `(reading_ts, sensor_type)` tick, long format:

| Column | Type | Notes |
|---|---|---|
| `reading_id` | string | Generated unique id (e.g. UUID or sequential) |
| `equipment_id` | string | `"CNC_BORING"`, FK to `equipment.parquet` |
| `reading_ts` | timestamp | 15-min ticks, working days only, within `[start-date, end-date]` |
| `sensor_type` | string | one of `VIBRATION` / `TEMPERATURE` / `RPM` |
| `reading_value` | number | Per Module 2 §4's formula |

No `RAW.CMMS_LOG` file is produced by this script.

---

## 5. Explicitly OUT of scope (deferred to EPIC-FULLDATA / EPIC-RUL)

Developer-agent must **not** build any of the following into this story — they are deliberate simplifications, not oversights:

- Multiple machines / multi-machine correlated wear (Module 2 §2's other 2 machines: CNC Milling, CNC Horizontal)
- Crew-capacity contention mechanism (Module 2 §5/§5a — weekly/weekend slot logic, background plant draw, priority tie-break by order volume)
- The other 4 failure modes (tool/insert wear, coolant/thermal, servo/RPM instability, unexplainable stoppage) and per-cycle mode drawing
- Multiple maintenance cycles / cycle resets / PM or breakdown restoration events
- `RAW.CMMS_LOG` generation entirely
- Order-derived utilization (Module 2 §7 — `required_hours_week`/`available_hours_week`/`operating_hours_week`); this thin version uses a flat working-days calendar instead
- Holiday calendar, residual idle-chance mechanic (Module 2 §1)
- Full 3-year history + 8-week look-ahead timeline; order series generation (`RAW.SALES_ORDER`); inventory/spare-parts generation (`RAW.INVENTORY_FG_SNAPSHOT`, `RAW.SPARE_PART_SNAPSHOT`); `RAW.CALENDAR`
- 30-day sensor drip-feed / held-back tick files (FR-PL-04b) — this script produces one bulk Parquet file, no live-tick split
- Right-censoring labels (`y_lower`/`y_upper`, FR-DG-11) — no training pipeline consumes this data yet
- Uploading Parquet to the Stage or `COPY INTO` — that is S-DATA-2's scope, not this story's

---

## 6. Open items for Developer-agent

- Exact `equipment_id` naming convention, `line_name`/`product_id`/`variant` values for CNC Boring — should match whatever Module 1/`DIM_PRODUCT` values Developer-agent finds already in use elsewhere in the repo (none exist yet as of this design; Developer-agent should pick sensible values consistent with BRD §5.1's Caliper-line naming and keep them consistent with what S-DBT-x's models will expect).
- Exact λ (Weibull scale) value for CNC Boring within Module 2's 500–800h range — pick one, document the choice inline as a constant.
- `reading_id` generation scheme (UUID vs. sequential int) — either is fine, just be consistent.
