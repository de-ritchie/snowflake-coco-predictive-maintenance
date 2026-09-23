# Design: Ops Setup v2 + Pipeline Wiring (RAW destinations, full-generator swap, demo drip-feed)

Status: **Frozen** — brainstormed interactively with the user, frozen on explicit signal ("Freeze it as written"). Reconciled 2026-09-23 post-implementation/review — Reviewer-agent returned a PASS verdict; see §1 (factual correction), §10 (live-verification results), and §11 (deviations from original design) for what changed since the original freeze.
**Stories**: SH-34 (S-OPS-SETUP-2: update setup script to v2), SH-36 (S-DATA-8: inventory + spare parts generation), SH-41 (S-DATA-9: 30-day drip-feed split)
**Branch**: `feature/SH-6-34-36-41-ops-setup-v2-pipeline-wiring`
**Traces to**: [docs/04-1-LLD.md](../04-1-LLD.md) (RAW table DDL/schema), [docs/04-10-LLD.md](../04-10-LLD.md) §1 (setup script) and §6 (demo script), `scripts/README.md`, `manage.py`, `generator/full_data_generator.py` + `generator/fulldata/simulate.py`

## 1. Scope and SH-25 absorption note

SH-36's generation logic (weekly inventory/spare-part snapshots tied to repair events) is already fully built in `generator/fulldata/inventory.py` (merged core generator engine, PR #10). SH-36 only needs a Snowflake RAW destination — same work as SH-34's DDL/load extension.

SH-41's file-splitting (per-tick Parquet files for the trailing 30 days) is also already built (`generator/fulldata/simulate.py` writes `live_ticks/`). What's left for SH-41 is the demo-script v2 progressive-release mechanism.

SH-41's Jira description says it "supersedes S-DEMO-1's single-file stand-in" and "updates demo script to v2" — but S-DEMO-1 (SH-25, demo script v1) was never actually *built out*: SH-25 is still "To Do", and `scripts/08_demo.sql` pre-existed only as an unbuilt v1 stub with no real logic behind it. **Corrected 2026-09-23 per Reviewer-agent finding**: the original text of this section claimed `scripts/08_demo.sql` "doesn't exist" — this was wrong; the stub file was already present in the repo. **Decision (unchanged)**: this branch builds the full demo-script capability under SH-41 directly, reimplemented in Python per §6 rather than as `CREATE TASK`/`EXECUTE TASK` SQL — there's no working v1 logic to incrementally upgrade, so it's built once, correctly. Per explicit user instruction, the pre-existing stub file is **restored/updated in place, not deleted**: its header is rewritten to point at the actual implementation (`manage.py demo inject-tick` / `manage.py demo reset-cursor`) and to mark it clearly as a non-runnable historical stub that is never executed. SH-25 is **not** added to this branch's Jira scope; it will be moved to Done separately (Jira-only) once this branch's implementation is confirmed to cover its intent.

## 2. RAW DDL — `scripts/02_setup_raw_ddl.sql` (extended)

4 new tables, following the existing convention exactly (`CREATE TABLE IF NOT EXISTS`, plain columns, no `PRIMARY KEY`/`FOREIGN KEY` constraints — key documented in a comment only, matching `RAW.EQUIPMENT`/`SENSOR_READING`/`CMMS_LOG`):

