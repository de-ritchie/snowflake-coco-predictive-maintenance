# SH-2-11 — Upload + load into Raw (S-DATA-2)

Status: **Frozen** — confirmed by user 2026-08-29
Updated 2026-08-29 (post-implementation): §2 and §3 revised to reflect real deviations discovered during live testing — see "Deviations from original design" at the end. The freeze covers scope/intent; the *mechanism* sections below now describe what was actually built, not what was originally agreed.
Epic: EPIC-SKELETON (P0) §4.2 "Data generation — thin"
Traces to: [02-FRD.md](../02-FRD.md) FR-OPS-01, [04-1-LLD.md](../04-1-LLD.md) Module 1 (RAW section), [04-10-LLD.md](../04-10-LLD.md) Module 10 §1/§8, [docs/05-Epics.md](../05-Epics.md) §4.2

---

## 1. Problem / scope

EPIC-SKELETON's §4.2 Definition of Done: `RAW.SENSOR_READING` must have real rows in Snowflake *before any dbt model is written*. SH-13 (S-DATA-1) produced the local Parquet (`equipment.parquet`, `sensor_reading.parquet`, no `cmms_log.parquet`). This story's job is to get those rows into Snowflake: upload to the Stage created by S-ENV-2, then `COPY INTO` the RAW tables.

**Gap discovered during design**: no `CREATE TABLE` DDL exists anywhere in the repo for any RAW table — `04-1-LLD.md` only *describes* the schema (columns/keys), it was never turned into executable DDL, and `01_setup.sql` v0 only created role/warehouse/database/schemas/stage. `COPY INTO` requires the target table to exist first, so this story must also create the RAW table DDL — confirmed with the user as in-scope here, but as a **separate script from the existing `01_setup.sql`**, treated as the next phase of setup rather than appended into the same file.

---

## 2. Agreed scope

| Dimension | Decision |
|---|---|
| RAW table DDL | **In scope for this story.** `CREATE TABLE IF NOT EXISTS` for `RAW.EQUIPMENT`, `RAW.SENSOR_READING`, `RAW.CMMS_LOG` — columns exactly per Module 1's RAW table (column names/order, no extra audit columns). Goes in a **new script**, not appended to `01_setup.sql`. |
| Upload mechanism | **AS ACTUALLY BUILT** (deviates from original plan — see "Deviations" section): SQL statements are still pure `PUT` + `COPY INTO` (same SQL text originally designed), but they are executed via **`snowflake.connector.connect(connection_name="snow-coco")`** (OAuth authenticator) from Python, not via `snow sql -f`. `manage.py` reads each `.sql` file, strips comment lines, splits on `;`, and executes each statement through the connector's cursor. No Python upload *logic* was added — the connector is purely a transport for the same SQL — but the invocation path is Python, not the `snow` CLI. |
| RAW.CMMS_LOG load | Table is **created** (DDL) but **no `COPY INTO` is issued** for it — SH-13's generator produces no `cmms_log.parquet` file (bearing-wear-only thin pass has nothing to log), so there's nothing to load. Table stays empty until EPIC-FULLDATA's generator starts producing PM/breakdown events. (Unchanged from original design.) |
| Idempotency | Rely on `COPY INTO`'s native load-history dedup (already the documented Module 10 §8 behavior) — no `FORCE=TRUE`, no truncate-first logic. Re-running with the same filenames is a no-op on already-loaded files. (Unchanged from original design.) |
| Orchestration | **AS ACTUALLY BUILT** (reverses original plan — see "Deviations" section): a new **`manage.py`** at repo root, using **`typer`** for its CLI, provides docker-compose-style `up`/`down`. `up` runs `01_setup.sql` → generates thin data via `generator/thin_sensor_generator.py` (imported directly in-process, not subprocessed) → runs `02_setup_raw_ddl.sql` → runs `03_setup_raw_load.sql`, all against one `snow-coco` connection. `down` runs `09_teardown.sql`. Both verified end-to-end against the live account (full teardown + rebuild; `RAW.SENSOR_READING` landed 4320 rows with a correct timestamp range). |
| Teardown | **AS ACTUALLY BUILT** (deviates from original plan — see "Deviations" section): `09_teardown.sql` (renamed from `07_teardown.sql`) is no longer a comment-only stub — it now contains real executable SQL (`DROP DATABASE IF EXISTS snowcomotive; DROP ROLE IF EXISTS snowcomotive_role; DROP WAREHOUSE IF EXISTS snowcomotive_wh;`), because `manage.py down` needed a real target to execute. |

