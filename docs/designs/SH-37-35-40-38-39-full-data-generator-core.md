# Design: Full Data Generator Core (EPIC-FULLDATA)

Status: **Frozen** — brainstormed interactively with the user, frozen on explicit signal ("Go ahead and freeze").
**Stories**: SH-37 (S-DATA-3), SH-35 (S-DATA-4), SH-40 (S-DATA-5), SH-38 (S-DATA-6), SH-39 (S-DATA-7)
**Branch**: `feature/SH-6-37-35-40-38-39-full-data-generator-core`
**Traces to**: [docs/04-2-LLD.md](../04-2-LLD.md) (Module 2 — Data Generation Algorithm, primary spec), [docs/01-BRD.md](../01-BRD.md) assumption 15 / §7 beat 3, [docs/02-FRD.md](../02-FRD.md) FR-DG-01 through 12, [docs/04-1-LLD.md](../04-1-LLD.md) (RAW table schemas)

These 5 stories are combined into one design because they're one integrated week-major simulation loop per LLD §9, not separable pieces — the crew-capacity constraint is shared state across all 3 machines within a week.

Supersedes `generator/thin_sensor_generator.py` (S-DATA-1) as the real generator; the thin file is left untouched as a reference/fallback, not deleted or modified.

---

## 1. Scope

Build the full synthetic data generator: 3 sensor-enabled machines, 5 failure-mode signatures, shared weekly crew-capacity contention, order-derived utilization, CMMS logging, and finished-goods/spare-part inventory snapshots — covering the full 3-year history + 8-week look-ahead window (LLD §8-9). Also includes splitting the trailing 30 days of sensor data into individual per-tick Parquet files for progressive release (LLD §9 step 5 / FR-PL-04b) — confirmed in scope for this story.

**Out of scope for this design** (explicitly deferred):
- §10's post-generation hand-patch mechanism itself — only a no-op seam is added now; the actual patch logic is S-DATA-10's job ("run + verify + patch").
- Stage upload / `COPY INTO` (S-DATA-2's job, already built for the thin generator's pattern — this generator only writes local Parquet, both bulk and per-tick).
- `--reuse-dataset-path`'s full wiring into `manage.py up` (S-OPS-SETUP-2) — the flag exists on this CLI now (short-circuit if the path already has the expected output files) but the ops-script integration is a separate story.

---

## 2. File layout

New package `generator/fulldata/`, alongside the existing flat `generator/thin_sensor_generator.py` (untouched):

```
generator/
  thin_sensor_generator.py        # existing, S-DATA-1, unchanged
  full_data_generator.py          # new CLI entrypoint (argparse, orchestration, Parquet writes)
  fulldata/
    __init__.py
    sim_calendar.py                # working-day/holiday calendar, week indexing (§1)
    machine_config.py              # 3 machines' baselines, failure-mode signatures (§2, §3)
    orders.py                      # order series generation + smoothing (§7)
    degradation.py                 # health/reading formulas, Weibull/Exponential draws (§4)
    crew_capacity.py                # weekly PM/breakdown resolution (§5)
    cmms.py                        # CMMS log rows + technician note templates (§5)
    inventory.py                    # FG + spare-part snapshot generation (§7, §5)
    patch.py                        # no-op stub seam for §10, filled in by S-DATA-10
    simulate.py                     # the week-major loop (§9) orchestrating all of the above
conftest.py                        # added post-freeze: not part of the original layout — required so
                                    # pytest resolves the top-level `generator` package import from
                                    # tests/test_full_data_generator.py; inserts the repo root onto sys.path
```

`sim_calendar.py` (not `calendar.py`) to avoid shadowing the stdlib module.

---

## 3. RNG strategy

