# Environment & Operations Scripts

Lifecycle scripts for the SnowComotive project, per FR-OPS-01 through 06 / LLD Module 10 / `docs/05-Epics.md` §2. Numbered by execution order. These are **versioned, living scripts** — extended in place as later Epics add components, not replaced with new files. See each file's header comment for its current scope and what's still pending.

| # | File | Stage | Status | Jira |
|---|---|---|---|---|
| 01 | `01_setup.sql` | Env spin-up (role/warehouse/db/schemas/stage) | **Built** (v0 scope: S-ENV-1/S-ENV-2) | SH-10, SH-14 (Done) |
| 02 | `02_setup_raw_ddl.sql` | RAW table DDL (RAW.EQUIPMENT/SENSOR_READING/CMMS_LOG) | **Built** | SH-11 (S-DATA-2) |
| 03 | `03_setup_raw_load.sql` | Upload + load thin generator output into RAW.EQUIPMENT/SENSOR_READING | **Built** | SH-11 (S-DATA-2) |
| 04 | `04_pipeline_run_phase1.sql` | dbt run — features (Raw→Std→Cons→FEAST) | Built (v2 — FEAST added, tag:inference+ phase split wired) | SH-15, SH-20, SH-24, SH-26, SH-29 |
| 05 | `05_train_models.sql` | Model training | Built (v1 — IsolationForest, via CREATE PROCEDURE + CALL) | SH-22 (IsolationForest), SH-44 (RUL, P1) |
| 06 | `06_pipeline_run_phase2.sql` | dbt run — inference & downstream | Not built | SH-23 |
| 07 | `07_post_setup.sql` | Semantic view + agent(s) + Streamlit deploy | Not built | SH-30, SH-28, SH-31, SH-33 |
| 08 | `08_demo.sql` | Live tick injection (demo-only manual trigger) | Not built | SH-25 |
| 09 | `09_teardown.sql` | Full teardown (P3, deliberately last) | Not built | SH-68, SH-61 |

Run order: 01 → 02 → 03 → 04 → 05 → 06 → 07, then 08 on demand during a demo, 09 only when actually tearing down an environment.

**Primary invocation: `manage.py up` / `manage.py down`** (repo root, `typer` CLI). `up`
runs `01_setup.sql` as ACCOUNTADMIN (the role it creates doesn't exist yet to log in
as) → switches to `snowcomotive_role` for everything else (so objects it creates are
owned consistently with dbt's own connection) → generates thin data via
`generator/thin_sensor_generator.py` (called in-process) → `02_setup_raw_ddl.sql` →
`03_setup_raw_load.sql`, all through one `snow-coco` connector session (OAuth, token
cached via `keyring` — see `AGENTS.md` "Local dev environment"), then shells out to
`dbt seed` → `dbt run --exclude tag:inference+` → `05_train_models.sql` (own
`snowcomotive_role` connector session) → `dbt run --select tag:inference+` (a
no-op today, wired for S-MODEL-3) → `dbt test` inside `predictive_maintenance_dbt/`
(its own committed `profiles.yml`, same `snow-coco` account, dbt manages its own
connection separately from the connector sessions above). `down` runs
`09_teardown.sql` as ACCOUNTADMIN (it drops `snowcomotive_role` itself).

**The `snow` CLI is not used in this project.** It was tried and dropped: the
MFA-authenticated `snow` CLI connection required a fresh TOTP passcode on every
call, which was rejected as a workflow — see
`docs/designs/1 - SH-2-11-upload-load-into-raw.md` §8 for the full writeup. Do
not run `snow sql -f` for these scripts; use `manage.py` instead, extending it
if a new script needs to join the sequence.

`manage.py up`'s generator step already covers the prerequisite below —
running the generator manually is only needed for standalone debugging outside
`manage.py`:

```
uv run generator/thin_sensor_generator.py --start-date 2026-07-01 --end-date 2026-07-31
```