---

## 3. Script structure — renumbering required

The numbered lifecycle scripts 01–07 are already claimed for other stages (`scripts/README.md`). This story inserts two new files after `01_setup.sql`, so everything from the old `02` onward shifts up by two. **AS ACTUALLY BUILT**: the SQL file contents/numbering below are unchanged from the original plan, but invocation is no longer `snow sql -f <script> --connection <connection>` per file — `manage.py up`/`down` now runs the relevant scripts in sequence via one long-lived `snowflake.connector` connection (see §2 "Orchestration" and "Deviations from original design" below). **The `snow` CLI is no longer used at all** — its MFA/TOTP-based connection required a fresh passcode on every invocation, which is why it was dropped entirely rather than kept as a fallback (see §8.1).

| # | File | Status | Content |
|---|---|---|---|
| 01 | `01_setup.sql` | Unchanged | Role/warehouse/database/schemas/stage (S-ENV-1/S-ENV-2) — no edits |
| 02 *(NEW)* | `02_setup_raw_ddl.sql` | This story | `CREATE TABLE IF NOT EXISTS` for `RAW.EQUIPMENT`, `RAW.SENSOR_READING`, `RAW.CMMS_LOG` per Module 1 columns |
| 03 *(NEW)* | `03_setup_raw_load.sql` | This story | `PUT` local Parquet → Stage, then `COPY INTO RAW.EQUIPMENT` / `COPY INTO RAW.SENSOR_READING` (no CMMS_LOG copy — see §2) |
| 04 | `04_pipeline_run_phase1.sql` | Renamed from old `02_pipeline_run_phase1.sql` | Unchanged content |
| 05 | `05_train_models.sql` | Renamed from old `03_train_models.sql` | Unchanged content |
| 06 | `06_pipeline_run_phase2.sql` | Renamed from old `04_pipeline_run_phase2.sql` | Unchanged content |
| 07 | `07_post_setup.sql` | Renamed from old `05_post_setup.sql` | Unchanged content |
| 08 | `08_demo.sql` | Renamed from old `06_demo.sql` | Unchanged content |
| 09 | `09_teardown.sql` | Renamed from old `07_teardown.sql` | Unchanged content (see §2 — no edits needed, `DROP DATABASE` already cascades over the new RAW tables) |

`scripts/README.md`'s table must be updated to reflect the new numbering, statuses, and Jira references, and must add a note that `generator/thin_sensor_generator.py` needs to be run first (producing `./output/equipment.parquet` and `./output/sensor_reading.parquet`) before `03_setup_raw_load.sql` can `PUT` anything — this is a manual prerequisite, not automated by this story.

---

## 4. `02_setup_raw_ddl.sql` — target details

```sql
CREATE TABLE IF NOT EXISTS snowcomotive.raw.equipment (
  equipment_id STRING,
  equipment_name STRING,
  line_name STRING,
  product_id STRING,
  variant STRING,
  is_sensor_enabled BOOLEAN,
  throughput_units_per_hour NUMBER,
  commissioned_ts TIMESTAMP_NTZ
);

CREATE TABLE IF NOT EXISTS snowcomotive.raw.sensor_reading (
  reading_id STRING,
  equipment_id STRING,
  reading_ts TIMESTAMP_NTZ,
  sensor_type STRING,
  reading_value FLOAT
);

CREATE TABLE IF NOT EXISTS snowcomotive.raw.cmms_log (
  event_id STRING,
  equipment_id STRING,
  event_type STRING,
  event_start_ts TIMESTAMP_NTZ,
  event_end_ts TIMESTAMP_NTZ,
  technician_notes STRING
);
```

Column list/order and grain are as specified in `04-1-LLD.md`'s RAW table — Developer-agent should not add audit/metadata columns. Exact Snowflake type mapping (e.g. `STRING` vs `VARCHAR`, `NUMBER` precision) is an implementation detail Developer-agent can resolve consistently with any existing type conventions elsewhere in the repo (none exist yet, so pick sensible defaults and stay consistent within this file).

---

## 5. `03_setup_raw_load.sql` — target details