- Single `np.random.default_rng(seed)` threaded through every module (matches the thin generator's existing pattern) — one master RNG passed down through `simulate.py` into each concern, not per-concern child seeds. Simpler, and reproducibility only needs to hold at the whole-run level for this project's purposes.
- CLI flags added now:
  - `--seed` (default `42`, matches the thin generator).
  - `--reuse-dataset-path`: if provided and the path already contains the expected output Parquet files, skip generation entirely and print a message — full pipeline wiring (S-OPS-SETUP-2 / `manage.py up`) is a separate story, but the flag's basic behavior is built now so that story only has to wire the CLI call, not add generator logic.
- `--start-date`/`--end-date` are dropped in favor of one `--now` (default: today) from which the 3-year-back start and 8-week-forward look-ahead end are derived — the LLD's week numbering (weeks 1-156 historical, 157-164 look-ahead) is anchored to "now," not to independently-specified start/end dates.

---

## 4. Output format

- Same Parquet + `use_deprecated_int96_timestamps=True` convention as the thin generator, for every timestamped output.
- Bulk files written to `--output-dir`:
  - `equipment.parquet` — all value-stream stages (not just the 3 sensor-enabled ones), per FR-DG-02. **Fixed 2026-09-22 (Reviewer-agent finding)**: `commissioned_ts` was originally computed as `base_monday - 365 days` (mirroring the thin generator's convention); now correctly set to `base_monday` (the true simulation/wear-tracking start), so right-censoring stays robustly derivable from `RAW.CMMS_LOG` + window boundary alone (invariant 8) even in the theoretical case of a machine with zero CMMS events.
  - `sensor_reading.parquet` — long format, same shape as the thin generator's, covering only the **historical** portion (everything older than the trailing 30 days) — ready for immediate bulk load.
  - `cmms_log.parquet` — **added now** (thin generator deferred it); `event_id, equipment_id, event_type, event_start_ts, event_end_ts, technician_notes` per `RAW.CMMS_LOG` (LLD Module 1).
  - `sales_order.parquet` — `order_week, product_id, variant, order_units` per `RAW.SALES_ORDER`, full 3yr+8wk span, no split (weekly grain, not drip-fed).
  - `inventory_fg_snapshot.parquet` — `snapshot_week, product_id, variant, fg_units_on_hand` per `RAW.INVENTORY_FG_SNAPSHOT`, same full span, no split.
  - `spare_part_snapshot.parquet` — `snapshot_week, equipment_id, spare_part_name, units_on_hand, lead_time_days` per `RAW.SPARE_PART_SNAPSHOT`, same full span, no split.
- **Trailing-30-day sensor drip-feed** (LLD §9 step 5 / FR-PL-04b), written to a `--output-dir/live_ticks/` subfolder: one Parquet file per 15-minute tick timestamp across the trailing 30 days, each containing that tick's rows for all 3 machines × 3 sensors (9 rows/file), named by timestamp (e.g. `live_ticks/reading_2026-08-22T06-00-00.parquet`) so a later progressive-release mechanism (Task/`COPY INTO` loop) can release them one at a time in timestamp order. This generator only produces the files; actual timed release into the Stage is a separate pipeline-wiring concern (already noted out of scope — S-DATA-2/ops).

---

## 5. Machine / product / order mapping (resolved)

3 sensor-enabled machines, 2 product lines — **only the 2 primary order series are generated**, the secondary/low-volume variants (ICE Caliper, EV Engine Head) are dropped entirely rather than generated as flat filler:

| Machine | Line | Product | Variant generated |
|---|---|---|---|
| CNC Boring | Caliper | BRAKE_CALIPER | EV only |
| CNC Milling | Caliper | BRAKE_CALIPER | EV only |
| CNC Horizontal | Engine Head | ENGINE_HEAD | ICE only |

- `required_hours_week` for Boring and Milling = `smoothed(EV Caliper orders) / throughput_units_per_hour` — identical value fed to both (LLD §7's "sequential stages receive the same required_hours_week").
- `required_hours_week` for Horizontal = `smoothed(ICE EngineHead orders) / throughput_units_per_hour`.
- Smoothing window: trailing 4 weeks (LLD's stated starting point), i.e. for week `w`, average of weeks `max(1, w-3)..w`.
- `RAW.SALES_ORDER` / `RAW.INVENTORY_FG_SNAPSHOT` therefore only ever contain `(BRAKE_CALIPER, EV)` and `(ENGINE_HEAD, ICE)` rows for this generator run.

---

## 6. Calendar (§1) — resolved parameters

- 5-day work week (Mon-Fri), 3×8hr shifts = 24 operating hours/day.
- **10 fixed generic holidays/year**, recurring every year of the run on fixed month/day (independent of weekday, i.e. a holiday can fall on what would otherwise be a working day and cancels it):

  | Holiday | Date |
  |---|---|
  | New Year's Day | Jan 1 |
  | Winter Break Day | Feb 15 |
  | Spring Holiday | Apr 10 |
  | Labor Day | May 1 |
  | Mid-Year Founders Day | Jun 19 |
  | Summer Holiday | Jul 4 |
  | Harvest Day | Sep 1 |
  | Autumn Holiday | Oct 31 |
  | Thanksgiving-style Day | Nov 27 |
  | Winter Holiday | Dec 25 |

- Week indexing: week 1 starts on the Monday on/before `now - 3 years`; weeks are Mon-Sun; week 156 is the last full historical week (156 weeks ≈ 3 years); weeks 157-160 = look-ahead weeks 1-4, weeks 161-164 = look-ahead weeks 5-8, matching LLD §7's `weeks_elapsed`/`lookahead_ramp` numbering exactly.
- **Residual idle chance — resolved mechanic**: independent per `(machine, working day)` Bernoulli draw, `p ≈ 2.5%` (mid-range of the LLD's stated 2-3%). If true, that day's available hours drop to 0 **for that machine only** — other machines' schedules that week are unaffected. Applied during `simulate.py`'s daily tick allocation, before computing `available_hours_week` net of PM/breakdown duration.

---

## 7. Failure modes & degradation (§3-4) — no open parameters beyond what LLD already fixes

- 5 modes per LLD §3 table, drawn per maintenance cycle with the stated probabilities (28/24/20/18/10%).
- Primary sensor = highest-sensitivity sensor for the mode (drives Weibull draw + targeted breakdown repair).
- **Per-machine Weibull λ (resolved)**: CNC Boring = 650h (unchanged from the thin generator), CNC Milling = 600h, CNC Horizontal = 750h. Per BRD assumption 9/LLD §7, per-hour wear rate is neutral by design — these differences are noise-level tuning, not a deliberate skew toward either line. Shape = 2.0 for all machines (unchanged).
- Unexplainable mode: `T_fail ~ Exponential(mean=400h)`; sensor readings stay **flat baseline + noise for every tick, right up through the tick where `T_fail` is reached** — no partial ramp, no special marker. The breakdown event itself (CMMS log entry + restoration) is the only signal that anything happened; sensor data shows zero precursor by design.
- Degradation (`t` increment) only accrues during actual simulated operating hours for that machine — never during holidays, non-working weekends, residual-idle-flagged days, or PM/breakdown downtime hours that day.

---

## 8. Crew capacity (§5) — resolved mechanics for the circularity in LLD §9 step 2a

LLD §9 step 2a says "draw this week's crew-capacity outcome (breakdown check, background plant draw, then priority contest)" before simulating ticks — but whether a breakdown happens is only discovered *during* tick simulation. Resolved as a two-pass-per-week algorithm inside `simulate.py`:

**Pass 1 (Monday-morning decision, before any ticks this week):**
1. Background plant draw: `Bernoulli(p=0.25)` (LLD mid-range default). If true, the weekday slot is consumed elsewhere — no PM for our 3 machines this week; skip to Pass 2.
2. Otherwise, determine which of the 3 machines have PM due: `calendar_days_since_last_service >= 30`, where "last service" = the more recent of that machine's last PM **or** last breakdown repair (both are service events that reset wear per §5's restoration table; documented assumption since LLD doesn't explicitly say breakdown repair also resets the PM-due clock — flagged for Reviewer-agent to sanity-check against the narrative, not just correctness).
3. Among machines with PM due, compare **this week's raw (unsmoothed) `order_units_week`** for their product line (LLD says "current-week-order-volume", not smoothed) — the line with the higher value wins the slot.
   - Tie-break if multiple machines on the winning line are both due (e.g. Boring and Milling both overdue in the same week): the one with the larger `calendar_days_since_last_service` (more overdue) wins. Documented default, not explicitly specified in the LLD — flagged for the user to override if a different tie-break is preferred, since it's a genuine edge case with no stated rule.
4. Tentatively assign the winning machine's PM to the **first working day of that week** (resolved earlier in brainstorm).

**Pass 2 (day-by-day tick simulation, per machine, for the week):**
- Each day, for each machine: check breakdown *before* PM if both would land the same day for the same machine — breakdown always wins per §5 step 1, and the tentative PM is forfeited for that machine this week (the slot is simply not used by anyone else this week either, since Pass 1 already ran once).
- If today is this machine's tentative PM day and no breakdown fires first: apply PM (`Uniform(1,3)h` duration deducted from that day's 24h before computing operating hours), restore all 3 sensors to `Uniform(0.90, 0.95)`, log CMMS, draw new mode + `T_fail`, reset `t=0` — then continue simulating the rest of that day's ticks under the fresh cycle.
- If `T_fail` is reached on any tick (any day, any machine): immediate breakdown (`Uniform(2,8)h` duration deducted from that day), apply the primary/secondary/unchanged restoration table, log CMMS, draw new mode + `T_fail`, reset `t=0`.
- **Fixed 2026-09-22 (Reviewer-agent finding)**: the originally-implemented version applied PM unconditionally on the tentative day without checking for an imminent breakdown, contradicting this section's "breakdown always wins if both would land the same day" ordering. Fixed by checking `state.t_fail_hours - state.t_hours <= TICK_HOURS` (i.e. breakdown would occur on the very next tick) immediately before applying PM; if true, PM is skipped entirely and the slot is forfeited for the week (`weekday_pm_done = True`), letting the tick loop's existing breakdown-detection logic fire naturally on the next tick. Covered by the new regression test `test_pm_breakdown_tie_break_breakdown_wins_and_slot_forfeited` (§13).
- **Residual scope note (documented gap, not a defect)**: the fix above only catches a collision on the *very next tick* of the tentative PM day, not "breakdown lands later that same day" as this section's prose more loosely states. If `t_fail_hours` would be reached on, say, tick 5 of a longer working day, PM still applies first and resets the cycle, so that particular later-same-day breakdown never happens under the old cycle. This is distinct from the bug above: "exact-same-tick collision" is fixed; "later-same-day collision" remains simplified and is left as-is.

**Weekend pass**: on designated maintenance weekends (every other week-end, ~26/year), after the weekday pass completes, repeat Pass 1's steps 1-3 once more (against machines not already serviced this week) for a second chance. PM performed here costs 0 scheduled operating hours (weekends aren't working days — no tick generation happens, so there's nothing to deduct from). Weekend crew capacity = 1 job per designated weekend (LLD default, unchanged).

**Daily hour allocation**: `operating_hours_week` is spread **evenly** across that week's working days (not front-loaded) — `hours_per_day ≈ operating_hours_week / num_working_days`, further reduced per-day by that day's PM/breakdown duration and residual-idle zeroing, capped at 24h/day. Ticks are generated every 15 minutes across whatever hours remain for that machine that day.

**Look-ahead weeks (157-164)**: the crew-capacity loop (Pass 1 + Pass 2's service-event bookkeeping) still runs through these weeks — PM contention and neglect keep accruing — but **no sensor ticks are generated** (RUL is a model prediction from current state, not a simulated future; BRD assumption 12). Only orders/inventory are generated normally for these weeks.

---

## 9. CMMS technician notes (§5) — resolved, written now

3-4 template strings per mode, randomly selected per event (deterministic templates, no LLM call):

- **Bearing wear**: "Vibration trending up over past week, elevated bearing temp; bearing replaced." / "Audible bearing noise reported by operator; inspected and replaced worn bearing." / "Routine vibration check flagged early bearing wear; preemptively serviced." / "Bearing temperature alarm triggered; bearing housing serviced and repacked."
- **Tool/insert wear**: "Vibration spikes on boring passes; insert showing visible wear, replaced." / "Surface finish quality drift reported; tool insert swapped." / "Tool life counter exceeded threshold; insert replaced during scheduled check." / "Chatter noted during machining; worn insert identified and replaced."
- **Coolant/thermal**: "Elevated spindle temperature trend; coolant flow inspected and coolant topped off/replaced." / "Coolant concentration low, contributing to thermal drift; coolant system flushed and refilled." / "Thermal alarm on spindle housing; coolant line partially blocked, cleared and serviced." / "Temperature creeping above baseline over past days; coolant pump serviced."
- **Servo/RPM instability**: "RPM instability observed under load; servo drive parameters recalibrated." / "Intermittent speed fluctuation reported by operator; servo motor inspected and adjusted." / "RPM feedback drift trending outside tolerance; servo encoder cleaned/recalibrated." / "Speed control alarm triggered; servo drive board reseated and tested."
- **Unexplainable**: "No precursor signal; machine stopped unexpectedly. Root cause not identified — restarted after inspection." / "Sudden stoppage, no sensor trend preceding event. Electrical fault suspected; reset and returned to service." / "Unplanned downtime with no clear cause; control system reset resolved the issue." / "Unexpected halt reported by operator; no fault found on inspection, machine restarted."

---

## 10. Inventory (§7) — resolved parameters

- Finished-goods target days-of-supply: **14 days** historically, tightening to **7 days** during the look-ahead's EV Caliper spike window (weeks 157-164) — makes the inventory-buffer factor bite in the priority score demo per LLD §7.
- Spare parts: reorder point = `lead_time_days` worth of that part's average weekly consumption (simple, no additional safety-stock buffer); decrement by 1 per repair event consuming that part, replenish after `lead_time_days` (from `RAW.SPARE_PART_SNAPSHOT`).

---

## 11. §10 fallback patch — stub only

`generator/fulldata/patch.py` contains a single no-op function (e.g. `apply_engine_head_patch(output_dir, ...)`) with a docstring pointing to LLD §10 and a `# TODO(S-DATA-10)` marker — not called from `full_data_generator.py`'s main flow yet. Gives S-DATA-10 a clear seam without building any patch logic now.

---

## 12. Invariants for Reviewer-agent

1. At most one weekday PM job across all 3 machines per week; at most one additional PM per designated maintenance weekend — never two machines serviced in the same week.
2. Breakdowns always get immediate repair, bypassing the crew-capacity contest entirely, any day.
3. Degradation (`t` increment) only accrues during actual simulated operating hours — never during holidays, non-working weekends, residual-idle-flagged days, or hours beyond that day's capped allocation.
4. Restoration ranges strictly followed: PM → all 3 sensors `Uniform(0.90,0.95)`; breakdown → primary sensor `Uniform(0.90,0.95)`, non-primary sensors with sensitivity > 0.3 → `Uniform(0.70,0.80)`, sensitivity ≤ 0.3 → unchanged.
5. Unexplainable-mode readings show flat baseline+noise with zero precursor right up to the breakdown tick.
6. `T_fail` draws: `Weibull(shape=2.0, scale=λ_machine)` for explainable modes; `Exponential(mean=400h)` for unexplainable — never mixed up.
7. Sensor tick generation stops at "now"; look-ahead weeks (157-164) produce orders/inventory + crew-capacity bookkeeping only, no sensor ticks.
8. Right-censoring must be derivable purely from `RAW.CMMS_LOG`'s `event_type`/timestamps + the window boundary (per LLD §6) — the generator does not need, and should not add, a separate censored-flag column.
9. Sensor volume sanity: original estimate was ~600K historical rows (3 machines × 3 sensors × 96 reads/day × ~694 operating days) and ~18K rows in the trailing-30-day live window, split across ~2,880 per-tick files (30 days × 96 ticks/day), assuming near-100% machine utilization. **Updated 2026-09-22 per Reviewer-agent's two full-scale verification runs (`--seed 42`, full 156+8 weeks): actual measured output is 507,327 historical sensor rows and 13,533 live-window rows (520,860 total), split across 1,734 per-tick files.** The gap from the original estimate is explained by measured machine utilization — CNC Boring/Milling average 85% (max 95%, min 77%) and CNC Horizontal averages 73% (max 80%, min 65%) — because `operating_hours_week = min(required_hours_week, available_hours_week)` (§5/§8) does not produce near-100% utilization; it's order-derived and legitimately falls below full capacity in many weeks. The live-tick file count (1,734 vs. the original ~2,880 estimate) is proportional to this same utilization gap — a known characteristic of the order-derived model, not a defect, and is a fact for whoever later builds the progressive-release mechanism (S-DATA-2/ops, out of scope here) to account for.
10. Failure-mode draw probabilities per cycle match 28/24/20/18/10% (bearing/tool/coolant/servo/unexplainable) within statistical tolerance over many cycles.
11. `RAW.SALES_ORDER`/`RAW.INVENTORY_FG_SNAPSHOT` only ever contain `(BRAKE_CALIPER, EV)` and `(ENGINE_HEAD, ICE)` rows (§5 resolution — secondary variants not generated).
12. The bulk `sensor_reading.parquet` and the `live_ticks/` per-tick files never overlap in timestamp range — the trailing 30 days appear only in `live_ticks/`, never duplicated into the bulk file.

---

## 13. Testability

- **Fixed-seed reproducibility**: running with the same `--seed` twice produces identical row counts and checksums (or byte-identical Parquet) — verifies no hidden nondeterminism (e.g. dict iteration order, unseeded calls) crept in.
- **Statistical sanity checks**: row counts near the LLD back-of-envelope (invariant 9), failure-mode distribution near invariant 10's probabilities, crew-capacity invariant 1 holds across the full run.
- **Visual eyeball**: Reviewer-agent plots at least one machine's full sensor trace (e.g. vibration over the 3-year window) to confirm the degradation/restoration sawtooth pattern looks sane, and specifically inspects CNC Horizontal's trailing-30-day window for the "overdue" narrative signal (informational for now — the §10 patch stub exists precisely because this isn't guaranteed on a given seed).
- **Pytest invariant assertions**: `tests/test_full_data_generator.py` asserts invariants 1-8, 11, and 12 programmatically against a generated (small date-range, fast) run — not full statistical distribution checks (those stay a manual/notebook sanity pass per above), just the hard structural invariants that must never break. `pytest` is not yet a project dependency — Developer-agent adds it via `uv add --dev pytest`. **Updated 2026-09-22**: 12 tests total, including a `test_reproducibility_same_seed_same_row_counts` test (fixed-seed reproducibility, above) and a regression test added post-review, `test_pm_breakdown_tie_break_breakdown_wins_and_slot_forfeited`, covering the §8 breakdown-vs-PM tie-break fix.

---

## 14. Explicitly deferred further

- §10's actual patch logic — S-DATA-10.
- `--reuse-dataset-path`'s wiring into `manage.py up` — S-OPS-SETUP-2.
- Actual timed/progressive release of `live_ticks/` files into the Stage (the Task/`COPY INTO` scheduling mechanism itself) — this story only generates the per-tick files; release timing is pipeline-wiring (S-DATA-2/ops territory).
- Any Stage upload / `COPY INTO` — S-DATA-2's existing mechanism, unchanged.
