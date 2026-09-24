# Design: Run generator, verify + realign demo narrative

Status: **Frozen** — T2/T3/T4 are concrete, buildable designs with no blocking ambiguity. T1 and T5 were initially blocked by a tooling gap in the design session (no live SQL access); both were filled in immediately after freeze by the orchestrating session (which has SQL access) — see §3 and §6 for the real findings: T1 requires the fallback patch (Engine Head serviced only 3 days ago, not overdue), and T5 found a genuine 100%-anomaly-week example (`CNC_BORING`, 2026-07-27) plus a data-freshness gap to address before demo. §7 lists what's still a judgment call for the user (patch magnitude/failure-mode choice) vs. what's now settled. Reconciled 2026-09-24 post-implementation/review — Reviewer-agent returned a clean **PASS** verdict, zero bugs found across 7 independent live re-verification checks. See §11 for one real fix discovered mid-implementation and bundled into this story (a pre-existing `cons__dim_sensor_baseline` seed gap that would have silently broken T1's own narrative at the dashboard level, unrelated to the T1/T2/T3/T4/T5 scope above but a direct blocker to it) and §8 for a retroactive checklist correction (`docs/05-Epics.md`).
**Story**: SH-42 (S-DATA-10)
**Branch**: `feature/SH-6-42-run-generator-verify-demo-narrative`
**Traces to**: [docs/04-2-LLD.md](../04-2-LLD.md) §10 (fallback patch), §9 (generation algorithm), [docs/04-10-LLD.md](../04-10-LLD.md) §6 (demo script mechanism), [docs/05-Epics.md](../05-Epics.md) S-DATA-10, [docs/designs/SH-34-36-41-ops-setup-v2-pipeline-wiring.md](SH-34-36-41-ops-setup-v2-pipeline-wiring.md) (existing `inject-tick`/`reset-cursor` mechanism this extends), [docs/designs/SH-69-std-cons-order-inventory-oee-models.md](SH-69-std-cons-order-inventory-oee-models.md) (CONS tables T5 queries), [docs/designs/SH-27-28-30-31-32-33-semantic-view-agent-chat.md](SH-27-28-30-31-32-33-semantic-view-agent-chat.md) (semantic view / verified-query mechanism T5 extends)

---

## 0. Session tooling constraint (read before treating T1/T5 as verified)

This design session had **no working live-execution or live-query tool**, despite the story's instructions assuming one:

- No SQL-execution tool against Snowflake exists in this session's toolset (only Atlassian/Confluence/browser/file tools, matching the same constraint SH-69/SH-27 already documented in their own §0s).
- The browser tool errored on every `browser_navigate` call this session (`"The WebView must be attached to the DOM..."`) — Snowsight could not be reached to run queries by hand either.
- A tabular-file-reading tool was referenced in this environment's tool list but returned `"Tool 'read_tabular' not found"` on every invocation — unavailable in practice, so even the already-generated local `output/*.parquet` files (confirmed present on disk from a prior `manage.py up` run, dated 2026-08-17 through 2026-09-11) could not be inspected with pandas.
- No `bash`/shell tool exists to run `uv run manage.py up` or a one-off Python inspection script directly.

**Consequence**: T1 ("is Engine Head convincingly overdue?") and T5 ("find a real correlated row in live data") cannot be answered with an actual data finding from this design session — only from source-code analysis. Both sections below give a fully concrete, numeric, executable procedure so Developer-agent (who does have `uv run`/connector access) can answer them in minutes once implementation starts, rather than leaving them as open design questions. This is a real gap, not a design shortcut — flagged again in §7.

---

## 1. Scope (from `docs/05-Epics.md` S-DATA-10, already agreed with the user)

- **T1**: Run the full generator; verify Engine Head (`CNC_HORIZONTAL`) is convincingly overdue for maintenance; apply the accepted §10 hand-patch if not.
- **T2**: Fix `RAW.CMMS_LOG` leaking future-dated "completed" PM bookkeeping events from the look-ahead crew-capacity simulation.
- **T3**: Shrink the live drip-feed window `LIVE_WINDOW_DAYS` (30 days) to 48 hours, restructured into exactly 2 sequential ~24h batches.
- **T4**: Add `manage.py demo inject-batch`, extending the existing `inject-tick`/`reset-cursor` cursor mechanism.
- **T5**: Design a descriptive (non-computed) order-driven prioritization signal for the dashboard/Chat agent.

---

## 2. T2 — Fix the look-ahead CMMS leak

### The bug (confirmed by reading `simulate.py:113-147`)

```python
if is_lookahead:
    # Crew-capacity bookkeeping only -- no sensor ticks (LLD SS9 step 3).
    if weekday_pm_machine and tentative_pm_day:
        state = states[weekday_pm_machine]
        duration_hours = float(rng.uniform(1, 3))
        start_ts = datetime.combine(tentative_pm_day, time.min)
        end_ts = start_ts + timedelta(hours=duration_hours)
        _restore_pm(rng, state)
        cmms_rows.append(                                    # <-- the leak
            cmms.make_event(weekday_pm_machine, "PM", start_ts, end_ts, cmms.pick_pm_note(rng))
        )
        state.mode, state.t_fail_hours = _new_cycle(rng, MACHINES[weekday_pm_machine]["weibull_lambda_hours"])
        state.t_hours = 0.0
        state.last_service_date = tentative_pm_day
    spare_part_snapshot_rows.extend(spare_parts_sim.snapshot_week(week_num))
    continue
```

Every look-ahead week (8 weeks past "now") that wins the weekly PM crew contest appends a `cmms.make_event(..., "PM", ...)` row with a future `event_start_ts`/`event_end_ts` and a `technician_notes` string like *"Scheduled preventive maintenance performed; all sensors inspected and serviced."* — wording and populated `event_end_ts` both assert something that **already happened**, when in fact no sensor ticks were ever simulated for that week at all (§10 LLD's own step 3 note: "the 8-week look-ahead only has orders/inventory, no simulated sensor ticks"). This row flows unfiltered through `STD.CMMS_LOG` → `CONS.FCT_MAINTENANCE_EVENT` (confirmed pure pass-through in `cons__fct_maintenance_event.sql`, per SH-69 §3 Q4) and would be visible to the Chat agent / Overview page as a real, completed maintenance record dated in the future.

### Decision: (a) — exclude the look-ahead PM row from `cmms_rows` entirely; keep all in-memory state updates

```python
if is_lookahead:
    # Crew-capacity bookkeeping only -- no sensor ticks (LLD SS9 step 3).
    # This branch must NOT append to cmms_rows: no RAW.CMMS_LOG row should
    # ever claim a PM "happened" in the look-ahead window, since no sensor
    # ticks are simulated there either (T2 fix, SH-42).
    if weekday_pm_machine and tentative_pm_day:
        state = states[weekday_pm_machine]
        _restore_pm(rng, state)
        state.mode, state.t_fail_hours = _new_cycle(rng, MACHINES[weekday_pm_machine]["weibull_lambda_hours"])
        state.t_hours = 0.0
        state.last_service_date = tentative_pm_day
    spare_part_snapshot_rows.extend(spare_parts_sim.snapshot_week(week_num))
    continue
```

Removed: the `duration_hours`/`start_ts`/`end_ts` draw and the `cmms_rows.append(cmms.make_event(...))` call. Kept: `_restore_pm`, the new failure-mode/`t_fail_hours` draw, `t_hours` reset, and `last_service_date` advance — these are exactly what the crew-capacity math needs to know whether a machine is PM-due in a *later* look-ahead week (`is_pm_due` compares against `last_service_date`), and that contention must still resolve correctly even though nothing is exported.

### Reasoning (why (a), not (b) with an `is_scheduled`/`is_actual` column)

1. **Matches the project's own already-frozen invariant for this table.** SH-69 §3 Q4 (frozen, reviewed, live-verified) established `CONS.FCT_MAINTENANCE_EVENT` as a straight pass-through of `STD.CMMS_LOG`, and the semantic view's `MaintenanceEvent` entity (SH-27/28/30 §3, also frozen/reviewed) has no `is_scheduled` dimension. Adding option (b)'s column would mean revising two already-reviewed, already-shipped artifacts (a dbt model + a live `CREATE SEMANTIC VIEW` DDL) for a fix whose actual requirement is "don't emit fake data," not "let consumers filter fake data out downstream." Every future consumer (Chat agent NL queries, Overview page, any EPIC-RUL feature built later) would need to remember to add `WHERE is_scheduled = FALSE` — a foot-gun the simpler fix removes at the source.
2. **Matches the BRD's own stated design intent for the look-ahead window** (LLD §10 Known simplifications: "the 8-week look-ahead only carries orders/inventory, never simulated sensor ticks"). CMMS bookkeeping in that window was only ever meant to be an *internal simulation aid* (crew-capacity math needs to know future contention to compute `hours_since_last_service` correctly at "now"), never a real exported fact. Option (a) makes the code's actual behavior match that stated intent exactly; option (b) would formalize the leak as an intentional, permanent two-tier data model instead of removing it.
3. **No blast radius beyond `simulate.py`.** No RAW/STD/CONS DDL, dbt model, dbt test, semantic view, or Chat agent instruction needs to change. `RAW.CMMS_LOG` simply receives fewer rows (all future-dated ones removed) — existing `not_null`/`accepted_values` tests on `event_type` (SH-69 §3 Q6) are unaffected.
4. **Directly helps T1.** With this fix, `RAW.CMMS_LOG`'s **last real event for any machine is always historical** — exactly what T1's "how many days since Engine Head's last PM, as of now" check needs to compute honestly, with no chance of a future bookkeeping row silently making the machine look *more* recently serviced than it really was.

---

## 3. T1 — Verify Engine Head is convincingly overdue (procedure + criteria; see §0/§7 for why no live finding is reported here)

### Concrete criterion for "convincingly overdue"

Two independent signals, **both** required:

1. **Calendar overdue**: `days_since_last_pm_or_breakdown` for `CNC_HORIZONTAL`, computed as `historical_end_date − MAX(event_end_ts)` across all `RAW.CMMS_LOG` rows for that `equipment_id` (any `event_type`) that exist **after** the T2 fix (so this is always a real historical event, never a look-ahead bookkeeping row) — **≥ 45 calendar days** (1.5× `PM_DUE_INTERVAL_DAYS`, `crew_capacity.py:14`). This comfortably clears "technically overdue" (≥30 days) into "visibly, narratively overdue."
2. **Wear signal**: the machine's own accumulated operating hours `t_hours` relative to its currently-drawn `t_fail_hours` should be in the degraded tail — concretely, the trailing ~10 sensor readings (any of `VIBRATION`/`TEMPERATURE`) for `CNC_HORIZONTAL` in `output/sensor_reading.parquet` (bulk historical) or `output/live_ticks/` (last 30 days pre-T3, or last 48h post-T3) should show a **median at least 1 baseline-std above the machine's own baseline mean** (`machine_config.py`'s `sensor_baselines["CNC_HORIZONTAL"]`: `VIBRATION (2.2, 0.3)`, `TEMPERATURE (42.0, 3.0)`) — this is the visual, dashboard-visible half of "at-risk" (matches `1_Overview.py`'s own `is_anomaly`-driven badge logic, so a presenter can point at the same badge turning red).

### Check procedure (Developer-agent's first implementation step for this story)

```python
# one-off inspection, run via `uv run python -c "..."` or a scratch script
import pandas as pd
cmms = pd.read_parquet("output/cmms_log.parquet")
eh = cmms[cmms.equipment_id == "CNC_HORIZONTAL"].sort_values("event_end_ts")
last_event_end = eh.event_end_ts.max()
# historical_end_date is printed by full_data_generator.py / derivable as the
# Sunday immediately before manage.py up's own --now anchor's 3yr-back Monday
# + 156 weeks -- simplest is to log it directly from run_simulation() during
# the generator run, or read it off output/calendar.parquet's max date minus
# the known 8-week lookahead tail.
days_since = (historical_end_date - last_event_end.date()).days
readings = pd.read_parquet("output/sensor_reading.parquet")
eh_vib = readings[(readings.equipment_id == "CNC_HORIZONTAL") & (readings.sensor_type == "VIBRATION")].sort_values("reading_ts").tail(10)
median_vib = eh_vib.reading_value.median()  # compare to baseline mean 2.2, std 0.3
```

- **If both criteria hold**: no patch needed. Record the actual `days_since` / median-reading numbers found in this doc's §7 (update this section) and in the PR description — this is real, reportable evidence for the demo, not a guess.
- **If either fails**: apply the §10 fallback exactly as specified, with these concrete parameters (filling in the LLD's deliberately-unspecified "how much further back," since §10 leaves exact magnitude to build time):
  1. In `output/cmms_log.parquet`, shift `CNC_HORIZONTAL`'s last real event's `event_start_ts`/`event_end_ts` back by **`45 − days_since + 10` days** (lands at 55 days overdue — comfortably past the 45-day bar with a safety margin, not a razor's-edge value that a re-run could undercut).
  2. Re-derive the implied failure state: use failure mode **`BEARING_WEAR`** (highest prior probability, 0.28, per `machine_config.py`, and dual-sensor sensitivity — `VIBRATION 0.8`, `TEMPERATURE 0.6` — for a visually dramatic two-sensor story, matching `1_Overview.py`'s multi-sensor detail cards). Pick `t_fail_hours` such that the new implied `t_hours / t_fail_hours ≈ 0.90` (visibly degraded per `degradation.health()`'s formula, not yet at outright failure) — e.g. if the shifted gap implies ~600 accumulated operating hours (a plausible utilization given `throughput_units_per_hour=10` and Engine Head's order volume), set `t_fail_hours ≈ 667`.
  3. Re-run `degradation.generate_reading(...)` with this `mode`/`h0=1.0`/`t_hours`/`t_fail_hours` for every `CNC_HORIZONTAL` row in the trailing window (`output/sensor_reading.parquet`'s tail + every `output/live_ticks/*.parquet` file for that machine) to replace the raw reading values in place — **not** freehand edits, per §10's explicit instruction, so the patched data stays internally consistent with the same degradation curve the anomaly model trains on.
  4. Document the patch with the one-line note §10 requires (README or demo script comment): *"Trailing-window Engine Head maintenance/sensor data was hand-adjusted post-generation (SH-42) to ensure the priority-flip narrative; underlying generation logic (`simulate.py`) is unchanged."*

**No RNG re-roll loop** — per §10's explicit instruction not to iterate seeds indefinitely; the patch procedure above is the designed, one-time fallback path, used only if the as-generated run fails the two criteria.

### Live finding (filled in post-freeze by the orchestrating session, which has SQL access — 2026-09-24)

**Verdict: fallback patch is required.** Ran the exact check against the live account (`snowcomotive.raw.cmms_log`/`sensor_reading`):

| Check | Result | Pass? |
|---|---|---|
| Calendar overdue (≥45 days since last *real* event) | Most recent real `CNC_HORIZONTAL` event: `PM`, `event_end_ts = 2026-09-21` → **only 3 days** before "now" (~2026-09-24) | **FAIL** |
| Wear signal (trailing readings ≥1σ above baseline) | Trailing 2 days: `TEMPERATURE` avg 46.44 (baseline 42.0±3.0 → **+1.48σ**), `VIBRATION` avg 2.738 (baseline 2.2±0.3 → **+1.79σ**) | PASS (but incoherent alongside a 3-day-old service) |

This also live-confirms the exact §2/T2 bug in the wild: the top row returned by `ORDER BY event_end_ts DESC` was `PM, event_start_ts = 2026-10-26, event_end_ts = 2026-10-26 02:25:17` — **32 days in the future** relative to "now" — the look-ahead bookkeeping leak, caught live before the T2 fix is even applied. Post-T2-fix, this row disappears and the real most-recent event (`2026-09-21`) becomes correctly visible as the basis for the overdue calculation — reinforcing §2 reasoning point 4 (T2 is a prerequisite for T1 being answerable honestly at all, not just a nice-to-have).

**Conclusion**: criterion 1 fails outright (3 days ≪ 45), so the §10 fallback patch (steps 1–4 above) must be applied by Developer-agent. The wear-signal-already-elevated-despite-recent-service finding is itself a good argument for *why* a patch is warranted here rather than just re-running with a different seed: the as-generated data is narratively contradictory (freshly serviced but already trending unhealthy), not just "not overdue enough" — a hand-patch resolves both problems at once by pushing the last-service date back to align with the already-elevated readings.

---

## 4. T3 — Shrink the live window to 48h / 2 batches (a real bug found + fixed along the way)

### A latent bug this shrink would otherwise expose

`historical_end_date = week_start(base_monday, historical_weeks + 1) - timedelta(days=1)`. Since `week_start()` always returns a Monday (`sim_calendar.py:44-46`), `historical_end_date` is **deterministically always a Sunday**, for any `--now` value. The current live/historical split is calendar-date arithmetic: `live_start_date = historical_end_date - timedelta(days=LIVE_WINDOW_DAYS - 1)`, and `is_live_day = d >= live_start_date` is only ever evaluated for `d` in `wdays` (Mon–Fri, `working_days_in_week()`) — **sensor ticks are never generated on weekends at all**, live or historical.

With `LIVE_WINDOW_DAYS = 30` this was invisible (30 calendar days always contains ~20 weekdays, plenty of live ticks). Naively setting `LIVE_WINDOW_DAYS = 2` to mean "48 hours" would compute `live_start_date = Sunday − 1 day = Saturday` — **both Saturday and Sunday are weekends, so zero live ticks would ever be generated**, silently breaking the entire demo drip-feed for every single generator run (not a probabilistic edge case — `historical_end_date` landing on Sunday is guaranteed by construction, every time).

### Fix: redefine the live window as "trailing N working days with ticks," not "trailing N calendar days"

```python
LIVE_WINDOW_WORKING_DAYS = 2  # 48h demo window = 2 trailing *working* days of ticks
                              # (not calendar days -- see SH-42 design doc S4:
                              # historical_end_date is always a Sunday, so a
                              # naive calendar-day subtraction can land the
                              # window entirely on a weekend and produce zero
                              # live ticks).


def _trailing_working_days(end_date: date, n: int) -> list[date]:
    days: list[date] = []
    d = end_date
    while len(days) < n:
        if sim_calendar.is_working_day(d):
            days.append(d)
        d -= timedelta(days=1)
    return sorted(days)
```

Replace:
```python
live_start_date = historical_end_date - timedelta(days=LIVE_WINDOW_DAYS - 1)
```
with:
```python
live_start_date = _trailing_working_days(historical_end_date, LIVE_WINDOW_WORKING_DAYS)[0]
```

This guarantees the held-back window always covers exactly 2 real working days' worth of ticks (skipping weekends/holidays automatically, via the already-existing `is_working_day()`), robust to `historical_end_date` landing on any weekday. `is_live_day = d >= live_start_date` (unchanged) now correctly holds back exactly those 2 working days.

### Batching: each held-back working day *is* one 24h batch — no new sub-day boundary logic needed

Because ticks are only ever generated on working days (never weekends), and the fix above guarantees exactly 2 such days are held back, the file set in `output/live_ticks/` will, by construction, contain files from exactly 2 distinct calendar dates. **Batch 1 = the earlier date's files, batch 2 = the later date's files.** This is a clean, natural 24h-chunk boundary (`reading_<date>T...` filenames already sort chronologically per the existing mechanism's own convention) with zero additional timestamp-arithmetic complexity in `manage.py` — see §5.

Per-tick files are still written individually (`live_tick_buffer` → one Parquet per `reading_ts`, unchanged) — only the *injection-time* grouping is coarse, exactly as scoped ("don't lose the fine-grained files").

---

## 5. T4 — `manage.py demo inject-batch`

### Design: no parameters needed — "inject the next not-yet-loaded date-chunk"

Given T3 guarantees exactly 2 distinct calendar dates ever appear among `output/live_ticks/*.parquet` (§4), `inject-batch` needs no arguments: each invocation loads *all* remaining files sharing the date of the next not-yet-injected file, in one connector session, then advances the cursor to the last file loaded — composing with the existing `.cursor` file exactly like `inject-tick` does today (`_read_cursor`/`_write_cursor`, unchanged).

```python
def _batch_date(filename: str) -> str:
    """'reading_2026-09-10T14-30-00.parquet' -> '2026-09-10'."""
    return filename[len("reading_"):len("reading_") + 10]


def inject_next_batch() -> None:
    files = _live_tick_files()
    if not files:
        print(f"No live tick files found in {LIVE_TICKS_DIR}. Run `manage.py up` first.")
        raise typer.Exit(code=1)

    names = [f.name for f in files]
    cursor = _read_cursor()
    if cursor is None or cursor not in names:
        if cursor is not None:
            print(f"Cursor references unknown tick '{cursor}' -- restarting from the first file.")
        next_idx = 0
    else:
        next_idx = names.index(cursor) + 1

    if next_idx >= len(files):
        print("All batches already injected. Run `manage.py demo reset-cursor` to restart.")
        raise typer.Exit(code=0)

    chunk_date = _batch_date(names[next_idx])
    chunk_files = [f for f in files[next_idx:] if _batch_date(f.name) == chunk_date]
    remaining_after = len(files) - next_idx - len(chunk_files)

    print(f"--- Injecting batch {chunk_date} ({len(chunk_files)} tick file(s)) ---")
    conn = snowflake.connector.connect(connection_name=CONNECTION_NAME, role="snowcomotive_role")
    try:
        cur = conn.cursor()
        cur.execute(
            f"PUT 'file://{LIVE_TICKS_DIR}/reading_{chunk_date}*.parquet' "
            "@snowcomotive.raw.landing_stage/sensor_reading/ AUTO_COMPRESS=FALSE OVERWRITE=TRUE"
        )
        file_list = ", ".join(f"'{f.name}'" for f in chunk_files)
        cur.execute(
            "COPY INTO snowcomotive.raw.sensor_reading "
            "FROM @snowcomotive.raw.landing_stage/sensor_reading/ "
            f"FILES = ({file_list}) "
            "FILE_FORMAT = (TYPE = PARQUET) MATCH_BY_COLUMN_NAME = CASE_INSENSITIVE "
            "ON_ERROR = 'ABORT_STATEMENT'"
        )
        rows = cur.fetchall()
        columns = [c[0] for c in cur.description]
    finally:
        conn.close()

    status_idx = columns.index("status") if "status" in columns else None
    if status_idx is not None and rows and not all(r[status_idx] == "LOADED" for r in rows):
        print(f"COPY INTO did not report LOADED for all files: {rows}")
        raise typer.Exit(code=1)

    _write_cursor(chunk_files[-1].name)
    print(f"Injected batch {chunk_date} ({len(chunk_files)} file(s)). {remaining_after} tick(s) remaining.")
```

```python
@demo_app.command("inject-batch")
def demo_inject_batch() -> None:
    """Inject every output/live_ticks/ file in the next ~24h date-chunk into RAW.SENSOR_READING, in one session."""
    inject_next_batch()
```

- **One PUT, one COPY INTO** for the whole chunk (glob-pattern PUT uploads every file for that date in a single statement; a single `COPY INTO ... FILES = (...)` explicitly lists every filename in the chunk rather than relying on stage-wide dedup — matches the existing `inject_next_tick`'s "explicit success check before advancing the cursor" invariant, just extended from 1 file to N).
- **Cursor semantics unchanged**: still a single filename string, still only written after `COPY INTO` confirms `LOADED` for every file. `inject-tick`/`reset-cursor` keep working standalone and interoperate with `inject-batch` transparently (e.g. running `inject-tick` after both batches are exhausted correctly reports "All ticks already injected").
- **No new parameterization** — deliberately, since there are only ever 2 batches; if a future story widens the live window again, this same date-grouping logic keeps working unchanged (it doesn't hardcode "2").

### New demo flow this enables (matching the Epics doc's stated sequence)

`manage.py up` (bulk pipeline against data as of `now − 48h`) → `demo inject-batch` (batch 1) → dynamic tables auto-refresh (`target_lag` on `CONS.FCT_SENSOR_READING`/`FEAST.FCT_SENSOR_FEATURES_INFERENCE`/`CONS.FCT_ANOMALY_RESULT`, no retraining) → show updated predictions on the Overview page → `demo inject-batch` (batch 2) → show how predictions changed.

---

## 6. T5 — Order-driven prioritization context (descriptive, not a computed score)

### Placement: both a new Overview page section and a new semantic-view verified query

Same "one query, two surfaces" pattern SH-27/28/30 already established for the health-status query (that doc's own reasoning, §4: grounds a Chat answer against something the presenter can visually cross-check elsewhere in the same demo breath). Do the same here:

1. **New Overview page section** (`oee_command_center_app/pages/1_Overview.py`), titled "Order-driven priority signals," below the existing "Sensor detail" section.
2. **New `AI_VERIFIED_QUERIES` entry** in `scripts/07_post_setup.sql`'s `CREATE SEMANTIC VIEW`, alongside the existing `current_machine_health_status` entry (SH-27/28/30 §3).

### Query design

Grain mismatch to resolve: `cons__fct_order` is at `(order_week, product_id, variant)` — a **product/line** grain; `cons__fct_anomaly_result` is at `(equipment_id, reading_ts)` — a fine-grained **machine/tick** grain; `cons__fct_inventory_spare` is at `(period_week, equipment_id, spare_part_name)`. There is no failure-mode→spare-part mapping anywhere in RAW/STD/CONS (`SPARE_PART_BY_MODE` in `machine_config.py` is generator-internal only, never persisted) — correctly out of scope per T5's own framing ("NOT a computed priority score," no `FCT_RUL_PREDICTION`), so the query surfaces **all** of a machine's tracked spare parts and their lead times as general readiness context, not a specific part tied to a predicted failure mode.

```sql
WITH weekly_order AS (
    SELECT
        p.product_id, p.variant,
        o.order_week,
        o.order_units,
        AVG(o.order_units) OVER (
            PARTITION BY o.product_id, o.variant
            ORDER BY o.order_week
            ROWS BETWEEN 3 PRECEDING AND CURRENT ROW
        ) AS trailing_4wk_avg_order_units
    FROM snowcomotive.cons.cons__fct_order o
    JOIN snowcomotive.cons.cons__dim_product p
        ON p.product_id = o.product_id AND p.variant = o.variant
),
weekly_anomaly AS (
    SELECT
        equipment_id,
        DATE_TRUNC('week', reading_ts) AS period_week,
        AVG(anomaly_score) AS avg_anomaly_score,
        SUM(IFF(is_anomaly, 1, 0)) AS anomaly_count
    FROM snowcomotive.cons.cons__fct_anomaly_result
    GROUP BY equipment_id, DATE_TRUNC('week', reading_ts)
),
spare_readiness AS (
    SELECT equipment_id, period_week, MIN(lead_time_days) AS min_lead_time_days, SUM(units_on_hand) AS total_units_on_hand
    FROM snowcomotive.cons.cons__fct_inventory_spare
    GROUP BY equipment_id, period_week
)
SELECT
    m.line_name,
    m.equipment_id,
    m.equipment_name,
    wo.order_week,
    wo.order_units,
    wo.trailing_4wk_avg_order_units,
    wa.avg_anomaly_score,
    wa.anomaly_count,
    sr.min_lead_time_days,
    sr.total_units_on_hand
FROM snowcomotive.cons.cons__dim_equipment m
JOIN weekly_order wo
    ON wo.product_id = m.product_id AND wo.variant = m.variant
LEFT JOIN weekly_anomaly wa
    ON wa.equipment_id = m.equipment_id AND wa.period_week = wo.order_week
LEFT JOIN spare_readiness sr
    ON sr.equipment_id = m.equipment_id AND sr.period_week = wo.order_week
WHERE m.is_sensor_enabled
ORDER BY m.line_name, wo.order_week DESC;
```

**Natural-language question** (for the `AI_VERIFIED_QUERIES` clause): *"How does recent order volume for each line compare to that line's equipment anomaly trend and spare-part readiness?"*

### Overview page section (sketch)

```python
@st.cache_data(ttl=300)
def load_priority_signals() -> pd.DataFrame:
    conn = get_connection()
    return conn.query(PRIORITY_SIGNALS_SQL, ttl=300)  # the query above

st.subheader("Order-driven priority signals")
signals_df = load_priority_signals()
for line_name, line_df in signals_df.groupby("LINE_NAME"):
    latest = line_df.sort_values("ORDER_WEEK").iloc[-1]
    st.markdown(
        f"**{line_name}**: order volume trailing-4wk avg **{latest['TRAILING_4WK_AVG_ORDER_UNITS']:.0f}** units/wk, "
        f"avg anomaly score **{latest['AVG_ANOMALY_SCORE']:.3f}**, "
        f"spare-part lead time as low as **{latest['MIN_LEAD_TIME_DAYS']:.0f} days**."
    )
```

### Real-data example (filled in post-freeze by the orchestrating session, which has SQL access — 2026-09-24)

Ran the exact query above live. Recent look-ahead weeks (2026-09-28 onward) correctly return `NULL` anomaly data — no sensor ticks exist for the future, as expected. Historical weeks join cleanly. Found a genuine, dramatic real example — **CNC_BORING, week of 2026-07-27**:

| line_name | equipment_id | order_week | order_units | trailing_4wk_avg | avg_anomaly_score | anomaly_count | min_lead_time_days | total_units_on_hand |
|---|---|---|---|---|---|---|---|---|
| Caliper | CNC_BORING | 2026-07-27 | 1546 | 1662.5 | **−0.0812** | **440 (out of 440 readings — 100%)** | 7 | 10 |

This is a real anomaly spike week: **every single sensor reading for `CNC_BORING` that week was flagged anomalous** (`anomaly_count = n = 440`), with a negative average `decision_function` score (more negative = more anomalous for IsolationForest). Order volume that week (1546) was actually a dip below its trailing average (1662.5) — so the natural narrated line for this specific week is "order volume held steady/dipped slightly, but `CNC_BORING` had a 100% anomaly week with only 7-day spare-part lead time and 10 units on hand as buffer." The following week (2026-08-03) order volume rebounded to 1792 (above trailing average) while anomalies dropped to 33/445 — a genuine, tell-able "order pressure returning right as the machine was still working through anomalies" story across those two weeks together, which is arguably a *better* demo beat than a single-week snapshot (shows the correlation evolving, not just a static fact).

**One data-freshness observation worth flagging** (not a bug in this query, a pipeline-staleness note): `CONS.FCT_ANOMALY_RESULT`'s most recent populated week at query time was `2026-08-10` — roughly 6 weeks behind the most recent `RAW.SENSOR_READING` data — even though it's a `dynamic_table` with `target_lag`. Likely explanation: the underlying `FEAST.FCT_SENSOR_FEATURES_TRAIN`/inference chain or the trained model's `sample_input_data` window didn't get a fresh incremental refresh trigger recently in this account, or the last `manage.py up` run's dbt phase-2 step ran before the most recent sensor data landed. Developer-agent should force-refresh the relevant dynamic tables (`ALTER DYNAMIC TABLE ... REFRESH`) as part of this story's verification pass, and re-run T5's query afterward to confirm the *latest* week (not just the July window) shows real data before wiring the Overview section — the demo should show current-week correlation, not a 6-week-old snapshot.

**Resolved**: this freshness gap was closed via an operational rebuild (`dbt run --full-refresh` chain, after the §11 item 1 seed fix) rather than any code change — no dynamic-table DDL or refresh-trigger logic was modified.

---

## 7. Flagged for the user's review (please read before marking T1/T5 done)

1. **T1 now has a live finding (§3)**: fallback patch **is required** — Engine Head was serviced only 3 days ago (fails the ≥45-day bar), and the live check also caught the T2 bug in the wild (a future-dated `PM` row 32 days ahead of "now"). Developer-agent should apply the §3 patch procedure as specified.
2. **T5's real-data example is now filled in (§6)**: `CNC_BORING`, week of `2026-07-27`, 100% anomaly rate (440/440 readings flagged) — a genuine, dramatic finding, not fabricated. A data-freshness gap was also found (`CONS.FCT_ANOMALY_RESULT` ~6 weeks stale relative to `RAW.SENSOR_READING` at query time) — Developer-agent should force-refresh the relevant dynamic tables before wiring T5's Overview section, so the demo shows current data, not a July snapshot.
3. **T3's working-day-boundary bug fix (§4)** was discovered incidentally while designing the 48h shrink — it is a real, deterministic bug in the *current* `LIVE_WINDOW_DAYS=30` code path's boundary logic that only manifests at small window sizes, not something introduced by this story. Flagging clearly since it's a substantive design decision beyond pure parameterization, not just a lookup.
4. **T1's fallback-patch magnitude** (§3: 55-days-overdue target, `BEARING_WEAR` mode, `t/t_fail ≈ 0.90`) are concrete but not user-confirmed picks — reasonable defaults chosen for a dramatic-but-plausible narrative, adjustable if the user wants a different failure mode or severity for the demo story.

---

## 8. Files to create/modify (Developer-agent's checklist)

**Modified**:
- `generator/fulldata/simulate.py` — T2 fix (§2): remove the `cmms_rows.append(...)` call from the `is_lookahead` branch. T3 fix (§4): replace `LIVE_WINDOW_DAYS` with `LIVE_WINDOW_WORKING_DAYS = 2` + `_trailing_working_days()` helper + the `live_start_date` computation.
- `manage.py` — T4 (§5): new `inject_next_batch()` function + `_batch_date()` helper + `@demo_app.command("inject-batch")`. `inject_next_tick`/`reset_cursor` untouched.
- `scripts/07_post_setup.sql` — T5 (§6): add the new `AI_VERIFIED_QUERIES` entry (`order_driven_priority_signals` or similar key) alongside the existing `current_machine_health_status` entry.
- `oee_command_center_app/pages/1_Overview.py` — T5 (§6): new "Order-driven priority signals" section, following the file's existing `@st.cache_data(ttl=300)` + `conn.query(...)` convention.
- `output/cmms_log.parquet`, `output/sensor_reading.parquet`, `output/live_ticks/*.parquet` (for `CNC_HORIZONTAL` only) — T1 (§3), **only if** the live check fails the two criteria; regenerate via `manage.py up` otherwise (no patch needed).
- One-line demo-script/README note documenting the T1 patch, **only if applied** (§3 step 4).
- `docs/05-Epics.md` — S-DATA-10's entry updated to reflect the actual expanded T1–T5 scope shipped by this story (retroactive addition to this checklist — under-scoped at freeze time, not missing from the implementation; see §11 item 2).
- `predictive_maintenance_dbt/seeds/cons__dim_sensor_baseline.csv` — real fix discovered mid-implementation, not in original design scope (see §11 item 1).

**Reads only** (no changes): `generator/fulldata/crew_capacity.py`, `generator/fulldata/machine_config.py`, `generator/fulldata/degradation.py`, `generator/fulldata/sim_calendar.py`, `generator/fulldata/cmms.py`.

---

## 9. Invariants for Reviewer-agent

1. `RAW.CMMS_LOG` (post-fix) contains zero rows with `event_start_ts` or `event_end_ts` after `historical_end_date` — no look-ahead-week PM bookkeeping event is ever exported (§2).
2. Crew-capacity math is unaffected by the T2 fix: a machine serviced (per the weekly contest) during a look-ahead week still has its `last_service_date`/`mode`/`t_fail_hours` state updated in-memory, so a *later* look-ahead week's `is_pm_due` check still resolves correctly — verify by confirming the removed-row machine doesn't spuriously win the PM contest again immediately the following week.
3. `output/live_ticks/` contains files from **exactly 2 distinct calendar dates** after the T3 fix, for any `--now` value (including one where `historical_end_date`, always a Sunday, would have broken the old calendar-day-subtraction logic) — this is the concrete, testable proof that §4's bug fix actually holds.
4. `manage.py demo inject-batch` PUTs+COPYs an entire date-chunk in one connector session (one PUT, one COPY INTO) and only advances `.cursor` after `COPY INTO` reports `LOADED` for every file in the chunk — a partial failure must leave the cursor untouched, exactly like `inject-tick` (§5).
5. `inject-tick`/`reset-cursor` behavior is byte-for-byte unchanged by this story — no regression to the existing single-tick demo path.
6. T5's new Overview section and verified query never claim a specific spare part is "the" part needed for a predicted failure — only aggregate readiness context (`MIN(lead_time_days)`, `SUM(units_on_hand)`) across all tracked parts for that equipment, since no failure-mode→part mapping exists in CONS (§6).

---

## 10. Explicitly deferred

- Any computed `CONS.FCT_PRIORITY_SCORE` / `PriorityScore` semantic entity — EPIC-RUL's job, needs `CONS.FCT_RUL_PREDICTION`, not built.
- A failure-mode→spare-part join for T5 — would need a new static mapping (`SPARE_PART_BY_MODE` is generator-internal only); out of scope, not asked for.
- `09_teardown.sql` / DDL changes for the new verified query — `CREATE OR REPLACE SEMANTIC VIEW` already handles re-creation idempotently, no teardown changes needed.
- Any change to `inject-tick`/`reset-cursor`'s own code — explicitly out of scope per the story text, kept as-is for finer-grained control if ever needed.

---

## 11. Deviations from original design

Consolidating the real deviations from the frozen scope found while implementing this story (mirroring the "Deviations" convention used in prior multi-file design docs in this repo, e.g. `docs/designs/SH-34-36-41-ops-setup-v2-pipeline-wiring.md` §11):

1. **`predictive_maintenance_dbt/seeds/cons__dim_sensor_baseline.csv` had a pre-existing gap that would have silently broken T1's own narrative — not in the original design scope, but a direct blocker to it, so it was fixed as part of this story rather than filed separately.** The seed only had 3 rows (`CNC_BORING` baselines only). `feast__fct_sensor_features_inference`/`cons__fct_anomaly_result`'s FEAST macro inner-joins against this seed, so `CNC_MILLING` and `CNC_HORIZONTAL` got **zero rows** in both `FEAST.FCT_SENSOR_FEATURES_INFERENCE` and `CONS.FCT_ANOMALY_RESULT` — meaning Engine Head (`CNC_HORIZONTAL`) would never surface a red/at-risk anomaly badge on the Overview page, regardless of how correctly the T1/T2 CMMS/sensor patch (§3) was applied underneath. This would have silently broken the demo's central "Engine Head overdue" narrative at the presenter-visible dashboard/badge level.
   - **Why bundled into SH-42 rather than filed as a separate story**: (a) it is a direct blocker to T1's own stated goal — a correct CMMS/sensor patch with no anomaly badge to show for it is not a working demo narrative; (b) small, self-contained surface area (one seed file, 6 rows added); (c) a pre-existing gap unrelated to any other in-flight story, so there was no risk of stepping on parallel work.
   - **Fix**: added 6 rows (`CNC_MILLING` and `CNC_HORIZONTAL` baselines) to reach 9 total rows, values copied exactly from `generator/fulldata/machine_config.py`'s `sensor_baselines` dict, cross-checked number-by-number by Reviewer-agent against the source.
   - **Verification (live, post-fix)**: retrained the model and rebuilt the FEAST/inference chain (`dbt run --full-refresh`), then confirmed live that `CNC_HORIZONTAL` now has 52,737 anomaly-result rows, 1,594 of them flagged anomalous (`is_anomaly = TRUE`), including at its most recent reading — the Engine Head anomaly badge is now populated end-to-end, not silently empty.
2. **`docs/05-Epics.md` was modified but was not in the original design doc's §8 "Files to create/modify" checklist.** This is a legitimate, AGENTS.md-mandated update — keeping the backlog doc's S-DATA-10 entry in sync with the actual expanded T1–T5 scope this story shipped — just under-scoped in the original checklist rather than an unplanned addition. Added to §8's file list retroactively so the checklist matches what actually shipped.

---

## 12. Reviewer-agent verification (live, 2026-09-24)

Reviewer-agent returned a clean **PASS** verdict — zero bugs found across 7 independent live re-verification checks against the working-tree diff, including independent confirmation of the `cons__dim_sensor_baseline` fix (§11 item 1: baseline values cross-checked number-by-number against `machine_config.py`, and the live `CNC_HORIZONTAL` anomaly-result row/flag counts re-confirmed post-`dbt run --full-refresh`).