```sql
-- Prerequisite (manual, not automated by this script):
--   python generator/thin_sensor_generator.py --start-date <YYYY-MM-DD> --end-date <YYYY-MM-DD>
-- produces ./output/equipment.parquet and ./output/sensor_reading.parquet

PUT file://./output/equipment.parquet @snowcomotive.raw.landing_stage/equipment/ AUTO_COMPRESS=FALSE OVERWRITE=TRUE;
PUT file://./output/sensor_reading.parquet @snowcomotive.raw.landing_stage/sensor_reading/ AUTO_COMPRESS=FALSE OVERWRITE=TRUE;

COPY INTO snowcomotive.raw.equipment
  FROM @snowcomotive.raw.landing_stage/equipment/
  FILE_FORMAT = (TYPE = PARQUET)
  MATCH_BY_COLUMN_NAME = CASE_INSENSITIVE
  ON_ERROR = 'ABORT_STATEMENT';

COPY INTO snowcomotive.raw.sensor_reading
  FROM @snowcomotive.raw.landing_stage/sensor_reading/
  FILE_FORMAT = (TYPE = PARQUET)
  MATCH_BY_COLUMN_NAME = CASE_INSENSITIVE
  ON_ERROR = 'ABORT_STATEMENT';

-- No COPY INTO for cmms_log — SH-13's thin generator produces no cmms_log.parquet
-- (bearing-wear-only pass has nothing to log). Table exists (02) but stays empty
-- until EPIC-FULLDATA's generator emits PM/breakdown events.
```