```sql
-- Key: (order_week, product_id, variant)
CREATE TABLE IF NOT EXISTS snowcomotive.raw.sales_order (
  order_week TIMESTAMP_NTZ,
  product_id STRING,
  variant STRING,
  order_units NUMBER(10,0)
);

-- Key: (snapshot_week, product_id, variant)
CREATE TABLE IF NOT EXISTS snowcomotive.raw.inventory_fg_snapshot (
  snapshot_week TIMESTAMP_NTZ,
  product_id STRING,
  variant STRING,
  fg_units_on_hand NUMBER(10,0)
);

-- Key: (snapshot_week, equipment_id, spare_part_name)
CREATE TABLE IF NOT EXISTS snowcomotive.raw.spare_part_snapshot (
  snapshot_week TIMESTAMP_NTZ,
  equipment_id STRING,
  spare_part_name STRING,
  units_on_hand NUMBER(10,0),
  lead_time_days NUMBER(10,0)
);

-- Key: calendar_date. Full-replace (static) -- see §4 idempotency note below,
-- this table is NOT append-only like the others.
CREATE TABLE IF NOT EXISTS snowcomotive.raw.calendar (
  calendar_date DATE,
  is_working_day BOOLEAN,
  is_holiday BOOLEAN
);
```

## 3. Generator: `calendar.parquet` as a 7th bulk output

`generator/full_data_generator.py` gains `calendar.parquet` alongside its existing 6 bulk files. Built in `simulate.py` (or a small new helper in `sim_calendar.py`) by iterating every `calendar_date` across the full historical+look-ahead window (`base_monday` through the last look-ahead week's Sunday) and reusing the already-existing `sim_calendar.is_working_day` / `sim_calendar.is_holiday` — no new holiday table, no duplicated logic. Output columns: `calendar_date, is_working_day, is_holiday`, written with the same `use_deprecated_int96_timestamps=True` Parquet convention (timestamp columns only — `calendar_date` is a plain `DATE`, so this flag is a no-op for it but kept for consistency with the writer helper already in use).

`BULK_OUTPUT_FILES` in `full_data_generator.py` gets `"calendar.parquet"` added, and `run_simulation`'s returned dict gets a `"calendar"` key.

## 4. Load — `scripts/03_setup_raw_load.sql` (extended)

`PUT` + `COPY INTO` added for all 4 new tables, same pattern as the existing 2:

```sql
PUT 'file://./output/sales_order.parquet' @snowcomotive.raw.landing_stage/sales_order/ AUTO_COMPRESS=FALSE OVERWRITE=TRUE;
PUT 'file://./output/inventory_fg_snapshot.parquet' @snowcomotive.raw.landing_stage/inventory_fg_snapshot/ AUTO_COMPRESS=FALSE OVERWRITE=TRUE;
PUT 'file://./output/spare_part_snapshot.parquet' @snowcomotive.raw.landing_stage/spare_part_snapshot/ AUTO_COMPRESS=FALSE OVERWRITE=TRUE;
PUT 'file://./output/calendar.parquet' @snowcomotive.raw.landing_stage/calendar/ AUTO_COMPRESS=FALSE OVERWRITE=TRUE;
PUT 'file://./output/cmms_log.parquet' @snowcomotive.raw.landing_stage/cmms_log/ AUTO_COMPRESS=FALSE OVERWRITE=TRUE;

COPY INTO snowcomotive.raw.sales_order FROM @snowcomotive.raw.landing_stage/sales_order/
  FILE_FORMAT = (TYPE = PARQUET) MATCH_BY_COLUMN_NAME = CASE_INSENSITIVE ON_ERROR = 'ABORT_STATEMENT';

COPY INTO snowcomotive.raw.inventory_fg_snapshot FROM @snowcomotive.raw.landing_stage/inventory_fg_snapshot/
  FILE_FORMAT = (TYPE = PARQUET) MATCH_BY_COLUMN_NAME = CASE_INSENSITIVE ON_ERROR = 'ABORT_STATEMENT';

COPY INTO snowcomotive.raw.spare_part_snapshot FROM @snowcomotive.raw.landing_stage/spare_part_snapshot/
  FILE_FORMAT = (TYPE = PARQUET) MATCH_BY_COLUMN_NAME = CASE_INSENSITIVE ON_ERROR = 'ABORT_STATEMENT';

COPY INTO snowcomotive.raw.cmms_log FROM @snowcomotive.raw.landing_stage/cmms_log/
  FILE_FORMAT = (TYPE = PARQUET) MATCH_BY_COLUMN_NAME = CASE_INSENSITIVE ON_ERROR = 'ABORT_STATEMENT';

-- RAW.CALENDAR is full-replace (static), not append-only -- TRUNCATE + FORCE=TRUE
-- bypass COPY INTO's native load-history dedup, which would otherwise make a
-- second setup run on regenerated calendar data a silent no-op.
TRUNCATE TABLE snowcomotive.raw.calendar;
COPY INTO snowcomotive.raw.calendar FROM @snowcomotive.raw.landing_stage/calendar/
  FILE_FORMAT = (TYPE = PARQUET) MATCH_BY_COLUMN_NAME = CASE_INSENSITIVE ON_ERROR = 'ABORT_STATEMENT' FORCE = TRUE;
```

Note: `cmms_log` was already DDL'd (`02_setup_raw_ddl.sql`) but never loaded, since the thin generator produced no `cmms_log.parquet`. The full generator does, so wiring its load is in scope for this branch (part of SH-34's "swap thin invocation for full generator call" — the swap is incomplete if one of the full generator's outputs is silently dropped).

## 5. `manage.py` swap

- `generate_thin_data()` is replaced by a call into `generator/full_data_generator.py`'s `run_simulation` (in-process import, same style as the current thin-data call).
- `up` command gains two new Typer options, both passed straight through to the generator:
  - `--seed` (default `42`, matches the generator's own default).
  - `--reuse-dataset-path` (default `None`) — if set and the path already contains the expected bulk output files, generation is skipped (existing `_reuse_dataset_exists` check in `full_data_generator.py` already does this).
- `manage.py up` now **always** produces full data — there is no thin-data code path left in `up`. `generator/thin_sensor_generator.py` itself is left untouched as a standalone reference/fallback script (per the generator-core design doc), just no longer invoked by `manage.py`.
- `--start-date`/`--end-date` options on `manage.py up` are dropped (the full generator uses `--now`, anchoring 3-years-back/8-weeks-forward internally, not an arbitrary start/end range) — `manage.py up` gets a `--now` passthrough option instead (default: today), matching the generator CLI's own flag.

## 6. Demo script v2 (SH-41's actual new work)

No `CREATE TASK`/`EXECUTE TASK` — deliberately simpler than Module 10 §6's literal pseudocode, per explicit user decision. Mechanism:

- New `manage.py demo inject-tick` command:
  1. Reads a cursor file at `output/live_ticks/.cursor` (plain text, holds the last-injected filename; absent/empty means "start from the first file"). This path is already covered by the existing `output/*` `.gitignore` entry.
  2. Lists `output/live_ticks/*.parquet`, sorted chronologically by filename (the existing `reading_<timestamp>.parquet` naming sorts correctly as strings).
  3. Finds the next file after the cursor's position (or the first file if the cursor is empty).
  4. `PUT` that one file to `@snowcomotive.raw.landing_stage/sensor_reading/` and `COPY INTO snowcomotive.raw.sensor_reading` directly in one connector session — same shape as `03_setup_raw_load.sql`'s existing PUT+COPY INTO pattern, just for a single file, invoked synchronously (no Task).
  5. Updates `.cursor` to the injected filename.
  6. Prints the injected filename/timestamp and remaining tick count, so the presenter has something to narrate.
- New `manage.py demo reset-cursor` command: deletes/clears `output/live_ticks/.cursor`, restarting the drip-feed from the first tick for a repeat demo run.
- `scripts/README.md`'s row 08 is updated to point at `manage.py demo inject-tick`/`reset-cursor` instead of a SQL file. `scripts/08_demo.sql` itself is **not** deleted — it pre-existed as an unbuilt v1 stub (see §1's correction); this story restores/updates its header in place to point at the actual Python implementation and mark it as a non-runnable historical stub that is never executed, since the real demo mechanism is Python-only (no Task/SQL object left to script).

**Operational note (flagged by Reviewer-agent, not a bug in the demo mechanism itself)**: `manage.py up`'s dataset regeneration (`generate_full_data()` → `run_simulation()`) rewrites `output/live_ticks/*.parquet` on every run — this is pre-existing generator behavior, not introduced by this story. Running `up` again with a different `--seed`/`--now` between demo sessions can desync an in-progress demo cursor, since `.cursor` references filenames from the prior generation that may no longer exist (or may now mean something different) after a fresh run. Presenters should avoid re-running `up` mid-demo, or run `manage.py demo reset-cursor` after any regeneration.

## 7. Streamlit button — left untouched

The SH-21 "Inject next tick" sidebar button stays exactly as built: disabled, gated behind `?demo=1`, with its "not wired yet" help text unchanged. Streamlit is deployed into Snowflake (Module 10 §5's `snow streamlit deploy`) and has no access to the local repo's `output/live_ticks/` directory or cursor file at runtime, so wiring the button for real is not attempted in this branch. The actual trigger for a live demo is the presenter running `manage.py demo inject-tick` locally, narrated verbally. Real button wiring (if ever done) remains SH-33's (S-OPS-POST-1b) job, unchanged from the original scope split.

## 8. Affected files

- `scripts/02_setup_raw_ddl.sql` — 4 new `CREATE TABLE IF NOT EXISTS` statements.
- `scripts/03_setup_raw_load.sql` — 5 new PUT+COPY INTO blocks (4 new tables + `cmms_log`), plus the `RAW.CALENDAR` truncate+force exception.
- `scripts/README.md` — status rows updated for 02/03 (new tables), and row 08 repointed to `manage.py demo` commands instead of a SQL file.
- `scripts/08_demo.sql` — header restored/updated in place (not deleted, not created-from-scratch — see §1) to point at `manage.py demo inject-tick`/`reset-cursor` and mark itself a non-runnable historical stub.
- `generator/full_data_generator.py` — `calendar.parquet` added to `BULK_OUTPUT_FILES` and the writer loop.
- `generator/fulldata/simulate.py` (or `sim_calendar.py`) — calendar-row generation helper, reusing existing `is_working_day`/`is_holiday`.
- `manage.py` — `generate_thin_data()` removed/replaced; `up` calls the full generator with `--seed`/`--reuse-dataset-path`/`--now`; new `demo` sub-command group with `inject-tick` and `reset-cursor`.

## 9. Invariants for Reviewer-agent

1. All new RAW DDL is idempotent (`CREATE TABLE IF NOT EXISTS`), consistent with the existing 3 tables.
2. `RAW.CALENDAR` is the one deliberate exception to "COPY INTO's native load-history dedup makes reload idempotent" (FR-OPS-06) — its truncate+`FORCE=TRUE` load must not be applied to any other table; the other 4 append-only tables must rely on native dedup only, no truncate.
3. `manage.py up` never touches `output/live_ticks/` or the demo cursor — the drip-feed is fully decoupled from environment setup; running `up` repeatedly must not disturb an in-progress demo cursor.
4. `manage.py demo inject-tick` must be safe to re-run after a partial failure: if `PUT`+`COPY INTO` succeeds but the cursor update fails (or vice versa), re-running should not silently skip or double-inject a tick — cursor update happens only after COPY INTO confirms success.
5. `cmms_log` load added in this branch must use the same `ON_ERROR = 'ABORT_STATEMENT'` convention as the rest of `03_setup_raw_load.sql` — no silent partial loads.
6. The generator's `calendar.parquet` must span the exact same date range as the rest of the run (`base_monday` through the last look-ahead week), with no gaps — `STD.CALENDAR` (a future story, `view` materialization per Module 1) will depend on full date coverage.

## 10. Verification (live, 2026-09-23)

Run manually via `uv run python manage.py up` against the real `snow-coco` Snowflake connection (not an automated test):

- Full dataset generation succeeded — 7/7 bulk files with correct row counts: `equipment` 15, `sensor_reading` 507327, `cmms_log` 114, `sales_order` 328, `inventory_fg_snapshot` 328, `spare_part_snapshot` 1968, `calendar` 1148.
- `02_setup_raw_ddl.sql`: all 4 new tables (`SALES_ORDER`, `INVENTORY_FG_SNAPSHOT`, `SPARE_PART_SNAPSHOT`, `CALENDAR`) created successfully live.
- `03_setup_raw_load.sql`: all 7 PUT+COPY INTO loads returned `LOADED` with exact row-count matches, including the previously-orphaned `cmms_log` load (114/114, now fixed by this branch — see §4's note) and the `CALENDAR` truncate+force sequence (1148/1148).
- `dbt seed` passed.
- `05_train_models.sql` failed with a pre-existing, out-of-scope `ModuleNotFoundError: No module named 'shap'` (a `snowflake-ml-python` explainability packaging issue unrelated to this story's diff) — noted for awareness, not a blocker for this branch.
- `manage.py demo inject-tick`/`reset-cursor` were verified via static review + mocked-connector simulation only, not against a live Snowflake `live_ticks/` directory — that requires a full generator run first and was not exercised end-to-end in this pass.

## 11. Deviations from original design

Consolidating the two real deviations from the frozen LLD/Jira scope found while implementing this story (mirroring the "Deviations" convention used in every prior multi-file design doc in this repo, e.g. `docs/designs/1 - SH-2-11-upload-load-into-raw.md` §8):

1. **SH-25 (S-DEMO-1) absorption, not supersession.** SH-41's Jira description frames itself as superseding S-DEMO-1's "single-file stand-in" and updating the demo script to v2 — implying a v1 already existed and was being upgraded. In reality S-DEMO-1/SH-25 was never built: it was still "To Do", and `scripts/08_demo.sql` pre-existed only as an empty, unbuilt stub with no working logic. There was nothing to incrementally upgrade, so this branch built the full demo capability once, directly under SH-41, rather than modifying pre-existing v1 logic. See §1 for the full history, including a factual correction to this section's original (incorrect) claim that `08_demo.sql` "doesn't exist" — it did exist, as a stub, and was restored/updated in place rather than deleted.
2. **Demo mechanism drops `CREATE TASK`/`EXECUTE TASK` from `docs/04-10-LLD.md` Module 10 §6's literal pseudocode.** The LLD's original design used a Snowflake `TASK` object (`PUT` + `EXECUTE TASK snowcomotive.raw.sensor_tick_copy_task`) to fire tick injection. This branch instead implements a simpler, fully synchronous mechanism — `manage.py demo inject-tick`/`reset-cursor` (§6) — doing the same `PUT` + `COPY INTO` directly in one Python connector session, with no `TASK` object created at all. Rationale: a `TASK`-based mechanism adds asynchronous-completion uncertainty (the presenter can't be sure the copy finished before narrating) and an extra Snowflake object to manage/teardown, with no benefit for a single-presenter, single-click demo flow. This is a deliberate, explicit simplification (see §11 "Explicitly deferred further" below) — not revisited unless a future story specifically needs scheduled/automatic tick release.

## 12. Explicitly deferred further

- Real wiring of the Streamlit "Inject next tick" button — SH-33 (S-OPS-POST-1b), unchanged.
- Any `CREATE TASK`/`EXECUTE TASK` mechanism — explicitly dropped in favor of direct PUT+COPY INTO; not revisited unless a future story asks for scheduled/automatic tick release.
- `STD.SALES_ORDER`/`STD.INVENTORY_FG_SNAPSHOT`/`STD.SPARE_PART_SNAPSHOT`/`STD.CALENDAR` dbt models — no existing dbt models reference these RAW tables yet; this branch is RAW-layer + ops wiring only, dbt Standardized-layer work is a separate future story.
- SH-25's Jira ticket itself — not touched by this branch; moved to Done separately once this implementation is confirmed to cover its intent.