`PUT`'s local path is relative to wherever the executing process's cwd is — confirmed resolved correctly relative to the repo root (matching the generator's own default `--output-dir ./output`) since `manage.py` is always invoked from repo root (§8.1/§8.2).

Note on `OVERWRITE=TRUE` on `PUT`: this only controls whether the *staged* file is replaced on re-upload — it does not bypass `COPY INTO`'s own load-history dedup (a file already loaded by name/checksum into a RAW table is still skipped by `COPY INTO` on a subsequent run, per Module 10 §8). This is intentional — regenerating fresh Parquet under the same filename during dev re-stages it, but a `COPY INTO` re-run of already-loaded data remains a no-op, matching the agreed idempotency behavior.

`ON_ERROR = 'ABORT_STATEMENT'` chosen over `CONTINUE`/`SKIP_FILE` since this is a thin, single-file, dev-time load — a malformed file should fail loudly, not silently skip rows (consistent with Module 10 §8's "safe to invoke out of order" meaning "doesn't corrupt state," not "always succeeds").

---

## 6. Explicitly OUT of scope

- `RAW.SALES_ORDER`, `RAW.INVENTORY_FG_SNAPSHOT`, `RAW.SPARE_PART_SNAPSHOT`, `RAW.CALENDAR` — deferred to EPIC-FULLDATA per the Epics doc's explicit note ("defer order/inventory/calendar to EPIC-FULLDATA if not needed for the first model").
- Any `COPY INTO` for `RAW.CMMS_LOG` — table created, load deferred (§2).
- ~~A Python orchestrator chaining spin-up→pipeline→teardown~~ — **superseded, see §8.2**: `manage.py` now does exactly this.
- ~~Adding the `snow` CLI to the `uv`-managed toolchain~~ — moot: the actual mechanism uses `snowflake-connector-python` directly, not the `snow` CLI (§8.1).
- ~~Any changes to `07_teardown.sql`/`09_teardown.sql`~~ — **superseded, see §8.3**: it needed real `DROP` statements to support `manage.py down`.
- Automating the SH-13 generator invocation itself as a standalone manual step — **partially superseded**: `manage.py up` now calls the generator in-process (§8.2), though the generator script itself still also works standalone via its own CLI.

---

## 7. Open items for Developer-agent

- Exact Snowflake column type mapping for the 3 new tables (§4) — `STRING`/`VARCHAR`, `NUMBER` precision/scale, `TIMESTAMP_NTZ` vs `TIMESTAMP_TZ` — pick sensible defaults, no existing convention to match yet in this repo.
- Update `scripts/README.md`'s table for the full renumbering (§3) plus the new prerequisite note about running the generator before step 03.
- Confirm the 5 renamed files' internal header comments (Jira references, "traces to" lines) still read correctly after the file renames — no content changes needed beyond the filename-implied step number if headers reference the old number anywhere.

---

## 8. Deviations from original design

Discovered and resolved during live testing, after the freeze above. These are documented here rather than silently folded into §2/§3 as if always planned — the trigger in every case was concrete workflow friction or a real bug hit while actually running the story against Snowflake, not a change of taste.

### 8.1 Upload mechanism: `snow sql -f` → Python via `snowflake-connector-python`

**Original design**: "SQL-native `PUT` + `COPY INTO`, no Python upload wrapper... runnable via `snow sql -f`."

**What happened**: `snow sql -f` against the `snow-coco-passcode` connection (`username_password_mfa` authenticator) required a fresh MFA/TOTP passcode on *every single invocation*. The user explicitly rejected this as a workflow ("I don't want that method anymore").

**What was actually built**: The same SQL text (unchanged `PUT`/`COPY INTO` statements) is now executed via `snowflake.connector.connect(connection_name="snow-coco")` — the existing OAuth (`authenticator = oauth_authorization_code`) connection already documented in `AGENTS.md`, not a new connection. The `secure-local-storage` extra was added to `snowflake-connector-python` (via `keyring`) so the OAuth token is cached locally instead of popping a browser window on every run. This is a transport change only — no SQL logic changed, no Python-side upload/retry logic was introduced — but it does mean the RAW-load path is no longer "SQL-native, no Python," contra the original design's explicit framing.

### 8.2 Orchestration: "Out of scope" → `manage.py` up/down wrapper

**Original design**: "Out of scope. No Python spin-up→teardown wrapper — scripts continue to be invoked individually via `snow sql -f`."

**What happened**: Once the connector-based invocation existed (8.1), the user asked for a docker-compose-style `up`/`down` orchestrator instead of running scripts by hand one at a time.

**What was actually built**: `manage.py` (repo root), CLI via `typer` (new dependency, added to `pyproject.toml`). `up` runs `01_setup.sql` → `generator/thin_sensor_generator.py` (imported and called in-process, not subprocessed) → `02_setup_raw_ddl.sql` → `03_setup_raw_load.sql`, all against a single `snow-coco` connection. `down` runs `09_teardown.sql`. Both commands were tested end-to-end against the live account (full teardown, then full rebuild), confirmed working: `RAW.SENSOR_READING` landed 4320 rows with a correct timestamp range post-rebuild. This directly reverses the original "no orchestrator" decision — the "user's own follow-up" framing in §6/§2 of the original design turned out to be wrong; it happened within this same story's live-testing pass, not as a separate future story.

### 8.3 Teardown: "No changes needed" → real DROP statements

**Original design**: "No changes needed. `07_teardown.sql`'s existing stub already does `DROP DATABASE IF EXISTS snowcomotive`..."

**What happened**: The "existing stub" was actually comment-only (Status: NOT YET BUILT) at the time of freeze — the original design's assumption that it already contained a working `DROP DATABASE` was incorrect. This surfaced once `manage.py down` (8.2) needed something real to execute.

**What was actually built**: `scripts/09_teardown.sql` now contains real executable SQL:
```sql
DROP DATABASE IF EXISTS snowcomotive;
DROP ROLE IF EXISTS snowcomotive_role;
DROP WAREHOUSE IF EXISTS snowcomotive_wh;
```
This was necessary to support the new orchestrator (8.2), not an independent scope addition — teardown had to become real the moment `down` needed to actually call it.

### 8.4 Generator bug fix: Parquet timestamp encoding (SH-13 file, fixed under SH-11)

**Not anticipated by the original design at all** — this is a genuine bug found during live `COPY INTO` testing, not a design decision reversal.

**What happened**: `generator/thin_sensor_generator.py`'s `to_parquet()` calls used pandas/pyarrow defaults, which write nanosecond-precision INT64 `TIMESTAMP` columns. Snowflake's Parquet `COPY INTO` reader misread these — microsecond values were interpreted as seconds, producing "Invalid date" errors / absurd years (e.g. 56014071).

**Fix**: added `use_deprecated_int96_timestamps=True` to both `to_parquet()` calls in `generator/thin_sensor_generator.py` (`equipment_df.to_parquet(...)` and `sensor_df.to_parquet(...)`). INT96 is the older, unambiguous physical timestamp encoding that Snowflake's Parquet reader handles correctly. This is a fix to SH-13's already-merged generator file, made necessary by and verified during SH-11's `COPY INTO` testing — it is not itself a SH-11 scope item, but it had to be fixed in-line to get SH-11's load path working end-to-end.
